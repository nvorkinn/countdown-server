"""Add the parcels panel: device_config.parcels_enabled, and parcel_keys for its Parcel API key.

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-30 12:00:00.000000

"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0028"
down_revision: str | Sequence[str] | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Alembic looks these up by name, so export them rather than leave them looking unused.
__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    """Add the parcels switch, off for every device, and the table for the Parcel API keys, encrypted."""
    with op.batch_alter_table("device_config") as batch:
        batch.add_column(sa.Column("parcels_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")))
    op.create_table(
        "parcel_keys",
        sa.Column("device_id", sa.String(), sa.ForeignKey("devices.device_id"), primary_key=True),
        sa.Column("key_encrypted", sa.String(), nullable=False),
    )


def downgrade() -> None:
    """Drop the Parcel API keys and the switch."""
    op.drop_table("parcel_keys")
    with op.batch_alter_table("device_config") as batch:
        batch.drop_column("parcels_enabled")
