"""How the broker knows who is asking: devices by their bearer secrets, browsers by the login proxy's headers."""

import functools
import hashlib
import hmac
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from flask import current_app, g, jsonify, redirect, request, url_for
from sqlalchemy import select

from .db import db
from .models import Device, DeviceUser

if TYPE_CHECKING:
    from flask import Response
    from flask.typing import ResponseReturnValue

# A Flask view function, as the decorators here take and return it.
type View = Callable[..., ResponseReturnValue]

# How stale a device's last-seen times may get before a request updates them, so a screen's polls aren't each a
# database write.
LAST_SEEN_RESOLUTION = timedelta(minutes=1)


def hash_secret(secret: str) -> str:
    """Return the stored form of a device secret.

    A plain unsalted SHA-256 is enough because secrets are long random strings, not passwords, and being
    deterministic lets a secret find its device.
    """
    return hashlib.sha256(secret.encode()).hexdigest()


def with_retry_after(response: Response, seconds: int) -> Response:
    """Set Retry-After on a response, so a view can return (response, status) on every path."""
    response.headers["Retry-After"] = str(seconds)
    return response


class DeviceAuthError(Exception):
    """A request that device auth turns away.

    `status` is 401, for a missing, malformed or unknown secret; `message` is the `error` it is answered with.
    """

    def __init__(self, status: int, message: str) -> None:
        """Turn the request away with `status`, answering `message`."""
        super().__init__(message)
        self.status = status
        self.message = message


def authenticate_device(authorization: str) -> Device:
    """Identify the device behind an `Authorization: Bearer <secret>` header, whose secret alone finds it.

    It is the screen's secret (devices.display_secret_hash); no match is a 401 (DeviceAuthError). Sets g.device_id,
    logs the request and records the device as seen. The prefix is case-sensitive: `bearer x` is refused.
    """
    secret = authorization.removeprefix("Bearer ")
    if not authorization.startswith("Bearer ") or not secret:
        raise DeviceAuthError(401, "unauthorized")

    device = _find_by_secret(secret)
    if device is None:
        raise DeviceAuthError(401, "unauthorized")

    g.device_id = device.device_id
    # The slug, not the friendly name (device_name): that's whatever its owner typed, which could name a place, and
    # nothing logged may.
    current_app.logger.info(
        "%s %s device=%s (%s)",
        request.method,
        request.path,
        device.assignment.slug if device.assignment else "unnamed",
        device.device_id,
    )
    _mark_seen(device)
    return device


def require_device_auth(view: View) -> View:
    """Protect a device-facing endpoint with the device's bearer secret (authenticate_device), passing device_id."""

    @functools.wraps(view)
    def wrapped(*args: object, **kwargs: object) -> ResponseReturnValue:
        try:
            device = authenticate_device(request.headers.get("Authorization", ""))
        except DeviceAuthError as error:
            return jsonify(error=error.message), error.status
        return view(*args, device_id=device.device_id, **kwargs)

    # For the tests that keep a device route off the browser login (test_caddyfile) and match it to the contract's
    # deviceSecret (test_api_contract).
    wrapped.device_auth = True  # ty: ignore[missing-attribute] -- a marker attribute, not part of the view type
    return wrapped


def _find_by_secret(secret: str) -> Device | None:
    # An indexed lookup rather than a constant-time compare: the timing can only reveal something
    # about the hash, and no one can work back from a hash to a secret that produces it.
    return db.session.scalar(select(Device).where(Device.display_secret_hash == hash_secret(secret)))


def _mark_seen(device: Device) -> None:
    now = datetime.now(UTC)
    stale = {"last_seen_at": device.last_seen_at, "display_last_seen_at": device.display_last_seen_at}
    changed = False
    for column, seen in stale.items():
        if seen is None or now - LAST_SEEN_RESOLUTION >= seen:
            setattr(device, column, now)
            changed = True
    if changed:
        db.session.commit()


def _require_bearer_token(config_key: str) -> Callable[[View], View]:
    """Protect an endpoint with the token in the broker's setting `config_key`.

    With none set the endpoint is off and answers 404, as if it didn't exist.
    """

    def decorator(view: View) -> View:
        @functools.wraps(view)
        def wrapped(*args: object, **kwargs: object) -> ResponseReturnValue:
            expected = current_app.config[config_key]
            if not expected:
                return jsonify(error="not found"), 404

            auth_header = request.headers.get("Authorization", "")
            secret = auth_header.removeprefix("Bearer ")
            if not auth_header.startswith("Bearer ") or not hmac.compare_digest(secret, expected):
                return jsonify(error="unauthorized"), 401
            return view(*args, **kwargs)

        return wrapped

    return decorator


# The read-only status endpoint's own token, which isn't a device secret.
require_status_token = _require_bearer_token("STATUS_TOKEN")


def current_user() -> str | None:
    """Return the signed-in user's id, from the header the login proxy sets.

    The broker trusts it because only the proxy can reach it (see caddy/Caddyfile).
    """
    return request.headers.get(current_app.config["USER_HEADER"], "").strip() or None


def signed_in_user() -> str:
    """Return the signed-in user's id, in a view only they reach (require_paired_user has turned anyone else away)."""
    user_id = current_user()
    if user_id is None:  # pragma: no cover -- the view's decorator has already answered 401
        msg = "no signed-in user"
        raise RuntimeError(msg)
    return user_id


def current_username() -> str | None:
    """Return the signed-in user's username, from the header the login proxy sets next to the user id.

    It's only for display: it's not the user's identity (that's current_user) and it can change.
    """
    return request.headers.get(current_app.config["USERNAME_HEADER"], "").strip() or None


