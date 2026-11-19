"""Test a touchscreen's live feed (GET /api/devices/feed): its panels as server-sent events, each again when it changes.

Its refusals (401, 409, 503) are the other panel routes', in test_touchscreen_panels.py. Here the stream runs on a
stand-in clock: each wait between reads moves it on, and runs `between` first, which is how a test changes the store
while the stream is open.
"""

import json

import pytest
from countdown_api.models import DeviceFeedEvent
from pydantic import TypeAdapter

from broker.services import feed
from tests.routes.test_touchscreen_panels import (
    DEPARTURES,
    METALERTS,
    NORWAY,
    PANELS,
    SITUATIONS,
    _oslo,
    _stored,
)
from tests.routes.test_touchscreens import _auth, _paired


class Clock:
    """The stream's clock and wait: a wait moves the clock on and calls `between` (with the time it waited until)."""

    def __init__(self):
        self.now = 1000.0
        self.between = lambda _now: None

    def sleep(self, seconds):
        self.now += seconds
        self.between(self.now)


@pytest.fixture
def clock(monkeypatch):
    stand_in = Clock()
    monkeypatch.setattr(feed, "clock", lambda: stand_in.now)
    monkeypatch.setattr(feed, "sleep", stand_in.sleep)
    return stand_in


@pytest.fixture
def device(app, client):
    device_id = _paired(app, client)
    _stored(app, device_id)
    return device_id


def _messages(response) -> list[dict]:
    """Return the stream's messages, each as its fields: {"event", "data"}, {"comment"} or {"retry"}."""
    text = b"".join(response.response).decode()
    assert text.endswith("\n\n")
    messages = []
    for block in text.removesuffix("\n\n").split("\n\n"):
        message = {}
        for line in block.split("\n"):
            name, _, value = line.partition(": ") if not line.startswith(":") else ("comment", "", line[2:])
            message[name] = value
        messages.append(message)
    return messages


def _events(messages):
    return [(m["event"], json.loads(m["data"])) for m in messages if "event" in m]


def test_it_opens_with_each_panel_as_the_route_answers_it(client, device, clock):
    response = client.get("/api/devices/feed", headers=_auth())

    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"
    assert response.headers["Cache-Control"] == "no-store"
    messages = _messages(response)
    assert messages[0] == {"retry": str(feed.RETRY_MS)}
    events = _events(messages)
    assert events[:3] == [
        ("weather", PANELS["weather"]),
        ("arrivals", PANELS["arrivals"]),
        ("notices", PANELS["notices"]),
    ]
    for name, data in events:
        TypeAdapter(DeviceFeedEvent).validate_python({"event": name, "data": data})


def test_a_norwegian_devices_notices_are_its_places_weather_warnings_as_getnotices_answers(app, client, clock):
    device_id = _paired(app, client)
    store = _stored(app, device_id, _oslo())
    store[SITUATIONS["key"]] = json.dumps(SITUATIONS["value"])
    later = {**METALERTS["value"], "fetched_at": "2026-10-18T12:30:00Z"}

    def provider_writes(now):
        if now == 1000 + feed.POLL_S:
            store[METALERTS["key"]] = json.dumps(later)

    clock.between = provider_writes
    events = _events(_messages(client.get("/api/devices/feed", headers=_auth())))

    notices = [data for name, data in events if name == "notices"]
    narrowed = {**NORWAY["notices"]["metalerts"], "fetched_at": later["fetched_at"]}
    assert notices == [NORWAY["notices"], {**NORWAY["notices"], "metalerts": narrowed}]


def test_nothing_changing_sends_only_pings_and_it_ends_after_its_lifetime(client, device, clock):
    messages = _messages(client.get("/api/devices/feed", headers=_auth()))

    assert len(_events(messages)) == 3
    pings = [m for m in messages if "comment" in m]
    assert pings
    assert {m["comment"] for m in pings} == {"ping"}
    # One every PING_S (a whole number of reads), for the whole of the stream's life.
    assert len(pings) == feed.LIFETIME_S // feed.PING_S
    assert clock.now == pytest.approx(1000 + feed.LIFETIME_S)


def test_a_change_sends_that_panel_again_and_only_it(app, client, device, clock):
    stop = DEPARTURES["stops"][0]
    later = {**stop["value"], "fetched_at": "2026-10-17T12:00:30Z"}

    def provider_writes(now):
        if now == 1000 + 2 * feed.POLL_S:
            app.extensions["redis"].strings[stop["key"]] = json.dumps(later)

    clock.between = provider_writes
    events = _events(_messages(client.get("/api/devices/feed", headers=_auth())))

    assert [name for name, _ in events] == ["weather", "arrivals", "notices", "arrivals"]
    sent = {s["id"]: s["departures"] for s in events[3][1]["stops"]}
    assert sent[later["stop_id"]] == later


def test_it_ends_when_the_store_goes_down(app, client, device, clock):
    def store_fails(now):
        if now >= 1000 + 3 * feed.POLL_S:
            app.extensions["redis"].fail = True

    clock.between = store_fails
    messages = _messages(client.get("/api/devices/feed", headers=_auth()))

    assert len(_events(messages)) == 3
    assert clock.now == 1000 + 3 * feed.POLL_S


def test_it_ends_when_the_device_is_unpaired(app, client, device, clock):
    def unpaired(now):
        if now >= 1000 + feed.POLL_S:
            del app.extensions["redis"].strings[f"v1:config:{device}"]

    clock.between = unpaired
    _messages(client.get("/api/devices/feed", headers=_auth()))

    assert clock.now == 1000 + feed.POLL_S


def test_a_touchscreen_origin_may_read_it(app, client, device, clock):
    origin = "https://tablet.example"
    app.config["TOUCHSCREEN_RETURN_ORIGINS"] = frozenset({origin})
    response = client.get("/api/devices/feed", headers={**_auth(), "Origin": origin})

    assert response.headers["Access-Control-Allow-Origin"] == origin
