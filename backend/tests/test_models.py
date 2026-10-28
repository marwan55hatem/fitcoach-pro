import datetime as dt
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    AuditLog,
    Checkin,
    Consent,
    Exercise,
    InbodyScan,
    MealLog,
    Profile,
    Program,
    ProgramDay,
    ProgramExercise,
    SetLog,
    StoredFile,
    User,
    WeightLog,
    WorkoutSession,
)
from app.models.enums import (
    ConsentType,
    Experience,
    FileKind,
    Goal,
    MealSlot,
    MealSource,
    ProgramStatus,
    Sex,
)

TODAY = dt.date(2026, 10, 28)


def expect_rejected(
    db: Session, *objs: object, error: type[Exception] = IntegrityError
) -> None:
    """Assert the database rejects these rows, leaving the session usable afterwards."""
    with pytest.raises(error), db.begin_nested():
        db.add_all(objs)
        db.flush()
    for obj in objs:
        if obj in db:
            db.expunge(obj)


def make_user(db: Session, email: str | None = None) -> User:
    user = User(email=email or f"{uuid.uuid4().hex[:8]}@example.com", password_hash="x")
    db.add(user)
    db.flush()
    return user


def make_file(db: Session, user: User, kind: FileKind) -> StoredFile:
    f = StoredFile(
        user_id=user.id,
        storage_key=f"{user.id}/{uuid.uuid4()}",
        content_type="image/jpeg",
        size_bytes=1234,
        sha256="a" * 64,
        kind=kind,
    )
    db.add(f)
    db.flush()
    return f


def make_exercise(db: Session, slug: str = "barbell-bench-press") -> Exercise:
    ex = Exercise(
        name="Barbell Bench Press",
        slug=slug,
        muscle_primary="chest",
        muscles_secondary=["triceps", "front_delts"],
        equipment="barbell",
        level=Experience.BEGINNER,
        instructions=["Lie on the bench", "Lower the bar to mid-chest", "Press up"],
        image_keys=[],
        source="test",
        license="CC0",
    )
    db.add(ex)
    db.flush()
    return ex


def test_user_with_profile_and_defaults(db: Session) -> None:
    user = make_user(db)
    db.add(
        Profile(
            user_id=user.id,
            birth_date=dt.date(1995, 5, 17),
            sex=Sex.MALE,
            height_cm=Decimal("178.5"),
            goal=Goal.LEAN_BULK,
            experience=Experience.INTERMEDIATE,
            days_per_week=4,
            session_minutes=60,
            equipment=["barbell", "dumbbell"],
        )
    )
    db.flush()
    db.refresh(user)

    assert (
        user.id is not None
        and user.created_at is not None
        and user.updated_at is not None
    )
    assert user.profile is not None
    assert user.profile.timezone == "Africa/Cairo"
    assert user.profile.units == "metric"
    assert user.profile.goal is Goal.LEAN_BULK
    assert user.profile.equipment == ["barbell", "dumbbell"]


def test_program_session_with_sets(db: Session) -> None:
    user = make_user(db)
    bench = make_exercise(db)

    program = Program(
        user_id=user.id,
        version=1,
        status=ProgramStatus.ACTIVE,
        generated_by="ai",
        rationale="Upper/lower split for 4 days a week.",
    )
    day = ProgramDay(user_id=user.id, day_index=1, name="Upper A", focus="chest/back")
    day.exercises.append(
        ProgramExercise(
            user_id=user.id,
            exercise_id=bench.id,
            order=1,
            sets=3,
            rep_min=6,
            rep_max=10,
            rest_s=120,
        )
    )
    program.days.append(day)
    db.add(program)
    db.flush()

    session = WorkoutSession(user_id=user.id, program_day_id=day.id, performed_on=TODAY)
    for n, (reps, kg) in enumerate([(10, "60"), (8, "65"), (7, "65")], start=1):
        session.sets.append(
            SetLog(
                user_id=user.id,
                exercise_id=bench.id,
                program_exercise_id=day.exercises[0].id,
                set_number=n,
                reps=reps,
                weight_kg=Decimal(kg),
                rpe=Decimal("8.5"),
            )
        )
    db.add(session)
    db.flush()
    db.expire_all()

    loaded = db.get(WorkoutSession, session.id)
    assert loaded is not None
    assert [s.reps for s in loaded.sets] == [10, 8, 7]
    assert (
        db.get(Program, program.id).days[0].exercises[0].exercise.slug
        == "barbell-bench-press"
    )


def test_inbody_scan_and_meal_log(db: Session) -> None:
    user = make_user(db)
    scan_file = make_file(db, user, FileKind.INBODY)
    food_file = make_file(db, user, FileKind.FOOD)

    scan = InbodyScan(
        user_id=user.id,
        taken_on=TODAY,
        file_id=scan_file.id,
        weight_kg=Decimal("78.4"),
        smm_kg=Decimal("36.1"),
        body_fat_mass_kg=Decimal("14.2"),
        pbf_pct=Decimal("18.1"),
        bmi=Decimal("24.6"),
        tbw_l=Decimal("46.0"),
        bmr_kcal=1790,
        visceral_fat_level=6,
        inbody_score=78,
        segmental={"right_arm": {"lean_kg": 3.4, "fat_kg": 0.9}},
        extraction_confidence={"weight_kg": 0.99, "smm_kg": 0.9},
        ai_raw={"model": "test"},
    )
    meal = MealLog(
        user_id=user.id,
        date=TODAY,
        slot=MealSlot.LUNCH,
        source=MealSource.PHOTO,
        name="Chicken, rice and salad",
        items=[
            {"name": "grilled chicken", "grams": 180},
            {"name": "rice", "grams": 200},
        ],
        kcal=720,
        protein_g=Decimal("55.0"),
        carbs_g=Decimal("80.0"),
        fat_g=Decimal("18.0"),
        confidence=Decimal("0.72"),
        ai_raw={"model": "test"},
        file_id=food_file.id,
    )
    db.add_all([scan, meal])
    db.flush()
    db.refresh(scan)
    db.refresh(meal)

    # AI-extracted data is a draft until the user confirms it.
    assert scan.confirmed is False and meal.confirmed is False
    assert meal.user_edited is False
    assert scan.segmental["right_arm"]["lean_kg"] == 3.4


