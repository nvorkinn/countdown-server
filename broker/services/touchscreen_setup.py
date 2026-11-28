"""A new touchscreen's setup on its owner's phone, as one visit from a single QR code.

The touchscreen shows one code: the pairing page with its token. Once the owner has signed in and the screen is theirs,
their phone goes on to /device/<id>/setup, which sends it through the rest. There are two ways that runs.

A touchscreen that reports on its own setup (PUT /api/devices/setup, `report`) takes part in a setup both screens show
in step. It has a row (TouchscreenSetup) from its first report, which it sends as soon as it has registered, with the
PKCE challenge for its first person's Spotify sign-in. The step (`status`, the contract's SetupStep) is the first of
these not done:

1. `account`: someone owns it.
2. `spotify`: its first person has signed in to Spotify on the phone. The broker makes the sign-in from the reported
   challenge when the phone gets there (`sign_in_for_phone`), so the phone doesn't wait for the touchscreen to ask. The
   touchscreen takes Spotify's code, exchanges it with its verifier, and asks for that person's Soloist key code,
   which is what says this step is done.
3. `soloist_key`: they have pasted their Soloist API key.
4. `soloist_sign_in`: its Soloist has signed in, which the touchscreen reports.
5. `location`: the owner has said on the phone where it is, or skipped that (`location_done`).
6. `presence`: the phone is paired over Bluetooth, which the touchscreen reports.
7. `done`: all of those, or the touchscreen has reported that it finished.

A touchscreen that has never reported is set up as before (`next_step`): the phone goes to whatever the touchscreen
has asked for next, its Spotify sign-in and then its Soloist key page, waits between them, and is done once a key is
saved.
"""

from datetime import UTC, datetime, timedelta
from typing import Literal

from countdown_api.models import SetupReport, SetupStatus, SetupStep
from countdown_api.models import SpotifySignIn as SignInAnswer
from sqlalchemy import exists, select

from broker.db import db
from broker.models import Device, SoloistCode, SoloistKey, SpotifySignIn, TouchscreenSetup
from broker.services import spotify_sign_in
from broker.services.soloist import is_paired_touchscreen

type Step = tuple[Literal["spotify"], str] | tuple[Literal["soloist"], str] | tuple[Literal["waiting", "done"], None]

# How long the phone waits, back from Spotify, for the touchscreen to take the code and ask for its person's Soloist
# key code: a few seconds as a rule. Past it something went wrong on the touchscreen, and the phone signs in again.
ANSWER_WAIT = timedelta(seconds=60)


def follows(device_id: str) -> bool:
    """Whether the touchscreen reports on its own setup, so that it and the phone show the same step."""
    return db.session.get(TouchscreenSetup, device_id) is not None


def in_setup(device_id: str) -> bool:
    """Whether the touchscreen is still being set up.

    One that reports on its setup is until it says it has finished. Any other is while someone owns it and nobody on
    it has a Soloist key yet.
    """
    device = db.session.get(Device, device_id)
    if device is None or not is_paired_touchscreen(device):
        return False
    if device.setup is not None:
        return device.setup.finished_at is None
    return not _has_key(device_id)


def next_step(device_id: str) -> Step:
    """Return where the owner's phone goes next for a touchscreen that doesn't report on its setup.

    A sign-in id, a Soloist code, or that it waits or is done.
    """
    if not in_setup(device_id):
        # Paired by now (the page is its owner's), so with a key: or an e-paper screen, with nothing more to do.
        return "done", None
    sign_in = _open_sign_in(device_id, unanswered=True)
    if sign_in is not None:
        return "spotify", sign_in.id
    code = _open_code(device_id)
    if code is not None:
        return "soloist", code
    return "waiting", None


def report(device: Device, reported: SetupReport) -> None:
    """Keep what a touchscreen reports of its own setup, and commit: each field sent, the rest as they were.

    Its first report makes it one that takes part. `finished` is kept as when it first said so.
    """
    setup = device.setup
    if setup is None:
        setup = device.setup = TouchscreenSetup()
    sent = reported.model_fields_set
    if "spotify" in sent and reported.spotify is not None:
        setup.spotify_client_id = reported.spotify.client_id
        setup.spotify_code_challenge = reported.spotify.code_challenge
        setup.spotify_scope = reported.spotify.scope
    if "soloist_device" in sent and reported.soloist_device is not None:
        setup.soloist_device = reported.soloist_device
    if "soloist_signed_in" in sent and reported.soloist_signed_in is not None:
        setup.soloist_signed_in = reported.soloist_signed_in
    if "phone_paired" in sent and reported.phone_paired is not None:
        setup.phone_paired = reported.phone_paired
    if "finished" in sent and reported.finished is not None:
        if not reported.finished:
            setup.finished_at = None
        elif setup.finished_at is None:
            setup.finished_at = datetime.now(UTC)
    db.session.commit()


