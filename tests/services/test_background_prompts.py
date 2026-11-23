"""The background provider's logic (broker/services/backgrounds.py): the parts of the day, the weather, the prompt."""

from datetime import UTC, date, datetime, timedelta

import pytest
from countdown_api import fixtures
from countdown_api.models import StoredBankHolidays, StoredForecast, StoredMetOfficeWarnings

from broker.services import backgrounds
from broker.services.backgrounds import Context, Occasion, Place, Weather

FORECAST = StoredForecast.model_validate(fixtures.load("weather.json")["value"])
LONDON = Place("london-gb-2643743", "London", "GB")
OSLO = Place("oslo-no-3143244", "Oslo", "NO")
# The example's first day, at noon: its sunrise is 07:58 and sunset 16:22.
NOON = datetime(2026, 1, 15, 12, tzinfo=UTC)


def _at(hour: int, minute: int = 0) -> datetime:
    return NOON.replace(hour=hour, minute=minute)


def test_the_parts_of_the_day_start_from_sunrise_and_sunset():
    day = backgrounds.day_of(FORECAST, NOON)

    assert day is not None
    assert day.local_date == date(2026, 1, 15)
    sunrise = datetime.fromisoformat(FORECAST.forecast.daily["sunrise"][0]).replace(tzinfo=UTC)
    sunset = datetime.fromisoformat(FORECAST.forecast.daily["sunset"][0]).replace(tzinfo=UTC)
    assert day.starts == {
        "dawn": sunrise - timedelta(minutes=30),
        "day": sunrise + timedelta(minutes=90),
        "dusk": sunset - timedelta(hours=1),
        "night": sunset + timedelta(minutes=45),
    }


def test_a_day_the_forecast_lacks_has_none():
    assert backgrounds.day_of(FORECAST, datetime(2027, 1, 1, tzinfo=UTC)) is None


def test_the_local_date_is_the_places_own():
    """A place ahead of UTC is on the next day while UTC is still on the day before."""
    ahead = FORECAST.model_copy(
        update={"forecast": FORECAST.forecast.model_copy(update={"utc_offset_seconds": 3600 * 10})}
    )

    day = backgrounds.day_of(ahead, datetime(2026, 1, 15, 20, tzinfo=UTC))

    assert day is not None
    assert day.local_date == date(2026, 1, 16)


def test_each_part_is_due_45_minutes_before_it_starts():
    day = backgrounds.day_of(FORECAST, NOON)
    assert day is not None

    assert backgrounds.due(day, day.starts["dawn"] - timedelta(minutes=46)) is None
    assert backgrounds.due(day, day.starts["dawn"] - timedelta(minutes=45)) == "dawn"
    assert backgrounds.due(day, day.starts["day"] - timedelta(minutes=44)) == "day"
    assert backgrounds.due(day, NOON) == "day"
    assert backgrounds.due(day, day.starts["night"] - timedelta(minutes=45)) == "night"


def test_the_weather_is_the_forecasts_for_an_hour_into_the_part():
    day = backgrounds.day_of(FORECAST, NOON)
    assert day is not None
    hourly = FORECAST.forecast.hourly
    hour = (day.starts["dusk"] + timedelta(hours=1)).strftime("%Y-%m-%dT%H")
    index = next(i for i, time in enumerate(hourly["time"]) if time.startswith(hour))

    weather = backgrounds.weather_at(FORECAST, day, "dusk")

    assert weather is not None
    assert weather.code == hourly["weather_code"][index]
    assert weather.max_c == FORECAST.forecast.daily["temperature_max"][0]


@pytest.mark.parametrize(
    ("code", "words"),
    [(0, "clear sky"), (3, "grey overcast"), (63, "steady rain"), (73, "falling snow"), (95, "thunder")],
)
def test_weather_codes_are_words(code, words):
    assert words in backgrounds.describe_code(code)


def test_hot_cold_and_windy_days_say_so():
    assert "hot summer haze" in backgrounds.describe_weather(Weather(0, 30, 10))
    assert "bitterly cold" in backgrounds.describe_weather(Weather(0, 1, 10))
    assert "strong wind" in backgrounds.describe_weather(Weather(0, 15, 50))
    assert backgrounds.describe_weather(Weather(0, None, None)) == "clear sky"


@pytest.mark.parametrize(
    ("day", "name"),
    [
        (date(2026, 10, 31), "Halloween"),
        (date(2026, 12, 25), "Christmas"),
        (date(2026, 12, 10), "the run-up to Christmas"),
        (date(2026, 4, 5), "Easter Sunday"),
        (date(2026, 3, 15), "Mothering Sunday"),
        (date(2026, 11, 8), "Remembrance Sunday"),
        (date(2026, 5, 16), "the Eurovision final"),
        (date(2026, 6, 10), "Pride Month"),
    ],
)
def test_the_calendars_occasions(day, name):
    assert name in [occasion.name for occasion in backgrounds.occasions(day)]


def test_an_ordinary_day_has_no_occasion():
    assert backgrounds.occasions(date(2026, 1, 14)) == []


def test_a_bank_holiday_is_englands_and_wales():
    stored = StoredBankHolidays.model_validate(fixtures.load("bank_holidays.json")["value"])
    event = stored.divisions["england-and-wales"].events[0]

    assert backgrounds.bank_holiday(stored, event.date) == event.title
    assert backgrounds.bank_holiday(stored, date(2026, 1, 14)) is None
    assert backgrounds.bank_holiday(None, event.date) is None


