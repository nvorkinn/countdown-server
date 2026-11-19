"""Town and city search for the weather place pickers, the settings site's and a device's own, from Open-Meteo.

And a picker's current location: the town or city at a position (a touchscreen's browser's), or where the IP is.
"""

import math
from typing import TYPE_CHECKING, Any

from flask import Blueprint, jsonify, request

from broker.auth import require_paired_device_or_user
from broker.clients import weather
from broker.services import geoip, weather_place

if TYPE_CHECKING:
    from flask.typing import ResponseReturnValue

bp = Blueprint("place_search", __name__)

# Shorter queries match too much to be worth searching.
MIN_QUERY_LENGTH = 2
# How far Open-Meteo's place by a name can be from the position it was looked up for and still be that place: a
# GeoNames town is Open-Meteo's too, give or take its centre, while another place of the same name (London, Ontario)
# is far off.
SAME_PLACE_KM = 50
MAX_LATITUDE = 90
MAX_LONGITUDE = 180


@bp.get("/api/places/search")
@require_paired_device_or_user
def search_places() -> ResponseReturnValue:
    """Return the towns and cities matching ?q=, best first (the contract's PlaceSearchResult each).

    Each one's `value` is the weather section a save with it picked has; 502 with none when Open-Meteo can't be asked.
    """
    query = request.args.get("q", "").strip()
    if len(query) < MIN_QUERY_LENGTH:
        return jsonify([])
    try:
        places = weather.search_places(query)
    except weather.GeocodingUnavailableError:
        return jsonify([]), 502
    return jsonify([weather_place.search_result(place) for place in places])


@bp.get("/api/places/here")
@require_paired_device_or_user
def locate_place() -> ResponseReturnValue:
    """Return the town or city at ?latitude=&longitude=, or with neither where the caller's IP is: [] or one place.

    It's searchPlaces' answer for the town's name nearest the position (a PlaceSearchResult); 400 for only one of the
    two or one out of range, 502 with none when Open-Meteo can't be asked.
    """
    try:
        position = _position(request.args.get("latitude"), request.args.get("longitude"))
    except ValueError:
        return jsonify(error="send latitude and longitude in degrees, or neither"), 400
    located = geoip.place_near(*position) if position else geoip.locate_ip(request.remote_addr)
    if located is None:
        return jsonify([])
    try:
        places = weather.search_places(located.name)
    except weather.GeocodingUnavailableError:
        return jsonify([]), 502
    place = _same_place(places, located)
    return jsonify([weather_place.search_result(place)] if place else [])


def _position(latitude: str | None, longitude: str | None) -> tuple[float, float] | None:
    """Return the position sent, or None for none. Raises ValueError for half of one, or one that isn't a position."""
    if latitude is None and longitude is None:
        return None
    lat, lon = float(latitude or ""), float(longitude or "")
    if not (math.isfinite(lat) and math.isfinite(lon) and abs(lat) <= MAX_LATITUDE and abs(lon) <= MAX_LONGITUDE):
        msg = "not a position"
        raise ValueError(msg)
    return lat, lon


def _same_place(places: list[dict[str, Any]], located: geoip.Located) -> dict[str, Any] | None:
    """Return the one of Open-Meteo's places by the name that is the place located: the nearest, if near enough.

    With no position to go by (an IP the database names but doesn't place), the best match for the name.
    """
    if located.latitude is None or located.longitude is None:
        return places[0] if places else None
    lat, lon = located.latitude, located.longitude
    nearest = min(
        places,
        key=lambda place: geoip.distance_km(lat, lon, place["latitude"], place["longitude"]),
        default=None,
    )
    if nearest is None or geoip.distance_km(lat, lon, nearest["latitude"], nearest["longitude"]) > SAME_PLACE_KM:
        return None
    return nearest
