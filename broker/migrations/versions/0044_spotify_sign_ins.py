"""Add spotify_sign_ins: a touchscreen's Spotify sign-ins on its people's phones.

Revision ID: 0044
Revises: 0043
Create Date: 2026-11-24 06:00:00.000000

"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0044"
down_revision: str | Sequence[str] | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Alembic looks these up by name, so export them rather than leave them looking unused.
__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    """Add the table of sign-ins, each a touchscreen's until it lapses or is answered."""
    op.create_table(
        "spotify_sign_ins",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("device_id", sa.String(), sa.ForeignKey("devices.device_id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("code_challenge", sa.String(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("redirect_uri", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("code", sa.String(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
    )
    op.create_index("ix_spotify_sign_ins_device_id", "spotify_sign_ins", ["device_id"])


def downgrade() -> None:
    """Drop the sign-ins."""
    op.drop_index("ix_spotify_sign_ins_device_id", table_name="spotify_sign_ins")
    op.drop_table("spotify_sign_ins")
