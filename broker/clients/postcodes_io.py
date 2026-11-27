"""Looks UK postcodes up with postcodes.io (keyless): the ones nearest a position, and where one postcode is.

A postcode, like a position, says where someone lives: neither is ever logged, nor is a request's URL, which has one.
"""

import logging
from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import requests

API_BASE = "https://api.postcodes.io"
# Asked while someone waits at a setup wizard, so a slow answer is worth less than a "try again".
TIMEOUT = 5
# postcodes.io's own bounds for a reverse lookup's radius, in metres.
MAX_RADIUS = 2000

logger = logging.getLogger(__name__)


class PostcodesUnavailableError(Exception):
    """postcodes.io couldn't be asked, or answered with something that isn't a postcode lookup's result."""


def _reason(error: Exception) -> str:
    """Say what went wrong without the error's own text, which quotes the URL and so the postcode or position."""
    if isinstance(error, requests.HTTPError) and error.response is not None:
        return f"{type(error).__name__} {error.response.status_code}"
    return type(error).__name__


def _placed(result: Any) -> bool:  # noqa: ANN401 -- whatever postcodes.io sent
    """Whether a result is a postcode with a position: some (the Channel Islands', the Isle of Man's) have none."""
    return (
        isinstance(result, dict)
        and isinstance(result.get("postcode"), str)
        and isinstance(result.get("latitude"), int | float)
        and isinstance(result.get("longitude"), int | float)
    )


def nearest(latitude: float, longitude: float, radius: int, limit: int) -> list[dict[str, Any]]:
    """Return up to `limit` postcodes within `radius` metres of the position, nearest first; [] for none.

    Each is postcodes.io's own result (postcode, latitude, longitude, distance in metres, country, ...). Raises
    PostcodesUnavailableError if postcodes.io couldn't be asked.
    """
    try:
        response = requests.get(
            f"{API_BASE}/postcodes",
            params={"lon": longitude, "lat": latitude, "limit": limit, "radius": min(radius, MAX_RADIUS)},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        # None near (the sea, another country) is a 200 with a null result.
        found = response.json()["result"] or []
        if not isinstance(found, list):
            raise TypeError  # noqa: TRY301 -- the same answer as any other that isn't a lookup's
        return [result for result in found if _placed(result) and isinstance(result.get("distance"), int | float)]
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        logger.warning("Couldn't ask postcodes.io for the postcodes near a position: %s", _reason(e))  # not where
        raise PostcodesUnavailableError from e


def lookup(postcode: str) -> dict[str, Any] | None:
    """Return postcodes.io's result for a UK postcode (postcode, latitude, longitude, country, region, ...).

    None if there's no such postcode, or it has no position. Raises PostcodesUnavailableError if postcodes.io couldn't
    be asked.
    """
    try:
        response = requests.get(f"{API_BASE}/postcodes/{quote(postcode, safe='')}", timeout=TIMEOUT)
        # Unknown, or terminated: a 404 with {"error": "Postcode not found"}.
        if response.status_code == HTTPStatus.NOT_FOUND:
            return None
        response.raise_for_status()
        result = response.json()["result"]
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        logger.warning("Couldn't look a postcode up with postcodes.io: %s", _reason(e))  # not which, nor the URL
        raise PostcodesUnavailableError from e
    return result if _placed(result) else None
