import pytest

from app.models.enums import BodyArea, Equipment, Experience, Mechanic, Severity
from app.rules.exercise_catalog import (
    MUSCLE_MAP,
    MUSCLES,
    CatalogExercise,
    is_available,
    is_substitute,
    normalize_equipment,
    normalize_level,
    normalize_mechanic,
    normalize_muscle,
    rank_substitutes,
    required_equipment,
    slugify,
)
from app.rules.limitations import (
    AREA_RULES,
    ExerciseFacts,
    LimitationFacts,
    exclusions_for,
    is_excluded,
    limitations_from_areas,
)

E = Equipment

# --- normalisation ----------------------------------------------------------------------


def test_every_dataset_muscle_maps_into_the_vocabulary() -> None:
    assert len(MUSCLE_MAP) == 17
    for raw, canonical in MUSCLE_MAP.items():
        assert normalize_muscle(raw) == canonical in MUSCLES
    assert normalize_muscle("  Quadriceps ") == "quads"
    assert normalize_muscle("abdominals") == "abs"
    assert normalize_muscle("LOWER BACK") == "lower_back"


def test_unknown_muscle_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown muscle"):
        normalize_muscle("gluteus maximus")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("body only", E.BODY_WEIGHT),
        (None, E.BODY_WEIGHT),
        ("dumbbell", E.DUMBBELL),
        ("barbell", E.BARBELL),
        ("e-z curl bar", E.EZ_BAR),
        ("cable", E.CABLE),
        ("machine", E.MACHINE),
        ("kettlebells", E.KETTLEBELL),
        ("bands", E.BANDS),
        ("medicine ball", E.OTHER),
        ("exercise ball", E.OTHER),
        ("foam roll", E.OTHER),
        ("other", E.OTHER),
        (" Barbell ", E.BARBELL),
    ],
)
def test_normalize_equipment(raw: str | None, expected: Equipment) -> None:
    assert normalize_equipment(raw) is expected


def test_unknown_equipment_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown equipment"):
        normalize_equipment("jetpack")


def test_level_and_mechanic() -> None:
    assert normalize_level("expert") is Experience.ADVANCED
    assert normalize_level("Beginner") is Experience.BEGINNER
    assert normalize_level(None) is None
    assert normalize_mechanic("compound") is Mechanic.COMPOUND
    assert normalize_mechanic(None) is None
    with pytest.raises(ValueError, match="unknown level"):
        normalize_level("godlike")
    with pytest.raises(ValueError, match="unknown mechanic"):
        normalize_mechanic("magic")


@pytest.mark.parametrize(
    ("text", "slug"),
    [
        ("3_4_Sit-Up", "3-4-sit-up"),
        ("Barbell Bench Press - Medium Grip", "barbell-bench-press-medium-grip"),
        ("Café Curl", "cafe-curl"),
        ("  Wide-Grip  Lat Pulldown ", "wide-grip-lat-pulldown"),
        ("!!!", ""),
    ],
)
def test_slugify(text: str, slug: str) -> None:
    assert slugify(text) == slug


# --- equipment --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "equipment", "expected"),
    [
        ("Incline Dumbbell Press", E.DUMBBELL, {E.DUMBBELL, E.BENCH}),
        ("Barbell Bench Press - Medium Grip", E.BARBELL, {E.BARBELL, E.BENCH}),
        ("EZ-Bar Skullcrusher", E.EZ_BAR, {E.EZ_BAR, E.BENCH}),
        ("Preacher Curl", E.EZ_BAR, {E.EZ_BAR, E.BENCH}),
        ("Dumbbell Lunges", E.DUMBBELL, {E.DUMBBELL}),
        ("Barbell Curl", E.BARBELL, {E.BARBELL}),
        ("Triceps Pushdown", E.CABLE, {E.CABLE}),
        (
            "Lying Leg Curls",
            E.MACHINE,
            {E.MACHINE},
        ),  # "lying" only implies a bench for free weights
        ("Wide-Grip Lat Pulldown", E.CABLE, {E.CABLE}),
        ("Pullups", E.BODY_WEIGHT, {E.PULL_UP_BAR}),
        ("Chin-Up", E.BODY_WEIGHT, {E.PULL_UP_BAR}),
        ("Hanging Leg Raise", E.BODY_WEIGHT, {E.PULL_UP_BAR}),
        ("Weighted Pull Ups", E.OTHER, {E.PULL_UP_BAR}),
        ("Bench Dips", E.BODY_WEIGHT, {E.BENCH}),
        ("Pushups", E.BODY_WEIGHT, {E.BODY_WEIGHT}),
    ],
)
def test_required_equipment(
    name: str, equipment: Equipment, expected: set[Equipment]
) -> None:
    assert required_equipment(name, equipment) == frozenset(expected)


