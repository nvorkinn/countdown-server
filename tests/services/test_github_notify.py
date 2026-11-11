import json

import pytest
import responses

from broker.services import github_notify
from broker.services.github_notify import Message


def api(method: str) -> str:
    return github_notify.API_URL.format(token="123:bot", method=method)


SEND = api("sendMessage")


def sent_json(call) -> dict:
    """Return the JSON a call to Telegram sent."""
    body = call.request.body
    assert isinstance(body, bytes)
    return json.loads(body)


REPO = {"full_name": "nvorkinn/countdown"}
PULL = {"number": 7, "title": "Add parcels", "html_url": "https://github.com/nvorkinn/countdown/pull/7"}
MERGED = {"action": "closed", "repository": REPO, "pull_request": PULL | {"merged": True}}


@pytest.fixture
def ctx(app):
    app.config.update(TELEGRAM_BOT_TOKEN="123:bot", TELEGRAM_CHAT_ID="42")
    with app.app_context():
        yield app


def test_a_merge_is_named_with_its_link():
    assert github_notify.message("pull_request", MERGED) == Message(
        "Merged nvorkinn/countdown#7: Add parcels\nhttps://github.com/nvorkinn/countdown/pull/7"
    )


def test_a_closed_pr_that_didnt_merge_is_quiet():
    closed = MERGED | {"pull_request": PULL | {"merged": False}}
    assert github_notify.message("pull_request", closed) is None


def test_requested_changes_are_sent_but_approvals_are_not():
    review = {"action": "submitted", "repository": REPO, "pull_request": PULL}
    requested = github_notify.message("pull_request_review", review | {"review": {"state": "changes_requested"}})
    assert requested is not None
    assert requested.text.startswith("Changes requested on nvorkinn/countdown#7")
    assert github_notify.message("pull_request_review", review | {"review": {"state": "approved"}}) is None


@pytest.mark.parametrize(("conclusion", "sent"), [("failure", True), ("timed_out", True), ("success", False)])
def test_only_failed_checks_on_a_pr_are_sent(conclusion, sent):
    suite = {"conclusion": conclusion, "pull_requests": [{"number": 7}]}
    found = github_notify.message("check_suite", {"action": "completed", "repository": REPO, "check_suite": suite})
    assert (found is not None) is sent
    if found:
        assert found.text == "Checks failed in nvorkinn/countdown\nhttps://github.com/nvorkinn/countdown/pull/7"


def test_failed_checks_with_no_pr_are_quiet():
    suite = {"conclusion": "failure", "pull_requests": []}
    assert (
        github_notify.message("check_suite", {"action": "completed", "repository": REPO, "check_suite": suite}) is None
    )


def test_a_finished_release_says_how_it_went_but_other_runs_are_quiet():
    run = {"name": "Release", "conclusion": "failure", "html_url": "https://github.com/run/1"}
    payload = {"action": "completed", "repository": REPO, "workflow_run": run}
    assert github_notify.message("workflow_run", payload) == Message(
        "Release in nvorkinn/countdown: failure\nhttps://github.com/run/1"
    )
    assert github_notify.message("workflow_run", payload | {"workflow_run": run | {"name": "Test"}}) is None


def test_a_release_waiting_for_approval_repeats_by_run_not_by_job_and_links_its_run():
    job = {"workflow_name": "Release", "run_id": 99, "html_url": "https://github.com/job/1"}
    found = github_notify.message("workflow_job", {"action": "waiting", "repository": REPO, "workflow_job": job})
    run = "https://github.com/nvorkinn/countdown/actions/runs/99"
    assert found == Message(f"A release in nvorkinn/countdown is waiting for your approval\n{run}", "waiting:99", run)


@responses.activate
def test_a_message_goes_to_the_chat(ctx):
    sent = responses.post(SEND, json={"ok": True})
    github_notify.notify("delivery-1", "pull_request", MERGED)
    assert len(sent.calls) == 1
    message = sent_json(sent.calls[0])
    assert message["chat_id"] == "42"
    assert message["text"].startswith("Merged nvorkinn/countdown#7")


@responses.activate
def test_nothing_is_sent_while_the_dashboard_is_open(ctx):
    sent = responses.post(SEND, json={"ok": True})
    github_notify.mark_seen()
    github_notify.notify("delivery-1", "pull_request", MERGED)
    assert not sent.calls
    assert ctx.extensions["github_cache"].exists(github_notify.SEEN)


@responses.activate
def test_a_redelivery_and_a_second_waiting_job_are_sent_once(ctx):
    sent = responses.post(SEND, json={"ok": True})
    github_notify.notify("delivery-1", "pull_request", MERGED)
    github_notify.notify("delivery-1", "pull_request", MERGED)
    job = {"workflow_name": "Release", "run_id": 99, "html_url": "https://github.com/job/1"}
    waiting = {"action": "waiting", "repository": REPO, "workflow_job": job}
    github_notify.notify("delivery-2", "workflow_job", waiting)
    github_notify.notify("delivery-3", "workflow_job", waiting | {"workflow_job": job | {"html_url": "job/2"}})
    assert len(sent.calls) == 2


@responses.activate
def test_nothing_is_sent_without_a_bot(ctx):
    sent = responses.post(SEND, json={"ok": True})
    ctx.config["TELEGRAM_CHAT_ID"] = ""
    github_notify.notify("delivery-1", "pull_request", MERGED)
    assert not sent.calls


@responses.activate
def test_a_failed_send_is_logged_without_the_token(ctx, caplog):
    responses.post(SEND, status=401)
    github_notify.notify("delivery-1", "pull_request", MERGED)
    assert "Couldn't call Telegram's sendMessage: 401" in caplog.text
    assert "123:bot" not in caplog.text


@responses.activate
def test_an_odd_delivery_is_logged_not_raised(ctx, caplog):
    sent = responses.post(SEND, json={"ok": True})
    github_notify.notify("delivery-1", "pull_request", {"action": "closed", "repository": REPO})
    assert not sent.calls
    assert "unexpected shape for a notification" in caplog.text


JOB = {"workflow_name": "Release", "run_id": 99, "html_url": "https://github.com/job/1"}
WAITING = {"action": "waiting", "repository": REPO, "workflow_job": JOB}


@responses.activate
def test_a_waiting_release_gets_a_button_that_opens_its_run(ctx):
    sent = responses.post(SEND, json={"ok": True})
    github_notify.notify("delivery-1", "workflow_job", WAITING)
    button = sent_json(sent.calls[0])["reply_markup"]["inline_keyboard"][0][0]
    assert button == {"text": "Review in GitHub", "url": "https://github.com/nvorkinn/countdown/actions/runs/99"}


@responses.activate
def test_other_messages_have_no_button(ctx):
    sent = responses.post(SEND, json={"ok": True})
    github_notify.notify("delivery-1", "pull_request", MERGED)
    assert "reply_markup" not in sent_json(sent.calls[0])
