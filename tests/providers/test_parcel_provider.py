"""The parcel provider (broker/providers/parcel.py): Parcel faked, Redis faked (fakeredis), the database a real one.

The store contract's parcels.json (countdown_api.fixtures) is the stored shape. Parcel's own API is never called: the
provider is given a fake for it, and the client is tested against `responses`.
"""

import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import fakeredis
import pytest
import requests
import responses
from countdown_api import fixtures
from countdown_api import models as store_models

from broker.clients import parcel as client
from broker.db import db
from broker.models import Device, DeviceConfig
from broker.providers import parcel
from broker.providers.loop import SUB_CHANGED, read_only_app
from broker.providers.parcel import (
    DELIVERED_KEPT_S,
    MIN_GAP_S,
    STATUS_TTL_S,
    TTL_S,
    ParcelProvider,
    parcels_key,
    status_key,
)
from broker.services import render_jobs
from broker.services.render_jobs import JOBS, SCREENS

FIXTURE = fixtures.load("parcels.json")
DEVICE = FIXTURE["device_id"]
# 10:00 in London, in October (BST): a waking hour.
NOW = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)
KEY = "parcel-key-1234567890-secret"
CARRIERS = {"dhl": "DHL", "rmg": "Royal Mail"}


def delivery(number="JD1", status_code=2, **extra):
    return {
        "carrier_code": "dhl",
        "description": "Shoes",
        "status_code": status_code,
        "tracking_number": number,
        "events": [],
        **extra,
    }


class FakeParcel:
    """Parcel's view-deliveries, as a provider's `deliveries`: answers what it was given, or raises it."""

    def __init__(self, answer=None):
        self.answer = [delivery()] if answer is None else answer
        self.keys = []

    def __call__(self, api_key):
        self.keys.append(api_key)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture
def store():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def provider_app(app):
    provider_app = read_only_app()
    yield provider_app
    with provider_app.app_context():
        db.engine.dispose()


@pytest.fixture
def clock():
    class Clock:
        now = NOW.timestamp()

        def __call__(self):
            return self.now

    return Clock()


@pytest.fixture
def keys(monkeypatch):
    """Return the decrypted Parcel keys by device id, as services/parcel_key.get_parcel_api_key gives them."""
    keys = {DEVICE: KEY}
    monkeypatch.setattr(parcel, "get_parcel_api_key", lambda _db, device_id: keys.get(device_id))
    return keys


@pytest.fixture
def fake():
    return FakeParcel()


@pytest.fixture
def provider(provider_app, store, clock, fake, keys):
    store.sadd("v1:sub:parcel", *keys)
    return ParcelProvider(provider_app, store, clock=clock, deliveries=fake, carrier_names=lambda: CARRIERS)


@pytest.fixture(autouse=True)
def device(app):
    with app.app_context():
        db.session.add(Device(device_id=DEVICE, created_at=NOW))
        db.session.add(DeviceConfig(device_id=DEVICE, postcode=""))
        db.session.commit()


def stored(store, key=None):
    value = store.get(key or parcels_key(DEVICE))
    return None if value is None else json.loads(value)


def test_writes_the_parcels_in_the_stored_shape(provider, store, fake):
    fake.answer = [
        delivery("JD1", 4, date_expected="2026-10-17 00:00:00"),
        delivery("JD3", 8),
        delivery("JD2", 2, timestamp_expected=int(datetime(2026, 10, 16, 23, 30, tzinfo=UTC).timestamp())),
    ]

    assert provider.run_cycle() == 2

    value = stored(store)
    store_models.ParcelList.model_validate(value)
    assert value["fetched_at"].startswith("2026-10-17T09:00:00")
    # By expected date, undated last; 23:30 UTC on the 16th is the 17th in London (BST).
    assert [(p["tracking_number"], p["status"], p["expected_date"]) for p in value["parcels"]] == [
        ("JD1", "out_for_delivery", "2026-10-17"),
        ("JD2", "in_transit", "2026-10-17"),
        ("JD3", "ordered", None),
    ]
    assert value["parcels"][0]["carrier"] == "DHL"
    assert 0 < store.ttl(parcels_key(DEVICE)) <= TTL_S == FIXTURE["ttl_s"]
    assert store.get(status_key(DEVICE)) == "ok"
    assert 0 < store.ttl(status_key(DEVICE)) <= STATUS_TTL_S == FIXTURE["status_ttl_s"]


