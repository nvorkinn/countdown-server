"""What the weather panel's backgrounds show, and when each part of the day's is due: the background provider's logic.

A place's day has four backgrounds, one per part of it (the contract's BackgroundPhase): dawn starts 30 minutes before
sunrise, day 90 minutes after it, dusk an hour before sunset and night 45 minutes after it, from the stored forecast's
sunrise and sunset. Each is made LEAD before it starts, from the forecast for that hour, so a change in the weather is
caught; dawn is a new picture, and each later one relights the one before, so the view is the same all day.

A picture's prompt is the day's scene (a place with scenes of its own, like London, goes through them a day at a time,
each with its subject up high and a calm foreground), in that part of the day's light, the season and that hour's
weather, the day's occasions (a hand-kept calendar: Halloween, Christmas, Eurovision... never the news), a UK bank
holiday, and the Met Office's warnings for the place, all kept subtle. It asks for the subject along the top, from the
middle to the right, and keeps the left third, the bottom half and the top edge calm, because the Dashboard's weather
and notices, departure boards, and tab bar and hourly forecast are laid over them.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, get_args

from countdown_api.models import BackgroundPhase, StoredBankHolidays, StoredForecast, StoredMetOfficeWarnings

PHASES: tuple[BackgroundPhase, ...] = get_args(BackgroundPhase)
# How long before its part of the day starts a picture is made: a picture takes about a minute, and the provider ticks
# every few, so it's ready in time while the forecast for its hour is fresh.
LEAD = timedelta(minutes=45)
# The curated pictures shown when no model could make one (fallback: true), one per part of the day.
FALLBACKS = Path(__file__).parent.parent / "backgrounds"
# What a relight adds before the prompt, so the model keeps the view and changes only the light and weather.
RELIGHT = (
    "Keep this exact photo's view, buildings, framing and composition; change only the time of day, light and weather "
    "to match:\n"
)


@dataclass(frozen=True)
class Place:
    """A weather key the touchscreens show, how to name it, and the postcodes their notice boards have."""

    key: str
    name: str
    country: str | None = None
    postcodes: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Day:
    """The place's local day, as its forecast has it: the date, its offset from UTC, and when each part starts."""

    local_date: date
    offset: timezone
    starts: dict[BackgroundPhase, datetime]


def fallback(phase: BackgroundPhase) -> bytes:
    """Return the curated picture for the part of the day."""
    return (FALLBACKS / f"{phase}.jpg").read_bytes()


# ------------------------------------------------------------------------------------------------------- the day


def day_of(stored: StoredForecast, now: datetime) -> Day | None:
    """Return the place's day at `now`, from its forecast; None if the forecast doesn't have that day."""
    forecast = stored.forecast
    offset = timezone(timedelta(seconds=forecast.utc_offset_seconds))
    today = now.astimezone(offset).date()
    daily = forecast.daily
    try:
        index = [str(value)[:10] for value in daily["time"]].index(today.isoformat())
        sunrise = _local(daily["sunrise"][index], offset)
        sunset = _local(daily["sunset"][index], offset)
    except KeyError, IndexError, ValueError, TypeError:
        return None
    starts: dict[BackgroundPhase, datetime] = {
        "dawn": sunrise - timedelta(minutes=30),
        "day": sunrise + timedelta(minutes=90),
        "dusk": sunset - timedelta(hours=1),
        "night": sunset + timedelta(minutes=45),
    }
    return Day(today, offset, starts)


def due(day: Day, now: datetime) -> BackgroundPhase | None:
    """Return the latest part of the day whose picture should be made by `now` (LEAD before it starts); None yet."""
    phase = None
    for candidate in PHASES:
        if now >= day.starts[candidate] - LEAD:
            phase = candidate
    return phase


def _local(value: object, offset: timezone) -> datetime:
    return datetime.fromisoformat(str(value)).replace(tzinfo=offset)


# --------------------------------------------------------------------------------------------------- the weather


@dataclass(frozen=True)
class Weather:
    """The weather a picture shows: its hour's code (WMO, as Open-Meteo reports it) and the day's figures."""

    code: int
    max_c: float | None
    wind_kmh: float | None


