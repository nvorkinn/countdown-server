"""The parcel provider: each screen's parcels, read from the owner's Parcel account and written to the shared store.

    python -m broker.providers.parcel

It runs on providers/loop.py's loop, in read_only_app(), and decrypts the Parcel API keys itself
(services/parcel_key.py), so they never leave the broker. For every device in `v1:sub:parcel` it asks Parcel for the
owner's active deliveries with the owner's own key, and writes them to `v1:parcels:<device id>` (countdown-api's
ParcelList) and whether Parcel took the key to `v1:parcels-status:<device id>` (ParcelsLinkStatus).

Parcel allows 20 requests an hour per key. A cycle runs every INTERVAL_S, and a device is only asked in the hours its
screen is awake (POLL_FROM_HOUR..POLL_UNTIL_HOUR, in its time zone), so a key sees about three requests an hour by day
and none overnight. A cycle started early by `v1:sub:changed` (some config was saved) only asks the devices whose key is
new or changed, or that Parcel refused (a saved config is the signal to try again), and no key is asked twice within
MIN_GAP_S whatever nudges come.

A 401 writes `invalid_key` and stops asking that key until the next nudge; a 429 writes `rate_limited` and leaves the
key alone until Parcel says (or BACKOFF_S). Any other failure only loses that device's poll: its parcels expire if it
keeps failing. When a device's parcels change, its screen is queued for a render (services/render_jobs.py).

Parcel's answer has no "delivered" list in the active view, so a parcel that was there last time and now isn't (or now
says delivered) is kept as delivered for DELIVERED_KEPT_S, so the screen can show that it arrived. The previous list is
read back from the store, so a restart forgets nothing.
"""

import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from countdown_api import keys as store_keys
from countdown_api import models as store_models
from cryptography.fernet import InvalidToken

from broker.clients import parcel as parcel_client
from broker.db import db
from broker.models import DeviceConfig
from broker.providers import loop
from broker.providers.loop import ProviderLoop
from broker.services import render_jobs
from broker.services.parcel_key import get_parcel_api_key
from broker.services.postcodes import postcode_country
from broker.services.renderer_config import timezone

if TYPE_CHECKING:
    from collections.abc import Callable

    import redis
    from flask import Flask

# Every 20 minutes by day: three requests an hour of the 20 Parcel allows, and a parcel's change shows within about a
# quarter of an hour.
INTERVAL_S = 20 * 60
# The hours of the day, in the device's time zone, that its parcels are polled in: 06:00 up to 23:00.
POLL_FROM_HOUR = 6
POLL_UNTIL_HOUR = 23
# No key is asked twice within this, however many config saves nudge the provider: at most 12 an hour of the 20.
MIN_GAP_S = 5 * 60
# How long a rate-limited key is left alone when Parcel doesn't say.
BACKOFF_S = 60 * 60
# The longest a key is left alone, whatever Parcel asks for.
MAX_BACKOFF_S = 6 * 60 * 60
# How long a parcel seen delivered stays in the list.
DELIVERED_KEPT_S = 4 * 60 * 60
# How long Parcel's carrier names are kept: the list changes daily.
CARRIERS_TTL_S = 24 * 60 * 60
TTL_S = store_keys.PARCELS_TTL_S
STATUS_TTL_S = store_keys.PARCELS_STATUS_TTL_S
DEFAULT_ZONE = "Europe/London"

# Parcel's status codes (docs: api-view-deliveries) as the store contract's statuses. A code not here is "unknown".
STATUSES = {
    0: "delivered",
    1: "unknown",  # frozen: no update for a long time
    2: "in_transit",
    3: "exception",  # awaiting pickup by the recipient: held, and needs the owner
    4: "out_for_delivery",
    5: "unknown",  # not found
    6: "exception",  # failed delivery attempt
    7: "exception",
    8: "ordered",  # the carrier has the information but not the parcel
}


def parcels_key(device_id: str) -> str:
    """Return the key of a device's parcels (the store contract's)."""
    return store_keys.parcels(device_id)


def status_key(device_id: str) -> str:
    """Return the key of a device's Parcel link status (the store contract's)."""
    return store_keys.parcels_status(device_id)


def polling_hours(now: datetime, zone: str) -> bool:
    """Whether `now` is within the hours a device in `zone` is polled."""
    return POLL_FROM_HOUR <= now.astimezone(ZoneInfo(zone)).hour < POLL_UNTIL_HOUR


def _expected_date(delivery: dict[str, Any], zone: ZoneInfo) -> date | None:
    """Return the day Parcel expects the delivery: from its epoch time in `zone` when it has one, else its date."""
    stamp = delivery.get("timestamp_expected")
    if isinstance(stamp, int) and not isinstance(stamp, bool):
        return datetime.fromtimestamp(stamp, zone).date()
    text = delivery.get("date_expected")
    if isinstance(text, str):
        try:
            # Parcel says the date has no zone, and not what format: ISO with a date, or a date and a time.
            return datetime.fromisoformat(text.strip()).date()
        except ValueError:
            return None
    return None


