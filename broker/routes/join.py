"""A person joining a touchscreen as a member: the page its member code opens on their phone (/join).

The touchscreen shows a link with a code (POST /api/devices/members/codes). The settings site is behind the login, so
by the time the request gets here the person has signed in to their own account. The page names the touchscreen and
says what joining lets it do; nothing changes until they confirm, which is a post from this page (services/members.py
does the joining).

The visit alone never joins: a link is easy to be sent, and joining lets that touchscreen ask for what the person
saved for themselves (their Spotify Soloist API key). So the page says so, and the confirming post is taken only from
this site, as /register's is.
"""

from typing import TYPE_CHECKING

from flask import Blueprint, render_template, request

from broker.auth import require_user_id, screen_label
from broker.db import db
from broker.models import SoloistKey
from broker.routes.register import _posted_here
from broker.services import members

if TYPE_CHECKING:
    from flask.typing import ResponseReturnValue

bp = Blueprint("join", __name__)

SPENT = "This link has expired or has already been used. Ask for a new one on the touchscreen."


@bp.route("/join", methods=["GET", "POST"])
@require_user_id
def join(user_id: str) -> ResponseReturnValue:
    """Ask the signed-in person to confirm joining the touchscreen the code names, and on their post, join them.

    200 with the page either way: the question, that they've joined, or that the code is spent. 403 for a confirming
    post that didn't come from this site.
    """
    if request.method == "GET":
        code = request.args.get("code", "").strip()
        device = members.open_code(code)
        if device is None:
            return render_template("join.html", error=SPENT)
        return render_template("join.html", confirm=True, code=code, screen=screen_label(device))

    if not _posted_here():
        return render_template("join.html", error="Confirm on this site to join the touchscreen."), 403
    device = members.join(request.form.get("code", "").strip(), user_id)
    if device is None:
        return render_template("join.html", error=SPENT)
    return render_template(
        "join.html",
        screen=screen_label(device),
        owner=device.owner == user_id,
        soloist_linked=db.session.get(SoloistKey, user_id) is not None,
    )