def test_draft_scan_can_be_confirmed_but_flag_requires_timestamp(db: Session) -> None:
    user = make_user(db)
    scan = InbodyScan(user_id=user.id, taken_on=TODAY, weight_kg=Decimal(80))
    db.add(scan)
    db.flush()

    other = InbodyScan(
        user_id=user.id, taken_on=TODAY, confirmed=True
    )  # no confirmed_at
    expect_rejected(db, other)

    scan.confirmed = True
    scan.confirmed_at = dt.datetime.now(dt.UTC)
    db.flush()


def test_cascade_delete_removes_all_user_data(db: Session) -> None:
    user = make_user(db)
    other = make_user(db)
    f = make_file(db, user, FileKind.BODY_PHOTO)
    db.add_all(
        [
            Consent(user_id=user.id, type=ConsentType.HEALTH_DATA),
            WeightLog(user_id=user.id, date=TODAY, weight_kg=Decimal(80)),
            WeightLog(user_id=other.id, date=TODAY, weight_kg=Decimal(70)),
            InbodyScan(user_id=user.id, taken_on=TODAY, file_id=f.id),
        ]
    )
    db.flush()

    db.execute(sa.delete(User).where(User.id == user.id))
    db.expire_all()

    for model in (Consent, WeightLog, InbodyScan, StoredFile):
        assert (
            db.scalars(sa.select(model).where(model.user_id == user.id)).first() is None
        )
    # The other user's data is untouched.
    assert db.scalars(sa.select(WeightLog).where(WeightLog.user_id == other.id)).one()


def test_audit_log_survives_user_deletion(db: Session) -> None:
    user = make_user(db)
    entry = AuditLog(
        user_id=user.id, action="account.delete", meta={"reason": "user_request"}
    )
    db.add(entry)
    db.flush()

    db.execute(sa.delete(User).where(User.id == user.id))
    db.expire_all()
    kept = db.get(AuditLog, entry.id)
    assert (
        kept is not None
        and kept.user_id is None
        and kept.meta == {"reason": "user_request"}
    )


def test_unique_constraints(db: Session) -> None:
    user = make_user(db)
    db.add(WeightLog(user_id=user.id, date=TODAY, weight_kg=Decimal(80)))
    db.flush()

    expect_rejected(db, WeightLog(user_id=user.id, date=TODAY, weight_kg=Decimal(81)))

    make_user(db, "dup@example.com")
    expect_rejected(db, User(email="dup@example.com", password_hash="x"))
    expect_rejected(
        db, User(email="Mixed@Example.com", password_hash="x")
    )  # must be lowercase


def test_catalog_exercise_slug_is_unique_but_custom_may_reuse(db: Session) -> None:
    user = make_user(db)
    make_exercise(db, "squat")
    expect_rejected(db, Exercise(name="Squat 2", slug="squat", muscle_primary="quads"))

    custom = Exercise(
        user_id=user.id, name="My Squat", slug="squat", muscle_primary="quads"
    )
    db.add(custom)
    db.flush()


def test_only_one_active_program_per_user(db: Session) -> None:
    user = make_user(db)
    db.add(
        Program(
            user_id=user.id, version=1, status=ProgramStatus.ACTIVE, generated_by="ai"
        )
    )
    db.flush()
    expect_rejected(
        db,
        Program(
            user_id=user.id, version=2, status=ProgramStatus.ACTIVE, generated_by="ai"
        ),
    )
    db.add(
        Program(
            user_id=user.id, version=2, status=ProgramStatus.DRAFT, generated_by="ai"
        )
    )
    db.flush()


@pytest.mark.parametrize(
    "factory",
    [
        lambda uid: Checkin(user_id=uid, date=TODAY, soreness=6),
        lambda uid: ProgramExercise(
            user_id=uid,
            program_day_id=uuid.uuid4(),
            exercise_id=uuid.uuid4(),
            order=1,
            sets=3,
            rep_min=10,
            rep_max=5,
        ),
        lambda uid: Profile(user_id=uid, days_per_week=9),
    ],
)
def test_check_constraints_reject_bad_values(db: Session, factory) -> None:  # type: ignore[no-untyped-def]
    user = make_user(db)
    expect_rejected(db, factory(user.id), error=IntegrityError)


def test_enum_values_are_validated(db: Session) -> None:
    user = make_user(db)
    expect_rejected(
        db,
        Consent(user_id=user.id, type="not_a_type"),  # type: ignore[arg-type]
        error=sa.exc.StatementError,
    )
