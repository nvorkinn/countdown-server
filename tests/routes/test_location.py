"""The setup wizard's Location chapter: POST /api/devices/locate, GET /api/postcodes/lookup and GET /api/stops/near.

Google, postcodes.io, Kartverket and TfL are mocked (responses).
"""

import json
import logging
from urllib.parse import parse_qsl, urlsplit

import pytest
import responses
from countdown_api import fixtures
from countdown_api.models import DeviceLocation, NearbyStopResult, Postcode

from broker.clients.google_geolocation import GEOLOCATE_URL
from broker.clients.kartverket import ADDRESS_SEARCH_URL
from broker.clients.postcodes_io import API_BASE as POSTCODES_IO
from broker.clients.tfl import API_BASE as TFL
from broker.services import pairing
from broker.services.pair_throttle import LOCATES_PER_HOUR

LOCATE = "/api/devices/locate"
SCAN = {
    "wifi_access_points": [
        {"mac_address": "aa:bb:cc:dd:ee:01", "signal_strength": -43, "channel": 11, "age": 120},
        {"mac_address": "aa:bb:cc:dd:ee:02", "signal_strength": -71},
    ]
}
SE17_2PU = {
    "postcode": "SE17 2PU",
    "latitude": 51.484787,
    "longitude": -0.091724,
    "country": "England",
    "region": "London",
    "admin_district": "Southwark",
}


def _query(index: int = 0) -> dict[str, str]:
    """Return the query of the call mocked at `index`."""
    return dict(parse_qsl(urlsplit(str(responses.calls[index].request.url)).query))


def _body(index: int = 0) -> dict:
    """Return the JSON body of the call mocked at `index`."""
    body = responses.calls[index].request.body
    assert isinstance(body, bytes | str)
    return json.loads(body)


@pytest.fixture
def device(app, paired_client):
    """Return a client that sends a paired screen's secret, with a Google key set."""
    client, _, secret = paired_client
    app.config["GOOGLE_GEOLOCATION_API_KEY"] = "the-google-key"
    client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {secret}"
    return client


def _google_finds(lat=51.4884, lng=-0.0917, accuracy=12.0):
    responses.post(GEOLOCATE_URL, json={"location": {"lat": lat, "lng": lng}, "accuracy": accuracy})


def _postcodes_io_finds(*postcodes):
    responses.get(f"{POSTCODES_IO}/postcodes", json={"status": 200, "result": list(postcodes) or None})


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", LOCATE),
        ("GET", "/api/postcodes/lookup?postcode=SE172PU"),
        ("GET", "/api/stops/near?latitude=51.5&longitude=-0.1"),
    ],
)
@responses.activate
def test_each_needs_a_paired_screen(client, anonymous_client, register_device, method, path):
    """Each spends a key (or a source's patience), so isn't open to anyone who finds the URL, or anyone who signs up."""
    _, unpaired_secret = register_device("an-unpaired-screens-secret")

    assert anonymous_client.open(path, method=method, json=SCAN).status_code == 401
    assert client.open(path, method=method, json=SCAN).status_code == 403
    unpaired = anonymous_client.open(
        path, method=method, json=SCAN, headers={"Authorization": f"Bearer {unpaired_secret}"}
    )
    assert unpaired.status_code == 403
    assert len(responses.calls) == 0


@responses.activate
def test_locate_returns_the_position_and_the_postcodes_nearest_first(device):
    _google_finds(accuracy=12.4)
    _postcodes_io_finds(SE17_2PU | {"distance": 381.6}, SE17_2PU | {"postcode": "SE17 2HG", "distance": 23.29})

    response = device.post(LOCATE, json=SCAN)

    assert response.status_code == 200
    assert response.get_json() == {
        "latitude": 51.4884,
        "longitude": -0.0917,
        "accuracy": 12.4,
        "postcodes": [
            {"postcode": "SE17 2HG", "latitude": 51.484787, "longitude": -0.091724, "distance": 23, "country": "GB"},
            {"postcode": "SE17 2PU", "latitude": 51.484787, "longitude": -0.091724, "distance": 382, "country": "GB"},
        ],
    }
    assert _query(0) == {"key": "the-google-key"}
    assert _body(0)["wifiAccessPoints"][1] == {"macAddress": "aa:bb:cc:dd:ee:02", "signalStrength": -71}
    assert _query(1) == {"lon": "-0.0917", "lat": "51.4884", "limit": "10", "radius": "200"}