def status(device: Device, redirect_uri: str) -> SetupStatus | None:
    """Return where the touchscreen's setup stands (the contract's SetupStatus); None for one that doesn't report.

    `redirect_uri` is the settings site's Spotify callback, as the touchscreen is told it with a sign-in.
    """
    setup = device.setup
    if setup is None:
        return None
    step = step_of(device)
    sign_in = _open_sign_in(device.device_id, unanswered=False) if step == "spotify" else None
    return SetupStatus(
        step=step,
        spotify_sign_in=(
            SignInAnswer(
                id=sign_in.id,
                redirect_uri=redirect_uri,
                expires_in=max(0, int((sign_in.expires_at - datetime.now(UTC)).total_seconds())),
            )
            if sign_in is not None
            else None
        ),
        soloist_device=setup.soloist_device,
    )


def step_of(device: Device) -> SetupStep:  # noqa: PLR0911 -- a return per step
    """Return the step a touchscreen that reports on its setup is at: the first not done (see the module's list)."""
    setup = device.setup
    if setup is None:  # pragma: no cover -- callers ask only for one that reports
        msg = "the touchscreen doesn't report on its setup"
        raise ValueError(msg)
    if setup.finished_at is not None:
        return "done"
    if not is_paired_touchscreen(device):
        return "account"
    has_key = _has_key(device.device_id)
    if not has_key and _open_code(device.device_id) is None:
        return "spotify"
    if not has_key:
        return "soloist_key"
    if not setup.soloist_signed_in:
        return "soloist_sign_in"
    if setup.location_done_at is None:
        return "location"
    if not setup.phone_paired:
        return "presence"
    return "done"


def sign_in_for_phone(device: Device, redirect_uri: str) -> str | None:
    """Return the id of the Spotify sign-in to send the owner's phone to, at the `spotify` step; None to wait.

    The one still open if there is one, else a new one made from the challenge the touchscreen reported. The phone
    waits instead while the touchscreen has nothing reported to sign in with, and for ANSWER_WAIT after it came back
    from Spotify, while the touchscreen takes the code and catches up.
    """
    setup = device.setup
    if setup is None or not (setup.spotify_client_id and setup.spotify_code_challenge and setup.spotify_scope):
        return None
    answered = setup.spotify_answered_at
    if answered is not None and datetime.now(UTC) - answered < ANSWER_WAIT:
        return None
    sign_in = _open_sign_in(device.device_id, unanswered=True)
    if sign_in is None:
        sign_in = spotify_sign_in.start(
            device, setup.spotify_client_id, setup.spotify_code_challenge, setup.spotify_scope, redirect_uri
        )
    return sign_in.id


def spotify_answered(device_id: str, *, signed_in: bool) -> None:
    """Note that the phone is back from Spotify for a touchscreen that reports on its setup, and commit.

    With Spotify's code the phone then waits for the touchscreen; refused, it may sign in again at once.
    """
    setup = db.session.get(TouchscreenSetup, device_id)
    if setup is None:
        return
    setup.spotify_answered_at = datetime.now(UTC) if signed_in else None
    db.session.commit()


def location_done(device_id: str) -> None:
    """Note that the owner has said where the touchscreen is, or skipped that, and commit."""
    setup = db.session.get(TouchscreenSetup, device_id)
    if setup is not None and setup.location_done_at is None:
        setup.location_done_at = datetime.now(UTC)
        db.session.commit()


def open_code(device_id: str) -> str | None:
    """Return the Soloist key code the touchscreen last asked for that can still be used, for the phone to open."""
    return _open_code(device_id)


def forget(device: Device) -> None:
    """Start the setup of a touchscreen that changes hands over (not committed): what it reported was its last owner's.

    The challenge stays, as it is the touchscreen's own, so its new owner's phone can still go straight to Spotify.
    """
    setup = device.setup
    if setup is None:
        return
    setup.spotify_answered_at = None
    setup.soloist_signed_in = False
    setup.location_done_at = None
    setup.phone_paired = False
    setup.finished_at = None


def _has_key(device_id: str) -> bool:
    return bool(db.session.scalar(select(exists().where(SoloistKey.device_id == device_id))))


def _open_code(device_id: str) -> str | None:
    return db.session.scalar(
        select(SoloistCode.code)
        .where(SoloistCode.device_id == device_id, SoloistCode.expires_at >= datetime.now(UTC))
        .order_by(SoloistCode.expires_at.desc())
        .limit(1)
    )


def _open_sign_in(device_id: str, *, unanswered: bool) -> SpotifySignIn | None:
    """Return the touchscreen's newest Spotify sign-in that hasn't lapsed; with `unanswered`, only one still waiting."""
    query = select(SpotifySignIn).where(
        SpotifySignIn.device_id == device_id, SpotifySignIn.expires_at >= datetime.now(UTC)
    )
    if unanswered:
        query = query.where(SpotifySignIn.code.is_(None), SpotifySignIn.error.is_(None))
    return db.session.scalar(query.order_by(SpotifySignIn.expires_at.desc()).limit(1))
