from unittest.mock import patch

import pytest
from countdown_api.models import NoticeBoardAddress

from broker.models import DeviceConfig
from broker.services import household_address
from broker.services.household_address import AddressNotFoundError, lookup, member, posted, stored, title_case

EUFEMIAS = {"adressenavn": "Dronning Eufemias gate", "nummer": 1, "bokstav": "", "poststed": "OSLO"}
SAVED = NoticeBoardAddress(street="Dronning Eufemias gate", house_number="1", city="Oslo")


def _config(postcode="0150", address=SAVED):
    config = DeviceConfig(postcode=postcode)
    household_address.store(config, address)
    return config


def _kartverket(*found):
    return patch("broker.clients.kartverket.search", return_value=list(found))


def test_lookup_takes_the_one_match_as_kartverket_spells_it():
    with _kartverket(EUFEMIAS) as search:
        assert lookup("dronning eufemias gate 1", "0150") == SAVED

    search.assert_called_once_with("dronning eufemias gate 1", "0150")


def test_the_house_number_has_its_letter():
    with _kartverket({"adressenavn": "Markveien", "nummer": 35, "bokstav": "B", "poststed": "OSLO"}):
        assert lookup("Markveien 35B", "0554") == NoticeBoardAddress(
            street="Markveien", house_number="35B", city="Oslo"
        )


@pytest.mark.parametrize(("found", "several"), [((), False), ((EUFEMIAS, {**EUFEMIAS, "nummer": 11}), True)])
def test_none_or_several_matches_are_not_found(found, several):
    with _kartverket(*found), pytest.raises(AddressNotFoundError) as raised:
        lookup("Dronning Eufemias gate", "0150")

    assert raised.value.several is several


def test_an_address_without_a_street_is_not_found():
    farm = {"adressenavn": None, "nummer": None, "bokstav": "", "poststed": "VÅGÅ"}
    with _kartverket(farm), pytest.raises(AddressNotFoundError):
        lookup("207/453", "2680")


@pytest.mark.parametrize(
    ("town", "expected"),
    [("OSLO", "Oslo"), ("MO I RANA", "Mo i Rana"), ("KRISTIANSAND S", "Kristiansand S"), ("ÅLESUND", "Ålesund")],
)
def test_title_case(town, expected):
    assert title_case(town) == expected


def test_only_a_norwegian_notice_board_has_an_address():
    assert stored(_config()) == SAVED
    assert stored(_config(postcode="SW1A 1AA")) is None
    assert stored(_config(address=None)) is None


def test_the_member_is_street_house_number_postcode_and_city():
    assert member(_config()) == "Dronning Eufemias gate|1|0150|Oslo"
    assert member(_config(address=None)) is None


def test_the_saved_address_is_kept_unasked_while_the_postcode_is():
    with _kartverket() as search:
        assert posted(_config(), "0150", None) == SAVED
        assert posted(_config(), "0150", " Dronning  Eufemias gate 1 ") == SAVED

    search.assert_not_called()


def test_a_new_postcode_clears_the_address_unless_a_new_one_comes_with_it():
    with _kartverket(EUFEMIAS) as search:
        assert posted(_config(postcode="0151"), "0150", None) is None
        assert posted(_config(postcode="0151"), "0150", "Dronning Eufemias gate 1") is None
        search.assert_not_called()
        assert posted(_config(postcode="0151", address=None), "0150", "Dronning Eufemias gate 1") == SAVED


def test_a_postcode_that_isnt_norwegian_clears_the_address():
    with _kartverket(EUFEMIAS) as search:
        assert posted(_config(), "SW1A 1AA", "Dronning Eufemias gate 1") is None

    search.assert_not_called()


def test_an_empty_address_clears_it():
    assert posted(_config(), "0150", "  ") is None
