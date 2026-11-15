"""Add spotify_tokens.scope: the scopes Spotify granted the account's link.

A touchscreen plays through the owner's Spotify and needs more scopes than a screen's music panel, so the broker keeps
what each link was granted. Links made before this have none recorded (null), and are linked again for a touchscreen.

Revision ID: 0034
Revises: 0033
Create Date: 2026-11-15 12:00:00.000000

"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0034"
down_revision: str | Sequence[str] | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Alembic looks these up by name, so export them rather than leave them looking unused.
__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    """Add spotify_tokens.scope, null for the links made before it."""
    with op.batch_alter_table("spotify_tokens") as batch:
        batch.add_column(sa.Column("scope", sa.String(), nullable=True))


def downgrade() -> None:
    """Drop spotify_tokens.scope."""
    with op.batch_alter_table("spotify_tokens") as batch:
        batch.drop_column("scope")
