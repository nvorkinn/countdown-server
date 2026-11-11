import hashlib
import hmac
import json
from unittest.mock import Mock, patch

import fakeredis
import pytest
import redis

from broker.services import github_app, github_notify


@pytest.fixture
def app_setup(app):
    app.config.update(
        GITHUB_DASHBOARD_USER_ID="user-1",
        GITHUB_APP_CLIENT_ID="Iv1.client",
        GITHUB_APP_CLIENT_SECRET="shh",
        GITHUB_APP_WEBHOOK_SECRET="hook",
    )
    app.extensions["redis"] = fakeredis.FakeRedis(decode_responses=True)
    return app


def deliver(client, event, payload, secret="hook"):  # noqa: S107 -- the tests' webhook secret
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/api/github/webhook",
        data=body,
        headers={"X-GitHub-Event": event, "X-Hub-Signature-256": signature, "Content-Type": "application/json"},
    )


def test_a_signed_delivery_is_handed_to_the_open_pages_without_a_login(app_setup, anonymous_client):
    with app_setup.extensions["github_changes"].listen() as listener:
        response = deliver(
            anonymous_client, "pull_request", {"repository": {"full_name": "alice/p"}, "pull_request": {"number": 4}}
        )
        assert response.status_code == 204
        assert listener.wait(0) == [{"repo": "alice/p", "kind": "pull", "id": "4"}]


def test_a_signed_delivery_is_passed_on_for_a_notification(app_setup, anonymous_client):
    payload = {"repository": {"full_name": "alice/p"}, "pull_request": {"number": 4}}
    with patch("broker.routes.github_webhook.github_notify.notify") as notify:
        deliver(anonymous_client, "pull_request", payload)
    notify.assert_called_once_with("", "pull_request", payload)


def open_events(client):
    """Open the events stream and return its chunks, read one at a time, and the response to close."""
    response = client.get("/github/events", buffered=False)
    return response, iter(response.response)


def test_the_events_stream_pushes_each_change_from_when_it_opened(app_setup, anonymous_client, client, monkeypatch):
    monkeypatch.setattr("broker.routes.github_dashboard.EVENTS_PING_S", 0.05)
    deliver(anonymous_client, "pull_request", {"repository": {"full_name": "alice/p"}, "pull_request": {"number": 1}})
    response, chunks = open_events(client)
    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"
    assert response.headers["Cache-Control"] == "no-store"
    assert next(chunks) == b": connected\n\n"
    assert next(chunks) == b": ping\n\n", "a change from before it opened isn't sent"
    deliver(anonymous_client, "pull_request", {"repository": {"full_name": "alice/p"}, "pull_request": {"number": 4}})
    assert json.loads(next(chunks).removeprefix(b"data: ")) == [{"repo": "alice/p", "kind": "pull", "id": "4"}]
    response.close()


def test_the_events_stream_holds_notifications_back_while_open(app_setup, client, monkeypatch):
    monkeypatch.setattr("broker.routes.github_dashboard.EVENTS_PING_S", 0.05)
    redis = app_setup.extensions["redis"]
    response, chunks = open_events(client)
    assert redis.exists(github_notify.SEEN)
    next(chunks)
    redis.delete(github_notify.SEEN)
    next(chunks)
    assert redis.exists(github_notify.SEEN), "each ping marks the page seen again"
    response.close()


def test_the_events_stream_stops_listening_once_closed(app_setup, client):
    feed = app_setup.extensions["github_changes"]
    response, chunks = open_events(client)
    next(chunks)
    assert feed.listening() == 1
    response.close()
    assert feed.listening() == 0


def test_the_events_stream_carries_on_without_redis(app_setup, client, monkeypatch):
    monkeypatch.setattr("broker.routes.github_dashboard.EVENTS_PING_S", 0.05)
    monkeypatch.setattr(
        "broker.routes.github_dashboard.github_notify.mark_seen", Mock(side_effect=redis.ConnectionError("down"))
    )
    response, chunks = open_events(client)
    assert response.status_code == 200
    assert [next(chunks), next(chunks)] == [b": connected\n\n", b": ping\n\n"]
    response.close()


