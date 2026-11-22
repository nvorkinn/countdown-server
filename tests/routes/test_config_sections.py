"""Test a device's config a section at a time: its version (ETag), PUT /api/devices/config/<section>, and If-Match.

A touchscreen saves its own sections with its secret; the settings site saves any of its owner's devices' with the
login and ?device_id=. Every save is one section whole, checked against the device type's model, and needs the
version the change was made to: a stale one is a 409, and nothing is saved.
"""

from unittest.mock import patch

import pytest
from countdown_api import fixtures
from countdown_api.models import ConfigVersion, PlaceSearchResult, StopSearchResult
from pydantic import TypeAdapter

from broker.clients import weather
from broker.db import db
from broker.models import DeviceConfig
from broker.services import config_sections, pairing
from tests.conftest import name_screen

TOUCHSCREEN = fixtures.load("touchscreen.json")
SECRET = TOUCHSCREEN["registration"]["secret"]
CONFIG_URL = "/api/devices/config"
OSLO = {
    "id": 3143244,
    "name": "Oslo",
    "admin1": "Oslo",
    "country": "Norway",
    "country_code": "NO",
    "latitude": 59.91273,
    "longitude": 10.74609,
    "label": "Oslo, Norway",
}


def _auth(secret=SECRET):
    return {"Authorization": f"Bearer {secret}"}


@pytest.fixture
def touchscreen(app, client):
    """Register the contract's touchscreen and pair the client's user with it; return its device id."""
    device_id = client.post("/api/devices/register", json=TOUCHSCREEN["registration"]).get_json()["device_id"]
    with app.app_context():
        name_screen(device_id)
        code = pairing.issue_code(device_id)
    assert client.post("/pair/add", data={"code": code}).status_code == 302
    return device_id


def _version(client, **kwargs):
    response = client.get(CONFIG_URL, **kwargs)
    assert response.status_code == 200
    return response.headers["ETag"]


def _save(client, section, body, etag, **kwargs):
    headers = {**kwargs.pop("headers", {}), "If-Match": etag}
    return client.put(f"{CONFIG_URL}/{section}", json=body, headers=headers, **kwargs)


# --- the version -------------------------------------------------------------------------------------


def test_the_config_comes_with_its_version(app, client, touchscreen):
    response = client.get(CONFIG_URL, headers=_auth())

    with app.app_context():
        expected = config_sections.version(db.session.get_one(DeviceConfig, touchscreen))
    assert response.headers["ETag"] == expected
    TypeAdapter(ConfigVersion).validate_python(expected)


def test_an_unchanged_config_is_a_304(client, touchscreen):
    etag = _version(client, headers=_auth())

    response = client.get(CONFIG_URL, headers={**_auth(), "If-None-Match": etag})

    assert (response.status_code, response.headers["ETag"], response.data) == (304, etag, b"")
    assert client.get(CONFIG_URL, headers={**_auth(), "If-None-Match": '"stale"'}).status_code == 200


def test_the_version_changes_with_any_save(client, touchscreen):
    before = _version(client, headers=_auth())

    client.post(f"/device/{touchscreen}", data={"theme": "night", "postcode": "", "weather_location": ""})

    assert _version(client, headers=_auth()) != before


def test_the_settings_site_reads_its_owners_device_by_id(client, anonymous_client, touchscreen, register_device):
    assert client.get(CONFIG_URL, query_string={"device_id": touchscreen}).get_json()["device_id"] == touchscreen
    # Not theirs (registered, but nobody's), no id, and no login.
    other, _ = register_device("another-very-long-device-secret")
    assert client.get(CONFIG_URL, query_string={"device_id": other}).status_code == 404
    assert client.get(CONFIG_URL).status_code == 400
    assert anonymous_client.get(CONFIG_URL, query_string={"device_id": touchscreen}).status_code == 401


# --- a section's save --------------------------------------------------------------------------------


def test_a_touchscreen_saves_one_section_whole(app, client, touchscreen):
    etag = _version(client, headers=_auth())
    style = {"skin": "night", "colour": "plum", "dynamic": True}

    response = _save(client, "style", style, etag, headers=_auth())

    assert response.status_code == 200
    assert response.get_json()["style"] == style
    assert response.headers["ETag"] not in {etag, None}
    assert _version(client, headers=_auth()) == response.headers["ETag"]
    with app.app_context():
        config = db.session.get_one(DeviceConfig, touchscreen)
        assert (config.theme, config.theme_colour, config.theme_dynamic) == ("night", "plum", True)


