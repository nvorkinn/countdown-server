"""The background provider (broker/providers/background.py): what it makes, when, and what it writes to the store."""

import hashlib
import json
from datetime import UTC, datetime, timedelta

import fakeredis
import pytest
from countdown_api import fixtures
from countdown_api import keys as store_keys
from countdown_api.models import StoredBackground

from broker.clients.image_models import ImageModelError
from broker.db import db
from broker.models import Device, DeviceConfig
from broker.providers.background import BackgroundProvider, places
from broker.providers.loop import read_only_app
from broker.services import backgrounds

WEATHER = fixtures.load("weather.json")
KEY = WEATHER["key"].removeprefix(store_keys.WEATHER_PREFIX)
# The example's first day: dawn is due from 06:43, the day's picture from 08:43.
DAWN = datetime(2026, 1, 15, 7, tzinfo=UTC)
NOON = datetime(2026, 1, 15, 12, tzinfo=UTC)


class Model:
    """A fake image model: answers a JPEG of its own per call, or fails."""

    def __init__(self, name="gpt-image-2", *, fails=False):
        self.name = name
        self.fails = fails
        self.calls: list[tuple[str, bytes | None]] = []

    def make(self, prompt, reference=None):
        self.calls.append((prompt, reference))
        if self.fails:
            msg = "OpenAI answered 500"
            raise ImageModelError(msg)
        return b"\xff\xd8\xff" + f"{self.name} {len(self.calls)}".encode()


@pytest.fixture
def store():
    store = fakeredis.FakeRedis()
    store.set(WEATHER["key"], json.dumps(WEATHER["value"]))
    return store


@pytest.fixture
def provider_app(app):
    provider_app = read_only_app()
    yield provider_app
    with provider_app.app_context():
        db.engine.dispose()


@pytest.fixture
def touchscreen(app, register_device):
    """Return a paired touchscreen's id, its weather the example's place."""

    def _make(
        secret="a-very-long-device-secret-1",  # noqa: S107 -- a test device's
        *,
        kind="touchscreen",
        paired=True,
        postcode="SE17 3LL",
    ):
        device_id, _ = register_device(secret)
        with app.app_context():
            device = db.session.get_one(Device, device_id)
            device.type = kind
            device.paired_at = datetime.now(UTC) if paired else None
            config = db.session.get_one(DeviceConfig, device_id)
            config.weather_location = config.weather_place_name = "London"
            config.weather_place_label = "London, England, United Kingdom"
            config.weather_place_id = 2643743
            config.weather_country_code = "GB"
            config.postcode = postcode
            db.session.commit()
        return device_id

    return _make


def _provider(provider_app, store, models, now):
    clock = {"now": now}
    provider = BackgroundProvider(provider_app, store, models=models, now=lambda: clock["now"])
    return provider, clock


def _stored(store) -> dict:
    value = store.get(store_keys.background(KEY))
    assert value is not None
    StoredBackground.model_validate_json(value)
    return json.loads(value)


def test_only_paired_touchscreens_places_are_made(provider_app, touchscreen):
    touchscreen()
    touchscreen("a-very-long-device-secret-2", postcode="SW1A 1AA")
    touchscreen("a-very-long-device-secret-3", kind="epaper")
    touchscreen("a-very-long-device-secret-4", paired=False)

    with provider_app.app_context():
        found = places()

    assert [(place.key, place.name, place.country) for place in found] == [
        (KEY, "London, England, United Kingdom", "GB")
    ]
    assert found[0].postcodes == {"SE17 3LL", "SW1A 1AA"}


def test_dawn_is_made_fresh_and_stored_by_its_digest(provider_app, store, touchscreen):
    touchscreen()
    model = Model()
    provider, _ = _provider(provider_app, store, [model], DAWN)

    assert provider.run_cycle() == 1

    stored = _stored(store)
    assert (stored["location"], stored["local_date"]) == (KEY, "2026-01-15")
    [image] = stored["images"]
    assert (image["phase"], image["fallback"], image["made_at"]) == ("dawn", False, "2026-01-15T07:00:00Z")
    picture = store.get(store_keys.background_image(image["digest"]))
    assert hashlib.sha256(picture).hexdigest() == image["digest"]
    assert model.calls[0][1] is None
    assert "at sunrise" in model.calls[0][0] or "first light" in model.calls[0][0]
    assert 0 < store.ttl(store_keys.background(KEY)) <= store_keys.BACKGROUND_TTL_S
    assert 0 < store.ttl(store_keys.background_image(image["digest"])) <= store_keys.BACKGROUND_IMAGE_TTL_S