def identity(parcel: dict[str, Any]) -> tuple[str, str]:
    """Return what makes a parcel the same one from one poll to the next: its carrier and tracking number."""
    return parcel["carrier"], parcel["tracking_number"]


def parcels_from(
    deliveries: list[dict[str, Any]],
    carriers: dict[str, str],
    previous: list[dict[str, Any]],
    now: datetime,
    zone: str,
) -> list[dict[str, Any]]:
    """Return the stored parcels (ParcelList's) from Parcel's active `deliveries` and the `previous` stored ones.

    A parcel's `updated_at` is when this provider last saw it change status (Parcel's own event dates have no stated
    format): kept from `previous` while its status is the same, else `now`. Ordered by expected date, undated last.
    """
    tz = ZoneInfo(zone)
    before = {identity(p): p for p in previous}
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for delivery in deliveries:
        code, number = delivery.get("carrier_code"), delivery.get("tracking_number")
        if not isinstance(code, str) or not isinstance(number, str) or not number:
            continue
        carrier = carriers.get(code) or code.upper()
        status = STATUSES.get(delivery.get("status_code"), "unknown")
        description = delivery.get("description")
        expected = _expected_date(delivery, tz)
        parcel = {
            "description": description if isinstance(description, str) and description else number,
            "carrier": carrier,
            "tracking_number": number,
            "status": status,
            "expected_date": expected.isoformat() if expected else None,
            "updated_at": now.isoformat(),
        }
        old = before.get((carrier, number))
        if old and old["status"] == status:
            parcel["updated_at"] = old["updated_at"]
        found[(carrier, number)] = parcel
    # Active deliveries only: one that has gone from the answer was delivered (or deleted in the app, which the API
    # can't tell apart), and shows as delivered for a while.
    for ident, old in before.items():
        if ident not in found and old["status"] != "delivered":
            found[ident] = {**old, "status": "delivered", "updated_at": now.isoformat()}
        elif ident not in found:
            found[ident] = old
    kept = [
        p
        for p in found.values()
        if p["status"] != "delivered"
        or now - datetime.fromisoformat(p["updated_at"]) < timedelta(seconds=DELIVERED_KEPT_S)
    ]
    return sorted(kept, key=lambda p: (p["expected_date"] is None, p["expected_date"] or ""))


def validated(parcels: list[dict[str, Any]], now: datetime) -> str:
    """Return the ParcelList JSON for `parcels`, checked against the store contract's model."""
    return store_models.ParcelList.model_validate({"fetched_at": now.isoformat(), "parcels": parcels}).model_dump_json(
        exclude_none=False
    )


@dataclass(frozen=True)
class Wanted:
    """A device in `v1:sub:parcel`, its key and the time zone its screen goes by."""

    device_id: str
    api_key: str
    zone: str


