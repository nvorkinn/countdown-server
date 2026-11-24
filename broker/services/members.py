"""A touchscreen's members: the people of a household who joined it, beside its owner, each with their own account.

A touchscreen is its owner's (services/pairing.py), but several people use one, and some of what it does for a person
needs something that is theirs alone: Spotify has each person make their own Soloist API key, and not share it. So a
person links their own account to the touchscreen, as a member (DeviceUser's role), and the touchscreen can then ask
for what they saved for themselves (GET /api/devices/soloist/key?user_id=).

Joining is the person's own doing, and needs them at the touchscreen:

1. the touchscreen asks for a code (issue_code) and shows it as a link to the settings site's /join;
2. the person opens it on their phone, signs in to their own account and confirms (join);
3. the touchscreen reads who joined off the code (code_of), and names them by that user id from then on.

Who is in the room is the touchscreen's own business (it lets a person be picked only while their phone is near).
What the broker keeps is only who agreed to be linked: anyone can register a device and say what they like from it,
so a device is never told anything of a person who didn't join it themselves.

A member has no say over the touchscreen: its settings stay its owner's. Members go when it changes hands or is
unpaired (forget), as they joined it as its last owner's.
"""

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select, update

from broker.db import db
from broker.models import Device, DeviceUser, MemberCode

# As long as a pairing code: time to pick up a phone, sign in and confirm.
CODE_TTL = timedelta(minutes=10)
# 16 random bytes as hex, the contract's MemberCodeValue: it's a link's query, never typed, so it can't be guessed.
CODE_BYTES = 16


def is_paired_touchscreen(device: Device) -> bool:
    """Whether the device is a touchscreen someone owns: only those have members."""
    return device.paired_at is not None and device.type == "touchscreen" and device.owner is not None


def issue_code(device_id: str) -> tuple[str, int]:
    """Return a fresh code for the touchscreen and the seconds it lasts, replacing any it had that no one used.

    A used code stays until it lapses, so the touchscreen can still read who joined with it. Also clears every
    device's lapsed codes.
    """
    now = datetime.now(UTC)
    db.session.execute(
        delete(MemberCode).where(
            or_(
                MemberCode.expires_at < now,
                (MemberCode.device_id == device_id) & MemberCode.user_id.is_(None),
            )
        )
    )
    code = secrets.token_hex(CODE_BYTES)
    db.session.add(MemberCode(code=code, device_id=device_id, expires_at=now + CODE_TTL))
    db.session.commit()
    return code, int(CODE_TTL.total_seconds())


def code_of(device_id: str, code: str) -> MemberCode | None:
    """Return the touchscreen's own live code, used or not; None for one that lapsed or is another device's."""
    found = db.session.get(MemberCode, code)
    if found is None or found.device_id != device_id or found.expires_at < datetime.now(UTC):
        return None
    return found


def open_code(code: str) -> Device | None:
    """Return the touchscreen a code would join its user to; None for one that's lapsed, used or no touchscreen's."""
    found = db.session.get(MemberCode, code)
    if found is None or found.user_id is not None or found.expires_at < datetime.now(UTC):
        return None
    return found.device if is_paired_touchscreen(found.device) else None


def join(code: str, user_id: str) -> Device | None:
    """Use the code up and link the user to its touchscreen as a member, returning it; None if the code won't do.

    The code is taken by a conditional UPDATE, so of two people racing on one code only one joins. The owner is
    linked already, and so is someone who joined before: the code then only tells the touchscreen who they are.
    """
    now = datetime.now(UTC)
    taken = db.session.execute(
        update(MemberCode)
        .where(MemberCode.code == code, MemberCode.user_id.is_(None), MemberCode.expires_at >= now)
        .values(user_id=user_id)
        .returning(MemberCode.device_id)
    ).first()
    device = db.session.get(Device, taken.device_id) if taken is not None else None
    if device is None or not is_paired_touchscreen(device):
        db.session.rollback()
        return None
    if not is_linked(device, user_id):
        device.users.append(DeviceUser(user_id=user_id, role="member"))
    db.session.commit()
    return device


def is_linked(device: Device, user_id: str) -> bool:
    """Whether the user is the device's owner or one of its members."""
    return any(link.user_id == user_id for link in device.users)


def remove(device: Device, user_id: str) -> bool:
    """Unlink a member from the device and commit; False for its owner, who isn't one. Anyone else is left unlinked."""
    if device.owner == user_id:
        return False
    device.users = [link for link in device.users if link.user_id != user_id]
    db.session.commit()
    return True


def forget(device: Device) -> None:
    """Drop the device's members and its codes (not committed): for one that changes hands or is unpaired."""
    device.users = [link for link in device.users if link.role == "owner"]
    device.member_codes.clear()


def joined(user_id: str) -> list[Device]:
    """Return the touchscreens the user has joined as a member, by id."""
    return list(
        db.session.scalars(
            select(Device)
            .join(DeviceUser, DeviceUser.device_id == Device.device_id)
            .where(DeviceUser.user_id == user_id, DeviceUser.role == "member")
            .order_by(Device.device_id)
        )
    )
