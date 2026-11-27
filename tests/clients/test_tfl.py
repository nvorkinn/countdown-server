from unittest.mock import Mock, patch

import pytest
import requests

from broker.clients.tfl import _get_stop_point, stops_near


def test_get_stop_point_asks_tfl_for_the_stop_id(app):
    http = Mock()
    http.get.return_value = Mock(status_code=200, json=Mock(return_value={"id": "940GZZLUKSX"}))

    with app.app_context():
        assert _get_stop_point("940GZZLUKSX", http) == {"id": "940GZZLUKSX"}

    assert http.get.call_args.args == ("https://api.tfl.gov.uk/StopPoint/940GZZLUKSX",)


@pytest.mark.parametrize("stop_id", ["../Line", "a/b", "a?b=c", "a#b", "a b", "", "%2e%2e"])
def test_get_stop_point_rejects_ids_that_could_change_the_url_path(app, stop_id):
    http = Mock()

    with app.app_context():
        assert _get_stop_point(stop_id, http) is None

    http.get.assert_not_called()


ELEPHANT_TUBE = {
    "naptanId": "940GZZLUEAC",
    "id": "940GZZLUEAC",
    "commonName": "Elephant & Castle Underground Station",
    "stopType": "NaptanMetroStation",
    "distance": 73.2,
    "lines": [{"name": "Bakerloo"}, {"name": "Northern"}],
    # Platforms and entrances: not a stop to pick, and with no distance of their own.
    "children": [{"id": "9400ZZLUEAC1", "stopType": "NaptanMetroPlatform", "lat": 0.0, "lon": 0.0}],
}
ELEPHANT_BUS = {
    "naptanId": "490000073P",
    "id": "490000073P",
    "commonName": "Elephant & Castle Station",
    "stopType": "NaptanPublicBusCoachTram",
    "stopLetter": "P",
    "distance": 43.4,
    "lines": [{"name": "133"}],
}


def test_stops_near_asks_tfl_for_tube_stations_and_bus_stops_around_the_position(app):
    response = Mock(status_code=200, json=Mock(return_value={"stopPoints": [ELEPHANT_TUBE, ELEPHANT_BUS]}))

    with app.app_context(), patch("broker.clients.tfl.requests.get", return_value=response) as get:
        found = stops_near(51.4948, -0.1001)

    assert get.call_args.args == ("https://api.tfl.gov.uk/StopPoint",)
    assert get.call_args.kwargs["params"] == {
        "lat": "51.4948",
        "lon": "-0.1001",
        "stopTypes": "NaptanMetroStation,NaptanPublicBusCoachTram",
        "radius": "1500",
        "modes": "tube,bus",
        "returnLines": "true",
        "app_key": "test-tfl-key",
    }
    assert [(stop["id"], stop["mode"], stop["distance"]) for stop in found] == [
        ("940GZZLUEAC", "tube", 73.2),
        ("490000073P", "bus", 43.4),
    ]
    assert found[0]["name"] == "Elephant & Castle"
    assert found[1]["letter"] == "P"


def test_stops_near_lists_a_stop_nested_under_several_once_at_its_nearest(app):
    hub = {"id": "HUB", "stopType": "TransportInterchange", "distance": 300.0, "children": [ELEPHANT_BUS]}
    far_hub = hub | {"id": "HUB2", "distance": 900.0}
    response = Mock(status_code=200, json=Mock(return_value={"stopPoints": [far_hub, hub]}))

    with app.app_context(), patch("broker.clients.tfl.requests.get", return_value=response):
        found = stops_near(51.4948, -0.1001)

    assert [(stop["id"], stop["distance"]) for stop in found] == [("490000073P", 300.0)]


@pytest.mark.parametrize(("latitude", "longitude"), [(59.91, 10.75), (48.85, 2.35), (51.5, 2.0)])
def test_stops_near_doesnt_ask_tfl_outside_the_uk_which_it_answers_with_a_400(app, latitude, longitude):
    with app.app_context(), patch("broker.clients.tfl.requests.get") as get:
        assert stops_near(latitude, longitude) == []

    get.assert_not_called()


def test_stops_near_raises_when_tfl_fails(app):
    response = Mock(status_code=500)
    response.raise_for_status.side_effect = requests.HTTPError("500", response=response)

    with (
        app.app_context(),
        patch("broker.clients.tfl.requests.get", return_value=response),
        pytest.raises(requests.HTTPError),
    ):
        stops_near(51.4948, -0.1001)
