"""The public sign-up at /pair: Pocket ID is mocked, and the device is linked when the new user first signs in."""

from urllib.parse import parse_qs, urlparse

import pytest
import requests

from broker.clients.pocket_id import PocketIdClient
from broker.db import db
from broker.models import Device, PairingCode, PendingPairing
from broker.services import pairing

POCKET = "https://id.example.com"
API_KEY = "admin-api-key-secret"
GROUP = "11111111-1111-1111-1111-111111111111"
NEW_USER = "22222222-2222-2222-2222-222222222222"
SITE = "https://config.example.com"
FORM = {"name": "Ada Lovelace", "email": "Ada@Example.com"}


class FakePocketId:
    """Stands in for requests.Session, recording each call; `create_status` is what POST /api/users answers."""

    def __init__(self):
        self.calls = []
        self.create_status = 201
        self.token_status = 201
        self.error = None

    def request(self, method, url, json=None, headers=None, timeout=None, **_kwargs):
        self.calls.append((method, url.removeprefix(POCKET), json, headers, timeout))
        if self.error:
            raise self.error
        response = requests.Response()
        path = url.removeprefix(POCKET)
        if (method, path) == ("POST", "/api/users"):
            response.status_code = self.create_status
            response._content = b'{"id": "%s"}' % NEW_USER.encode()
        elif path.endswith("/one-time-access-token"):
            response.status_code = self.token_status
            response._content = b'{"token": "tok-123"}'
        else:
            response.status_code = 204
        return response

    def paths(self):
        return [(method, path) for method, path, *_ in self.calls]


@pytest.fixture
def pocket(app, monkeypatch):
    app.config.update(
        POCKET_ID_URL=POCKET, POCKET_ID_API_KEY=API_KEY, POCKET_ID_COUNTDOWN_GROUP_ID=GROUP, SITE_URL=SITE
    )
    fake = FakePocketId()
    real_init = PocketIdClient.__init__
    monkeypatch.setattr(PocketIdClient, "__init__", lambda self, *a, **k: real_init(self, *a, **{**k, "session": fake}))
    return fake


@pytest.fixture
def code(app, register_device):
    device_id, _ = register_device()
    with app.app_context():
        return pairing.issue_code(device_id), device_id


def test_the_page_is_public_and_fills_in_the_code(anonymous_client):
    page = anonymous_client.get("/pair?code=abc234")
    assert page.status_code == 200
    assert b'value="ABC234"' in page.data


