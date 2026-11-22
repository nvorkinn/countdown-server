"""Add device_config's household address: a Norwegian notice board's street, house number and city, for its bins.

Revision ID: 0041
Revises: 0040
Create Date: 2026-11-22 16:00:00.000000

"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0041"
down_revision: str | Sequence[str] | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Alembic looks these up by name, so export them rather than leave them looking unused.
__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    """Add the columns, all null: no device has an address set yet."""
    with op.batch_alter_table("device_config") as batch:
        batch.add_column(sa.Column("address_street", sa.String(), nullable=True))
        batch.add_column(sa.Column("address_house_number", sa.String(), nullable=True))
        batch.add_column(sa.Column("address_city", sa.String(), nullable=True))


def downgrade() -> None:
    """Drop the columns."""
    with op.batch_alter_table("device_config") as batch:
        batch.drop_column("address_city")
        batch.drop_column("address_house_number")
        batch.drop_column("address_street")
