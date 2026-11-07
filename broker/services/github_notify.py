"""Telegram messages about the GitHub dashboard's webhook deliveries, held back while the dashboard is open.

The page reads the change feed every few seconds while its tab is visible (services/github_app.py), so a recent read
means its user is already watching and their phone stays quiet. Only what needs attention is sent: a release
finishing or waiting for approval, a PR's checks failing or changes being requested on it, and a PR merging.

A release waiting for approval gets an Approve button when Telegram's webhook is set up (routes/telegram_webhook.py):
a tap from the dashboard's chat approves every environment of that run its user may approve, through the dashboard's
own GitHub client, so it can't do anything the dashboard's Approve button couldn't.

Calls to Telegram are best-effort: a failure is logged, never raised, so GitHub's and Telegram's deliveries succeed.
"""

import logging
from typing import TYPE_CHECKING, NamedTuple

import requests
from flask import current_app

from broker.clients import github
from broker.services import github_app

if TYPE_CHECKING:
    from redis import Redis

logger = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/{method}"
TIMEOUT = 5
SEEN = "v1:github:dashboard-seen"
# Several of the page's 5-second feed reads, so one slow read doesn't count as the page being closed.
SEEN_S = 30
# GitHub can deliver an event again, and a release waits for approval in one delivery per job.
SENT = "v1:github:notified:{}"
SENT_S = 24 * 60 * 60
# Which repository a waiting run's Approve button is for, since a button's data is limited to 64 bytes. GitHub
# gives up on a waiting deployment after 30 days.
APPROVE = "v1:telegram:approve:{}"
APPROVE_S = 30 * 24 * 60 * 60
# Conclusions worth a message when a PR's checks finish.
FAILED = {"failure", "timed_out", "action_required"}


class Message(NamedTuple):
    """What to send about a delivery."""

    text: str
    # What makes another delivery a repeat of this one; "" for the delivery itself.
    repeat: str = ""
    # The release run to offer an Approve button for: its repository and run ID.
    approve: tuple[str, int] | None = None


def configured() -> bool:
    """Whether a Telegram bot and chat are set up to send to."""
    config = current_app.config
    return bool(config["TELEGRAM_BOT_TOKEN"] and config["TELEGRAM_CHAT_ID"])


def mark_seen() -> None:
    """Note that the dashboard is open, which holds messages back for a little while."""
    redis: Redis = current_app.extensions["redis"]
    redis.set(SEEN, "1", ex=SEEN_S)


def message(event: str, payload: dict) -> Message | None:
    """Return what to send about a delivery, or None when it isn't worth a message."""
    repo = (payload.get("repository") or {}).get("full_name")
    describe = MESSAGES.get(event)
    if not isinstance(repo, str) or describe is None:
        return None
    return describe(repo, payload)


def _merged(repo: str, payload: dict) -> Message | None:
    pull = payload["pull_request"]
    if payload.get("action") != "closed" or not pull.get("merged"):
        return None
    return Message(f"Merged {repo}#{pull['number']}: {pull['title']}\n{pull['html_url']}")


def _changes_requested(repo: str, payload: dict) -> Message | None:
    if payload.get("action") != "submitted" or payload["review"]["state"] != "changes_requested":
        return None
    pull = payload["pull_request"]
    return Message(f"Changes requested on {repo}#{pull['number']}: {pull['title']}\n{pull['html_url']}")


def _checks_failed(repo: str, payload: dict) -> Message | None:
    suite = payload["check_suite"]
    pulls = [f"https://github.com/{repo}/pull/{pull['number']}" for pull in suite["pull_requests"]]
    if payload.get("action") != "completed" or suite.get("conclusion") not in FAILED or not pulls:
        return None
    return Message(f"Checks failed in {repo}\n" + "\n".join(pulls))


def _release_finished(repo: str, payload: dict) -> Message | None:
    run = payload["workflow_run"]
    if payload.get("action") != "completed" or not github.is_release_name(run["name"]):
        return None
    return Message(f"{run['name']} in {repo}: {run['conclusion']}\n{run['html_url']}")


def _release_waiting(repo: str, payload: dict) -> Message | None:
    job = payload["workflow_job"]
    if payload.get("action") != "waiting" or not github.is_release_name(job.get("workflow_name")):
        return None
    run_id = int(job["run_id"])
    text = f"A release in {repo} is waiting for your approval\n{job['html_url']}"
    return Message(text, f"waiting:{run_id}", (repo, run_id))


