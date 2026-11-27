import logging
from unittest.mock import Mock, patch

import pytest
import requests

from broker.clients.kartverket import AddressLookupUnavailableError, postcode_position, search

EUFEMIAS = {"adressenavn": "Dronning Eufemias gate", "nummer": 1, "bokstav": "", "poststed": "OSLO"}


def _response(json_data, status_code=200):
    response = Mock()
    response.status_code = status_code
    response.json.return_value = json_data
    response.raise_for_status = Mock(
        side_effect=requests.HTTPError(str(status_code), response=response) if status_code >= 400 else None
    )
    return response


def test_search_asks_kartverket_in_the_postcode_for_enough_to_tell_one_from_several():
    with patch("broker.clients.kartverket.requests.get", return_value=_response({"adresser": [EUFEMIAS]})) as get:
        found = search("Dronning Eufemias gate 1", "0150")

    get.assert_called_once_with(
        "https://ws.geonorge.no/adresser/v1/sok",
        params={"sok": "Dronning Eufemias gate 1", "postnummer": "0150", "treffPerSide": 2},
        timeout=4,
    )
    assert found == [EUFEMIAS]


@pytest.mark.parametrize(
    "outcome",
    [
        requests.ConnectionError("https://ws.geonorge.no/adresser/v1/sok?sok=Thorvald+Meyers+gate+10"),
        _response({}, status_code=503),
        _response({"adresser": None}),
        _response({"adresser": ["Thorvald Meyers gate 10"]}),
    ],
)
def test_an_unanswered_search_is_unavailable_and_logs_no_address(outcome, caplog):
    effect = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
    with (
        caplog.at_level(logging.WARNING),
        patch("broker.clients.kartverket.requests.get", **effect),
        pytest.raises(AddressLookupUnavailableError),
    ):
        search("Thorvald Meyers gate 10", "0555")

    assert "Couldn't search Kartverket" in caplog.text
    assert "Thorvald" not in caplog.text
    assert "0555" not in caplog.text


def test_postcode_position_is_the_postcodes_first_address_point():
    found = {
        "adresser": [
            {"kommunenavn": "NORDRE FOLLO", "representasjonspunkt": {"epsg": "EPSG:4258", "lat": 59.72, "lon": 10.83}}
        ]
    }
    with patch("broker.clients.kartverket.requests.get", return_value=_response(found)) as get:
        assert postcode_position("1410") == {"latitude": 59.72, "longitude": 10.83, "municipality": "Nordre Follo"}

    assert get.call_args.kwargs["params"] == {"postnummer": "1410", "treffPerSide": 1}


@pytest.mark.parametrize("found", [[], [{"kommunenavn": "OSLO"}], [{"representasjonspunkt": {"lat": None}}]])
def test_postcode_position_is_none_without_an_address_with_a_point(found):
    with patch("broker.clients.kartverket.requests.get", return_value=_response({"adresser": found})):
        assert postcode_position("0150") is None


@pytest.mark.parametrize(
    "effect",
    [
        {"side_effect": requests.ConnectionError("https://ws.geonorge.no/adresser/v1/sok?postnummer=0150")},
        {"return_value": _response({"adresser": "none"})},
        {"return_value": _response({})},
    ],
)
def test_an_unanswered_postcode_position_is_unavailable_and_logs_no_postcode(effect, caplog):
    with (
        caplog.at_level(logging.WARNING),
        patch("broker.clients.kartverket.requests.get", **effect),
        pytest.raises(AddressLookupUnavailableError),
    ):
        postcode_position("0150")

    assert "0150" not in caplog.text
