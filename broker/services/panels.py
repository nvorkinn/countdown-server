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
    ParcelList,
    StopDepartures,
    StoredBackground,
    StoredBankHolidays,
    StoredEnturSituations,
    StoredForecast,
    StoredLineStatus,
    StoredMetOfficeWarnings,
    StoredNetworkStatus,
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
    """Return the device's DeviceWeather: its config's place, the place's stored forecast and its backgrounds so far.

    Each is null without: no place set, or nothing stored for it (its provider is down, or hasn't got to it yet).
    """
    place = config(store, device_id).weather
    forecast = background = None
    if place.key is not None:
        names = [store_keys.weather(place.key), store_keys.background(place.key)]
        stored_forecast, stored_background = store.mget(names)
        forecast = _read(names[0], stored_forecast, StoredForecast)
        background = _read(names[1], stored_background, StoredBackground)
    return {"location": place.location, "key": place.key, "forecast": forecast, "background": background}


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
    TfL's road disruptions near it, the Met Office's warnings for it and the UK's bank holidays; for a device with
    either, every Tube line's status (a London device's Tube status panel, shown when its notice board is empty); and
    for one with parcels switched on, its parcels, as the parcel provider stored them (already narrowed to those its
    settings show). Its Entur stops' situations (Entur's disruption messages on each stop and the lines serving it)
    come whatever its postcode, as its TfL stops' do.
    """
    settings = config(store, device_id)
    board = settings.notice_board
    parcels = settings.parcels is not None and settings.parcels.enabled
    tfl_stops = [stop.id for stop in settings.arrivals.stops if stop.source == "tfl"]
    entur_stops = [stop.id for stop in settings.arrivals.stops if stop.source == "entur"]
    wanted: list[tuple[str, type[BaseModel]]] = []
    for stop_id in tfl_stops:
        wanted += [
            (store_keys.tfl_line_status(stop_id), StoredLineStatus),
            (store_keys.tfl_stations(stop_id), StoredStationDisruptions),
        ]
    wanted += [(store_keys.entur_situations(stop_id), StoredEnturSituations) for stop_id in entur_stops]
    uk = board.country == "GB" and board.postcode is not None
    if uk:
        wanted += [
            (store_keys.tfl_roads(board.postcode), StoredRoadDisruptions),
            (store_keys.met_office(board.postcode), StoredMetOfficeWarnings),
            (store_keys.BANK_HOLIDAYS, StoredBankHolidays),
        ]
    london = bool(tfl_stops) or uk
    if london:
        wanted.append((store_keys.TFL_NETWORK, StoredNetworkStatus))
    if parcels:
        wanted.append((store_keys.parcels(device_id), ParcelList))
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
        "tfl_network": None,
        "entur_stops": [{"stop_id": stop_id, "situations": next(values)} for stop_id in entur_stops],
        "parcels": None,
    }
    if uk:
        answer |= {"tfl_roads": next(values), "met_office": next(values), "bank_holidays": next(values)}
    if london:
        answer["tfl_network"] = next(values)
    if parcels:
        answer["parcels"] = next(values)
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
