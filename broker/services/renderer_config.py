"""A screen's config as the render pool reads it: the countdown API contract's ConfigResponse, made from its settings.

The store keeps it per screen (services/config_store.py, `v1:config:<device id>`, without a pairing code), and with
the screen's live code while it has one (services/pairing_key.py, `v1:pairing:<device id>`). Both are made here, the
same way. The contract (vendor/countdown-api) is the shape countdown's renderer reads them with.
"""

from countdown_api.models import (
    AppleMusic,
    Arrivals,
    ConfigResponse,
    DepartureStop,
    Developer,
    EpaperConfig,
    EpaperStyle,
    Glowmarkt,
    NoticeBoard,
    Parcels,
    Spotify,
    TouchscreenConfig,
    TouchscreenDisplay,
    TouchscreenLyrics,
    TouchscreenStyle,
    Weather,
)
from cryptography.fernet import InvalidToken
from flask import current_app

from broker.db import db
from broker.models import Device, DeviceConfig, GlowmarktCredentials, ParcelKey
from broker.services import accounts, pace, pairing, stops, weather_place
from broker.services.postcodes import postcode_country


def build(config: DeviceConfig, *, pairing_code: str | None) -> ConfigResponse:
    """Return the config a renderer is sent for the device `config` is the settings of.

    It is the device's own settings, and no key of any kind: the renderer reads everything from the store, whose
    providers have their own keys (TfL's), or need none (Open-Meteo's weather). The broker's TfL key is only for its
    stop lookups. Carries the device_id, so a renderer needn't keep it, and `pairing_code`, the code it is to show
    (None for none).
    """
    device_id = config.device_id
    # The services are linked to the owner's account; the screen's own switches say which it shows.
    owner = accounts.owner(device_id)
    creds = db.session.get(GlowmarktCredentials, owner) if owner is not None else None
    parcel_key = db.session.get(ParcelKey, owner) if owner is not None else None
    country = postcode_country(config.postcode)
    stop_ids = config.tfl_stop_ids
    # What every type's config has besides its type: who the device is, its clock, and the panels both types show.
    arrivals = Arrivals(stops=[DepartureStop(source=stops.source_of(s), id=s) for s in stop_ids])
    # The key is what the weather is subscribed under, so it's what the renderer reads (v1:weather:<key>).
    weather = Weather(location=config.weather_location, key=weather_place.weather_key(config))
    notice_board = NoticeBoard(postcode=config.postcode or None, country=country)
    # The switch, on while the screen shows parcels and its owner has a Parcel API key saved, and which parcels it
    # shows: the key stays here, and the parcel provider fills the store the screen reads (a touchscreen through
    # getNotices).
    # The columns are plain integers, saved only from the contract's choices: validated, as the contract's literals.
    parcels = Parcels.model_validate(
        {
            "enabled": config.parcels_enabled and parcel_key is not None,
            "days_ahead": config.parcels_days_ahead,
            "kept_hours": config.parcels_kept_hours,
        }
    )
    if config.device.type == "touchscreen":
        # Paired by token, keeping its own time and playing its own music: nothing more than its language, its look,
        # its screen's sleep and its parcel notices.
        return TouchscreenConfig(
            device_id=device_id,
            type="touchscreen",
            width=config.width,
            height=config.height,
            clock_format=config.clock_format,
            language=config.language,
            arrivals=arrivals,
            weather=weather,
            notice_board=notice_board,
            style=TouchscreenStyle(skin=config.theme, colour=config.theme_colour, dynamic=config.theme_dynamic),
            display=TouchscreenDisplay(sleep_minutes=config.screen_sleep_minutes),
            lyrics=TouchscreenLyrics(size=config.lyrics_size, align=config.lyrics_align),
            parcels=parcels,
        )
    return EpaperConfig(
        device_id=device_id,
        type="epaper",
        width=config.width,
        height=config.height,
        clock_format=config.clock_format,
        arrivals=arrivals,
        weather=weather,
        notice_board=notice_board,
        pairing_code=pairing_code,
        # How often the render pool draws (faster while a provider asks). The screen's polls aren't paced by it: the
        # broker's answers are (services/pace.py).
        interval=pace.RENDER_INTERVAL_S,
        language=config.language,
        timezone=timezone(country, stop_ids),
        format=config.format,
        # The weather design is only drawn on an HD screen (models.is_hd_screen); other screens ignore it. The layout
        # is None for the screen's default; a renderer draws a layout the screen's size doesn't have as its default
        # too.
        style=EpaperStyle(
            orientation=config.orientation,
            drawing=config.style,
            weather=config.weather_design,
            layout=config.layout,
        ),
        spotify=Spotify(enabled=config.spotify_enabled),
        # Only the switch, on while the screen shows energy and its owner has a Glowmarkt login: the provider signs in
        # and fills the store, which the screen reads.
        glowmarkt=Glowmarkt(enabled=config.energy_enabled and _glowmarkt_enabled(creds)),
        # Only the switch: the GitHub token stays here, and the developer provider fills the store the screen reads.
        developer=Developer(enabled=config.developer_enabled),
        # Only the switch, like the developer panel's: the Apple token stays here, and the music provider fills the
        # store the screen reads.
        apple_music=AppleMusic(enabled=config.apple_music_enabled),
        parcels=parcels,
    )


def for_store(device: Device) -> ConfigResponse | None:
    """Return the copy of the device's config the store keeps for the render pool, or None if it keeps none.

    It is build()'s config with no pairing code (null): the code goes in its own key (services/pairing_key.py), so
    it's never in a copy that outlives it.

    None while the device isn't paired, or has a live code to show: the pool draws the pairing screen from the code's
    key then, or nothing.
    """
    if device.config is None or device.paired_at is None or pairing.live_code(device.device_id):
        return None
    return build(device.config, pairing_code=None)


def timezone(country: str | None, stop_ids: list[str]) -> str:
    """Return the screen's time zone: its postcode's country's, or, without a postcode, its first stop's."""
    if country is None and stop_ids:
        country = "NO" if stops.source_of(stop_ids[0]) == "entur" else "GB"
    return "Europe/Oslo" if country == "NO" else "Europe/London"


def _glowmarkt_enabled(creds: GlowmarktCredentials | None) -> bool:
    """Return whether there's a Glowmarkt account the provider signs in with (GlowmarktCredentials.sign_in)."""
    if creds is None:
        return False
    try:
        return creds.sign_in() is not None
    except InvalidToken:
        # The provider skips it too, so there would be nothing in the store to show.
        current_app.logger.warning("Can't decrypt the Glowmarkt password of user %s", creds.user_id)
        return False
