"""A device's routes: its registration, its config, and a touchscreen's Spotify token.

Once registered, a device reads its config here (GET /config, the store contract's getConfig), and a touchscreen
saves what its own settings panel shows (PUT /config, putConfig). A touchscreen draws itself from that config, as the
render pool draws an e-paper screen from the store's copy of one (services/config_store.py): it's the same config,
built the same way (renderer_config.build), and never has a pairing code, as a touchscreen is paired by its pairing
token, not a code it shows. Its owner sets it up on the settings site; a PUT sends the whole config as it got it and
may change only SAVED_HERE, and one that changes anything else is refused and saves nothing. An e-paper screen polls
for its frames (routes/frames.py).

A section at a time is PUT /config/<section> (putConfigSection, services/config_sections.py), which the settings site
calls too, with its owner's login instead of the secret (auth.require_device_or_owner), and GET /config with it. Every
answer carries the config's version as its ETag, and a section's save needs it back as If-Match: a stale one is a 409,
so whoever saved last isn't overwritten unseen.

A touchscreen plays its owner's Spotify itself (its Web Playback SDK), so it asks for a short-lived access token for
the account's link (GET /spotify/token); the refresh token and the app's secret stay here.
"""

from typing import TYPE_CHECKING, Any, TypeGuard

import requests
from countdown_api.models import EpaperConfig, TouchscreenConfig
from flask import Blueprint, current_app, jsonify, request
from redis import RedisError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from broker.auth import hash_secret, require_device_auth, require_device_or_owner, with_retry_after
from broker.clients import spotify
from broker.db import db
from broker.models import DEVICE_TYPES, FRAME_FORMATS, Device, DeviceConfig, DeviceType, FrameFormat, SpotifyToken
from broker.routes.pages import _contract_errors, _publish_saved
from broker.services import accounts, config_sections, config_store, panels, registration, renderer_config

if TYPE_CHECKING:
    from collections.abc import Callable

    from flask.typing import ResponseReturnValue
    from redis import Redis

bp = Blueprint("devices", __name__, url_prefix="/api/devices")

MIN_SECRET_LENGTH = 16

# ConfigResponse's members, by their tag, `type`.
CONFIGS = {"epaper": EpaperConfig, "touchscreen": TouchscreenConfig}
# What a touchscreen's own settings panel changes, and so what a PUT may: its look, when it sleeps, and its clock.
SAVED_HERE = frozenset({"clock_format", "style", "display"})


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
    lockout = throttle.record(ip)
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


@bp.get("/config")
@require_device_or_owner
def get_config(device_id: str, *, by_device: bool) -> ResponseReturnValue:  # noqa: ARG001 -- the same for both
    """Answer the device's config (ConfigResponse), paired or not, with no pairing code, and its version as the ETag.

    Takes the device's secret as `Authorization: Bearer <secret>`, or, from the settings site, its owner's login and
    ?device_id= (auth.require_device_or_owner). With If-None-Match of the version, a 304 while it's still the config.
    """
    built = renderer_config.build(db.session.get_one(DeviceConfig, device_id), pairing_code=None)
    etag = config_sections.version_of(built)
    if etag in _etags(request.headers.get("If-None-Match", ""), weak=True):
        return "", 304, {"ETag": etag}
    return built.model_dump(mode="json"), 200, {"ETag": etag}


