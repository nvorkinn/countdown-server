"""Add device_users: the users linked to a device, each with a role, in place of devices.owner.

Each device's owner becomes its row with the role "owner"; members (role "member") come later. A device has at most
one owner, which a partial unique index enforces.

Revision ID: 0035
Revises: 0034
Create Date: 2026-11-15 13:00:00.000000

"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0035"
down_revision: str | Sequence[str] | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Alembic looks these up by name, so export them rather than leave them looking unused.
__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    """Create device_users, give it each device's owner, and drop devices.owner."""
    op.create_table(
        "device_users",
        sa.Column("device_id", sa.String(), sa.ForeignKey("devices.device_id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("device_id", "user_id"),
    )
    op.create_index("ix_device_users_user_id", "device_users", ["user_id"])
    op.create_index(
        "ix_device_users_one_owner", "device_users", ["device_id"], unique=True, sqlite_where=sa.text("role = 'owner'")
    )
    op.execute(
        "INSERT INTO device_users (device_id, user_id, role) "
        "SELECT device_id, owner, 'owner' FROM devices WHERE owner IS NOT NULL"
    )
    op.drop_index("ix_devices_owner", "devices")
    with op.batch_alter_table("devices") as batch:
        batch.drop_column("owner")


def downgrade() -> None:
    """Put devices.owner back from the owners' rows, and drop device_users (members are lost)."""
    with op.batch_alter_table("devices") as batch:
        batch.add_column(sa.Column("owner", sa.String(), nullable=True))
    op.create_index("ix_devices_owner", "devices", ["owner"])
    op.execute(
        "UPDATE devices SET owner = (SELECT user_id FROM device_users "
        "WHERE device_users.device_id = devices.device_id AND role = 'owner')"
    )
    op.drop_index("ix_device_users_one_owner", "device_users")
    op.drop_index("ix_device_users_user_id", "device_users")
    op.drop_table("device_users")
