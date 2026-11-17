"""The background provider: the weather panel's AI-made photos, one per part of each place's day, into the store.

    python -m broker.providers.background

It runs on providers/loop.py's loop, in read_only_app(), with a store that answers bytes (the pictures). Every
INTERVAL_S it reads the places the paired touchscreens show (services/background_places.py: an e-paper screen draws no
background, so its places cost nothing) and, for each, works out from the stored forecast which part of the day is due
(services/backgrounds.py: each is made LEAD before it starts). One not made yet is made: dawn new, each later part
relit from the one before it, by the first model that answers (clients/image_models.py: OpenAI's), else
the curated picture with `fallback: true`, which is tried again after backgrounds.RETRY. Its bytes go to
`v1:background-image:<sha256>` and the day's list to `v1:background:<weather key>` (the store contract's), both kept
two days.

The day's list starts again at the place's dawn, so until then screens keep the night before's. A place with no
forecast stored (its provider is down, or new) waits for one. A picture takes about a minute, so a cycle with several
places due takes a few; the loop simply starts the next one late.
"""

import hashlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from countdown_api import keys as store_keys
from countdown_api.models import StoredBankHolidays, StoredForecast, StoredMetOfficeWarnings
from pydantic import BaseModel, ValidationError

from broker.clients import image_models
from broker.clients.image_models import ImageModel, ImageModelError
from broker.providers import loop
from broker.providers.loop import ProviderLoop
from broker.services import backgrounds
from broker.services.background_places import places
from broker.services.backgrounds import Context, Place

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    import redis
    from countdown_api.models import BackgroundPhase
    from flask import Flask

logger = logging.getLogger(__name__)

# Often enough that a part of the day is made well inside LEAD of its start.
INTERVAL_S = 300
TTL_S = store_keys.BACKGROUND_TTL_S
IMAGE_TTL_S = store_keys.BACKGROUND_IMAGE_TTL_S


class BackgroundProvider(ProviderLoop):
    """Makes each place's due background every cycle."""

    label = "Background"

    def __init__(
        self,
        app: Flask,
        store: redis.Redis,
        clock: Callable[[], float] = time.time,
        models: list[ImageModel] | None = None,
        now: Callable[[], datetime] = backgrounds.utc_now,
    ) -> None:
        """`store` answers bytes. `models` are those to try in order (image_models.configured() by default)."""
        super().__init__(app, store, clock)
        self.models = image_models.configured() if models is None else models
        self.now = now

    def interval_s(self) -> float:
        """Return INTERVAL_S."""
        return INTERVAL_S

    def run_cycle(self) -> int:
        """Make every place's due background; return how many were written. Raises RedisError."""
        if not self.models and not self.nudged:
            logger.warning("No OPENAI_API_KEY: the backgrounds are the curated ones")
        with self.app.app_context():
            wanted = places()
        written = 0
        for place in wanted:
            if self._stopping.is_set():
                break
            written += self._make_due(place)
        return written

    def _make_due(self, place: Place) -> int:
        """Make the place's due background if it hasn't one yet; return 1 if one was written, else 0."""
        forecast = self._read(store_keys.weather(place.key), StoredForecast)
        if forecast is None:
            return 0
        now = self.now()
        day = backgrounds.day_of(forecast, now)
        weather = None
        phase = backgrounds.due(day, now) if day else None
        if day is not None and phase is not None:
            weather = backgrounds.weather_at(forecast, day, phase)
        if day is None or phase is None or weather is None:
            return 0
        stored = self._json(store_keys.background(place.key))
        images = stored["images"] if stored and stored.get("local_date") == day.local_date.isoformat() else []
        if backgrounds.next_step(images, phase, now) == "keep":
            return 0
        context = Context(
            local_date=day.local_date,
            weather=weather,
            occasions=backgrounds.occasions(day.local_date),
            bank_holiday=self._bank_holiday(place, day.local_date),
            warnings=self._warnings(place),
        )
        prompt = backgrounds.prompt(place, phase, context)
        source = backgrounds.reference(images, phase)
        picture, fallback = self._make(place, phase, prompt, source)
        digest = hashlib.sha256(picture).hexdigest()
        image = {
            "phase": phase,
            "starts_at": backgrounds.iso_minute(day.starts[phase]),
            "digest": digest,
            "made_at": backgrounds.iso_utc(now),
            "fallback": fallback,
        }
        value = {
            "location": place.key,
            "local_date": day.local_date.isoformat(),
            "images": backgrounds.with_image(images, image),
        }
        with self.store.pipeline(transaction=False) as pipe:
            pipe.set(store_keys.background_image(digest), picture, ex=IMAGE_TTL_S)
            pipe.set(store_keys.background(place.key), json.dumps(value), ex=TTL_S)
            pipe.execute()
        logger.info("Made %s's %s background%s", place.key, phase, " (a fallback)" if fallback else "")
        return 1

    def _make(
        self, place: Place, phase: BackgroundPhase, prompt: str, source: dict[str, Any] | None
    ) -> tuple[bytes, bool]:
        """Return the picture and whether it's the fallback: the first model's that answers, else the curated one."""
        reference = self.store.get(store_keys.background_image(source["digest"])) if source else None
        for model in self.models:
            try:
                if isinstance(reference, bytes):
                    return model.make(backgrounds.RELIGHT + prompt, reference), False
                return model.make(prompt), False
            except ImageModelError as error:
                logger.warning("%s made no %s background for %s: %s", model.name, phase, place.key, error)
        return backgrounds.fallback(phase), True

    def _bank_holiday(self, place: Place, day: Any) -> str | None:  # noqa: ANN401 -- a date
        if place.country != "GB":
            return None
        return backgrounds.bank_holiday(self._read(store_keys.BANK_HOLIDAYS, StoredBankHolidays), day)

    def _warnings(self, place: Place) -> list[str]:
        if place.country != "GB":
            return []
        feeds = [self._read(store_keys.met_office(postcode), StoredMetOfficeWarnings) for postcode in place.postcodes]
        return backgrounds.warnings([feed for feed in feeds if feed is not None])

    def _json(self, key: str) -> dict[str, Any] | None:
        value = self.store.get(key)
        if value is None:
            return None
        try:
            return json.loads(value)
        except ValueError:
            return None

    def _read[M: BaseModel](self, key: str, model: type[M]) -> M | None:
        """Return the stored value as the contract's `model`; None if it's missing or the model won't read it."""
        value = self.store.get(key)
        if value is None:
            return None
        try:
            return model.model_validate_json(value)
        except ValidationError as error:
            logger.warning("%s isn't the contract's %s (%d errors)", key, model.__name__, error.error_count())
            return None


def main() -> None:
    """Run the background provider until SIGTERM or SIGINT."""
    loop.serve(BackgroundProvider, decode_responses=False)


if __name__ == "__main__":
    main()
