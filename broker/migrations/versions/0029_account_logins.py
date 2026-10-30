"""Link Spotify, Apple Music, GitHub, Parcel and Glowmarkt to the user's account rather than to each screen.

Each login table is keyed by the owner's user id instead of the device id, keeping the login of the owner's most
recently paired screen that had one (a login on a screen nobody owns goes). Apple Music's play log moves with its
token. Each screen keeps its own switch for each service; device_config.energy_enabled is new, and on for the screens
that had a Glowmarkt password.

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-30 12:00:00.000000

"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0029"
down_revision: str | Sequence[str] | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Alembic looks these up by name, so export them rather than leave them looking unused.
__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

# Each login table, and its columns besides the key.
TABLES = {
    "spotify_tokens": ("refresh_token", "access_token", "expires_at"),
    "glowmarkt_credentials": ("username", "password_encrypted"),
    "github_tokens": ("token_encrypted",),
    "parcel_keys": ("key_encrypted",),
    "apple_music_tokens": ("token_encrypted", "seen_ids"),
}


def _columns(table: str) -> list[sa.Column]:
    """Return the table's columns besides its key, as they are."""
    return {
        "spotify_tokens": [
            sa.Column("refresh_token", sa.String(), nullable=False),
            sa.Column("access_token", sa.String()),
            sa.Column("expires_at", sa.Float()),
        ],
        "glowmarkt_credentials": [
            sa.Column("username", sa.String(), nullable=False, server_default=sa.text("''")),
            sa.Column("password_encrypted", sa.String()),
        ],
        "github_tokens": [sa.Column("token_encrypted", sa.String(), nullable=False)],
        "parcel_keys": [sa.Column("key_encrypted", sa.String(), nullable=False)],
        "apple_music_tokens": [
            sa.Column("token_encrypted", sa.String()),
            sa.Column("seen_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        ],
    }[table]


def _kept(table: str) -> str:
    """Return SQL for the device whose row of `table` its owner keeps: their most recently paired one with a row."""
    return f"""
        SELECT kept.device_id FROM {table} AS kept_row JOIN devices AS kept ON kept.device_id = kept_row.device_id
        WHERE kept.owner = devices.owner ORDER BY kept.paired_at DESC, kept.device_id LIMIT 1
    """  # noqa: S608 -- the table is one of TABLES, never input


def upgrade() -> None:
    """Rekey each login table by the owner, keeping their newest screen's login, and add the energy switch."""
    with op.batch_alter_table("device_config") as batch:
        batch.add_column(sa.Column("energy_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")))
    op.execute(
        "UPDATE device_config SET energy_enabled = 1 WHERE device_id IN "
        "(SELECT device_id FROM glowmarkt_credentials WHERE password_encrypted IS NOT NULL)"
    )

    # The play log goes with the Apple Music token kept, to its owner; the other screens' logs go.
    with op.batch_alter_table("apple_music_plays") as batch:
        batch.add_column(sa.Column("user_id", sa.String()))
    op.execute(
        "UPDATE apple_music_plays SET user_id = (SELECT owner FROM devices WHERE devices.device_id = "  # noqa: S608 -- no input
        f"apple_music_plays.device_id AND devices.device_id = ({_kept('apple_music_tokens')}))"
    )
    op.execute("DELETE FROM apple_music_plays WHERE user_id IS NULL")
    with op.batch_alter_table("apple_music_plays") as batch:
        batch.drop_index("ix_apple_music_plays_device_id")
        batch.drop_column("device_id")
        batch.alter_column("user_id", existing_type=sa.String(), nullable=False)
        batch.create_index("ix_apple_music_plays_user_id", ["user_id"])

    for table, columns in TABLES.items():
        op.create_table(f"{table}_new", sa.Column("user_id", sa.String(), primary_key=True), *_columns(table))
        listed = ", ".join(columns)
        op.execute(
            f"INSERT INTO {table}_new (user_id, {listed}) "  # noqa: S608 -- names from TABLES, never input
            f"SELECT devices.owner, {', '.join(f'{table}.{column}' for column in columns)} "
            f"FROM {table} JOIN devices ON devices.device_id = {table}.device_id "
            f"WHERE devices.owner IS NOT NULL AND devices.device_id = ({_kept(table)})"
        )
        op.drop_table(table)
        op.rename_table(f"{table}_new", table)


def downgrade() -> None:
    """Give each of the owner's screens a copy of their account's logins, and the play log to their newest screen."""
    for table, columns in TABLES.items():
        op.create_table(
            f"{table}_old",
            sa.Column("device_id", sa.String(), sa.ForeignKey("devices.device_id"), primary_key=True),
            *_columns(table),
        )
        listed = ", ".join(columns)
        op.execute(
            f"INSERT INTO {table}_old (device_id, {listed}) "  # noqa: S608 -- names from TABLES, never input
            f"SELECT devices.device_id, {', '.join(f'{table}.{column}' for column in columns)} "
            f"FROM {table} JOIN devices ON devices.owner = {table}.user_id"
        )
        op.drop_table(table)
        op.rename_table(f"{table}_old", table)

    newest = (
        "SELECT device_id FROM devices WHERE devices.owner = apple_music_plays.user_id "
        "ORDER BY paired_at DESC, device_id LIMIT 1"
    )
    with op.batch_alter_table("apple_music_plays") as batch:
        batch.add_column(sa.Column("device_id", sa.String()))
    op.execute(f"UPDATE apple_music_plays SET device_id = ({newest})")  # noqa: S608 -- no input
    op.execute("DELETE FROM apple_music_plays WHERE device_id IS NULL")
    with op.batch_alter_table("apple_music_plays") as batch:
        batch.drop_index("ix_apple_music_plays_user_id")
        batch.drop_column("user_id")
        batch.alter_column("device_id", existing_type=sa.String(), nullable=False)
        batch.create_foreign_key("fk_apple_music_plays_device_id", "devices", ["device_id"], ["device_id"])
        batch.create_index("ix_apple_music_plays_device_id", ["device_id"])

    with op.batch_alter_table("device_config") as batch:
        batch.drop_column("energy_enabled")
