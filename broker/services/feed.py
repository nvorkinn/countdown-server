"""A touchscreen's live feed (getFeed): its weather, arrivals and notices as server-sent events, each when it changes.

The panels come from the store alone (services/panels.py), which countdown's providers rewrite every half minute or so,
so the feed reads them every POLL_S and sends whichever answer differs from the one it sent last, as an event named
for it with that answer as its data: all three as the stream opens, then each again only when it changes. A comment
line every PING_S keeps the connection open through proxies, and tells the device the stream is still alive.

A stream holds one of gunicorn's threads while it is open (gunicorn.conf.py), so it ends after LIFETIME_S and the
device opens a new one (its `retry` field says how soon): a stream lost without a close frees its thread then too. It
ends at once when the store goes down or the device is unpaired, as the device's next open is answered 503 or 409.

The stream reads the store in an app context of its own, not the request's: the request's context (and the database
session the device's secret was checked in) ends as the answer starts, so a ten-minute stream holds nothing open.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from redis import RedisError

from broker.services import panels

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from flask.ctx import AppContext
    from redis import Redis

logger = logging.getLogger(__name__)

# How often the store is read: the providers write every 30 s or so, so a change reaches the screen within a few.
POLL_S = 5.0
# A comment line after this long without an event, under Caddy's and the browser's idle timeouts.
PING_S = 15.0
# How long a stream lasts before the device opens a new one.
LIFETIME_S = 600.0
# How soon the device opens the next stream once one ends, in the stream's `retry` field.
RETRY_MS = 3000

# The stream's clock and wait, looked up as it runs, so a test can stand in for both.
clock: Callable[[], float] = time.monotonic
sleep: Callable[[float], None] = time.sleep

# getFeed's events, each named for the route whose answer it carries, in the order they're first sent.
READERS: dict[str, Callable[[Redis, str], dict[str, Any]]] = {
    "weather": panels.weather,
    "arrivals": panels.arrivals,
    "notices": panels.notices,
}


def read(store: Redis, device_id: str) -> dict[str, str]:
    """Return each event's data as the JSON it would be sent as. Raises NotPairedError and RedisError."""
    return {name: json.dumps(reader(store, device_id), separators=(",", ":")) for name, reader in READERS.items()}


def event(name: str, data: str) -> str:
    """Return the server-sent event `name` with `data` (one line of JSON)."""
    return f"event: {name}\ndata: {data}\n\n"


@dataclass
class Feed:
    """One device's stream, from its first reading (`first`, read before the answer starts, so it can still refuse)."""

    app_context: Callable[[], AppContext]
    store: Redis
    device_id: str
    first: dict[str, str]
    _sent: dict[str, str] = field(default_factory=dict)

    def __iter__(self) -> Iterator[str]:
        """Yield the stream: its retry field and every event, a ping when quiet, until LIFETIME_S or the store fails."""
        started = last_write = clock()
        yield f"retry: {RETRY_MS}\n\n"
        yield from self._changed(self.first)
        while (now := clock()) - started < LIFETIME_S:
            sleep(min(POLL_S, LIFETIME_S - (now - started)))
            reading = self._read()
            if reading is None:
                return
            changes = list(self._changed(reading))
            if changes:
                last_write = clock()
                yield from changes
            elif clock() - last_write >= PING_S:
                last_write = clock()
                yield ": ping\n\n"

    def _read(self) -> dict[str, str] | None:
        """Return the panels now; None (logged) when the store is down or the device is no longer paired."""
        with self.app_context():
            try:
                return read(self.store, self.device_id)
            except panels.NotPairedError:
                logger.info("A device's feed ended: it isn't paired any more")
            except RedisError as error:
                logger.warning("A device's feed ended: couldn't read the store: %s", type(error).__name__)
        return None

    def _changed(self, reading: dict[str, str]) -> Iterator[str]:
        for name, data in reading.items():
            if self._sent.get(name) != data:
                self._sent[name] = data
                yield event(name, data)
