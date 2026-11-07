"""Telegram messages about the GitHub dashboard's webhook deliveries, held back while the dashboard is open.

The page reads the change feed every few seconds while its tab is visible (services/github_app.py), so a recent read
means its user is already watching and their phone stays quiet. Only what needs attention is sent: a release
finishing or waiting for approval, a PR's checks failing or changes being requested on it, and a PR merging.

Sending is best-effort: a failure is logged, never raised, so GitHub's delivery still succeeds.
"""

import logging
from typing import TYPE_CHECKING

import requests
from flask import current_app

from broker.clients import github

if TYPE_CHECKING:
    from redis import Redis

logger = logging.getLogger(__name__)

SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"
TIMEOUT = 5
SEEN = "v1:github:dashboard-seen"
# Several of the page's 5-second feed reads, so one slow read doesn't count as the page being closed.
SEEN_S = 30
# GitHub can deliver an event again, and a release waits for approval in one delivery per job.
SENT = "v1:github:notified:{}"
SENT_S = 24 * 60 * 60
# Conclusions worth a message when a PR's checks finish.
FAILED = {"failure", "timed_out", "action_required"}


def configured() -> bool:
    """Whether a Telegram bot and chat are set up to send to."""
    config = current_app.config
    return bool(config["TELEGRAM_BOT_TOKEN"] and config["TELEGRAM_CHAT_ID"])


def mark_seen() -> None:
    """Note that the dashboard is open, which holds messages back for a little while."""
    redis: Redis = current_app.extensions["redis"]
    redis.set(SEEN, "1", ex=SEEN_S)


# A message to send, and what makes another delivery a repeat of it ("" for the delivery itself).
type Message = tuple[str, str] | None


def message(event: str, payload: dict) -> Message:
    """Return what to send about a delivery and what makes it a repeat, or None when it isn't worth a message."""
    repo = (payload.get("repository") or {}).get("full_name")
    describe = MESSAGES.get(event)
    if not isinstance(repo, str) or describe is None:
        return None
    return describe(repo, payload)


def _merged(repo: str, payload: dict) -> Message:
    pull = payload["pull_request"]
    if payload.get("action") != "closed" or not pull.get("merged"):
        return None
    return f"Merged {repo}#{pull['number']}: {pull['title']}\n{pull['html_url']}", ""


def _changes_requested(repo: str, payload: dict) -> Message:
    if payload.get("action") != "submitted" or payload["review"]["state"] != "changes_requested":
        return None
    pull = payload["pull_request"]
    return f"Changes requested on {repo}#{pull['number']}: {pull['title']}\n{pull['html_url']}", ""


def _checks_failed(repo: str, payload: dict) -> Message:
    suite = payload["check_suite"]
    pulls = [f"https://github.com/{repo}/pull/{pull['number']}" for pull in suite["pull_requests"]]
    if payload.get("action") != "completed" or suite.get("conclusion") not in FAILED or not pulls:
        return None
    return f"Checks failed in {repo}\n" + "\n".join(pulls), ""


def _release_finished(repo: str, payload: dict) -> Message:
    run = payload["workflow_run"]
    if payload.get("action") != "completed" or not github.is_release_name(run["name"]):
        return None
    return f"{run['name']} in {repo}: {run['conclusion']}\n{run['html_url']}", ""


def _release_waiting(repo: str, payload: dict) -> Message:
    job = payload["workflow_job"]
    if payload.get("action") != "waiting" or not github.is_release_name(job.get("workflow_name")):
        return None
    return f"A release in {repo} is waiting for your approval\n{job['html_url']}", f"waiting:{job['run_id']}"


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
    except KeyError, TypeError, AttributeError:
        logger.warning("Couldn't read a %s delivery for a notification", event)
        return
    if found is None:
        return
    text, repeat_key = found
    redis: Redis = current_app.extensions["redis"]
    if redis.exists(SEEN):
        return
    key = repeat_key or delivery
    if key and not redis.set(SENT.format(key), "1", nx=True, ex=SENT_S):
        return
    config = current_app.config
    try:
        response = requests.post(
            SEND_URL.format(token=config["TELEGRAM_BOT_TOKEN"]),
            json={"chat_id": config["TELEGRAM_CHAT_ID"], "text": text, "link_preview_options": {"is_disabled": True}},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        # The URL holds the bot token, so log only the status.
        status = error.response.status_code if error.response is not None else "no response"
        logger.warning("Couldn't send a Telegram notification: %s", status)
