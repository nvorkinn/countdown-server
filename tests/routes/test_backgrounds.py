"""Test the weather panel's backgrounds by digest (GET /api/backgrounds/<digest>, getBackgroundImage)."""

import hashlib

import pytest
from countdown_api import fixtures
from redis import RedisError

BACKGROUND = fixtures.load("background.json")
IMAGE = fixtures.read_bytes(BACKGROUND["image"]["file"])
DIGEST = hashlib.sha256(IMAGE).hexdigest()


def test_a_stored_background_is_its_webp_cached_forever_with_no_secret(app, client):
    app.extensions["redis_bytes"].set(BACKGROUND["image"]["key"], IMAGE)

    response = client.get(BACKGROUND["image"]["url"])

    assert response.status_code == 200
    assert response.mimetype == "image/webp"
    assert response.data == IMAGE
    assert response.headers["Cache-Control"] == "public, max-age=31536000, immutable"


def test_one_made_before_webp_is_still_answered_as_a_jpeg(app, client):
    jpeg = b"\xff\xd8\xff\xe0 an older picture"
    digest = hashlib.sha256(jpeg).hexdigest()
    app.extensions["redis_bytes"].set(f"v1:background-image:{digest}", jpeg)

    response = client.get(f"/api/backgrounds/{digest}")

    assert response.mimetype == "image/jpeg"
    assert response.data == jpeg


def test_an_unknown_or_expired_digest_is_a_404(client):
    response = client.get(f"/api/backgrounds/{DIGEST}")

    assert response.status_code == 404


@pytest.mark.parametrize("digest", [DIGEST.upper(), DIGEST[:-1], f"{DIGEST}0", "..%2Fsecret"])
def test_anything_but_a_digest_is_a_404_without_asking_the_store(app, client, monkeypatch, digest):
    asked = []
    monkeypatch.setattr(app.extensions["redis_bytes"], "get", asked.append)

    assert client.get(f"/api/backgrounds/{digest}").status_code == 404
    assert asked == []


def test_a_store_that_is_down_is_a_503_to_try_again(app, client, monkeypatch):
    def down(*_args):
        raise RedisError

    monkeypatch.setattr(app.extensions["redis_bytes"], "get", down)

    response = client.get(f"/api/backgrounds/{DIGEST}")

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
