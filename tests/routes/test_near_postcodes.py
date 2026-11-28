"""Test POST /api/postcodes/near: the UK postcodes nearest a position a phone found for itself.

A setup's location step on its owner's phone sends the phone's own position. A position says where someone
lives: it comes in the body, and is never logged.
"""

import logging
from urllib.parse import parse_qsl, urlsplit

import pytest
import responses
from countdown_api import fixtures
from countdown_api.models import NearbyPostcode, NearPostcodesRequest

from broker.clients.postcodes_io import API_BASE as POSTCODES_IO

NEAR = "/api/postcodes/near"
EXAMPLE = fixtures.load("locate.json")
POSITION = EXAMPLE["near_postcodes_request"]


@pytest.fixture
def device(paired_client):
    """Return a client that sends a paired screen's secret."""
    client, _, secret = paired_client
    client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {secret}"
    return client


def _postcodes_io_finds(*postcodes):
    responses.get(f"{POSTCODES_IO}/postcodes", json={"status": 200, "result": list(postcodes) or None})


def _asked() -> dict[str, str]:
    return dict(parse_qsl(urlsplit(responses.calls[0].request.url).query))


@responses.activate
def test_it_needs_a_paired_screen_or_its_owner(client, anonymous_client, register_device):
    _, unpaired_secret = register_device("an-unpaired-screens-secret")

    assert anonymous_client.post(NEAR, json=POSITION).status_code == 401
    # Signed in, but with no screen of their own yet.
    assert client.post(NEAR, json=POSITION).status_code == 403
    unpaired = anonymous_client.post(NEAR, json=POSITION, headers={"Authorization": f"Bearer {unpaired_secret}"})
    assert unpaired.status_code == 403
    assert len(responses.calls) == 0


@responses.activate
def test_an_owner_asks_from_their_phone_with_their_login(paired_client):
    client, _, _ = paired_client
    _postcodes_io_finds()

    response = client.post(NEAR, json=POSITION)

    assert response.status_code == 200
    assert response.get_json() == []


@responses.activate
def test_it_answers_the_contracts_example_nearest_first(device):
    NearPostcodesRequest.model_validate(POSITION)
    found = [{**postcode, "country": "England"} for postcode in EXAMPLE["near_postcodes"]]
    _postcodes_io_finds(*found)

    response = device.post(NEAR, json=POSITION)

    assert response.status_code == 200
    assert response.get_json() == EXAMPLE["near_postcodes"]
    for postcode in response.get_json():
        NearbyPostcode.model_validate(postcode)
    assert _asked() == {
        "lon": str(POSITION["longitude"]),
        "lat": str(POSITION["latitude"]),
        "limit": "10",
        "radius": "200",
    }


@pytest.mark.parametrize(("accuracy", "radius"), [(None, "200"), (5, "200"), (640.4, "640"), (25000, "2000")])
@responses.activate
def test_it_looks_as_far_out_as_the_phone_is_unsure_within_bounds(device, accuracy, radius):
    _postcodes_io_finds()
    body = {"latitude": 51.5, "longitude": -0.1} | ({} if accuracy is None else {"accuracy": accuracy})

    response = device.post(NEAR, json=body)

    assert response.status_code == 200
    assert response.get_json() == []
    assert _asked()["radius"] == radius


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {},
        {"latitude": 51.5},
        {"latitude": "51.5", "longitude": -0.1},
        {"latitude": True, "longitude": -0.1},
        {"latitude": 91, "longitude": 0},
        {"latitude": 51.5, "longitude": 181},
        {"latitude": 51.5, "longitude": -0.1, "accuracy": -1},
        {"latitude": 51.5, "longitude": -0.1, "accuracy": "near"},
        {"latitude": 51.5, "longitude": -0.1, "wifi_access_points": []},
    ],
)
@responses.activate
def test_it_refuses_what_isnt_a_position(device, body):
    response = device.post(NEAR, json=body)

    assert response.status_code == 400
    assert response.get_json()["error"]
    assert len(responses.calls) == 0


@responses.activate
def test_it_is_a_502_when_postcodes_io_fails_logging_no_position(device, caplog):
    responses.get(f"{POSTCODES_IO}/postcodes", status=503)

    with caplog.at_level(logging.DEBUG):
        response = device.post(NEAR, json=POSITION)

    assert response.status_code == 502
    assert response.get_json()["error"]
    assert str(POSITION["latitude"]) not in caplog.text
