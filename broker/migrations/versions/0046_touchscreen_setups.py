"""Add touchscreen_setups: a touchscreen's setup, in step on its own screen and its owner's phone.

Revision ID: 0046
Revises: 0045
Create Date: 2026-11-28 04:00:00.000000

"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0046"
down_revision: str | Sequence[str] | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Alembic looks these up by name, so export them rather than leave them looking unused.
__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    """Add the table of setups, one for each touchscreen that has reported on its own."""
    op.create_table(
        "touchscreen_setups",
        sa.Column("device_id", sa.String(), sa.ForeignKey("devices.device_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("spotify_client_id", sa.String(), nullable=True),
        sa.Column("spotify_code_challenge", sa.String(), nullable=True),
        sa.Column("spotify_scope", sa.String(), nullable=True),
        sa.Column("spotify_answered_at", sa.DateTime(), nullable=True),
        sa.Column("soloist_device", sa.String(), nullable=True),
        sa.Column("soloist_signed_in", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("location_done_at", sa.DateTime(), nullable=True),
        sa.Column("phone_paired", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    """Drop the setups."""
    op.drop_table("touchscreen_setups")
