"""Test the location step of a touchscreen's setup on its owner's phone (/device/<id>/setup/location).

The phone posts where it is, picks or types a postcode, ticks the stops near it, and the save sets the touchscreen's
notice board, weather and arrivals and ends the step. Positions are posted, never in an address, and nothing is logged.
"""

import html
import logging
from urllib.parse import parse_qsl, urlsplit

import pytest
import responses
from countdown_api import fixtures

from broker.clients.postcodes_io import API_BASE as POSTCODES_IO
from broker.clients.tfl import API_BASE as TFL
from broker.db import db
from broker.models import DeviceConfig, TouchscreenSetup
from broker.routes import setup_location as pages
from broker.services import geoip
from tests.conftest import USER
from tests.routes.test_touchscreen_setup_status import _following, _key_saved, _report, _status
from tests.routes.test_touchscreens import _auth

EXAMPLE = fixtures.load("locate.json")
POSITION = EXAMPLE["near_postcodes_request"]
POSTCODE = EXAMPLE["postcode"]
PLACE = {
    "id": 2643743,
    "name": "London",
    "label": "London, England, United Kingdom",
    "country_code": "GB",
    "latitude": 51.50853,
    "longitude": -0.12574,
}


@pytest.fixture
def at_location(app, client, anonymous_client):
    """Return the id of a touchscreen whose setup is at the location step, with the client its owner's phone."""
    device_id = _following(app, client)
    _key_saved(client, anonymous_client, device_id)
    assert _report(client, {"soloist_signed_in": True}).step == "location"
    return device_id


def _url(device_id, page=""):
    return f"/device/{device_id}/setup/location{page}"


def _html(response):
    """Return the page as it reads: its text with the entities a template writes (an apostrophe's) undone."""
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    return html.unescape(response.get_data(as_text=True))


def _postcodes_io_finds(*postcodes):
    responses.get(f"{POSTCODES_IO}/postcodes", json={"status": 200, "result": list(postcodes) or None})


def _postcodes_io_knows(postcode=POSTCODE):
    responses.get(
        f"{POSTCODES_IO}/postcodes/{postcode['postcode'].replace(' ', '%20')}",
        json={"status": 200, "result": postcode | {"quality": 1}},
    )


def _tfl_finds(*stop_points):
    responses.get(f"{TFL}/StopPoint", json={"stopPoints": list(stop_points)})


def _tube(naptan_id, name, distance, *lines):
    return {
        "naptanId": naptan_id,
        "commonName": name,
        "stopType": "NaptanMetroStation",
        "modes": ["tube"],
        "lines": [{"id": line.lower(), "name": line} for line in lines],
        "distance": distance,
    }


def _bus(naptan_id, name, letter, distance, *routes):
    return {
        "naptanId": naptan_id,
        "commonName": name,
        "stopType": "NaptanPublicBusCoachTram",
        "modes": ["bus"],
        "stopLetter": letter,
        "lines": [{"id": route.lower(), "name": route} for route in routes],
        "distance": distance,
    }


def _weather_is(monkeypatch, place=PLACE):
    """Have the position be in `place`, as the place data and Open-Meteo would say; None for a place neither knows."""
    located = geoip.Located("London", 51.50853, -0.12574) if place else None
    monkeypatch.setattr(geoip, "place_near", lambda *_: located)
    monkeypatch.setattr(pages.weather, "search_places", lambda _: [place] if place else [])


# --- getting to the step, and leaving it ----------------------------------------------------------


def test_the_setup_sends_the_phone_to_the_step_when_it_is_the_one(client, at_location):
    response = client.get(f"/device/{at_location}/setup")

    assert response.status_code == 302
    assert response.headers["Location"] == _url(at_location)