@pytest.mark.parametrize(("accuracy", "radius"), [(5.0, "200"), (640.4, "640"), (25000.0, "2000")])
@responses.activate
def test_locate_looks_for_postcodes_as_far_out_as_google_is_unsure_within_bounds(device, accuracy, radius):
    _google_finds(accuracy=accuracy)
    _postcodes_io_finds()

    response = device.post(LOCATE, json=SCAN)

    assert response.status_code == 200
    assert response.get_json()["postcodes"] == []
    assert _query(1)["radius"] == radius


@pytest.mark.parametrize(
    "body",
    [
        None,
        {},
        {"wifi_access_points": SCAN["wifi_access_points"][:1]},
        {"wifi_access_points": SCAN["wifi_access_points"] * 16},
        {"wifi_access_points": ["aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"]},
        {
            "wifi_access_points": [
                SCAN["wifi_access_points"][0],
                {"mac_address": "AA:BB:CC:DD:EE:02", "signal_strength": -71},
            ]
        },
        {
            "wifi_access_points": [
                SCAN["wifi_access_points"][0],
                {"mac_address": "aa-bb-cc-dd-ee-02", "signal_strength": -71},
            ]
        },
        {"wifi_access_points": [SCAN["wifi_access_points"][0], {"mac_address": "aa:bb:cc:dd:ee:02"}]},
        {
            "wifi_access_points": [
                SCAN["wifi_access_points"][0],
                {"mac_address": "aa:bb:cc:dd:ee:02", "signal_strength": 5},
            ]
        },
        {
            "wifi_access_points": [
                SCAN["wifi_access_points"][0],
                {"mac_address": "aa:bb:cc:dd:ee:02", "signal_strength": -7.5},
            ]
        },
        {
            "wifi_access_points": [
                SCAN["wifi_access_points"][0],
                {"mac_address": "aa:bb:cc:dd:ee:02", "signal_strength": True},
            ]
        },
        {
            "wifi_access_points": [
                SCAN["wifi_access_points"][0],
                {"mac_address": "aa:bb:cc:dd:ee:02", "signal_strength": -71, "channel": 0},
            ]
        },
        {
            "wifi_access_points": [
                SCAN["wifi_access_points"][0],
                {"mac_address": "aa:bb:cc:dd:ee:02", "signal_strength": -71, "age": -1},
            ]
        },
    ],
)
@responses.activate
def test_locate_refuses_a_body_that_isnt_a_scan(device, body):
    response = device.post(LOCATE, json=body)

    assert response.status_code == 400
    assert response.get_json()["error"]
    assert len(responses.calls) == 0


@responses.activate
def test_locate_is_a_404_when_google_cant_place_the_scan(device):
    responses.post(GEOLOCATE_URL, status=404, json={"error": {"code": 404, "message": "Not Found"}})

    response = device.post(LOCATE, json=SCAN)

    assert response.status_code == 404
    assert response.get_json()["error"]


@responses.activate
def test_locate_is_a_502_when_google_fails_logging_no_key_nor_access_point(device, caplog):
    responses.post(GEOLOCATE_URL, status=403, json={"error": {"code": 403, "message": "API key not valid"}})

    with caplog.at_level(logging.DEBUG):
        response = device.post(LOCATE, json=SCAN)

    assert response.status_code == 502
    assert response.get_json()["error"]
    assert "the-google-key" not in caplog.text
    assert "aa:bb:cc" not in caplog.text


@responses.activate
def test_locate_is_a_502_when_postcodes_io_fails_logging_no_position(device, caplog):
    _google_finds()
    responses.get(f"{POSTCODES_IO}/postcodes", status=503)

    with caplog.at_level(logging.DEBUG):
        response = device.post(LOCATE, json=SCAN)

    assert response.status_code == 502
    assert response.get_json()["error"]
    assert "51.48" not in caplog.text


@responses.activate
def test_locate_is_a_503_without_a_google_key(app, device):
    app.config["GOOGLE_GEOLOCATION_API_KEY"] = ""

    response = device.post(LOCATE, json=SCAN)

    assert response.status_code == 503
    assert response.get_json()["error"]
    assert len(responses.calls) == 0


@responses.activate
def test_locate_is_throttled_per_device(device, app, paired_client, register_device):
    _google_finds()
    _postcodes_io_finds()

    for _ in range(LOCATES_PER_HOUR):
        assert device.post(LOCATE, json=SCAN).status_code == 200
    throttled = device.post(LOCATE, json=SCAN)

    assert throttled.status_code == 429
    assert throttled.get_json()["error"]
    assert 0 < int(throttled.headers["Retry-After"]) <= 3600
    assert len(responses.calls) == 2 * LOCATES_PER_HOUR

    # Another screen isn't held up by this one.
    other_id, other_secret = register_device("another-screens-secret")
    with app.app_context():
        code = pairing.issue_code(other_id)
    client, _, _ = paired_client
    assert client.post("/pair/add", data={"code": code}).status_code == 302
    other = app.test_client().post(LOCATE, json=SCAN, headers={"Authorization": f"Bearer {other_secret}"})
    assert other.status_code == 200