def weather_at(stored: StoredForecast, day: Day, phase: BackgroundPhase) -> Weather | None:
    """Return the weather an hour into the part of the day (early afternoon for the day); None without a forecast."""
    forecast = stored.forecast
    hourly, daily = forecast.hourly, forecast.daily
    at = day.starts[phase] + timedelta(hours=1)
    if phase == "day":
        at = datetime.combine(day.local_date, datetime.min.time(), day.offset).replace(hour=13)
    hour = at.strftime("%Y-%m-%dT%H")
    try:
        index = next(i for i, time in enumerate(hourly["time"]) if str(time).startswith(hour))
        code = int(hourly["weather_code"][index])
    except KeyError, IndexError, ValueError, TypeError, StopIteration:
        return None
    today = day.local_date.isoformat()
    figures: dict[str, float | None] = {"temperature_max": None, "wind_speed_max": None}
    try:
        index = [str(value)[:10] for value in daily["time"]].index(today)
        for name in figures:
            figures[name] = daily[name][index]
    except KeyError, IndexError, ValueError, TypeError:
        pass
    return Weather(code, figures["temperature_max"], figures["wind_speed_max"])


def describe_code(code: int) -> str:  # noqa: C901, PLR0911 -- a phrase per band of WMO codes
    """Return a WMO weather code in words an image model paints well."""
    if code == 0:
        return "clear sky"
    if code <= 2:  # noqa: PLR2004 -- WMO's bands
        return "mostly clear with a few fair-weather clouds"
    if code == 3:  # noqa: PLR2004
        return "grey overcast sky"
    if code <= 48:  # noqa: PLR2004
        return "fog and mist, soft muted light"
    if code <= 57:  # noqa: PLR2004
        return "light drizzle, wet surfaces"
    if code <= 65:  # noqa: PLR2004
        return "steady rain, wet reflective streets, umbrellas"
    if code <= 67:  # noqa: PLR2004
        return "freezing rain, icy glaze"
    if code <= 77:  # noqa: PLR2004
        return "falling snow, snow settling on roofs"
    if code <= 82:  # noqa: PLR2004
        return "passing heavy showers, dramatic clouds with breaks of light"
    if code <= 86:  # noqa: PLR2004
        return "snow showers"
    return "thunderstorm, dark dramatic sky"


HOT_C, BITTER_C, COLD_C, WINDY_KMH = 27, 3, 9, 45


def describe_weather(weather: Weather) -> str:
    """Return the weather in words: its hour's sky, and how hot, cold or windy the day is."""
    parts = [describe_code(weather.code)]
    if weather.max_c is not None:
        if weather.max_c >= HOT_C:
            parts.append("hot summer haze")
        elif weather.max_c <= BITTER_C:
            parts.append("bitterly cold, frosty air")
        elif weather.max_c <= COLD_C:
            parts.append("cold, crisp air")
    if weather.wind_kmh is not None and weather.wind_kmh >= WINDY_KMH:
        parts.append("strong wind, trees bending, choppy water")
    return "; ".join(parts)


# Each part of the day's light, clear and overcast (WMO 3 and above).
LIGHT: dict[BackgroundPhase, tuple[str, str]] = {
    "dawn": (
        "at sunrise: low golden light from the east, a soft pink and amber sky",
        "at first light: a faint brightening behind the clouds, cool blue-grey tones",
    ),
    "day": ("in full daylight", "in flat, even daylight"),
    "dusk": (
        "at sunset: warm low light, the sky fading to orange and violet, the first lights coming on",
        "at dusk: fading grey light, the first lights coming on",
    ),
    "night": (
        "at night: a deep navy sky, the city's lights glowing and reflected",
        "at night: low clouds lit softly from below by the city, lights reflected",
    ),
}
OVERCAST = 3
# Fog and mist (WMO 45 and 48), the one cloudy band with no clouds to see.
FOG = (45, 48)


def cloudy(code: int) -> bool:
    """Return whether a WMO weather code's sky has clouds in it: all but a clear sky and fog."""
    return code > 0 and code not in FOG


# ------------------------------------------------------------------------------------------------- the occasions


@dataclass(frozen=True)
class Occasion:
    """A day worth a touch in the picture: its name, and how it shows."""

    name: str
    look: str


EUROVISION = Occasion("the Eurovision final", "a playful sparkle of stage lights and colourful light beams in the sky")
PRIDE = Occasion("Pride in London", "rainbow flags and bunting, a joyful atmosphere")
DIWALI = Occasion("Diwali", "the warm glow of diyas and festive lights")
LUNAR_NEW_YEAR = Occasion("Lunar New Year", "red lanterns strung across the scene")

