"""budget reservations: money held for in-flight paid calls (jobs, tasks, organizations)

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-20 12:00:00
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_MONEY = sa.Numeric(14, 6)  # same as app.models.Money


def upgrade() -> None:
    with op.batch_alter_table("organizations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("reserved_usd", _MONEY, nullable=False, server_default="0"))
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("reserved_cost_usd", _MONEY, nullable=False, server_default="0"))
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("reserved_cost_usd", _MONEY, nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_column("reserved_cost_usd")
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_column("reserved_cost_usd")
    with op.batch_alter_table("organizations", schema=None) as batch_op:
        batch_op.drop_column("reserved_usd")
