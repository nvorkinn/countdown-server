"""Keep a Soloist API key for each of a touchscreen's people, not one for its owner's account.

soloist_keys was keyed by the owner's user id (0042). Spotify has each person make their own key, and a touchscreen
has several people on it, so a key is now kept for a touchscreen and a person, and soloist_codes has the codes they
save theirs with. The keys saved to accounts are dropped: nothing read them yet, and they are pasted again with a code.

Revision ID: 0043
Revises: 0042
Create Date: 2026-11-24 12:00:00.000000

"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0043"
down_revision: str | Sequence[str] | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Alembic looks these up by name, so export them rather than leave them looking unused.
__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    """Replace the accounts' keys with a table of keys by touchscreen and person, and add their codes."""
    op.drop_table("soloist_keys")
    op.create_table(
        "soloist_keys",
        sa.Column("device_id", sa.String(), sa.ForeignKey("devices.device_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("person", sa.String(), primary_key=True),
        sa.Column("key_encrypted", sa.String(), nullable=False),
    )
    op.create_table(
        "soloist_codes",
        sa.Column("code", sa.String(), primary_key=True),
        sa.Column("device_id", sa.String(), sa.ForeignKey("devices.device_id", ondelete="CASCADE"), nullable=False),
        sa.Column("person", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_soloist_codes_device_id", "soloist_codes", ["device_id"])


def downgrade() -> None:
    """Drop the codes and the people's keys, and put back the empty table of accounts' keys."""
    op.drop_index("ix_soloist_codes_device_id", table_name="soloist_codes")
    op.drop_table("soloist_codes")
    op.drop_table("soloist_keys")
    op.create_table(
        "soloist_keys",
        sa.Column("user_id", sa.String(), primary_key=True),
        sa.Column("key_encrypted", sa.String(), nullable=False),
    )
