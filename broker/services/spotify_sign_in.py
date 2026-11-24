"""A touchscreen's people sign in to Spotify on their phones, not on its screen.

1. The touchscreen makes a PKCE verifier (RFC 7636) and keeps it. It asks for a sign-in with the verifier's challenge,
   its Spotify app's client id and its scopes (start), and shows the settings site's /spotify/sign-in for it as a
   code to scan.
2. The phone opens that, and is sent on to Spotify (authorize_url), with the sign-in's id as the state and the
   settings site's callback as the redirect address.
3. Spotify sends the phone back to the callback, which keeps Spotify's code or error (finish).
4. The touchscreen asks for it (take), once, and exchanges the code at Spotify with its own verifier.

The server never sees the verifier or a token: what it keeps for a sign-in is no use without the touchscreen. An id is
32 random hex digits that last ten minutes, so it can't be guessed, and all it lets anyone do is sign in to Spotify
for that touchscreen, as the person holding the phone.
"""

import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlencode

from sqlalchemy import delete

from broker.db import db
from broker.models import Device, SpotifySignIn

# As long as a Soloist key code: time to pick up a phone and sign in.
TTL = timedelta(minutes=10)
# 16 random bytes as hex, the contract's SpotifySignInId.
ID_BYTES = 16
ID_RE = re.compile(r"^[0-9a-f]{32}$")
AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
# The longest code or error kept from Spotify: far more than either is, and short enough to turn away junk.
MAX_ANSWER = 2048

type Status = Literal["waiting", "done", "failed"]


def is_id(value: object) -> bool:
    """Whether `value` is shaped like a sign-in's id (the contract's SpotifySignInId)."""
    return isinstance(value, str) and ID_RE.match(value) is not None


def start(device: Device, client_id: str, code_challenge: str, scope: str, redirect_uri: str) -> SpotifySignIn:
    """Make and commit a sign-in for the touchscreen. Also clears every device's lapsed ones."""
    now = datetime.now(UTC)
    db.session.execute(delete(SpotifySignIn).where(SpotifySignIn.expires_at < now))
    sign_in = SpotifySignIn(
        id=secrets.token_hex(ID_BYTES),
        device_id=device.device_id,
        client_id=client_id,
        code_challenge=code_challenge,
        scope=scope,
        redirect_uri=redirect_uri,
        expires_at=now + TTL,
    )
    db.session.add(sign_in)
    db.session.commit()
    return sign_in


def open_sign_in(sign_in_id: str) -> SpotifySignIn | None:
    """Return the sign-in with that id if the phone can still use it: it hasn't lapsed or been answered."""
    if not is_id(sign_in_id):
        return None
    found = db.session.get(SpotifySignIn, sign_in_id)
    if found is None or found.expires_at < datetime.now(UTC) or found.code or found.error:
        return None
    return found


def authorize_url(sign_in: SpotifySignIn) -> str:
    """Spotify's sign-in page for the sign-in, as the touchscreen asked for it."""
    query = {
        "response_type": "code",
        "client_id": sign_in.client_id,
        "redirect_uri": sign_in.redirect_uri,
        "state": sign_in.id,
        "code_challenge_method": "S256",
        "code_challenge": sign_in.code_challenge,
        "scope": sign_in.scope,
    }
    return f"{AUTHORIZE_URL}?{urlencode(query)}"


def finish(state: str, code: str, error: str) -> str | None:
    """Keep Spotify's code, or its error, for the sign-in `state` names, and return its touchscreen's device id.

    None if there's no open sign-in for it.
    """
    sign_in = open_sign_in(state)
    code, error = code[:MAX_ANSWER], error[:MAX_ANSWER]
    if sign_in is None or not (code or error):
        return None
    if error:
        sign_in.error = error
    else:
        sign_in.code = code
    device_id = sign_in.device_id
    db.session.commit()
    return device_id


def take(device_id: str, sign_in_id: str) -> tuple[Status, str | None] | None:
    """Return where the touchscreen's sign-in stands, with Spotify's code or error; None if there's no such one.

    An answered sign-in is deleted as it's taken, so its code is answered once.
    """
    if not is_id(sign_in_id):
        return None
    sign_in = db.session.get(SpotifySignIn, sign_in_id)
    if sign_in is None or sign_in.device_id != device_id or sign_in.expires_at < datetime.now(UTC):
        return None
    if not (sign_in.code or sign_in.error):
        return "waiting", None
    answer: tuple[Status, str | None] = ("done", sign_in.code) if sign_in.code else ("failed", sign_in.error)
    db.session.delete(sign_in)
    db.session.commit()
    return answer
