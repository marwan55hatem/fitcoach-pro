"""meal kcal range

Revision ID: 115460917612
Revises: e9091c29ade3
Create Date: 2026-11-04 18:59:45.769646

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "115460917612"
down_revision: str | Sequence[str] | None = "e9091c29ade3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """An estimated meal keeps its calorie range; kcal stays the midpoint."""
    op.add_column("meal_logs", sa.Column("kcal_min", sa.Integer(), nullable=True))
    op.add_column("meal_logs", sa.Column("kcal_max", sa.Integer(), nullable=True))
    op.create_check_constraint(
        op.f("ck_meal_logs_kcal_range"),
        "meal_logs",
        "kcal_min IS NULL OR (kcal_min >= 0 AND kcal_max IS NOT NULL "
        "AND kcal_max >= kcal_min)",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_meal_logs_kcal_range"), "meal_logs", type_="check")
    op.drop_column("meal_logs", "kcal_max")
    op.drop_column("meal_logs", "kcal_min")
