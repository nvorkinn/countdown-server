"""A device's weather, arrivals and notices, from the store alone: what a touchscreen draws its panels from.

The render pool draws an e-paper screen's panels from what countdown's providers keep in Redis (the store contract's
departures, weather and notices keys); a touchscreen draws its own, so the broker reads it the same values
(routes/devices.py: getWeather, getArrivals, getNotices). Everything comes from the store: the device's config is the
one the broker published there (`v1:config:<device id>`, so a device that isn't paired has none), and each panel's
data is its provider's value, served as stored. A value the contract's model won't read is logged and left out
(null), as a missing one is: its provider is down, or hasn't got to it yet, or countdown wrote a shape the contract
doesn't have.
"""

import json
from typing import TYPE_CHECKING, Any

from countdown_api import keys as store_keys
from countdown_api.models import (
    ConfigResponse,
    StopDepartures,
    StoredBankHolidays,
    StoredForecast,
    StoredLineStatus,
    StoredMetOfficeWarnings,
    StoredRoadDisruptions,
    StoredStationDisruptions,
)
from flask import current_app
from pydantic import BaseModel, TypeAdapter, ValidationError

if TYPE_CHECKING:
    from redis import Redis

CONFIGS: TypeAdapter[ConfigResponse] = TypeAdapter(ConfigResponse)


class NotPairedError(Exception):
    """The device has no config in the store: it isn't paired yet (or the store lost it until the next publish)."""


def config(store: Redis, device_id: str) -> ConfigResponse:
    """Return the device's config as the broker published it; NotPairedError with none. Raises RedisError."""
    value = store.get(store_keys.config(device_id))
    if value is None:
        raise NotPairedError(device_id)
    return CONFIGS.validate_json(value)


def weather(store: Redis, device_id: str) -> dict[str, Any]:
    """Return the device's DeviceWeather: its config's place and the place's stored forecast (null without)."""
    place = config(store, device_id).weather
    forecast = None
    if place.key is not None:
        forecast = _read(store_keys.weather(place.key), store.get(store_keys.weather(place.key)), StoredForecast)
    return {"location": place.location, "key": place.key, "forecast": forecast}


def arrivals(store: Redis, device_id: str) -> dict[str, Any]:
    """Return the device's DeviceArrivals: each of its stops, in the owner's order, with its stored departures."""
    stops = config(store, device_id).arrivals.stops
    names = [store_keys.departures(stop.id) for stop in stops]
    values = store.mget(names) if names else []
    return {
        "stops": [
            {"source": stop.source, "id": stop.id, "departures": _read(name, value, StopDepartures)}
            for stop, name, value in zip(stops, names, values, strict=True)
        ]
    }


def notices(store: Redis, device_id: str) -> dict[str, Any]:
    """Return the device's DeviceNotices: what the store has for its TfL stops and, for a UK postcode, its place.

    Its TfL stops' line statuses and station disruptions; then, only for a UK postcode (the sources are the UK's),
    TfL's road disruptions near it, the Met Office's warnings for it and the UK's bank holidays.
    """
    settings = config(store, device_id)
    board = settings.notice_board
    tfl_stops = [stop.id for stop in settings.arrivals.stops if stop.source == "tfl"]
    wanted: list[tuple[str, type[BaseModel]]] = []
    for stop_id in tfl_stops:
        wanted += [
            (store_keys.tfl_line_status(stop_id), StoredLineStatus),
            (store_keys.tfl_stations(stop_id), StoredStationDisruptions),
        ]
    uk = board.country == "GB" and board.postcode is not None
    if uk:
        wanted += [
            (store_keys.tfl_roads(board.postcode), StoredRoadDisruptions),
            (store_keys.met_office(board.postcode), StoredMetOfficeWarnings),
            (store_keys.BANK_HOLIDAYS, StoredBankHolidays),
        ]
    names = [name for name, _ in wanted]
    values = iter(
        _read(name, value, model)
        for (name, model), value in zip(wanted, store.mget(names) if names else [], strict=True)
    )
    answer: dict[str, Any] = {
        "postcode": board.postcode,
        "country": board.country,
        "tfl_stops": [{"stop_id": stop_id, "lines": next(values), "stations": next(values)} for stop_id in tfl_stops],
        "tfl_roads": None,
        "met_office": None,
        "bank_holidays": None,
    }
    if uk:
        answer |= {"tfl_roads": next(values), "met_office": next(values), "bank_holidays": next(values)}
    return answer


def _read(name: str, value: str | bytes | None, model: type[BaseModel]) -> Any:  # noqa: ANN401 -- the stored JSON
    """Return a stored value's JSON as stored, if the contract's `model` reads it; None if it's missing or doesn't."""
    if value is None:
        return None
    try:
        model.model_validate_json(value)
    except ValidationError as error:
        # The key and how many errors, not the value: a notice's text can name the device's place.
        current_app.logger.warning("%s isn't the contract's %s (%d errors)", name, model.__name__, error.error_count())
        return None
    return json.loads(value)
