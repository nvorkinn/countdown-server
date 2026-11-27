"""Looks a Norwegian address up in Kartverket's address register (Geonorge's address API, keyless).

The settings site checks a notice board's household address with it before saving it (services/household_address.py).
An address says where someone lives: it is never logged, nor is a request's URL, which has it in its query.
"""

import logging
from typing import Any

import requests

ADDRESS_SEARCH_URL = "https://ws.geonorge.no/adresser/v1/sok"
# Asked as a setting is saved, so a slow answer is worth less than a "try again".
TIMEOUT = 4
# Enough to tell one match from several.
HITS = 2

logger = logging.getLogger(__name__)


class AddressLookupUnavailableError(Exception):
    """Kartverket couldn't be asked, or answered with something that isn't an address search's result."""


def _reason(error: Exception) -> str:
    """Say what went wrong without the error's own text, which quotes the URL and so the address."""
    if isinstance(error, requests.HTTPError) and error.response is not None:
        return f"{type(error).__name__} {error.response.status_code}"
    return type(error).__name__


def search(text: str, postcode: str) -> list[dict[str, Any]]:
    """Return the addresses Kartverket finds for `text` in the Norwegian `postcode`: at most HITS of them.

    Each is Kartverket's own (adressenavn, nummer, bokstav, poststed, postnummer, ...), so several are returned
    whenever several match. Raises AddressLookupUnavailableError if Kartverket couldn't be asked.
    """
    try:
        response = requests.get(
            ADDRESS_SEARCH_URL,
            params={"sok": text, "postnummer": postcode, "treffPerSide": HITS},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        found = response.json()["adresser"]
        if not isinstance(found, list) or not all(isinstance(address, dict) for address in found):
            raise TypeError  # noqa: TRY301 -- the same answer as any other that isn't a search's
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        logger.warning("Couldn't search Kartverket for an address: %s", _reason(e))  # not which, nor the URL
        raise AddressLookupUnavailableError from e
    return found


def postcode_position(postcode: str) -> dict[str, Any] | None:
    """Return where a Norwegian postcode is, from its first address: {latitude, longitude, municipality}.

    None if Kartverket has no address in it (or none with a position). Raises AddressLookupUnavailableError if
    Kartverket couldn't be asked.
    """
    try:
        response = requests.get(ADDRESS_SEARCH_URL, params={"postnummer": postcode, "treffPerSide": 1}, timeout=TIMEOUT)
        response.raise_for_status()
        found = response.json()["adresser"]
        if not isinstance(found, list):
            raise TypeError  # noqa: TRY301 -- the same answer as any other that isn't a search's
        if not found:
            return None
        address = found[0]
        point = address.get("representasjonspunkt") or {}
        latitude, longitude = point.get("lat"), point.get("lon")
    except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError) as e:
        logger.warning("Couldn't search Kartverket for a postcode: %s", _reason(e))  # not which, nor the URL
        raise AddressLookupUnavailableError from e
    if not isinstance(latitude, int | float) or not isinstance(longitude, int | float):
        return None
    # Kartverket writes names in capitals ("NORDRE FOLLO").
    municipality = address.get("kommunenavn")
    return {
        "latitude": float(latitude),
        "longitude": float(longitude),
        "municipality": municipality.title() if isinstance(municipality, str) and municipality else None,
    }
