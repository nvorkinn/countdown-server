import json

import fakeredis
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
    app.config.update(TELEGRAM_BOT_TOKEN="123:bot", TELEGRAM_CHAT_ID="42", TELEGRAM_WEBHOOK_SECRET="")
    app.extensions["redis"] = fakeredis.FakeRedis(decode_responses=True)
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


def test_a_release_waiting_for_approval_repeats_by_run_not_by_job_and_offers_approval():
    job = {"workflow_name": "Release", "run_id": 99, "html_url": "https://github.com/job/1"}
    found = github_notify.message("workflow_job", {"action": "waiting", "repository": REPO, "workflow_job": job})
    assert found == Message(
        "A release in nvorkinn/countdown is waiting for your approval\nhttps://github.com/job/1",
        "waiting:99",
        ("nvorkinn/countdown", 99),
    )


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
def test_a_waiting_release_gets_an_approve_button_only_with_the_webhook(ctx):
    sent = responses.post(SEND, json={"ok": True})
    github_notify.notify("delivery-1", "workflow_job", WAITING)
    assert "reply_markup" not in sent_json(sent.calls[0])
    ctx.config["TELEGRAM_WEBHOOK_SECRET"] = "s3cret"
    github_notify.notify("delivery-2", "workflow_job", WAITING | {"workflow_job": JOB | {"run_id": 100}})
    button = sent_json(sent.calls[1])["reply_markup"]["inline_keyboard"][0][0]
    assert button == {"text": "Approve", "callback_data": "approve:100"}
    assert ctx.extensions["redis"].get(github_notify.APPROVE.format(100)) == "nvorkinn/countdown"


def tap(data="approve:99", user=42):
    return {
        "callback_query": {
            "id": "q1",
            "from": {"id": user},
            "data": data,
            "message": {"message_id": 5, "chat": {"id": 42}, "text": "A release is waiting"},
        }
    }


class FakeDashboard:
    def __init__(self, pending):
        self.pending = pending
        self.approved = []

    def owned_repository(self, owner, name):
        return {"full_name": f"{owner}/{name}"}

    def pending_deployments(self, repo, run_id):  # noqa: ARG002
        return self.pending

    def approve_deployment(self, repo, run_id, environment_id):
        self.approved.append((repo["full_name"], run_id, environment_id))


@pytest.fixture
def dashboard(ctx, monkeypatch):
    ctx.config["GITHUB_DASHBOARD_USER_ID"] = "user-1"
    ctx.extensions["redis"].set(github_notify.APPROVE.format(99), "nvorkinn/countdown")
    fake = FakeDashboard([{"id": 1, "can_approve": True}, {"id": 2, "can_approve": False}])
    monkeypatch.setattr(github_notify.github_app, "token", lambda user_id: f"token-for-{user_id}")
    monkeypatch.setattr(github_notify.github, "Dashboard", lambda _token, _redis: fake)
    return fake


@responses.activate
def test_a_tap_approves_what_you_may_and_removes_the_button(ctx, dashboard):
    answered = responses.post(api("answerCallbackQuery"), json={"ok": True})
    edited = responses.post(api("editMessageText"), json={"ok": True})
    github_notify.answer(tap())
    assert dashboard.approved == [("nvorkinn/countdown", 99, 1)]
    assert sent_json(answered.calls[0])["text"] == "Approved"
    edit = sent_json(edited.calls[0])
    assert edit["text"] == "A release is waiting\n\nApproved ✅"
    assert "reply_markup" not in edit
    assert not ctx.extensions["redis"].exists(github_notify.APPROVE.format(99))


@responses.activate
def test_a_tap_from_someone_else_approves_nothing(ctx, dashboard):
    answered = responses.post(api("answerCallbackQuery"), json={"ok": True})
    github_notify.answer(tap(user=666))
    assert not dashboard.approved
    assert sent_json(answered.calls[0])["text"] == "This isn't yours to approve."


@responses.activate
@pytest.mark.parametrize("data", ["approve:12345", "approve:", "deny:99", "approve:../x"])
def test_a_tap_for_no_waiting_release_approves_nothing(ctx, dashboard, data):
    answered = responses.post(api("answerCallbackQuery"), json={"ok": True})
    github_notify.answer(tap(data))
    assert not dashboard.approved
    assert sent_json(answered.calls[0])["text"] == "This release isn't waiting."


@responses.activate
def test_a_tap_with_nothing_you_may_approve_keeps_the_button(ctx, dashboard):
    dashboard.pending = [{"id": 2, "can_approve": False}]
    answered = responses.post(api("answerCallbackQuery"), json={"ok": True})
    edited = responses.post(api("editMessageText"), json={"ok": True})
    github_notify.answer(tap())
    assert sent_json(answered.calls[0])["text"] == "Nothing in this release is waiting for your approval."
    assert not edited.calls
