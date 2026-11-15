"""A touchscreen registering through the login: one visit registers it and makes the person signed in on it its owner.

The tablet opens APP_HOST/register?secret_hash=...&width=...&height=... . The settings site is behind the login
(Tinyauth and Pocket ID, in Caddy), so by the time the request gets here the person has signed in, or signed up and
added a passkey, and the login proxy's headers name them (auth.current_user). The same request then registers the
device and claims it for them, in one transaction (registration.register_and_claim): no code, no second step.

The tablet can pass `return_to`, a URL of its own app to come back to: once registered, the page redirects there,
adding `device_id` (or `error=taken` when someone else owns the touchscreen). It must be on one of the origins the
setting TOUCHSCREEN_RETURN_ORIGINS allows, so the route can't send anyone on to a site of an attacker's choosing; any
other is refused before anything is registered.

The tablet generates its secret and keeps it, as every device does, and sends only its hash (auth.hash_secret, the
hex SHA-256), so the secret is never in a URL, a log or a browser's history; the hash is what's stored anyway, and
doesn't authenticate anything. The tablet then calls the device API on API_HOST with its secret (GET
/api/devices/config and the rest), paired from its first call.

A visit that registers on its own is only taken from the tablet itself or the login's redirects back to it: a link
on another site could otherwise claim a device whose secret that site knows into the account of anyone signed in
who follows it, and that device could read their config and their Spotify token. So a visit the browser says came from
another site (Sec-Fetch-Site), or that doesn't say, gets a page with one button that confirms it, posted from this
site.
"""

import re
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import Blueprint, current_app, redirect, render_template, request
from sqlalchemy.exc import IntegrityError

from broker.auth import require_user_id, with_retry_after
from broker.db import db
from broker.routes.pages import _write_subscriptions_to_redis
from broker.services import config_store, registration

if TYPE_CHECKING:
    from flask.typing import ResponseReturnValue

bp = Blueprint("register", __name__)

SECRET_HASH_RE = re.compile(r"\A[0-9a-f]{64}\Z")
# A tablet's longest side in pixels: anything bigger is a mistake, not a screen.
MAX_SIZE = 10_000
# Where the browser says a navigation came from, for the ones that may register with no button: typed or opened by the
# tablet's app, this site, or a sibling host (the login's redirects back from Tinyauth and Pocket ID).
OWN_SITES = frozenset({"none", "same-origin", "same-site"})


@bp.route("/register", methods=["GET", "POST"])
@require_user_id
def register(user_id: str) -> ResponseReturnValue:
    """Register the touchscreen in the query (or the confirming form) and make the signed-in user its owner.

    200 with a page saying it's theirs, also for a repeat (a reload, or the tablet asking again), or a 303 to the
    `return_to` the tablet passed, with the device id; 400 for a hash, size or return URL that isn't one; 409 if
    someone else already owns it; 429 when this IP registers too often. A GET from another site, or one that doesn't
    say where it's from, changes nothing and asks to confirm (a POST, only from this site).
    """
    values = request.form if request.method == "POST" else request.args
    secret_hash = values.get("secret_hash", "").strip().lower()
    width, height = _size(values.get("width")), _size(values.get("height"))
    return_to = values.get("return_to", "").strip()
    if not SECRET_HASH_RE.match(secret_hash) or width is None or height is None or not _may_return_to(return_to):
        error = "This link isn't a touchscreen's: open it from the touchscreen again."
        return render_template("register.html", error=error), 400

    if request.method == "POST":
        if not _posted_here():
            return render_template("register.html", error="Confirm on this site to add the touchscreen."), 403
    elif request.headers.get("Sec-Fetch-Site") not in OWN_SITES:
        return render_template(
            "register.html", confirm=True, secret_hash=secret_hash, width=width, height=height, return_to=return_to
        )

    throttle = current_app.extensions["register_throttle"]
    ip = request.remote_addr or "unknown"
    retry_after = throttle.retry_after(ip)
    if retry_after is not None:
        page = render_template("register.html", error="Too many touchscreens added. Please wait and try again.")
        return with_retry_after(current_app.make_response(page), retry_after), 429
    throttle.record(ip)
    return _register_and_claim(user_id, secret_hash, registration.Screen("touchscreen", width, height), return_to)


def _register_and_claim(
    user_id: str, secret_hash: str, screen: registration.Screen, return_to: str
) -> ResponseReturnValue:
    """Register and claim the touchscreen, then go back to `return_to` with the outcome, or say it on the page."""
    try:
        try:
            found = registration.register_and_claim(secret_hash, screen, user_id, ip=request.remote_addr)
        except IntegrityError:
            # The same hash registered by a simultaneous request that got in first: answer as for any repeat.
            db.session.rollback()
            found = registration.register_and_claim(secret_hash, screen, user_id, ip=request.remote_addr)
    except registration.TakenError:
        if return_to:
            return redirect(_with_query(return_to, error="taken"), 303)
        error = "This touchscreen already belongs to someone else. They can remove it from their account first."
        return render_template("register.html", error=error), 409

    # As pairing does: the config goes in the store, and what it shows is subscribed to.
    config_store.publish(found.device_id)
    _write_subscriptions_to_redis()
    if return_to:
        return redirect(_with_query(return_to, device_id=found.device_id), 303)
    return render_template("register.html", device_id=found.device_id, created=found.created)


def _size(value: str | None) -> int | None:
    """Return a width or height from the query, or None unless it's a whole number of pixels the size of a screen."""
    if value is None or not value.isdigit():
        return None
    size = int(value)
    return size if 0 < size <= MAX_SIZE else None


def _may_return_to(url: str) -> bool:
    """Whether a return URL is absent, or on one of the tablet app's origins (TOUCHSCREEN_RETURN_ORIGINS)."""
    if not url:
        return True
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    # The origin as written, so a user:password@ or a different port never matches one allowed.
    origin = f"{parts.scheme}://{parts.netloc}".lower()
    return bool(parts.scheme and parts.netloc) and origin in current_app.config["TOUCHSCREEN_RETURN_ORIGINS"]


def _with_query(url: str, **params: str) -> str:
    """Return the URL with these query parameters added (replacing any of the same name it had)."""
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key not in params]
    return urlunsplit(parts._replace(query=urlencode([*query, *params.items()])))


def _posted_here() -> bool:
    """Whether the confirming POST came from a page of this site, not a form on another one."""
    site = request.headers.get("Sec-Fetch-Site")
    if site is not None:
        return site == "same-origin"
    origin = request.headers.get("Origin")
    return origin is not None and urlsplit(origin).netloc == request.host
