"""Add device_config's screen_brightness and quiet hours: a touchscreen's brightness and when its screen sleeps early.

Revision ID: 0040
Revises: 0039
Create Date: 2026-11-22 14:00:00.000000

"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0040"
down_revision: str | Sequence[str] | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Alembic looks these up by name, so export them rather than leave them looking unused.
__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    """Add the columns: full brightness, and quiet hours off, from 22:00 to 07:00 once switched on.

    As every device was before them: at full brightness, with no quiet hours.
    """
    with op.batch_alter_table("device_config") as batch:
        batch.add_column(sa.Column("screen_brightness", sa.Integer(), nullable=False, server_default=sa.text("100")))
        batch.add_column(sa.Column("quiet_hours_on", sa.Boolean(), nullable=False, server_default=sa.text("0")))
        batch.add_column(sa.Column("quiet_hours_start", sa.Integer(), nullable=False, server_default=sa.text("1320")))
        batch.add_column(sa.Column("quiet_hours_end", sa.Integer(), nullable=False, server_default=sa.text("420")))


def downgrade() -> None:
    """Drop the columns."""
    with op.batch_alter_table("device_config") as batch:
        batch.drop_column("quiet_hours_end")
        batch.drop_column("quiet_hours_start")
        batch.drop_column("quiet_hours_on")
        batch.drop_column("screen_brightness")
