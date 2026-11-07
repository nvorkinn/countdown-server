import json

import fakeredis
import pytest
import responses

from broker.services import github_notify

SEND = github_notify.SEND_URL.format(token="123:bot")
REPO = {"full_name": "nvorkinn/countdown"}
PULL = {"number": 7, "title": "Add parcels", "html_url": "https://github.com/nvorkinn/countdown/pull/7"}
MERGED = {"action": "closed", "repository": REPO, "pull_request": PULL | {"merged": True}}


@pytest.fixture
def ctx(app):
    app.config.update(TELEGRAM_BOT_TOKEN="123:bot", TELEGRAM_CHAT_ID="42")
    app.extensions["redis"] = fakeredis.FakeRedis(decode_responses=True)
    with app.app_context():
        yield app


def test_a_merge_is_named_with_its_link():
    assert github_notify.message("pull_request", MERGED) == (
        "Merged nvorkinn/countdown#7: Add parcels\nhttps://github.com/nvorkinn/countdown/pull/7",
        "",
    )


def test_a_closed_pr_that_didnt_merge_is_quiet():
    closed = MERGED | {"pull_request": PULL | {"merged": False}}
    assert github_notify.message("pull_request", closed) is None


def test_requested_changes_are_sent_but_approvals_are_not():
    review = {"action": "submitted", "repository": REPO, "pull_request": PULL}
    requested = github_notify.message("pull_request_review", review | {"review": {"state": "changes_requested"}})
    assert requested is not None
    assert requested[0].startswith("Changes requested on nvorkinn/countdown#7")
    assert github_notify.message("pull_request_review", review | {"review": {"state": "approved"}}) is None


@pytest.mark.parametrize(("conclusion", "sent"), [("failure", True), ("timed_out", True), ("success", False)])
def test_only_failed_checks_on_a_pr_are_sent(conclusion, sent):
    suite = {"conclusion": conclusion, "pull_requests": [{"number": 7}]}
    found = github_notify.message("check_suite", {"action": "completed", "repository": REPO, "check_suite": suite})
    assert (found is not None) is sent
    if found:
        assert found[0] == "Checks failed in nvorkinn/countdown\nhttps://github.com/nvorkinn/countdown/pull/7"


def test_failed_checks_with_no_pr_are_quiet():
    suite = {"conclusion": "failure", "pull_requests": []}
    assert (
        github_notify.message("check_suite", {"action": "completed", "repository": REPO, "check_suite": suite}) is None
    )


def test_a_finished_release_says_how_it_went_but_other_runs_are_quiet():
    run = {"name": "Release", "conclusion": "failure", "html_url": "https://github.com/run/1"}
    payload = {"action": "completed", "repository": REPO, "workflow_run": run}
    assert github_notify.message("workflow_run", payload) == (
        "Release in nvorkinn/countdown: failure\nhttps://github.com/run/1",
        "",
    )
    assert github_notify.message("workflow_run", payload | {"workflow_run": run | {"name": "Test"}}) is None


def test_a_release_waiting_for_approval_repeats_by_run_not_by_job():
    job = {"workflow_name": "Release", "run_id": 99, "html_url": "https://github.com/job/1"}
    found = github_notify.message("workflow_job", {"action": "waiting", "repository": REPO, "workflow_job": job})
    assert found == (
        "A release in nvorkinn/countdown is waiting for your approval\nhttps://github.com/job/1",
        "waiting:99",
    )


@responses.activate
def test_a_message_goes_to_the_chat(ctx):
    sent = responses.post(SEND, json={"ok": True})
    github_notify.notify("delivery-1", "pull_request", MERGED)
    assert len(sent.calls) == 1
    body = sent.calls[0].request.body
    assert isinstance(body, bytes)
    sent_json = json.loads(body)
    assert sent_json["chat_id"] == "42"
    assert sent_json["text"].startswith("Merged nvorkinn/countdown#7")


@responses.activate
def test_nothing_is_sent_while_the_dashboard_is_open(ctx):
    sent = responses.post(SEND, json={"ok": True})
    github_notify.mark_seen()
    github_notify.notify("delivery-1", "pull_request", MERGED)
    assert not sent.calls
    assert 0 < ctx.extensions["redis"].ttl(github_notify.SEEN) <= github_notify.SEEN_S


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
    assert "Couldn't send a Telegram notification: 401" in caplog.text
    assert "123:bot" not in caplog.text


@responses.activate
def test_an_odd_delivery_is_logged_not_raised(ctx, caplog):
    sent = responses.post(SEND, json={"ok": True})
    github_notify.notify("delivery-1", "pull_request", {"action": "closed", "repository": REPO})
    assert not sent.calls
    assert "unexpected shape for a notification" in caplog.text
