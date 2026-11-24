"""Test a touchscreen's members: people who join it with a code, and whose own Soloist API key it can then ask for.

A Soloist API key is its person's own and not to be shared, so each person of a household saves theirs to their own
account, and the touchscreen asks for the key of whoever is playing (GET /api/devices/soloist/key?user_id=). It is
only ever told about someone who joined it themselves: registering a device is open to anyone.
"""

from datetime import UTC, datetime, timedelta

import pytest
from countdown_api.models import MemberCode as ContractMemberCode
from countdown_api.models import MemberCodeStatus

from broker.db import db
from broker.models import Device, DeviceUser, MemberCode, SoloistKey
from broker.services import members, pairing
from tests.conftest import USER
from tests.routes.test_touchscreens import SECRET, TOUCHSCREEN, _auth, _paired, _register, _registered

CODES_URL = "/api/devices/members/codes"
KEY_URL = "/api/devices/soloist/key"
MEMBER = "member-1"
SAME_SITE = {"Sec-Fetch-Site": "same-origin"}


@pytest.fixture
def member(app):
    """Return a browser signed in as someone else of the household."""
    browser = app.test_client()
    browser.environ_base["HTTP_REMOTE_SUB"] = MEMBER
    return browser


def _code(client):
    response = client.post(CODES_URL, headers=_auth())
    assert response.status_code == 201
    return response.get_json()["code"]


def _join(browser, code):
    return browser.post("/join", data={"code": code}, headers=SAME_SITE)


def _joined(client, browser):
    """Have the browser's person join the touchscreen; return the code they used."""
    code = _code(client)
    assert _join(browser, code).status_code == 200
    return code


def _save_key(app, user_id, key):
    with app.app_context():
        saved = SoloistKey(user_id=user_id)
        saved.key = key
        db.session.add(saved)
        db.session.commit()


def _links(app, device_id):
    with app.app_context():
        return sorted((link.user_id, link.role) for link in db.session.get_one(Device, device_id).users)


# --- the code ---------------------------------------------------------------------------------------


def test_a_paired_touchscreen_gets_a_code_as_the_contract_has_it(app, client):
    _paired(app, client)

    response = client.post(CODES_URL, headers=_auth())

    assert response.status_code == 201
    code = ContractMemberCode.model_validate(response.get_json())
    assert code.expires_in == TOUCHSCREEN["member_code"]["expires_in"]
    assert len(code.code) == len(TOUCHSCREEN["member_code"]["code"])


def test_a_code_needs_the_devices_secret(app, client):
    _paired(app, client)

    assert client.post(CODES_URL).status_code == 401
    assert client.get(f"{CODES_URL}/{'0' * 32}").status_code == 401
    assert client.delete(f"/api/devices/members/{MEMBER}").status_code == 401


def test_an_unpaired_touchscreen_or_an_epaper_screen_has_no_members(app, client):
    _registered(app, client)
    assert client.post(CODES_URL, headers=_auth()).status_code == 409
    assert client.delete(f"/api/devices/members/{MEMBER}", headers=_auth()).status_code == 409

    secret = TOUCHSCREEN["epaper_registration"]["secret"]
    assert _register(client, TOUCHSCREEN["epaper_registration"]).status_code == 201
    assert client.post(CODES_URL, headers=_auth(secret)).status_code == 409
    assert secret != SECRET


def test_a_new_code_replaces_the_one_no_one_used(app, client):
    _paired(app, client)
    first = _code(client)
    second = _code(client)

    assert client.get(f"{CODES_URL}/{first}", headers=_auth()).status_code == 404
    assert client.get(f"{CODES_URL}/{second}", headers=_auth()).get_json() == TOUCHSCREEN["member_code_waiting"]


# --- joining ----------------------------------------------------------------------------------------


def test_the_page_asks_first_and_the_visit_alone_joins_no_one(app, client, member):
    device_id = _paired(app, client)
    code = _code(client)

    page = member.get(f"/join?code={code}")

    assert page.status_code == 200
    assert "Join touchscreen" in page.get_data(as_text=True)
    assert "Soloist" in page.get_data(as_text=True)
    assert _links(app, device_id) == [(USER, "owner")]
    assert client.get(f"{CODES_URL}/{code}", headers=_auth()).get_json() == {"user_id": None}


def test_confirming_joins_them_and_tells_the_touchscreen_who_they_are(app, client, member):
    device_id = _paired(app, client)
    code = _code(client)

    page = _join(member, code)

    assert page.status_code == 200
    assert "joined" in page.get_data(as_text=True)
    assert _links(app, device_id) == [(MEMBER, "member"), (USER, "owner")]
    status = client.get(f"{CODES_URL}/{code}", headers=_auth())
    assert MemberCodeStatus.model_validate(status.get_json()).user_id == MEMBER


