"""A device's registration.

An e-paper screen then polls for its frames (routes/frames.py), a touchscreen for its config (routes/config_api.py).
"""

from typing import TYPE_CHECKING, TypeGuard

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy.exc import IntegrityError

from broker.auth import hash_secret, with_retry_after
from broker.db import db
from broker.models import DEVICE_TYPES, FRAME_FORMATS, DeviceType, FrameFormat
from broker.services import registration

if TYPE_CHECKING:
    from flask.typing import ResponseReturnValue

bp = Blueprint("devices", __name__, url_prefix="/api/devices")

MIN_SECRET_LENGTH = 16


@bp.post("/register")
def register() -> ResponseReturnValue:
    """Give a device its identity.

    {"secret": ..., "type": ..., "width": ..., "height": ..., "format": ...} (the store contract's DeviceRegistration):
    the secret the device generated and keeps, what it is (one of DEVICE_TYPES; an e-paper screen if it doesn't say),
    its size in pixels and, for an e-paper screen only, the format it takes its frame in (one of FRAME_FORMATS). A
    touchscreen draws itself, so says no format. It becomes a device at once (201 {device_id}); only the secret's hash
    is stored, and identifies the device. A repeat, e.g. on every boot, is a 200, and changes nothing. A new device's
    weather location is guessed from its IP.

    Throttled per IP, every call counting, as anyone can call it.
    """
    throttle = current_app.extensions["register_throttle"]
    ip = request.remote_addr or "unknown"
    retry_after = throttle.retry_after(ip)
    if retry_after is not None:
        return with_retry_after(jsonify(error="too many registrations"), retry_after), 429
    # The call that reaches the limit still goes through; it's the next one that's refused.
    lockout = throttle.record_failure(ip)
    if lockout is not None:
        current_app.logger.warning("Too many registrations from %s; locked out for %ss", ip, lockout)

    body = request.get_json(silent=True) or {}
    secret = body.get("secret", "")
    if not isinstance(secret, str) or len(secret) < MIN_SECRET_LENGTH:
        return jsonify(error="secret must be a random string of at least 16 characters"), 400
    screen = _screen(body)
    if isinstance(screen, str):
        return jsonify(error=screen), 400

    secret_hash = hash_secret(secret)
    try:
        found = registration.register_screen(secret_hash, screen, ip=request.remote_addr)
    except IntegrityError:
        # The same secret registered by a simultaneous request that got in first: answer as for any repeat.
        db.session.rollback()
        found = registration.register_screen(secret_hash, screen, ip=request.remote_addr)

    if found is None:
        return with_retry_after(jsonify(error="too many devices waiting"), 60), 503
    return jsonify(device_id=found.device_id), 201 if found.created else 200


def _screen(body: dict) -> registration.Screen | str:
    """Return what the device says of itself (its type, size and frame format), or what's wrong with it."""
    device_type = body.get("type", "epaper")
    width, height, frame_format = body.get("width"), body.get("height"), body.get("format")
    if not _is_device_type(device_type):
        return f"type must be one of {', '.join(DEVICE_TYPES)}"
    if not (_is_size(width) and _is_size(height)):
        return "width and height must be positive integers"
    if device_type == "touchscreen":
        if "format" in body:
            return "a touchscreen draws itself, so takes no format"
        return registration.Screen(device_type, width, height)
    if not _is_frame_format(frame_format):
        return f"format must be one of {', '.join(FRAME_FORMATS)}"
    return registration.Screen(device_type, width, height, frame_format)


def _is_size(value: object) -> TypeGuard[int]:
    """Whether a reported width or height is a positive int (and not a bool, which is one to Python)."""
    return type(value) is int and value > 0


def _is_device_type(value: object) -> TypeGuard[DeviceType]:
    """Whether a device said it is a kind the broker knows."""
    return value in DEVICE_TYPES


def _is_frame_format(value: object) -> TypeGuard[FrameFormat]:
    """Whether a reported frame format is one the renderer can draw."""
    return value in FRAME_FORMATS