def test_signing_up_creates_the_user_in_the_group_and_sends_them_to_set_a_passkey(app, anonymous_client, pocket, code):
    pairing_code, device_id = code

    response = anonymous_client.post("/pair", data={"code": pairing_code, **FORM})

    assert response.status_code == 303
    method, path, body, headers, timeout = pocket.calls[0]
    assert (method, path) == ("POST", "/api/users")
    assert body["email"] == "ada@example.com"
    assert body["userGroupIds"] == [GROUP]
    assert (body["firstName"], body["lastName"], body["isAdmin"]) == ("Ada", "Lovelace", False)
    assert headers["X-API-Key"] == API_KEY
    assert timeout
    method, path, body, *_ = pocket.calls[1]
    assert (method, path) == ("POST", f"/api/users/{NEW_USER}/one-time-access-token")
    assert body == {"ttl": "10m"}

    target = urlparse(response.headers["Location"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == f"{POCKET}/lc/tok-123"
    assert parse_qs(target.query)["redirect"] == [SITE]
    assert API_KEY not in response.headers["Location"]
    # The code is spent, and the device is held for the new user rather than handed over.
    with app.app_context():
        assert db.session.get(PairingCode, pairing_code) is None
        assert db.session.get_one(PendingPairing, NEW_USER).device_id == device_id
        assert db.session.get_one(Device, device_id).owner is None


def test_the_device_is_linked_when_the_new_user_first_arrives_signed_in(app, anonymous_client, pocket, code):
    pairing_code, device_id = code
    anonymous_client.post("/pair", data={"code": pairing_code, **FORM})

    # What the proxy sends once Tinyauth has logged them in through Pocket ID.
    anonymous_client.get("/device", headers={"Remote-Sub": NEW_USER, "Remote-Name": "Ada Lovelace"})

    with app.app_context():
        device = db.session.get_one(Device, device_id)
        assert device.owner == NEW_USER
        assert device.paired_at is not None
        assert db.session.get(PendingPairing, NEW_USER) is None


def test_someone_else_signing_in_does_not_get_the_reserved_device(app, anonymous_client, pocket, code):
    pairing_code, device_id = code
    anonymous_client.post("/pair", data={"code": pairing_code, **FORM})

    anonymous_client.get("/device", headers={"Remote-Sub": "someone-else"})

    with app.app_context():
        assert db.session.get_one(Device, device_id).owner is None


def test_an_existing_email_is_sent_to_login_and_never_given_a_token(app, anonymous_client, pocket, code):
    pairing_code, device_id = code
    pocket.create_status = 409

    response = anonymous_client.post("/pair", data={"code": pairing_code, **FORM})

    assert response.status_code == 409
    # The login link leads on to the page that pairs from the code, so it isn't typed again.
    assert f"{SITE}/pair/add?code={pairing_code}".encode() in response.data
    assert pocket.paths() == [("POST", "/api/users")]  # no token, no group change
    # The code isn't used up by that, and nothing is reserved.
    with app.app_context():
        assert db.session.get(PairingCode, pairing_code) is not None
        assert db.session.scalar(db.select(PendingPairing)) is None
        assert db.session.get_one(Device, device_id).owner is None


def test_a_wrong_code_never_reaches_pocket_id(anonymous_client, pocket):
    response = anonymous_client.post("/pair", data={"code": "NOPE12", **FORM})

    assert response.status_code == 400
    assert pocket.calls == []


def test_a_code_is_single_use(anonymous_client, pocket, code):
    pairing_code, _ = code
    assert anonymous_client.post("/pair", data={"code": pairing_code, **FORM}).status_code == 303
    pocket.calls.clear()

    again = anonymous_client.post("/pair", data={"code": pairing_code, "name": "Eve", "email": "eve@example.com"})

    assert again.status_code == 400
    assert pocket.calls == []


@pytest.mark.parametrize(
    "form", [{"name": "", "email": "a@b.co"}, {"name": "Ada", "email": "not-an-email"}, {"name": "Ada", "email": ""}]
)
def test_bad_details_are_refused_before_pocket_id(anonymous_client, pocket, code, form):
    response = anonymous_client.post("/pair", data={"code": code[0], **form})

    assert response.status_code == 400
    assert pocket.calls == []


def test_a_failed_token_takes_the_account_back_and_keeps_the_code(app, anonymous_client, pocket, code):
    pairing_code, _ = code
    pocket.token_status = 500

    response = anonymous_client.post("/pair", data={"code": pairing_code, **FORM})

    assert response.status_code == 502
    assert pocket.paths()[-1] == ("DELETE", f"/api/users/{NEW_USER}")
    with app.app_context():
        assert db.session.get(PairingCode, pairing_code) is not None


def test_an_unreachable_pocket_id_is_a_clean_error_that_does_not_leak_the_key(anonymous_client, pocket, code, caplog):
    pocket.error = requests.ConnectionError(f"boom {API_KEY}")

    response = anonymous_client.post("/pair", data={"code": code[0], **FORM})

    assert response.status_code == 502
    assert API_KEY not in response.get_data(as_text=True)
    assert API_KEY not in caplog.text


def test_sign_up_is_off_until_pocket_id_is_configured(app, anonymous_client, code):
    app.config.update(POCKET_ID_URL="", POCKET_ID_API_KEY="", POCKET_ID_COUNTDOWN_GROUP_ID="")

    assert anonymous_client.post("/pair", data={"code": code[0], **FORM}).status_code == 503


def test_sign_ups_are_rate_limited_per_ip(anonymous_client, pocket):
    statuses = [anonymous_client.post("/pair", data={"code": "NOPE12", **FORM}).status_code for _ in range(6)]

    assert statuses[-1] == 429
    assert "Retry-After" in anonymous_client.post("/pair", data={"code": "NOPE12", **FORM}).headers


def test_a_signed_in_user_is_sent_to_add_a_screen_instead(client):
    response = client.get("/pair?code=ABC234")

    assert response.status_code == 302
    assert response.headers["Location"] == "/pair/add?code=ABC234"


def test_the_api_key_is_never_sent_with_a_page(anonymous_client, pocket):
    assert API_KEY not in anonymous_client.get("/pair").get_data(as_text=True)


@pytest.fixture
def screen_token(app, register_device):
    """Register an unowned screen and return the pairing token it sends its owner's browser, and its id."""
    device_id, _ = register_device()
    with app.app_context():
        return db.session.get_one(Device, device_id).pairing_token, device_id


def test_a_screen_link_with_a_token_asks_only_for_name_and_email(anonymous_client, screen_token):
    token, _ = screen_token

    page = anonymous_client.get(f"/pair?t={token}")

    assert page.status_code == 200
    assert f'name="token" value="{token}"'.encode() in page.data
    assert b'name="code"' not in page.data


def test_a_malformed_token_is_ignored_and_the_code_field_comes_back(anonymous_client):
    page = anonymous_client.get("/pair?t=not-a-token")

    assert b'name="token"' not in page.data
    assert b'name="code"' in page.data


def test_signing_up_with_the_screens_token_needs_no_code(app, anonymous_client, pocket, screen_token):
    token, device_id = screen_token

    response = anonymous_client.post("/pair", data={"token": token, **FORM})

    assert response.status_code == 303
    assert pocket.paths()[0] == ("POST", "/api/users")
    with app.app_context():
        assert db.session.get_one(PendingPairing, NEW_USER).device_id == device_id
        assert db.session.get_one(Device, device_id).owner is None  # theirs once they sign in

    anonymous_client.get("/device", headers={"Remote-Sub": NEW_USER, "Remote-Name": "Ada Lovelace"})
    with app.app_context():
        assert db.session.get_one(Device, device_id).owner == NEW_USER


def test_a_token_no_screen_has_registered_with_creates_no_account(app, anonymous_client, pocket):
    response = anonymous_client.post("/pair", data={"token": "a" * 64, **FORM})

    assert response.status_code == 409
    assert b"connected yet" in response.data
    assert pocket.calls == []  # nothing was sent to Pocket ID
    with app.app_context():
        assert db.session.scalar(db.select(PendingPairing)) is None


def test_a_token_for_a_screen_that_already_has_an_owner_creates_no_account(app, anonymous_client, pocket, screen_token):
    token, device_id = screen_token
    with app.app_context():
        db.session.get_one(Device, device_id).owner = "someone-else"
        db.session.commit()

    response = anonymous_client.post("/pair", data={"token": token, **FORM})

    assert response.status_code == 409
    assert pocket.calls == []


def test_an_existing_email_with_a_token_is_sent_back_through_login_to_the_token_page(
    anonymous_client, pocket, screen_token
):
    token, _ = screen_token
    pocket.create_status = 409

    response = anonymous_client.post("/pair", data={"token": token, **FORM})

    assert response.status_code == 409
    assert f"{SITE}/pair/add?t={token}".encode() in response.data
    assert pocket.paths() == [("POST", "/api/users")]  # no token made for an existing account