def test_the_start_offers_the_phones_location_a_typed_postcode_and_to_skip(client, at_location):
    page = _html(client.get(_url(at_location)))

    assert "Where is your touchscreen?" in page
    # The button a script shows where the browser can say where the phone is, and the form it posts.
    assert 'id="locate" hidden' in page
    assert f'action="{_url(at_location, "/near")}"' in page
    assert 'name="latitude"' in page
    assert f'data-type-url="{_url(at_location, "/type")}?located=no"' in page
    assert f'href="{_url(at_location, "/type")}"' in page
    assert f'action="{_url(at_location, "/skip")}"' in page
    assert "touchscreen_location.js" in page
    # It watches the step like the other steps' pages, in case the touchscreen finishes without it.
    assert 'data-step="location"' in page
    assert "touchscreen_setup.js" in page


@pytest.mark.parametrize(
    ("method", "page"),
    [("GET", ""), ("GET", "/type"), ("POST", "/near"), ("POST", "/postcode"), ("POST", "/save")],
)
@responses.activate
def test_away_from_the_step_every_page_goes_back_to_the_setup(app, client, method, page):
    device_id = _following(app, client)

    response = client.open(_url(device_id, page), method=method, data={"postcode": "SE17 2PU"})

    assert response.status_code == 303
    assert response.headers["Location"] == f"/device/{device_id}/setup"
    assert len(responses.calls) == 0


@pytest.mark.parametrize(
    ("method", "page"),
    [("GET", ""), ("GET", "/type"), ("POST", "/near"), ("POST", "/postcode"), ("POST", "/save"), ("POST", "/skip")],
)
def test_only_its_owner_reaches_a_screens_location_step(app, client, anonymous_client, at_location, method, page):
    someone = app.test_client()
    someone.environ_base["HTTP_REMOTE_SUB"] = f"not-{USER}"

    assert someone.open(_url(at_location, page), method=method).status_code == 404
    assert anonymous_client.open(_url(at_location, page), method=method).status_code == 401
    assert _status(client).step == "location"


def test_skipping_ends_the_step_and_saves_nothing(app, client, at_location):
    response = client.post(_url(at_location, "/skip"))

    assert response.status_code == 303
    assert response.headers["Location"] == f"/device/{at_location}/setup"
    assert _status(client).step == "presence"
    with app.app_context():
        assert not db.session.get_one(DeviceConfig, at_location).postcode


# --- the postcodes near the phone ------------------------------------------------------------------


@responses.activate
def test_the_phones_position_gets_the_best_guess_and_the_rest_in_order(client, at_location):
    found = [{**postcode, "country": "England"} for postcode in EXAMPLE["near_postcodes"]]
    _postcodes_io_finds(*found)

    page = _html(client.post(_url(at_location, "/near"), data=POSITION))

    assert "Is this your postcode?" in page
    nearest, others = found[0]["postcode"], sorted(postcode["postcode"] for postcode in found[1:])
    assert f'class="postcode is-best" name="postcode" value="{nearest}"' in page
    assert "Our best guess" in page
    shown = [page.index(f'class="postcode" name="postcode" value="{other}"') for other in others]
    assert shown == sorted(shown)
    assert f'action="{_url(at_location, "/postcode")}"' in page
    assert "Not here? Type it in" in page
    # Asked of postcodes.io, as far out as the phone is unsure.
    assert len(responses.calls) == 1
    asked = dict(parse_qsl(urlsplit(str(responses.calls[0].request.url)).query))
    assert asked == {
        "lon": str(POSITION["longitude"]),
        "lat": str(POSITION["latitude"]),
        "limit": "10",
        "radius": "200",
    }


@responses.activate
def test_a_lone_postcode_is_the_best_guess_with_no_others(client, at_location):
    _postcodes_io_finds({**EXAMPLE["near_postcodes"][0], "country": "England"})

    page = _html(client.post(_url(at_location, "/near"), data=POSITION))

    assert "Our best guess" in page
    assert "Or one of these nearby?" not in page