# Days that move each year by no rule worth coding: kept by hand, a year or two ahead.
MOVING: dict[date, Occasion] = {
    date(2026, 5, 16): EUROVISION,
    date(2027, 5, 15): EUROVISION,
    date(2026, 7, 4): PRIDE,
    date(2027, 7, 3): PRIDE,
    date(2026, 11, 8): DIWALI,
    date(2027, 10, 29): DIWALI,
    date(2027, 2, 6): LUNAR_NEW_YEAR,
    date(2028, 1, 26): LUNAR_NEW_YEAR,
}


def easter(year: int) -> date:
    """Return Easter Sunday (Gregorian; the anonymous algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    g = (b - (b + 8) // 25 + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741 -- the algorithm's name
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """Return the nth (from 1) `weekday` (Monday 0) of the month."""
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + (n - 1) * 7)


SUNDAY = 6
CHRISTMAS_EVE, BOXING_DAY = 24, 26
JUNE, DECEMBER = 6, 12


def occasions(day: date) -> list[Occasion]:
    """Return the day's occasions, from the calendar and MOVING."""
    year = day.year
    fixed = {
        date(year, 1, 1): Occasion("New Year's Day", "a fresh, quiet morning after the celebrations, a few streamers"),
        date(year, 1, 25): Occasion("Burns Night", "cosy warm lit windows"),
        date(year, 2, 14): Occasion("Valentine's Day", "a subtle romantic warmth, a few red and pink accents"),
        date(year, 3, 17): Occasion("St Patrick's Day", "touches of green, a festive mood"),
        easter(year) - timedelta(days=21): Occasion("Mothering Sunday", "spring flowers, soft warm light"),
        easter(year): Occasion("Easter Sunday", "spring blossom and daffodils"),
        date(year, 4, 23): Occasion("St George's Day", "a few St George flags"),
        date(year, 10, 31): Occasion("Halloween", "a few carved glowing pumpkins, an autumn mood"),
        date(year, 11, 5): Occasion("Bonfire Night", "fireworks bursting over the skyline"),
        nth_weekday(year, 11, SUNDAY, 2): Occasion("Remembrance Sunday", "red poppies, solemn and calm"),
        date(year, 12, 31): Occasion("New Year's Eve", "fireworks in the sky at midnight"),
    }
    found = [occasion for when, occasion in fixed.items() if when == day]
    if day.month == DECEMBER and CHRISTMAS_EVE <= day.day <= BOXING_DAY:
        found.append(Occasion("Christmas", "Christmas lights, a decorated tree, festive warmth"))
    elif day.month == DECEMBER and day.day < CHRISTMAS_EVE:
        found.append(Occasion("the run-up to Christmas", "strings of Christmas lights"))
    if day.month == JUNE:
        found.append(Occasion("Pride Month", "a single small rainbow flag somewhere in the scene"))
    if day in MOVING:
        found.append(MOVING[day])
    return found


# gov.uk's division of the bank holidays a place in the UK goes by: England and Wales', as the notice board's.
BANK_HOLIDAY_DIVISION = "england-and-wales"


def bank_holiday(stored: StoredBankHolidays | None, day: date) -> str | None:
    """Return the title of the UK bank holiday on `day` (England and Wales'), if it is one."""
    if stored is None or (division := stored.divisions.get(BANK_HOLIDAY_DIVISION)) is None:
        return None
    return next((event.title for event in division.events if event.date == day), None)


def warnings(stored: list[StoredMetOfficeWarnings]) -> list[str]:
    """Return the Met Office's warnings in brief ("yellow warning of snow"), each once, from the places' feeds."""
    found: dict[str, None] = {}
    for feed in stored:
        for item in feed.items:
            title = item.title.split(" affecting ")[0].strip()
            if title:
                found[title[0].lower() + title[1:]] = None
    return list(found)


# --------------------------------------------------------------------------------------------------- the prompt


@dataclass(frozen=True)
class Scene:
    """A view a place's pictures show: its subject, kept high up, and the calm foreground below it."""

    subject: str
    foreground: str


