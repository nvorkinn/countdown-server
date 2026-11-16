"""Test a device's panels from the store (GET /api/devices/weather, /arrivals and /notices).

A touchscreen draws its own weather, arrivals and notices, from the values countdown's providers keep in Redis for the
render pool: the store contract's departures, weather and notices keys. The broker reads them for the device's config
as it published it there, and answers them as stored; device_panels.json is what each route answers for its config.
"""

import json
import logging

import pytest
from countdown_api import fixtures
from countdown_api import keys as store_keys
from countdown_api.models import DeviceArrivals, DeviceNotices, DeviceWeather

from tests.routes.test_touchscreens import _auth, _paired, _registered

PANELS = fixtures.load("device_panels.json")
DEPARTURES = fixtures.load("departures.json")
WEATHER = fixtures.load("weather.json")
BACKGROUND = fixtures.load("background.json")
TFL = fixtures.load("tfl_notices.json")
MET_OFFICE = fixtures.load("met_office.json")
BANK_HOLIDAYS = fixtures.load("bank_holidays.json")


def _stored(app, device_id: str, config: dict | None = None) -> dict[str, str]:
    """Put the example's config (as the device's, or `config`) and every provider's example value in the store."""
    store = app.extensions["redis"].strings
    store[store_keys.config(device_id)] = json.dumps({**(config or PANELS["config"]), "device_id": device_id})
    for stop in DEPARTURES["stops"]:
        store[stop["key"]] = json.dumps(stop["value"])
    store[WEATHER["key"]] = json.dumps(WEATHER["value"])
    store[BACKGROUND["key"]] = json.dumps(BACKGROUND["value"])
    for kind in ("lines", "stations", "roads"):
        store[TFL[kind]["key"]] = json.dumps(TFL[kind]["value"])
    store[MET_OFFICE["key"]] = json.dumps(MET_OFFICE["value"])
    store[BANK_HOLIDAYS["key"]] = json.dumps(BANK_HOLIDAYS["value"])
    return store


@pytest.fixture
def device(app, client):
    """Return a paired touchscreen's id, with the example's config and every provider's value in the store."""
    device_id = _paired(app, client)
    _stored(app, device_id)
    return device_id


@pytest.mark.parametrize(
    ("route", "model"), [("weather", DeviceWeather), ("arrivals", DeviceArrivals), ("notices", DeviceNotices)]
)
def test_each_route_answers_the_contracts_example_from_the_store(client, device, route, model):
    response = client.get(f"/api/devices/{route}", headers=_auth())

    assert response.status_code == 200
    assert response.get_json() == PANELS[route]
    model.model_validate(response.get_json())


def test_the_config_is_the_one_in_the_store_not_the_database(app, client, device):
    elsewhere = {**PANELS["config"], "weather": {"location": "Somewhere", "key": None}}
    _stored(app, device, elsewhere)

    response = client.get("/api/devices/weather", headers=_auth())

    assert response.get_json() == {"location": "Somewhere", "key": None, "forecast": None, "background": None}


def test_whatever_isnt_stored_is_null(app, client, device):
    store = app.extensions["redis"].strings
    gone = (WEATHER["key"], BACKGROUND["key"], DEPARTURES["stops"][0]["key"], TFL["lines"]["key"], MET_OFFICE["key"])
    for key in gone:
        del store[key]

    weather = client.get("/api/devices/weather", headers=_auth()).get_json()
    arrivals = client.get("/api/devices/arrivals", headers=_auth()).get_json()
    notices = client.get("/api/devices/notices", headers=_auth()).get_json()

    assert weather["forecast"] is None
    assert weather["background"] is None
    stops = {stop["id"]: stop["departures"] for stop in arrivals["stops"]}
    assert stops[DEPARTURES["stops"][0]["value"]["stop_id"]] is None
    assert len(arrivals["stops"]) == len(PANELS["config"]["arrivals"]["stops"])  # the stop is still listed
    assert notices["tfl_stops"][0]["lines"] is None
    assert notices["tfl_stops"][0]["stations"] == TFL["stations"]["value"]
    assert notices["met_office"] is None
    assert notices["bank_holidays"] == BANK_HOLIDAYS["value"]


def test_a_value_the_contract_wont_read_is_null_and_logged_without_the_value(app, client, device, caplog):
    app.extensions["redis"].strings[TFL["roads"]["key"]] = json.dumps({"postcode": "SE17 3LL", "fetched_at": "never"})

    with caplog.at_level(logging.WARNING):
        notices = client.get("/api/devices/notices", headers=_auth()).get_json()

    assert notices["tfl_roads"] is None
    assert notices["met_office"] == MET_OFFICE["value"]
    assert f"{TFL['roads']['key']} isn't the contract's StoredRoadDisruptions" in caplog.text
    assert "SE17 3LL" not in caplog.text.replace(TFL["roads"]["key"], "")


def test_a_norwegian_postcode_has_no_uk_notices_and_the_store_isnt_asked_for_them(app, client, device):
    oslo = {**PANELS["config"], "notice_board": {"postcode": "0150", "country": "NO"}}
    store = _stored(app, device, oslo)
    store[store_keys.met_office("0150")] = json.dumps(MET_OFFICE["value"])

    notices = client.get("/api/devices/notices", headers=_auth()).get_json()

    assert (notices["postcode"], notices["country"]) == ("0150", "NO")
    assert (notices["tfl_roads"], notices["met_office"], notices["bank_holidays"]) == (None, None, None)
    # Its TfL stops are still its own.
    assert notices["tfl_stops"][0]["lines"] == TFL["lines"]["value"]


def test_a_device_with_no_stops_or_postcode_has_empty_panels(app, client, device):
    bare = {**PANELS["config"], "arrivals": {"stops": []}, "notice_board": {"postcode": None, "country": None}}
    _stored(app, device, bare)

    assert client.get("/api/devices/arrivals", headers=_auth()).get_json() == {"stops": []}
    notices = client.get("/api/devices/notices", headers=_auth()).get_json()
    assert notices == {
        "postcode": None,
        "country": None,
        "tfl_stops": [],
        "tfl_roads": None,
        "met_office": None,
        "bank_holidays": None,
    }


@pytest.mark.parametrize("route", ["weather", "arrivals", "notices"])
def test_a_device_not_paired_has_no_config_in_the_store_so_no_panels(app, client, route):
    device_id = _registered(app, client)
    assert store_keys.config(device_id) not in app.extensions["redis"].strings

    response = client.get(f"/api/devices/{route}", headers=_auth())

    assert response.status_code == 409


@pytest.mark.parametrize("route", ["weather", "arrivals", "notices"])
def test_redis_down_is_a_503_to_try_again(app, client, device, route):
    app.extensions["redis"].fail = True

    response = client.get(f"/api/devices/{route}", headers=_auth())

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"


@pytest.mark.parametrize("route", ["weather", "arrivals", "notices"])
def test_the_panels_need_the_devices_secret(client, device, route):
    assert client.get(f"/api/devices/{route}").status_code == 401
    assert client.get(f"/api/devices/{route}", headers=_auth("not-the-secret-at-all")).status_code == 401