@pytest.mark.parametrize(
    "position",
    [
        {},
        {"latitude": "51.5"},
        {"latitude": "north", "longitude": "-0.1"},
        {"latitude": "91", "longitude": "0"},
        {"latitude": "51.5", "longitude": "181"},
        {"latitude": "nan", "longitude": "0"},
    ],
)
@responses.activate
def test_without_a_position_the_owner_types_their_postcode(client, at_location, position):
    page = _html(client.post(_url(at_location, "/near"), data=position))

    assert "What's your postcode?" in page
    assert "couldn't get this phone's location" in page
    assert len(responses.calls) == 0


@responses.activate
def test_with_no_postcode_near_the_owner_types_theirs(client, at_location):
    _postcodes_io_finds()

    page = _html(client.post(_url(at_location, "/near"), data={"latitude": "59.91", "longitude": "10.75"}))

    assert "What's your postcode?" in page
    assert "couldn't find a postcode near you" in page


@responses.activate
def test_when_postcodes_io_fails_the_owner_types_theirs_and_no_position_is_logged(client, at_location, caplog):
    responses.get(f"{POSTCODES_IO}/postcodes", status=503)

    with caplog.at_level(logging.DEBUG):
        page = _html(client.post(_url(at_location, "/near"), data=POSITION | {"accuracy": "-5"}))

    assert "What's your postcode?" in page
    assert str(POSITION["latitude"]) not in caplog.text


def test_the_typing_page_says_why_when_the_phone_couldnt_be_located(client, at_location):
    plain = _html(client.get(_url(at_location, "/type")))
    refused = _html(client.get(_url(at_location, "/type"), query_string={"located": "no"}))

    assert 'name="postcode"' in plain
    assert 'autocomplete="postal-code"' in plain
    assert "couldn't get this phone's location" not in plain
    assert "couldn't get this phone's location" in refused


# --- a postcode picked or typed, and its stops ------------------------------------------------------


@responses.activate
def test_a_postcode_shows_the_stations_and_bus_stops_near_it_to_tick(client, at_location):
    _postcodes_io_knows()
    _tfl_finds(
        _bus("490014501K", "Westmoreland Road", "K", 136.2, "40", "68", "176", "468", "N68", "12", "148", "171"),
        _tube("940GZZLUKNG", "Kennington Underground Station", 1062.0, "Northern"),
        _tube("940GZZLUEAC", "Elephant & Castle Underground Station", 1198.4, "Bakerloo", "Northern"),
    )

    page = _html(client.post(_url(at_location, "/postcode"), data={"postcode": " se172pu "}))

    assert "The touchscreen can show live bus and Tube times" in page
    assert f'action="{_url(at_location, "/save")}"' in page
    # The postcode in its canonical form, with where it is, for the save.
    assert f'name="postcode" value="{POSTCODE["postcode"]}"' in page
    assert f'name="latitude" value="{POSTCODE["latitude"]}"' in page
    # Stations by their own names, their lines in TfL's colours.
    assert '<span class="stop-name">Kennington</span>' in page
    assert "Elephant & Castle</span>" in page
    assert "Underground Station" not in page
    assert 'style="background: #a45a2a; color: #fff">Bakerloo</span>' in page
    assert 'name="stop" value="940GZZLUKNG"' in page
    assert "1.1 km" in page
    # A bus stop with its letter, its first six routes, and the rest counted.
    assert '<span class="stop-letter">K</span>Westmoreland Road' in page
    assert page.count('class="chip is-bus"') == 6
    assert '<span class="chip is-more">+2</span>' in page
    assert "140 m" in page
    # None ticked to begin with.
    assert "checked" not in page


