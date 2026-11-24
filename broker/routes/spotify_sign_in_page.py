"""Where a phone signs one of a touchscreen's people in to Spotify (/spotify/sign-in and its callback).

The touchscreen shows /spotify/sign-in with its sign-in's id as a code to scan (services/spotify_sign_in.py). The
phone is sent on to Spotify, and Spotify sends it back to /spotify/sign-in/callback, which keeps Spotify's code for the
touchscreen to take. Both are public: the household has no accounts here, and the id is what says which sign-in it is.
"""

from typing import TYPE_CHECKING

from flask import Blueprint, redirect, render_template, request

from broker.services import spotify_sign_in

if TYPE_CHECKING:
    from flask.typing import ResponseReturnValue

bp = Blueprint("spotify_sign_in_page", __name__, url_prefix="/spotify/sign-in")


@bp.get("")
def start() -> ResponseReturnValue:
    """Send the phone on to Spotify for the sign-in `id` names, or say it has lapsed."""
    sign_in = spotify_sign_in.open_sign_in(request.args.get("id", "").strip())
    if sign_in is None:
        return render_template("spotify_sign_in.html", spent=True)
    return redirect(spotify_sign_in.authorize_url(sign_in))


@bp.get("/callback")
def callback() -> ResponseReturnValue:
    """Keep what Spotify answered for the sign-in its state names, and tell the person they can close the page."""
    state = request.args.get("state", "").strip()
    code = request.args.get("code", "").strip()
    error = request.args.get("error", "").strip()
    if not spotify_sign_in.finish(state, code, error):
        return render_template("spotify_sign_in.html", spent=True)
    return render_template("spotify_sign_in.html", declined=bool(error))
