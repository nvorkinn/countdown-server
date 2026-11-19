"""Test touchscreens: registering one, its config (GET and PUT /api/devices/config), and its settings page.

A touchscreen is a device like an e-paper screen, paired and set up the same way, but it draws itself from its config,
so the render pool never draws it, it has no frames, and its page has its own settings instead of the pool's.
"""

import re
from datetime import UTC, datetime
from html import unescape

from countdown_api import fixtures
from countdown_api.models import ConfigResponse, DeviceRegistration, TouchscreenConfig
from pydantic import TypeAdapter

from broker.db import db
from broker.models import THEMES, Device, DeviceConfig
from broker.routes import pages
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


def test_the_config_needs_the_devices_secret(app, client, anonymous_client):
    _registered(app, client)

    for method in (anonymous_client.get, client.put):
        assert method(CONFIG_URL).status_code == 401
        assert method(CONFIG_URL, headers=_auth("not-the-secret-of-any-device")).status_code == 401
    # Signed in to the settings site, a GET without a secret reads one of the person's devices, named by device_id.
    assert client.get(CONFIG_URL).status_code == 400


def test_a_touchscreen_gets_no_pairing_code(app, client):
    device_id = _registered(app, client)

    response = client.get(CONFIG_URL, headers=_auth())

    assert response.status_code == 200
    config = CONFIGS.validate_python(response.get_json())
    assert isinstance(config, TouchscreenConfig)
    assert config.device_id == device_id
    assert (config.width, config.height) == (1280, 800)
    # Unpaired, it's still not issued one, by registering or by asking for its config, which has no field for one.
    assert "pairing_code" not in response.get_json()
    with app.app_context():
        assert pairing.live_code(device_id) is None
    # Its own settings, at their defaults.
    assert response.get_json()["style"] == {"skin": "day", "colour": "forest", "dynamic": False}
    assert response.get_json()["display"] == {"sleep_minutes": 5}
    assert response.get_json()["lyrics"] == {"size": "medium", "align": "left"}


def test_a_paired_touchscreen_gets_its_config_with_its_settings(app, client):
    device_id = _paired(app, client)
    with app.app_context():
        config = db.session.get_one(DeviceConfig, device_id)
        config.theme, config.theme_colour, config.screen_sleep_minutes = "auto", "violet", 15
        config.theme_dynamic = True
        config.lyrics_size, config.lyrics_align = "large", "center"
        db.session.commit()

    response = client.get(CONFIG_URL, headers=_auth())

    assert response.status_code == 200
    body = response.get_json()
    assert body["type"] == "touchscreen"
    # Only what it shows itself: the contract's touchscreen config, field for field.
    assert set(body) == set(TOUCHSCREEN["config"]) == set(TouchscreenConfig.model_fields)
    assert body["style"] == TOUCHSCREEN["config"]["style"]
    assert body["display"] == TOUCHSCREEN["config"]["display"]
    assert body["lyrics"] == TOUCHSCREEN["config"]["lyrics"]
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
        style=TOUCHSCREEN["save"]["style"],
        display=TOUCHSCREEN["save"]["display"],
        lyrics=TOUCHSCREEN["save"]["lyrics"],
    )

    response = client.put(CONFIG_URL, json=sent, headers=_auth())

    assert response.status_code == 200
    assert response.get_json() == sent
    with app.app_context():
        config = db.session.get_one(DeviceConfig, device_id)
        assert (config.theme, config.theme_colour, config.screen_sleep_minutes) == ("night", "violet", 30)
        assert config.theme_dynamic
        assert config.clock_format == "12h"
        assert (config.lyrics_size, config.lyrics_align) == ("large", "center")
    assert client.get(CONFIG_URL, headers=_auth()).get_json() == sent


def test_a_save_without_lyrics_keeps_how_they_show(app, client):
    device_id = _paired(app, client)
    with app.app_context():
        config = db.session.get_one(DeviceConfig, device_id)
        config.lyrics_size, config.lyrics_align = "small", "right"
        db.session.commit()
    # An older touchscreen's save, from before the config had lyrics.
    sent = {key: value for key, value in _saved_with(client, clock_format="12h").items() if key != "lyrics"}

    response = client.put(CONFIG_URL, json=sent, headers=_auth())

    assert response.status_code == 200
    assert response.get_json()["lyrics"] == {"size": "small", "align": "right"}