@responses.activate
def test_only_so_many_stations_and_bus_stops_are_offered(client, at_location):
    _postcodes_io_knows()
    _tfl_finds(
        *(_tube(f"940GZZLU{n}", f"Station {n} Underground Station", 300 + n, "Victoria") for n in range(5)),
        *(_bus(f"49000000{n}A", f"Stop {n}", "A", 100 + n, "1") for n in range(8)),
    )

    page = _html(client.post(_url(at_location, "/postcode"), data={"postcode": POSTCODE["postcode"]}))

    assert page.count("Underground") == 0
    assert page.count('name="stop" value="940GZZLU') == 3
    assert page.count('name="stop" value="49000000') == 5


@pytest.mark.parametrize("typed", ["", "nowhere", "SE17", "SW1A 1AA and more"])
@responses.activate
def test_what_isnt_a_postcode_is_asked_for_again(client, at_location, typed):
    page = _html(client.post(_url(at_location, "/postcode"), data={"postcode": typed}))

    assert "doesn't look like a UK or Norwegian postcode" in page
    assert len(responses.calls) == 0


@responses.activate
def test_a_postcode_no_one_knows_is_asked_for_again_with_what_was_typed(client, at_location):
    responses.get(f"{POSTCODES_IO}/postcodes/ZZ9%209ZZ", status=404, json={"status": 404})

    page = _html(client.post(_url(at_location, "/postcode"), data={"postcode": "zz99zz"}))

    assert "couldn't find that postcode" in page
    assert 'value="zz99zz"' in page


@responses.activate
def test_a_postcode_that_cant_be_looked_up_can_be_tried_again(client, at_location):
    responses.get(f"{POSTCODES_IO}/postcodes/SE17%202PU", status=503)

    page = _html(client.post(_url(at_location, "/postcode"), data={"postcode": "SE17 2PU"}))

    assert "Something went wrong there. Try again?" in page


# --- the save ---------------------------------------------------------------------------------------


@responses.activate
def test_the_save_sets_the_postcode_the_weather_and_the_stops_and_ends_the_step(app, client, at_location, monkeypatch):
    _weather_is(monkeypatch)
    form = {
        "postcode": POSTCODE["postcode"],
        "latitude": str(POSTCODE["latitude"]),
        "longitude": str(POSTCODE["longitude"]),
        "stop": ["940GZZLUKNG", "490014501K"],
    }

    page = _html(client.post(_url(at_location, "/save"), data=form))

    assert "You're all set!" in page
    assert f"<li>{POSTCODE['postcode']}</li>" in page
    assert "<li>London</li>" in page
    assert "<li>2 stops</li>" in page
    assert f'href="/device/{at_location}/setup"' in page
    with app.app_context():
        config = db.session.get_one(DeviceConfig, at_location)
        assert config.postcode == POSTCODE["postcode"]
        assert (config.weather_place_id, config.weather_location) == (PLACE["id"], "London")
        assert config.tfl_stop_ids == ["940GZZLUKNG", "490014501K"]
        assert db.session.get_one(TouchscreenSetup, at_location).location_done_at is not None
    # Both screens move on, and the touchscreen reads what was saved in its config.
    assert _status(client).step == "presence"
    saved = client.get("/api/devices/config", headers=_auth()).get_json()
    assert saved["notice_board"]["postcode"] == POSTCODE["postcode"]
    assert [stop["id"] for stop in saved["arrivals"]["stops"]] == ["940GZZLUKNG", "490014501K"]
    assert client.get(f"/device/{at_location}/setup").status_code == 200


@responses.activate
def test_with_no_stops_ticked_the_arrivals_stay_as_they_were(app, client, at_location, monkeypatch):
    _weather_is(monkeypatch)
    with app.app_context():
        db.session.get_one(DeviceConfig, at_location).tfl_stop_ids = ["940GZZLUOVL"]
        db.session.commit()
    form = {"postcode": POSTCODE["postcode"], "latitude": "51.48", "longitude": "-0.09"}

    page = _html(client.post(_url(at_location, "/save"), data=form))

    assert "<li>No stops yet</li>" in page
    with app.app_context():
        assert db.session.get_one(DeviceConfig, at_location).tfl_stop_ids == ["940GZZLUOVL"]