def test_a_touchscreen_saves_how_it_shows_lyrics(app, client, touchscreen):
    assert client.get(CONFIG_URL, headers=_auth()).get_json()["lyrics"] == {"size": "medium", "align": "left"}
    etag = _version(client, headers=_auth())

    response = _save(client, "lyrics", {"size": "x-large", "align": "center"}, etag, headers=_auth())

    assert (response.status_code, response.get_json()["lyrics"]) == (200, {"size": "x-large", "align": "center"})
    with app.app_context():
        config = db.session.get_one(DeviceConfig, touchscreen)
        assert (config.lyrics_size, config.lyrics_align) == ("x-large", "center")
    refused = _save(client, "lyrics", {"size": "huge", "align": "left"}, response.headers["ETag"], headers=_auth())
    assert refused.status_code == 400


def test_a_touchscreen_groups_its_arrivals_by_route_and_a_save_without_the_switch_keeps_it(app, client, touchscreen):
    arrivals = client.get(CONFIG_URL, headers=_auth()).get_json()["arrivals"]
    assert arrivals["group_by_route"] is False
    etag = _version(client, headers=_auth())

    response = _save(client, "arrivals", {**arrivals, "group_by_route": True}, etag, headers=_auth())

    assert (response.status_code, response.get_json()["arrivals"]["group_by_route"]) == (200, True)
    with app.app_context():
        assert db.session.get_one(DeviceConfig, touchscreen).arrivals_group_by_route
    # An older kiosk saves its stops without the switch.
    response = _save(client, "arrivals", {"stops": arrivals["stops"]}, response.headers["ETag"], headers=_auth())
    assert response.get_json()["arrivals"]["group_by_route"] is True


def test_a_touchscreen_saves_which_parcels_it_shows_and_a_switch_alone_keeps_them(app, client, touchscreen):
    etag = _version(client, headers=_auth())

    window = {"enabled": False, "days_ahead": 2, "kept_hours": 24}
    response = _save(client, "parcels", window, etag, headers=_auth())

    assert (response.status_code, response.get_json()["parcels"]) == (200, window)
    with app.app_context():
        config = db.session.get_one(DeviceConfig, touchscreen)
        assert (config.parcels_days_ahead, config.parcels_kept_hours) == (2, 24)
    # An older kiosk sends only the switch; null is any day.
    response = _save(client, "parcels", {"enabled": False}, response.headers["ETag"], headers=_auth())
    assert response.get_json()["parcels"] == window
    response = _save(client, "parcels", {**window, "days_ahead": None}, response.headers["ETag"], headers=_auth())
    assert response.get_json()["parcels"]["days_ahead"] is None
    refused = _save(client, "parcels", {**window, "kept_hours": 3}, response.headers["ETag"], headers=_auth())
    assert refused.status_code == 400


def test_a_touchscreen_saves_its_brightness_and_quiet_hours(app, client, touchscreen):
    etag = _version(client, headers=_auth())
    # Quiet from 23:30 to 06:15: an end before the start runs past midnight.
    display = {"sleep_minutes": 15, "brightness": 30, "quiet_hours": {"on": True, "start": 1410, "end": 375}}

    response = _save(client, "display", display, etag, headers=_auth())

    assert (response.status_code, response.get_json()["display"]) == (200, display)
    with app.app_context():
        config = db.session.get_one(DeviceConfig, touchscreen)
        assert (config.screen_sleep_minutes, config.screen_brightness) == (15, 30)
        assert (config.quiet_hours_on, config.quiet_hours_start, config.quiet_hours_end) == (True, 1410, 375)
    # An older kiosk sends only the screen sleep: the rest stays as saved.
    response = _save(client, "display", {"sleep_minutes": 2}, response.headers["ETag"], headers=_auth())
    assert response.get_json()["display"] == {**display, "sleep_minutes": 2}
    # Off keeps the times.
    off = {**display, "quiet_hours": {**display["quiet_hours"], "on": False}}
    response = _save(client, "display", off, response.headers["ETag"], headers=_auth())
    assert response.get_json()["display"] == off


def test_a_flat_field_is_a_section_of_its_own(client, touchscreen):
    etag = _version(client, headers=_auth())

    response = _save(client, "clock_format", "12h", etag, headers=_auth())

    assert (response.status_code, response.get_json()["clock_format"]) == (200, "12h")


def test_a_save_needs_the_version_it_was_made_to(app, client, touchscreen):
    etag = _version(client, headers=_auth())
    assert _save(client, "display", {"sleep_minutes": 30}, etag, headers=_auth()).status_code == 200

    # Made to the version before that save: someone else's change would be lost, so nothing is saved.
    stale = _save(client, "style", {"skin": "night", "colour": "navy"}, etag, headers=_auth())
    missing = client.put(f"{CONFIG_URL}/style", json={"skin": "night", "colour": "navy"}, headers=_auth())

    assert (stale.status_code, missing.status_code) == (409, 428)
    with app.app_context():
        assert db.session.get_one(DeviceConfig, touchscreen).theme != "night"


