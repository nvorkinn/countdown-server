"""A device's config: GET and PUT /api/devices/config (the store contract's getConfig and putConfig).

A touchscreen draws itself from its config, as the render pool draws an e-paper screen from the store's copy of one
(services/config_store.py). It's the same config, built the same way (renderer_config.build), and never has a
pairing code: a touchscreen is paired by its pairing token, not a code it shows.

Its owner sets it up on the settings site. A touchscreen also saves what its own settings panel shows (SAVED_HERE)
with a PUT of the whole config, as it got it with the change made; everything else is changed on the settings site
only, so a PUT that changes anything else is refused, and saves nothing.
"""

from typing import TYPE_CHECKING

from countdown_api.models import EpaperConfig, TouchscreenConfig
from flask import Blueprint, jsonify, request

from broker.auth import require_device_auth
from broker.db import db
from broker.models import Device, DeviceConfig
from broker.services import config_store, renderer_config

if TYPE_CHECKING:
    from flask.typing import ResponseReturnValue

bp = Blueprint("config_api", __name__, url_prefix="/api/devices")

# ConfigResponse's members, by their tag, `type`.
CONFIGS = {"epaper": EpaperConfig, "touchscreen": TouchscreenConfig}
# What a touchscreen's own settings panel changes, and so what a PUT may: its look, when it sleeps, and the clock and
# language every panel shares.
SAVED_HERE = frozenset({"clock_format", "language", "style", "display"})


@bp.get("/config")
@require_device_auth()
def get_config(device_id: str) -> ResponseReturnValue:
    """Answer the device's config (ConfigResponse), paired or not, with no pairing code.

    Takes the device's secret as `Authorization: Bearer <secret>`.
    """
    config = db.session.get_one(DeviceConfig, device_id)
    return renderer_config.build(config, pairing_code=None).model_dump(mode="json"), 200


@bp.put("/config")
@require_device_auth()
def put_config(device_id: str) -> ResponseReturnValue:
    """Save a touchscreen's own settings from the whole config it sends, and answer the config as saved.

    400 for a config the contract doesn't allow or that changes anything but SAVED_HERE (read-only fields included),
    409 for a device no one has paired yet, or an e-paper screen, which has no settings panel of its own.
    """
    device = db.session.get_one(Device, device_id)
    config = db.session.get_one(DeviceConfig, device_id)
    if device.paired_at is None or device.type != "touchscreen":
        return jsonify(error="only a paired touchscreen saves its own config; set it up on the settings site"), 409
    body = request.get_json(silent=True)
    model = CONFIGS.get(body.get("type")) if isinstance(body, dict) else None
    try:
        if model is None:
            raise ValueError  # noqa: TRY301 -- the same answer as a config its model refuses
        sent = model.model_validate(body)
    except ValueError:  # pydantic's ValidationError is one
        return jsonify(error="not a config the contract allows"), 400
    current = renderer_config.build(config, pairing_code=None)
    changed = sorted(name for name in type(current).model_fields if getattr(sent, name) != getattr(current, name))
    if not isinstance(sent, TouchscreenConfig) or not set(changed) <= SAVED_HERE:
        refused = [name for name in changed if name not in SAVED_HERE] or ["type"]
        return jsonify(error=f"only {', '.join(sorted(SAVED_HERE))} can be saved here, not {', '.join(refused)}"), 400
    config.clock_format = sent.clock_format
    config.language = sent.language
    config.theme = sent.style.skin
    config.theme_colour = sent.style.colour
    config.screen_sleep_minutes = sent.display.sleep_minutes
    db.session.commit()
    # Every screen's copy, as a save on the settings site does (the render pool never draws a touchscreen, so there's
    # nothing to queue).
    config_store.publish_all()
    return renderer_config.build(config, pairing_code=None).model_dump(mode="json"), 200
