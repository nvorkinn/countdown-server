"""Test a touchscreen's people's Spotify Soloist API keys: one each, saved with a code and asked for by person.

Soloist runs on the touchscreen's Pi and takes a key at every start, and a key is its person's own. The household has
no accounts: the touchscreen names its people, asks for a code for one, and they paste their key on their phone at
/soloist with no login. A touchscreen is only ever sent keys saved for it, as registering a device is open to anyone.
"""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from countdown_api.models import SoloistApiKey, SoloistKeyCode, SoloistKeys
from cryptography.fernet import Fernet

from broker.db import db
from broker.models import Device, SoloistCode, SoloistKey
from broker.services import pairing, soloist
from tests.conftest import USER
from tests.routes.test_touchscreens import SECRET, TOUCHSCREEN, _auth, _paired, _register, _registered

CODES_URL = "/api/devices/soloist/codes"
KEYS_URL = "/api/devices/soloist/keys"
KEY_URL = "/api/devices/soloist/key"
PAGE_URL = "/soloist"
PERSON = TOUCHSCREEN["soloist_code_request"]["person"]
KEY = TOUCHSCREEN["soloist_key"]["api_key"]
SPENT = "expired or has already been used"


@pytest.fixture
def phone(app):
    """Return a browser that isn't signed in to anything: a person's phone."""
    return app.test_client()


def _code(client, person=PERSON, secret=SECRET):
    response = client.post(CODES_URL, json={"person": person}, headers=_auth(secret))
    assert response.status_code == 201
    return response.get_json()["code"]


def _paste(phone, code, key=KEY):
    return phone.post(PAGE_URL, data={"code": code, "api_key": key})


def _saved(client, phone, person=PERSON, key=KEY):
    """Have the person save `key` for the client's touchscreen with a fresh code."""
    assert "Your key is saved" in _paste(phone, _code(client, person), key).get_data(as_text=True)


def _key(client, person=PERSON, secret=SECRET):
    return client.get(KEY_URL, query_string={"person": person}, headers=_auth(secret))


def _other_touchscreen(app, client):
    """Register and pair a second touchscreen, someone else's; return its secret."""
    secret = "another-touchscreens-secret-0001"
    response = _register(client, {**TOUCHSCREEN["registration"], "secret": secret})
    assert response.status_code == 201
    with app.app_context():
        device = db.session.get_one(Device, response.get_json()["device_id"])
        device.owner = "someone-else"
        device.paired_at = datetime.now(UTC)
        db.session.commit()
    return secret


# --- the code ---------------------------------------------------------------------------------------


def test_a_paired_touchscreen_gets_a_code_as_the_contract_has_it(app, client):
    _paired(app, client)

    response = client.post(CODES_URL, json=TOUCHSCREEN["soloist_code_request"], headers=_auth())

    assert response.status_code == 201
    code = SoloistKeyCode.model_validate(response.get_json())
    assert code.expires_in == TOUCHSCREEN["soloist_code"]["expires_in"]
    assert len(code.code) == len(TOUCHSCREEN["soloist_code"]["code"])


@pytest.mark.parametrize("body", [None, {}, {"person": ""}, {"person": "a b"}, {"person": "a/b"}, {"person": 7}, []])
def test_a_code_needs_a_person_the_touchscreen_can_name(app, client, body):
    _paired(app, client)

    assert client.post(CODES_URL, json=body, headers=_auth()).status_code == 400


def test_a_person_too_long_to_be_a_name_is_refused(app, client):
    _paired(app, client)

    assert client.post(CODES_URL, json={"person": "x" * 129}, headers=_auth()).status_code == 400
    assert client.post(CODES_URL, json={"person": "x" * 128}, headers=_auth()).status_code == 201


def test_every_route_needs_the_devices_secret(app, client):
    _paired(app, client)

    assert client.post(CODES_URL, json={"person": PERSON}).status_code == 401
    assert client.get(KEYS_URL).status_code == 401
    assert client.delete(f"{KEYS_URL}/{PERSON}").status_code == 401
    assert client.get(KEY_URL, query_string={"person": PERSON}).status_code == 401
    assert _key(client, secret="not-the-devices-secret").status_code == 401