def test_the_key_goes_to_parcel_and_never_to_the_logs(provider, fake, caplog):
    caplog.set_level("DEBUG")
    fake.answer = client.InvalidKeyError("Parcel refused the key")

    provider.run_cycle()
    provider.clock.now += MIN_GAP_S
    provider.nudged = True
    provider.run_cycle()

    assert fake.keys[0] == KEY
    assert KEY not in caplog.text


@pytest.mark.parametrize(
    ("code", "expected"),
    [(0, "delivered"), (1, "unknown"), (2, "in_transit"), (3, "exception"), (4, "out_for_delivery"), (5, "unknown"),
     (6, "exception"), (7, "exception"), (8, "ordered"), (99, "unknown"), (None, "unknown")],
)  # fmt: skip
def test_maps_every_status_code(provider, store, fake, code, expected):
    fake.answer = [delivery(status_code=code)]

    provider.run_cycle()

    assert stored(store)["parcels"][0]["status"] == expected


def test_a_parcel_that_leaves_the_active_list_stays_as_delivered_for_a_few_hours(provider, store, fake, clock):
    provider.run_cycle()
    fake.answer = []

    clock.now += parcel.INTERVAL_S
    provider.run_cycle()

    [kept] = stored(store)["parcels"]
    assert (kept["tracking_number"], kept["status"]) == ("JD1", "delivered")
    delivered_at = kept["updated_at"]

    clock.now += DELIVERED_KEPT_S - MIN_GAP_S - 60
    provider.run_cycle()
    assert stored(store)["parcels"][0]["updated_at"] == delivered_at

    clock.now += MIN_GAP_S + 120
    provider.run_cycle()
    assert stored(store)["parcels"] == []


def test_updated_at_moves_only_when_the_status_does(provider, store, fake, clock):
    provider.run_cycle()
    first = stored(store)["parcels"][0]["updated_at"]

    clock.now += parcel.INTERVAL_S
    provider.run_cycle()
    assert stored(store)["parcels"][0]["updated_at"] == first

    clock.now += parcel.INTERVAL_S
    fake.answer = [delivery(status_code=4)]
    provider.run_cycle()
    assert stored(store)["parcels"][0]["updated_at"] != first


def _pool(store):
    store.hset(SCREENS, DEVICE, "screen")


def test_a_status_change_queues_a_render_and_no_change_does_not(provider, store, fake, clock):
    _pool(store)

    provider.run_cycle()
    assert store.lrange(JOBS, 0, -1) == [DEVICE]
    store.delete(JOBS, render_jobs.queued_key(DEVICE))

    clock.now += parcel.INTERVAL_S
    provider.run_cycle()
    assert store.lrange(JOBS, 0, -1) == []

    clock.now += parcel.INTERVAL_S
    fake.answer = [delivery(status_code=4)]
    provider.run_cycle()
    assert store.lrange(JOBS, 0, -1) == [DEVICE]


def test_a_401_writes_invalid_key_and_stops_asking_until_a_config_is_saved(provider, store, fake, clock):
    fake.answer = client.InvalidKeyError("Parcel refused the key")

    provider.run_cycle()
    assert store.get(status_key(DEVICE)) == "invalid_key"
    assert stored(store) is None

    clock.now += parcel.INTERVAL_S
    provider.run_cycle()
    assert len(fake.keys) == 1

    # The config is saved again: the nudge tries the key afresh, and a good answer clears the status.
    fake.answer = [delivery()]
    provider.nudged = True
    provider.run_cycle()
    assert len(fake.keys) == 2
    assert store.get(status_key(DEVICE)) == "ok"


def test_a_429_writes_rate_limited_and_backs_off(provider, store, fake, clock):
    fake.answer = client.RateLimitedError(None)

    provider.run_cycle()
    assert store.get(status_key(DEVICE)) == "rate_limited"

    clock.now += parcel.BACKOFF_S - 1
    provider.run_cycle()
    provider.nudged = True
    provider.run_cycle()
    assert len(fake.keys) == 1

    clock.now += 1
    fake.answer = [delivery()]
    provider.nudged = False
    provider.run_cycle()
    assert len(fake.keys) == 2
    assert store.get(status_key(DEVICE)) == "ok"


def test_retry_after_sets_the_backoff_within_a_cap(provider, fake, clock):
    fake.answer = client.RateLimitedError(10 * parcel.MAX_BACKOFF_S)

    provider.run_cycle()

    assert provider._backoff_until[DEVICE] == clock.now + parcel.MAX_BACKOFF_S


