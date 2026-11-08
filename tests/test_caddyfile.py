"""The routes in docker-compose.yml's labels (caddy-docker-proxy makes Caddyfile of them) and the access rules."""

import re
from fnmatch import fnmatchcase
from pathlib import Path

import yaml

COMPOSE = yaml.safe_load((Path(__file__).parent.parent / "docker-compose.yml").read_text())
SERVICES = COMPOSE["services"]


def _labels(service):
    return SERVICES[service]["labels"]


def _paths_without_login():
    """Return the paths the settings site's @browser matcher leaves out of the login."""
    return _labels("countdown-server")["caddy_0.@browser.not.path"].split()


def test_device_routes_are_not_behind_the_browser_login(app):
    """A device can't sign in: behind forward auth it gets the login page as HTML instead of JSON."""
    paths = _paths_without_login()
    device_routes = [
        rule.rule
        for rule in app.url_map.iter_rules()
        if getattr(app.view_functions[rule.endpoint], "device_auth", False)
    ]
    assert device_routes
    # Caddy's * matches across slashes, as fnmatch's does; a <converter> stands for one segment.
    behind_login = [
        route for route in device_routes if not any(fnmatchcase(re.sub(r"<[^>]+>", "x", route), path) for path in paths)
    ]
    assert behind_login == []


def test_dashboard_is_behind_the_login(app):
    """Its static files are public with the rest of /static/*: code and pictures, no data."""
    paths = _paths_without_login()
    routes = [rule.rule for rule in app.url_map.iter_rules() if rule.endpoint.startswith("github_dashboard.")]
    assert routes
    assert not any(fnmatchcase(route, pattern) for route in routes for pattern in paths)


def test_sign_up_is_public_but_adding_a_screen_is_not():
    """/pair is where a visitor registers with Pocket ID; /pair/add and /pair/token need the login."""
    paths = _paths_without_login()
    assert "/pair" in paths
    assert not any(fnmatchcase(route, pattern) for route in ("/pair/add", "/pair/token") for pattern in paths)


def test_only_the_signed_github_webhook_skips_the_login(app):
    """GitHub can't sign in, so the webhook checks its signature; nothing else of GitHub's is open."""
    paths = _paths_without_login()
    assert "/api/github/webhook" in paths
    assert [rule.rule for rule in app.url_map.iter_rules() if rule.endpoint.startswith("github_webhook.")] == [
        "/api/github/webhook"
    ]


def test_only_telegrams_webhook_skips_the_login(app):
    """Telegram can't sign in either, so its webhook checks the secret token it sends."""
    assert "/api/telegram/webhook" in _paths_without_login()
    assert [rule.rule for rule in app.url_map.iter_rules() if rule.endpoint.startswith("telegram_webhook.")] == [
        "/api/telegram/webhook"
    ]


def test_frame_answers_close_the_connection():
    """A 304 through Caddy has no Content-Length, and the ESP32 then reads until the connection closes.

    Without `Connection: close` that's Caddy's idle timeout, 5 minutes, after every unchanged frame.
    """
    assert _labels("countdown-server")["caddy_0.header"] == "/api/frame Connection close"


def test_the_login_headers_are_stripped_from_every_request():
    """The broker trusts them as the signed-in user, so only forward auth may set them."""
    labels = _labels("countdown-server")
    stripped = {value for key, value in labels.items() if key.startswith("caddy_0.request_header_")}
    snippet = _labels("tinyauth")["caddy_0.forward_auth.copy_headers"].split()
    assert {f"-{header}" for header in snippet} <= stripped


def test_only_the_browser_routes_import_the_login():
    labels = _labels("countdown-server")
    assert labels["caddy_0.handle_1"] == "@browser"
    assert labels["caddy_0.handle_1.import"] == "tinyauth"
    assert not any(key.startswith(f"caddy_0.handle_{n}.import") for key in labels for n in (0, 2))


