"""Add touchscreens: devices.type, and a touchscreen's own settings in device_config.

Every existing device is an e-paper screen, and every config gets the touchscreen settings' defaults (day, forest, 5
minutes), which only a touchscreen's config carries.

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-15 12:00:00.000000

"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0033"
down_revision: str | Sequence[str] | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Alembic looks these up by name, so export them rather than leave them looking unused.
__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    """Add devices.type and device_config's theme, theme_colour and screen_sleep_minutes."""
    with op.batch_alter_table("devices") as batch:
        batch.add_column(sa.Column("type", sa.String(), nullable=False, server_default=sa.text("'epaper'")))
    with op.batch_alter_table("device_config") as batch:
        batch.add_column(sa.Column("theme", sa.String(), nullable=False, server_default=sa.text("'day'")))
        batch.add_column(sa.Column("theme_colour", sa.String(), nullable=False, server_default=sa.text("'forest'")))
        batch.add_column(sa.Column("screen_sleep_minutes", sa.Integer(), nullable=False, server_default=sa.text("5")))


def downgrade() -> None:
    """Drop them."""
    with op.batch_alter_table("device_config") as batch:
        batch.drop_column("screen_sleep_minutes")
        batch.drop_column("theme_colour")
        batch.drop_column("theme")
    with op.batch_alter_table("devices") as batch:
        batch.drop_column("type")
