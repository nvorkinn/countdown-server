"""Where a touchscreen is, as its owner says on their phone during its setup (routes/setup_location.py).

The owner picks a postcode near where the phone is, or types one, and then the stops they use. From the postcode come
the touchscreen's local notices (the notice board) and its weather (the town at the postcode's position); the stops
are its arrivals. A postcode and a position say where someone lives: nothing here logs one.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from countdown_api.models import Arrivals, DepartureStop, NoticeBoard
from flask import current_app

from broker.clients import kartverket, postcodes_io
from broker.services import config_sections, stops, weather_place
from broker.services.postcodes import normalize_postcode, postcode_country

if TYPE_CHECKING:
    from broker.models import DeviceConfig

# The longest postcode taken as typed: "SW1A  1AA" with a space or two to spare (the contract's lookupPostcode).
MAX_TYPED = 10
# The most stations and bus stops offered, so the page stays short: the rest are for the settings.
MOST_STATIONS = 3
MOST_BUS_STOPS = 5
# The most of a bus stop's routes shown, the rest counted ("+3").
MOST_ROUTES = 6
# How many stops one save takes: every one a page offers, and no more.
MOST_STOPS = MOST_STATIONS + MOST_BUS_STOPS


class NotAPostcodeError(ValueError):
    """What was typed is neither a UK nor a Norwegian postcode."""


class LookupUnavailableError(Exception):
    """The postcode's source (postcodes.io, Kartverket) couldn't be asked."""


@dataclass(frozen=True)
class Place:
    """A postcode in its canonical form, and where it is."""

    postcode: str
    latitude: float
    longitude: float


def find_postcode(typed: str) -> Place | None:
    """Return where a UK or Norwegian postcode is, or None for one its source doesn't know.

    Raises NotAPostcodeError for anything that isn't one's shape, and LookupUnavailableError if its source couldn't
    be asked.
    """
    postcode = normalize_postcode(typed) if len(typed) <= MAX_TYPED else None
    if not postcode:
        raise NotAPostcodeError
    try:
        if postcode_country(postcode) == "NO":
            found = kartverket.postcode_position(postcode)
        else:
            found = postcodes_io.lookup(postcode)
            postcode = found["postcode"] if found is not None else postcode
    except (kartverket.AddressLookupUnavailableError, postcodes_io.PostcodesUnavailableError) as error:
        raise LookupUnavailableError from error
    if found is None:
        return None
    return Place(postcode=postcode, latitude=found["latitude"], longitude=found["longitude"])


def stops_near(latitude: float, longitude: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return the stations and the bus stops near a position to offer, nearest first: (stations, bus stops).

    At most MOST_STATIONS and MOST_BUS_STOPS, each as stops.near answers a stop. None of either outside TfL's area,
    or when TfL can't be asked: the step goes on without them.
    """
    try:
        near = stops.near(latitude, longitude)
    except Exception as error:  # noqa: BLE001 -- any failure is TfL's, and the step goes on without stops
        # Its type only: the error's text quotes the URL, with the position and the TfL key.
        current_app.logger.warning("Couldn't list the stops near a setup's postcode: %s", type(error).__name__)
        return [], []
    stations = [stop for stop in near if stop["mode"] == "tube"][:MOST_STATIONS]
    bus_stops = [stop for stop in near if stop["mode"] != "tube"][:MOST_BUS_STOPS]
    return stations, bus_stops


def save(config: DeviceConfig, postcode: str, place: dict[str, Any] | None, stop_ids: list[str]) -> str | None:
    """Write where the touchscreen is to its settings (not committed); return why it can't be saved, or None.

    The postcode is its notice board's. `place` is its weather's (as Open-Meteo has a place), kept as it was with
    none. The stops are its arrivals, kept as they were with none picked. Raises config_sections.SectionUnavailableError
    when the postcode can't be checked just now.
    """
    normalized = normalize_postcode(postcode)
    if not normalized:
        return "the postcode must be a UK or a Norwegian one"
    board = NoticeBoard(postcode=normalized, country=postcode_country(normalized))
    if (reason := config_sections.set_section(config, "notice_board", board)) is not None:
        return reason
    if place is not None:
        weather_place.store(config, place)
    if stop_ids:
        arrivals = Arrivals(
            stops=[DepartureStop(source=stops.source_of(stop_id), id=stop_id) for stop_id in stop_ids[:MOST_STOPS]],
            group_by_route=None,
        )
        return config_sections.set_section(config, "arrivals", arrivals)
    return None
