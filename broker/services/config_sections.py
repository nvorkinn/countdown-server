"""Saving a device's config a section at a time (PUT /api/devices/config/{section}), and the config's version.

A section is a top-level field of the contract's ConfigResponse that isn't read-only or the server's own (the
contract's ConfigSection): `arrivals`, `style`, and the flat `clock_format` and `language` too. A save replaces one
whole: the route puts the value into the config as it stands and validates the result with the device type's model,
and set() then writes that section to the device's settings (DeviceConfig), as the settings page's form does. What
the server works out itself (the notice board's country, the weather's location) is worked out again from what was
saved, whatever was sent.

The version is a hash of the config as built (renderer_config.build), so it changes when anything in it does,
whoever saved it: the settings page, a device's whole save, or a section's.
"""

import hashlib
import json
from typing import TYPE_CHECKING, Any

from countdown_api.models import (
    Arrivals,
    EpaperConfig,
    EpaperStyle,
    NoticeBoard,
    TouchscreenConfig,
    TouchscreenDisplay,
    TouchscreenStyle,
    Weather,
)

from broker.clients import weather
from broker.services import renderer_config, stops, weather_place
from broker.services.postcodes import normalize_postcode

if TYPE_CHECKING:
    from collections.abc import Callable

    from broker.models import DeviceConfig


class SectionUnavailableError(Exception):
    """A section that can't be checked right now: Open-Meteo, for a weather place."""


def version(config: DeviceConfig) -> str:
    """Return the config's version as an HTTP ETag (quoted), the contract's ConfigVersion."""
    return version_of(renderer_config.build(config, pairing_code=None))


def version_of(built: EpaperConfig | TouchscreenConfig) -> str:
    """Return the version of a config already built."""
    canonical = json.dumps(built.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return f'"{hashlib.sha256(canonical.encode()).hexdigest()[:32]}"'


def _arrivals(config: DeviceConfig, value: Arrivals) -> str | None:
    for stop in value.stops:
        if not stop.id or stops.source_of(stop.id) != stop.source:
            return "each stop's source must be its id's: entur for an NSR: id, tfl for any other"
    config.tfl_stop_ids = [stop.id for stop in value.stops]
    return None


def _weather(config: DeviceConfig, value: Weather) -> str | None:
    """Pick the place the key is of: the one saved, one Open-Meteo has (by the id the key ends in), or none (null)."""
    if value.key is None:
        if value.location.strip():
            return "pick a place from the place search: its value has the key"
        weather_place.store(config, None)
        return None
    if value.key == weather_place.weather_key(config):
        return None  # the place already saved: nothing to ask Open-Meteo
    place_id = weather_place.place_id_of(value.key)
    try:
        place = weather.get_place(place_id) if place_id is not None else None
    except weather.GeocodingUnavailableError as error:
        raise SectionUnavailableError from error
    if place is None or weather_place.place_key(place) != value.key:
        return "not the key of a place: use one the place search answered"
    weather_place.store(config, place)
    return None


def _notice_board(config: DeviceConfig, value: NoticeBoard) -> str | None:
    postcode = normalize_postcode(value.postcode or "")
    if postcode is None:
        return "the postcode must be a UK or a Norwegian one"
    config.postcode = postcode
    return None


def _style(config: DeviceConfig, value: EpaperStyle | TouchscreenStyle) -> None:
    if isinstance(value, TouchscreenStyle):
        config.theme, config.theme_colour = value.skin, value.colour
        return
    config.orientation = value.orientation
    config.style = value.drawing
    config.weather_design = value.weather
    config.layout = value.layout


def _display(config: DeviceConfig, value: TouchscreenDisplay) -> None:
    config.screen_sleep_minutes = value.sleep_minutes


def _switch(column: str) -> Callable[[DeviceConfig, Any], None]:
    """Return the setter of a service's section, which is only its switch."""

    def set_switch(config: DeviceConfig, value: Any) -> None:  # noqa: ANN401 -- any of the services' sections
        setattr(config, column, value.enabled)

    return set_switch


def _flat(column: str) -> Callable[[DeviceConfig, Any], None]:
    def set_flat(config: DeviceConfig, value: Any) -> None:  # noqa: ANN401 -- a flat field's value
        setattr(config, column, value)

    return set_flat


# Each section's setter, by its name in ConfigResponse (the contract's ConfigSection). A device's type has the
# sections its model has fields for (sections()). A setter returns why a value can't be saved (a fixed message, which
# never quotes the value), or None once it's written.
SETTERS: dict[str, Callable[[DeviceConfig, Any], str | None]] = {
    "clock_format": _flat("clock_format"),
    "language": _flat("language"),
    "arrivals": _arrivals,
    "weather": _weather,
    "notice_board": _notice_board,
    "spotify": _switch("spotify_enabled"),
    "apple_music": _switch("apple_music_enabled"),
    "glowmarkt": _switch("energy_enabled"),
    "developer": _switch("developer_enabled"),
    "parcels": _switch("parcels_enabled"),
    "style": _style,
    "display": _display,
}


def sections(model: type[EpaperConfig | TouchscreenConfig]) -> frozenset[str]:
    """Return the sections a config of this model (EpaperConfig or TouchscreenConfig) has."""
    return frozenset(SETTERS.keys() & model.model_fields.keys())


def set_section(config: DeviceConfig, section: str, value: object) -> str | None:
    """Write a section's value (validated by the device type's model) to the device's settings.

    Returns why a value can't be saved (a message fit to answer with), or None once it's written. Raises
    SectionUnavailableError when it can't be checked now.
    """
    return SETTERS[section](config, value)
