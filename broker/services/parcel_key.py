"""A device's Parcel API key: the owner's own, from their Parcel Premium account (web.parcelapp.net).

The config page saves it (broker/routes/pages.py) and the parcel provider reads it, through get_parcel_api_key. It is
kept encrypted (models.ParcelKey), never sent to the browser, and never logged. Having one is what switches the
parcels panel on: DeviceConfig.parcels_enabled, so `parcels.enabled` in the device's config and its place in
`v1:sub:parcel`.

Whether Parcel takes the key is the provider's to say, in `v1:parcels-status:<device id>`; link_status reads it.
"""

from typing import TYPE_CHECKING

from countdown_api import keys as store_keys
from redis import Redis, RedisError

from broker.models import ParcelKey

if TYPE_CHECKING:
    from countdown_api.models import ParcelsLinkStatus
    from flask_sqlalchemy import SQLAlchemy


def get_parcel_api_key(db: SQLAlchemy, device_id: str) -> str | None:
    """Return the device's Parcel API key, decrypted; None if it has none saved.

    Raises cryptography.fernet.InvalidToken if the stored key won't decrypt (CREDENTIAL_ENCRYPTION_KEY changed). Needs
    an app context, as `db.session` does.
    """
    row = db.session.get(ParcelKey, device_id)
    return row.key if row is not None else None


def has_key(db: SQLAlchemy, device_id: str) -> bool:
    """Whether the device has a Parcel API key saved, without decrypting it: the parcels panel is on."""
    return db.session.get(ParcelKey, device_id) is not None


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
