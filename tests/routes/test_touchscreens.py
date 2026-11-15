"""Test touchscreens: registering one, its config (GET /api/config), and its settings on the settings site.

A touchscreen is a device like an e-paper screen, paired and set up the same way, but it draws itself from its config,
so the render pool never draws it, it has no frames, and its page has its own settings instead of the pool's.
"""

import re
from datetime import UTC, datetime

from countdown_api import fixtures
from countdown_api.models import ConfigResponse, DeviceRegistration

from broker.db import db
from broker.models import Device, DeviceConfig
from broker.services import pairing, render_screens
from tests.conftest import name_screen

# The store contract's canonical example of a touchscreen and its routes.
TOUCHSCREEN = fixtures.load("touchscreen.json")
SECRET = TOUCHSCREEN["registration"]["secret"]


def _auth(secret=SECRET):
    return {"Authorization": f"Bearer {secret}"}


def _register(client, body=None):
    return client.post("/api/devices/register", json=body or TOUCHSCREEN["registration"])


def _registered(app, client):
    """Register the contract's touchscreen and name it, as pairing would; return its device id."""
    response = _register(client)
    assert response.status_code == 201
    device_id = response.get_json()["device_id"]
    with app.app_context():
        name_screen(device_id)
    return device_id


def _paired(app, client):
    """Register the contract's touchscreen and pair the client's user with it by a code; return its device id."""
    device_id = _registered(app, client)
    with app.app_context():
        code = pairing.issue_code(device_id)
    assert client.post("/pair/add", data={"code": code}).status_code == 302
    return device_id


# --- registering ----------------------------------------------------------------------------------


def test_a_touchscreen_registers_with_its_size_and_no_format(app, client):
    device_id = _registered(app, client)

    with app.app_context():
        device = db.session.get_one(Device, device_id)
        assert device.type == "touchscreen"
        assert (device.config.width, device.config.height) == (1280, 800)


def test_an_epaper_screen_registers_as_one_without_saying_so(app, client):
    response = _register(client, TOUCHSCREEN["epaper_registration"])

    assert response.status_code == 201
    with app.app_context():
        device = db.session.get_one(Device, response.get_json()["device_id"])
        assert device.type == "epaper"
        assert device.config.format == TOUCHSCREEN["epaper_registration"]["format"]


def test_the_contracts_registrations_are_what_the_route_takes():
    assert DeviceRegistration.model_validate(TOUCHSCREEN["registration"]).type == "touchscreen"
    assert DeviceRegistration.model_validate(TOUCHSCREEN["epaper_registration"]).type is None


def test_a_touchscreen_that_sends_a_format_is_refused(client):
    response = _register(client, {**TOUCHSCREEN["registration"], "format": "png"})

    assert response.status_code == 400
    assert "format" in response.get_json()["error"]


def test_a_touchscreen_without_its_size_is_refused(client):
    body = {key: value for key, value in TOUCHSCREEN["registration"].items() if key != "height"}

    assert _register(client, body).status_code == 400


def test_an_unknown_type_is_refused(client):
    response = _register(client, {**TOUCHSCREEN["registration"], "type": "tablet"})

    assert response.status_code == 400
    assert "type" in response.get_json()["error"]


def test_an_epaper_screen_still_needs_its_format(client):
    body = {key: value for key, value in TOUCHSCREEN["epaper_registration"].items() if key != "format"}

    assert _register(client, body).status_code == 400


# --- its config -----------------------------------------------------------------------------------


def test_the_config_needs_the_devices_secret(app, client):
    _registered(app, client)

    assert client.get("/api/config").status_code == 401
    assert client.get("/api/config", headers=_auth("not-the-secret-of-any-device")).status_code == 401


def test_an_unpaired_touchscreen_gets_the_pairing_value(app, client):
    device_id = _registered(app, client)

    response = client.get("/api/config", headers=_auth())

    assert response.status_code == 200
    config = ConfigResponse.model_validate(response.get_json())
    assert config.device_id == device_id
    assert config.type == "touchscreen"
    with app.app_context():
        assert config.pairing_code == pairing.live_code(device_id)
    assert config.pairing_code is not None
    # Its own settings, at the contract's defaults, as the contract's pairing value has them.
    assert response.get_json()["touchscreen"] == TOUCHSCREEN["pairing"]["touchscreen"]


def test_a_touchscreen_waiting_long_gets_a_new_code(app, client):
    device_id = _registered(app, client)
    with app.app_context():
        for code in db.session.get_one(Device, device_id).pairing_codes:
            code.expires_at = datetime(2000, 1, 1, tzinfo=UTC)
        db.session.commit()

    config = client.get("/api/config", headers=_auth()).get_json()

    assert config["pairing_code"] is not None