# London's scenes, one a day in turn (scene_of): each has its subject up high and a naturally calm, plain foreground for
# the departure boards to sit on. Left to itself the model paints the Houses of Parliament almost every time. Neighbours
# in the list are unlike each other (river, park, aerial), so two days running look different.
LONDON_SCENES = (
    Scene(
        "Tower Bridge, with the towers of the City rising behind it, seen from far downstream",
        "the wide, calm river Thames",
    ),
    Scene(
        "the view from the top of Greenwich Park: the Old Royal Naval College's twin domes and the river below, "
        "Canary Wharf's towers beyond",
        "the park's broad, open lawn sloping away, smooth and plain",
    ),
    Scene(
        "the trees and rooftops along the far shore of the Serpentine in Hyde Park, Knightsbridge's buildings "
        "rising above them",
        "the lake's open, still water",
    ),
    Scene(
        "an aerial view from high over the river of the City of London's towers: the Gherkin, the Walkie-Talkie, "
        "the Cheesegrater and their glass neighbours, St Paul's dome among them",
        "the river and the low, dark streets beneath, simple and in shadow",
    ),
    Scene(
        "the Houses of Parliament and Elizabeth Tower, seen from the far bank of the Thames",
        "the wide, calm river",
    ),
    Scene(
        "the view from the top of Primrose Hill: London's skyline along the horizon, from the BT Tower to the Shard",
        "the hill's open grass sloping down, smooth and plain",
    ),
    Scene(
        "Regent's Canal at King's Cross: the old gasholder frames and canal-side buildings rising on the far side",
        "the canal's still, dark water",
    ),
    Scene(
        "St Paul's Cathedral's dome rising above the north bank, seen across the Thames from Bankside",
        "the wide, calm river",
    ),
)
# Oslo's, the same way: neighbours alternate water, park and hillside, so two days running look different.
OSLO_SCENES = (
    Scene(
        "the Oslo Opera House, its white sloping roof rising out of the fjord, the Barcode towers behind it, seen "
        "from across the water",
        "the fjord's wide, calm water",
    ),
    Scene(
        "the Monolith on its terrace at the top of Vigeland Park, with Gustav Vigeland's granite figures around it",
        "the park's broad, open lawn, smooth and plain",
    ),
    Scene(
        "Oslo City Hall's twin brick towers above the harbour, seen from out on the water of Pipervika",
        "the harbour's wide, calm water",
    ),
    Scene(
        "the Holmenkollen ski jump on its wooded ridge high above the city, seen from far below",
        "the open, smooth slope of the hillside below it",
    ),
    Scene(
        "the Storting, Norway's parliament, its yellow-brick front and rounded hall, seen across Eidsvolls plass",
        "the square's open, plain paving",
    ),
    Scene(
        "the view from Ekeberg hill over the city and the fjord: the Opera House, Bjørvika's towers and the hills "
        "beyond",
        "the hill's open grass sloping down, smooth and plain",
    ),
    Scene(
        "Akershus Fortress's walls and towers on the headland, seen across the harbour",
        "the harbour's wide, calm water",
    ),
    Scene(
        "the Royal Palace at the top of its park, at the head of Karl Johans gate",
        "the palace park's open, plain lawn",
    ),
)
SCENES: dict[str, tuple[Scene, ...]] = {"london-gb-2643743": LONDON_SCENES, "oslo-no-3143244": OSLO_SCENES}


def scene_of(place: Place, day: date) -> Scene:
    """Return the scene the place's pictures show on `day`: its own scenes in turn, a day each, or its everyday view.

    By date, not part of the day, so a day's later pictures relight the same scene.
    """
    if scenes := SCENES.get(place.key):
        return scenes[day.toordinal() % len(scenes)]
    return Scene(
        f"a recognisable everyday view of {place.name}, as people who live there know it",
        "open water, grass or a plain square",
    )


# The season by month, as the places are all in the northern hemisphere.
SEASONS = {
    1: "winter",
    2: "late winter",
    3: "early spring",
    4: "spring",
    5: "late spring",
    6: "early summer",
    7: "summer",
    8: "late summer",
    9: "early autumn",
    10: "autumn",
    11: "late autumn",
    12: "early winter",
}


@dataclass(frozen=True)
class Context:
    """Everything a picture's prompt is made from, besides the place and part of the day."""

    local_date: date
    weather: Weather
    occasions: list[Occasion]
    bank_holiday: str | None = None
    warnings: list[str] = field(default_factory=list)