def user_devices() -> list[Device]:
    """Return the devices the signed-in user is paired with, the one paired most recently first."""
    user = current_user()
    if user is None:
        return []
    return list(
        db.session.scalars(
            select(Device).where(Device.owner == user, Device.paired_at.is_not(None)).order_by(Device.paired_at.desc())
        )
    )


def paired_device_id() -> str | None:
    """Return the device the signed-in user is paired with (the one paired most recently, if several)."""
    devices = user_devices()
    return devices[0].device_id if devices else None


def screen_label(device: Device) -> str:
    """Return what a screen is called on the pages.

    That is its friendly name, else a slug its owner chose, else "Screen" and a piece of its id (a slug that is the
    device id is never shown).
    """
    if device.device_name:
        return device.device_name
    if device.assignment and device.assignment.slug != device.device_id:
        return device.assignment.slug
    return f"Screen {device.device_id[:6]}"


def _signed_in_user() -> str | ResponseReturnValue:
    """Return the signed-in user's id, or the 401 that turns away a request that didn't come through the login."""
    return current_user() or ("Sign in required.", 401)


def require_user(view: View) -> View:
    """Protect browser pages that only need someone signed in.

    Without the proxy's header the request didn't come through the login, so it's a 401.
    """

    @functools.wraps(view)
    def wrapped(*args: object, **kwargs: object) -> ResponseReturnValue:
        user = _signed_in_user()
        if not isinstance(user, str):
            return user
        return view(*args, **kwargs)

    return wrapped


def require_user_id(view: View) -> View:
    """Protect browser pages like require_user, and pass the signed-in user's id to the view as `user_id`."""

    @functools.wraps(view)
    def wrapped(*args: object, **kwargs: object) -> ResponseReturnValue:
        user = _signed_in_user()
        if not isinstance(user, str):
            return user
        return view(*args, user_id=user, **kwargs)

    return wrapped


def require_paired_user(view: View) -> View:
    """Protect browser pages that act on one of the signed-in user's devices.

    A page with a `device_id` in its URL is for that device, and 404s unless it's the user's (never admits it
    exists); one without is for the one paired most recently. A user with none goes to /pair. Sets g.device_id,
    and the view doesn't get the URL's device_id. Looked up on every request, so unpairing and forgetting take
    effect at once.
    """

    @functools.wraps(view)
    def wrapped(*args: object, **kwargs: object) -> ResponseReturnValue:
        if current_user() is None:
            return "Sign in required.", 401
        requested = kwargs.pop("device_id", None)
        owned = [device.device_id for device in user_devices()]
        if requested is None:
            if not owned:
                return redirect(url_for("pages.add_screen"))
            g.device_id = owned[0]
        elif requested in owned:
            g.device_id = requested
        else:
            return jsonify(error="not found"), 404
        return view(*args, **kwargs)

    return wrapped


def require_account_user(view: View) -> View:
    """Protect the account's pages, for someone with a screen of their own or one they joined as a member.

    A member has no screen to set up but has an account all the same: what they save to it for themselves (their
    Soloist API key) is what the touchscreen they joined asks for. Anyone with neither goes to /pair, as on the other
    pages.
    """

    @functools.wraps(view)
    def wrapped(*args: object, **kwargs: object) -> ResponseReturnValue:
        user = current_user()
        if user is None:
            return "Sign in required.", 401
        if not user_devices() and db.session.scalar(select(DeviceUser).where(DeviceUser.user_id == user)) is None:
            return redirect(url_for("pages.add_screen"))
        return view(*args, **kwargs)

    return wrapped


def require_device_or_owner(view: View) -> View:
    """Protect a route a device calls with its secret and the settings site with its owner's login.

    With an Authorization header it's the device's own call (authenticate_device), for itself. Without one it's the
    settings site's, by a signed-in person (401 otherwise) for the device of theirs that ?device_id= names: a 400
    without one, and a 404 for one that isn't theirs (never admitting it exists). The view gets `device_id`, and
    `by_device`, which says which it was.
    """

    @functools.wraps(view)
    def wrapped(*args: object, **kwargs: object) -> ResponseReturnValue:
        if "Authorization" in request.headers:
            try:
                device = authenticate_device(request.headers["Authorization"])
            except DeviceAuthError as error:
                return jsonify(error=error.message), error.status
            return view(*args, device_id=device.device_id, by_device=True, **kwargs)
        if current_user() is None:
            return jsonify(error="sign in, or send the device's secret"), 401
        requested = request.args.get("device_id", "")
        if not requested:
            return jsonify(error="device_id is needed"), 400
        if requested not in {device.device_id for device in user_devices()}:
            return jsonify(error="not found"), 404
        g.device_id = requested
        return view(*args, device_id=requested, by_device=False, **kwargs)

    # A device route, which the screens' API must serve (see require_device_auth).
    wrapped.device_auth = True  # ty: ignore[missing-attribute] -- a marker attribute, not part of the view type
    return wrapped


def require_paired_device_or_user(view: View) -> View:
    """Protect a route for a paired device (by its secret), or a person with a paired device (by the login).

    Either way it takes a screen someone has set up, as signing up is open to anyone: one with none is a 403.
    """

    @functools.wraps(view)
    def wrapped(*args: object, **kwargs: object) -> ResponseReturnValue:
        if "Authorization" in request.headers:
            try:
                device = authenticate_device(request.headers["Authorization"])
            except DeviceAuthError as error:
                return jsonify(error=error.message), error.status
            if device.paired_at is None:
                return jsonify(error="the device isn't paired yet"), 403
        elif current_user() is None:
            return jsonify(error="sign in, or send the device's secret"), 401
        elif not user_devices():
            return jsonify(error="pair a screen first"), 403
        return view(*args, **kwargs)

    wrapped.device_auth = True  # ty: ignore[missing-attribute] -- a marker attribute, not part of the view type
    return wrapped
