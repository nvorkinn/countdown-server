"""The signed-in user's account page: the services linked to their account, which all their screens can show.

Each screen's own page only switches each service on or off for that screen (services/accounts.py).
"""

from typing import TYPE_CHECKING

from cryptography.fernet import InvalidToken
from flask import Blueprint, current_app, redirect, render_template, request, url_for

from broker.auth import require_paired_user, signed_in_user
from broker.clients import apple_music
from broker.db import db
from broker.models import GitHubToken, GlowmarktCredentials, ParcelKey, SpotifyToken
from broker.routes.pages import _write_subscriptions_to_redis
from broker.services import accounts, config_store, device_keys, parcel_key, render_jobs

if TYPE_CHECKING:
    from collections.abc import Callable

    from flask.typing import ResponseReturnValue

    from broker.services.parcel_key import ParcelsLinkStatus

bp = Blueprint("account", __name__, url_prefix="/account")


@bp.route("", methods=["GET", "POST"])
@require_paired_user
def account() -> ResponseReturnValue:
    """Show the account's logins, and save the ones posted."""
    if request.method == "POST":
        return _save_logins()
    return _render_account()


def _save_logins() -> ResponseReturnValue:
    """Save the logins posted, and have every screen of the user's drawn again if any changed."""
    user_id = signed_in_user()
    changed = _stage_logins(user_id)
    db.session.commit()
    if changed:
        # What the store has from an account that's gone or been replaced, before the providers are told to write the
        # new one's, so no screen shows the old account's data.
        for keys_of in changed:
            accounts.forget_keys(user_id, keys_of)
        _write_subscriptions_to_redis()
        config_store.publish_all()
        render_jobs.enqueue(current_app.extensions["redis"], accounts.screens(user_id))
    return redirect(url_for("account.account"))


def _stage_logins(user_id: str) -> list[Callable[[str], list[str]]]:
    """Stage the logins posted, for the user's account.

    Return, for each one that changed, what lists the keys the providers made from it (device_keys'): a key posted
    counts as changed even if it's the same, so the parcel provider tries afresh one Parcel refused.
    """
    changed: list[Callable[[str], list[str]]] = []
    if _save_glowmarkt_credentials(user_id):
        changed.append(device_keys.energy)
    if _save_github_token(user_id):
        changed.append(device_keys.developer)
    if _save_parcel_key(user_id):
        changed.append(device_keys.parcels)
    return changed


def _save_glowmarkt_credentials(user_id: str) -> bool:
    """Stage the posted Glowmarkt username, and password unless its field was left blank, for the user's account.

    Return whether that changes the account the glow-provider signs in with, or leaves it none.
    """
    glowmarkt_password = request.form.get("glowmarkt_password", "")
    stored = db.session.get(GlowmarktCredentials, user_id)
    before = _glowmarkt_account(stored) if stored is not None else None
    creds = stored or GlowmarktCredentials(user_id=user_id)
    creds.username = request.form.get("glowmarkt_username", "").strip()
    if glowmarkt_password:
        creds.password = glowmarkt_password
    db.session.add(creds)
    return _glowmarkt_account(creds) != before


def _glowmarkt_account(creds: GlowmarktCredentials) -> tuple[str, str] | str | None:
    """Return the account the provider signs in with (GlowmarktCredentials.sign_in); None for none.

    "undecryptable" for a password that won't decrypt, which the provider can't sign in with either.
    """
    try:
        return creds.sign_in()
    except InvalidToken:
        return "undecryptable"


def _save_github_token(user_id: str) -> bool:
    """Stage the posted GitHub token for the user's account, unless its field was left blank (keeping the stored one).

    Return whether that replaces the token the developer provider uses.
    """
    token = request.form.get("github_token", "").strip()
    if not token:
        return False
    stored = db.session.get(GitHubToken, user_id)
    try:
        unchanged = stored is not None and stored.token == token
    except InvalidToken:
        unchanged = False
    if unchanged:
        return False
    stored = stored or GitHubToken(user_id=user_id)
    stored.token = token
    db.session.add(stored)
    return True


def _save_parcel_key(user_id: str) -> bool:
    """Stage the posted Parcel API key for the user's account, unless its field was left blank (keeping the stored one).

    Return whether a key was posted, so what the provider made from the last one is cleared. The key is never logged.
    """
    key = request.form.get("parcel_api_key", "").strip()
    if not key:
        return False
    stored = db.session.get(ParcelKey, user_id) or ParcelKey(user_id=user_id)
    stored.key = key
    db.session.add(stored)
    return True


def _parcel_status(user_id: str) -> ParcelsLinkStatus | None:
    """Return what Parcel last said of the key, from the first of the user's screens the provider has asked for."""
    store = current_app.extensions["redis"]
    return next(
        (status for device_id in accounts.screens(user_id) if (status := parcel_key.link_status(store, device_id))),
        None,
    )


def _render_account() -> str:
    user_id = signed_in_user()
    creds = db.session.get(GlowmarktCredentials, user_id)
    return render_template(
        "account.html",
        spotify_linked=db.session.get(SpotifyToken, user_id) is not None,
        apple_music_available=apple_music.configured(),
        apple_music_linked=apple_music.account_linked(user_id),
        github_linked=db.session.get(GitHubToken, user_id) is not None,
        parcel_linked=parcel_key.has_key(db, user_id),
        parcel_status=_parcel_status(user_id),
        glowmarkt={
            "username": creds.username if creds else "",
            "has_password": bool(creds and creds.password_encrypted),
        },
    )


@bp.post("/spotify/disconnect")
@require_paired_user
def spotify_disconnect() -> ResponseReturnValue:
    """Forget the account's Spotify tokens, unlinking Spotify from all the user's screens."""
    user_id = signed_in_user()
    token = db.session.get(SpotifyToken, user_id)
    if token is not None:
        db.session.delete(token)
        db.session.commit()
        # What it was playing, and its top lists: the music provider writes them again within a minute for whatever is
        # still linked.
        accounts.forget_keys(user_id, device_keys.music)
    return redirect(url_for("account.account"))


@bp.post("/github/disconnect")
@require_paired_user
def github_disconnect() -> ResponseReturnValue:
    """Forget the account's GitHub token, for all the user's screens."""
    user_id = signed_in_user()
    token = db.session.get(GitHubToken, user_id)
    if token is not None:
        db.session.delete(token)
        db.session.commit()
        # The developer provider rewrites the panel without the GitHub summary within a cycle.
        accounts.forget_keys(user_id, device_keys.developer)
    return redirect(url_for("account.account"))


@bp.post("/parcel/disconnect")
@require_paired_user
def parcel_disconnect() -> ResponseReturnValue:
    """Forget the account's Parcel API key, which takes the parcels panel off all the user's screens."""
    user_id = signed_in_user()
    key = db.session.get(ParcelKey, user_id)
    if key is not None:
        db.session.delete(key)
        db.session.commit()
        # The provider stops polling it once the key is gone; what it wrote would otherwise show until its TTL.
        accounts.forget_keys(user_id, device_keys.parcels)
        _write_subscriptions_to_redis()
        config_store.publish_all()
    return redirect(url_for("account.account"))
