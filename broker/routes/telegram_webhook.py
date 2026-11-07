"""Telegram's webhook for the dashboard's bot: outside the login, so its secret token is the only check."""

import hmac
from typing import TYPE_CHECKING

from flask import Blueprint, current_app, jsonify, request

from broker.services import github_notify

if TYPE_CHECKING:
    from flask.typing import ResponseReturnValue

bp = Blueprint("telegram_webhook", __name__)


@bp.post("/api/telegram/webhook")
def webhook() -> ResponseReturnValue:
    """Act on a tap of a message's button; anything without the secret token is refused."""
    config = current_app.config
    secret = config["TELEGRAM_WEBHOOK_SECRET"]
    if not (secret and github_notify.configured() and config["GITHUB_DASHBOARD_USER_ID"]):
        return jsonify(error="Not found."), 404
    received = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(secret.encode(), received.encode()):
        return jsonify(error="Invalid secret token."), 401
    update = request.get_json(silent=True)
    if isinstance(update, dict):
        try:
            github_notify.answer(update)
        except KeyError, TypeError, AttributeError:
            current_app.logger.warning("A Telegram update had an unexpected shape")
    # Anything but success makes Telegram send the update again.
    return "", 204