def test_an_unpaired_touchscreen_or_an_epaper_screen_has_no_keys(app, client):
    _registered(app, client)
    epaper = TOUCHSCREEN["epaper_registration"]["secret"]
    assert _register(client, TOUCHSCREEN["epaper_registration"]).status_code == 201
    assert epaper != SECRET

    for secret in (SECRET, epaper):
        assert client.post(CODES_URL, json={"person": PERSON}, headers=_auth(secret)).status_code == 409
        assert client.get(KEYS_URL, headers=_auth(secret)).status_code == 409
        assert client.delete(f"{KEYS_URL}/{PERSON}", headers=_auth(secret)).status_code == 409
        assert _key(client, secret=secret).status_code == 409


# --- the page ---------------------------------------------------------------------------------------


def test_the_page_needs_no_login_and_opening_it_saves_nothing(app, client, phone):
    _paired(app, client)
    code = _code(client)

    page = phone.get(PAGE_URL, query_string={"code": code})

    assert page.status_code == 200
    assert "Paste your key" in page.get_data(as_text=True)
    assert client.get(KEYS_URL, headers=_auth()).get_json() == {"people": []}
    # Nor does opening it use the code up.
    assert "Paste your key" in phone.get(PAGE_URL, query_string={"code": code}).get_data(as_text=True)


def test_pasting_the_key_saves_it_for_that_person_and_never_shows_it(app, client, phone):
    _paired(app, client)

    page = _paste(phone, _code(client), f" {KEY} ")

    assert "Your key is saved" in page.get_data(as_text=True)
    assert KEY not in page.get_data(as_text=True)
    listed = client.get(KEYS_URL, headers=_auth())
    assert listed.get_json() == TOUCHSCREEN["soloist_keys"]
    assert SoloistKeys.model_validate(listed.get_json()).people == [PERSON]
    assert KEY not in listed.get_data(as_text=True)


def test_a_code_works_once(app, client, phone):
    _paired(app, client)
    code = _code(client)
    _paste(phone, code)

    assert SPENT in _paste(phone, code, "someone-elses-key").get_data(as_text=True)
    assert SPENT in phone.get(PAGE_URL, query_string={"code": code}).get_data(as_text=True)
    assert _key(client).get_json() == {"api_key": KEY}


def test_a_lapsed_or_unknown_code_saves_nothing(app, client, phone):
    _paired(app, client)
    code = _code(client)
    with app.app_context():
        db.session.get_one(SoloistCode, code).expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.session.commit()

    for spent in (code, "f" * 32, ""):
        assert SPENT in _paste(phone, spent).get_data(as_text=True)
        assert SPENT in phone.get(PAGE_URL, query_string={"code": spent}).get_data(as_text=True)
    assert client.get(KEYS_URL, headers=_auth()).get_json() == {"people": []}


@pytest.mark.parametrize("key", ["", "   ", "k" * 513])
def test_an_empty_or_overlong_key_is_asked_for_again_with_the_code_unspent(app, client, phone, key):
    _paired(app, client)
    code = _code(client)

    assert "Paste the key from your dashboard" in _paste(phone, code, key).get_data(as_text=True)

    assert client.get(KEYS_URL, headers=_auth()).get_json() == {"people": []}
    assert "Your key is saved" in _paste(phone, code).get_data(as_text=True)


def test_a_new_code_for_a_person_replaces_theirs_and_no_one_elses(app, client, phone):
    _paired(app, client)
    first = _code(client)
    others = _code(client, "someone-else")
    second = _code(client)

    assert SPENT in _paste(phone, first).get_data(as_text=True)
    assert "Your key is saved" in _paste(phone, others, "the-others-key").get_data(as_text=True)
    assert "Your key is saved" in _paste(phone, second).get_data(as_text=True)


def test_saving_again_replaces_the_persons_key(app, client, phone):
    _paired(app, client)
    _saved(client, phone)
    _saved(client, phone, key="a-newer-key")

    assert _key(client).get_json() == {"api_key": "a-newer-key"}
    assert client.get(KEYS_URL, headers=_auth()).get_json() == {"people": [PERSON]}


# --- the key ----------------------------------------------------------------------------------------


def test_the_touchscreen_gets_each_persons_own_key_as_the_contract_has_it(app, client, phone):
    _paired(app, client)
    _saved(client, phone)
    _saved(client, phone, "someone-else", "the-others-key")

    response = client.get(KEY_URL, query_string=TOUCHSCREEN["soloist_key_query"], headers=_auth())

    assert response.status_code == 200
    assert response.get_json() == TOUCHSCREEN["soloist_key"]
    assert SoloistApiKey.model_validate(response.get_json()).api_key == KEY
    assert response.headers["Cache-Control"] == "no-store"
    assert _key(client, "someone-else").get_json() == {"api_key": "the-others-key"}
    assert sorted(client.get(KEYS_URL, headers=_auth()).get_json()["people"]) == sorted([PERSON, "someone-else"])


