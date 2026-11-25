"""Test a new touchscreen's setup on its owner's phone from its one QR code: pair, Spotify, the Soloist key, done.

After pairing, the phone lands on /device/setup, which sends it to whatever the touchscreen has asked for next, and
the Spotify callback and the Soloist page send it back there while the screen has no key yet.
"""

from tests.conftest import USER
from tests.routes.test_touchscreen_soloist import CODES_URL, KEY, PAGE_URL, PERSON, _paste
from tests.routes.test_touchscreen_spotify_sign_in import ASKED, CALLBACK_URL, CODE, SIGN_INS_URL
from tests.routes.test_touchscreens import _auth, _paired, _registered

SETUP_URL = "/device/setup"


def _sign_in(client):
    response = client.post(SIGN_INS_URL, json=ASKED, headers=_auth())
    assert response.status_code == 201
    return response.get_json()["id"]


def _code(client):
    response = client.post(CODES_URL, json={"person": PERSON}, headers=_auth())
    assert response.status_code == 201
    return response.get_json()["code"]


def test_the_phone_waits_until_the_touchscreen_asks_for_a_sign_in(app, client):
    _paired(app, client)

    page = client.get(SETUP_URL)

    assert page.status_code == 200
    assert page.headers["Cache-Control"] == "no-store"
    assert "location.reload()" in page.get_data(as_text=True)


def test_the_whole_visit_runs_from_pairing_to_ready(app, client, anonymous_client):
    device_id = _paired(app, client)
    sign_in = _sign_in(client)

    to_spotify = client.get(SETUP_URL)
    assert to_spotify.status_code == 302
    assert to_spotify.headers["Location"] == f"/spotify/sign-in?id={sign_in}"

    # Back from Spotify on the public callback, which sends the phone on while the screen has no key.
    back = anonymous_client.get(CALLBACK_URL, query_string={"state": sign_in, "code": CODE})
    assert back.status_code == 302
    assert back.headers["Location"] == f"/device/{device_id}/setup"
    # The touchscreen hasn't asked for a code yet: wait.
    assert client.get(back.headers["Location"]).status_code == 200

    code = _code(client)
    to_soloist = client.get(f"/device/{device_id}/setup")
    assert to_soloist.headers["Location"] == f"{PAGE_URL}?code={code}"

    saved = _paste(anonymous_client, code)
    assert saved.status_code == 303
    assert saved.headers["Location"] == f"/device/{device_id}/setup"

    done = client.get(saved.headers["Location"])
    assert done.status_code == 302
    assert done.headers["Location"] == "/pair/done"


def test_a_touchscreen_with_a_key_is_set_up_so_its_pages_say_so_as_before(app, client, anonymous_client):
    _paired(app, client)
    _paste(anonymous_client, _code(client))

    sign_in = _sign_in(client)
    page = anonymous_client.get(CALLBACK_URL, query_string={"state": sign_in, "code": CODE})
    assert page.status_code == 200
    assert "signed in on the touchscreen" in page.get_data(as_text=True)

    second = client.post(CODES_URL, json={"person": "someone-else"}, headers=_auth()).get_json()["code"]
    assert "Your key is saved" in _paste(anonymous_client, second, KEY).get_data(as_text=True)


def test_a_refused_sign_in_does_not_go_on(app, client, anonymous_client):
    _paired(app, client)
    sign_in = _sign_in(client)

    page = anonymous_client.get(CALLBACK_URL, query_string={"state": sign_in, "error": "access_denied"})

    assert page.status_code == 200
    assert "didn't sign you in" in page.get_data(as_text=True)


def test_an_unpaired_touchscreens_sign_in_does_not_go_on(app, client, anonymous_client):
    _registered(app, client)
    sign_in = _sign_in(client)

    page = anonymous_client.get(CALLBACK_URL, query_string={"state": sign_in, "code": CODE})

    assert page.status_code == 200


def test_only_its_owner_sees_a_screens_setup(app, client, anonymous_client):
    device_id = _paired(app, client)
    someone = app.test_client()
    someone.environ_base["HTTP_REMOTE_SUB"] = f"not-{USER}"

    assert someone.get(f"/device/{device_id}/setup").status_code == 404
    assert anonymous_client.get(f"/device/{device_id}/setup").status_code == 401
