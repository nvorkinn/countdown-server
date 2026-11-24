"""Test a touchscreen's people signing in to Spotify on their phones.

The touchscreen asks for a sign-in with its PKCE challenge and shows /spotify/sign-in as a code to scan; the phone is
sent on to Spotify and comes back to the callback, which keeps Spotify's code; the touchscreen takes it once and
exchanges it with its own verifier. Nothing here talks to Spotify.
"""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from countdown_api.models import SpotifySignIn, SpotifySignInResult

from broker.db import db
from broker.models import SpotifySignIn as StoredSignIn
from tests.routes.test_touchscreens import SECRET, TOUCHSCREEN, _auth, _register, _registered

SIGN_INS_URL = "/api/devices/spotify/sign-ins"
PAGE_URL = "/spotify/sign-in"
CALLBACK_URL = "/spotify/sign-in/callback"
ASKED = TOUCHSCREEN["spotify_sign_in_request"]
CODE = TOUCHSCREEN["spotify_sign_in_done"]["code"]
SITE = "https://app.countdown.build"


@pytest.fixture
def phone(app):
    """Return a browser that isn't signed in to anything: a person's phone."""
    return app.test_client()


def _start(client, secret=SECRET, body=ASKED):
    response = client.post(SIGN_INS_URL, json=body, headers=_auth(secret))
    assert response.status_code == 201
    return SpotifySignIn.model_validate(response.get_json())


def _status(client, sign_in_id, secret=SECRET):
    return client.get(f"{SIGN_INS_URL}/{sign_in_id}", headers=_auth(secret))


def test_a_touchscreen_starts_a_sign_in_as_the_contract_has_it(app, client):
    _registered(app, client)
    app.config["SITE_URL"] = SITE

    sign_in = _start(client)

    assert sign_in.expires_in == TOUCHSCREEN["spotify_sign_in"]["expires_in"]
    assert sign_in.redirect_uri == TOUCHSCREEN["spotify_sign_in"]["redirect_uri"]
    assert len(sign_in.id) == len(TOUCHSCREEN["spotify_sign_in"]["id"])


def test_without_a_site_url_the_redirect_is_the_callback_on_this_host(app, client):
    _registered(app, client)
    app.config["SITE_URL"] = ""

    assert _start(client).redirect_uri.endswith(CALLBACK_URL)


@pytest.mark.parametrize(
    "body",
    [
        None,
        {},
        {**ASKED, "code_challenge": "short"},
        {**ASKED, "client_id": "not/an id"},
        {**ASKED, "scope": "streaming\nevil"},
        {**ASKED, "code_verifier": "never sent"},
    ],
)
def test_a_request_that_isnt_one_is_refused(app, client, body):
    _registered(app, client)

    response = client.post(SIGN_INS_URL, json=body, headers=_auth())

    assert response.status_code == 400


def test_an_epaper_screen_does_not_sign_in_to_spotify(client):
    secret = TOUCHSCREEN["epaper_registration"]["secret"]
    assert _register(client, TOUCHSCREEN["epaper_registration"]).status_code == 201

    response = client.post(SIGN_INS_URL, json=ASKED, headers=_auth(secret))

    assert response.status_code == 409


def test_the_phone_is_sent_on_to_spotify_with_what_the_touchscreen_asked_for(app, client, phone):
    _registered(app, client)
    app.config["SITE_URL"] = SITE
    sign_in = _start(client)

    response = phone.get(PAGE_URL, query_string={"id": sign_in.id})

    assert response.status_code == 302
    spotify = urlsplit(response.headers["Location"])
    assert f"{spotify.scheme}://{spotify.netloc}{spotify.path}" == "https://accounts.spotify.com/authorize"
    assert {name: values[0] for name, values in parse_qs(spotify.query).items()} == {
        "response_type": "code",
        "client_id": ASKED["client_id"],
        "redirect_uri": sign_in.redirect_uri,
        "state": sign_in.id,
        "code_challenge_method": "S256",
        "code_challenge": ASKED["code_challenge"],
        "scope": ASKED["scope"],
    }


def test_the_touchscreen_waits_then_takes_spotifys_code_once(app, client, phone):
    _registered(app, client)
    sign_in = _start(client)

    waiting = _status(client, sign_in.id)
    assert waiting.status_code == 200
    assert waiting.get_json() == TOUCHSCREEN["spotify_sign_in_waiting"]

    page = phone.get(CALLBACK_URL, query_string={"state": sign_in.id, "code": CODE})
    assert page.status_code == 200
    assert "signed in on the touchscreen" in page.get_data(as_text=True)

    done = _status(client, sign_in.id)
    assert done.status_code == 200
    assert done.headers["Cache-Control"] == "no-store"
    assert SpotifySignInResult.model_validate(done.get_json()) == SpotifySignInResult(status="done", code=CODE)
    # Once: the code isn't answered again.
    assert _status(client, sign_in.id).status_code == 404


def test_a_refusal_is_answered_as_failed(app, client, phone):
    _registered(app, client)
    sign_in = _start(client)

    page = phone.get(CALLBACK_URL, query_string={"state": sign_in.id, "error": "access_denied"})

    assert "didn't sign you in" in page.get_data(as_text=True)
    assert _status(client, sign_in.id).get_json() == {"status": "failed", "error": "access_denied"}


def test_a_sign_in_comes_back_once(app, client, phone):
    _registered(app, client)
    sign_in = _start(client)
    phone.get(CALLBACK_URL, query_string={"state": sign_in.id, "code": CODE})

    again = phone.get(CALLBACK_URL, query_string={"state": sign_in.id, "code": "another-code"})
    scanned_again = phone.get(PAGE_URL, query_string={"id": sign_in.id})

    assert "expired" in again.get_data(as_text=True)
    assert scanned_again.status_code == 200
    assert "expired" in scanned_again.get_data(as_text=True)
    assert _status(client, sign_in.id).get_json()["code"] == CODE


@pytest.mark.parametrize("sign_in_id", ["", "not-hex", "0" * 32])
def test_an_unknown_sign_in_is_said_to_have_expired(phone, sign_in_id):
    assert "expired" in phone.get(PAGE_URL, query_string={"id": sign_in_id}).get_data(as_text=True)
    page = phone.get(CALLBACK_URL, query_string={"state": sign_in_id, "code": CODE})
    assert "expired" in page.get_data(as_text=True)


def test_a_lapsed_sign_in_is_gone(app, client, phone):
    _registered(app, client)
    sign_in = _start(client)
    with app.app_context():
        db.session.get_one(StoredSignIn, sign_in.id).expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.session.commit()

    assert phone.get(PAGE_URL, query_string={"id": sign_in.id}).status_code == 200
    assert "expired" in phone.get(CALLBACK_URL, query_string={"state": sign_in.id, "code": CODE}).get_data(as_text=True)
    assert _status(client, sign_in.id).status_code == 404


def test_only_the_touchscreen_that_started_it_is_answered(app, client, phone):
    _registered(app, client)
    sign_in = _start(client)
    phone.get(CALLBACK_URL, query_string={"state": sign_in.id, "code": CODE})
    other = "another-touchscreens-secret-0001"
    assert _register(client, {**TOUCHSCREEN["registration"], "secret": other}).status_code == 201

    assert _status(client, sign_in.id, other).status_code == 404
    assert _status(client, sign_in.id).get_json()["code"] == CODE
