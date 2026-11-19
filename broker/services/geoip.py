"""Suggests a weather location from an IP, a town or city name.

A new screen starts with the one its own IP suggests (services/registration.py); the config page prefills an empty
field with the one the visitor's IP suggests, stored only if they save it. A weather picker's current location
(routes/place_search.py) is the place near the position its browser sends, or the one its IP suggests.

The lookup is local, so no IP leaves the server: DB-IP's City Lite database places the IP, and the
suggestion is the biggest town or city near there from GeoNames, since an IP's place is rough at best
and the weather is the same across a city anyway. Both files are fetched when the image is built
(scripts/fetch_geodata.py); without them there's simply no suggestion.
"""

import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import maxminddb
from flask import current_app

# How far from where the IP places someone a bigger place can be and still be the suggestion. Inner
# London to central London is under 15 km; Coventry to Birmingham, a city in its own right, is 30.
NEARBY_KM = 25
# How far a position can be from every town or city and still be given the nearest one: out in the country, with
# none of 15,000 people within NEARBY_KM, the nearest town's weather still beats none.
FARTHEST_KM = 100


@dataclass(frozen=True)
class Located:
    """Where a suggestion is for: the town or city's name, and the position it was suggested for, if known."""

    name: str
    latitude: float | None
    longitude: float | None


@dataclass(frozen=True)
class Place:
    """A town or city from GeoNames."""

    name: str
    latitude: float
    longitude: float
    population: int


@dataclass(frozen=True)
class GeoData:
    """The IP database and the places a suggestion is picked from."""

    reader: maxminddb.Reader
    places: list[Place]


@lru_cache
def _load(directory: str) -> GeoData | None:
    path = Path(directory)
    try:
        reader = maxminddb.open_database(str(path / "dbip-city-lite.mmdb"))
        with (path / "places.tsv").open(encoding="utf-8") as f:
            places = [
                Place(name, float(lat), float(lon), int(population or 0))
                for name, lat, lon, population in (line.rstrip("\n").split("\t") for line in f)
            ]
    except OSError, ValueError, maxminddb.InvalidDatabaseError:
        current_app.logger.info("No IP location data in %s, so no weather location suggestions", directory)
        return None
    return GeoData(reader, places)


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return how far apart two positions are, in km."""
    # Equirectangular: plenty accurate over the few tens of km that matter here.
    x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    y = math.radians(lat2 - lat1)
    return 6371 * math.hypot(x, y)


def _biggest_place_near(places: list[Place], latitude: float, longitude: float) -> Place | None:
    max_degrees = NEARBY_KM / 111  # a degree of latitude is ~111 km; skips most places cheaply
    nearby = [
        p
        for p in places
        if abs(p.latitude - latitude) <= max_degrees
        and distance_km(latitude, longitude, p.latitude, p.longitude) <= NEARBY_KM
    ]
    if not nearby:
        return None
    return max(nearby, key=lambda p: p.population)


def _nearest_place(places: list[Place], latitude: float, longitude: float) -> Place | None:
    max_degrees = FARTHEST_KM / 111
    distances = [
        (distance_km(latitude, longitude, p.latitude, p.longitude), p)
        for p in places
        if abs(p.latitude - latitude) <= max_degrees
    ]
    nearest = min(distances, key=lambda pair: pair[0], default=None)
    return nearest[1] if nearest and nearest[0] <= FARTHEST_KM else None


def place_near(latitude: float, longitude: float) -> Located | None:
    """Return the town or city for a position: the biggest nearby, else the nearest; None with none, or no data."""
    data = _load(current_app.config["GEODATA_DIR"])
    if data is None:
        return None
    place = _biggest_place_near(data.places, latitude, longitude) or _nearest_place(data.places, latitude, longitude)
    return Located(place.name, latitude, longitude) if place else None


def locate_ip(ip: str | None) -> Located | None:
    """Return the town or city for where the IP is, and where that is, or None if there's no telling.

    There's none for a private IP, an IP the database doesn't know, or when there's no data.
    """
    data = _load(current_app.config["GEODATA_DIR"])
    if data is None or not ip:
        return None
    try:
        record = data.reader.get(ip)
    except ValueError:
        return None
    if not isinstance(record, dict):
        return None

    location = record.get("location")
    latitude = longitude = None
    if isinstance(location, dict):
        latitude, longitude = location.get("latitude"), location.get("longitude")
    if isinstance(latitude, int | float) and isinstance(longitude, int | float):
        place = _biggest_place_near(data.places, latitude, longitude)
        if place is not None:
            return Located(place.name, latitude, longitude)
    else:
        latitude = longitude = None
    # Somewhere with no town of 15,000 people nearby: the database's own name for it will do.
    city = record.get("city")
    names = city.get("names") if isinstance(city, dict) else None
    name = names.get("en") if isinstance(names, dict) else None
    return Located(name, latitude, longitude) if isinstance(name, str) and name else None


def suggest_weather_location(ip: str | None) -> str | None:
    """Return a town or city name for where the IP is, or None if there's no telling (locate_ip's name)."""
    located = locate_ip(ip)
    return located.name if located else None
