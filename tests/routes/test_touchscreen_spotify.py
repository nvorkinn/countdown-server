"""Test a touchscreen's Spotify: the access token it plays its owner's Spotify with (GET /api/devices/spotify/token).

The link is the owner's account's, and its refresh token never leaves the broker: the touchscreen only ever gets a
short-lived access token, renewed here when it has little left. A link made before touchscreens could use it lacks
their scopes, so it's linked again, which the Account page asks for once there's a touchscreen.
"""

import time
from unittest.mock import Mock, patch

import requests
from countdown_api import fixtures
from countdown_api.models import SpotifyAccessToken

from broker.clients import spotify
from broker.db import db
from broker.models import SpotifyToken
from tests.conftest import account_of
from tests.routes.test_touchscreens import SECRET, TOUCHSCREEN, _auth, _paired, _register, _registered

TOKEN_URL = "/api/devices/spotify/token"
GRANTED = TOUCHSCREEN["spotify_token"]["scope"]


def _link(app, device_id, scope=GRANTED, expires_in=3600):
    """Link Spotify to the device's owner's account, with tokens that have `expires_in` s left."""
    with app.app_context():
        db.session.add(
            SpotifyToken(
                user_id=account_of(device_id),
                refresh_token="refresh-secret",
                access_token="access",
                expires_at=time.time() + expires_in,
                scope=scope,
            )
        )
        db.session.commit()


def _renewed(scope=GRANTED):
    response = Mock()
    response.json.return_value = {"access_token": "renewed", "token_type": "Bearer", "expires_in": 3600, "scope": scope}
    response.raise_for_status = Mock()
    return response


def test_the_contracts_token_is_what_the_route_answers():
    assert SpotifyAccessToken.model_validate(TOUCHSCREEN["spotify_token"]).token_type == "Bearer"
    assert set(GRANTED.split()) >= spotify.TOUCHSCREEN_SCOPES
    assert set(spotify.SCOPE.split()) >= spotify.TOUCHSCREEN_SCOPES | {"user-top-read"}
    assert fixtures.path("touchscreen.json").is_file()


def test_a_paired_touchscreen_gets_an_access_token_and_never_the_refresh_token(app, client):
    device_id = _paired(app, client)
    _link(app, device_id)

    response = client.get(TOKEN_URL, headers=_auth())

    assert response.status_code == 200
    body = response.get_json()
    assert SpotifyAccessToken.model_validate(body).access_token == "access"
    assert body["scope"] == GRANTED
    assert 3500 < body["expires_in"] <= 3600
    assert "refresh-secret" not in response.get_data(as_text=True)


def test_a_token_with_little_left_is_renewed_first(app, client):
    device_id = _paired(app, client)
    _link(app, device_id, expires_in=120)

    with patch("broker.clients.spotify.requests.post", return_value=_renewed()) as renew:
        body = client.get(TOKEN_URL, headers=_auth()).get_json()

    assert renew.call_args.kwargs["data"] == {"grant_type": "refresh_token", "refresh_token": "refresh-secret"}
    assert body["access_token"] == "renewed"
    assert body["expires_in"] > 3500


def test_it_needs_the_devices_secret(app, client):
    device_id = _paired(app, client)
    _link(app, device_id)

    assert client.get(TOKEN_URL).status_code == 401
    assert client.get(TOKEN_URL, headers=_auth("not-the-secret-at-all")).status_code == 401


def test_an_owner_without_spotify_gets_none(app, client):
    _paired(app, client)

    assert client.get(TOKEN_URL, headers=_auth()).status_code == 404


def test_a_link_without_the_touchscreens_scopes_is_linked_again(app, client):
    device_id = _paired(app, client)
    _link(app, device_id, scope=None)

    response = client.get(TOKEN_URL, headers=_auth())

    assert response.status_code == 403
    assert "Account page" in response.get_json()["error"]


def test_an_unpaired_touchscreen_or_an_epaper_screen_gets_none(app, client):
    _registered(app, client)
    assert client.get(TOKEN_URL, headers=_auth()).status_code == 409

    secret = TOUCHSCREEN["epaper_registration"]["secret"]
    assert _register(client, TOUCHSCREEN["epaper_registration"]).status_code == 201
    assert client.get(TOKEN_URL, headers=_auth(secret)).status_code == 409
    assert secret != SECRET


def test_spotify_refusing_to_renew_is_a_502_that_logs_no_token(app, client, caplog):
    device_id = _paired(app, client)
    _link(app, device_id, expires_in=0)
    failed = requests.HTTPError("400 for https://accounts.spotify.com/api/token refresh_token=refresh-secret")

    with patch("broker.clients.spotify.requests.post", side_effect=failed):
        response = client.get(TOKEN_URL, headers=_auth())

    assert response.status_code == 502
    assert "refresh-secret" not in caplog.text


def test_a_renewal_keeps_the_scopes_when_spotify_leaves_them_out(app, client):
    device_id = _paired(app, client)
    _link(app, device_id, expires_in=0)
    renewed = _renewed()
    del renewed.json.return_value["scope"]

    with patch("broker.clients.spotify.requests.post", return_value=renewed):
        assert client.get(TOKEN_URL, headers=_auth()).get_json()["scope"] == GRANTED


# --- the Account page -----------------------------------------------------------------------------


def test_the_account_page_asks_for_spotify_again_for_a_touchscreen(app, client):
    device_id = _paired(app, client)
    _link(app, device_id, scope="user-read-currently-playing user-read-playback-state user-top-read")

    html = client.get("/account").get_data(as_text=True)

    assert "Reconnect Spotify so your touchscreen can play it." in html
    assert ">Reconnect<" in html


def test_a_link_with_the_scopes_needs_nothing(app, client):
    device_id = _paired(app, client)
    _link(app, device_id)

    assert "Reconnect" not in client.get("/account").get_data(as_text=True)


def test_without_a_touchscreen_an_old_link_needs_nothing(app, paired_client):
    client, device_id, _ = paired_client
    with app.app_context():
        db.session.add(SpotifyToken(user_id=account_of(device_id), refresh_token="r", access_token="a", expires_at=0))
        db.session.commit()

    assert "Reconnect" not in client.get("/account").get_data(as_text=True)