@bp.put("/config")
@require_device_auth
def put_config(device_id: str) -> ResponseReturnValue:
    """Save a touchscreen's own settings from the whole config it sends, and answer the config as saved.

    400 for a config the contract doesn't allow or that changes anything but SAVED_HERE (read-only fields included),
    409 for a device no one has paired yet, or an e-paper screen, which has no settings panel of its own, and for an
    If-Match that isn't the config's version any more; 403 when its owner doesn't approve the save
    (accounts.approves_device_save).
    """
    device = db.session.get_one(Device, device_id)
    config = _locked_config(device_id)
    if device.paired_at is None or device.type != "touchscreen":
        return jsonify(error="only a paired touchscreen saves its own config; set it up on the settings site"), 409
    if not accounts.approves_device_save(device):
        return jsonify(error="the device's owner hasn't approved this save"), 403
    current = renderer_config.build(config, pairing_code=None)
    if_match = request.headers.get("If-Match")
    if if_match is not None and config_sections.version_of(current) not in _etags(if_match):
        return _stale()
    body = request.get_json(silent=True)
    model = CONFIGS.get(body.get("type")) if isinstance(body, dict) else None
    try:
        if model is None:
            raise ValueError  # noqa: TRY301 -- the same answer as a config its model refuses
        sent = model.model_validate(body)
    except ValueError:  # pydantic's ValidationError is one
        return jsonify(error="not a config the contract allows"), 400
    changed = sorted(name for name in type(current).model_fields if getattr(sent, name) != getattr(current, name))
    if not isinstance(sent, TouchscreenConfig) or not set(changed) <= SAVED_HERE:
        refused = [name for name in changed if name not in SAVED_HERE] or ["type"]
        return jsonify(error=f"only {', '.join(sorted(SAVED_HERE))} can be saved here, not {', '.join(refused)}"), 400
    config.clock_format = sent.clock_format
    config.theme = sent.style.skin
    config.theme_colour = sent.style.colour
    config.screen_sleep_minutes = sent.display.sleep_minutes
    db.session.commit()
    # Every screen's copy, as a save on the settings site does (the render pool never draws a touchscreen, so there's
    # nothing to queue).
    config_store.publish_all()
    return _saved(config)


@bp.put("/config/<section>")
@require_device_or_owner
def put_config_section(device_id: str, section: str, *, by_device: bool) -> ResponseReturnValue:  # noqa: PLR0911 -- an answer per failure
    """Replace one section of the device's config whole (config_sections), and answer the config as saved.

    Needs If-Match, the version the change was made to (428 without): a stale one is a 409, so the caller reloads
    rather than overwrites what was saved in between. The section is put into the config as it stands and the result
    validated with the device type's model (400 if it refuses it, or the value can't be saved); one the type doesn't
    have is a 404. A device saves its own config only as a paired touchscreen whose owner approves (403 otherwise);
    on the settings site, any device of the person's (auth.require_device_or_owner). 503 when a weather place can't
    be checked with Open-Meteo right now.
    """
    device = db.session.get_one(Device, device_id)
    if by_device and (refused := _refused_own_save(device)):
        return refused
    model = CONFIGS[device.type]
    if section not in config_sections.sections(model):
        return jsonify(error=f"a {device.type} config has no section {section!r}"), 404
    if_match = request.headers.get("If-Match")
    if if_match is None:
        return jsonify(error="send the config's version (its ETag) as If-Match"), 428
    config = _locked_config(device_id)
    current = renderer_config.build(config, pairing_code=None)
    if config_sections.version_of(current) not in _etags(if_match):
        return _stale()
    body = request.get_json(silent=True)
    try:
        if body is None:
            raise ValueError  # noqa: TRY301 -- the same answer as a section the model refuses
        spliced = model.model_validate({**current.model_dump(mode="json"), section: body})
    except ValueError:  # pydantic's ValidationError is one
        return jsonify(error=f"not a {section} the contract allows for a {device.type}"), 400
    turned = isinstance(spliced, EpaperConfig) and spliced.style.orientation != config.orientation
    if refused := _set_section(config, section, getattr(spliced, section)):
        db.session.rollback()
        return refused
    db.session.commit()
    _publish_saved(device_id, turned=turned)
    return _saved(config)


def _refused_own_save(device: Device) -> ResponseReturnValue | None:
    """Return the 403 for a device that may not save its own config, or None if it may."""
    if device.paired_at is None or device.type != "touchscreen":
        return jsonify(error="only a paired touchscreen saves its own config; set it up on the settings site"), 403
    if not accounts.approves_device_save(device):
        return jsonify(error="the device's owner hasn't approved this save"), 403
    return None


def _set_section(config: DeviceConfig, section: str, value: object) -> ResponseReturnValue | None:
    """Write a section to the device's settings; return the answer if it can't be saved, or None if it was."""
    try:
        config_sections.set_section(config, section, value)
    except config_sections.SectionError as error:
        return jsonify(error=str(error)), 400
    except config_sections.SectionUnavailableError:
        return with_retry_after(jsonify(error="the weather place can't be checked just now"), 60), 503
    # Every save is checked as the settings page's are: the config it makes, as built, against the type's model.
    if _contract_errors(config):
        return jsonify(error=f"not a {section} the contract allows for a {config.device.type}"), 400
    return None