def test_a_save_with_the_old_dynamic_colour_is_forest_with_dynamic_on(app, client):
    device_id = _paired(app, client)
    sent = _saved_with(client, style={"skin": "day", "colour": "dynamic", "dynamic": False})

    response = client.put(CONFIG_URL, json=sent, headers=_auth())

    assert response.status_code == 200
    assert response.get_json()["style"] == {"skin": "day", "colour": "forest", "dynamic": True}
    with app.app_context():
        config = db.session.get_one(DeviceConfig, device_id)
        assert (config.theme_colour, config.theme_dynamic) == ("forest", True)


def test_a_touchscreens_save_ignores_an_epaper_screens_fields(app, client):
    device_id = _paired(app, client)
    with app.app_context():
        language = db.session.get_one(DeviceConfig, device_id).language
    # A touchscreen's config has no language or integrations, so a save that sends them (an older client's) drops them.
    sent = _saved_with(client, language="nb" if language == "en" else "en", spotify={"enabled": True})

    response = client.put(CONFIG_URL, json=sent, headers=_auth())

    assert response.status_code == 200
    assert "language" not in response.get_json()
    assert "spotify" not in response.get_json()
    with app.app_context():
        config = db.session.get_one(DeviceConfig, device_id)
        assert (config.language, config.spotify_enabled) == (language, False)