@responses.activate
def test_a_weather_place_that_cant_be_found_doesnt_stop_the_save(app, client, at_location, monkeypatch):
    _weather_is(monkeypatch, place=None)
    with app.app_context():
        before = db.session.get_one(DeviceConfig, at_location).weather_location
    form = {"postcode": POSTCODE["postcode"], "latitude": "51.48", "longitude": "-0.09", "stop": "940GZZLUKNG"}

    page = _html(client.post(_url(at_location, "/save"), data=form))

    assert "You're all set!" in page
    assert "<li>1 stop</li>" in page
    assert page.count("<li>") == 2
    with app.app_context():
        config = db.session.get_one(DeviceConfig, at_location)
        assert config.postcode == POSTCODE["postcode"]
        assert config.weather_location == before


@responses.activate
def test_the_weather_stays_as_it_was_when_open_meteo_cant_be_asked(app, client, at_location, monkeypatch):
    monkeypatch.setattr(geoip, "place_near", lambda *position: geoip.Located("London", *position))

    def unavailable(_):
        raise pages.weather.GeocodingUnavailableError

    monkeypatch.setattr(pages.weather, "search_places", unavailable)
    form = {"postcode": POSTCODE["postcode"], "latitude": "51.48", "longitude": "-0.09"}

    assert "You're all set!" in _html(client.post(_url(at_location, "/save"), data=form))
    assert _status(client).step == "presence"


@responses.activate
def test_a_save_without_a_position_sets_the_postcode_alone(app, client, at_location):
    page = _html(client.post(_url(at_location, "/save"), data={"postcode": POSTCODE["postcode"]}))

    assert "You're all set!" in page
    with app.app_context():
        assert db.session.get_one(DeviceConfig, at_location).postcode == POSTCODE["postcode"]


@responses.activate
def test_a_save_of_what_isnt_a_postcode_saves_nothing_and_asks_again(app, client, at_location):
    page = _html(client.post(_url(at_location, "/save"), data={"postcode": "nowhere", "stop": "940GZZLUKNG"}))

    assert "doesn't look like a UK or Norwegian postcode" in page
    assert _status(client).step == "location"
    with app.app_context():
        config = db.session.get_one(DeviceConfig, at_location)
        assert not config.postcode
        assert not config.tfl_stop_ids


@responses.activate
def test_a_save_takes_no_more_stops_than_the_page_offers(app, client, at_location):
    form = {"postcode": POSTCODE["postcode"], "stop": [f"49000000{n}A" for n in range(12)]}

    page = _html(client.post(_url(at_location, "/save"), data=form))

    assert "<li>8 stops</li>" in page
    with app.app_context():
        assert len(db.session.get_one(DeviceConfig, at_location).tfl_stop_ids) == 8


@responses.activate
def test_a_postcode_with_no_stops_near_is_saved_at_once(app, client, at_location, monkeypatch):
    _weather_is(monkeypatch)
    _postcodes_io_knows()
    _tfl_finds()

    page = _html(client.post(_url(at_location, "/postcode"), data={"postcode": POSTCODE["postcode"]}))

    assert "You're all set!" in page
    assert "<li>No stops yet</li>" in page
    assert _status(client).step == "presence"


@responses.activate
def test_when_tfl_fails_the_postcode_is_saved_without_stops_logging_no_position(
    app, client, at_location, monkeypatch, caplog
):
    _weather_is(monkeypatch)
    _postcodes_io_knows()
    responses.get(f"{TFL}/StopPoint", status=500)

    with caplog.at_level(logging.DEBUG):
        page = _html(client.post(_url(at_location, "/postcode"), data={"postcode": POSTCODE["postcode"]}))

    assert "You're all set!" in page
    assert str(POSTCODE["latitude"]) not in caplog.text
