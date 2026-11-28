"""The location step of a touchscreen's setup, on its owner's phone (/device/<id>/setup/location).

For a touchscreen that reports on its setup (services/touchscreen_setup.py), at the step where its owner says where it
is. The phone finds its own position (static/touchscreen_location.js) and posts it here, so nothing is asked of Google;
the pages are plain forms from there:

1. the start: use this phone's location, type a postcode, or skip;
2. the postcodes nearest the phone, to pick one from (`near`), or a box to type one in (`type_postcode`);
3. the stations and bus stops near the postcode, to tick (`postcode`);
4. the save (`save`), which ends the step, and says what was set.

A position is posted, never part of an address, and neither it nor a postcode is logged. Anywhere but at that step, each
of these goes back to the setup's own address, which sends the phone wherever is next.
"""

import math
import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from flask import Blueprint, Response, current_app, g, redirect, render_template, request, url_for

from broker.auth import require_paired_user
from broker.clients import postcodes_io, weather
from broker.db import db
from broker.models import Device, DeviceConfig
from broker.routes.location import MAX_LATITUDE, MAX_LONGITUDE, _postcodes_near
from broker.routes.pages import _contract_errors, _publish_saved
from broker.routes.place_search import _same_place
from broker.services import config_sections, geoip, setup_location, touchscreen_setup

if TYPE_CHECKING:
    from flask.typing import ResponseReturnValue

bp = Blueprint("setup_location", __name__, url_prefix="/device/<device_id>/setup/location")

# TfL's lines' colours, as the touchscreen draws them: (background, text) by the line's name in lower case.
OVERGROUND = ("#ee7c0e", "#fff")
LINE_COLOURS = {
    "bakerloo": ("#a45a2a", "#fff"),
    "central": ("#e32017", "#fff"),
    "circle": ("#ffd300", "#000"),
    "district": ("#00782a", "#fff"),
    "dlr": ("#00a4a7", "#fff"),
    "elizabeth line": ("#6950a1", "#fff"),
    "hammersmith & city": ("#f3a9bb", "#000"),
    "jubilee": ("#a0a5a9", "#000"),
    "liberty": OVERGROUND,
    "lioness": OVERGROUND,
    "metropolitan": ("#9b0056", "#fff"),
    "mildmay": OVERGROUND,
    "northern": ("#111", "#fff"),
    "piccadilly": ("#003688", "#fff"),
    "suffragette": OVERGROUND,
    "victoria": ("#0098d4", "#fff"),
    "waterloo & city": ("#95cdba", "#000"),
    "weaver": OVERGROUND,
    "windrush": OVERGROUND,
}
FALLBACK_LINE_COLOURS = ("#3a4252", "#fff")
# A station's name without what every station's has.
_STATION_SUFFIX = re.compile(r" (Underground|DLR|Rail) Station$")
KILOMETRE = 1000

UNKNOWN = "Hmm, we couldn't find that postcode."
FAILED = "Something went wrong there. Try again?"
NOT_ONE = "That doesn't look like a UK or Norwegian postcode."


def _at_step(view: Any) -> Any:  # noqa: ANN401 -- a view, wrapped
    """Let the view run only for one of the person's touchscreens whose setup is at the location step.

    Any other request goes to the setup's own address, which sends the phone wherever is next (or says the screen is
    ready).
    """

    def wrapped() -> ResponseReturnValue:
        device = db.session.get_one(Device, g.device_id)
        if device.setup is None or touchscreen_setup.step_of(device) != "location":
            return redirect(url_for("pages.phone_setup", device_id=g.device_id), HTTPStatus.SEE_OTHER)
        return view()

    wrapped.__name__ = view.__name__
    wrapped.__doc__ = view.__doc__
    return require_paired_user(wrapped)


def _page(view: str, **context: Any) -> Response:  # noqa: ANN401 -- whatever the view shows
    response = current_app.make_response(
        render_template("touchscreen_location.html", view=view, device_id=g.device_id, **context)
    )
    # Where someone lives: nothing keeps the page.
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("")
@_at_step
def start() -> ResponseReturnValue:
    """Ask how to say where the touchscreen is: by this phone's location, by a typed postcode, or not now."""
    return _page("start")


@bp.post("/near")
@_at_step
def near() -> ResponseReturnValue:
    """Offer the postcodes nearest the position the phone found for itself, or the box to type one in with none.

    The form posts `latitude`, `longitude` and `accuracy` as the phone's browser gave them. Without a position, or
    with no postcode near it (outside the UK, say), or with postcodes.io not answering, the owner types theirs.
    """
    position = _position(request.form.get("latitude"), request.form.get("longitude"))
    if position is None:
        return _page("type", note="We couldn't get this phone's location. Type your postcode instead.")
    accuracy = _number(request.form.get("accuracy")) or 0
    try:
        found = _postcodes_near(*position, max(accuracy, 0))
    except postcodes_io.PostcodesUnavailableError:
        found = []
    if not found:
        return _page("type", note="We couldn't find a postcode near you. Type it in instead.")
    # The nearest as the best guess, the rest in a grid, which reads best in order.
    return _page("postcodes", best=found[0]["postcode"], others=sorted(postcode["postcode"] for postcode in found[1:]))


