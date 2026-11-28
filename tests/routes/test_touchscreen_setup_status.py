"""Test a touchscreen's setup that it and its owner's phone both follow: GET and PUT /api/devices/setup and the phone.

A touchscreen takes part by reporting (PUT), first as soon as it has registered, with its first Spotify sign-in's
challenge. From then on the broker works out the step, both ask for it, and the phone's /device/<id>/setup goes
wherever the step says. A touchscreen that never reports is set up as before (test_touchscreen_setup.py).
"""

from datetime import UTC, datetime, timedelta

from countdown_api.models import SetupStatus
from sqlalchemy import delete

from broker.db import db
from broker.models import Device, SoloistKey, SpotifySignIn, TouchscreenSetup
from broker.services import pairing, touchscreen_setup
from tests.conftest import USER
from tests.routes.test_touchscreen_soloist import CODES_URL, PAGE_URL, PERSON, _paste
from tests.routes.test_touchscreen_spotify_sign_in import CALLBACK_URL, CODE, SIGN_INS_URL
from tests.routes.test_touchscreens import TOUCHSCREEN, _auth, _registered

SETUP_URL = "/api/devices/setup"
START = TOUCHSCREEN["setup_start"]
DEVICE_NAME = TOUCHSCREEN["setup_soloist_report"]["soloist_device"]


def _report(client, body):
    response = client.put(SETUP_URL, json=body, headers=_auth())
    assert response.status_code == 200
    return SetupStatus.model_validate(response.get_json())


def _status(client):
    response = client.get(SETUP_URL, headers=_auth())
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    return SetupStatus.model_validate(response.get_json())


def _pair(app, client, device_id):
    with app.app_context():
        code = pairing.issue_code(device_id)
    assert client.post("/pair/add", data={"code": code}).status_code == 302


def _following(app, client, start=START):
    """Register the contract's touchscreen, have it report as it does at once, and pair it; return its device id."""
    device_id = _registered(app, client)
    assert _report(client, start).step == "account"
    _pair(app, client, device_id)
    return device_id


def _phone(client, device_id):
    return client.get(f"/device/{device_id}/setup")


def _to_spotify(client, device_id):
    """Send the phone to its setup page at the Spotify step; return the id of the sign-in it is sent on to."""
    response = _phone(client, device_id)
    assert response.status_code == 302
    prefix = "/spotify/sign-in?id="
    assert response.headers["Location"].startswith(prefix)
    return response.headers["Location"].removeprefix(prefix)


def _signed_in(client, anonymous_client, device_id):
    """Take the phone to Spotify and back, and have the touchscreen take the code; return the sign-in's id."""
    sign_in = _to_spotify(client, device_id)
    back = anonymous_client.get(CALLBACK_URL, query_string={"state": sign_in, "code": CODE})
    assert back.headers["Location"] == f"/device/{device_id}/setup"
    taken = client.get(f"{SIGN_INS_URL}/{sign_in}", headers=_auth())
    assert taken.get_json() == {"status": "done", "code": CODE}
    return sign_in


def _key_saved(client, anonymous_client, device_id):
    """Go through Spotify and the Soloist key, as far as the step the touchscreen's Soloist signs in at."""
    _signed_in(client, anonymous_client, device_id)
    code = client.post(CODES_URL, json={"person": PERSON}, headers=_auth()).get_json()["code"]
    assert _paste(anonymous_client, code).headers["Location"] == f"/device/{device_id}/setup"


# --- taking part ----------------------------------------------------------------------------------


def test_a_touchscreen_that_has_reported_nothing_takes_no_part(app, client):
    _registered(app, client)

    response = client.get(SETUP_URL, headers=_auth())

    assert response.status_code == 404
    assert response.get_json()["error"]


def test_its_first_report_comes_before_it_is_paired_and_keeps_its_challenge(app, client):
    device_id = _registered(app, client)

    status = _report(client, START)

    assert status == SetupStatus.model_validate(TOUCHSCREEN["setup_account"])
    assert _status(client).step == "account"
    with app.app_context():
        setup = db.session.get_one(TouchscreenSetup, device_id)
        assert (setup.spotify_client_id, setup.spotify_code_challenge, setup.spotify_scope) == (
            START["spotify"]["client_id"],
            START["spotify"]["code_challenge"],
            START["spotify"]["scope"],
        )


def test_an_empty_report_only_says_it_takes_part(app, client):
    _registered(app, client)

    assert _report(client, {}).step == "account"