@responses.activate
def test_lookup_postcode_finds_a_uk_postcode_in_any_spelling(device):
    responses.get(f"{POSTCODES_IO}/postcodes/SE17%202PU", json={"status": 200, "result": SE17_2PU | {"quality": 1}})

    response = device.get("/api/postcodes/lookup?postcode=se172pu")

    assert response.status_code == 200
    assert response.get_json() == SE17_2PU


@responses.activate
def test_lookup_postcode_finds_a_norwegian_postcode_at_its_first_address(device):
    address = {"kommunenavn": "OSLO", "representasjonspunkt": {"epsg": "EPSG:4258", "lat": 59.903, "lon": 10.7403}}
    responses.get(ADDRESS_SEARCH_URL, json={"adresser": [address]})

    response = device.get("/api/postcodes/lookup?postcode=0150")

    assert response.status_code == 200
    assert response.get_json() == {
        "postcode": "0150",
        "latitude": 59.903,
        "longitude": 10.7403,
        "country": "Norway",
        "region": None,
        "admin_district": "Oslo",
    }
    assert _query(0) == {"postnummer": "0150", "treffPerSide": "1"}


@pytest.mark.parametrize("postcode", ["", "SW1A", "12345", "not a postcode", "S E 1 7 2 P U"])
@responses.activate
def test_lookup_postcode_refuses_what_isnt_a_postcode(device, postcode):
    response = device.get("/api/postcodes/lookup", query_string={"postcode": postcode})

    assert response.status_code == 400
    assert response.get_json()["error"]
    assert len(responses.calls) == 0


@responses.activate
def test_lookup_postcode_is_a_404_for_one_that_doesnt_exist(device):
    responses.get(
        f"{POSTCODES_IO}/postcodes/ZZ1%201ZZ", status=404, json={"status": 404, "error": "Postcode not found"}
    )
    responses.get(ADDRESS_SEARCH_URL, json={"adresser": []})

    assert device.get("/api/postcodes/lookup?postcode=ZZ11ZZ").status_code == 404
    assert device.get("/api/postcodes/lookup?postcode=9999").status_code == 404


@responses.activate
def test_lookup_postcode_is_a_502_when_its_source_fails_logging_no_postcode(device, caplog):
    responses.get(f"{POSTCODES_IO}/postcodes/SE17%202PU", status=500)
    responses.get(ADDRESS_SEARCH_URL, status=500)

    with caplog.at_level(logging.DEBUG):
        assert device.get("/api/postcodes/lookup?postcode=SE172PU").status_code == 502
        assert device.get("/api/postcodes/lookup?postcode=0150").status_code == 502

    assert "SE17" not in caplog.text
    assert "0150" not in caplog.text


def _tube(naptan_id, name, distance):
    return {
        "naptanId": naptan_id,
        "commonName": f"{name} Underground Station",
        "stopType": "NaptanMetroStation",
        "distance": distance,
        "lines": [{"name": "Northern"}],
    }


def _bus(naptan_id, letter, distance):
    return {
        "naptanId": naptan_id,
        "commonName": "Walworth Road",
        "stopType": "NaptanPublicBusCoachTram",
        "stopLetter": letter,
        "distance": distance,
        "lines": [{"name": "12"}],
    }


@responses.activate
def test_stops_near_lists_tube_stations_and_bus_stops_nearest_first(device):
    stop_points = [
        _tube("940GZZLUKNG", "Kennington", 754.4),
        _bus("490A", "A", 120.6),
        _tube("940GZZLUEAC", "Elephant & Castle", 73.2),
        _bus("490FAR", "Z", 512.0),  # too far to walk to a bus
    ]
    responses.get(f"{TFL}/StopPoint", json={"stopPoints": stop_points})

    response = device.get("/api/stops/near?latitude=51.4948&longitude=-0.1001")

    assert response.status_code == 200
    results = response.get_json()
    assert [(r["id"], r["distance"]) for r in results] == [("940GZZLUEAC", 73), ("490A", 121), ("940GZZLUKNG", 754)]
    assert results[0] == {
        "id": "940GZZLUEAC",
        "name": "Elephant & Castle",
        "mode": "tube",
        "letter": "",
        "lines": ["Northern"],
        "subtitle": "Underground • Northern",
        "source": "tfl",
        "area": "London",
        "indent": False,
        "value": {"source": "tfl", "id": "940GZZLUEAC"},
        "distance": 73,
    }
    assert results[1]["letter"] == "A"
    params = _query(0)
    assert params["radius"] == "1500"
    assert params["app_key"] == "test-tfl-key"


