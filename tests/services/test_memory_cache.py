import pytest

from broker.services import memory_cache
from broker.services.memory_cache import MemoryCache


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(memory_cache.time, "monotonic", lambda: now[0])
    return now


def test_values_last_until_they_expire(clock):
    cache = MemoryCache()
    assert cache.set("a", "1", ex=30)
    cache.set("b", "2")
    assert cache.get("a") == "1"
    assert cache.exists("a")
    clock[0] += 30
    assert cache.get("a") is None
    assert not cache.exists("a")
    assert cache.get("b") == "2", "without a lifetime a value stays"


def test_nx_only_sets_an_empty_or_expired_key(clock):
    cache = MemoryCache()
    assert cache.set("k", "1", ex=10, nx=True)
    assert not cache.set("k", "2", ex=10, nx=True)
    assert cache.get("k") == "1"
    clock[0] += 10
    assert cache.set("k", "3", ex=10, nx=True)
    cache.delete("k")
    assert cache.get("k") is None


def test_the_least_recently_used_entries_go_first_once_full():
    cache = MemoryCache(max_entries=2)
    cache.set("a", "1")
    cache.set("b", "2")
    cache.get("a")
    cache.set("c", "3")
    assert cache.get("b") is None
    assert (cache.get("a"), cache.get("c")) == ("1", "3")