def test_joining_needs_a_login(app, client, anonymous_client):
    _paired(app, client)
    code = _code(client)

    assert anonymous_client.get(f"/join?code={code}").status_code == 401
    assert _join(anonymous_client, code).status_code == 401


def test_a_confirmation_from_another_site_joins_no_one(app, client, member):
    device_id = _paired(app, client)
    code = _code(client)

    response = member.post("/join", data={"code": code}, headers={"Sec-Fetch-Site": "cross-site"})

    assert response.status_code == 403
    assert _links(app, device_id) == [(USER, "owner")]


def test_a_code_works_once(app, client, member):
    device_id = _paired(app, client)
    code = _joined(client, member)
    other = app.test_client()
    other.environ_base["HTTP_REMOTE_SUB"] = "member-2"

    assert "expired or has already been used" in _join(other, code).get_data(as_text=True)
    assert "expired or has already been used" in other.get(f"/join?code={code}").get_data(as_text=True)
    assert _links(app, device_id) == [(MEMBER, "member"), (USER, "owner")]


def test_a_lapsed_or_unknown_code_joins_no_one(app, client, member):
    device_id = _paired(app, client)
    code = _code(client)
    with app.app_context():
        db.session.get_one(MemberCode, code).expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.session.commit()

    for spent in (code, "f" * 32, ""):
        assert "expired or has already been used" in _join(member, spent).get_data(as_text=True)
    assert _links(app, device_id) == [(USER, "owner")]
    assert client.get(f"{CODES_URL}/{code}", headers=_auth()).status_code == 404


def test_the_owner_using_a_code_stays_the_owner_and_is_named(app, client):
    device_id = _paired(app, client)
    code = _code(client)

    assert "your own" in _join(client, code).get_data(as_text=True)

    assert _links(app, device_id) == [(USER, "owner")]
    assert client.get(f"{CODES_URL}/{code}", headers=_auth()).get_json() == {"user_id": USER}


def test_joining_twice_is_one_link(app, client, member):
    device_id = _paired(app, client)
    _joined(client, member)
    _joined(client, member)

    assert _links(app, device_id) == [(MEMBER, "member"), (USER, "owner")]


def test_one_touchscreens_code_is_not_anothers_to_read(app, client):
    _paired(app, client)
    code = _code(client)
    other = TOUCHSCREEN["epaper_registration"]["secret"]
    assert _register(client, TOUCHSCREEN["epaper_registration"]).status_code == 201

    assert client.get(f"{CODES_URL}/{code}", headers=_auth(other)).status_code == 404


# --- whose key ---------------------------------------------------------------------------------------


def test_a_member_s_key_is_theirs_and_the_owners_is_the_owners(app, client, member):
    _paired(app, client)
    _joined(client, member)
    _save_key(app, USER, "the-owners-key")
    _save_key(app, MEMBER, "the-members-key")

    assert client.get(KEY_URL, headers=_auth()).get_json() == {"api_key": "the-owners-key"}
    assert client.get(f"{KEY_URL}?user_id={USER}", headers=_auth()).get_json() == {"api_key": "the-owners-key"}
    theirs = client.get(f"{KEY_URL}?user_id={MEMBER}", headers=_auth())
    assert theirs.get_json() == {"api_key": "the-members-key"}
    assert theirs.headers["Cache-Control"] == "no-store"


def test_someone_who_never_joined_is_not_named_whatever_the_device_says(app, client):
    _paired(app, client)
    _save_key(app, MEMBER, "the-members-key")

    response = client.get(f"{KEY_URL}?user_id={MEMBER}", headers=_auth())

    # Anyone can register a device and ask: a key goes only to a touchscreen its person joined themselves.
    assert response.status_code == 404
    assert "the-members-key" not in response.get_data(as_text=True)
    # And it reads the same as a member with no key saved, so a device can't tell who has an account.
    assert response.get_json() == client.get(f"{KEY_URL}?user_id=no-such-user", headers=_auth()).get_json()


def test_a_member_with_no_key_has_none_and_never_gets_the_owners(app, client, member):
    _paired(app, client)
    _joined(client, member)
    _save_key(app, USER, "the-owners-key")

    response = client.get(f"{KEY_URL}?user_id={MEMBER}", headers=_auth())

    assert response.status_code == 404
    assert "the-owners-key" not in response.get_data(as_text=True)


