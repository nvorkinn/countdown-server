"""Places a device by the Wi-Fi access points it can see, with Google's Geolocation API and the broker's key.

The kiosk's setup wizard scans and sends the access points (routes/location.py), so the key never sits on a device. The
access points and the position they give say where someone lives, and the request's URL has the key in it: none of
them is ever logged.
"""

import logging
import math
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

import requests

GEOLOCATE_URL = "https://www.googleapis.com/geolocation/v1/geolocate"
# Asked while someone waits at the setup wizard, so a slow answer is worth less than a "try again".
TIMEOUT = 8

logger = logging.getLogger(__name__)


class GeolocationUnavailableError(Exception):
    """Google couldn't be asked, refused the key, or answered with something that isn't a position."""


@dataclass(frozen=True)
class Position:
    """Where Google puts the scan: degrees, and the radius in metres it is 68% sure of."""

    latitude: float
    longitude: float
    accuracy: float


def _reason(error: Exception) -> str:
    """Say what went wrong without the error's own text, which quotes the URL and so the key."""
    if isinstance(error, requests.HTTPError) and error.response is not None:
        return f"{type(error).__name__} {error.response.status_code}"
    return type(error).__name__


def _access_point(access_point: dict[str, Any]) -> dict[str, Any]:
    """Return an access point in Google's spelling, with only the fields Google knows (it refuses any other)."""
    google = {"macAddress": access_point["mac_address"], "signalStrength": access_point["signal_strength"]}
    if access_point.get("channel") is not None:
        google["channel"] = access_point["channel"]
    if access_point.get("age") is not None:
        google["age"] = access_point["age"]
    return google


def locate(access_points: list[dict[str, Any]], key: str) -> Position | None:
    """Return where Google puts these access points ({mac_address, signal_strength, channel?, age?} each).

    None if Google can't place them (its 404). Raises GeolocationUnavailableError if Google couldn't be asked, or
    refused (a bad key, its quota spent).
    """
    try:
        response = requests.post(
            GEOLOCATE_URL,
            params={"key": key},
            # Not the IP's position: that's the broker's, not the device's.
            json={"considerIp": False, "wifiAccessPoints": [_access_point(point) for point in access_points]},
            timeout=TIMEOUT,
        )
        if response.status_code == HTTPStatus.NOT_FOUND:
            return None
        response.raise_for_status()
        body = response.json()
        position = Position(float(body["location"]["lat"]), float(body["location"]["lng"]), float(body["accuracy"]))
        if not all(math.isfinite(value) for value in (position.latitude, position.longitude, position.accuracy)):
            raise ValueError  # noqa: TRY301 -- the same answer as any other that isn't a position
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        logger.warning("Couldn't locate a device with Google: %s", _reason(e))  # not where, nor the URL
        raise GeolocationUnavailableError from e
    return position
