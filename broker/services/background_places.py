"""The places the weather panel's backgrounds are made for: those the paired touchscreens show.

The background provider makes a picture for each (providers/background.py), and the broker publishes them to the store
with the subscriptions (routes/pages.py): their weather keys as `v1:sub:background`, and beside each member what its
key doesn't say, as `v1:background-place:<weather key>` (the store contract's BackgroundPlace). An e-paper screen draws
no background, so its place isn't one, and nor is that of a touchscreen nobody has paired.
"""

import re

from countdown_api.models import BackgroundPlace
from sqlalchemy import select

from broker.db import db
from broker.models import Device, DeviceConfig
from broker.services import weather_place
from broker.services.backgrounds import Place

# A country code as Open-Meteo gives it, in either case; the contract's is in capitals.
_COUNTRY = re.compile(r"[A-Za-z]{2}")


def places() -> list[Place]:
    """Return the places the paired touchscreens show, each with the postcodes of their notice boards."""
    rows = db.session.execute(
        select(DeviceConfig)
        .join(Device, Device.device_id == DeviceConfig.device_id)
        .where(Device.type == "touchscreen", Device.paired_at.is_not(None), Device.display_secret_hash.is_not(None))
        .order_by(DeviceConfig.device_id)
    ).scalars()
    found: dict[str, Place] = {}
    for config in rows:
        key = weather_place.weather_key(config)
        if key is None:
            continue
        place = found.get(key) or Place(
            key=key,
            name=config.weather_place_label or config.weather_place_name or config.weather_location,
            country=config.weather_country_code,
        )
        if config.postcode:
            place = Place(place.key, place.name, place.country, place.postcodes | {config.postcode})
        found[key] = place
    return list(found.values())


def stored(place: Place) -> str | None:
    """Return the place as the store has it under v1:background-place:<key> (the contract's BackgroundPlace, json).

    None for a place with nothing to call it, which the contract leaves out: its key is still a member of the set.
    """
    if not place.name:
        return None
    country = place.country.upper() if place.country and _COUNTRY.fullmatch(place.country) else None
    return BackgroundPlace(name=place.name, country=country, postcodes=sorted(place.postcodes)).model_dump_json()
