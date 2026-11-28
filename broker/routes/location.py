"""Where a kiosk is, for its setup's location step: the postcodes near a position, a postcode's place, its stops.

The step runs on the owner's phone: the phone says where it is itself, and is answered the UK postcodes nearest that
(/api/postcodes/near, from postcodes.io). The owner picks or types one (looked up here too), and then picks from the
Tube stations and bus stops near it. Positions and postcodes say where someone lives: none of them is logged.
"""

import math
from typing import TYPE_CHECKING, Any

from flask import Blueprint, current_app, jsonify, request

from broker.auth import require_paired_device_or_user
from broker.clients import kartverket, postcodes_io
from broker.services import stops
from broker.services.postcodes import normalize_postcode, postcode_country

if TYPE_CHECKING:
    from flask.typing import ResponseReturnValue

bp = Blueprint("location", __name__)

# How many postcodes a location lists, and how far around its position postcodes.io looks: the position's accuracy,
# but never so tight that a good fix finds none nor so wide that a poor one lists another district's.
NEARBY_POSTCODES = 10
MIN_POSTCODE_RADIUS = 200
MAX_POSTCODE_RADIUS = 2000
MAX_LATITUDE = 90
MAX_LONGITUDE = 180
# The longest postcode the contract takes as typed: "SW1A  1AA" with a space or two to spare.
MAX_POSTCODE_LENGTH = 10


@bp.post("/api/postcodes/near")
@require_paired_device_or_user
def near_postcodes() -> ResponseReturnValue:
    """Return the UK postcodes nearest the position sent, nearest first (the contract's NearbyPostcode each).

    The body is a NearPostcodesRequest: a position, as a phone's browser finds its own, and how sure it is. None
    outside the UK. 400 for a body that isn't one, 502 if postcodes.io couldn't be asked.
    """
    body = request.get_json(silent=True)
    position = _position(body.get("latitude"), body.get("longitude")) if isinstance(body, dict) else None
    accuracy = body.get("accuracy", 0) if isinstance(body, dict) else None
    if (
        position is None
        or not isinstance(body, dict)
        or set(body) - {"latitude", "longitude", "accuracy"}
        or not _is_number(accuracy)
        or accuracy < 0
    ):
        return jsonify(error="send latitude and longitude in degrees, and accuracy in metres if known"), 400
    try:
        return jsonify(_postcodes_near(*position, accuracy))
    except postcodes_io.PostcodesUnavailableError:
        return jsonify(error="couldn't ask postcodes.io for the postcodes nearby"), 502


def _postcodes_near(latitude: float, longitude: float, accuracy: float) -> list[dict[str, Any]]:
    """Return the UK postcodes nearest a position as the contract's NearbyPostcode, nearest first.

    postcodes.io looks as far around the position as it is uncertain, within MIN_ and MAX_POSTCODE_RADIUS. Raises
    postcodes_io.PostcodesUnavailableError if it couldn't be asked.
    """
    radius = round(min(max(accuracy, MIN_POSTCODE_RADIUS), MAX_POSTCODE_RADIUS))
    nearby = postcodes_io.nearest(latitude, longitude, radius, NEARBY_POSTCODES)
    return [
        {
            "postcode": found["postcode"],
            "latitude": found["latitude"],
            "longitude": found["longitude"],
            "distance": round(found["distance"]),
            "country": "GB",
        }
        for found in sorted(nearby, key=lambda found: found["distance"])[:NEARBY_POSTCODES]
    ]


@bp.get("/api/postcodes/lookup")
@require_paired_device_or_user
def lookup_postcode() -> ResponseReturnValue:  # noqa: PLR0911 -- an answer per way it can end
    """Return where the UK or Norwegian ?postcode= is (the contract's Postcode).

    400 for one that's neither, 404 for one that doesn't exist (or has no position), 502 if its source couldn't be
    asked.
    """
    typed = request.args.get("postcode", "")
    postcode = normalize_postcode(typed) if len(typed) <= MAX_POSTCODE_LENGTH else None
    if not postcode:
        return jsonify(error="send a UK or Norwegian postcode"), 400
    if postcode_country(postcode) == "NO":
        try:
            found = kartverket.postcode_position(postcode)
        except kartverket.AddressLookupUnavailableError:
            return jsonify(error="couldn't ask Kartverket"), 502
        if found is None:
            return jsonify(error="no such postcode"), 404
        return jsonify(
            postcode=postcode,
            latitude=found["latitude"],
            longitude=found["longitude"],
            country="Norway",
            region=None,
            admin_district=found["municipality"],
        )
    try:
        found = postcodes_io.lookup(postcode)
    except postcodes_io.PostcodesUnavailableError:
        return jsonify(error="couldn't ask postcodes.io"), 502
    if found is None:
        return jsonify(error="no such postcode"), 404
    return jsonify(
        postcode=found["postcode"],
        latitude=found["latitude"],
        longitude=found["longitude"],
        country=found.get("country") or "",
        region=found.get("region"),
        admin_district=found.get("admin_district"),
    )


@bp.get("/api/stops/near")
@require_paired_device_or_user
def stops_near() -> ResponseReturnValue:
    """Return the Tube stations and bus stops near ?latitude=&longitude=, nearest first (NearbyStopResult each).

    [] outside TfL's area; 400 for a position that isn't one, 502 with none if TfL couldn't be asked.
    """
    try:
        position = _position(float(request.args.get("latitude", "")), float(request.args.get("longitude", "")))
    except ValueError:
        position = None
    if position is None:
        return jsonify(error="send latitude and longitude in degrees"), 400
    try:
        return jsonify(stops.near(*position))
    except Exception as e:  # noqa: BLE001 -- any failure is TfL's, and answered the same
        # Not the error's text, which quotes the URL: the position, and the TfL key.
        current_app.logger.warning("Couldn't list the stops near a position: %s", _reason(e))
        return jsonify([]), 502


def _reason(error: Exception) -> str:
    """Say what went wrong without the error's own text: its type, and an HTTP error's status ("HTTPError 503")."""
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    return f"{type(error).__name__} {status}" if status is not None else type(error).__name__


def _is_number(value: Any) -> bool:  # noqa: ANN401 -- whatever the JSON had
    """Whether a JSON value is a finite number (not a boolean)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _position(latitude: Any, longitude: Any) -> tuple[float, float] | None:  # noqa: ANN401 -- whatever was sent
    """Return a latitude and longitude in degrees as a position, or None if they aren't one."""
    if not (
        _is_number(latitude)
        and _is_number(longitude)
        and abs(latitude) <= MAX_LATITUDE
        and abs(longitude) <= MAX_LONGITUDE
    ):
        return None
    return float(latitude), float(longitude)