@pytest.mark.parametrize(
    ("section", "body"),
    [
        ("style", {"skin": "dusk", "colour": "plum"}),
        ("style", {"orientation": "vertical", "drawing": "airy", "weather": "columns", "layout": None}),
        ("display", {"sleep_minutes": 10}),
        ("display", {"sleep_minutes": 5, "brightness": 9}),
        ("display", {"sleep_minutes": 5, "brightness": 101}),
        ("display", {"sleep_minutes": 5, "brightness": 50.5}),
        ("display", {"sleep_minutes": 5, "quiet_hours": {"on": True, "start": 1320}}),
        ("display", {"sleep_minutes": 5, "quiet_hours": {"on": True, "start": -1, "end": 420}}),
        ("display", {"sleep_minutes": 5, "quiet_hours": {"on": True, "start": 1320, "end": 1440}}),
        ("display", None),
        ("clock_format", "25h"),
        ("arrivals", {"stops": [{"source": "tfl"}]}),
    ],
)
def test_a_section_its_types_model_refuses_is_a_400(app, client, touchscreen, section, body):
    etag = _version(client, headers=_auth())

    assert _save(client, section, body, etag, headers=_auth()).status_code == 400
    assert _version(client, headers=_auth()) == etag


@pytest.mark.parametrize("section", ["interval", "timezone", "device_id", "width", "format", "nothing"])
def test_only_a_types_sections_can_be_saved(client, touchscreen, section):
    etag = _version(client, headers=_auth())

    assert _save(client, section, {}, etag, headers=_auth()).status_code == 404


def test_an_epaper_screen_has_no_display_section(paired_client):
    client, device_id, _ = paired_client
    etag = _version(client, query_string={"device_id": device_id})

    response = _save(client, "display", {"sleep_minutes": 5}, etag, query_string={"device_id": device_id})

    assert response.status_code == 404


def test_a_device_saves_its_own_config_only_as_a_paired_touchscreen(client, paired_client):
    _, _, epaper_secret = paired_client
    etag = _version(client, headers=_auth(epaper_secret))

    response = _save(client, "clock_format", "12h", etag, headers=_auth(epaper_secret))

    assert response.status_code == 403


def test_an_unpaired_touchscreen_cant_save(app, client):
    client.post("/api/devices/register", json=TOUCHSCREEN["registration"])
    etag = _version(client, headers=_auth())

    assert _save(client, "clock_format", "12h", etag, headers=_auth()).status_code == 403


def test_the_settings_site_saves_an_epaper_screens_sections(app, paired_client, fake_redis):
    client, device_id, _ = paired_client
    site = {"query_string": {"device_id": device_id}}
    etag = _version(client, **site)
    style = {"orientation": "vertical", "drawing": "card", "weather": "poster", "layout": None}

    saved = _save(client, "style", style, etag, **site)
    arrivals = {"stops": [{"source": "tfl", "id": "490000077E"}, {"source": "entur", "id": "NSR:Quay:7333"}]}
    saved = _save(client, "arrivals", arrivals, saved.headers["ETag"], **site)

    assert saved.status_code == 200
    assert (saved.get_json()["style"], saved.get_json()["arrivals"]) == (style, {**arrivals, "group_by_route": False})
    with app.app_context():
        config = db.session.get_one(DeviceConfig, device_id)
        assert (config.orientation, config.style, config.weather_design) == ("vertical", "card", "poster")
    # Published as a save on the page is: the stops' subscriptions, and a render queued.
    assert fake_redis.sets["v1:sub:tfl"] == {"490000077E"}
    assert fake_redis.sets["v1:sub:entur"] == {"NSR:Quay:7333"}


def test_a_stop_whose_source_isnt_its_ids_is_refused(paired_client):
    client, device_id, _ = paired_client
    site = {"query_string": {"device_id": device_id}}
    etag = _version(client, **site)

    response = _save(client, "arrivals", {"stops": [{"source": "tfl", "id": "NSR:Quay:7333"}]}, etag, **site)

    assert response.status_code == 400
    assert "source" in response.get_json()["error"]


def test_the_notice_boards_country_is_the_postcodes(paired_client):
    client, device_id, _ = paired_client
    site = {"query_string": {"device_id": device_id}}
    etag = _version(client, **site)

    saved = _save(client, "notice_board", {"postcode": "sw1a1aa", "country": "NO"}, etag, **site)
    refused = _save(client, "notice_board", {"postcode": "NOPE", "country": None}, saved.headers["ETag"], **site)

    assert saved.get_json()["notice_board"] == {"postcode": "SW1A 1AA", "country": "GB"}
    assert refused.status_code == 400


