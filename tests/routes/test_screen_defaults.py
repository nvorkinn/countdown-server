"""Test the settings saved for new screens: saving them from a screen's page, and a first pairing applying them.

A user keeps one set for each type of device (services/screen_defaults.py). A screen of that type takes them the first
time the user pairs with it, however they pair, and never what the screen said of itself at registration.
"""

import hashlib

import pytest

from broker.auth import hash_secret
from broker.db import db
from broker.models import Device, DeviceConfig, ScreenDefaults
from broker.services import pairing, screen_defaults
from tests.conftest import USER

OTHER_SECRET = "another-very-long-device-secret-value"
TOUCHSCREEN_SECRET_HASH = hash_secret("touchscreen-secret-0123456789abcdef")
PLACE = {
    "weather_location": "Bergen",
    "weather_place_id": 3161732,
    "weather_place_name": "Bergen",
    "weather_place_label": "Bergen, Vestland, Norway",
    "weather_country_code": "NO",
    "weather_lat": 60.39,
    "weather_lon": 5.32,
}
# An e-paper screen's settings, each unlike its default.
EPAPER = {
    "clock_format": "12h",
    "language": "nb",
    "style": "card",
    "orientation": "horizontal",
    "tfl_stop_ids": ["490008660N", "NSR:StopPlace:58366"],
    "arrivals_group_by_route": True,
    "spotify_enabled": True,
    "parcels_enabled": True,
    "parcels_kept_hours": 0,
    **PLACE,
}
# A touchscreen's.
TOUCHSCREEN = {
    "theme": "night",
    "screen_sleep_minutes": 15,
    "screen_brightness": 40,
    "quiet_hours_on": True,
    "quiet_hours_start": 21 * 60,
    "lyrics_size": "large",
    "tfl_stop_ids": ["490008660N"],
}


def _token(secret):
    return hashlib.sha256(f"pairing:{hashlib.sha256(secret.encode()).hexdigest()}".encode()).hexdigest()


def _set(app, device_id, settings):
    with app.app_context():
        config = db.session.get_one(DeviceConfig, device_id)
        for field, value in settings.items():
            setattr(config, field, value)
        db.session.commit()


def _settings(app, device_id, fields):
    with app.app_context():
        config = db.session.get_one(DeviceConfig, device_id)
        return {field: getattr(config, field) for field in fields}


def _saved(app, device_type="epaper", user=USER):
    with app.app_context():
        saved = db.session.get(ScreenDefaults, (user, device_type))
        return None if saved is None else dict(saved.settings)


def _keep(app, device_type, settings, user=USER):
    """Save `settings` for the user's new screens of the type, as if from a screen called Kitchen."""
    with app.app_context():
        db.session.add(
            ScreenDefaults(
                user_id=user, type=device_type, settings=settings, saved_at=db.func.now(), source_name="Kitchen"
            )
        )
        db.session.commit()


def _register_touchscreen(client):
    """Register a touchscreen and make the client's user its owner in one request (/register); return its id."""
    url = f"/register?secret_hash={TOUCHSCREEN_SECRET_HASH}&width=1280&height=800&brightness_control=true"
    assert client.get(url, headers={"Sec-Fetch-Site": "none"}).status_code == 200
    with client.application.app_context():
        return db.session.query(Device).filter_by(display_secret_hash=TOUCHSCREEN_SECRET_HASH).one().device_id


@pytest.fixture
def saved_epaper(app, paired_client):
    """Set up the user's first e-paper screen (EPAPER) and save its settings for new ones; return its id."""
    client, device_id, _ = paired_client
    _set(app, device_id, EPAPER)
    assert client.post(f"/device/{device_id}/defaults").status_code == 302
    return device_id


# --- saving them from a screen's page -------------------------------------------------------------


def test_saving_keeps_the_screens_settings_for_its_type(app, saved_epaper):
    saved = _saved(app)

    assert saved.keys() == screen_defaults.FIELDS["epaper"]
    assert {field: saved[field] for field in EPAPER} == EPAPER
    assert _saved(app, "touchscreen") is None


def test_what_the_screen_said_of_itself_is_never_saved(app, saved_epaper):
    assert not {"device_id", "width", "height", "format", "brightness_control"} & _saved(app).keys()
    for fields in screen_defaults.FIELDS.values():
        assert not {"device_id", "width", "height", "format", "brightness_control"} & fields


def test_every_saved_setting_is_one_a_screen_has(app):
    for fields in screen_defaults.FIELDS.values():
        assert fields <= set(DeviceConfig.__table__.columns.keys())


def test_a_touchscreen_saves_its_own_settings_and_none_of_an_epaper_screens(app):
    assert {"theme", "quiet_hours_on", "lyrics_size", "tfl_stop_ids"} <= screen_defaults.FIELDS["touchscreen"]
    assert not {"style", "orientation", "layout", "spotify_enabled"} & screen_defaults.FIELDS["touchscreen"]


def test_the_page_says_which_settings_new_screens_start_with(app, paired_client):
    client, device_id, _ = paired_client
    page = client.get(f"/device/{device_id}").get_data(as_text=True)
    assert "Your new e-paper screens start with the standard settings." in page
    assert "Stop using them" not in page

    client.post(f"/device/{device_id}/defaults")

    page = client.get(f"/device/{device_id}").get_data(as_text=True)
    assert "Your new e-paper screens start with the settings saved from" in page
    assert "Stop using them" in page


