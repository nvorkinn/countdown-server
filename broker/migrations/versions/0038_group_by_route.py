"""Add device_config.arrivals_group_by_route: whether a stop's departures are grouped by route.

Revision ID: 0038
Revises: 0037
Create Date: 2026-11-20 15:00:00.000000

"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0038"
down_revision: str | Sequence[str] | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Alembic looks these up by name, so export them rather than leave them looking unused.
__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    """Add the switch, off, so every device shows a row per departure as before."""
    with op.batch_alter_table("device_config") as batch:
        batch.add_column(
            sa.Column("arrivals_group_by_route", sa.Boolean(), nullable=False, server_default=sa.text("0"))
        )


def downgrade() -> None:
    """Drop the switch."""
    with op.batch_alter_table("device_config") as batch:
        batch.drop_column("arrivals_group_by_route")
