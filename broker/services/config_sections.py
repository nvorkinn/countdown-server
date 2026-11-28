"""Saving a device's config a section at a time (PUT /api/devices/config/{section}), and the config's version.

A section is a top-level field of the contract's ConfigResponse that isn't read-only or the server's own (the
contract's ConfigSection): `arrivals`, `style`, `lyrics`, `holidays`, and the flat `clock_format` and `language` too. A save
replaces one whole: the route puts the value into the config as it stands and validates the result with the device
type's model, and set() then writes that section to the device's settings (DeviceConfig), as the settings page's form
does. What the server works out itself (the notice board's country, the weather's location) is worked out again from
what was saved, whatever was sent.

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
    Parcels,
    TouchscreenConfig,
    TouchscreenDisplay,
    TouchscreenHolidays,
    TouchscreenLyrics,
    TouchscreenStyle,
    Weather,
)

from broker.clients import weather
from broker.services import household_address, renderer_config, stops, weather_place
from broker.services.postcodes import normalize_postcode

if TYPE_CHECKING:
    from collections.abc import Callable

    from broker.models import DeviceConfig, ThemeColour


class SectionUnavailableError(Exception):
    """A section that can't be checked right now; `reason` says which, as the answer's error (a fixed clause)."""

    reason = "the section can't be checked just now"


class PlaceUnavailableError(SectionUnavailableError):
    """A weather place that Open-Meteo can't be asked about right now."""

    reason = "the weather place can't be checked just now"


class AddressUnavailableError(SectionUnavailableError):
    """A household address that Kartverket can't be asked about right now."""

    reason = "the address can't be checked just now"


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
    # A device from before the switch sends none: it keeps what's saved.
    if value.group_by_route is not None:
        config.arrivals_group_by_route = value.group_by_route
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
        raise PlaceUnavailableError from error
    if place is None or weather_place.place_key(place) != value.key:
        return "not the key of a place: use one the place search answered"
    weather_place.store(config, place)
    return None


def _notice_board(config: DeviceConfig, value: NoticeBoard) -> str | None:
    """Save the postcode and, for a Norwegian one, the household address, as Kartverket has it.

    An address that isn't the one saved is looked up by its street and house number in the postcode (its city is
    Kartverket's, whatever was sent), and saved only when exactly one matches. A new postcode clears the saved one; a
    device that doesn't know the address yet sends none, and keeps it.
    """
    postcode = normalize_postcode(value.postcode or "")
    if postcode is None:
        return "the postcode must be a UK or a Norwegian one"
    if "address" not in value.model_fields_set:
        text = None
    else:
        text = household_address.text_of(value.address) if value.address else ""
    try:
        address = household_address.posted(config, postcode, text)
    except household_address.AddressNotFoundError as error:
        if error.several:
            return "more than one address in the postcode matches: give the house number, and its letter if it has one"
        return "no address in the postcode matches: give the street and house number as on the letterbox"
    except household_address.AddressUnavailableError as error:
        raise AddressUnavailableError from error
    household_address.store(config, address)
    config.postcode = postcode
    return None


def touchscreen_colour(style: TouchscreenStyle) -> tuple[ThemeColour, bool]:
    """Return the colour and whether Dynamic is on, as DeviceConfig keeps them, from a touchscreen's style.

    The contract's old dynamic colour is forest with Dynamic on, which is how it looked without any album art.
    """
    if style.colour == "dynamic":
        return "forest", True
    return style.colour, style.dynamic


def _style(config: DeviceConfig, value: EpaperStyle | TouchscreenStyle) -> None:
    if isinstance(value, TouchscreenStyle):
        config.theme = value.skin
        config.theme_colour, config.theme_dynamic = touchscreen_colour(value)
        return
    config.orientation = value.orientation
    config.style = value.drawing
    config.weather_design = value.weather
    config.layout = value.layout


def _display(config: DeviceConfig, value: TouchscreenDisplay) -> None:
    config.screen_sleep_minutes = value.sleep_minutes
    # Left out (a device older than them), the brightness and the quiet hours stay as saved.
    if "brightness" in value.model_fields_set:
        config.screen_brightness = value.brightness
    if value.quiet_hours is not None:
        config.quiet_hours_on = value.quiet_hours.on
        config.quiet_hours_start, config.quiet_hours_end = value.quiet_hours.start, value.quiet_hours.end


def _lyrics(config: DeviceConfig, value: TouchscreenLyrics) -> None:
    config.lyrics_size, config.lyrics_align = value.size, value.align


def _holidays(config: DeviceConfig, value: TouchscreenHolidays) -> None:
    config.holidays_days_ahead = value.days_ahead


def _parcels(config: DeviceConfig, value: Parcels) -> None:
    config.parcels_enabled = value.enabled
    # Left out (a device older than them), each stays as saved; a sent null days_ahead is any day.
    if "days_ahead" in value.model_fields_set:
        config.parcels_days_ahead = value.days_ahead
    if "kept_hours" in value.model_fields_set and value.kept_hours is not None:
        config.parcels_kept_hours = value.kept_hours


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
    "parcels": _parcels,
    "style": _style,
    "display": _display,
    "lyrics": _lyrics,
    "holidays": _holidays,
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
