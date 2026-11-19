"""poster artwork: brand assets get a kind (logo | artwork)

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-19 12:00:00
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("brand_assets", schema=None) as batch_op:
        batch_op.add_column(sa.Column("kind", sa.String(length=16), nullable=False, server_default="logo"))


def downgrade() -> None:
    with op.batch_alter_table("brand_assets", schema=None) as batch_op:
        batch_op.drop_column("kind")
