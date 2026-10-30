"""Browser-facing Spotify linking."""

import secrets
from typing import TYPE_CHECKING

from flask import Blueprint, redirect, request, session, url_for

from broker.auth import current_user, require_paired_user, user_devices
from broker.clients import spotify

if TYPE_CHECKING:
    from flask.typing import ResponseReturnValue

bp = Blueprint("spotify_oauth", __name__, url_prefix="/auth/spotify")


@bp.get("/login")
@require_paired_user
def login() -> ResponseReturnValue:
    """Send the signed-in user to Spotify to link it to their account, from the account page."""
    state = secrets.token_urlsafe(16)
    session["spotify_oauth_state"] = state
    return redirect(spotify.authorize_url(state))


@bp.get("/callback")
def callback() -> ResponseReturnValue:
    """Store the tokens Spotify hands back against the signed-in user's account, and go back to the account page."""
    error = request.args.get("error")
    if error:
        return f"Spotify authorization failed: {error}", 400

    if request.args.get("state") != session.pop("spotify_oauth_state", None):
        return "Invalid or expired login attempt, please try connecting again.", 400

    # Checked again: the user may have lost their screens, or signed out, since they left.
    user_id = current_user()
    if user_id is None or not user_devices():
        return redirect(url_for("pages.pair"))

    spotify.exchange_code(user_id, request.args.get("code"))
    return redirect(url_for("account.account"))
