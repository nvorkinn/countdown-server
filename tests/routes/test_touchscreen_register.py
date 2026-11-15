"""Test /register: a touchscreen registered and claimed by the person signed in on it, in one request.

The settings site's login (Tinyauth and Pocket ID, in Caddy) has already signed the person in when the request gets
here, so the test client's login headers stand in for it.
"""

import hashlib

from broker.auth import hash_secret
from broker.db import db
from broker.models import Device, DeviceConfig, PairingCode
from broker.services.pair_throttle import MAX_REGISTRATION_LOCKOUT_SECONDS, REGISTRATIONS_PER_MINUTE, PairThrottle
from tests.conftest import USER

SECRET = "touchscreen-secret-0123456789abcdef"
SECRET_HASH = hash_secret(SECRET)
OTHER_USER = "someone-else"


def _url(secret_hash=SECRET_HASH, width="1280", height="800"):
    return f"/register?secret_hash={secret_hash}&width={width}&height={height}"


def _open(client, url=None, site="none"):
    """Open the link as the tablet does (typed or opened by its app: Sec-Fetch-Site none), or from `site`."""
    headers = {} if site is None else {"Sec-Fetch-Site": site}
    return client.get(url or _url(), headers=headers)


def _device(app):
    with app.app_context():
        device = db.session.query(Device).filter_by(display_secret_hash=SECRET_HASH).one_or_none()
        if device is not None:
            db.session.expunge(device)
        return device


def test_one_visit_registers_the_touchscreen_and_makes_the_signed_in_user_its_owner(app, client):
    response = _open(client)

    assert response.status_code == 200
    assert b"Your touchscreen is set up" in response.data
    device = _device(app)
    assert device.type == "touchscreen"
    assert device.owner == USER
    assert device.paired_at is not None
    with app.app_context():
        config = db.session.get_one(DeviceConfig, device.device_id)
        assert (config.width, config.height) == (1280, 800)
        assert db.session.query(PairingCode).count() == 0


def test_the_device_then_reads_its_config_with_its_secret(app, client, anonymous_client):
    _open(client)

    response = anonymous_client.get("/api/devices/config", headers={"Authorization": f"Bearer {SECRET}"})

    assert response.status_code == 200
    assert response.get_json()["device_id"] == _device(app).device_id


def test_a_repeat_is_the_same_device_and_still_the_users(app, client):
    _open(client)
    first = _device(app).device_id

    response = _open(client)

    assert response.status_code == 200
    assert b"already yours" in response.data
    assert _device(app).device_id == first
    with app.app_context():
        assert db.session.query(Device).count() == 1


def test_a_touchscreen_someone_else_owns_is_refused_and_left_theirs(app, client):
    _open(client)
    other = app.test_client()
    other.environ_base["HTTP_REMOTE_SUB"] = OTHER_USER

    response = _open(other)

    assert response.status_code == 409
    assert _device(app).owner == USER


def test_the_logins_redirects_back_count_as_this_site(app, client):
    assert _open(client, site="same-site").status_code == 200
    assert _device(app).owner == USER


def test_a_link_from_another_site_only_asks_to_confirm(app, client):
    response = _open(client, site="cross-site")

    assert response.status_code == 200
    assert b"Add this touchscreen to your account?" in response.data
    assert _device(app) is None


def test_a_browser_that_doesnt_say_where_it_came_from_asks_to_confirm(app, client):
    response = _open(client, site=None)

    assert b"Add this touchscreen to your account?" in response.data
    assert _device(app) is None


def test_confirming_from_this_site_registers_it(app, client):
    form = {"secret_hash": SECRET_HASH, "width": "1280", "height": "800"}

    response = client.post("/register", data=form, headers={"Sec-Fetch-Site": "same-origin"})

    assert response.status_code == 200
    assert _device(app).owner == USER


def test_confirming_without_fetch_metadata_checks_the_origin(app, client):
    form = {"secret_hash": SECRET_HASH, "width": "1280", "height": "800"}

    assert client.post("/register", data=form, headers={"Origin": "https://evil.example"}).status_code == 403
    assert _device(app) is None
    assert client.post("/register", data=form, headers={"Origin": "http://localhost"}).status_code == 200
    assert _device(app).owner == USER


def test_a_post_from_another_site_is_refused(app, client):
    form = {"secret_hash": SECRET_HASH, "width": "1280", "height": "800"}

    response = client.post("/register", data=form, headers={"Sec-Fetch-Site": "cross-site"})

    assert response.status_code == 403
    assert _device(app) is None


def test_a_hash_or_size_that_isnt_one_is_refused(app, client):
    for url in (
        _url(secret_hash="not-a-hash"),
        _url(secret_hash=hashlib.sha256(b"x").hexdigest()[:63]),
        _url(width="0"),
        _url(height="-5"),
        _url(width="12.5"),
        _url(width="100000"),
        "/register",
    ):
        assert _open(client, url).status_code == 400, url
    with app.app_context():
        assert db.session.query(Device).count() == 0


def test_without_the_login_it_is_refused(app, anonymous_client):
    assert _open(anonymous_client).status_code == 401
    assert _device(app) is None


def test_too_many_from_one_ip_are_throttled_like_the_device_apis_register(app, client):
    # The real limit, which the app fixture lifts.
    app.extensions["register_throttle"] = PairThrottle(
        max_events=REGISTRATIONS_PER_MINUTE, max_lockout=MAX_REGISTRATION_LOCKOUT_SECONDS
    )
    urls = [_url(secret_hash=hash_secret(f"secret-{n:032d}")) for n in range(REGISTRATIONS_PER_MINUTE + 1)]

    statuses = [_open(client, url).status_code for url in urls]

    assert statuses == [200] * REGISTRATIONS_PER_MINUTE + [429]
