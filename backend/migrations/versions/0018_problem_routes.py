"""Группы Telegram для проблем с участков (problem_routes).

Revision ID: 0018
Revises: 0017
"""
import os

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "problem_routes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("chat_ref", sa.String(64), nullable=False),
        sa.Column("section_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_status", sa.String(8), nullable=True),
        sa.Column("last_error", sa.String(300), nullable=True),
    )
    op.create_index(op.f("ix_problem_routes_tenant_id"), "problem_routes", ["tenant_id"])
    if op.get_bind().dialect.name == "postgresql":
        app_role = os.environ.get("PLAN24_APP_ROLE", "plan24_app")
        op.execute("ALTER TABLE problem_routes ENABLE ROW LEVEL SECURITY")
        op.execute("CREATE POLICY tenant_isolation ON problem_routes "
                   "USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()))")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON problem_routes TO {app_role}")


def downgrade() -> None:
    op.drop_index(op.f("ix_problem_routes_tenant_id"), table_name="problem_routes")
    op.drop_table("problem_routes")
