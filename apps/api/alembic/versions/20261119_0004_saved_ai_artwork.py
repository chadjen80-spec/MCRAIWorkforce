"""saved AI artwork: brand assets record where an artwork came from (upload | ai)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-19 15:00:00
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("brand_assets", schema=None) as batch_op:
        batch_op.add_column(sa.Column("source", sa.String(length=16), nullable=False, server_default="upload"))


def downgrade() -> None:
    with op.batch_alter_table("brand_assets", schema=None) as batch_op:
        batch_op.drop_column("source")