def test_a_paired_touchscreen_gets_its_config_with_its_settings(app, client):
    device_id = _paired(app, client)
    with app.app_context():
        config = db.session.get_one(DeviceConfig, device_id)
        config.theme, config.theme_colour, config.screen_sleep_minutes = "auto", "dynamic", 15
        db.session.commit()

    response = client.get("/api/config", headers=_auth())

    assert response.status_code == 200
    body = response.get_json()
    assert body["pairing_code"] is None
    assert body["type"] == "touchscreen"
    assert body["touchscreen"] == TOUCHSCREEN["config"]["touchscreen"]
    ConfigResponse.model_validate(body)


def test_an_epaper_screens_config_has_no_touchscreen_settings(app, client):
    secret = TOUCHSCREEN["epaper_registration"]["secret"]
    assert _register(client, TOUCHSCREEN["epaper_registration"]).status_code == 201

    body = client.get("/api/config", headers=_auth(secret)).get_json()

    assert body["type"] == "epaper"
    assert body["touchscreen"] is None


def test_a_touchscreen_has_no_frames(app, client):
    _registered(app, client)

    assert client.post("/api/frame", headers=_auth()).status_code == 409


def test_the_render_pool_never_draws_a_touchscreen(app, client):
    device_id = _registered(app, client)
    epaper = _register(client, TOUCHSCREEN["epaper_registration"]).get_json()["device_id"]
    with app.app_context():
        name_screen(epaper)

        listed = render_screens.listed(datetime.now(UTC))

    # Both have a live code; only the e-paper screen has a pairing screen for the pool to draw.
    assert epaper in listed
    assert device_id not in listed


# --- its settings page ----------------------------------------------------------------------------


def test_a_touchscreens_page_has_its_settings_and_not_the_pools(app, client):
    _paired(app, client)

    html = client.get("/device").get_data(as_text=True)

    assert '<h2 class="card-title">Touchscreen</h2>' in html
    assert re.findall(r'id="theme_([a-z]+)"[^>]*checked', html) == ["day"]
    assert re.findall(r'id="theme_colour_(\w+)"[^>]*checked', html) == ["forest"]
    assert re.findall(r'id="screen_sleep_minutes_(\w+)"[^>]*checked', html) == ["5"]
    # It draws itself: no picture, style, layout or orientation.
    assert 'id="preview"' not in html
    assert 'name="style"' not in html
    assert 'name="orientation"' not in html


def test_an_epaper_screens_page_has_no_touchscreen_settings(paired_client):
    client, _, _ = paired_client

    html = client.get("/device").get_data(as_text=True)

    assert "Touchscreen" not in html
    assert 'name="theme"' not in html
    assert 'id="preview"' in html


def test_a_touchscreens_settings_are_saved_with_the_rest(app, client):
    device_id = _paired(app, client)

    response = client.post(
        "/device", data={"theme": "night", "theme_colour": "plum", "screen_sleep_minutes": "0", "clock_format": "12h"}
    )

    assert response.status_code == 302
    with app.app_context():
        config = db.session.get_one(DeviceConfig, device_id)
        assert (config.theme, config.theme_colour, config.screen_sleep_minutes) == ("night", "plum", 0)
        assert config.clock_format == "12h"
    body = client.get("/api/config", headers=_auth()).get_json()
    assert body["touchscreen"] == {"theme": "night", "theme_colour": "plum", "screen_sleep_minutes": 0}
    assert body["clock_format"] == "12h"


def test_a_save_without_the_touchscreen_settings_keeps_them(app, client):
    device_id = _paired(app, client)
    with app.app_context():
        db.session.get_one(DeviceConfig, device_id).theme = "night"
        db.session.commit()

    assert client.post("/device", data={"clock_format": "12h"}).status_code == 302

    with app.app_context():
        assert db.session.get_one(DeviceConfig, device_id).theme == "night"


def test_settings_the_contract_doesnt_allow_are_refused(app, client):
    device_id = _paired(app, client)

    for wrong in ({"theme": "dusk"}, {"theme_colour": "mauve"}, {"screen_sleep_minutes": "10"}):
        response = client.post("/device", data=wrong)
        assert response.status_code == 400
        assert b"Nothing was saved." in response.data

    with app.app_context():
        config = db.session.get_one(DeviceConfig, device_id)
        assert (config.theme, config.theme_colour, config.screen_sleep_minutes) == ("day", "forest", 5)
