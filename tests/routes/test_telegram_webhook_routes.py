from unittest.mock import patch

import fakeredis
import pytest

UPDATE = {"update_id": 1, "callback_query": {"id": "q1", "from": {"id": 42}, "data": "approve:99"}}


@pytest.fixture
def app_setup(app):
    app.config.update(
        GITHUB_DASHBOARD_USER_ID="user-1",
        TELEGRAM_BOT_TOKEN="123:bot",
        TELEGRAM_CHAT_ID="42",
        TELEGRAM_WEBHOOK_SECRET="s3cret",
    )
    app.extensions["redis"] = fakeredis.FakeRedis(decode_responses=True)
    return app


def post(client, update=UPDATE, secret="s3cret"):  # noqa: S107 -- the tests' secret token
    return client.post("/api/telegram/webhook", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": secret})


def test_an_update_with_the_secret_token_is_answered_without_a_login(app_setup, anonymous_client):
    with patch("broker.routes.telegram_webhook.github_notify.answer") as answer:
        assert post(anonymous_client).status_code == 204
    answer.assert_called_once_with(UPDATE)


@pytest.mark.parametrize("secret", ["guess", ""])
def test_an_update_without_the_secret_token_is_refused(app_setup, anonymous_client, secret):
    with patch("broker.routes.telegram_webhook.github_notify.answer") as answer:
        assert post(anonymous_client, secret=secret).status_code == 401
    answer.assert_not_called()


@pytest.mark.parametrize("unset", ["TELEGRAM_WEBHOOK_SECRET", "TELEGRAM_BOT_TOKEN", "GITHUB_DASHBOARD_USER_ID"])
def test_the_webhook_is_off_until_everything_it_needs_is_set(app_setup, anonymous_client, unset):
    app_setup.config[unset] = ""
    assert post(anonymous_client).status_code == 404


def test_an_update_of_an_unexpected_shape_is_logged_and_still_accepted(app_setup, anonymous_client, caplog):
    assert post(anonymous_client, update={"callback_query": {"id": "q1"}}).status_code == 204
    assert "A Telegram update had an unexpected shape" in caplog.text