def test_a_save_that_changes_what_only_the_settings_site_does_is_refused(app, client):
    device_id = _paired(app, client)
    current = _saved_with(client)

    for wrong in (
        {**current, "device_id": "ffffffffffff"},
        {**current, "width": 640},
        {**current, "weather": {"location": "Oslo", "key": "oslo-no-3143244"}},
        {**current, "arrivals": {"stops": [{"source": "tfl", "id": "490000077E"}]}},
        {**current, "notice_board": {"postcode": "0150", "country": "NO"}},
        {**current, "style": {**current["style"], "skin": "night"}, "height": 600},
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

    # What the tablet shows (the time format, departures, weather and notice board), then its own style, display and
    # lyrics.
    titles = re.findall(r'<h2 class="card-title">([^<]+)</h2>', html)
    assert titles == ["General", "Departures", "Weather", "Notice board", "Style", "Display", "Synced Lyrics", "Screen"]
    assert re.findall(r'id="lyrics_size_([\w-]+)"[^>]*checked', html) == ["medium"]
    assert re.findall(r'id="lyrics_align_(\w+)"[^>]*checked', html) == ["left"]
    # English-only, and its music is its own: no language and no integrations.
    assert 'name="language"' not in html
    assert '_enabled"' not in html
    # Nor an e-paper screen's pages of stops.
    assert "Two stops to a page" not in html
    assert "data-pages" not in html
    assert re.findall(r'id="theme_([a-z]+)"[^>]*checked', html) == ["day"]
    assert re.findall(r'id="theme_colour_(\w+)"[^>]*checked', html) == ["forest"]
    assert re.findall(r'id="screen_sleep_minutes_(\w+)"[^>]*checked', html) == ["5"]
    # It draws itself: no picture, style, layout or orientation.
    assert 'id="preview"' not in html
    assert 'name="style"' not in html
    assert 'name="orientation"' not in html
    assert 'name="layout"' not in html
    # Nor the tour, whose pictures are an e-paper screen's.
    assert "tourStart" not in html
    assert "config_tour.js" not in html


def test_a_devices_page_shows_what_it_said_at_registration_but_cant_change_it(app, client, paired_client):
    touchscreen = _paired(app, client)
    _, epaper, _ = paired_client

    for device_id, kind, size in (
        (touchscreen, "Touchscreen", "1280 \u00d7 800"),
        (epaper, "E-paper screen", "480 \u00d7 800"),
    ):
        html = client.get(f"/device/{device_id}").get_data(as_text=True)
        facts = html.split('<dl class="device-facts"')[1].split("</dl>")[0]
        assert f"<dd>{kind}</dd>" in facts
        assert f"{size} px" in facts
        assert f"<code>{device_id}</code>" in facts
        # Shown, never a field.
        assert "<input" not in facts


def test_an_epaper_screens_page_has_no_touchscreen_settings(paired_client):
    client, _, _ = paired_client

    html = client.get("/device").get_data(as_text=True)

    assert 'name="theme"' not in html
    assert 'name="theme_colour"' not in html
    assert 'name="screen_sleep_minutes"' not in html
    assert 'name="lyrics_size"' not in html
    assert 'id="preview"' in html


def test_a_touchscreens_settings_are_saved_with_the_rest(app, client):
    device_id = _paired(app, client)

    response = client.post(
        "/device",
        data={
            "theme": "night",
            "theme_colour": "plum",
            "theme_dynamic": "on",
            "screen_sleep_minutes": "0",
            "lyrics_size": "small",
            "lyrics_align": "right",
            "clock_format": "12h",
        },
    )

    assert response.status_code == 302
    with app.app_context():
        config = db.session.get_one(DeviceConfig, device_id)
        assert (config.theme, config.theme_colour, config.screen_sleep_minutes) == ("night", "plum", 0)
        assert config.clock_format == "12h"
    body = client.get(CONFIG_URL, headers=_auth()).get_json()
    assert (body["style"], body["display"]) == (
        {"skin": "night", "colour": "plum", "dynamic": True},
        {"sleep_minutes": 0},
    )
    assert body["clock_format"] == "12h"
    assert body["lyrics"] == {"size": "small", "align": "right"}


def test_a_touchscreens_save_keeps_its_integrations(app, client):
    device_id = _paired(app, client)
    with app.app_context():
        db.session.get_one(DeviceConfig, device_id).spotify_enabled = True
        db.session.commit()

    # Its page has no integrations, so nothing posted turns them off.
    assert client.post("/device", data={"clock_format": "12h"}).status_code == 302

    with app.app_context():
        assert db.session.get_one(DeviceConfig, device_id).spotify_enabled


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

    for wrong in (
        {"theme": "dusk"},
        {"theme_colour": "mauve"},
        {"screen_sleep_minutes": "10"},
        {"lyrics_size": "huge"},
        {"lyrics_align": "justify"},
    ):
        response = client.post("/device", data=wrong)
        assert response.status_code == 400
        assert b"Nothing was saved." in response.data

    with app.app_context():
        config = db.session.get_one(DeviceConfig, device_id)
        assert (config.theme, config.theme_colour, config.screen_sleep_minutes) == ("day", "forest", 5)


def test_a_save_the_owner_doesnt_approve_is_refused(app, client, monkeypatch):
    device_id = _paired(app, client)
    sent = _saved_with(client, clock_format="12h")
    monkeypatch.setattr("broker.services.accounts.approves_device_save", lambda _device: False)

    response = client.put(CONFIG_URL, json=sent, headers=_auth())

    assert response.status_code == 403
    with app.app_context():
        assert db.session.get_one(DeviceConfig, device_id).clock_format != "12h"


def test_a_touchscreens_save_with_an_epaper_screens_settings_is_refused(app, client):
    device_id = _paired(app, client)

    for field, value in (
        ("orientation", "horizontal"),
        ("style", "hero"),
        ("weather_design", "poster"),
        ("layout", ""),
    ):
        response = client.post("/device", data={field: value, "clock_format": "12h"})
        assert response.status_code == 400, field
        assert b"isn&#39;t a setting of a touchscreen." in response.data
        assert b"Nothing was saved." in response.data

    with app.app_context():
        assert db.session.get_one(DeviceConfig, device_id).clock_format == "24h"


def test_an_epaper_screens_save_with_a_touchscreens_settings_is_refused(app, paired_client):
    client, device_id, _ = paired_client

    for field, value in (("theme", "night"), ("theme_colour", "plum"), ("screen_sleep_minutes", "0")):
        response = client.post("/device", data={field: value, "clock_format": "12h", "orientation": "vertical"})
        assert response.status_code == 400, field
        assert b"isn&#39;t a setting of an e-paper screen." in response.data

    with app.app_context():
        config = db.session.get_one(DeviceConfig, device_id)
        assert (config.clock_format, config.theme, config.theme_colour) == ("24h", "day", "forest")


def test_a_save_is_checked_against_the_contracts_model_for_the_type(app, client, monkeypatch):
    """Whatever the page lets through, a save that makes a config the contract refuses is refused, and saves nothing."""
    device_id = _paired(app, client)
    # The page taking a skin the contract doesn't have, as if their lists had drifted apart.
    monkeypatch.setitem(pages.CHOICES, "theme", ({**THEMES, "dusk": "Dusk"}, "The theme"))

    response = client.post("/device", data={"theme": "dusk", "clock_format": "12h"})

    assert response.status_code == 400
    assert b"These settings aren&#39;t allowed for a touchscreen" in response.data
    with app.app_context():
        config = db.session.get_one(DeviceConfig, device_id)
        assert (config.theme, config.clock_format) == ("day", "24h")


def test_each_types_page_posts_only_its_own_types_fields(app, client, paired_client):
    touchscreen = _paired(app, client)
    _, epaper, _ = paired_client

    for device_id, device_type in ((touchscreen, "touchscreen"), (epaper, "epaper")):
        html = client.get(f"/device/{device_id}").get_data(as_text=True)
        posted = set(re.findall(r'<(?:input|select|textarea)[^>]*\sname="([^"]+)"', html))
        others = set().union(*(fields for kind, fields in pages.TYPE_FIELDS.items() if kind != device_type))
        assert posted & pages.TYPE_FIELDS[device_type]
        assert not posted & others, device_type


# --- saving a section at a time -------------------------------------------------------------------


def test_a_touchscreens_page_saves_a_section_at_a_time_from_the_version_it_shows(app, client):
    device_id = _paired(app, client)

    html = client.get("/device").get_data(as_text=True)
    etag = client.get(CONFIG_URL, query_string={"device_id": device_id}).headers["ETag"]

    # No Save bar or whole-form save: config_sections.js saves each section as it changes, from what the page has.
    assert 'id="saveBar"' not in html
    assert "static/settings_form.js" not in html
    assert "static/config_sections.js" in html
    version = re.search(r'data-version="([^"]+)"', html)
    assert version is not None
    assert unescape(version.group(1)) == etag
    assert f'data-device-id="{device_id}"' in html
    shown = re.search(r'<script type="application/json" id="deviceConfig">(.*?)</script>', html, re.DOTALL)
    assert shown is not None
    assert CONFIGS.validate_json(shown.group(1)) == CONFIGS.validate_python(
        client.get(CONFIG_URL, query_string={"device_id": device_id}).get_json()
    )


def test_an_epaper_screens_page_still_saves_the_whole_form(paired_client):
    client, _, _ = paired_client

    html = client.get("/device").get_data(as_text=True)

    assert 'id="saveBar"' in html
    assert "static/settings_form.js" in html
    assert "static/config_sections.js" not in html
    assert 'id="deviceConfig"' not in html


def test_a_screens_name_is_saved_on_its_own(app, client):
    device_id = _paired(app, client)

    response = client.post(f"/device/{device_id}/name", data={"screen_name": "  Kitchen  "})

    assert response.status_code == 200
    assert response.get_json() == {"name": "Kitchen", "label": "Kitchen"}
    with app.app_context():
        assert db.session.get_one(Device, device_id).device_name == "Kitchen"


def test_a_name_too_long_or_missing_isnt_saved(app, client):
    device_id = _paired(app, client)

    too_long = client.post(f"/device/{device_id}/name", data={"screen_name": "x" * (pages.assignments.NAME_MAX + 1)})
    missing = client.post(f"/device/{device_id}/name", data={})

    assert too_long.status_code == missing.status_code == 400
    assert "at most" in too_long.get_json()["error"]
    with app.app_context():
        assert db.session.get_one(Device, device_id).device_name != "x" * (pages.assignments.NAME_MAX + 1)
