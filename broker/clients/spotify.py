"""Talks to Spotify on a device's behalf: the OAuth token exchange, keeping its tokens fresh, and the Web API calls.

The tokens are its owner's account's (services/accounts.py): one link serves all their screens.
"""

import urllib.parse
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any

import requests
from countdown_api.models import SpotifyAccessToken
from flask import current_app

from broker.db import db
from broker.models import SpotifyToken
from broker.services import accounts

# What a touchscreen (the Max Mix) plays and browses with, through its Web Playback SDK and the Web API.
TOUCHSCREEN_SCOPES = frozenset(
    {
        "streaming",
        "user-read-email",
        "user-read-private",
        "user-read-playback-state",
        "user-modify-playback-state",
        "user-read-currently-playing",
        "user-library-read",
        "user-read-recently-played",
        "playlist-read-private",
    }
)
# A touchscreen gets a token with at least this long left, so it isn't back for another at once.
TOUCHSCREEN_TOKEN_MARGIN_S = 600
# One link serves every screen of the account, so it asks for what the music panel reads and a touchscreen plays with.
SCOPE = " ".join(sorted(TOUCHSCREEN_SCOPES | {"user-top-read"}))
AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"  # noqa: S105 -- a URL, not a password


def authorize_url(state: str) -> str:
    """Return the URL that sends the browser to Spotify's consent page, carrying `state` to the callback."""
    params = {
        "client_id": current_app.config["SPOTIFY_CLIENT_ID"],
        "response_type": "code",
        "redirect_uri": current_app.config["SPOTIFY_REDIRECT_URI"],
        "scope": SCOPE,
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(user_id: str, code: str | None) -> None:
    """Swap the code from Spotify's OAuth callback for tokens and store them against the user's account."""
    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": current_app.config["SPOTIFY_REDIRECT_URI"],
        },
        auth=_client_credentials(),
        timeout=10,
    )
    response.raise_for_status()
    _store_tokens(user_id, response.json())


def _client_credentials() -> tuple[str, str]:
    return current_app.config["SPOTIFY_CLIENT_ID"], current_app.config["SPOTIFY_CLIENT_SECRET"]


def _store_tokens(user_id: str, payload: dict) -> None:
    expires_at = datetime.now(UTC).timestamp() + payload["expires_in"]
    refresh_token = payload.get("refresh_token") or _existing_refresh_token(user_id)
    if refresh_token is None:
        # Spotify sends one with an account's first tokens, and the row keeps it from then on.
        msg = "Spotify sent no refresh token and the account has none stored"
        raise ValueError(msg)

    token = db.session.get(SpotifyToken, user_id) or SpotifyToken(user_id=user_id)
    token.refresh_token = refresh_token
    token.access_token = payload["access_token"]
    token.expires_at = expires_at
    # A refresh's answer may leave it out, which means the scopes are as they were.
    token.scope = payload.get("scope") or token.scope
    db.session.add(token)
    db.session.commit()


def _existing_refresh_token(user_id: str) -> str | None:
    token = db.session.get(SpotifyToken, user_id)
    return token.refresh_token if token else None


def _refresh_access_token(user_id: str, refresh_token: str) -> str:
    response = requests.post(
        TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        auth=_client_credentials(),
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    _store_tokens(user_id, payload)
    return payload["access_token"]


def _get_access_token(device_id: str, margin: float = 30) -> str | None:
    """Return a valid access token for the device's owner's Spotify, refreshing it if it has under `margin` s left.

    None when their account hasn't linked Spotify (or nobody owns the device).
    """
    user_id = accounts.owner(device_id)
    token = db.session.get(SpotifyToken, user_id) if user_id is not None else None
    if user_id is None or token is None:
        return None

    if token.expires_at is None or token.expires_at - margin < datetime.now(UTC).timestamp():
        return _refresh_access_token(user_id, token.refresh_token)
    return token.access_token


def lacks_touchscreen_scopes(token: SpotifyToken) -> bool:
    """Whether the link was made without what a touchscreen plays with (made before it was asked for, if unknown)."""
    return not set((token.scope or "").split()) >= TOUCHSCREEN_SCOPES


def touchscreen_token(device_id: str) -> SpotifyAccessToken | None:
    """Return an access token for a touchscreen to play its owner's Spotify with, renewed if it's about to expire.

    None when the owner's account hasn't linked Spotify. The refresh token never leaves the broker. Raises
    requests.RequestException if Spotify can't renew it.
    """
    access_token = _get_access_token(device_id, margin=TOUCHSCREEN_TOKEN_MARGIN_S)
    user_id = accounts.owner(device_id)
    token = db.session.get(SpotifyToken, user_id) if user_id is not None else None
    if access_token is None or token is None:
        return None
    expires_in = max(0, int((token.expires_at or 0) - datetime.now(UTC).timestamp()))
    return SpotifyAccessToken(
        access_token=access_token,
        token_type="Bearer",  # noqa: S106 -- the scheme's name, not a password
        expires_in=expires_in,
        scope=token.scope or "",
    )


def get_queue(device_id: str) -> dict[str, Any]:
    """Return Spotify's /me/player/queue for the device: what's playing and what's up next.

    The object has `currently_playing` and `queue`. {} when Spotify isn't linked or nothing is playing (204).
    """
    access_token = _get_access_token(device_id)
    if access_token is None:
        return {}

    response = requests.get(
        "https://api.spotify.com/v1/me/player/queue",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if response.status_code == HTTPStatus.NO_CONTENT or not response.content:
        return {}
    response.raise_for_status()
    return response.json()


def get_currently_playing(device_id: str) -> dict[str, Any]:
    """Return Spotify's /me/player/currently-playing for the device: the track, `progress_ms` and `is_playing`.

    {} when Spotify isn't linked or nothing is playing (204). Much cheaper than the queue, so the music provider asks
    it every few seconds while something plays.
    """
    access_token = _get_access_token(device_id)
    if access_token is None:
        return {}

    response = requests.get(
        "https://api.spotify.com/v1/me/player/currently-playing",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if response.status_code == HTTPStatus.NO_CONTENT or not response.content:
        return {}
    response.raise_for_status()
    return response.json()


def get_users_top_items(device_id: str, type_: str, params: dict[str, str]) -> dict[str, Any] | None:
    """Spotify's top artists or tracks (`type_`) for the device's user, as its paging object (`items`, `total`, ...).

    None when Spotify isn't linked, {} for a 204.
    """
    access_token = _get_access_token(device_id)
    if access_token is None:
        return None
    response = requests.get(
        f"https://api.spotify.com/v1/me/top/{type_}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
        params=params,
    )
    if response.status_code == HTTPStatus.NO_CONTENT or not response.content:
        return {}
    response.raise_for_status()
    return response.json()