def test_a_report_keeps_what_it_leaves_out(app, client):
    device_id = _following(app, client)
    _report(client, {"soloist_device": DEVICE_NAME})

    status = _report(client, {"phone_paired": True})

    assert status.soloist_device == DEVICE_NAME
    with app.app_context():
        setup = db.session.get_one(TouchscreenSetup, device_id)
        assert setup.phone_paired
        assert setup.spotify_code_challenge == START["spotify"]["code_challenge"]


def test_a_report_that_isnt_one_is_refused(app, client):
    _registered(app, client)

    for wrong in ({"step": "done"}, {"finished": "yes"}, {"soloist_device": ""}, {"spotify": {"client_id": "x"}}):
        assert client.put(SETUP_URL, json=wrong, headers=_auth()).status_code == 400
    assert client.put(SETUP_URL, data="not json", headers=_auth()).status_code == 400
    assert client.get(SETUP_URL, headers=_auth()).status_code == 404


def test_an_e_paper_screen_has_no_setup(register_device, anonymous_client):
    _, secret = register_device()

    assert anonymous_client.get(SETUP_URL, headers=_auth(secret)).status_code == 409
    assert anonymous_client.put(SETUP_URL, json={}, headers=_auth(secret)).status_code == 409


def test_both_need_the_device_or_its_owner(app, client, anonymous_client):
    device_id = _following(app, client)
    someone = app.test_client()
    someone.environ_base["HTTP_REMOTE_SUB"] = f"not-{USER}"

    # Its owner's phone asks with their login, on the settings site.
    owners = client.get(SETUP_URL, query_string={"device_id": device_id})
    assert owners.status_code == 200
    assert owners.get_json()["step"] == "spotify"
    assert client.get(SETUP_URL).status_code == 400
    assert someone.get(SETUP_URL, query_string={"device_id": device_id}).status_code == 404
    assert anonymous_client.get(SETUP_URL, query_string={"device_id": device_id}).status_code == 401
    # Only the touchscreen reports.
    assert client.put(SETUP_URL, json={}).status_code == 401
    assert anonymous_client.put(SETUP_URL, json={}, headers=_auth("not-its-secret-at-all")).status_code == 401


# --- the steps, in order --------------------------------------------------------------------------


def test_the_whole_setup_runs_in_step_from_pairing_to_done(app, client, anonymous_client):  # noqa: PLR0915 -- one visit, start to end
    device_id = _following(app, client)
    # Paired: the phone hasn't got to Spotify yet, so there is no sign-in to tell the touchscreen of.
    assert _status(client) == SetupStatus(step="spotify")

    # The phone goes straight on to Spotify, on a sign-in the broker made from the touchscreen's challenge.
    sign_in = _to_spotify(client, device_id)
    told = _status(client)
    assert told.step == "spotify"
    assert told.spotify_sign_in is not None
    assert told.spotify_sign_in.id == sign_in
    assert str(told.spotify_sign_in.redirect_uri).endswith(CALLBACK_URL)
    assert 0 < told.spotify_sign_in.expires_in <= 600
    with app.app_context():
        made = db.session.get_one(SpotifySignIn, sign_in)
        assert (made.device_id, made.code_challenge) == (device_id, START["spotify"]["code_challenge"])
    # Asking again doesn't make another.
    assert _to_spotify(client, device_id) == sign_in

    # Back from Spotify the phone waits, on the same step, while the touchscreen takes the code.
    back = anonymous_client.get(CALLBACK_URL, query_string={"state": sign_in, "code": CODE})
    assert back.headers["Location"] == f"/device/{device_id}/setup"
    waiting = _phone(client, device_id)
    assert waiting.status_code == 200
    assert waiting.headers["Cache-Control"] == "no-store"
    html = waiting.get_data(as_text=True)
    assert 'data-step="spotify"' in html
    assert 'data-reload-after-s="62"' in html
    assert f'data-status-url="{SETUP_URL}?device_id={device_id}"' in html
    assert "touchscreen_setup.js" in html
    # The touchscreen is still told which sign-in, to take its code.
    assert _status(client).spotify_sign_in.id == sign_in
    assert client.get(f"{SIGN_INS_URL}/{sign_in}", headers=_auth()).get_json() == {"status": "done", "code": CODE}
    assert _status(client) == SetupStatus(step="spotify")

    # The touchscreen asks for its person's key code, which says they are signed in: the phone goes to paste the key.
    code = client.post(CODES_URL, json={"person": PERSON}, headers=_auth()).get_json()["code"]
    assert _status(client).step == "soloist_key"
    assert _phone(client, device_id).headers["Location"] == f"{PAGE_URL}?code={code}"
    assert _paste(anonymous_client, code).headers["Location"] == f"/device/{device_id}/setup"

    # Its Soloist signs in when they pick it in Spotify, by the name the touchscreen reported.
    assert _report(client, {"soloist_device": DEVICE_NAME}).step == "soloist_sign_in"
    pick = _phone(client, device_id).get_data(as_text=True)
    assert 'data-step="soloist_sign_in"' in pick
    assert f"<strong>{DEVICE_NAME}</strong>" in pick
    assert "https://open.spotify.com/" in pick
    assert "data-reload-after-s" not in pick

    # Then where it is, which the owner may leave for later.
    located = _report(client, TOUCHSCREEN["setup_soloist_report"])
    assert located == SetupStatus.model_validate(TOUCHSCREEN["setup_location"])
    where = _phone(client, device_id).get_data(as_text=True)
    assert 'data-step="location"' in where
    assert f'action="/device/{device_id}/setup/location/skip"' in where
    skipped = client.post(f"/device/{device_id}/setup/location/skip")
    assert skipped.status_code == 303
    assert skipped.headers["Location"] == f"/device/{device_id}/setup"

    # Then Bluetooth, which the touchscreen does, and the phone says the same.
    assert _status(client).step == "presence"
    bluetooth = _phone(client, device_id).get_data(as_text=True)
    assert 'data-step="presence"' in bluetooth
    assert "Bluetooth" in bluetooth

    done = _report(client, TOUCHSCREEN["setup_finish"])
    assert done == SetupStatus.model_validate(TOUCHSCREEN["setup_done"])
    assert _phone(client, device_id).headers["Location"] == "/pair/done"