def test_saving_again_replaces_them(app, client, saved_epaper):
    _set(app, saved_epaper, {"style": "hero"})

    client.post(f"/device/{saved_epaper}/defaults")

    assert _saved(app)["style"] == "hero"


def test_clearing_forgets_them(app, client, saved_epaper):
    assert client.post(f"/device/{saved_epaper}/defaults/clear").status_code == 302

    assert _saved(app) is None
    assert client.post(f"/device/{saved_epaper}/defaults/clear").status_code == 302  # nothing to forget is fine


def test_someone_elses_screen_is_not_found(app, saved_epaper):
    stranger = app.test_client()
    stranger.environ_base["HTTP_REMOTE_SUB"] = "someone-else"

    assert stranger.post(f"/device/{saved_epaper}/defaults").status_code == 404
    assert stranger.post(f"/device/{saved_epaper}/defaults/clear").status_code == 404
    assert _saved(app, user="someone-else") is None
    assert _saved(app) is not None


# --- a first pairing applies them -----------------------------------------------------------------


def test_a_new_screen_paired_by_token_starts_with_them(app, client, register_device, saved_epaper):
    device_id, _ = register_device(OTHER_SECRET)

    assert client.post("/pair/token", json={"token": _token(OTHER_SECRET)}).status_code == 200

    assert _settings(app, device_id, EPAPER) == EPAPER
    # Its own, as it registered.
    assert _settings(app, device_id, ["width", "height", "format"]) == {
        "width": 480,
        "height": 800,
        "format": "mono-1bpp-packed",
    }


def test_a_new_screen_paired_by_code_starts_with_them(app, client, register_device, saved_epaper, config_of):
    device_id, _ = register_device(OTHER_SECRET)
    with app.app_context():
        code = pairing.issue_code(device_id)

    assert client.post("/pair/add", data={"code": code}).status_code == 302

    assert _settings(app, device_id, EPAPER) == EPAPER
    # And the config the render pool reads has them.
    assert config_of(device_id)["style"]["drawing"] == "card"


def test_a_screen_reserved_at_sign_up_starts_with_them_once_claimed(app, register_device, saved_epaper):
    device_id, _ = register_device(OTHER_SECRET)
    with app.app_context():
        assert pairing.reserve_for_signup_by_token(_token(OTHER_SECRET), USER) == device_id
        assert pairing.claim_pending(USER) == device_id

    assert _settings(app, device_id, EPAPER) == EPAPER


def test_a_touchscreen_registered_through_the_login_starts_with_its_types(app, client):
    _keep(app, "touchscreen", TOUCHSCREEN)

    device_id = _register_touchscreen(client)

    assert _settings(app, device_id, TOUCHSCREEN) == TOUCHSCREEN
    assert _settings(app, device_id, ["width", "height", "brightness_control"]) == {
        "width": 1280,
        "height": 800,
        "brightness_control": True,
    }


def test_the_settings_of_one_type_are_not_the_others(app, client, saved_epaper):
    device_id = _register_touchscreen(client)

    assert _settings(app, device_id, ["clock_format", "language", "tfl_stop_ids"]) == {
        "clock_format": "24h",
        "language": "en",
        "tfl_stop_ids": [],
    }


def test_pairing_again_leaves_the_screens_settings_alone(app, client, register_device, saved_epaper):
    device_id, _ = register_device(OTHER_SECRET)
    client.post("/pair/token", json={"token": _token(OTHER_SECRET)})
    _set(app, device_id, {"style": "hero"})

    assert client.post("/pair/token", json={"token": _token(OTHER_SECRET)}).status_code == 200

    assert _settings(app, device_id, ["style"]) == {"style": "hero"}


def test_someone_with_none_saved_gets_the_standard_settings(app, register_device, saved_epaper):
    device_id, _ = register_device(OTHER_SECRET)
    stranger = app.test_client()
    stranger.environ_base["HTTP_REMOTE_SUB"] = "someone-else"

    assert stranger.post("/pair/token", json={"token": _token(OTHER_SECRET)}).status_code == 200

    assert _settings(app, device_id, ["style", "orientation", "tfl_stop_ids"]) == {
        "style": "airy",
        "orientation": "vertical",
        "tfl_stop_ids": [],
    }


def test_after_clearing_a_new_screen_gets_the_standard_settings(app, client, register_device, saved_epaper):
    client.post(f"/device/{saved_epaper}/defaults/clear")
    device_id, _ = register_device(OTHER_SECRET)

    client.post("/pair/token", json={"token": _token(OTHER_SECRET)})

    assert _settings(app, device_id, ["style"]) == {"style": "airy"}


def test_a_setting_that_no_longer_exists_is_skipped(app, client, register_device):
    _keep(app, "epaper", {"style": "card", "width": 1, "retired_setting": "x"})
    device_id, _ = register_device(OTHER_SECRET)

    client.post("/pair/token", json={"token": _token(OTHER_SECRET)})

    assert _settings(app, device_id, ["style", "width"]) == {"style": "card", "width": 480}


def test_settings_the_contract_refuses_leave_the_screen_as_it_registered(app, client, register_device, caplog):
    _keep(app, "epaper", {"style": "card", "clock_format": "13h"})
    device_id, _ = register_device(OTHER_SECRET)

    assert client.post("/pair/token", json={"token": _token(OTHER_SECRET)}).status_code == 200

    assert _settings(app, device_id, ["style", "clock_format"]) == {"style": "airy", "clock_format": "24h"}
    assert "aren't allowed" in caplog.text
    assert "13h" not in caplog.text
