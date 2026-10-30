"""A user's account: the services linked to it, which every screen they own can show.

Spotify, Apple Music, GitHub, Parcel and Glowmarkt are linked once, to the signed-in user (auth.current_user), in rows
keyed by their user id (models.SpotifyToken and the rest); each screen switches each one on or off for itself
(DeviceConfig.spotify_enabled and the rest). The providers still work screen by screen, writing each screen's own keys,
so they reach a screen's services through its owner (Device.owner).
"""

from typing import TYPE_CHECKING

from sqlalchemy import select

from broker.db import db
from broker.models import Device
from broker.services import device_keys

if TYPE_CHECKING:
    from collections.abc import Callable


def owner(device_id: str) -> str | None:
    """Return the user whose account the device's services come from: its owner; None for a device nobody owns."""
    device = db.session.get(Device, device_id)
    return device.owner if device is not None else None


def screens(user_id: str) -> list[str]:
    """Return the ids of the devices the user owns, which show what their account links."""
    return list(db.session.scalars(select(Device.device_id).where(Device.owner == user_id).order_by(Device.device_id)))


def forget_keys(user_id: str, keys_of: Callable[[str], list[str]]) -> None:
    """Delete what the providers made from one of the user's services for every screen of theirs (device_keys)."""
    device_keys.delete([key for device_id in screens(user_id) for key in keys_of(device_id)])
