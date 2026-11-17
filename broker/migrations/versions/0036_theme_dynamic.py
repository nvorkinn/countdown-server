"""Add device_config.theme_dynamic: Dynamic as an on/off beside the colour, not one of the colours.

A touchscreen whose colour was dynamic gets forest with Dynamic on, which is how it looked without any album art.

Revision ID: 0036
Revises: 0035
Create Date: 2026-11-17 06:30:00.000000

"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0036"
down_revision: str | Sequence[str] | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Alembic looks these up by name, so export them rather than leave them looking unused.
__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    """Add theme_dynamic, and turn the dynamic colour into forest with it on."""
    with op.batch_alter_table("device_config") as batch:
        batch.add_column(sa.Column("theme_dynamic", sa.Boolean(), nullable=False, server_default=sa.text("0")))
    op.execute("UPDATE device_config SET theme_colour = 'forest', theme_dynamic = 1 WHERE theme_colour = 'dynamic'")


def downgrade() -> None:
    """Put Dynamic back as the colour where it was on, and drop theme_dynamic."""
    op.execute("UPDATE device_config SET theme_colour = 'dynamic' WHERE theme_dynamic = 1")
    with op.batch_alter_table("device_config") as batch:
        batch.drop_column("theme_dynamic")
