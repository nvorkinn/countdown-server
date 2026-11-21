"""Add device_config.brightness_control: whether a touchscreen can set its screen's brightness.

Revision ID: 0039
Revises: 0038
Create Date: 2026-11-21 23:55:00.000000

"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0039"
down_revision: str | Sequence[str] | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Alembic looks these up by name, so export them rather than leave them looking unused.
__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    """Add the column, false, as no device registered before it said it can."""
    with op.batch_alter_table("device_config") as batch:
        batch.add_column(sa.Column("brightness_control", sa.Boolean(), nullable=False, server_default=sa.text("0")))


def downgrade() -> None:
    """Drop the column."""
    with op.batch_alter_table("device_config") as batch:
        batch.drop_column("brightness_control")
