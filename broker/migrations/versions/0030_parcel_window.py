"""Add device_config.parcels_days_ahead and parcels_kept_hours, which parcels a screen shows and for how long.

Existing screens keep what they showed: any day (null), and delivered parcels for 4 hours.

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-31 04:00:00.000000

"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0030"
down_revision: str | Sequence[str] | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Alembic looks these up by name, so export them rather than leave them looking unused.
__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    """Add device_config.parcels_days_ahead and parcels_kept_hours."""
    with op.batch_alter_table("device_config") as batch:
        batch.add_column(sa.Column("parcels_days_ahead", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("parcels_kept_hours", sa.Integer(), server_default=sa.text("4"), nullable=False))


def downgrade() -> None:
    """Drop device_config.parcels_days_ahead and parcels_kept_hours."""
    with op.batch_alter_table("device_config") as batch:
        batch.drop_column("parcels_kept_hours")
        batch.drop_column("parcels_days_ahead")
