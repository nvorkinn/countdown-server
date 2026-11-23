"""Test CORS on the routes a touchscreen calls from its browser (broker/cors.py).

Only the tablet app's origins (TOUCHSCREEN_RETURN_ORIGINS) are answered, by name, on its routes only, and the headers
are on every answer, failures included, so the app can read them.
"""

import pytest
from countdown_api import fixtures

from broker.services import pairing
from tests.conftest import name_screen

TOUCHSCREEN = fixtures.load("touchscreen.json")
SECRET = TOUCHSCREEN["registration"]["secret"]
APP_ORIGIN = "https://the-max-mix.countdown.build"
CONFIG_URL = "/api/devices/config"


@pytest.fixture(autouse=True)
def _app_origin(app):
    app.config["TOUCHSCREEN_RETURN_ORIGINS"] = frozenset({APP_ORIGIN})


def _from_app(**headers):
    return {"Origin": APP_ORIGIN, **headers}


def _auth():
    return {"Authorization": f"Bearer {SECRET}"}


@pytest.fixture
def touchscreen(app, client):
    """Register the contract's touchscreen and pair the client's user with it; return its device id."""
    device_id = client.post("/api/devices/register", json=TOUCHSCREEN["registration"]).get_json()["device_id"]
    with app.app_context():
        name_screen(device_id)
        code = pairing.issue_code(device_id)
    assert client.post("/pair/add", data={"code": code}).status_code == 302
    return device_id


@pytest.mark.parametrize(
    ("path", "method"),
    [
        (CONFIG_URL, "GET"),
        (CONFIG_URL, "PUT"),
        (f"{CONFIG_URL}/style", "PUT"),
        ("/api/devices/register", "POST"),
        ("/api/devices/spotify/token", "GET"),
        ("/api/devices/soloist/key", "GET"),
        ("/api/devices/account", "GET"),
        ("/api/stops/search", "GET"),
        ("/api/places/search", "GET"),
        ("/api/places/here", "GET"),
    ],
)
def test_the_app_is_answered_a_preflight_without_a_secret(anonymous_client, path, method):
    response = anonymous_client.options(
        path,
        headers=_from_app(
            **{"Access-Control-Request-Method": method, "Access-Control-Request-Headers": "authorization,if-match"}
        ),
    )

    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == APP_ORIGIN
    assert method in response.headers["Access-Control-Allow-Methods"]
    allowed = {name.strip() for name in response.headers["Access-Control-Allow-Headers"].split(",")}
    assert allowed == {"Authorization", "Content-Type", "If-Match", "If-None-Match"}
    assert int(response.headers["Access-Control-Max-Age"]) > 0
    assert "Access-Control-Allow-Credentials" not in response.headers
    assert "Origin" in response.vary


def test_the_app_can_read_its_config_and_its_version(touchscreen, anonymous_client):
    response = anonymous_client.get(CONFIG_URL, headers=_from_app(**_auth()))

    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == APP_ORIGIN
    assert response.headers["Access-Control-Expose-Headers"] == "ETag"
    assert "Access-Control-Allow-Methods" not in response.headers  # only on a preflight
    assert "Origin" in response.vary


def test_the_app_can_read_why_a_call_failed(touchscreen, anonymous_client):
    etag = anonymous_client.get(CONFIG_URL, headers=_auth()).headers["ETag"]
    style = {"skin": "night", "colour": "navy"}
    saved = anonymous_client.put(f"{CONFIG_URL}/style", json=style, headers=_from_app(**_auth(), **{"If-Match": etag}))
    assert saved.status_code == 200

    failures = [
        anonymous_client.get(CONFIG_URL, headers=_from_app()),
        anonymous_client.put(f"{CONFIG_URL}/style", json=style, headers=_from_app(**_auth(), **{"If-Match": etag})),
        anonymous_client.put(f"{CONFIG_URL}/style", json=style, headers=_from_app(**_auth())),
    ]

    assert [response.status_code for response in failures] == [401, 409, 428]
    for response in failures:
        assert response.headers["Access-Control-Allow-Origin"] == APP_ORIGIN
        assert "Origin" in response.vary


@pytest.mark.parametrize("origin", ["https://evil.example", "https://the-max-mix.countdown.build.evil.example", "null"])
def test_other_origins_get_no_cors_headers(anonymous_client, origin):
    preflight = anonymous_client.options(CONFIG_URL, headers={"Origin": origin, "Access-Control-Request-Method": "GET"})
    call = anonymous_client.get(CONFIG_URL, headers={"Origin": origin})

    for response in (preflight, call):
        assert not [name for name, _value in response.headers if name.lower().startswith("access-control-")]
        assert "Origin" in response.vary


def test_no_cors_without_any_origins_set(app, anonymous_client):
    app.config["TOUCHSCREEN_RETURN_ORIGINS"] = frozenset()

    response = anonymous_client.options(CONFIG_URL, headers=_from_app(**{"Access-Control-Request-Method": "GET"}))

    assert "Access-Control-Allow-Origin" not in response.headers


@pytest.mark.parametrize("path", ["/", "/account", "/register", "/api/frame", "/health"])
def test_the_settings_site_and_the_other_routes_get_no_cors_headers(client, path):
    response = client.get(path, headers=_from_app())

    assert "Access-Control-Allow-Origin" not in response.headers