def test_a_services_switch_is_saved_but_on_only_with_its_login(app, paired_client):
    client, device_id, _ = paired_client
    site = {"query_string": {"device_id": device_id}}

    saved = _save(client, "glowmarkt", {"enabled": True}, _version(client, **site), **site)

    assert saved.get_json()["glowmarkt"] == {"enabled": False}  # no Glowmarkt login on the account
    with app.app_context():
        assert db.session.get_one(DeviceConfig, device_id).energy_enabled


# --- the weather -------------------------------------------------------------------------------------


def test_the_weather_is_saved_by_the_key_a_place_search_answered(app, client, touchscreen):
    etag = _version(client, headers=_auth())
    with patch("broker.clients.weather.get_place", return_value=OSLO) as get_place:
        response = _save(client, "weather", {"location": "anything", "key": "oslo-no-3143244"}, etag, headers=_auth())

    get_place.assert_called_once_with(3143244)
    assert response.get_json()["weather"] == {"location": "Oslo", "key": "oslo-no-3143244"}
    with app.app_context():
        config = db.session.get_one(DeviceConfig, touchscreen)
        assert (config.weather_place_id, config.weather_lat) == (3143244, 59.91273)

    # Sent back as it came, the saved place isn't looked up again.
    with patch("broker.clients.weather.get_place") as get_place:
        again = _save(client, "weather", response.get_json()["weather"], response.headers["ETag"], headers=_auth())
    get_place.assert_not_called()
    assert again.status_code == 200


@pytest.mark.parametrize("key", ["oslo-no-1", "bergen-no-3143244", "oslo"])
def test_a_key_that_isnt_a_places_is_refused(client, touchscreen, key):
    etag = _version(client, headers=_auth())
    with patch("broker.clients.weather.get_place", side_effect=lambda place_id: OSLO if place_id == 3143244 else None):
        response = _save(client, "weather", {"location": "Oslo", "key": key}, etag, headers=_auth())

    assert response.status_code == 400


def test_no_key_clears_the_place(app, client, touchscreen):
    etag = _version(client, headers=_auth())

    cleared = _save(client, "weather", {"location": "", "key": None}, etag, headers=_auth())
    typed = _save(client, "weather", {"location": "Oslo", "key": None}, cleared.headers["ETag"], headers=_auth())

    assert cleared.get_json()["weather"] == {"location": "", "key": None}
    assert typed.status_code == 400


def test_a_place_that_cant_be_checked_is_a_503(client, touchscreen):
    etag = _version(client, headers=_auth())
    with patch("broker.clients.weather.get_place", side_effect=weather.GeocodingUnavailableError):
        response = _save(client, "weather", {"location": "Oslo", "key": "oslo-no-3143244"}, etag, headers=_auth())

    assert response.status_code == 503
    assert "Retry-After" in response.headers


# --- the searches, with a device's secret --------------------------------------------------------------


def test_a_paired_touchscreen_searches_with_its_secret(anonymous_client, touchscreen):
    with patch("broker.clients.weather.search_places", return_value=[OSLO]):
        places = anonymous_client.get("/api/places/search?q=oslo", headers=_auth())
    with patch("broker.services.stops.search", return_value=[]):
        stops = anonymous_client.get("/api/stops/search?q=oslo", headers=_auth())

    assert (places.status_code, stops.status_code) == (200, 200)
    (place,) = [PlaceSearchResult.model_validate(result) for result in places.get_json()]
    assert place.value.key == "oslo-no-3143244"


def test_a_device_must_be_paired_to_search(anonymous_client):
    anonymous_client.post("/api/devices/register", json=TOUCHSCREEN["registration"])

    with patch("broker.clients.weather.search_places") as search:
        assert anonymous_client.get("/api/places/search?q=oslo", headers=_auth()).status_code == 403
        assert anonymous_client.get("/api/places/search?q=oslo", headers=_auth("not-any-secret")).status_code == 401
    search.assert_not_called()


def test_the_searches_answer_the_contracts_results(anonymous_client, touchscreen):
    stop = {
        "id": "490000077E",
        "source": "tfl",
        "name": "Oval Station",
        "area": "London",
        "mode": "bus",
        "letter": "E",
        "lines": ["36"],
        "subtitle": "Stop E • 36",
        "indent": False,
    }
    with (
        patch("broker.clients.tfl.search_stops", return_value=[stop]),
        patch("broker.clients.entur.search_stops", return_value=[]),
    ):
        results = anonymous_client.get("/api/stops/search?q=oval", headers=_auth()).get_json()

    (result,) = [StopSearchResult.model_validate(result) for result in results]
    assert (result.value.source, result.value.id) == ("tfl", "490000077E")
