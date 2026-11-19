"""Add device_config.lyrics_size and lyrics_align: how a touchscreen shows synced lyrics.

Revision ID: 0037
Revises: 0036
Create Date: 2026-11-19 07:30:00.000000

"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0037"
down_revision: str | Sequence[str] | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Alembic looks these up by name, so export them rather than leave them looking unused.
__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    """Add the lyrics' size and alignment, at medium and left, as every touchscreen showed them."""
    with op.batch_alter_table("device_config") as batch:
        batch.add_column(sa.Column("lyrics_size", sa.String(), nullable=False, server_default=sa.text("'medium'")))
        batch.add_column(sa.Column("lyrics_align", sa.String(), nullable=False, server_default=sa.text("'left'")))


def downgrade() -> None:
    """Drop the lyrics' size and alignment."""
    with op.batch_alter_table("device_config") as batch:
        batch.drop_column("lyrics_align")
        batch.drop_column("lyrics_size")
