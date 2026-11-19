"""A user's account: the services linked to it, which every screen they own can show.

Spotify, Apple Music, GitHub, Parcel and Glowmarkt are linked once, to the signed-in user (auth.current_user), in rows
keyed by their user id (models.SpotifyToken and the rest); each screen switches each one on or off for itself
(DeviceConfig.spotify_enabled and the rest). The providers still work screen by screen, writing each screen's own keys,
so they reach a screen's services through its owner (Device.owner).
"""

from typing import TYPE_CHECKING

from countdown_api.models import AccountLink, DeviceAccount
from sqlalchemy import select

from broker.db import db
from broker.models import Device, ParcelKey, SpotifyToken
from broker.services import device_keys

if TYPE_CHECKING:
    from collections.abc import Callable


def owner(device_id: str) -> str | None:
    """Return the user whose account the device's services come from: its owner; None for a device nobody owns."""
    device = db.session.get(Device, device_id)
    return device.owner if device is not None else None


def links(user_id: str) -> DeviceAccount:
    """Return which services the user's account has linked, as a device is told (getAccount): never a login or key.

    Whether each one's row is there, and no more: a screen's own switch for it (a config's `parcels.enabled`) is the
    screen's, and whether the service still takes the login is its provider's to say.
    """
    return DeviceAccount(
        parcel=AccountLink(linked=db.session.get(ParcelKey, user_id) is not None),
        spotify=AccountLink(linked=db.session.get(SpotifyToken, user_id) is not None),
    )


def approves_device_save(device: Device) -> bool:
    """Whether the device's owner approves a save the device makes of its own config (PUT /api/devices/config).

    Saves need the owner's approval, enforced here. For now that is a stub: a device with an owner saves as its owner,
    so every save of an owned device is approved. Members (DeviceUser's "member" role) will need the owner to approve
    what they change from a shared touchscreen; this is where that check goes.
    """
    return device.owner is not None


def screens(user_id: str) -> list[str]:
    """Return the ids of the devices the user owns, which show what their account links."""
    return list(db.session.scalars(select(Device.device_id).where(Device.owner == user_id).order_by(Device.device_id)))


def has_touchscreen(user_id: str) -> bool:
    """Whether the user owns a touchscreen."""
    owned = select(Device.device_id).where(Device.owner == user_id, Device.type == "touchscreen").limit(1)
    return db.session.scalar(owned) is not None


def forget_keys(user_id: str, keys_of: Callable[[str], list[str]]) -> None:
    """Delete what the providers made from one of the user's services for every screen of theirs (device_keys)."""
    device_keys.delete([key for device_id in screens(user_id) for key in keys_of(device_id)])
