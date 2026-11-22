"""A device's weather, arrivals and notices, from the store alone: what a touchscreen draws its panels from.

The render pool draws an e-paper screen's panels from what countdown's providers keep in Redis (the store contract's
departures, weather and notices keys); a touchscreen draws its own, so the broker reads it the same values
(routes/devices.py: getWeather, getArrivals, getNotices). Everything comes from the store: the device's config is the
one the broker published there (`v1:config:<device id>`, so a device that isn't paired has none), and each panel's
data is its provider's value, served as stored (MET Norway's warnings, stored for all of Norway, narrowed to those
covering the device's place, as countdown's notice board narrows them). A value the contract's model won't read is
logged and left out (null), as a missing one is: its provider is down, or hasn't got to it yet, or countdown wrote a
shape the contract doesn't have.
"""

import itertools
import json
from typing import TYPE_CHECKING, Any

from countdown_api import keys as store_keys
from countdown_api.models import (
    ConfigResponse,
    ParcelList,
    StopDepartures,
    StoredBackground,
    StoredBankHolidays,
    StoredBinCollections,
    StoredEnturSituations,
    StoredFloods,
    StoredForecast,
    StoredLineStatus,
    StoredLocation,
    StoredMetAlerts,
    StoredMetOfficeWarnings,
    StoredNetworkStatus,
    StoredRoadDisruptions,
    StoredStationDisruptions,
)
from flask import current_app
from pydantic import BaseModel, TypeAdapter, ValidationError

from broker.services.household_address import member_of

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
    """Return the device's DeviceNotices: the stored values for its stops and, for a UK or Norwegian postcode, its area.

    Its TfL stops' line statuses and station disruptions; then, only for a UK postcode (the sources are the UK's),
    TfL's road disruptions near it, the Environment Agency's flood warnings near it, the Met Office's warnings for it
    and the UK's bank holidays; only for a Norwegian postcode, MET Norway's weather warnings whose area covers where it
    is (its `v1:location:` place, as provider-locations looked it up; null until then) and, with an address set, its
    household's bin collection days (as the bins provider stored them; null until then); for a device with either,
    every Tube line's status (a London device's Tube status panel, shown when its notice board is empty); and
    for one with parcels switched on, its parcels, as the parcel provider stored them (already narrowed to those its
    settings show). Its Entur stops' situations (Entur's disruption messages on each stop and the lines serving it)
    come whatever its postcode, as its TfL stops' do.
    """
    settings = config(store, device_id)
    board = settings.notice_board
    tfl_stops = [stop.id for stop in settings.arrivals.stops if stop.source == "tfl"]
    entur_stops = [stop.id for stop in settings.arrivals.stops if stop.source == "entur"]
    stop_keys: list[tuple[str, type[BaseModel]]] = []
    for stop_id in tfl_stops:
        stop_keys += [
            (store_keys.tfl_line_status(stop_id), StoredLineStatus),
            (store_keys.tfl_stations(stop_id), StoredStationDisruptions),
        ]
    stop_keys += [(store_keys.entur_situations(stop_id), StoredEnturSituations) for stop_id in entur_stops]
    area = _area_keys(settings, device_id, has_tfl_stops=bool(tfl_stops))
    wanted = stop_keys + [(name, model) for _, name, model in area]
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
        "floods": None,
        "met_office": None,
        "bank_holidays": None,
        "tfl_network": None,
        "entur_stops": [{"stop_id": stop_id, "situations": next(values)} for stop_id in entur_stops],
        "metalerts": None,
        "parcels": None,
        "bins": None,
    }
    found = {field: next(values) for field, _, _ in area}
    location = found.pop("location", None)
    if "metalerts" in found:
        found["metalerts"] = _covering(found["metalerts"], location)
    return answer | found


def _area_keys(
    settings: ConfigResponse, device_id: str, *, has_tfl_stops: bool
) -> list[tuple[str, str, type[BaseModel]]]:
    """Return the keys for the device's place and parcels, each with the notices' field it fills (`location` none).

    Only for a UK postcode, its UK sources; only for a Norwegian one, its location and MET Norway's warnings and, with
    an address, the household's bins (the key is the SHA1 of its v1:sub:address member, so it names no home in a log);
    the Tube's status with TfL stops or a UK postcode; and the device's parcels while they're switched on.
    """
    board = settings.notice_board
    keys: list[tuple[str, str, type[BaseModel]]] = []
    uk = board.country == "GB" and board.postcode is not None
    if uk:
        keys += [
            ("tfl_roads", store_keys.tfl_roads(board.postcode), StoredRoadDisruptions),
            ("floods", store_keys.floods(board.postcode), StoredFloods),
            ("met_office", store_keys.met_office(board.postcode), StoredMetOfficeWarnings),
            ("bank_holidays", store_keys.BANK_HOLIDAYS, StoredBankHolidays),
        ]
    if board.country == "NO" and board.postcode is not None:
        keys += [
            ("location", store_keys.location(board.postcode), StoredLocation),
            ("metalerts", store_keys.METALERTS, StoredMetAlerts),
        ]
        if board.address is not None:
            keys.append(("bins", store_keys.bins(member_of(board.address, board.postcode)), StoredBinCollections))
    if has_tfl_stops or uk:
        keys.append(("tfl_network", store_keys.TFL_NETWORK, StoredNetworkStatus))
    if settings.parcels is not None and settings.parcels.enabled:
        keys.append(("parcels", store_keys.parcels(device_id), ParcelList))
    return keys


def _covering(metalerts: dict[str, Any] | None, location: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the stored MetAlerts with only the warnings covering the location, in both languages; None without either.

    As countdown's notice board picks out its place's (renderer notices/norway.py `warnings_at`): what MET answers when
    asked for the place itself, with the location at four decimals. A postcode Kartverket doesn't know has no location.
    """
    place = (location or {}).get("location")
    if metalerts is None or place is None:
        return None
    latitude, longitude = round(place["latitude"], 4), round(place["longitude"], 4)
    return {
        **metalerts,
        **{
            lang: {
                **metalerts[lang],
                "features": [
                    feature
                    for feature in metalerts[lang].get("features") or []
                    if covers(feature.get("geometry"), latitude, longitude)
                ],
            }
            for lang in ("no", "en")
        },
    }


def covers(geometry: dict[str, Any] | None, latitude: float, longitude: float) -> bool:
    """Whether a warning's area covers a point: its GeoJSON geometry, a Polygon or MultiPolygon (longitude first).

    A warning without one covers nowhere: MET gives every warning its area.
    """
    if not geometry:
        return False
    coordinates = geometry.get("coordinates") or []
    match geometry.get("type"):
        case "Polygon":
            polygons = [coordinates]
        case "MultiPolygon":
            polygons = coordinates
        case _:
            return False
    return any(_inside(polygon, longitude, latitude) for polygon in polygons)


def _inside(polygon: list[Any], longitude: float, latitude: float) -> bool:
    """Whether a point is in a GeoJSON polygon: in its outer ring and none of its holes.

    By counting the edges of every ring that a ray from the point crosses (even-odd), so a point in a hole is out.
    """
    inside = False
    for ring in polygon:
        points = [(point[0], point[1]) for point in ring]  # leaving out any altitude
        if not points:
            continue
        for (x1, y1), (x2, y2) in itertools.pairwise([*points, points[0]]):
            if (y1 > latitude) != (y2 > latitude) and longitude < x1 + (latitude - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
    return inside


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