def test_every_step_shows_until_the_touchscreen_says_it_has_finished(app, client, anonymous_client):
    device_id = _following(app, client)
    _key_saved(client, anonymous_client, device_id)
    _report(client, {"soloist_signed_in": True})
    client.post(f"/device/{device_id}/setup/location/skip")

    # Its phone paired is the last step, so the setup is done with it, and stays done.
    assert _report(client, {"phone_paired": True}).step == "done"
    assert _report(client, {"phone_paired": False}).step == "presence"
    assert _report(client, {"finished": True}).step == "done"
    assert _report(client, {"soloist_signed_in": False}).step == "done"


def test_finishing_ends_the_setup_wherever_it_was(app, client):
    device_id = _following(app, client)

    assert _report(client, {"finished": True}).step == "done"

    assert _phone(client, device_id).headers["Location"] == "/pair/done"
    with app.app_context():
        first = db.session.get_one(TouchscreenSetup, device_id).finished_at
    # Said again, it finished when it first said so; taken back, it is being set up again.
    _report(client, {"finished": True})
    with app.app_context():
        assert db.session.get_one(TouchscreenSetup, device_id).finished_at == first
    assert _report(client, {"finished": False}).step == "spotify"


def test_skipping_the_location_twice_changes_nothing(app, client):
    device_id = _following(app, client)
    client.post(f"/device/{device_id}/setup/location/skip")
    with app.app_context():
        first = db.session.get_one(TouchscreenSetup, device_id).location_done_at

    client.post(f"/device/{device_id}/setup/location/skip")

    with app.app_context():
        assert db.session.get_one(TouchscreenSetup, device_id).location_done_at == first


def test_only_its_owner_skips_a_screens_location(app, client, anonymous_client):
    device_id = _following(app, client)
    someone = app.test_client()
    someone.environ_base["HTTP_REMOTE_SUB"] = f"not-{USER}"

    assert someone.post(f"/device/{device_id}/setup/location/skip").status_code == 404
    assert anonymous_client.post(f"/device/{device_id}/setup/location/skip").status_code == 401
    with app.app_context():
        assert db.session.get_one(TouchscreenSetup, device_id).location_done_at is None


# --- the Spotify step's corners --------------------------------------------------------------------


def test_the_phone_waits_while_the_touchscreen_has_reported_no_challenge(app, client):
    device_id = _following(app, client, start={})

    page = _phone(client, device_id)

    assert page.status_code == 200
    assert 'data-step="spotify"' in page.get_data(as_text=True)
    assert _status(client) == SetupStatus(step="spotify")
    # Once it has, the phone goes on.
    _report(client, START)
    assert _to_spotify(client, device_id)


