from unittest.mock import Mock, patch

import pytest
import requests

from broker.services import geoip
from broker.services.geoip import GeoData, Place


def _response(json_data):
    response = Mock()
    response.status_code = 200
    response.json.return_value = json_data
    response.raise_for_status = Mock()
    return response


LONDONS = {
    "results": [
        {
            "id": 2643743,
            "name": "London",
            "latitude": 51.50853,
            "longitude": -0.12574,
            "country_code": "GB",
            "admin1": "England",
            "country": "United Kingdom",
        },
        {
            "id": 6058560,
            "name": "London",
            "latitude": 42.98339,
            "longitude": -81.23304,
            "country_code": "CA",
            "admin1": "Ontario",
            "country": "Canada",
        },
    ]
}


def test_place_search_requires_a_paired_screen(client, anonymous_client):
    with patch("broker.clients.weather.requests.get") as get:
        assert client.get("/api/places/search?q=london").status_code == 403
        assert anonymous_client.get("/api/places/search?q=london").status_code == 401

    get.assert_not_called()


def test_place_search_returns_open_meteos_matches_best_first(paired_client):
    client, _, _ = paired_client
    with patch("broker.clients.weather.requests.get", return_value=_response(LONDONS)) as get:
        response = client.get("/api/places/search?q=%20London%20")

    assert response.status_code == 200
    assert get.call_args.kwargs["params"]["name"] == "London"
    assert response.get_json() == [
        {
            "id": "2643743",
            "name": "London",
            "subtitle": "England, United Kingdom",
            "indent": False,
            # What picking it saves: the whole weather section, as the config answers it with this place.
            "value": {"location": "London", "key": "london-gb-2643743"},
            "label": "London, England, United Kingdom",
            "country_code": "GB",
            "latitude": 51.50853,
            "longitude": -0.12574,
        },
        {
            "id": "6058560",
            "name": "London",
            "subtitle": "Ontario, Canada",
            "indent": False,
            "value": {"location": "London", "key": "london-ca-6058560"},
            "label": "London, Ontario, Canada",
            "country_code": "CA",
            "latitude": 42.98339,
            "longitude": -81.23304,
        },
    ]


def test_place_search_ignores_a_query_too_short_to_search(paired_client):
    client, _, _ = paired_client
    with patch("broker.clients.weather.requests.get") as get:
        for query in ("", "l", " l "):
            response = client.get(f"/api/places/search?q={query}")
            assert (response.status_code, response.get_json()) == (200, [])
    get.assert_not_called()


def test_place_search_is_a_502_with_no_places_when_open_meteo_is_down(paired_client):
    client, _, _ = paired_client
    with patch("broker.clients.weather.requests.get", side_effect=requests.Timeout):
        response = client.get("/api/places/search?q=london")

    assert (response.status_code, response.get_json()) == (502, [])


class FakeReader:
    """Stands in for DB-IP's database: {ip: record}."""

    def __init__(self, records):
        self.records = records

    def get(self, ip):
        return self.records.get(ip)


@pytest.fixture
def geodata(monkeypatch):
    records = {}
    places = [Place("London", 51.50853, -0.12574, 8961989), Place("Reading", 51.45625, -0.97113, 318014)]
    monkeypatch.setattr(geoip, "_load", lambda _directory: GeoData(FakeReader(records), places))  # ty: ignore[invalid-argument-type] -- a stand-in Reader
    return records


def test_current_location_requires_a_paired_screen(client, anonymous_client, geodata):
    with patch("broker.clients.weather.requests.get") as get:
        assert client.get("/api/places/here").status_code == 403
        assert anonymous_client.get("/api/places/here").status_code == 401

    get.assert_not_called()


def test_current_location_is_the_place_at_the_position_sent(paired_client, geodata):
    client, _, _ = paired_client
    with patch("broker.clients.weather.requests.get", return_value=_response(LONDONS)) as get:
        response = client.get("/api/places/here?latitude=51.47&longitude=-0.11")

    assert response.status_code == 200
    assert get.call_args.kwargs["params"]["name"] == "London"
    # Of Open-Meteo's Londons, the one there, not Ontario's.
    assert [(place["id"], place["value"]) for place in response.get_json()] == [
        ("2643743", {"location": "London", "key": "london-gb-2643743"})
    ]


def test_current_location_without_a_position_is_where_the_ip_is(paired_client, geodata):
    client, _, _ = paired_client
    geodata["81.2.69.1"] = {"location": {"latitude": 42.98, "longitude": -81.23}, "city": {"names": {"en": "London"}}}
    with patch("broker.clients.weather.requests.get", return_value=_response(LONDONS)):
        response = client.get("/api/places/here", environ_base={"REMOTE_ADDR": "81.2.69.1"})

    assert response.status_code == 200
    assert [place["id"] for place in response.get_json()] == ["6058560"]


def test_current_location_is_none_when_there_is_no_telling(paired_client, geodata):
    client, _, _ = paired_client
    with patch("broker.clients.weather.requests.get") as get:
        response = client.get("/api/places/here", environ_base={"REMOTE_ADDR": "10.0.0.1"})

    assert (response.status_code, response.get_json()) == (200, [])
    get.assert_not_called()


def test_current_location_is_none_when_open_meteos_place_by_the_name_is_elsewhere(paired_client, geodata):
    client, _, _ = paired_client
    reading_elsewhere = {"results": [{**LONDONS["results"][1], "name": "Reading"}]}
    with patch("broker.clients.weather.requests.get", return_value=_response(reading_elsewhere)):
        response = client.get("/api/places/here?latitude=51.45&longitude=-0.97")

    assert (response.status_code, response.get_json()) == (200, [])


@pytest.mark.parametrize(
    "query", ["latitude=51.5", "longitude=-0.1", "latitude=north&longitude=-0.1", "latitude=91&longitude=0"]
)
def test_current_location_is_a_400_for_a_position_that_isnt_one(paired_client, geodata, query):
    client, _, _ = paired_client
    with patch("broker.clients.weather.requests.get") as get:
        assert client.get(f"/api/places/here?{query}").status_code == 400
    get.assert_not_called()


def test_current_location_is_a_502_with_no_places_when_open_meteo_is_down(paired_client, geodata):
    client, _, _ = paired_client
    with patch("broker.clients.weather.requests.get", side_effect=requests.Timeout):
        response = client.get("/api/places/here?latitude=51.5&longitude=-0.1")

    assert (response.status_code, response.get_json()) == (502, [])
