"""Test touchscreens: registering one, its config (GET and PUT /api/devices/config), and its settings page.

A touchscreen is a device like an e-paper screen, paired and set up the same way, but it draws itself from its config,
so the render pool never draws it, it has no frames, and its page has its own settings instead of the pool's.
"""

import re
from datetime import UTC, datetime

from countdown_api import fixtures
from countdown_api.models import ConfigResponse, DeviceRegistration, TouchscreenConfig
from pydantic import TypeAdapter

from broker.db import db
from broker.models import Device, DeviceConfig
from broker.services import pairing, render_screens
from tests.conftest import name_screen

# The store contract's canonical example of a touchscreen and its routes.
TOUCHSCREEN = fixtures.load("touchscreen.json")
SECRET = TOUCHSCREEN["registration"]["secret"]
CONFIGS = TypeAdapter(ConfigResponse)
CONFIG_URL = "/api/devices/config"


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
        assert db.session.get_one(Device, device_id).type == "touchscreen"
        config = db.session.get_one(DeviceConfig, device_id)
        assert (config.width, config.height) == (1280, 800)


def test_an_epaper_screen_registers_as_one_without_saying_so(app, client):
    response = _register(client, TOUCHSCREEN["epaper_registration"])

    assert response.status_code == 201
    with app.app_context():
        device_id = response.get_json()["device_id"]
        assert db.session.get_one(Device, device_id).type == "epaper"
        assert db.session.get_one(DeviceConfig, device_id).format == TOUCHSCREEN["epaper_registration"]["format"]


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

    for method in (client.get, client.put):
        assert method(CONFIG_URL).status_code == 401
        assert method(CONFIG_URL, headers=_auth("not-the-secret-of-any-device")).status_code == 401


def test_a_touchscreen_gets_no_pairing_code(app, client):
    device_id = _registered(app, client)

    response = client.get(CONFIG_URL, headers=_auth())

    assert response.status_code == 200
    config = CONFIGS.validate_python(response.get_json())
    assert isinstance(config, TouchscreenConfig)
    assert config.device_id == device_id
    assert (config.width, config.height) == (1280, 800)
    # Unpaired, it's still not issued one, by registering or by asking for its config.
    assert config.pairing_code is None
    with app.app_context():
        assert pairing.live_code(device_id) is None
    # Its own settings, at the contract's defaults.
    assert response.get_json()["style"] == TOUCHSCREEN["pairing"]["style"]
    assert response.get_json()["display"] == TOUCHSCREEN["pairing"]["display"]


def test_a_paired_touchscreen_gets_its_config_with_its_settings(app, client):
    device_id = _paired(app, client)
    with app.app_context():
        config = db.session.get_one(DeviceConfig, device_id)
        config.theme, config.theme_colour, config.screen_sleep_minutes = "auto", "dynamic", 15
        db.session.commit()

    response = client.get(CONFIG_URL, headers=_auth())

    assert response.status_code == 200
    body = response.get_json()
    assert body["pairing_code"] is None
    assert body["type"] == "touchscreen"
    assert body["style"] == TOUCHSCREEN["config"]["style"]
    assert body["display"] == TOUCHSCREEN["config"]["display"]
    CONFIGS.validate_python(body)


def test_an_epaper_screens_config_has_no_touchscreen_settings(app, client):
    secret = TOUCHSCREEN["epaper_registration"]["secret"]
    assert _register(client, TOUCHSCREEN["epaper_registration"]).status_code == 201

    body = client.get(CONFIG_URL, headers=_auth(secret)).get_json()

    assert body["type"] == "epaper"
    assert "display" not in body
    assert set(body["style"]) == {"orientation", "drawing", "weather", "layout"}


def _saved_with(client, **changes):
    """Return the touchscreen's config as GET answers it, with `changes` made to its JSON."""
    return {**client.get(CONFIG_URL, headers=_auth()).get_json(), **changes}


def test_a_paired_touchscreen_saves_its_own_settings(app, client):
    device_id = _paired(app, client)
    sent = _saved_with(
        client,
        clock_format="12h",
        language="nb",
        style=TOUCHSCREEN["save"]["style"],
        display=TOUCHSCREEN["save"]["display"],
    )

    response = client.put(CONFIG_URL, json=sent, headers=_auth())

    assert response.status_code == 200
    assert response.get_json() == sent
    with app.app_context():
        config = db.session.get_one(DeviceConfig, device_id)
        assert (config.theme, config.theme_colour, config.screen_sleep_minutes) == ("night", "dynamic", 30)
        assert (config.clock_format, config.language) == ("12h", "nb")
    assert client.get(CONFIG_URL, headers=_auth()).get_json() == sent


def test_a_save_that_changes_what_only_the_settings_site_does_is_refused(app, client):
    device_id = _paired(app, client)
    current = _saved_with(client)

    for wrong in (
        {**current, "device_id": "ffffffffffff"},
        {**current, "width": 640},
        {**current, "weather": {"location": "Oslo", "key": "oslo-no-3143244"}},
        {**current, "arrivals": {"stops": [{"source": "tfl", "id": "490000077E"}]}},
        {**current, "spotify": {"enabled": not current["spotify"]["enabled"]}},
        {**current, "style": {**current["style"], "skin": "night"}, "interval": 5},
    ):
        response = client.put(CONFIG_URL, json=wrong, headers=_auth())
        assert response.status_code == 400
        assert "only clock_format" in response.get_json()["error"]

    with app.app_context():
        assert db.session.get_one(DeviceConfig, device_id).theme == "day"


def test_a_save_the_contract_doesnt_allow_is_refused(app, client):
    _paired(app, client)
    current = _saved_with(client)

    for wrong in (
        {**current, "style": {**current["style"], "skin": "dusk"}},
        {**current, "display": {"sleep_minutes": 10}},
        {key: value for key, value in current.items() if key != "display"},
        b"not json",
    ):
        kwargs = {"data": wrong} if isinstance(wrong, bytes) else {"json": wrong}
        assert client.put(CONFIG_URL, headers=_auth(), **kwargs).status_code == 400


def test_an_unpaired_touchscreen_or_an_epaper_screen_cant_save(app, client):
    _registered(app, client)
    assert client.put(CONFIG_URL, json=_saved_with(client), headers=_auth()).status_code == 409

    secret = TOUCHSCREEN["epaper_registration"]["secret"]
    assert _register(client, TOUCHSCREEN["epaper_registration"]).status_code == 201
    body = client.get(CONFIG_URL, headers=_auth(secret)).get_json()
    assert client.put(CONFIG_URL, json=body, headers=_auth(secret)).status_code == 409


def test_the_old_config_route_is_gone(app, client):
    _registered(app, client)

    assert client.get("/api/config", headers=_auth()).status_code == 404


def test_a_touchscreen_has_no_frames(app, client):
    _registered(app, client)

    assert client.post("/api/frame", headers=_auth()).status_code == 409


def test_the_render_pool_never_draws_a_touchscreen(app, client):
    device_id = _registered(app, client)
    epaper = _register(client, TOUCHSCREEN["epaper_registration"]).get_json()["device_id"]
    with app.app_context():
        name_screen(epaper)

        listed = render_screens.listed(datetime.now(UTC))

    # The e-paper screen has a code, so a pairing screen for the pool to draw; the touchscreen draws itself.
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
    body = client.get(CONFIG_URL, headers=_auth()).get_json()
    assert (body["style"], body["display"]) == ({"skin": "night", "colour": "plum"}, {"sleep_minutes": 0})
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
