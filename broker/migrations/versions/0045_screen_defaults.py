"""Keep the settings a user's new screens start with, one set for each type of device.

screen_defaults has a snapshot of one screen's settings (services/screen_defaults.py), saved from its page, which a
screen of that type takes the first time the user pairs with it.

Revision ID: 0045
Revises: 0044
Create Date: 2026-11-25 12:00:00.000000

"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0045"
down_revision: str | Sequence[str] | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Alembic looks these up by name, so export them rather than leave them looking unused.
__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    """Add the table of settings saved for new screens, by user and type of device."""
    op.create_table(
        "screen_defaults",
        sa.Column("user_id", sa.String(), primary_key=True),
        sa.Column("type", sa.String(), primary_key=True),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("saved_at", sa.DateTime(), nullable=False),
        sa.Column("source_name", sa.String(), nullable=False),
    )


def downgrade() -> None:
    """Drop the settings saved for new screens."""
    op.drop_table("screen_defaults")
