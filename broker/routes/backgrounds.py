"""The weather panel's backgrounds, by their SHA-256 (GET /api/backgrounds/<digest>, the contract's getBackgroundImage).

A touchscreen's getWeather names each of its place's backgrounds by digest (services/panels.py); the screen loads the
picture here as a plain image (an <img> or a CSS background), which can't send the device's secret, so this takes none.
The address is the picture's own SHA-256, so it says nothing about the device or its place, and the same address is
always the same bytes: it's answered as immutable, for a year. The background provider writes the bytes
(`v1:background-image:<digest>`, two days' TTL); one that has expired is a 404.
"""

import re
from typing import TYPE_CHECKING

from countdown_api import keys as store_keys
from flask import Blueprint, Response, current_app, jsonify
from redis import RedisError

from broker.auth import with_retry_after
from broker.clients.image_models import media_type

if TYPE_CHECKING:
    from flask.typing import ResponseReturnValue

bp = Blueprint("backgrounds", __name__)

# The contract's BackgroundDigest: a SHA-256 in lower-case hex.
DIGEST = re.compile(r"[0-9a-f]{64}")
# A year: the same digest is always the same picture.
IMMUTABLE = "public, max-age=31536000, immutable"


@bp.get("/api/backgrounds/<digest>")
def background_image(digest: str) -> ResponseReturnValue:
    """Answer the background (WebP, or JPEG if older); 404 for no such picture or not a digest, 503 if Redis is down."""
    if not DIGEST.fullmatch(digest):
        return jsonify(error="no such background"), 404
    try:
        image = current_app.extensions["redis_bytes"].get(store_keys.background_image(digest))
    except RedisError as error:
        current_app.logger.warning("Couldn't read a background from Redis: %s", type(error).__name__)
        return with_retry_after(jsonify(error="the store couldn't be reached"), 5), 503
    if image is None:
        return jsonify(error="no such background"), 404
    response = Response(image, mimetype=media_type(image))
    response.headers["Cache-Control"] = IMMUTABLE
    return response
