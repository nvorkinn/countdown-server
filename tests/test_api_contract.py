"""The broker's HTTP routes against the contract's: countdown-api's store.yaml `paths` (vendor/countdown-api).

Both ways: every route and method the contract has, the broker serves, and every `/api` route the broker serves is in
the contract. So a route renamed on one side only (`/device` for `/devices`), or one added without its contract, fails
here. The pages outside `/api` are the settings site's own: only those the contract names (/register, a device's page)
are checked, one way. A route goes into the contract in the same PR that adds it to the broker; the contract's PR
merges first and the submodule is bumped to it.
"""

import importlib.resources
import json
import re

CONTRACT = json.loads(importlib.resources.files("countdown_api").joinpath("store.json").read_text())

OPERATIONS = frozenset({"get", "put", "post", "delete", "patch"})

# `/api` routes not in the contract yet, each with why. The last test fails once one is in it (or gone from the
# broker), so this only shrinks: add the route to the contract rather than here.
NOT_IN_CONTRACT = {
    ("POST", "/api/frame"): "the e-paper screen's frame poll and log report (countdown-esp32), older than the paths",
    ("GET", "/api/places/search"): "the settings page's place picker; the search endpoints go into the contract next",
    ("GET", "/api/stops/search"): "the settings page's stop picker; the search endpoints go into the contract next",
    ("GET", "/api/status/devices"): "monitoring (a Home Assistant sensor), with its own status token",
    ("GET", "/api/status/health"): "the health check, for monitoring",
}


def _contract_routes() -> dict[tuple[str, str], dict]:
    return {
        (method.upper(), path): operation
        for path, item in CONTRACT["paths"].items()
        for method, operation in item.items()
        if method in OPERATIONS
    }


def _broker_routes(app) -> dict[tuple[str, str], str]:
    """Return each route and method the broker serves, its path in the contract's spelling, and the endpoint."""
    routes = {}
    for rule in app.url_map.iter_rules():
        # Flask's <device_id> (or <string:device_id>) is OpenAPI's {device_id}.
        path = re.sub(r"<(?:[^:<>]+:)?([^<>]+)>", r"{\1}", rule.rule)
        for method in rule.methods - {"HEAD", "OPTIONS"}:
            routes[method, path] = rule.endpoint
    return routes


def test_the_contract_has_routes():
    assert len(_contract_routes()) > 1


def test_the_broker_serves_every_route_in_the_contract(app):
    missing = sorted(_contract_routes().keys() - _broker_routes(app).keys())
    assert missing == [], "in the contract, but the broker doesn't serve them"


def test_every_api_route_is_in_the_contract(app):
    unlisted = sorted(
        route
        for route in _broker_routes(app).keys() - _contract_routes().keys() - NOT_IN_CONTRACT.keys()
        if route[1] == "/api" or route[1].startswith("/api/")
    )
    assert unlisted == [], "served under /api, but not in the contract: add them to countdown-api's store.yaml"


def test_the_device_secret_guards_exactly_the_routes_the_contract_says(app):
    """A route the contract puts behind `deviceSecret` has require_device_auth, and only those."""
    broker = _broker_routes(app)
    contract = _contract_routes()
    for route, operation in contract.items():
        wants_secret = any("deviceSecret" in requirement for requirement in operation.get("security", []))
        view = app.view_functions[broker[route]]
        assert getattr(view, "device_auth", False) == wants_secret, route


def test_every_route_kept_out_of_the_contract_still_needs_to_be(app):
    """Once a route is in the contract (or the broker drops it), take it off NOT_IN_CONTRACT."""
    stale = sorted(
        route for route in NOT_IN_CONTRACT if route in _contract_routes() or route not in _broker_routes(app)
    )
    assert stale == []
