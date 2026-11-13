"""The routes in docker-compose.yml's labels (caddy-docker-proxy makes Caddyfile of them) and the access rules."""

import re
from fnmatch import fnmatchcase
from pathlib import Path

import yaml

COMPOSE = yaml.safe_load((Path(__file__).parent.parent / "docker-compose.yml").read_text())
SERVICES = COMPOSE["services"]


def _labels(service):
    return SERVICES[service]["labels"]


LOGIN_HEADERS = {"Remote-User", "Remote-Name", "Remote-Email", "Remote-Groups", "Remote-Sub"}


def _screen_api_paths():
    """Return the paths the screens' API (API_HOST, LEGACY_HOST) sends to the broker, with no login."""
    return _labels("countdown-server")["caddy_2.@screen_api.path"].split()


def _covered(route, patterns):
    # Caddy's * matches across slashes, as fnmatch's does; a <converter> stands for one segment.
    return any(fnmatchcase(re.sub(r"<[^>]+>", "x", route), pattern) for pattern in patterns)


def test_the_names_are_the_settings_site_the_screens_api_and_the_old_shared_one():
    labels = _labels("countdown-server")
    assert labels["caddy_0"] == "${APP_HOST:?}"
    assert labels["caddy_2"] == "(countdown_screen_api)"
    assert labels["caddy_3"] == "${API_HOST:?}"
    assert labels["caddy_4"] == "${LEGACY_HOST:?}"


def test_every_device_route_is_on_the_screens_api(app):
    """A device can't sign in: behind forward auth it gets the login page as HTML instead of JSON."""
    device_routes = [
        rule.rule
        for rule in app.url_map.iter_rules()
        if getattr(app.view_functions[rule.endpoint], "device_auth", False)
    ]
    assert device_routes
    assert [route for route in device_routes if not _covered(route, _screen_api_paths())] == []


def test_the_screens_api_serves_nothing_from_the_settings_site(app):
    """Only device routes and the status endpoint (its own token) are on API_HOST: no page, no browser call."""
    pages = [rule.rule for rule in app.url_map.iter_rules() if rule.endpoint.startswith(("pages.", "account."))]
    assert pages
    assert [route for route in pages if _covered(route, _screen_api_paths())] == []
    for site in ("caddy_3", "caddy_4"):
        assert _labels("countdown-server")[f"{site}.import"] == "countdown_screen_api"


def test_the_api_name_answers_404_and_the_old_name_redirects_to_the_settings_site():
    labels = _labels("countdown-server")
    assert labels["caddy_3.handle.respond"] == "404"
    assert labels["caddy_4.handle.redir"] == "https://${APP_HOST}{uri}"


def test_sign_up_is_public_but_adding_a_screen_is_not():
    """/pair is where a visitor registers with Pocket ID; /pair/add and /pair/token need the login."""
    labels = _labels("countdown-server")
    public = labels["caddy_0.@pair.path"].split() + labels["caddy_0.@public.path"].split()
    assert "/pair" in public
    assert not any(fnmatchcase(route, pattern) for route in ("/pair/add", "/pair/token") for pattern in public)


def test_only_sign_up_about_and_static_files_skip_the_login():
    labels = _labels("countdown-server")
    assert labels["caddy_0.@public.path"].split() == ["/about", "/static/*"]
    assert labels["caddy_0.handle_1"] == "@public"
    # The catch-all: everything else on the settings site imports the login.
    assert "caddy_0.handle_2" not in labels
    assert labels["caddy_0.handle_2.import"] == "countdown_auth"
    assert not any(key.startswith(f"caddy_0.handle_{n}.import") for key in labels for n in (0, 1))


def test_frame_answers_close_the_connection():
    """A 304 through Caddy has no Content-Length, and the ESP32 then reads until the connection closes.

    Without `Connection: close` that's Caddy's idle timeout, 5 minutes, after every unchanged frame.
    """
    assert _labels("countdown-server")["caddy_2.header"] == "/api/frame Connection close"


def test_the_login_headers_are_stripped_from_every_request():
    """The broker trusts them as the signed-in user, so only forward auth may set them, on every name."""
    labels = _labels("countdown-server")
    snippet = _labels("countdown-tinyauth")["caddy_0.forward_auth.copy_headers"].split()
    assert set(snippet) == LOGIN_HEADERS
    for site in ("caddy_0", "caddy_2"):
        stripped = {value for key, value in labels.items() if key.startswith(f"{site}.request_header_")}
        assert {f"-{header}" for header in snippet} <= stripped, site


def test_sign_up_asks_the_login_who_is_calling_but_serves_the_page_either_way():
    """/pair is public, yet a signed-in visitor gets the Remote-* headers (so a second screen needs no form)."""
    labels = _labels("countdown-server")
    prefix = "caddy_0.handle_0.forward_auth"
    assert labels["caddy_0.@pair.path"] == "/pair"
    assert labels["caddy_0.handle_0"] == "@pair"
    assert labels[prefix] == "countdown-tinyauth:3000"
    assert labels[f"{prefix}.uri"] == "/api/auth/caddy"
    assert set(labels[f"{prefix}.copy_headers"].split()) == LOGIN_HEADERS
    # Any refusal from the login carries on without headers: the matched response needs a route, or it is sent on.
    assert labels[f"{prefix}.@anonymous.status"] == "3xx 4xx 5xx"
    assert labels[f"{prefix}.handle_response"] == "@anonymous"
    assert f"{prefix}.handle_response.vars" in labels


def test_bare_domain_redirects_to_the_settings_site():
    """The bare domain and www. have nothing of their own; they send visitors on, keeping the path."""
    labels = _labels("countdown-server")
    assert labels["caddy_1"] == "${APEX_HOST:?}, www.${APEX_HOST}"
    assert labels["caddy_1.redir"] == "https://${APP_HOST}{uri}"


def test_the_settings_site_is_for_the_countdown_group():
    labels = _labels("countdown-server")
    assert labels["tinyauth.apps.countdown.oauth.groups"] == "countdown"
    assert labels["tinyauth.apps.countdown.config.domain"] == "${APP_HOST}"


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
        assert labels["caddy.handle_1.import"] == "countdown_auth"


def test_every_protected_route_has_a_group_rule():
    """With the OAuth whitelist open, an app with no group rule would let in any Pocket ID user."""
    for service in SERVICES.values():
        labels = service.get("labels", {})
        imports = any(key.endswith(".import") and value == "countdown_auth" for key, value in labels.items())
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
