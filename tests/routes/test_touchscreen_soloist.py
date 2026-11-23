"""Test a touchscreen's Soloist API key, which it starts Spotify Soloist with (GET /api/devices/soloist/key).

Soloist runs on the touchscreen's Pi and takes the key at every start, so this is the one saved key the broker sends
out. It is the owner's, saved on their Account page, and goes only to their own paired touchscreens: registering is
open to anyone, so a device gets nothing but what its own owner saved.
"""

import logging

from countdown_api.models import SoloistApiKey
from cryptography.fernet import Fernet

from broker.db import db
from broker.models import SoloistKey
from tests.conftest import USER
from tests.routes.test_touchscreens import SECRET, TOUCHSCREEN, _auth, _paired, _register, _registered

KEY_URL = "/api/devices/soloist/key"
ACCOUNT_URL = "/account"
KEY = TOUCHSCREEN["soloist_key"]["api_key"]


def _save(app, user_id, key=KEY):
    with app.app_context():
        saved = SoloistKey(user_id=user_id)
        saved.key = key
        db.session.add(saved)
        db.session.commit()


def _stored(app, user_id):
    """Return the user's saved key, decrypted; None if they have none."""
    with app.app_context():
        saved = db.session.get(SoloistKey, user_id)
        return saved.key if saved is not None else None


def test_a_paired_touchscreen_gets_its_owners_key_as_the_contract_has_it(app, client):
    _paired(app, client)
    _save(app, USER)

    response = client.get(KEY_URL, headers=_auth())

    assert response.status_code == 200
    assert response.get_json() == TOUCHSCREEN["soloist_key"]
    assert SoloistApiKey.model_validate(response.get_json()).api_key == KEY


def test_the_key_is_never_cached(app, client):
    _paired(app, client)
    _save(app, USER)

    assert client.get(KEY_URL, headers=_auth()).headers["Cache-Control"] == "no-store"


def test_it_is_encrypted_at_rest(app, client):
    _paired(app, client)
    _save(app, USER)

    with app.app_context():
        assert KEY not in db.session.get_one(SoloistKey, USER).key_encrypted


def test_it_needs_the_devices_secret(app, client):
    _paired(app, client)
    _save(app, USER)

    for response in (client.get(KEY_URL), client.get(KEY_URL, headers=_auth("not-the-devices-secret"))):
        assert response.status_code == 401
        assert KEY not in response.get_data(as_text=True)


def test_an_owner_without_a_key_gets_none(app, client):
    _paired(app, client)

    assert client.get(KEY_URL, headers=_auth()).status_code == 404


def test_a_touchscreen_gets_its_own_owners_key_and_no_one_elses(app, client):
    _save(app, "someone-else", key="someone-elses-soloist-key")
    _paired(app, client)

    response = client.get(KEY_URL, headers=_auth())

    # Anyone can register a device and pair it to their own account: that gets them only what they saved themselves.
    assert response.status_code == 404
    assert "someone-elses-soloist-key" not in response.get_data(as_text=True)


def test_an_unpaired_touchscreen_or_an_epaper_screen_gets_none(app, client):
    _registered(app, client)
    assert client.get(KEY_URL, headers=_auth()).status_code == 409

    secret = TOUCHSCREEN["epaper_registration"]["secret"]
    assert _register(client, TOUCHSCREEN["epaper_registration"]).status_code == 201
    assert client.get(KEY_URL, headers=_auth(secret)).status_code == 409
    assert secret != SECRET


def test_a_key_that_wont_decrypt_is_as_good_as_none(app, client, caplog):
    _paired(app, client)
    with app.app_context():
        # Saved under another CREDENTIAL_ENCRYPTION_KEY.
        db.session.add(SoloistKey(user_id=USER, key_encrypted=Fernet(Fernet.generate_key()).encrypt(b"k").decode()))
        db.session.commit()

    with caplog.at_level(logging.WARNING):
        response = client.get(KEY_URL, headers=_auth())

    assert response.status_code == 404
    assert "won't decrypt" in caplog.text


def test_the_key_is_not_logged(app, client, caplog):
    _paired(app, client)
    _save(app, USER)

    with caplog.at_level(logging.DEBUG):
        assert client.get(KEY_URL, headers=_auth()).status_code == 200

    assert KEY not in caplog.text


def test_the_account_page_saves_the_key_and_never_shows_it(app, client):
    _paired(app, client)
    assert client.post(ACCOUNT_URL, data={"soloist_api_key": f" {KEY} "}).status_code == 302

    assert _stored(app, USER) == KEY
    page = client.get(ACCOUNT_URL).get_data(as_text=True)
    assert "Spotify Soloist" in page
    assert KEY not in page
    assert client.get(KEY_URL, headers=_auth()).get_json() == {"api_key": KEY}


def test_a_blank_field_keeps_the_saved_key(app, client):
    _paired(app, client)
    _save(app, USER)

    client.post(ACCOUNT_URL, data={"soloist_api_key": ""})

    assert _stored(app, USER) == KEY


def test_removing_the_key_stops_it_being_sent(app, client):
    _paired(app, client)
    _save(app, USER)

    response = client.post("/account/soloist/disconnect")

    assert response.status_code == 302
    assert _stored(app, USER) is None
    assert client.get(KEY_URL, headers=_auth()).status_code == 404


def test_an_account_with_no_touchscreen_is_not_asked_for_a_key(paired_client):
    client, _, _ = paired_client

    assert "Spotify Soloist" not in client.get(ACCOUNT_URL).get_data(as_text=True)
