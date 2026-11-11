"""Drop github_app_tokens: the GitHub dashboard moved to an app of its own, so its sign-in goes.

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-11 12:00:00.000000

"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0032"
down_revision: str | Sequence[str] | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Alembic looks these up by name, so export them rather than leave them looking unused.
__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    """Drop github_app_tokens, and the tokens in it."""
    op.drop_table("github_app_tokens")


def downgrade() -> None:
    """Recreate github_app_tokens as 0024 made it, empty."""
    op.create_table(
        "github_app_tokens",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("access_token_encrypted", sa.String(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("refresh_expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )
