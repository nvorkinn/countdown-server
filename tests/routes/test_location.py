"""The setup wizard's Location chapter: GET /api/postcodes/lookup and GET /api/stops/near.

postcodes.io, Kartverket and TfL are mocked (responses).
"""

import logging
from urllib.parse import parse_qsl, urlsplit

import pytest
import responses
from countdown_api import fixtures
from countdown_api.models import NearbyStopResult, Postcode

from broker.clients.kartverket import ADDRESS_SEARCH_URL
from broker.clients.postcodes_io import API_BASE as POSTCODES_IO
from broker.clients.tfl import API_BASE as TFL

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


@pytest.fixture
def device(paired_client):
    """Return a client that sends a paired screen's secret."""
    client, _, secret = paired_client
    client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {secret}"
    return client


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/postcodes/lookup?postcode=SE172PU"),
        ("GET", "/api/stops/near?latitude=51.5&longitude=-0.1"),
    ],
)
@responses.activate
def test_each_needs_a_paired_screen(client, anonymous_client, register_device, method, path):
    """Each spends a key (or a source's patience), so isn't open to anyone who finds the URL, or anyone who signs up."""
    _, unpaired_secret = register_device("an-unpaired-screens-secret")

    assert anonymous_client.open(path, method=method).status_code == 401
    assert client.open(path, method=method).status_code == 403
    unpaired = anonymous_client.open(path, method=method, headers={"Authorization": f"Bearer {unpaired_secret}"})
    assert unpaired.status_code == 403
    assert len(responses.calls) == 0


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