def test_nothing_is_made_twice_or_before_its_time(provider_app, store, touchscreen):
    touchscreen()
    model = Model()
    provider, clock = _provider(provider_app, store, [model], DAWN - timedelta(hours=2))

    assert provider.run_cycle() == 0
    clock["now"] = DAWN
    assert provider.run_cycle() == 1
    assert provider.run_cycle() == 0
    assert len(model.calls) == 1


def test_the_day_relights_dawn(provider_app, store, touchscreen):
    touchscreen()
    model = Model()
    provider, clock = _provider(provider_app, store, [model], DAWN)
    provider.run_cycle()
    dawn = store.get(store_keys.background_image(_stored(store)["images"][0]["digest"]))

    clock["now"] = NOON
    assert provider.run_cycle() == 1

    prompt, reference = model.calls[1]
    assert reference == dawn
    assert prompt.startswith(backgrounds.RELIGHT)
    assert [image["phase"] for image in _stored(store)["images"]] == ["dawn", "day"]


def test_a_failed_model_falls_back_to_the_curated_picture(provider_app, store, touchscreen):
    touchscreen()
    provider, _ = _provider(provider_app, store, [Model(fails=True)], DAWN)

    provider.run_cycle()

    [image] = _stored(store)["images"]
    assert image["fallback"] is True
    assert store.get(store_keys.background_image(image["digest"])) == backgrounds.fallback("dawn")


def test_without_a_key_the_pictures_are_the_curated_ones(provider_app, store, touchscreen):
    touchscreen()
    provider, _ = _provider(provider_app, store, [], DAWN)

    provider.run_cycle()

    assert _stored(store)["images"][0]["fallback"] is True


def test_a_fallback_is_tried_again_after_half_an_hour(provider_app, store, touchscreen):
    touchscreen()
    model = Model(fails=True)
    provider, clock = _provider(provider_app, store, [model], DAWN)
    provider.run_cycle()

    model.fails = False
    clock["now"] = DAWN + timedelta(minutes=10)
    assert provider.run_cycle() == 0
    clock["now"] = DAWN + timedelta(minutes=30)
    assert provider.run_cycle() == 1

    [image] = _stored(store)["images"]
    assert image["fallback"] is False


def test_a_new_day_starts_a_new_list_at_dawn(provider_app, store, touchscreen):
    touchscreen()
    store.set(
        store_keys.background(KEY),
        json.dumps(
            {
                "location": KEY,
                "local_date": "2026-01-14",
                "images": [
                    {
                        "phase": "night",
                        "starts_at": "2026-01-14T17:05:00+00:00",
                        "digest": "a" * 64,
                        "made_at": "2026-01-14T16:20:00Z",
                        "fallback": False,
                    },
                ],
            }
        ),
    )
    provider, _ = _provider(provider_app, store, [Model()], DAWN)

    provider.run_cycle()

    stored = _stored(store)
    assert stored["local_date"] == "2026-01-15"
    assert [image["phase"] for image in stored["images"]] == ["dawn"]


def test_no_forecast_no_background(provider_app, store, touchscreen):
    touchscreen()
    store.delete(WEATHER["key"])
    model = Model()
    provider, _ = _provider(provider_app, store, [model], DAWN)

    assert provider.run_cycle() == 0
    assert model.calls == []


def test_the_prompt_has_the_places_warnings(provider_app, store, touchscreen):
    touchscreen()
    store.set(
        store_keys.met_office("SE17 3LL"),
        json.dumps(
            {
                "fetched_at": "2026-01-15T06:00:00Z",
                "postcode": "SE17 3LL",
                "region": "se",
                "items": [{"title": "Yellow warning of ice affecting London & South East"}],
            }
        ),
    )
    model = Model()
    provider, _ = _provider(provider_app, store, [model], DAWN)

    provider.run_cycle()

    assert "yellow warning of ice" in model.calls[0][0]
