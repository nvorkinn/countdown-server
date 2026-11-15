"""Town and city search for the weather place pickers, the settings site's and a device's own, from Open-Meteo."""

from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request

from broker.auth import require_paired_device_or_user
from broker.clients import weather
from broker.services import weather_place

if TYPE_CHECKING:
    from flask.typing import ResponseReturnValue

bp = Blueprint("place_search", __name__)

# Shorter queries match too much to be worth searching.
MIN_QUERY_LENGTH = 2


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