def test_is_available() -> None:
    assert is_available([E.BODY_WEIGHT], [])  # bodyweight is always possible
    assert is_available([], [])
    assert is_available([E.DUMBBELL, E.BENCH], [E.DUMBBELL, E.BENCH, E.CABLE])
    assert not is_available([E.DUMBBELL, E.BENCH], [E.DUMBBELL])  # missing the bench
    assert not is_available([E.OTHER], [E.DUMBBELL])  # exotic gear must be owned
    assert is_available([E.OTHER], [E.OTHER])
    assert is_available(["dumbbell"], ["dumbbell"])  # plain strings are accepted
    with pytest.raises(ValueError, match="jetpack"):
        is_available(["jetpack"], [])


# --- substitutes ------------------------------------------------------------------------


def ex(
    name: str,
    equipment: Equipment,
    primary: str = "chest",
    secondary: tuple[str, ...] = (),
    mechanic: Mechanic | None = Mechanic.COMPOUND,
    level: Experience | None = Experience.BEGINNER,
    category: str | None = "strength",
) -> CatalogExercise:
    return CatalogExercise(
        id=name.lower().replace(" ", "-"),
        name=name,
        muscle_primary=primary,
        muscles_secondary=secondary,
        equipment=equipment,
        level=level,
        mechanic=mechanic,
        category=category,
    )


BENCH = ex("Barbell Bench Press", E.BARBELL, secondary=("shoulders", "triceps"))
POOL = [
    BENCH,
    ex("Incline Dumbbell Press", E.DUMBBELL, secondary=("shoulders", "triceps")),
    ex(
        "Dumbbell Flyes",
        E.DUMBBELL,
        secondary=("shoulders",),
        mechanic=Mechanic.ISOLATION,
    ),
    ex("Cable Crossover", E.CABLE, mechanic=Mechanic.ISOLATION),
    ex("Pushups", E.BODY_WEIGHT, secondary=("shoulders", "triceps")),
    ex("Squat", E.BARBELL, primary="quads"),
    ex("Chest Stretch", E.BODY_WEIGHT, mechanic=None, category="stretching"),
]


def names(result: list[CatalogExercise]) -> list[str]:
    return [r.name for r in result]


def test_substitutes_same_muscle_equipment_and_not_self() -> None:
    result = rank_substitutes(BENCH, POOL, user_equipment=[E.DUMBBELL, E.BENCH])
    assert names(result) == ["Incline Dumbbell Press", "Pushups", "Dumbbell Flyes"]
    assert BENCH not in result  # never itself
    assert "Squat" not in names(result)  # different muscle
    assert "Cable Crossover" not in names(result)  # no cable
    assert "Chest Stretch" not in names(result)  # a stretch is not a strength swap


def test_substitutes_need_every_piece_of_equipment() -> None:
    # Incline dumbbell press needs a bench as well as dumbbells.
    result = rank_substitutes(BENCH, POOL, user_equipment=[E.DUMBBELL])
    assert "Incline Dumbbell Press" not in names(result)
    assert names(result) == ["Pushups", "Dumbbell Flyes"]


def test_substitutes_bodyweight_only() -> None:
    assert names(rank_substitutes(BENCH, POOL, user_equipment=[])) == ["Pushups"]


def test_substitutes_respect_excluded_ids_and_limit() -> None:
    everything = [E.DUMBBELL, E.BENCH, E.CABLE]
    ranked = rank_substitutes(BENCH, POOL, user_equipment=everything)
    assert len(ranked) == 4
    result = rank_substitutes(
        BENCH, POOL, user_equipment=everything, excluded_ids=frozenset({"pushups"})
    )
    assert "Pushups" not in names(result)
    assert len(rank_substitutes(BENCH, POOL, user_equipment=everything, limit=2)) == 2


def test_substitutes_rank_similar_exercises_first_with_stable_ties() -> None:
    result = rank_substitutes(
        BENCH, POOL, user_equipment=[E.DUMBBELL, E.BENCH, E.CABLE]
    )
    # Compound + shared secondaries beat isolation moves; exact ties break alphabetically.
    # Scores: Incline 6, Pushups 6 (tie), Flyes 2 (shares "shoulders"), Crossover 1.
    assert names(result) == [
        "Incline Dumbbell Press",
        "Pushups",
        "Dumbbell Flyes",
        "Cable Crossover",
    ]


