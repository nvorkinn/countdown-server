"""Test a touchscreen's Spotify Soloist key (GET /api/devices/soloist/key).

The key is the server's own secret (SPOTIFY_SOLOIST_API_KEY, from Vaultwarden), which no release carries: only a
paired touchscreen gets it, with its secret, and it's never logged.
"""

import logging

import pytest
from countdown_api.models import SoloistApiKey

from tests.routes.test_touchscreens import SECRET, TOUCHSCREEN, _auth, _paired, _register, _registered

KEY_URL = "/api/devices/soloist/key"
KEY = "soloist-key-for-tests"


@pytest.fixture(autouse=True)
def _soloist_key(app):
    app.config["SPOTIFY_SOLOIST_API_KEY"] = KEY


def test_a_paired_touchscreen_gets_the_key_and_no_cache_keeps_it(app, client, caplog):
    _paired(app, client)

    with caplog.at_level(logging.DEBUG):
        response = client.get(KEY_URL, headers=_auth())

    assert response.status_code == 200
    assert SoloistApiKey.model_validate(response.get_json()).api_key == KEY
    assert response.headers["Cache-Control"] == "no-store"
    assert KEY not in caplog.text


def test_it_needs_the_devices_secret(app, client):
    _paired(app, client)

    assert client.get(KEY_URL).status_code == 401
    assert client.get(KEY_URL, headers=_auth("not-the-secret-at-all")).status_code == 401


def test_an_unpaired_touchscreen_or_an_epaper_screen_gets_none(app, client):
    _registered(app, client)
    assert client.get(KEY_URL, headers=_auth()).status_code == 409

    secret = TOUCHSCREEN["epaper_registration"]["secret"]
    assert _register(client, TOUCHSCREEN["epaper_registration"]).status_code == 201
    assert client.get(KEY_URL, headers=_auth(secret)).status_code == 409
    assert secret != SECRET


def test_a_server_without_a_key_answers_404(app, client):
    _paired(app, client)
    app.config["SPOTIFY_SOLOIST_API_KEY"] = ""

    assert client.get(KEY_URL, headers=_auth()).status_code == 404
