"""The settings a user's new screens start with: saved from one of their screens, and applied at a first pairing.

A user keeps at most one set for each type of device (models.ScreenDefaults), saved from a screen's page. A screen of
that type takes them the first time the user pairs with it (pairing._pair), in place of the defaults it registered
with. They are how the screen looks and what it shows (FIELDS), never what the screen said of itself at registration:
its size, frame format and whether it can set its brightness stay its own.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from flask import current_app

from broker.db import db
from broker.models import TYPE_FIELDS, ScreenDefaults
from broker.services import renderer_config

if TYPE_CHECKING:
    from broker.models import Device, DeviceConfig, DeviceType

# The settings both types of device have, by DeviceConfig attribute: the clock and language, the weather's place, the
# departure stops, the notice board's postcode and address, and the parcel notices.
_SHARED = frozenset(
    {
        "clock_format",
        "language",
        "weather_location",
        "weather_place_id",
        "weather_place_name",
        "weather_place_label",
        "weather_country_code",
        "weather_lat",
        "weather_lon",
        "tfl_stop_ids",
        "arrivals_group_by_route",
        "postcode",
        "address_street",
        "address_house_number",
        "address_city",
        "parcels_enabled",
        "parcels_days_ahead",
        "parcels_kept_hours",
    }
)
# An e-paper screen's other integrations' switches: a touchscreen's config has none of them.
_EPAPER_INTEGRATIONS = frozenset({"spotify_enabled", "apple_music_enabled", "developer_enabled", "energy_enabled"})

# The settings saved and applied for each type of device, by DeviceConfig attribute: those above and the type's own
# (models.TYPE_FIELDS).
FIELDS: dict[DeviceType, frozenset[str]] = {
    "epaper": _SHARED | _EPAPER_INTEGRATIONS | TYPE_FIELDS["epaper"],
    "touchscreen": _SHARED | TYPE_FIELDS["touchscreen"],
}


def get(user_id: str, device_type: DeviceType) -> ScreenDefaults | None:
    """Return the settings the user's new screens of this type start with, if they saved any."""
    return db.session.get(ScreenDefaults, (user_id, device_type))


def save(user_id: str, config: DeviceConfig, source_name: str) -> None:
    """Keep the screen's settings as what the user's new screens of its type start with (not committed).

    They replace any the user saved for the type before. `source_name` is what the screen is called, for the page.
    """
    device_type = config.device.type
    settings = {field: getattr(config, field) for field in sorted(FIELDS[device_type])}
    saved = get(user_id, device_type)
    if saved is None:
        saved = ScreenDefaults(user_id=user_id, type=device_type)
        db.session.add(saved)
    saved.settings = settings
    saved.saved_at = datetime.now(UTC)
    saved.source_name = source_name


def clear(user_id: str, device_type: DeviceType) -> None:
    """Forget the settings the user saved for new screens of this type (not committed)."""
    saved = get(user_id, device_type)
    if saved is not None:
        db.session.delete(saved)


def apply(device: Device, user_id: str) -> bool:
    """Give the device the settings the user saved for new screens of its type (not committed); whether it took them.

    Only the settings still saved for the type (FIELDS) are set: one the snapshot lacks, as it was saved before the
    setting existed, keeps the device's default, and one no longer a setting is skipped. The config they make must be
    one the contract allows (renderer_config.contract_allows), as a value saved long ago may no longer be: if it
    isn't, the device keeps the settings it had.
    """
    config = device.config
    saved = get(user_id, device.type)
    if config is None or saved is None:
        return False
    settings = {field: value for field, value in saved.settings.items() if field in FIELDS[device.type]}
    before = {field: getattr(config, field) for field in settings}
    for field, value in settings.items():
        setattr(config, field, value)
    if renderer_config.contract_allows(config):
        return True
    for field, value in before.items():
        setattr(config, field, value)
    # Not the settings themselves: they are the owner's.
    current_app.logger.warning(
        "The settings saved for new %s screens aren't allowed for %s", device.type, device.device_id
    )
    return False