def _locked_config(device_id: str) -> DeviceConfig:
    """Return the device's settings, read afresh and locked until the save commits.

    So two saves of one version can't both pass the If-Match check. SQLite has no FOR UPDATE (SQLAlchemy leaves it
    out), but the broker's one sync worker (gunicorn.conf.py) takes one request at a time anyway.
    """
    return db.session.scalars(
        select(DeviceConfig)
        .where(DeviceConfig.device_id == device_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()


def _etags(header: str, *, weak: bool = False) -> set[str]:
    """Return the ETags an If-Match header lists; `weak` for If-None-Match's, where W/"x" is "x" too."""
    tags = {tag.strip() for tag in header.split(",") if tag.strip()}
    return {tag.removeprefix("W/") for tag in tags} if weak else tags


def _stale() -> ResponseReturnValue:
    return jsonify(error="the config has changed since: read it again"), 409


def _saved(config: DeviceConfig) -> ResponseReturnValue:
    """Answer the config as saved, with its new version."""
    built = renderer_config.build(config, pairing_code=None)
    return built.model_dump(mode="json"), 200, {"ETag": config_sections.version_of(built)}


@bp.get("/spotify/token")
@require_device_auth
def spotify_token(device_id: str) -> ResponseReturnValue:
    """Answer a paired touchscreen an access token for its owner's Spotify (SpotifyAccessToken).

    404 when the owner's account hasn't linked Spotify, 403 when the link was made without the scopes a touchscreen
    plays with (linking it again on the Account page grants them), 409 for a device no one has paired yet or an e-paper
    screen, and 502 when Spotify won't renew the token.
    """
    device = db.session.get_one(Device, device_id)
    if device.paired_at is None or device.type != "touchscreen" or device.owner is None:
        return jsonify(error="only a paired touchscreen gets a Spotify token"), 409
    link = db.session.get(SpotifyToken, device.owner)
    if link is None:
        return jsonify(error="the owner's account hasn't linked Spotify"), 404
    if spotify.lacks_touchscreen_scopes(link):
        return jsonify(error="link Spotify again on the Account page to play it on a touchscreen"), 403
    try:
        token = spotify.touchscreen_token(device_id)
    except requests.RequestException as error:
        # Its type only: the error's text may carry the request, and so the refresh token.
        current_app.logger.warning("Spotify didn't renew a touchscreen's token: %s", type(error).__name__)
        return jsonify(error="Spotify couldn't renew the token"), 502
    if token is None:  # unlinked in between
        return jsonify(error="the owner's account hasn't linked Spotify"), 404
    return token.model_dump(mode="json"), 200


@bp.get("/weather")
@require_device_auth
def weather(device_id: str) -> ResponseReturnValue:
    """Answer the device's weather (DeviceWeather): its place's forecast, as the store has it."""
    return _panel(panels.weather, device_id)


@bp.get("/arrivals")
@require_device_auth
def arrivals(device_id: str) -> ResponseReturnValue:
    """Answer the device's live arrivals (DeviceArrivals): each of its stops' departures, as the store has them."""
    return _panel(panels.arrivals, device_id)


@bp.get("/notices")
@require_device_auth
def notices(device_id: str) -> ResponseReturnValue:
    """Answer the device's notices (DeviceNotices): their sources for its stops and postcode, as the store has them."""
    return _panel(panels.notices, device_id)


def _panel(read: Callable[[Redis, str], dict[str, Any]], device_id: str) -> ResponseReturnValue:
    """Answer what `read` makes of the store for the device: 409 while it isn't paired, 503 while Redis is down."""
    try:
        return read(current_app.extensions["redis"], device_id), 200
    except panels.NotPairedError:
        return jsonify(error="the device isn't paired yet"), 409
    except RedisError as error:
        current_app.logger.warning("Couldn't read a device's panel from Redis: %s", type(error).__name__)
        return with_retry_after(jsonify(error="the store couldn't be reached"), 5), 503
