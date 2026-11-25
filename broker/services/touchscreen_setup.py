"""A new touchscreen's setup on its owner's phone, as one visit from a single QR code.

The touchscreen shows one code: the pairing page with its token. Once the owner has signed in and the screen is theirs,
their phone goes on to /device/setup, which sends it to whatever the touchscreen has asked for next, in turn:

1. its Spotify sign-in (the touchscreen starts one once it is paired), then
2. the Soloist key page for the person who just signed in (the touchscreen asks for a code once it has their token),

and says the screen is ready once a key is saved. Between steps the touchscreen is still catching up, so the page
waits and asks again. The Spotify callback and the Soloist page send the phone back here while the screen has no key
yet: that is what says it is still being set up.
"""

from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import exists, select

from broker.db import db
from broker.models import Device, SoloistCode, SoloistKey, SpotifySignIn
from broker.services.soloist import is_paired_touchscreen

type Step = tuple[Literal["spotify"], str] | tuple[Literal["soloist"], str] | tuple[Literal["waiting", "done"], None]


def in_setup(device_id: str) -> bool:
    """Whether the touchscreen is still being set up: someone owns it, and nobody on it has a Soloist key yet."""
    device = db.session.get(Device, device_id)
    if device is None or not is_paired_touchscreen(device):
        return False
    return not db.session.scalar(select(exists().where(SoloistKey.device_id == device_id)))


def next_step(device_id: str) -> Step:
    """Return where the owner's phone goes next: a sign-in id, a Soloist code, or that it waits or is done."""
    if not in_setup(device_id):
        # Paired by now (the page is its owner's), so with a key: or an e-paper screen, with nothing more to do.
        return "done", None
    now = datetime.now(UTC)
    sign_in = db.session.scalar(
        select(SpotifySignIn.id)
        .where(
            SpotifySignIn.device_id == device_id,
            SpotifySignIn.expires_at >= now,
            SpotifySignIn.code.is_(None),
            SpotifySignIn.error.is_(None),
        )
        .order_by(SpotifySignIn.expires_at.desc())
        .limit(1)
    )
    if sign_in is not None:
        return "spotify", sign_in
    code = db.session.scalar(
        select(SoloistCode.code)
        .where(SoloistCode.device_id == device_id, SoloistCode.expires_at >= now)
        .order_by(SoloistCode.expires_at.desc())
        .limit(1)
    )
    if code is not None:
        return "soloist", code
    return "waiting", None