def test_a_sign_in_that_lapses_is_made_again(app, client):
    device_id = _following(app, client)
    first = _to_spotify(client, device_id)
    with app.app_context():
        db.session.get_one(SpotifySignIn, first).expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.session.commit()
    assert _status(client) == SetupStatus(step="spotify")

    second = _to_spotify(client, device_id)

    assert second != first
    assert _status(client).spotify_sign_in.id == second


def test_the_phone_signs_in_again_if_the_touchscreen_never_catches_up(app, client, anonymous_client):
    device_id = _following(app, client)
    first = _signed_in(client, anonymous_client, device_id)
    assert _phone(client, device_id).status_code == 200
    with app.app_context():
        setup = db.session.get_one(TouchscreenSetup, device_id)
        setup.spotify_answered_at = datetime.now(UTC) - touchscreen_setup.ANSWER_WAIT - timedelta(seconds=1)
        db.session.commit()

    again = _to_spotify(client, device_id)

    assert again != first


def test_a_refused_sign_in_can_be_tried_again_from_the_phone(app, client, anonymous_client):
    device_id = _following(app, client)
    sign_in = _to_spotify(client, device_id)

    page = anonymous_client.get(CALLBACK_URL, query_string={"state": sign_in, "error": "access_denied"})

    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "didn't sign you in" in html
    assert f'href="/device/{device_id}/setup"' in html
    assert 'id="closePage"' not in html
    # The touchscreen is told it failed, and the phone's next go is a new sign-in at once.
    assert client.get(f"{SIGN_INS_URL}/{sign_in}", headers=_auth()).get_json()["status"] == "failed"
    assert _to_spotify(client, device_id) != sign_in


def test_a_refused_sign_in_on_a_touchscreen_that_takes_no_part_ends_as_before(app, client, anonymous_client):
    device_id = _registered(app, client)
    _pair(app, client, device_id)
    asked = client.post(SIGN_INS_URL, json=TOUCHSCREEN["spotify_sign_in_request"], headers=_auth())

    page = anonymous_client.get(CALLBACK_URL, query_string={"state": asked.get_json()["id"], "error": "access_denied"})

    html = page.get_data(as_text=True)
    assert "You can close this page." in html
    assert "Try again" not in html


def test_the_phone_waits_for_a_key_code_that_has_lapsed_to_be_asked_for_again(app, client, anonymous_client):
    device_id = _following(app, client)
    _key_saved(client, anonymous_client, device_id)
    assert _status(client).step == "soloist_sign_in"
    with app.app_context():
        db.session.execute(delete(SoloistKey).where(SoloistKey.device_id == device_id))
        db.session.commit()

    # No key and no code: the touchscreen asks for another once it has someone signed in, so the step is Spotify's.
    assert _status(client).step == "spotify"


# --- a change of owner ----------------------------------------------------------------------------


def test_a_touchscreen_that_changes_hands_starts_its_setup_over(app, client, anonymous_client):
    device_id = _following(app, client)
    _key_saved(client, anonymous_client, device_id)
    _report(client, {"soloist_signed_in": True, "phone_paired": True, "finished": True})
    client.post(f"/device/{device_id}/setup/location/skip")

    with app.app_context():
        device = db.session.get_one(Device, device_id)
        pairing.claim(device, "someone-new")
        db.session.commit()

    with app.app_context():
        setup = db.session.get_one(TouchscreenSetup, device_id)
        assert (setup.soloist_signed_in, setup.phone_paired) == (False, False)
        assert (setup.location_done_at, setup.finished_at, setup.spotify_answered_at) == (None, None, None)
        # Its challenge is its own, so its new owner's phone still goes straight to Spotify.
        assert setup.spotify_code_challenge == START["spotify"]["code_challenge"]
    assert _status(client).step == "spotify"


def test_a_touchscreen_that_takes_no_part_changes_hands_as_before(app, client):
    device_id = _registered(app, client)
    _pair(app, client, device_id)

    with app.app_context():
        pairing.claim(db.session.get_one(Device, device_id), "someone-new")
        db.session.commit()
        assert db.session.get(TouchscreenSetup, device_id) is None


def test_forgetting_a_touchscreen_takes_its_setup_with_it(app, client):
    device_id = _following(app, client)

    with app.app_context():
        db.session.delete(db.session.get_one(Device, device_id))
        db.session.commit()
        assert db.session.get(TouchscreenSetup, device_id) is None
