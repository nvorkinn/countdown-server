import threading

from broker.services.github_changes import ChangeFeed

PULL = {"repo": "alice/p", "kind": "pull", "id": "4"}
RUN = {"repo": "alice/p", "kind": "run", "id": "9"}


def test_every_listener_gets_what_is_published_while_it_listens():
    feed = ChangeFeed()
    feed.publish([PULL])  # nobody listening: dropped
    with feed.listen() as first, feed.listen() as second:
        feed.publish([PULL])
        feed.publish([RUN])
        assert first.wait(0) == [PULL, RUN], "what arrived meanwhile comes as one batch"
        assert second.wait(0) == [PULL, RUN]
        assert first.wait(0) == []
    assert feed.listening() == 0


def test_nothing_published_means_nothing_to_send():
    feed = ChangeFeed()
    with feed.listen() as listener:
        feed.publish([])
        assert listener.wait(0.01) == []


def test_a_waiting_listener_wakes_when_another_thread_publishes():
    feed = ChangeFeed()
    with feed.listen() as listener:
        timer = threading.Timer(0.05, feed.publish, args=([PULL],))
        timer.start()
        assert listener.wait(5) == [PULL]
        timer.join()