def test_a_person_with_no_key_has_none_and_no_person_is_a_400(app, client, phone):
    _paired(app, client)
    _saved(client, phone)

    assert _key(client, "someone-else").status_code == 404
    assert client.get(KEY_URL, headers=_auth()).status_code == 400


def test_a_touchscreen_is_never_sent_a_key_saved_for_another(app, client, phone):
    _paired(app, client)
    _saved(client, phone)
    other = _other_touchscreen(app, client)

    # Anyone can register a device and ask for a person by name: it gets only what was saved for it.
    response = _key(client, secret=other)
    assert response.status_code == 404
    assert KEY not in response.get_data(as_text=True)
    assert client.get(KEYS_URL, headers=_auth(other)).get_json() == {"people": []}
    # Nor can it forget the other's.
    assert client.delete(f"{KEYS_URL}/{PERSON}", headers=_auth(other)).status_code == 204
    assert _key(client).get_json() == {"api_key": KEY}


def test_the_key_is_encrypted_at_rest_and_never_logged(app, client, phone, caplog):
    device_id = _paired(app, client)
    with caplog.at_level(logging.DEBUG):
        _saved(client, phone)
        assert _key(client).status_code == 200

    assert KEY not in caplog.text
    with app.app_context():
        assert KEY not in db.session.get_one(SoloistKey, (device_id, PERSON)).key_encrypted


def test_a_key_that_wont_decrypt_is_as_good_as_none(app, client, caplog):
    device_id = _paired(app, client)
    with app.app_context():
        # Saved under another CREDENTIAL_ENCRYPTION_KEY.
        elsewhere = Fernet(Fernet.generate_key()).encrypt(b"k").decode()
        db.session.add(SoloistKey(device_id=device_id, person=PERSON, key_encrypted=elsewhere))
        db.session.commit()

    with caplog.at_level(logging.WARNING):
        response = _key(client)

    assert response.status_code == 404
    assert "won't decrypt" in caplog.text


# --- forgetting -------------------------------------------------------------------------------------


def test_the_touchscreen_forgets_a_persons_key_and_their_unused_code(app, client, phone):
    _paired(app, client)
    _saved(client, phone)
    _saved(client, phone, "someone-else", "the-others-key")
    unused = _code(client)

    assert client.delete(f"{KEYS_URL}/{PERSON}", headers=_auth()).status_code == 204

    assert _key(client).status_code == 404
    assert SPENT in _paste(phone, unused).get_data(as_text=True)
    assert client.get(KEYS_URL, headers=_auth()).get_json() == {"people": ["someone-else"]}
    # Forgetting them again changes nothing.
    assert client.delete(f"{KEYS_URL}/{PERSON}", headers=_auth()).status_code == 204


def test_a_touchscreen_that_changes_hands_keeps_none_of_its_keys(app, client, phone):
    device_id = _paired(app, client)
    _saved(client, phone)
    unused = _code(client, "someone-else")
    with app.app_context():
        assert pairing.redeem_code(pairing.issue_code(device_id), "new-owner") == device_id

    assert _key(client).status_code == 404
    assert client.get(KEYS_URL, headers=_auth()).get_json() == {"people": []}
    # Nor can a code its last owner's household was shown still save one.
    assert SPENT in _paste(phone, unused).get_data(as_text=True)


def test_pairing_again_as_the_same_owner_keeps_the_keys(app, client, phone):
    device_id = _paired(app, client)
    _saved(client, phone)
    with app.app_context():
        assert pairing.redeem_code(pairing.issue_code(device_id), USER) == device_id

    assert _key(client).get_json() == {"api_key": KEY}


def test_unpairing_from_the_command_line_drops_the_keys(app, client, phone, cli):
    device_id = _paired(app, client)
    _saved(client, phone)

    assert cli("unpair", device_id).exit_code == 0

    with app.app_context():
        assert soloist.people(device_id) == []


def test_forgetting_a_device_takes_its_keys_and_codes(app, client, phone):
    device_id = _paired(app, client)
    _saved(client, phone)
    _code(client, "someone-else")
    with app.app_context():
        db.session.delete(db.session.get_one(Device, device_id))
        db.session.commit()

        assert db.session.scalars(db.select(SoloistKey)).all() == []
        assert db.session.scalars(db.select(SoloistCode)).all() == []