@responses.activate
def test_stops_near_lists_six_tube_stations_and_twenty_stops_at_most(device):
    stop_points = [_tube(f"940T{n}", f"Station {n}", 600.0 + n) for n in range(10)]
    stop_points += [_bus(f"490B{n:02}", "A", 10.0 + n) for n in range(30)]
    responses.get(f"{TFL}/StopPoint", json={"stopPoints": stop_points})

    results = device.get("/api/stops/near?latitude=51.4948&longitude=-0.1001").get_json()

    assert len(results) == 20
    assert all(r["mode"] == "bus" for r in results)
    assert [r["distance"] for r in results] == sorted(r["distance"] for r in results)

    responses.replace(responses.GET, f"{TFL}/StopPoint", json={"stopPoints": stop_points[:10]})
    tube = device.get("/api/stops/near?latitude=51.4948&longitude=-0.1001").get_json()
    assert [r["id"] for r in tube] == [f"940T{n}" for n in range(6)]


@responses.activate
def test_stops_near_is_empty_outside_london(device):
    responses.get(f"{TFL}/StopPoint", json={"stopPoints": []})

    for position in ("latitude=53.48&longitude=-2.24", "latitude=59.91&longitude=10.75"):
        response = device.get(f"/api/stops/near?{position}")
        assert response.status_code == 200
        assert response.get_json() == []


@pytest.mark.parametrize(
    "query",
    [
        "",
        "latitude=51.5",
        "longitude=-0.1",
        "latitude=north&longitude=-0.1",
        "latitude=91&longitude=0",
        "latitude=0&longitude=nan",
    ],
)
@responses.activate
def test_stops_near_refuses_what_isnt_a_position(device, query):
    response = device.get(f"/api/stops/near?{query}")

    assert response.status_code == 400
    assert response.get_json()["error"]
    assert len(responses.calls) == 0


@responses.activate
def test_stops_near_is_a_502_with_none_when_tfl_fails_logging_no_position_nor_key(device, caplog):
    responses.get(f"{TFL}/StopPoint", status=500)

    with caplog.at_level(logging.DEBUG):
        response = device.get("/api/stops/near?latitude=51.4948&longitude=-0.1001")

    assert response.status_code == 502
    assert response.get_json() == []
    assert "51.4948" not in caplog.text
    assert "test-tfl-key" not in caplog.text


EXAMPLE = fixtures.load("locate.json")


@responses.activate
def test_locate_answers_the_contracts_example(device):
    located = EXAMPLE["locate"]
    _google_finds(located["latitude"], located["longitude"], located["accuracy"])
    _postcodes_io_finds(*({**found, "country": "England"} for found in located["postcodes"]))

    response = device.post(LOCATE, json=EXAMPLE["locate_request"])

    assert response.status_code == 200
    assert response.get_json() == located
    DeviceLocation.model_validate(response.get_json())
    assert len(_body(0)["wifiAccessPoints"]) == len(EXAMPLE["locate_request"]["wifi_access_points"])


@responses.activate
def test_lookup_postcode_answers_the_contracts_example(device):
    found = EXAMPLE["postcode"]
    responses.get(f"{POSTCODES_IO}/postcodes/SE17%202PU", json={"status": 200, "result": found | {"quality": 1}})

    response = device.get("/api/postcodes/lookup", query_string={"postcode": EXAMPLE["postcode_query"]})

    assert response.status_code == 200
    assert response.get_json() == found
    Postcode.model_validate(response.get_json())


@responses.activate
def test_stops_near_answers_the_contracts_nearby_stop_results(device):
    stop_points = [_bus("490014501K", "K", 136.2), _tube("940GZZLUKNG", "Kennington", 1062.4)]
    responses.get(f"{TFL}/StopPoint", json={"stopPoints": stop_points})

    response = device.get("/api/stops/near", query_string=EXAMPLE["near_query"])

    assert response.status_code == 200
    results = response.get_json()
    assert [(r["id"], r["distance"]) for r in results] == [("490014501K", 136), ("940GZZLUKNG", 1062)]
    for result in results:
        NearbyStopResult.model_validate(result)
