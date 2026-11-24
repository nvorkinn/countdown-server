"""The page where one of a touchscreen's people pastes their Spotify Soloist API key (/soloist).

The touchscreen shows a link with a code (POST /api/devices/soloist/codes), which the person opens on their phone. The
page is public: the household has no accounts here, and the code says which touchscreen and person the key is for
(services/soloist.py). It is 32 random hex digits that last ten minutes and work once, so it can't be guessed, and all
it lets anyone do is save a key for that one person.

The key is never shown again, nor logged: it goes from the form to the database, encrypted, and from there only to
the touchscreen it was saved for.
"""

from typing import TYPE_CHECKING

from flask import Blueprint, render_template, request

from broker.services import soloist

if TYPE_CHECKING:
    from flask.typing import ResponseReturnValue

bp = Blueprint("soloist_page", __name__)

SPENT = "This link has expired or has already been used. Ask for a new one on the touchscreen."


@bp.route("/soloist", methods=["GET", "POST"])
def save_key() -> ResponseReturnValue:
    """Show the field for the key of the person the code names, and save the one posted.

    200 with the page either way: the field, that the key is saved, or that the code is spent (a GET checks it without
    using it up). An empty or overlong key is asked for again, the code unspent.
    """
    if request.method == "GET":
        code = request.args.get("code", "").strip()
        if not soloist.code_is_open(code):
            return render_template("soloist.html", error=SPENT)
        return render_template("soloist.html", code=code)

    code = request.form.get("code", "").strip()
    key = request.form.get("api_key", "").strip()
    if not soloist.code_is_open(code):
        return render_template("soloist.html", error=SPENT)
    if not key or len(key) > soloist.MAX_KEY:
        return render_template("soloist.html", code=code, invalid=True)
    if not soloist.save(code, key):
        return render_template("soloist.html", error=SPENT)
    return render_template("soloist.html", saved=True)
