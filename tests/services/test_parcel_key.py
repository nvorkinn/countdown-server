"""A device's Parcel API key: kept encrypted, read back by the parcel provider (broker/services/parcel_key.py)."""

import fakeredis
import pytest
from countdown_api import keys as store_keys
from redis import ConnectionError as RedisConnectionError

from broker.db import db
from broker.models import ParcelKey
from broker.services import parcel_key


def _save(device_id: str, key: str = "parcel-secret") -> None:
    row = ParcelKey(device_id=device_id)
    row.key = key
    db.session.add(row)
    db.session.commit()


def test_the_provider_reads_the_key_decrypted(app, register_device):
    device_id, _ = register_device()
    with app.app_context():
        _save(device_id)

        assert parcel_key.get_parcel_api_key(db, device_id) == "parcel-secret"
        assert db.session.get_one(ParcelKey, device_id).key_encrypted != "parcel-secret"


def test_a_device_with_no_key_has_none_and_the_panel_off(app, register_device):
    device_id, _ = register_device()
    with app.app_context():
        assert parcel_key.get_parcel_api_key(db, device_id) is None
        assert not parcel_key.has_key(db, device_id)
        _save(device_id)
        assert parcel_key.has_key(db, device_id)


@pytest.mark.parametrize("status", ["ok", "invalid_key", "rate_limited"])
def test_the_link_status_is_what_the_provider_wrote(status):
    store = fakeredis.FakeRedis(decode_responses=True)
    store.set(store_keys.parcels_status("d"), status)

    assert parcel_key.link_status(store, "d") == status


def test_the_link_status_is_none_before_the_provider_has_tried_or_for_a_value_it_does_not_know():
    store = fakeredis.FakeRedis()
    assert parcel_key.link_status(store, "d") is None

    store.set(store_keys.parcels_status("d"), "something-new")
    assert parcel_key.link_status(store, "d") is None


def test_the_link_status_is_none_when_redis_is_down():
    class Down:
        def get(self, _key):
            raise RedisConnectionError

    assert parcel_key.link_status(Down(), "d") is None