def test_sign_up_asks_the_login_who_is_calling_but_serves_the_page_either_way():
    """/pair is public, yet a signed-in visitor gets the Remote-* headers (so a second screen needs no form)."""
    labels = _labels("countdown-server")
    prefix = "caddy_0.handle_0.forward_auth"
    assert labels["caddy_0.@pair.path"] == "/pair"
    assert labels["caddy_0.handle_0"] == "@pair"
    assert labels[prefix] == "tinyauth:3000"
    assert labels[f"{prefix}.uri"] == "/api/auth/caddy"
    assert set(labels[f"{prefix}.copy_headers"].split()) == {
        "Remote-User", "Remote-Name", "Remote-Email", "Remote-Groups", "Remote-Sub"
    }  # fmt: skip
    # Any refusal from the login carries on without headers: the matched response needs a route, or it is sent on.
    assert labels[f"{prefix}.@anonymous.status"] == "3xx 4xx 5xx"
    assert labels[f"{prefix}.handle_response"] == "@anonymous"
    assert f"{prefix}.handle_response.vars" in labels
    # A visitor's own Remote-* headers are stripped before the login is asked, on every route.
    assert {"-Remote-User", "-Remote-Email", "-Remote-Name", "-Remote-Groups", "-Remote-Sub"} <= {
        value for key, value in labels.items() if key.startswith("caddy_0.request_header_")
    }


def test_bare_domain_redirects_to_the_settings_site():
    """The bare domain and www. have nothing of their own; they send visitors on, keeping the path."""
    labels = _labels("countdown-server")
    assert labels["caddy_1"] == "${APEX_HOST:?}, www.${APEX_HOST}"
    assert labels["caddy_1.redir"] == "https://${AUTH_HOST}{uri}"


def test_the_settings_site_is_for_the_countdown_group():
    labels = _labels("countdown-server")
    assert labels["tinyauth.apps.countdown.oauth.groups"] == "countdown"
    assert labels["tinyauth.apps.countdown.config.domain"] == "${AUTH_HOST}"


def test_the_victoria_uis_are_for_admins_and_only_ingest_skips_the_login():
    for service, host in (("victoria-logs", "LOGS_HOST"), ("victoria-metrics", "METRICS_HOST")):
        labels = _labels(service)
        app = service.removeprefix("victoria-")
        assert labels[f"tinyauth.apps.{app}.oauth.groups"] == "admins"
        assert labels[f"tinyauth.apps.{app}.config.domain"] == "${" + host + "}"
        # The ingest route has the bearer token and no login; the rest imports the login.
        # The token is Caddy's runtime placeholder, never its value: a value in a label shows in `docker inspect` and in
        # the Caddyfile caddy-docker-proxy logs.
        assert labels["caddy.@ingest.header"] == 'Authorization "Bearer {env.LOGS_INGEST_TOKEN}"'
        assert not any(key.startswith("caddy.handle_0.import") for key in labels)
        assert labels["caddy.handle_1.import"] == "tinyauth"


def test_every_protected_route_has_a_group_rule():
    """With the OAuth whitelist open, an app with no group rule would let in any Pocket ID user."""
    for service in SERVICES.values():
        labels = service.get("labels", {})
        imports = any(key.endswith(".import") and value == "tinyauth" for key, value in labels.items())
        apps = {m.group(1) for key in labels if (m := re.match(r"tinyauth\.apps\.([^.]+)\.", key))}
        if imports:
            assert apps
        for app in apps:
            assert f"tinyauth.apps.{app}.oauth.groups" in labels


def test_the_docker_socket_is_only_mounted_in_the_proxy():
    for name, service in SERVICES.items():
        mounted = any("docker.sock" in str(volume) for volume in service.get("volumes", []))
        assert mounted == (name == "socket-proxy")
    assert SERVICES["socket-proxy"]["environment"]["POST"] == "0"