def test_an_unsigned_or_wrongly_signed_delivery_is_refused(app_setup, anonymous_client):
    payload = {"repository": {"full_name": "alice/p"}, "pull_request": {"number": 4}}
    with app_setup.extensions["github_changes"].listen() as listener:
        assert deliver(anonymous_client, "pull_request", payload, secret="guess").status_code == 401
        assert (
            anonymous_client.post(
                "/api/github/webhook", json=payload, headers={"X-GitHub-Event": "pull_request"}
            ).status_code
            == 401
        )
        assert listener.wait(0) == []


def test_a_signed_delivery_of_an_unexpected_shape_is_refused(app_setup, anonymous_client):
    assert deliver(anonymous_client, "pull_request", {"repository": {"full_name": "alice/p"}}).status_code == 400


def test_the_webhook_is_off_without_its_secret(app_setup, anonymous_client):
    app_setup.config["GITHUB_APP_WEBHOOK_SECRET"] = ""
    assert deliver(anonymous_client, "push", {}, secret="").status_code == 404


def test_the_page_offers_to_connect_and_says_webhooks_are_on(app_setup, client):
    page = client.get("/github").get_data(as_text=True)
    assert 'href="/github/connect"' in page
    assert '<meta name="github-webhooks" content="on">' in page
    with patch("broker.routes.github_dashboard.github_app.connected", return_value=True):
        assert 'href="/github/connect"' not in client.get("/github").get_data(as_text=True)


def test_connect_sends_the_user_to_github_with_a_state_kept_in_the_session(app_setup, client):
    response = client.get("/github/connect")
    assert response.status_code == 302
    with client.session_transaction() as session:
        state = session["github_app_state"]
    assert response.headers["Location"] == github_app.AUTHORIZE_URL + f"?client_id=Iv1.client&state={state}"


def test_the_callback_needs_the_state_this_session_started(app_setup, client):
    with patch("broker.routes.github_dashboard.github_app.connect") as connect:
        assert client.get("/github/callback", query_string={"code": "c", "state": "forged"}).status_code == 400
        client.get("/github/connect")
        with client.session_transaction() as session:
            state = session["github_app_state"]
        response = client.get("/github/callback", query_string={"code": "c", "state": state})
        assert (response.status_code, response.headers["Location"]) == (302, "/github")
        connect.assert_called_once_with("user-1", "c")
        assert client.get("/github/callback", query_string={"code": "c", "state": state}).status_code == 400


def test_a_cancelled_sign_in_connects_nothing(app_setup, client):
    client.get("/github/connect")
    with client.session_transaction() as session:
        state = session["github_app_state"]
    with patch("broker.routes.github_dashboard.github_app.connect") as connect:
        assert (
            client.get("/github/callback", query_string={"error": "access_denied", "state": state}).status_code == 400
        )
    connect.assert_not_called()


def test_github_calls_use_the_app_token_once_connected(app_setup, client):
    with (
        patch("broker.routes.github_dashboard.github.Dashboard") as dashboard,
        patch("broker.routes.github_dashboard.github_app.token", return_value="ghu_user") as token,
    ):
        dashboard.return_value.repositories.return_value = []
        assert client.get("/github/repositories").status_code == 200
    token.assert_called_once_with("user-1")
    assert dashboard.call_args.args[0] == "ghu_user"


def test_without_a_sign_in_the_user_is_asked_to_connect(app_setup, client):
    with patch("broker.routes.github_dashboard.github.Dashboard") as dashboard:
        response = client.get("/github/repositories")
    assert response.status_code == 409
    assert response.json == {"error": "Connect GitHub to use the dashboard."}
    dashboard.assert_not_called()


def test_without_the_app_the_dashboard_is_off(app_setup, client):
    app_setup.config["GITHUB_APP_CLIENT_SECRET"] = ""
    assert client.get("/github").status_code == 503
    assert client.get("/github/connect").status_code == 503
