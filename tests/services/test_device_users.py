"""Test device_users: who a device is linked to, with a role, and Device.owner, its owner's row."""

import pytest
from flask_migrate import downgrade, upgrade
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from broker.db import db
from broker.models import Device, DeviceUser
from broker.services import accounts


def _device(device_id="dev-1", **fields):
    device = Device(device_id=device_id, created_at=db.func.now(), **fields)
    db.session.add(device)
    db.session.commit()
    return device


def _links(device_id="dev-1"):
    return sorted(
        (link.user_id, link.role)
        for link in db.session.scalars(select(DeviceUser).where(DeviceUser.device_id == device_id))
    )


def test_a_device_starts_with_no_one_linked(app):
    with app.app_context():
        device = _device()
        assert device.owner is None
        assert _links() == []


def test_setting_the_owner_is_its_owner_row(app):
    with app.app_context():
        device = _device()
        device.owner = "alice"
        db.session.commit()
        assert _links() == [("alice", "owner")]
        assert db.session.scalar(select(Device.device_id).where(Device.owner == "alice")) == "dev-1"


def test_a_new_owner_replaces_the_old_one(app):
    with app.app_context():
        device = _device()
        device.owner = "alice"
        db.session.commit()
        device.owner = "bob"
        db.session.commit()
        assert _links() == [("bob", "owner")]


def test_a_member_who_becomes_the_owner_keeps_one_link(app):
    with app.app_context():
        device = _device()
        device.owner = "alice"
        device.users.append(DeviceUser(user_id="bob", role="member"))
        db.session.commit()
        device.owner = "bob"
        db.session.commit()
        assert _links() == [("bob", "owner")]


def test_no_owner_drops_the_owner_and_keeps_the_members(app):
    with app.app_context():
        device = _device()
        device.owner = "alice"
        device.users.append(DeviceUser(user_id="bob", role="member"))
        db.session.commit()
        device.owner = None
        db.session.commit()
        assert _links() == [("bob", "member")]


def test_the_database_allows_one_owner_a_device(app):
    with app.app_context():
        _device()
        db.session.add_all([DeviceUser(device_id="dev-1", user_id=u, role="owner") for u in ("alice", "bob")])
        with pytest.raises(IntegrityError):
            db.session.commit()


def test_deleting_a_device_deletes_its_links(app):
    with app.app_context():
        device = _device()
        device.owner = "alice"
        db.session.commit()
        db.session.delete(device)
        db.session.commit()
        assert _links() == []


def test_an_owned_devices_own_saves_are_approved(app):
    with app.app_context():
        device = _device()
        assert not accounts.approves_device_save(device)
        device.owner = "alice"
        assert accounts.approves_device_save(device)


def test_the_migration_moves_owners_both_ways(app):
    with app.app_context():
        downgrade(revision="0034")
        with db.engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO devices (device_id, owner, created_at) VALUES "
                "('owned', 'alice', '2026-11-15 00:00:00'), ('unowned', NULL, '2026-11-15 00:00:00')"
            )
        upgrade()
        with db.engine.connect() as conn:
            assert conn.exec_driver_sql("SELECT device_id, user_id, role FROM device_users").all() == [
                ("owned", "alice", "owner")
            ]
        downgrade(revision="0034")
        with db.engine.connect() as conn:
            rows = conn.exec_driver_sql("SELECT device_id, owner FROM devices ORDER BY device_id").all()
            assert rows == [("owned", "alice"), ("unowned", None)]
        upgrade()
