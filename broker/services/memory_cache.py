"""A small in-process key-value store with expiry, for the GitHub dashboard in place of Redis.

The dashboard's webhook, pages and Telegram messages are all served by the broker's one gunicorn process
(gunicorn.conf.py), so what they share can live in its memory: GitHub's ETags and answers, which Telegram messages were
sent, and whether the page is open. A restart forgets it all, which costs at most a few uncached GitHub reads or a
repeated message.

It answers the few Redis calls the dashboard makes (get, set with ex/nx, exists, delete), so the GitHub client can take
either. Thread-safe, since gunicorn serves each request on a thread; the oldest entries go once it holds `max_entries`.
"""

import threading
import time
from collections import OrderedDict


class MemoryCache:
    """Strings by key, each with an optional lifetime."""

    def __init__(self, max_entries: int = 5000) -> None:
        """Start empty, keeping at most `max_entries`."""
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, tuple[str, float | None]] = OrderedDict()
        self._max_entries = max_entries

    def get(self, key: str) -> str | None:
        """Return the value at `key`, or None when there's none or it has expired."""
        with self._lock:
            entry = self._live(key)
            if entry is None:
                return None
            self._entries.move_to_end(key)
            return entry[0]

    def set(self, key: str, value: str, *, ex: float | None = None, nx: bool = False) -> bool:
        """Store `value` for `ex` seconds (for good without); with `nx`, only if `key` is empty. Return if stored."""
        with self._lock:
            if nx and self._live(key) is not None:
                return False
            self._entries[key] = (value, None if ex is None else time.monotonic() + ex)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
            return True

    def exists(self, key: str) -> bool:
        """Whether `key` holds a value that hasn't expired."""
        with self._lock:
            return self._live(key) is not None

    def delete(self, key: str) -> None:
        """Forget `key`."""
        with self._lock:
            self._entries.pop(key, None)

    def _live(self, key: str) -> tuple[str, float | None] | None:
        entry = self._entries.get(key)
        if entry is not None and entry[1] is not None and entry[1] <= time.monotonic():
            del self._entries[key]
            return None
        return entry
