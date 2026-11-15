"""A device's config, which a touchscreen polls: GET /api/config (the store contract's getConfig).

A touchscreen draws itself from its config, as the render pool draws an e-paper screen from the store's copy of one
(services/config_store.py). It's the same config, built the same way (renderer_config.build), and never has a
pairing code: a touchscreen is paired by its pairing token, not a code it shows. Its owner changes it on the settings
site, which saves it whole; nothing else does.
"""

from typing import TYPE_CHECKING

from flask import Blueprint

from broker.auth import require_device_auth
from broker.db import db
from broker.models import DeviceConfig
from broker.services import renderer_config

if TYPE_CHECKING:
    from flask.typing import ResponseReturnValue

bp = Blueprint("config_api", __name__, url_prefix="/api")


@bp.get("/config")
@require_device_auth()
def get_config(device_id: str) -> ResponseReturnValue:
    """Answer the device's config (ConfigResponse), paired or not, with no pairing code.

    Takes the device's secret as `Authorization: Bearer <secret>`.
    """
    config = db.session.get_one(DeviceConfig, device_id)
    built = renderer_config.build(config, pairing_code=None)
    return built.model_dump(mode="json"), 200
