"""A user's Parcel API key, from their own Parcel Premium account (web.parcelapp.net), for all their screens.

The config page saves it against their account (broker/routes/pages.py) and the parcel provider reads it for each
screen, through its owner, with get_parcel_api_key. It is kept encrypted (models.ParcelKey), never sent to the browser,
and never logged. A screen shows the panel while its owner has one and its DeviceConfig.parcels_enabled is on: that's
`parcels.enabled` in its config (services/renderer_config.py).

Whether Parcel takes the key is the provider's to say, in `v1:parcels-status:<device id>`; link_status reads it.
"""

from typing import TYPE_CHECKING

from countdown_api import keys as store_keys
from redis import Redis, RedisError

from broker.models import ParcelKey
from broker.services import accounts

if TYPE_CHECKING:
    from countdown_api.models import ParcelsLinkStatus
    from flask_sqlalchemy import SQLAlchemy


def get_parcel_api_key(db: SQLAlchemy, device_id: str) -> str | None:
    """Return the Parcel API key of the device's owner, decrypted; None if they have none saved (or nobody owns it).

    Raises cryptography.fernet.InvalidToken if the stored key won't decrypt (CREDENTIAL_ENCRYPTION_KEY changed). Needs
    an app context, as `db.session` does.
    """
    user_id = accounts.owner(device_id)
    row = db.session.get(ParcelKey, user_id) if user_id is not None else None
    return row.key if row is not None else None


def has_key(db: SQLAlchemy, user_id: str) -> bool:
    """Whether the user's account has a Parcel API key saved, without decrypting it."""
    return db.session.get(ParcelKey, user_id) is not None


def link_status(store: Redis, device_id: str) -> ParcelsLinkStatus | None:
    """Return whether Parcel took the key at the provider's last poll; None if it hasn't tried (or Redis is down)."""
    try:
        value = store.get(store_keys.parcels_status(device_id))
    except RedisError:
        return None
    match value.decode() if isinstance(value, bytes) else value:
        case "ok" | "invalid_key" | "rate_limited" as status:
            return status
        case _:
            return None