# --- leaving and being removed -----------------------------------------------------------------------


def test_the_touchscreen_removes_a_member_and_is_told_nothing_of_them_again(app, client, member):
    device_id = _paired(app, client)
    _joined(client, member)
    _save_key(app, MEMBER, "the-members-key")

    assert client.delete(f"/api/devices/members/{MEMBER}", headers=_auth()).status_code == 204

    assert _links(app, device_id) == [(USER, "owner")]
    assert client.get(f"{KEY_URL}?user_id={MEMBER}", headers=_auth()).status_code == 404
    # Removing them again changes nothing.
    assert client.delete(f"/api/devices/members/{MEMBER}", headers=_auth()).status_code == 204


def test_the_owner_is_not_a_member_to_remove(app, client):
    device_id = _paired(app, client)

    assert client.delete(f"/api/devices/members/{USER}", headers=_auth()).status_code == 409
    assert _links(app, device_id) == [(USER, "owner")]


def test_a_member_leaves_from_their_account_page(app, client, member):
    device_id = _paired(app, client)
    _joined(client, member)
    _save_key(app, MEMBER, "the-members-key")

    assert member.post(f"/account/screens/{device_id}/leave").status_code == 302

    assert _links(app, device_id) == [(USER, "owner")]
    assert client.get(f"{KEY_URL}?user_id={MEMBER}", headers=_auth()).status_code == 404


def test_leaving_never_unlinks_an_owner_or_anyone_else(app, client, member):
    device_id = _paired(app, client)
    _joined(client, member)

    client.post(f"/account/screens/{device_id}/leave")

    assert _links(app, device_id) == [(MEMBER, "member"), (USER, "owner")]


def test_a_touchscreen_that_changes_hands_keeps_none_of_its_members(app, client, member):
    device_id = _paired(app, client)
    _joined(client, member)
    _save_key(app, MEMBER, "the-members-key")
    unused = _code(client)
    with app.app_context():
        code = pairing.issue_code(device_id)
        assert pairing.redeem_code(code, "new-owner") == device_id

    assert _links(app, device_id) == [("new-owner", "owner")]
    assert client.get(f"{KEY_URL}?user_id={MEMBER}", headers=_auth()).status_code == 404
    # Nor can a code its last owner's touchscreen showed still be joined with.
    assert "expired or has already been used" in _join(member, unused).get_data(as_text=True)


def test_pairing_again_as_the_same_owner_keeps_the_members(app, client, member):
    device_id = _paired(app, client)
    _joined(client, member)
    with app.app_context():
        code = pairing.issue_code(device_id)
        assert pairing.redeem_code(code, USER) == device_id

    assert _links(app, device_id) == [(MEMBER, "member"), (USER, "owner")]


def test_forgetting_a_device_takes_its_members_and_codes(app, client, member):
    device_id = _paired(app, client)
    _joined(client, member)
    with app.app_context():
        db.session.delete(db.session.get_one(Device, device_id))
        db.session.commit()

        assert db.session.scalars(db.select(DeviceUser)).all() == []
        assert db.session.scalars(db.select(MemberCode)).all() == []
        assert members.joined(MEMBER) == []


# --- a member's account ------------------------------------------------------------------------------


def test_someone_with_no_screen_and_none_joined_has_no_account_page(member):
    assert member.get("/account").status_code == 302


def test_a_member_has_an_account_page_with_their_key_and_the_screens_they_joined(app, client, member):
    _paired(app, client)
    _joined(client, member)

    page = member.get("/account").get_data(as_text=True)

    assert "Spotify Soloist" in page
    assert "Touchscreens you've joined" in page
    # What's for screens of one's own isn't there: a member sets none up.
    assert "Glowmarkt" not in page
    assert "GitHub" not in page


def test_a_member_saves_their_own_key_and_never_sees_the_touchscreens_settings(app, client, member):
    device_id = _paired(app, client)
    _joined(client, member)

    assert member.post("/account", data={"soloist_api_key": "the-members-key"}).status_code == 302

    assert client.get(f"{KEY_URL}?user_id={MEMBER}", headers=_auth()).get_json() == {"api_key": "the-members-key"}
    assert "the-members-key" not in member.get("/account").get_data(as_text=True)
    # Its settings stay its owner's.
    assert member.get(f"/device/{device_id}").status_code == 404
    assert member.get(f"/api/devices/config?device_id={device_id}").status_code == 404


def test_an_owner_sees_no_joined_screens_of_their_own(app, client):
    _paired(app, client)

    assert "Touchscreens you've joined" not in client.get("/account").get_data(as_text=True)
