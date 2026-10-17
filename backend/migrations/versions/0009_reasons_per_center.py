"""Причины отклонений — по участкам: fact_reasons.work_center_id (пусто — общая причина, видна всем участкам).

Revision ID: 0009
Revises: 0008

Существующие причины остаются общими, история фактов не меняется. Уникальность имени теперь в пределах участка.
"""
import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None



def _rebuild_sqlite(with_center: bool) -> None:
    """SQLite не умеет менять безымянное UNIQUE: пересоздаём таблицу (новое имя → копия → удаление старой → переименование,
    чтобы ссылки из facts на fact_reasons не переписались)."""
    cols = [sa.Column("id", sa.Integer(), nullable=False), sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=160), nullable=False), sa.Column("version", sa.Integer(), nullable=False)]
    cons = [sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]), sa.PrimaryKeyConstraint("id")]
    if with_center:
        cols.append(sa.Column("work_center_id", sa.Integer(), nullable=True))
        cons += [sa.ForeignKeyConstraint(["work_center_id"], ["work_centers.id"]), sa.UniqueConstraint("tenant_id", "work_center_id", "name", name="uq_fact_reasons_tenant_center_name")]
    else:
        cons.append(sa.UniqueConstraint("tenant_id", "name"))
    op.create_table("fact_reasons_new", *cols, *cons)
    fields = "id, tenant_id, name, version"
    op.execute(f"INSERT INTO fact_reasons_new ({fields}) SELECT {fields} FROM fact_reasons")
    op.drop_table("fact_reasons")
    op.rename_table("fact_reasons_new", "fact_reasons")
    op.create_index("ix_fact_reasons_tenant_id", "fact_reasons", ["tenant_id"])
    if with_center:
        op.create_index("ix_fact_reasons_work_center_id", "fact_reasons", ["work_center_id"])


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    if pg:
        op.drop_constraint("fact_reasons_tenant_id_name_key", "fact_reasons", type_="unique")
        op.add_column("fact_reasons", sa.Column("work_center_id", sa.Integer(), sa.ForeignKey("work_centers.id"), nullable=True))
        op.create_unique_constraint("uq_fact_reasons_tenant_center_name", "fact_reasons", ["tenant_id", "work_center_id", "name"])
        op.create_index("ix_fact_reasons_work_center_id", "fact_reasons", ["work_center_id"])
    else:
        _rebuild_sqlite(True)


def downgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    # у разных участков могут быть одинаковые имена — при откате оставляем по одной причине с каждым именем
    op.execute("DELETE FROM fact_reasons WHERE work_center_id IS NOT NULL AND id NOT IN (SELECT MIN(id) FROM fact_reasons GROUP BY tenant_id, name) "
               "AND id NOT IN (SELECT reason_id FROM facts WHERE reason_id IS NOT NULL)")
    if pg:
        op.drop_index("ix_fact_reasons_work_center_id", table_name="fact_reasons")
        op.drop_constraint("uq_fact_reasons_tenant_center_name", "fact_reasons", type_="unique")
        op.drop_column("fact_reasons", "work_center_id")
        op.create_unique_constraint("fact_reasons_tenant_id_name_key", "fact_reasons", ["tenant_id", "name"])
    else:
        _rebuild_sqlite(False)
