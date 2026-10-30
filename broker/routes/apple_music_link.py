"""Browser-facing Apple Music linking, from the account page (routes/account.py)."""

from typing import TYPE_CHECKING

from flask import Blueprint, current_app, jsonify, redirect, request, url_for

from broker.auth import require_paired_user, signed_in_user
from broker.clients import apple_music
from broker.db import db
from broker.models import AppleMusicPlay, AppleMusicToken
from broker.services import accounts, device_keys

if TYPE_CHECKING:
    from flask.typing import ResponseReturnValue

bp = Blueprint("apple_music_link", __name__)


@bp.get("/account/apple-music/developer-token")
@require_paired_user
def developer_token() -> ResponseReturnValue:
    """Return the developer token and app name that MusicKit JS is configured with."""
    if not apple_music.configured():
        return jsonify(error="Apple Music isn't set up on this server"), 404
    return jsonify(developer_token=apple_music.developer_token(), app_name=current_app.config["APPLE_MUSIC_APP_NAME"])


@bp.post("/account/apple-music/connect")
@require_paired_user
def connect() -> ResponseReturnValue:
    """Store the Music User Token MusicKit JS got when the listener signed in to Apple, against their account."""
    token = request.form.get("music_user_token", "").strip()
    if not token:
        return jsonify(error="No Apple Music token was sent"), 400
    user_id = signed_in_user()
    stored = db.session.get(AppleMusicToken, user_id) or AppleMusicToken(user_id=user_id, seen_ids=[])
    stored.token = token
    db.session.add(stored)
    db.session.commit()
    return redirect(url_for("account.account"))


@bp.post("/account/apple-music/disconnect")
@require_paired_user
def disconnect() -> ResponseReturnValue:
    """Forget the account's Apple Music token and the plays it logged, for all the user's screens."""
    user_id = signed_in_user()
    stored = db.session.get(AppleMusicToken, user_id)
    if stored is not None:
        db.session.delete(stored)
    db.session.query(AppleMusicPlay).filter_by(user_id=user_id).delete()
    db.session.commit()
    # What it last played, and the top lists counted from its plays: the music provider writes them again within a
    # minute for whatever is still linked.
    accounts.forget_keys(user_id, device_keys.music)
    return redirect(url_for("account.account"))
