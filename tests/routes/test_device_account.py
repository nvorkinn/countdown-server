"""Test what a device is told of its owner's account: which services it has linked (GET /api/devices/account).

The links are the account's, not the device's, so they aren't in its config, whose `parcels.enabled` is off both while
the screen's switch is off and while the account has no Parcel key. This is how a touchscreen tells the two apart.
"""

from countdown_api.models import DeviceAccount

from broker.db import db
from broker.models import Device, ParcelKey, SpotifyToken
from tests.routes.test_touchscreens import TOUCHSCREEN, _auth, _paired, _registered

ACCOUNT_URL = "/api/devices/account"
CONFIG_URL = "/api/devices/config"


def _owner(app, device_id):
    with app.app_context():
        return db.session.get_one(Device, device_id).owner


def _save_parcel_key(app, device_id):
    with app.app_context():
        key = ParcelKey(user_id=db.session.get_one(Device, device_id).owner)
        key.key = "parcel-api-key-secret"
        db.session.add(key)
        db.session.commit()


def test_the_contracts_account_is_what_the_route_answers(app, client):
    device_id = _paired(app, client)
    with app.app_context():
        db.session.add(SpotifyToken(user_id=_owner(app, device_id), refresh_token="refresh-secret"))
        db.session.commit()

    response = client.get(ACCOUNT_URL, headers=_auth())

    assert response.status_code == 200
    assert response.get_json() == TOUCHSCREEN["account"]
    assert DeviceAccount.model_validate(response.get_json()).spotify.linked
    assert "refresh-secret" not in response.get_data(as_text=True)


def test_an_account_with_nothing_linked_says_so(app, client):
    _paired(app, client)

    response = client.get(ACCOUNT_URL, headers=_auth())

    assert response.get_json() == {"parcel": {"linked": False}, "spotify": {"linked": False}}


def test_a_saved_parcel_key_is_linked_whatever_the_screens_switch_says(app, client):
    device_id = _paired(app, client)
    _save_parcel_key(app, device_id)

    response = client.get(ACCOUNT_URL, headers=_auth())

    # Linked, though the screen's own switch is still off: the config's `enabled` alone can't say which it is.
    assert response.get_json()["parcel"] == {"linked": True}
    assert client.get(CONFIG_URL, headers=_auth()).get_json()["parcels"]["enabled"] is False
    assert "parcel-api-key-secret" not in response.get_data(as_text=True)


def test_it_needs_the_devices_secret(app, client):
    _paired(app, client)

    assert client.get(ACCOUNT_URL).status_code == 401
    assert client.get(ACCOUNT_URL, headers=_auth("not-the-devices-secret")).status_code == 401


def test_a_device_no_one_has_paired_has_no_account(app, client):
    _registered(app, client)

    assert client.get(ACCOUNT_URL, headers=_auth()).status_code == 409