def prompt(place: Place, phase: BackgroundPhase, context: Context) -> str:
    """Return the prompt for the place's picture for the part of the day."""
    clear, overcast = LIGHT[phase]
    light = overcast if context.weather.code >= OVERCAST else clear
    when = f"{SEASONS[context.local_date.month]} ({context.local_date.strftime('%B')})"
    view = scene_of(place, context.local_date)
    # Named only when the forecast has clouds: a model draws what a prompt names, so a clear sky would grow some.
    clouds = ", its clouds catching the light" if cloudy(context.weather.code) else ""
    lines = [
        f"A wide, photographic, landscape-format image of {view.subject}, in {when}, {light}.",
        f"Weather: {describe_weather(context.weather)}.",
    ]
    lines += [f"It is {occasion.name}: {occasion.look}." for occasion in context.occasions]
    holiday = context.bank_holiday
    if holiday and not any(occasion.name in holiday for occasion in context.occasions):
        lines.append(f"It is a bank holiday ({holiday}): relaxed, people out enjoying a day off.")
    lines += [f"Today there is a {warning}; show it only through the weather and mood." for warning in context.warnings]
    lines += [
        (
            "Keep any seasonal or festive touches subtle and understated, an everyday photo of the day rather than a "
            "poster, and only up near the main subject."
        ),
        (
            "Composition: the picture fills a wide screen, with text laid over its left side, its bottom half and its "
            "top edge, so:"
        ),
        (
            "- The very top of the picture, about its top fifth, is a band of plain sky: even the tallest tower or "
            "spire stays below it, with nothing reaching up into it."
        ),
        (
            "- The main subject sits high in the picture, in its upper half just under that band of sky, anywhere from "
            "the centre to the right. It can be smaller and further away rather than fill the frame."
        ),
        (
            "- The left third, from top to bottom, is calm and simple: open sky above, the foreground below, with no "
            "landmarks, buildings, trees, people, birds, fireworks, decorations or bright lights in it."
        ),
        (
            f"- The bottom half is {view.foreground}: calm, even and low in detail across the whole width, with "
            "nothing close up in it (no boats, people, railings, benches or objects)."
        ),
        (
            "A little more dramatic than an everyday snapshot: a rich, colourful sky true to the weather above"
            f"{clouds}, higher contrast and deeper shadows, the lit subject glowing against its darker surroundings. "
            "Keep it believable rather than overdone. The calm areas stay darker and even so white text over them "
            "stays readable. Realistic colours, high detail in the subject only."
        ),
        "Absolutely no text, letters, numbers, signs, logos, watermarks, borders or user interface.",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------- what to make next

# How long a fallback picture stands before the provider tries the model again for the same part of the day.
RETRY = timedelta(minutes=30)

Action = Literal["make", "keep"]


def next_step(images: list[dict[str, Any]], phase: BackgroundPhase, now: datetime) -> Action:
    """Return whether the part of the day's picture still has to be made, given today's `images` so far (stored JSON).

    Made already: keep it, unless it's a fallback that has stood for RETRY.
    """
    made = next((image for image in images if image["phase"] == phase), None)
    if made is None:
        return "make"
    if made["fallback"] and now - datetime.fromisoformat(made["made_at"]) >= RETRY:
        return "make"
    return "keep"


def reference(images: list[dict[str, Any]], phase: BackgroundPhase) -> dict[str, Any] | None:
    """Return the picture a part of the day relights: the latest made one (not a fallback) before it; None for dawn."""
    if phase == "dawn":
        return None
    earlier = PHASES[: PHASES.index(phase)]
    made = [image for image in images if image["phase"] in earlier and not image["fallback"]]
    return made[-1] if made else None


def with_image(images: list[dict[str, Any]], image: dict[str, Any]) -> list[dict[str, Any]]:
    """Return today's images with `image` in its part of the day's place (replacing a fallback), by `starts_at`."""
    kept = [existing for existing in images if existing["phase"] != image["phase"]]
    return sorted([*kept, image], key=lambda each: datetime.fromisoformat(each["starts_at"]))


def utc_now() -> datetime:
    """Return the time now, in UTC."""
    return datetime.now(UTC)


# Rounded down to the minute, as the contract's example has them.
def iso_minute(moment: datetime) -> str:
    """Return `moment` to the minute, with its offset ("2026-01-15T07:31:00+00:00")."""
    return moment.replace(second=0, microsecond=0).isoformat()


def iso_utc(moment: datetime) -> str:
    """Return `moment` in UTC to the second, with a Z ("2026-01-15T06:46:12Z")."""
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