MESSAGES = {
    "pull_request": _merged,
    "pull_request_review": _changes_requested,
    "check_suite": _checks_failed,
    "workflow_run": _release_finished,
    "workflow_job": _release_waiting,
}


def notify(delivery: str, event: str, payload: dict) -> None:
    """Send a message about a webhook delivery, unless it isn't worth one, is a repeat, or the dashboard is open."""
    if not configured():
        return
    try:
        found = message(event, payload)
    except KeyError, TypeError, ValueError, AttributeError:
        logger.warning("A GitHub webhook delivery had an unexpected shape for a notification")
        return
    if found is None:
        return
    redis: Redis = current_app.extensions["redis"]
    if redis.exists(SEEN):
        return
    key = found.repeat or delivery
    if key and not redis.set(SENT.format(key), "1", nx=True, ex=SENT_S):
        return
    config = current_app.config
    send = {"chat_id": config["TELEGRAM_CHAT_ID"], "text": found.text, "link_preview_options": {"is_disabled": True}}
    # Without Telegram's webhook nothing would hear the tap.
    if found.approve and config["TELEGRAM_WEBHOOK_SECRET"]:
        repo, run_id = found.approve
        redis.set(APPROVE.format(run_id), repo, ex=APPROVE_S)
        send["reply_markup"] = {"inline_keyboard": [[{"text": "Approve", "callback_data": f"approve:{run_id}"}]]}
    _telegram("sendMessage", send)


def answer(update: dict) -> None:
    """Act on a tap of an Approve button, if it came from the dashboard's chat, and say how it went."""
    query = update.get("callback_query")
    if not isinstance(query, dict):
        return
    if str(query["from"]["id"]) != current_app.config["TELEGRAM_CHAT_ID"]:
        _telegram("answerCallbackQuery", {"callback_query_id": query["id"], "text": "This isn't yours to approve."})
        return
    redis: Redis = current_app.extensions["redis"]
    kind, _, run = str(query.get("data", "")).partition(":")
    repo = redis.get(APPROVE.format(run)) if kind == "approve" and run.isdecimal() else None
    if not isinstance(repo, str):
        _telegram("answerCallbackQuery", {"callback_query_id": query["id"], "text": "This release isn't waiting."})
        return
    approved, result = _approve(repo, int(run))
    _telegram("answerCallbackQuery", {"callback_query_id": query["id"], "text": result})
    if approved:
        redis.delete(APPROVE.format(run))
        sent = query["message"]
        # Editing the text without a keyboard removes the button.
        _telegram(
            "editMessageText",
            {
                "chat_id": sent["chat"]["id"],
                "message_id": sent["message_id"],
                "text": f"{sent['text']}\n\nApproved ✅",
                "link_preview_options": {"is_disabled": True},
            },
        )


def _approve(repo: str, run_id: int) -> tuple[bool, str]:
    """Approve every environment of the run its user may approve, as the dashboard's user; say how it went."""
    owner, _, name = repo.partition("/")
    try:
        api = github.Dashboard(
            github_app.token(current_app.config["GITHUB_DASHBOARD_USER_ID"]), current_app.extensions["redis"]
        )
        repository = api.owned_repository(owner, name)
        waiting = [item for item in api.pending_deployments(repository, run_id) if item["can_approve"]]
        if not waiting:
            return False, "Nothing in this release is waiting for your approval."
        for item in waiting:
            api.approve_deployment(repository, run_id, item["id"])
    except github.DashboardError as error:
        return False, error.public_message
    except requests.RequestException:
        logger.warning("GitHub didn't answer a Telegram approval")
        return False, "GitHub didn't answer. Try the dashboard."
    return True, "Approved"


def _telegram(method: str, payload: dict) -> None:
    config = current_app.config
    try:
        response = requests.post(
            API_URL.format(token=config["TELEGRAM_BOT_TOKEN"], method=method), json=payload, timeout=TIMEOUT
        )
        response.raise_for_status()
    except requests.RequestException as error:
        # The URL holds the bot token, so log only the status.
        status = error.response.status_code if error.response is not None else "no response"
        logger.warning("Couldn't call Telegram's %s: %s", method, status)