def test_warnings_are_their_titles_in_brief_each_once():
    feed = {
        "fetched_at": "2026-01-15T06:00:00Z",
        "postcode": "SE17 3LL",
        "region": "se",
        "items": [
            {"title": "Yellow warning of snow affecting London & South East"},
            {"title": "Yellow warning of snow affecting London & South East"},
            {"title": "Amber warning of wind affecting South West England"},
        ],
    }
    stored = StoredMetOfficeWarnings.model_validate(feed)

    assert backgrounds.warnings([stored]) == ["yellow warning of snow", "amber warning of wind"]


def test_the_prompt_has_the_scene_season_light_weather_occasions_and_the_calm_zones():
    context = Context(
        date(2026, 12, 25),
        Weather(73, 1, 10),
        [Occasion("Christmas", "Christmas lights")],
        bank_holiday="Christmas Day",
        warnings=["yellow warning of snow"],
    )

    prompt = backgrounds.prompt(LONDON, "night", context)

    view = backgrounds.scene_of(LONDON, date(2026, 12, 25))
    assert view.subject in prompt
    assert f"The bottom half is {view.foreground}" in prompt
    assert "early winter (December)" in prompt
    assert "low clouds lit softly" in prompt  # snow is overcast
    assert "falling snow" in prompt
    assert "bitterly cold" in prompt
    assert "It is Christmas: Christmas lights." in prompt
    assert "bank holiday" not in prompt  # Christmas already says it
    assert "yellow warning of snow" in prompt
    assert "anywhere from the centre to the right" in prompt
    assert "The left third, from top to bottom, is calm and simple" in prompt
    assert "top fifth, is a band of plain sky" in prompt
    assert "no text" in prompt
    assert "1280" not in prompt  # zones in words, not pixels


@pytest.mark.parametrize(("code", "clouds"), [(0, False), (2, True), (3, True), (45, False), (61, True), (95, True)])
def test_clouds_catch_the_light_only_when_the_sky_has_them(code, clouds):
    context = Context(date(2026, 11, 23), Weather(code, 7, 10), [])

    prompt = backgrounds.prompt(LONDON, "day", context)

    assert "a rich, colourful sky true to the weather above" in prompt
    assert ("clouds catching the light" in prompt) is clouds


def test_london_goes_through_its_scenes_a_day_at_a_time():
    week = [backgrounds.scene_of(LONDON, date(2026, 11, 17) + timedelta(days=n)) for n in range(7)]

    assert len(set(week)) == 7
    assert all(view in backgrounds.LONDON_SCENES for view in week)
    later = date(2026, 11, 17) + timedelta(days=len(backgrounds.LONDON_SCENES))
    assert backgrounds.scene_of(LONDON, later) == week[0]


def test_oslo_goes_through_its_scenes_a_day_at_a_time():
    days = range(len(backgrounds.OSLO_SCENES))
    week = [backgrounds.scene_of(OSLO, date(2026, 11, 17) + timedelta(days=n)) for n in days]

    assert set(week) == set(backgrounds.OSLO_SCENES)
    landmarks = ("Holmenkollen", "Opera House", "Storting", "Vigeland", "City Hall")
    assert all(any(name in view.subject for view in week) for name in landmarks)


def test_every_part_of_a_day_has_the_same_scene_so_a_relight_keeps_it():
    context = Context(date(2026, 11, 17), Weather(3, 10, 10), [])

    subjects = {
        phase: backgrounds.scene_of(LONDON, context.local_date).subject in backgrounds.prompt(LONDON, phase, context)
        for phase in backgrounds.PHASES
    }

    assert all(subjects.values())


def test_a_place_with_no_scene_of_its_own_is_its_everyday_view():
    prompt = backgrounds.prompt(
        Place("bergen-no-3161732", "Bergen, Norway"), "day", Context(NOON.date(), Weather(0, 5, 5), [])
    )

    assert "a recognisable everyday view of Bergen, Norway" in prompt
    assert "in full daylight" in prompt


def _image(phase, *, fallback=False, made_at="2026-01-15T11:00:00Z", starts_at="2026-01-15T09:28:00+00:00"):
    return {"phase": phase, "starts_at": starts_at, "digest": phase * 4, "made_at": made_at, "fallback": fallback}


def test_a_part_is_made_once_but_a_fallback_is_tried_again_after_half_an_hour():
    assert backgrounds.next_step([], "day", NOON) == "make"
    assert backgrounds.next_step([_image("day")], "day", NOON) == "keep"
    assert backgrounds.next_step([_image("day", fallback=True, made_at="2026-01-15T11:45:00Z")], "day", NOON) == "keep"
    assert backgrounds.next_step([_image("day", fallback=True, made_at="2026-01-15T11:30:00Z")], "day", NOON) == "make"


def test_a_part_relights_the_latest_made_one_before_it_and_dawn_none():
    dawn, day = _image("dawn"), _image("day", fallback=True)

    assert backgrounds.reference([dawn, day], "dawn") is None
    assert backgrounds.reference([dawn, day], "dusk") == dawn  # the day's was a fallback
    assert backgrounds.reference([], "night") is None


def test_a_new_image_replaces_its_parts_and_keeps_them_in_order():
    dawn = _image("dawn", starts_at="2026-01-15T07:28:00+00:00")
    old_day = _image("day", fallback=True)
    new_day = _image("day")

    assert backgrounds.with_image([old_day, dawn], new_day) == [dawn, new_day]


@pytest.mark.parametrize("phase", backgrounds.PHASES)
def test_every_part_has_a_curated_jpeg(phase):
    assert backgrounds.fallback(phase).startswith(b"\xff\xd8\xff")
