"""Add member_codes: the codes a touchscreen shows for a person to join it as a member.

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
    """Add the table: a code, its touchscreen, when it lapses, and who joined with it (null until someone has)."""
    op.create_table(
        "member_codes",
        sa.Column("code", sa.String(), primary_key=True),
        sa.Column("device_id", sa.String(), sa.ForeignKey("devices.device_id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
    )
    op.create_index("ix_member_codes_device_id", "member_codes", ["device_id"])


def downgrade() -> None:
    """Drop the codes. The members who joined with them stay, as device_users rows."""
    op.drop_index("ix_member_codes_device_id", table_name="member_codes")
    op.drop_table("member_codes")
