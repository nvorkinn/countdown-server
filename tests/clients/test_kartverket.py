import logging
from unittest.mock import Mock, patch

import pytest
import requests

from broker.clients.kartverket import AddressLookupUnavailableError, search

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
