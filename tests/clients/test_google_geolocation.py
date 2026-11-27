"""The Google Geolocation client: Google mocked (responses)."""

import json
import logging
from urllib.parse import parse_qsl, urlsplit

import pytest
import responses

from broker.clients import google_geolocation
from broker.clients.google_geolocation import GEOLOCATE_URL, GeolocationUnavailableError, Position, locate

ACCESS_POINTS = [
    {"mac_address": "aa:bb:cc:dd:ee:01", "signal_strength": -43, "channel": 11, "age": 120},
    {"mac_address": "aa:bb:cc:dd:ee:02", "signal_strength": -71},
]


def _query(index: int = 0) -> dict[str, str]:
    """Return the query of the call mocked at `index`."""
    return dict(parse_qsl(urlsplit(str(responses.calls[index].request.url)).query))


def _body(index: int = 0) -> dict:
    """Return the JSON body of the call mocked at `index`."""
    body = responses.calls[index].request.body
    assert isinstance(body, bytes | str)
    return json.loads(body)


@responses.activate
def test_locate_asks_google_with_the_key_and_only_the_fields_it_knows():
    responses.post(GEOLOCATE_URL, json={"location": {"lat": 51.4884, "lng": -0.0917}, "accuracy": 12.5})

    assert locate(ACCESS_POINTS, "the-key") == Position(51.4884, -0.0917, 12.5)

    assert _query() == {"key": "the-key"}
    assert _body(0) == {
        "considerIp": False,
        "wifiAccessPoints": [
            {"macAddress": "aa:bb:cc:dd:ee:01", "signalStrength": -43, "channel": 11, "age": 120},
            {"macAddress": "aa:bb:cc:dd:ee:02", "signalStrength": -71},
        ],
    }


@responses.activate
def test_locate_is_none_when_google_cant_place_the_access_points():
    responses.post(GEOLOCATE_URL, status=404, json={"error": {"code": 404, "message": "Not Found"}})

    assert locate(ACCESS_POINTS, "the-key") is None


@pytest.mark.parametrize(
    "reply",
    [
        {"status": 403, "json": {"error": {"code": 403, "message": "The provided API key is invalid."}}},
        {"status": 500, "body": "oops"},
        {"json": {"accuracy": 10}},
        {"json": {"location": {"lat": "north", "lng": 0}, "accuracy": 10}},
        {"body": "not json"},
        {"body": responses.ConnectionError("down")},
    ],
)
@responses.activate
def test_locate_raises_when_google_cant_be_asked_logging_neither_the_key_nor_where(reply, caplog):
    responses.post(GEOLOCATE_URL, **reply)

    with (
        caplog.at_level(logging.WARNING, logger=google_geolocation.__name__),
        pytest.raises(GeolocationUnavailableError),
    ):
        locate(ACCESS_POINTS, "the-secret-key")

    assert caplog.records
    assert "the-secret-key" not in caplog.text
    assert "aa:bb:cc" not in caplog.text
    assert "googleapis" not in caplog.text