@bp.get("/type")
@_at_step
def type_postcode() -> ResponseReturnValue:
    """Show the box to type a postcode in; with ?located=no, say the phone's location couldn't be had."""
    note = "We couldn't get this phone's location. Type your postcode instead." if "located" in request.args else None
    return _page("type", note=note)


@bp.post("/postcode")
@_at_step
def postcode() -> ResponseReturnValue:
    """Take the postcode picked or typed, and offer the stops near it; with none near, save it and end the step."""
    typed = request.form.get("postcode", "").strip()
    try:
        place = setup_location.find_postcode(typed)
    except setup_location.NotAPostcodeError:
        return _page("type", typed=typed, error=NOT_ONE)
    except setup_location.LookupUnavailableError:
        return _page("type", typed=typed, error=FAILED)
    if place is None:
        return _page("type", typed=typed, error=UNKNOWN)
    stations, bus_stops = setup_location.stops_near(place.latitude, place.longitude)
    if not stations and not bus_stops:
        return _save(place.postcode, (place.latitude, place.longitude), [])
    return _page(
        "stops",
        place=place,
        stations=[_station(stop) for stop in stations],
        bus_stops=[_bus_stop(stop) for stop in bus_stops],
    )


@bp.post("/save")
@_at_step
def save() -> ResponseReturnValue:
    """Save the postcode, the weather's place at it and the stops ticked, which ends the step, and say what was set."""
    position = _position(request.form.get("latitude"), request.form.get("longitude"))
    return _save(request.form.get("postcode", ""), position, request.form.getlist("stop"))


@bp.post("/skip")
@require_paired_user
def skip() -> ResponseReturnValue:
    """Leave where the touchscreen is for later, in its settings, and go on with its setup."""
    touchscreen_setup.location_done(g.device_id)
    return redirect(url_for("pages.phone_setup", device_id=g.device_id), HTTPStatus.SEE_OTHER)


def _save(postcode: str, position: tuple[float, float] | None, stop_ids: list[str]) -> ResponseReturnValue:
    """Save where the touchscreen is and end the step; back to the typing box, with why, if it can't be saved."""
    config = db.session.get_one(DeviceConfig, g.device_id)
    # The weather is the town's at the postcode's position. Not found, or not to be had just now, it stays as it was.
    place = _weather_place(*position) if position is not None else None
    try:
        # Refused, it isn't a postcode after all: the form's was changed on its way here.
        problem = NOT_ONE if setup_location.save(config, postcode, place, stop_ids) is not None else None
    except config_sections.SectionUnavailableError:
        problem = FAILED
    if problem is None and _contract_errors(config):
        problem = FAILED
    if problem is not None:
        db.session.rollback()
        return _page("type", typed=postcode, error=problem)
    saved_postcode, saved_stops = config.postcode, len(stop_ids[: setup_location.MOST_STOPS])
    db.session.commit()
    _publish_saved(g.device_id, turned=False)
    touchscreen_setup.location_done(g.device_id)
    return _page("done", postcode=saved_postcode, place=place["name"] if place else None, stop_count=saved_stops)


def _weather_place(latitude: float, longitude: float) -> dict[str, Any] | None:
    """Return the town or city at a position as Open-Meteo has it, for the weather; None with none, or no answer."""
    located = geoip.place_near(latitude, longitude)
    if located is None:
        return None
    try:
        places = weather.search_places(located.name)
    except weather.GeocodingUnavailableError:
        return None
    return _same_place(places, located)


def _number(value: str | None) -> float | None:
    try:
        number = float(value or "")
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _position(latitude: str | None, longitude: str | None) -> tuple[float, float] | None:
    """Return a posted latitude and longitude as a position, or None if they aren't one."""
    lat, lon = _number(latitude), _number(longitude)
    if lat is None or lon is None or abs(lat) > MAX_LATITUDE or abs(lon) > MAX_LONGITUDE:
        return None
    return lat, lon


def _distance(metres: float) -> str:
    """Return a distance as the stops show it: "140 m", or "1.2 km" from a kilometre on."""
    if metres >= KILOMETRE:
        return f"{metres / KILOMETRE:.1f} km"
    return f"{round(metres / 10) * 10} m"


def _station(stop: dict[str, Any]) -> dict[str, Any]:
    """Return what a station's card shows: its name without "Underground Station", its lines in TfL's colours."""
    return {
        "id": stop["id"],
        "name": _STATION_SUFFIX.sub("", stop["name"]),
        "distance": _distance(stop["distance"]),
        "lines": [
            {"name": line, "colours": LINE_COLOURS.get(line.lower(), FALLBACK_LINE_COLOURS)}
            for line in stop.get("lines", [])
        ],
    }


def _bus_stop(stop: dict[str, Any]) -> dict[str, Any]:
    """Return what a bus stop's card shows: its name and letter, and its first routes with the rest counted."""
    routes = stop.get("lines", [])
    return {
        "id": stop["id"],
        "name": stop["name"],
        "letter": stop.get("letter", ""),
        "distance": _distance(stop["distance"]),
        "routes": routes[: setup_location.MOST_ROUTES],
        "more_routes": max(0, len(routes) - setup_location.MOST_ROUTES),
    }
