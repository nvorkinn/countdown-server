"""A touchscreen's people's Spotify Soloist API keys: one for each person on it, saved with a code.

Spotify Soloist runs on a touchscreen's Pi and takes an API key at every start. Spotify has each person make their own
key and not share it, and a touchscreen has several people on it (a household), so the broker keeps a key for a
touchscreen and a person. The household has no accounts here: the touchscreen names its people itself (`person`, their
Spotify id), and the broker reads nothing into the name.

1. The touchscreen asks for a code for one of its people (issue_code) and shows it as a link to /soloist.
2. They open it on their phone and paste their key, with no login (save): the code says which touchscreen and person
   it is for, and is too long to guess.
3. The touchscreen sees they have one (people) and asks for it when they play (key_of).

A touchscreen is only ever sent keys that were saved for it, so a device anyone registers gets no one else's. The keys
go when it changes hands or is unpaired (forget): they were saved for its last owner's household.
"""

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select

from broker.db import db
from broker.models import Device, SoloistCode, SoloistKey

# As long as a pairing code: time to pick up a phone, find the key and paste it.
CODE_TTL = timedelta(minutes=10)
# 16 random bytes as hex, the contract's SoloistKeyCodeValue: it's a link's query, never typed, so it can't be guessed.
CODE_BYTES = 16
# The contract's SoloistPerson: a name of the touchscreen's own, with no whitespace or slash (it's a path segment too).
MAX_PERSON = 128
# Far longer than any key Spotify issues, and short enough to keep a mistaken paste of a whole page out.
MAX_KEY = 512


def is_paired_touchscreen(device: Device) -> bool:
    """Whether the device is a touchscreen someone owns: only those have people with keys."""
    return device.paired_at is not None and device.type == "touchscreen" and device.owner is not None


def is_person(value: object) -> bool:
    """Whether `value` is a name a touchscreen can give one of its people (the contract's SoloistPerson)."""
    return (
        isinstance(value, str)
        and 0 < len(value) <= MAX_PERSON
        and "/" not in value
        and not any(character.isspace() for character in value)
    )


def issue_code(device_id: str, person: str) -> tuple[str, int]:
    """Return a fresh code for the person's key on the touchscreen, and the seconds it lasts.

    It replaces any code of theirs not yet used. Also clears every device's lapsed codes.
    """
    now = datetime.now(UTC)
    db.session.execute(
        delete(SoloistCode).where(
            or_(
                SoloistCode.expires_at < now,
                (SoloistCode.device_id == device_id) & (SoloistCode.person == person),
            )
        )
    )
    code = secrets.token_hex(CODE_BYTES)
    db.session.add(SoloistCode(code=code, device_id=device_id, person=person, expires_at=now + CODE_TTL))
    db.session.commit()
    return code, int(CODE_TTL.total_seconds())


def code_is_open(code: str) -> bool:
    """Whether a key can be saved with the code: it is a paired touchscreen's, and hasn't lapsed or been used."""
    found = db.session.get(SoloistCode, code)
    return found is not None and found.expires_at >= datetime.now(UTC) and is_paired_touchscreen(found.device)


def save(code: str, key: str) -> bool:
    """Use the code up and save `key` for the touchscreen and person it names, over any they had; False if it won't do.

    The code is taken by a conditional DELETE, so of two posts racing on one code only one saves. The key is never
    logged.
    """
    taken = db.session.execute(
        delete(SoloistCode)
        .where(SoloistCode.code == code, SoloistCode.expires_at >= datetime.now(UTC))
        .returning(SoloistCode.device_id, SoloistCode.person)
    ).first()
    device = db.session.get(Device, taken.device_id) if taken is not None else None
    if taken is None or device is None or not is_paired_touchscreen(device):
        db.session.rollback()
        return False
    saved = db.session.get(SoloistKey, (taken.device_id, taken.person))
    if saved is None:
        saved = SoloistKey(device_id=taken.device_id, person=taken.person)
        db.session.add(saved)
    saved.key = key
    db.session.commit()
    return True


def people(device_id: str) -> list[str]:
    """Return the touchscreen's people who have a key saved, by name."""
    return list(
        db.session.scalars(
            select(SoloistKey.person).where(SoloistKey.device_id == device_id).order_by(SoloistKey.person)
        )
    )


def key_of(device_id: str, person: str) -> SoloistKey | None:
    """Return the key saved for the person on the touchscreen, if there is one."""
    return db.session.get(SoloistKey, (device_id, person))


def remove(device_id: str, person: str) -> None:
    """Delete the person's key on the touchscreen, and any code of theirs not yet used, and commit."""
    db.session.execute(delete(SoloistKey).where(SoloistKey.device_id == device_id, SoloistKey.person == person))
    db.session.execute(delete(SoloistCode).where(SoloistCode.device_id == device_id, SoloistCode.person == person))
    db.session.commit()


def forget(device: Device) -> None:
    """Drop every key and code of the device's (not committed): for one that changes hands or is unpaired."""
    device.soloist_keys.clear()
    device.soloist_codes.clear()
