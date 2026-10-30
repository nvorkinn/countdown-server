"""Parcel (the delivery-tracking app): an owner's active deliveries, read with their own premium API key.

What the parcel provider needs: the view-deliveries endpoint (https://parcelapp.net/help/api-view-deliveries.html)
and the carriers' names. The key travels only in the `api-key` header, never in a URL, and no error here carries it.
Parcel's limit is 20 requests an hour per key.
"""

from http import HTTPStatus
from typing import Any

import requests

BASE_URL = "https://api.parcel.app/external"
DELIVERIES_URL = f"{BASE_URL}/deliveries/"
CARRIERS_URL = f"{BASE_URL}/supported_carriers.json"
TIMEOUT = 10


class ParcelError(Exception):
    """Parcel couldn't be asked, or answered with something unexpected. The message never holds the key."""


class InvalidKeyError(ParcelError):
    """Parcel answered 401: the key isn't one it knows."""


class RateLimitedError(ParcelError):
    """Parcel answered 429: too many requests for the key. `retry_after_s` is what it asked for, if it did."""

    def __init__(self, retry_after_s: int | None) -> None:
        """Remember how long Parcel asked to be left alone, if it said."""
        super().__init__("Parcel is rate-limiting the key")
        self.retry_after_s = retry_after_s


def deliveries(api_key: str, *, session: requests.Session | None = None) -> list[dict[str, Any]]:
    """Return the owner's active deliveries as Parcel sent them.

    Raises InvalidKeyError on a 401, RateLimitedError on a 429, and ParcelError for any other failure.
    """
    try:
        response = (session or requests).get(
            DELIVERIES_URL, params={"filter_mode": "active"}, headers={"api-key": api_key}, timeout=TIMEOUT
        )
    except requests.RequestException as e:
        # The error's type only: a library error's text can quote the request.
        msg = f"Couldn't reach Parcel ({type(e).__name__})"
        raise ParcelError(msg) from None
    if response.status_code == HTTPStatus.UNAUTHORIZED:
        msg = "Parcel refused the key"
        raise InvalidKeyError(msg)
    if response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
        retry_after = response.headers.get("Retry-After", "")
        raise RateLimitedError(int(retry_after) if retry_after.isascii() and retry_after.isdigit() else None)
    if response.status_code != HTTPStatus.OK:
        msg = f"Parcel answered {response.status_code}"
        raise ParcelError(msg)
    try:
        body = response.json()
    except ValueError:
        msg = "Parcel's answer wasn't JSON"
        raise ParcelError(msg) from None
    if not isinstance(body, dict) or not body.get("success") or not isinstance(body.get("deliveries"), list):
        # error_message is Parcel's own text, not ours to log: it may be about the key.
        msg = "Parcel's answer wasn't a success"
        raise ParcelError(msg)
    return [d for d in body["deliveries"] if isinstance(d, dict)]


def carrier_names(*, session: requests.Session | None = None) -> dict[str, str]:
    """Return Parcel's carrier names by carrier code (the list needs no key, and changes daily)."""
    try:
        response = (session or requests).get(CARRIERS_URL, timeout=TIMEOUT)
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as e:
        msg = f"Couldn't read Parcel's carriers ({type(e).__name__})"
        raise ParcelError(msg) from None
    if not isinstance(body, dict):
        msg = "Parcel's carriers weren't an object"
        raise ParcelError(msg)
    return {code: c["name"] for code, c in body.items() if isinstance(c, dict) and isinstance(c.get("name"), str)}
