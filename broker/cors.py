"""CORS for the screens' API calls a touchscreen makes from its browser: its config, the pickers' searches, its token.

A touchscreen's app is a web page on its own origin (one of TOUCHSCREEN_RETURN_ORIGINS), so its calls to API_HOST are
cross-origin, and with the Bearer secret, JSON bodies and If-Match each one needs a preflight first. Only those origins
are answered, each by name; any other origin gets no CORS headers, and the browser keeps the answer from it. No
credentials are allowed: the device's secret in the Authorization header is the login, not a cookie.

The headers go on every answer from these routes, a 401, 409 or 412 included, so the app can read why a call failed.
Flask answers the preflight (OPTIONS) itself, without calling the route, so it needs no secret.
"""

from flask import Flask, Response, current_app, request

# The routes a touchscreen calls (the countdown-api contract's): its registration, config and Spotify token under
# /api/devices/ (its location among them), the stop and place pickers' searches and current location, and a setup's
# postcode lookup, nearby postcodes and nearby stops. The settings site's pages and the e-paper screens' frames aren't
# here.
PATH_PREFIXES = ("/api/devices/",)
PATHS = frozenset(
    {
        "/api/stops/search",
        "/api/stops/near",
        "/api/places/search",
        "/api/places/here",
        "/api/postcodes/lookup",
        "/api/postcodes/near",
    }
)
# The one device route no web page is answered, the app's included: it sends a person's Soloist API key, which
# Spotify says never to put in a client-side app. The touchscreen's own service asks for it and hands it to Soloist.
# With no CORS headers a browser fails the preflight, so the page's script never sees the answer.
NOT_FOR_PAGES = frozenset({"/api/devices/soloist/key"})

ALLOW_METHODS = "GET, POST, PUT, DELETE, OPTIONS"
ALLOW_HEADERS = "Authorization, Content-Type, If-Match, If-None-Match"
EXPOSE_HEADERS = "ETag"
# How long a browser may keep a preflight's answer: an hour, so a touchscreen doesn't ask before every save.
MAX_AGE_SECONDS = 3600


def init_app(app: Flask) -> None:
    """Add CORS headers to the touchscreen's routes' answers, for the origins TOUCHSCREEN_RETURN_ORIGINS lists."""
    app.after_request(_add_cors_headers)


def _add_cors_headers(response: Response) -> Response:
    if request.path in NOT_FOR_PAGES or not (request.path in PATHS or request.path.startswith(PATH_PREFIXES)):
        return response
    # Whether the headers are there depends on the Origin, so a cache mustn't give one origin's answer to another.
    response.vary.add("Origin")
    origin = request.headers.get("Origin", "")
    if origin.rstrip("/").lower() not in current_app.config["TOUCHSCREEN_RETURN_ORIGINS"]:
        return response
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Expose-Headers"] = EXPOSE_HEADERS
    if request.method == "OPTIONS" and "Access-Control-Request-Method" in request.headers:
        response.headers["Access-Control-Allow-Methods"] = ALLOW_METHODS
        response.headers["Access-Control-Allow-Headers"] = ALLOW_HEADERS
        response.headers["Access-Control-Max-Age"] = str(MAX_AGE_SECONDS)
    return response