class ParcelProvider(ProviderLoop):
    """Asks Parcel for every subscribed device's active deliveries each cycle, in the device's waking hours."""

    label = "Parcel"

    def __init__(  # noqa: PLR0913 -- the clocks and Parcel's client are there for the tests
        self,
        app: Flask,
        store: redis.Redis,
        *,
        interval_s: int = INTERVAL_S,
        clock: Callable[[], float] = time.time,
        deliveries: Callable[[str], list[dict[str, Any]]] = parcel_client.deliveries,
        carrier_names: Callable[[], dict[str, str]] = parcel_client.carrier_names,
    ) -> None:
        """See ProviderLoop; `deliveries` and `carrier_names` ask Parcel."""
        super().__init__(app, store, clock)
        self._interval_s = interval_s
        self._deliveries = deliveries
        self._carrier_names = carrier_names
        # By device id: the key last asked with (kept in memory only, as glow keeps its passwords), and when.
        self._asked: dict[str, tuple[str, float]] = {}
        # Devices whose key Parcel refused, by the key: not asked again until a nudge.
        self._refused: dict[str, str] = {}
        # Device -> when its key may be asked again, after Parcel rate-limited it.
        self._backoff_until: dict[str, float] = {}
        self._carriers: dict[str, str] = {}
        self._carriers_at = float("-inf")

    def interval_s(self) -> float:
        """Return how far apart the ticks are: INTERVAL_S unless the tests say otherwise."""
        return self._interval_s

    def run_cycle(self) -> int:
        """Ask Parcel for every device that is due and write what came back; return how many keys were written."""
        # The store's client decodes responses, so these are str.
        device_ids = sorted(str(d) for d in self.store.smembers(store_keys.subscription(store_keys.PARCEL)))
        with self.app.app_context():
            try:
                wanted = self._wanted(device_ids)
            finally:
                # Short transactions: the web app writes to the same SQLite file, and the polls take a while.
                db.session.remove()
        current = {w.device_id for w in wanted}
        for state in (self._asked, self._refused, self._backoff_until):
            for gone in set(state) - current:
                del state[gone]
        written = 0
        for want in wanted:
            if self._stopping.is_set():
                break
            if self._due(want):
                written += self._poll_device(want)
        self.logger.debug("Parcel: wrote %d keys for %d devices", written, len(wanted))
        return written

    def _wanted(self, device_ids: list[str]) -> list[Wanted]:
        """Return the subscribed devices that have a key (decrypted) and the zone of each."""
        wanted = []
        for device_id in device_ids:
            try:
                api_key = get_parcel_api_key(db, device_id)
            except InvalidToken:
                self.logger.warning("Parcel: can't decrypt the key of device %s", device_id)
                continue
            if not api_key:
                continue
            config = db.session.get(DeviceConfig, device_id)
            zone = timezone(postcode_country(config.postcode), config.tfl_stop_ids) if config else DEFAULT_ZONE
            wanted.append(Wanted(device_id, api_key, zone))
        return wanted

    def _due(self, want: Wanted) -> bool:
        """Whether this cycle asks Parcel for the device.

        Not while it is backed off or was asked a moment ago. A cycle a config save started asks only when the key is
        new, changed or was refused (and at any hour); a tick asks in the device's waking hours.
        """
        now = self.clock()
        if now < self._backoff_until.get(want.device_id, 0):
            return False
        asked = self._asked.get(want.device_id)
        if asked and now - asked[1] < MIN_GAP_S:
            return False
        if self.nudged:
            # A saved config is the signal to try a refused key again, and to ask at once whatever the hour.
            return asked is None or asked[0] != want.api_key or want.device_id in self._refused
        if self._refused.get(want.device_id) == want.api_key:
            return False
        return polling_hours(datetime.fromtimestamp(now, UTC), want.zone)

    def _carrier_names_now(self, now: float) -> dict[str, str]:
        """Return Parcel's carrier names, asked for again once a day; the last ones (or none) if it can't be asked."""
        if now - self._carriers_at >= CARRIERS_TTL_S:
            self._carriers_at = now
            try:
                self._carriers = self._carrier_names()
            except parcel_client.ParcelError as e:
                self.logger.warning("Parcel: %s; using the carriers' codes", e)
        return self._carriers

    def _poll_device(self, want: Wanted) -> int:
        now = self.clock()
        stamp = datetime.fromtimestamp(now, UTC)
        self._asked[want.device_id] = (want.api_key, now)
        try:
            deliveries = self._deliveries(want.api_key)
        except parcel_client.InvalidKeyError:
            self._refused[want.device_id] = want.api_key
            self.logger.warning(
                "Parcel: refused the key of device %s; not asking again until its config is saved", want.device_id
            )
            return self._write_status(want.device_id, "invalid_key")
        except parcel_client.RateLimitedError as e:
            wait = min(e.retry_after_s or BACKOFF_S, MAX_BACKOFF_S)
            self._backoff_until[want.device_id] = now + wait
            self.logger.warning("Parcel: rate-limiting device %s's key; asking again in %ds", want.device_id, wait)
            return self._write_status(want.device_id, "rate_limited")
        except parcel_client.ParcelError as e:
            # Its keys aren't refreshed, so they expire if this keeps up.
            self.logger.warning("Parcel: device %s: %s", want.device_id, e)
            return 0
        self._refused.pop(want.device_id, None)
        previous = self._previous(want.device_id)
        old = None if previous is None else previous["parcels"]
        parcels = parcels_from(deliveries, self._carrier_names_now(now), old or [], stamp, want.zone)
        with self.store.pipeline(transaction=False) as pipe:
            pipe.set(parcels_key(want.device_id), validated(parcels, stamp), ex=TTL_S)
            pipe.set(status_key(want.device_id), "ok", ex=STATUS_TTL_S)
            pipe.execute()
        if old is None or _statuses(old) != _statuses(parcels):
            # After the write, so the render reads the new data. Never raises: the tick draws the screen anyway.
            render_jobs.enqueue(self.store, [want.device_id])
        return 2

    def _previous(self, device_id: str) -> dict[str, Any] | None:
        """Return the device's stored ParcelList, or None if it has none (or an unreadable one)."""
        raw = self.store.get(parcels_key(device_id))
        if raw is None:
            return None
        try:
            return store_models.ParcelList.model_validate_json(raw).model_dump(mode="json")
        except ValueError:
            return None

    def _write_status(self, device_id: str, status: str) -> int:
        """Write the link status; nothing asks a refused key again, so `invalid_key` lapses after its TTL."""
        self.store.set(status_key(device_id), status, ex=STATUS_TTL_S)
        return 1


def _statuses(parcels: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    return {identity(p): p["status"] for p in parcels}


def main() -> None:
    """Run the provider until SIGINT or SIGTERM."""
    loop.serve(ParcelProvider)


if __name__ == "__main__":
    main()