def test_is_substitute_checks_one_candidate() -> None:
    gym = [E.DUMBBELL, E.BENCH]
    incline, flyes, crossover, pushups, squat, stretch = POOL[1:]
    assert is_substitute(BENCH, incline, user_equipment=gym)
    assert is_substitute(BENCH, flyes, user_equipment=gym)
    assert not is_substitute(BENCH, BENCH, user_equipment=gym)  # never itself
    assert not is_substitute(BENCH, squat, user_equipment=[E.BARBELL])  # other muscle
    assert not is_substitute(BENCH, crossover, user_equipment=gym)  # no cable
    assert not is_substitute(BENCH, stretch, user_equipment=gym)  # not strength
    assert not is_substitute(
        BENCH, pushups, user_equipment=gym, excluded_ids=frozenset({"pushups"})
    )


def test_is_substitute_agrees_with_the_ranking() -> None:
    gym = [E.DUMBBELL, E.BENCH]
    ranked = rank_substitutes(BENCH, POOL, user_equipment=gym, limit=len(POOL))
    allowed = [c for c in POOL if is_substitute(BENCH, c, user_equipment=gym)]
    assert sorted(names(ranked)) == sorted(names(allowed))


def test_stretch_targets_only_get_stretch_substitutes() -> None:
    stretch = POOL[-1]
    other = ex("Doorway Stretch", E.BODY_WEIGHT, mechanic=None, category="stretching")
    assert names(rank_substitutes(stretch, [*POOL, other], user_equipment=[])) == [
        "Doorway Stretch"
    ]


# --- limitations ------------------------------------------------------------------------


def facts(
    name: str,
    primary: str,
    secondary: tuple[str, ...] = (),
    mechanic: Mechanic | None = Mechanic.COMPOUND,
) -> ExerciseFacts:
    return ExerciseFacts(name, primary, secondary, mechanic)


OHP = facts("Barbell Shoulder Press", "shoulders", ("triceps",))
MILITARY = facts("Standing Military Press", "shoulders", ("triceps",))
LATERAL = facts("Seated Side Lateral Raise", "shoulders", (), Mechanic.ISOLATION)
BENCH_PRESS = facts(
    "Barbell Bench Press - Medium Grip", "chest", ("shoulders", "triceps")
)
PUSHDOWN = facts("Triceps Pushdown", "triceps", (), Mechanic.ISOLATION)
SKULL = facts("EZ-Bar Skullcrusher", "triceps", (), Mechanic.ISOLATION)
LEG_PRESS = facts("Leg Press", "quads", ("glutes", "hamstrings"))
LEG_EXT = facts("Leg Extensions", "quads", (), Mechanic.ISOLATION)
LEG_CURL = facts("Lying Leg Curls", "hamstrings", (), Mechanic.ISOLATION)
RDL = facts("Romanian Deadlift", "hamstrings", ("glutes", "lower_back"))
SQUAT = facts("Barbell Full Squat", "quads", ("glutes", "hamstrings"))
CALF = facts("Standing Calf Raises", "calves", (), Mechanic.ISOLATION)
BOX_JUMP = facts("Box Jump (Multiple Response)", "quads", ("calves",), None)
LUNGE = facts("Dumbbell Lunges", "quads", ("glutes", "hamstrings"))
HIP_THRUST = facts("Barbell Hip Thrust", "glutes", ("hamstrings",))
PUSHUP = facts("Pushups", "chest", ("shoulders", "triceps"))
SHRUG = facts("Barbell Shrug", "traps", (), Mechanic.ISOLATION)
BENT_ROW = facts("Bent Over Barbell Row", "middle_back", ("lats", "biceps"))
CURL = facts("Barbell Curl", "biceps", (), Mechanic.ISOLATION)


