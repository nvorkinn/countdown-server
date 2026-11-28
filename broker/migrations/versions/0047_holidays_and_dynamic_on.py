"""Add device_config.holidays_days_ahead, and make Dynamic on by default.

Revision ID: 0047
Revises: 0046
Create Date: 2026-11-28 07:30:00.000000

"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0047"
down_revision: str | Sequence[str] | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Alembic looks these up by name, so export them rather than leave them looking unused.
__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    """Mention holidays a week ahead, and start new touchscreens with Dynamic on."""
    with op.batch_alter_table("device_config") as batch:
        batch.add_column(sa.Column("holidays_days_ahead", sa.Integer(), nullable=False, server_default=sa.text("7")))
        batch.alter_column("theme_dynamic", existing_type=sa.Boolean(), server_default=sa.text("1"))


def downgrade() -> None:
    """Drop holidays_days_ahead, and start new touchscreens with Dynamic off again."""
    with op.batch_alter_table("device_config") as batch:
        batch.alter_column("theme_dynamic", existing_type=sa.Boolean(), server_default=sa.text("0"))
        batch.drop_column("holidays_days_ahead")
