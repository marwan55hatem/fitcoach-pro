"""tracking core: planned sets, bed meal slot, nutrition settings

Revision ID: c59bcce4fcee
Revises: 8c0c88cdffee
Create Date: 2026-11-03 18:22:12.637214

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c59bcce4fcee"
down_revision: str | Sequence[str] | None = "8c0c88cdffee"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_SLOTS = ("breakfast", "lunch", "dinner", "snack", "pre_workout", "post_workout")
NEW_SLOTS = (*OLD_SLOTS, "bed")
SLOT_TABLES = ("meal_logs", "meal_options")


def _slot_check(slots: tuple[str, ...]) -> str:
    return "slot IN (" + ", ".join(f"'{s}'" for s in slots) + ")"


def _replace_slot_checks(slots: tuple[str, ...]) -> None:
    # Autogenerate does not see CHECK changes on non-native enums, so swap them here.
    for table in SLOT_TABLES:
        op.drop_constraint(op.f(f"ck_{table}_mealslot"), table, type_="check")
        op.create_check_constraint(
            op.f(f"ck_{table}_mealslot"), table, _slot_check(slots)
        )


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "nutrition_settings",
        sa.Column(
            "calorie_adjustment_kcal",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("protein_g_per_kg", sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "calorie_adjustment_kcal BETWEEN -1000 AND 1000",
            name=op.f("ck_nutrition_settings_calorie_adjustment_range"),
        ),
        sa.CheckConstraint(
            "protein_g_per_kg IS NULL OR protein_g_per_kg BETWEEN 1 AND 3",
            name=op.f("ck_nutrition_settings_protein_range"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_nutrition_settings_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_nutrition_settings")),
        sa.UniqueConstraint("user_id", name=op.f("uq_nutrition_settings_user_id")),
    )
    op.create_index(
        op.f("ix_nutrition_settings_user_id"),
        "nutrition_settings",
        ["user_id"],
        unique=False,
    )

    op.add_column(
        "set_logs",
        sa.Column(
            "done", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    op.add_column("set_logs", sa.Column("target_reps_min", sa.Integer(), nullable=True))
    op.add_column("set_logs", sa.Column("target_reps_max", sa.Integer(), nullable=True))
    op.add_column(
        "set_logs",
        sa.Column("target_weight_kg", sa.Numeric(precision=6, scale=2), nullable=True),
    )
    op.alter_column("set_logs", "reps", existing_type=sa.INTEGER(), nullable=True)
    # Rows logged before planned sets existed were all performed sets.
    op.execute("UPDATE set_logs SET done = true")
    op.create_check_constraint(
        op.f("ck_set_logs_done_has_reps"), "set_logs", "NOT done OR reps IS NOT NULL"
    )
    op.create_check_constraint(
        op.f("ck_set_logs_target_reps_range"),
        "set_logs",
        "target_reps_min IS NULL OR (target_reps_min >= 1 AND "
        "(target_reps_max IS NULL OR target_reps_max >= target_reps_min))",
    )
    op.create_check_constraint(
        op.f("ck_set_logs_target_weight_non_negative"),
        "set_logs",
        "target_weight_kg IS NULL OR target_weight_kg >= 0",
    )

    op.create_index(
        "uq_workout_sessions_one_open_per_user",
        "workout_sessions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    _replace_slot_checks(NEW_SLOTS)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("UPDATE meal_logs SET slot = 'snack' WHERE slot = 'bed'")
    op.execute("UPDATE meal_options SET slot = 'snack' WHERE slot = 'bed'")
    _replace_slot_checks(OLD_SLOTS)
    op.drop_index(
        "uq_workout_sessions_one_open_per_user",
        table_name="workout_sessions",
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.drop_constraint(op.f("ck_set_logs_target_weight_non_negative"), "set_logs")
    op.drop_constraint(op.f("ck_set_logs_target_reps_range"), "set_logs")
    op.drop_constraint(op.f("ck_set_logs_done_has_reps"), "set_logs")
    op.execute("DELETE FROM set_logs WHERE reps IS NULL")
    op.alter_column("set_logs", "reps", existing_type=sa.INTEGER(), nullable=False)
    op.drop_column("set_logs", "target_weight_kg")
    op.drop_column("set_logs", "target_reps_max")
    op.drop_column("set_logs", "target_reps_min")
    op.drop_column("set_logs", "done")
    op.drop_index(
        op.f("ix_nutrition_settings_user_id"), table_name="nutrition_settings"
    )
    op.drop_table("nutrition_settings")