@pytest.mark.parametrize(
    ("exercise", "area", "severity", "reason"),
    [
        # shoulder: patterns at every severity, primary compound from moderate, secondary at severe
        (MILITARY, BodyArea.SHOULDER, Severity.MILD, "movement"),
        (
            facts(
                "Standing Front Barbell Raise Over Head",
                "shoulders",
                (),
                Mechanic.ISOLATION,
            ),
            BodyArea.SHOULDER,
            Severity.MILD,
            "movement",
        ),
        (
            facts("Standing Barbell Press Behind Neck", "shoulders", ("triceps",)),
            BodyArea.SHOULDER,
            Severity.MILD,
            "movement",
        ),
        (
            facts(
                "Lying Close-Grip Barbell Triceps Extension Behind The Head",
                "triceps",
                (),
                Mechanic.ISOLATION,
            ),
            BodyArea.SHOULDER,
            Severity.MILD,
            "movement",
        ),
        (OHP, BodyArea.SHOULDER, Severity.MILD, None),
        (OHP, BodyArea.SHOULDER, Severity.MODERATE, "loaded_primary"),
        (LATERAL, BodyArea.SHOULDER, Severity.MODERATE, None),
        (LATERAL, BodyArea.SHOULDER, Severity.SEVERE, "loaded_primary"),
        (BENT_ROW, BodyArea.SHOULDER, Severity.SEVERE, None),
        (BENCH_PRESS, BodyArea.SHOULDER, Severity.MODERATE, None),
        (BENCH_PRESS, BodyArea.SHOULDER, Severity.SEVERE, "loaded_secondary"),
        # elbow
        (SKULL, BodyArea.ELBOW, Severity.MILD, "movement"),
        (PUSHDOWN, BodyArea.ELBOW, Severity.MILD, None),
        (
            PUSHDOWN,
            BodyArea.ELBOW,
            Severity.MODERATE,
            None,
        ),  # isolation, not a compound
        (PUSHDOWN, BodyArea.ELBOW, Severity.SEVERE, "loaded_primary"),
        (CURL, BodyArea.ELBOW, Severity.SEVERE, "loaded_primary"),
        # knee
        (LEG_EXT, BodyArea.KNEE, Severity.MILD, "movement"),
        (LEG_PRESS, BodyArea.KNEE, Severity.MILD, "movement"),
        (SQUAT, BodyArea.KNEE, Severity.MILD, "movement"),
        (BOX_JUMP, BodyArea.KNEE, Severity.MILD, "movement"),
        (LEG_CURL, BodyArea.KNEE, Severity.SEVERE, None),
        # lower back
        (RDL, BodyArea.LOWER_BACK, Severity.MILD, "movement"),
        (SQUAT, BodyArea.LOWER_BACK, Severity.MILD, "movement"),
        (BENT_ROW, BodyArea.LOWER_BACK, Severity.MILD, "movement"),
        (LEG_PRESS, BodyArea.LOWER_BACK, Severity.SEVERE, None),
        (LEG_CURL, BodyArea.LOWER_BACK, Severity.MILD, None),
        # hip
        (LUNGE, BodyArea.HIP, Severity.MILD, "movement"),
        (HIP_THRUST, BodyArea.HIP, Severity.MILD, None),
        (HIP_THRUST, BodyArea.HIP, Severity.MODERATE, "loaded_primary"),
        # ankle / foot
        (BOX_JUMP, BodyArea.ANKLE_FOOT, Severity.MILD, "movement"),
        (CALF, BodyArea.ANKLE_FOOT, Severity.MILD, None),
        (CALF, BodyArea.ANKLE_FOOT, Severity.MODERATE, None),
        (CALF, BodyArea.ANKLE_FOOT, Severity.SEVERE, "loaded_primary"),
        # wrist, neck, upper back
        (PUSHUP, BodyArea.WRIST_HAND, Severity.MILD, "movement"),
        (SHRUG, BodyArea.NECK, Severity.MILD, "movement"),
        (BENT_ROW, BodyArea.UPPER_BACK, Severity.MILD, "movement"),
        (BENT_ROW, BodyArea.UPPER_BACK, Severity.MODERATE, "movement"),
    ],
)
def test_exclusion_table(
    exercise: ExerciseFacts, area: BodyArea, severity: Severity, reason: str | None
) -> None:
    result = exclusions_for(exercise, [LimitationFacts(area, severity)])
    if reason is None:
        assert result == []
    else:
        assert [(e.area, e.reason) for e in result] == [(area, reason)]


def test_every_body_area_has_a_rule_and_other_never_excludes() -> None:
    assert set(AREA_RULES) == set(BodyArea)
    for exercise in [OHP, SQUAT, RDL, LEG_PRESS, PUSHUP, SKULL]:
        for severity in Severity:
            assert (
                exclusions_for(exercise, [LimitationFacts(BodyArea.OTHER, severity)])
                == []
            )


def test_no_limitations_excludes_nothing() -> None:
    for exercise in [OHP, SQUAT, RDL, BOX_JUMP]:
        assert exclusions_for(exercise, []) == []


def test_inactive_limitations_are_ignored() -> None:
    assert not is_excluded(
        MILITARY, [LimitationFacts(BodyArea.SHOULDER, Severity.SEVERE, False)]
    )


def test_multiple_limitations_are_combined() -> None:
    limitations = [
        LimitationFacts(BodyArea.KNEE, Severity.MILD),
        LimitationFacts(BodyArea.LOWER_BACK, Severity.MILD),
    ]
    result = exclusions_for(SQUAT, limitations)
    assert {e.area for e in result} == {BodyArea.KNEE, BodyArea.LOWER_BACK}
    assert exclusions_for(LEG_CURL, limitations) == []


def test_limitations_from_areas_dedupes_and_defaults_to_moderate() -> None:
    result = limitations_from_areas([BodyArea.KNEE, BodyArea.KNEE, BodyArea.HIP])
    assert [(item.body_area, item.severity, item.active) for item in result] == [
        (BodyArea.KNEE, Severity.MODERATE, True),
        (BodyArea.HIP, Severity.MODERATE, True),
    ]
    assert limitations_from_areas([]) == []
