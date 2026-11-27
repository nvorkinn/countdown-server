"""The postcodes.io client: postcodes.io mocked (responses)."""

import logging
from urllib.parse import parse_qsl, urlsplit

import pytest
import responses

from broker.clients import postcodes_io
from broker.clients.postcodes_io import API_BASE, PostcodesUnavailableError, lookup, nearest

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


@responses.activate
def test_nearest_asks_for_the_postcodes_around_the_position():
    responses.get(f"{API_BASE}/postcodes", json={"status": 200, "result": [SE17_2PU | {"distance": 23.3}]})

    assert nearest(51.4884, -0.0917, 500, 10) == [SE17_2PU | {"distance": 23.3}]

    assert _query(0) == {"lon": "-0.0917", "lat": "51.4884", "limit": "10", "radius": "500"}


@responses.activate
def test_nearest_never_asks_wider_than_postcodes_io_allows():
    responses.get(f"{API_BASE}/postcodes", json={"status": 200, "result": None})

    assert nearest(51.4884, -0.0917, 5000, 10) == []

    assert _query(0)["radius"] == "2000"


@responses.activate
def test_nearest_skips_postcodes_without_a_position():
    unplaced = SE17_2PU | {"postcode": "JE2 4WD", "latitude": None, "longitude": None, "distance": 3.0}
    responses.get(f"{API_BASE}/postcodes", json={"status": 200, "result": [unplaced, SE17_2PU | {"distance": 9.0}]})

    assert [found["postcode"] for found in nearest(51.4884, -0.0917, 500, 10)] == ["SE17 2PU"]


@responses.activate
def test_lookup_returns_postcodes_ios_result():
    responses.get(f"{API_BASE}/postcodes/SE17%202PU", json={"status": 200, "result": SE17_2PU})

    assert lookup("SE17 2PU") == SE17_2PU


@pytest.mark.parametrize(
    "reply",
    [
        {"status": 404, "json": {"status": 404, "error": "Postcode not found"}},
        {"json": {"status": 200, "result": SE17_2PU | {"latitude": None, "longitude": None}}},
    ],
)
@responses.activate
def test_lookup_is_none_for_an_unknown_or_unplaced_postcode(reply):
    responses.get(f"{API_BASE}/postcodes/SE17%202PU", **reply)

    assert lookup("SE17 2PU") is None


@pytest.mark.parametrize(
    "reply",
    [
        {"status": 500, "body": "oops"},
        {"status": 429, "body": "slow down"},
        {"json": {"status": 200}},
        {"body": "not json"},
        {"body": responses.ConnectionError("down")},
    ],
)
@responses.activate
def test_both_raise_when_postcodes_io_cant_be_asked_logging_neither_postcode_nor_position(reply, caplog):
    responses.get(f"{API_BASE}/postcodes", **reply)
    responses.get(f"{API_BASE}/postcodes/SE17%202PU", **reply)

    with caplog.at_level(logging.WARNING, logger=postcodes_io.__name__):
        with pytest.raises(PostcodesUnavailableError):
            nearest(51.4884, -0.0917, 500, 10)
        with pytest.raises(PostcodesUnavailableError):
            lookup("SE17 2PU")

    assert len(caplog.records) == 2
    assert "SE17" not in caplog.text
    assert "51.48" not in caplog.text
    assert "postcodes.io/" not in caplog.text


@pytest.mark.parametrize("postcode", ["se17 2pu", "SE172PU", "../health", "SE17 2PU?x=1", "SE17 2PU/../x", ""])
@responses.activate
def test_lookup_asks_nothing_for_what_isnt_a_canonical_postcode(postcode):
    assert lookup(postcode) is None
    assert len(responses.calls) == 0
