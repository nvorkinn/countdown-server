"""Where a kiosk is, for its setup's location step: its position and postcodes, a postcode's, its stops.

A kiosk's own wizard sends the Wi-Fi access points the kiosk sees, and the broker asks Google where they are with its
own key, so the key never sits on a device; postcodes.io lists the UK postcodes nearest. On its owner's phone the step
needs no Google: the phone says where it is itself, and is answered the postcodes nearest that (/api/postcodes/near).
The owner picks or types one (looked up here too), and then picks from the Tube stations and bus stops near it. Access
points, positions and postcodes say where someone lives: none of them is logged.
"""

import math
import re
from typing import TYPE_CHECKING, Any, TypeGuard

from flask import Blueprint, current_app, g, jsonify, request

from broker.auth import current_user, require_paired_device_or_user, with_retry_after
from broker.clients import google_geolocation, kartverket, postcodes_io
from broker.services import stops
from broker.services.postcodes import normalize_postcode, postcode_country

if TYPE_CHECKING:
    from flask.typing import ResponseReturnValue

bp = Blueprint("location", __name__)

MIN_ACCESS_POINTS = 2
MAX_ACCESS_POINTS = 30
_MAC_ADDRESS = re.compile(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}")
MIN_SIGNAL_STRENGTH = -120
MAX_CHANNEL = 233
# How many postcodes a location lists, and how far around its position postcodes.io looks: Google's accuracy, but
# never so tight that a good fix finds none nor so wide that a poor one lists another district's.
NEARBY_POSTCODES = 10
MIN_POSTCODE_RADIUS = 200
MAX_POSTCODE_RADIUS = 2000
MAX_LATITUDE = 90
MAX_LONGITUDE = 180
# The longest postcode the contract takes as typed: "SW1A  1AA" with a space or two to spare.
MAX_POSTCODE_LENGTH = 10


@bp.post("/api/devices/locate")
@require_paired_device_or_user
def locate_device() -> ResponseReturnValue:  # noqa: PLR0911 -- an answer per way it can end
    """Return where the Wi-Fi access points sent are, and the UK postcodes nearest (the contract's DeviceLocation).

    The body is a DeviceLocateRequest. 400 for one that isn't, 404 if Google can't place them, 429 (with Retry-After)
    past LOCATES_PER_HOUR a device, 502 if Google or postcodes.io couldn't be asked, 503 with no Google key set.
    """
    throttle = current_app.extensions["locate_throttle"]
    caller = g.get("device_id") or f"user:{current_user()}"
    retry_after = throttle.retry_after(caller)
    if retry_after is not None:
        return with_retry_after(jsonify(error="too many location lookups"), retry_after), 429

    access_points = _access_points(request.get_json(silent=True))
    if isinstance(access_points, str):
        return jsonify(error=access_points), 400
    key = current_app.config["GOOGLE_GEOLOCATION_API_KEY"]
    if not key:
        return jsonify(error="location lookups aren't set up on this server"), 503

    # The call that reaches the limit still goes through; it's the next one that's refused.
    throttle.record(caller)
    try:
        position = google_geolocation.locate(access_points, key)
    except google_geolocation.GeolocationUnavailableError:
        return jsonify(error="couldn't ask Google where the device is"), 502
    if position is None:
        return jsonify(error="Google can't place these access points"), 404

    try:
        postcodes = _postcodes_near(position.latitude, position.longitude, position.accuracy)
    except postcodes_io.PostcodesUnavailableError:
        return jsonify(error="couldn't ask postcodes.io for the postcodes nearby"), 502
    return jsonify(
        latitude=position.latitude,
        longitude=position.longitude,
        accuracy=position.accuracy,
        postcodes=postcodes,
    )


@bp.post("/api/postcodes/near")
@require_paired_device_or_user
def near_postcodes() -> ResponseReturnValue:
    """Return the UK postcodes nearest the position sent, nearest first (the contract's NearbyPostcode each).

    The body is a NearPostcodesRequest: a position, as a phone's browser finds its own, and how sure it is. None
    outside the UK. 400 for a body that isn't one, 502 if postcodes.io couldn't be asked.
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) - {"latitude", "longitude", "accuracy"}:
        body = {}
    position = _position(body.get("latitude"), body.get("longitude"))
    accuracy = body.get("accuracy", 0)
    if position is None or not _is_number(accuracy) or accuracy < 0:
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


def _is_number(value: Any) -> TypeGuard[float]:  # noqa: ANN401 -- whatever the JSON had
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


def _is_int(value: Any, low: int, high: int | None = None) -> bool:  # noqa: ANN401 -- whatever the JSON had
    """Whether a JSON value is a whole number (not a boolean) from low to high."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= low and (high is None or value <= high)


def _access_points(body: Any) -> list[dict[str, Any]] | str:  # noqa: ANN401, PLR0911 -- any JSON; a reason per fault
    """Return a DeviceLocateRequest's access points, each with only its known fields, or why it isn't one."""
    points = body.get("wifi_access_points") if isinstance(body, dict) else None
    if not isinstance(points, list) or not MIN_ACCESS_POINTS <= len(points) <= MAX_ACCESS_POINTS:
        return f"wifi_access_points must list {MIN_ACCESS_POINTS} to {MAX_ACCESS_POINTS} access points"
    access_points = []
    for point in points:
        if not isinstance(point, dict):
            return "each access point must be an object"
        mac_address = point.get("mac_address")
        if not isinstance(mac_address, str) or not _MAC_ADDRESS.fullmatch(mac_address):
            return "mac_address must be lowercase aa:bb:cc:dd:ee:ff"
        if not _is_int(point.get("signal_strength"), MIN_SIGNAL_STRENGTH, 0):
            return f"signal_strength must be whole dBm from {MIN_SIGNAL_STRENGTH} to 0"
        access_point = {"mac_address": mac_address, "signal_strength": point["signal_strength"]}
        if "channel" in point:
            if not _is_int(point["channel"], 1, MAX_CHANNEL):
                return f"channel must be from 1 to {MAX_CHANNEL}"
            access_point["channel"] = point["channel"]
        if "age" in point:
            if not _is_int(point["age"], 0):
                return "age must be whole milliseconds, 0 or more"
            access_point["age"] = point["age"]
        access_points.append(access_point)
    return access_points