def test_other_failures_write_nothing(provider, store, fake):
    fake.answer = client.ParcelError("Parcel answered 500")

    assert provider.run_cycle() == 0

    assert store.keys("v1:parcels*") == []


def test_not_asked_overnight_but_asked_when_a_key_is_first_saved(provider, store, fake, clock):
    # 01:00 in London.
    clock.now = datetime(2026, 10, 18, 0, 0, tzinfo=UTC).timestamp()
    assert provider.run_cycle() == 0
    assert fake.keys == []

    provider.nudged = True
    assert provider.run_cycle() == 2
    assert len(fake.keys) == 1


def test_a_nudge_skips_keys_already_asked_and_the_gap_caps_the_rate(provider, fake, clock):
    provider.run_cycle()

    clock.now += MIN_GAP_S + 1
    provider.nudged = True
    provider.run_cycle()
    assert len(fake.keys) == 1

    # Even a tick right after another is held to the gap.
    provider.nudged = False
    clock.now += parcel.INTERVAL_S
    provider.run_cycle()
    provider.run_cycle()
    assert len(fake.keys) == 2


def test_a_changed_key_is_asked_on_the_nudge(provider, keys, fake, clock):
    provider.run_cycle()
    keys[DEVICE] = "another-key-0987654321"

    clock.now += MIN_GAP_S + 1
    provider.nudged = True
    provider.run_cycle()

    assert fake.keys == [KEY, "another-key-0987654321"]


def test_devices_without_a_key_or_not_subscribed_are_skipped(provider, store, keys, fake):
    store.sadd("v1:sub:parcel", "nokey000000")

    provider.run_cycle()

    assert fake.keys == [KEY]


def test_the_cycle_is_a_provider_loop_tick(provider):
    assert provider.interval_s() == parcel.INTERVAL_S == 20 * 60
    assert provider.label == "Parcel"
    assert SUB_CHANGED == "v1:sub:changed"


def test_carriers_fall_back_to_the_code_and_are_asked_once_a_day(provider_app, store, keys, clock, fake):
    calls = []

    def names():
        calls.append(1)
        msg = "down"
        raise client.ParcelError(msg)

    store.sadd("v1:sub:parcel", DEVICE)
    provider = ParcelProvider(provider_app, store, clock=clock, deliveries=fake, carrier_names=names)
    provider.run_cycle()
    clock.now += parcel.INTERVAL_S
    provider.run_cycle()

    assert stored(store)["parcels"][0]["carrier"] == "DHL"
    assert len(calls) == 1


# The client, against Parcel's documented API.


@responses.activate
def test_the_client_sends_the_key_in_a_header_only_and_asks_for_active():
    responses.get(client.DELIVERIES_URL, json={"success": True, "deliveries": [delivery()]})

    assert client.deliveries(KEY) == [delivery()]

    [call] = responses.calls
    assert call.request.headers["api-key"] == KEY
    url = call.request.url or ""
    assert parse_qs(urlsplit(url).query) == {"filter_mode": ["active"]}
    assert KEY not in url


@responses.activate
def test_the_client_raises_the_matching_error_and_none_carries_the_key():
    for status, error in ((401, client.InvalidKeyError), (429, client.RateLimitedError), (500, client.ParcelError)):
        responses.upsert(responses.GET, client.DELIVERIES_URL, status=status, headers={"Retry-After": "90"})
        with pytest.raises(error) as raised:
            client.deliveries(KEY)
        assert KEY not in str(raised.value)
        if isinstance(raised.value, client.RateLimitedError):
            assert raised.value.retry_after_s == 90


@responses.activate
def test_the_client_wraps_network_errors_and_bad_bodies():
    responses.get(client.DELIVERIES_URL, body=requests.ConnectionError(f"https://x/?api-key={KEY}"))
    with pytest.raises(client.ParcelError) as raised:
        client.deliveries(KEY)
    assert KEY not in str(raised.value)
    assert raised.value.__cause__ is None

    for body in ("not json", json.dumps({"success": False, "error_message": KEY}), json.dumps([1])):
        responses.upsert(responses.GET, client.DELIVERIES_URL, body=body)
        with pytest.raises(client.ParcelError) as raised:
            client.deliveries(KEY)
        assert KEY not in str(raised.value)


@responses.activate
def test_the_client_reads_carrier_names():
    responses.get(client.CARRIERS_URL, json={"dhl": {"name": "DHL"}, "odd": 1})

    assert client.carrier_names() == {"dhl": "DHL"}
