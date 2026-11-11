"""What the GitHub webhook saw change, handed straight to the dashboard pages connected to this process.

The webhook (routes/github_webhook.py) publishes each delivery's changes; every open live connection
(routes/github_dashboard.events) has its own queue and receives them all. Nothing is kept: a page reads everything
whenever it (re)connects, so a change made while no page listened is never needed. That only works while the webhook
and the pages share one process, which gunicorn.conf.py keeps (one worker, threads for the open connections).
"""

import queue
import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator

type Change = dict[str, str]


class ChangeFeed:
    """Fans changes out to the connections listening now; thread-safe, as gunicorn serves each request on a thread."""

    def __init__(self) -> None:
        """Start with nobody listening."""
        self._lock = threading.Lock()
        self._listeners: set[queue.SimpleQueue[list[Change]]] = set()

    def publish(self, changes: list[Change]) -> None:
        """Hand `changes` to every connection listening now."""
        if not changes:
            return
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            listener.put(changes)

    @contextmanager
    def listen(self) -> Generator[Listener]:
        """Receive what's published from now until the block ends."""
        inbox: queue.SimpleQueue[list[Change]] = queue.SimpleQueue()
        with self._lock:
            self._listeners.add(inbox)
        try:
            yield Listener(inbox)
        finally:
            with self._lock:
                self._listeners.discard(inbox)

    def listening(self) -> int:
        """How many connections are listening."""
        with self._lock:
            return len(self._listeners)


class Listener:
    """One connection's inbox."""

    def __init__(self, inbox: queue.SimpleQueue[list[Change]]) -> None:
        """Wrap the queue the feed puts changes on."""
        self._inbox = inbox

    def wait(self, timeout_s: float) -> list[Change]:
        """Wait up to `timeout_s` for changes; return everything that has arrived, or nothing when none did."""
        try:
            changes = list(self._inbox.get(timeout=timeout_s))
        except queue.Empty:
            return []
        while True:
            try:
                changes += self._inbox.get_nowait()
            except queue.Empty:
                return changes
