"""Add soloist_keys: a user's Spotify Soloist API key, which their touchscreens start Soloist with.

Revision ID: 0042
Revises: 0041
Create Date: 2026-11-23 12:00:00.000000

"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0042"
down_revision: str | Sequence[str] | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Alembic looks these up by name, so export them rather than leave them looking unused.
__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    """Add the table for the Soloist API keys, encrypted and keyed by their owner like the other logins."""
    op.create_table(
        "soloist_keys",
        sa.Column("user_id", sa.String(), primary_key=True),
        sa.Column("key_encrypted", sa.String(), nullable=False),
    )


def downgrade() -> None:
    """Drop the Soloist API keys."""
    op.drop_table("soloist_keys")
