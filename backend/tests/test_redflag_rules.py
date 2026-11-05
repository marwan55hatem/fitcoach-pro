import pytest

from app.rules.redflags import (
    Category,
    CoachMode,
    detect,
    normalize,
    safe_reply,
)


@pytest.mark.parametrize(
    ("text", "category"),
    [
        # Chest symptoms
        ("I get chest pain when I do squats", Category.CHEST_PAIN),
        ("my chest feels tight and heavy during cardio", Category.CHEST_PAIN),
        ("sadri byewga3ni fel tamreen", Category.CHEST_PAIN),
        ("صدري بيوجعني وانا بتمرن", Category.CHEST_PAIN),
        ("عندي ألم في الصدر", Category.CHEST_PAIN),
        # Fainting and dizziness
        ("I fainted after deadlifts yesterday", Category.FAINTING),
        ("I almost passed out on the bench", Category.FAINTING),
        ("gatly dawkha gamda", Category.FAINTING),
        ("اغمى عليا في الجيم", Category.FAINTING),
        ("بيجيلي دوخة بعد التمرين", Category.FAINTING),
        # Severe or sharp joint pain
        ("sharp pain in my knee when I squat", Category.SEVERE_PAIN),
        ("I heard a pop in my shoulder and it hurts a lot", Category.SEVERE_PAIN),
        ("ركبتي بتوجعني جامد", Category.SEVERE_PAIN),
        # Numbness
        ("my arm goes numb during curls", Category.NUMBNESS),
        ("3andi tanmeel fe idi", Category.NUMBNESS),
        ("عندي تنميل في رجلي", Category.NUMBNESS),
        # Self-harm (checked before everything else)
        ("I want to kill myself", Category.SELF_HARM),
        ("sometimes I think about hurting myself", Category.SELF_HARM),
        ("3ayez amoot", Category.SELF_HARM),
        ("نفسي اموت", Category.SELF_HARM),
        ("بفكر في الانتحار", Category.SELF_HARM),
        # Eating-disorder cues
        ("I make myself throw up after eating", Category.EATING_DISORDER),
        ("I haven't eaten in 3 days to lose weight", Category.EATING_DISORDER),
        ("I binge and then starve myself", Category.EATING_DISORDER),
        ("بطرش بعد الأكل عشان مخنش", Category.EATING_DISORDER),
        # Pregnancy
        ("I'm pregnant, can I still train legs?", Category.PREGNANCY),
        ("ana 7amel", Category.PREGNANCY),
        ("انا حامل في الشهر الرابع", Category.PREGNANCY),
        # Medication questions
        ("can I take ibuprofen before training?", Category.MEDICATION),
        ("should I stop my blood pressure medication?", Category.MEDICATION),
        ("ينفع اخد مسكن قبل التمرين", Category.MEDICATION),
        # Minors
        ("I'm 15 years old, what should I lift?", Category.MINOR),
        ("ana 3andi 16 sana", Category.MINOR),
        ("انا عندي 14 سنة", Category.MINOR),
        # Steroids and banned drugs
        ("what steroid cycle should I run?", Category.DOPING),
        ("is clenbuterol good for cutting?", Category.DOPING),
        ("عايز اخد منشطات", Category.DOPING),
        ("7o2an hormones", Category.DOPING),
    ],
)
def test_detects(text: str, category: Category) -> None:
    flag = detect(text)
    assert flag is not None, text
    assert flag.category is category


@pytest.mark.parametrize(
    "text",
    [
        "How was my week?",
        "Why did my bench stall?",
        "my chest day was great, new PR on bench",
        "chest press or dumbbell press?",
        "I'm 25 and want to build muscle",
        "my legs are sore after squats",
        "can I swap my dinner?",
        "عايز اكبر عضلات صدري",
        "kill it at the gym today",
        "my 15 kg dumbbells feel light",
        "",
    ],
)
def test_ordinary_coaching_is_not_flagged(text: str) -> None:
    assert detect(text) is None, text


def test_self_harm_wins_over_other_flags() -> None:
    flag = detect("my chest hurts and I want to die")
    assert flag is not None and flag.category is Category.SELF_HARM


def test_modes() -> None:
    assert detect("I want to kill myself").mode is CoachMode.CRISIS  # type: ignore[union-attr]
    assert detect("I make myself throw up after eating").mode is CoachMode.NO_NUMBERS  # type: ignore[union-attr]
    assert detect("chest pain during squats").mode is CoachMode.REFER  # type: ignore[union-attr]


@pytest.mark.parametrize("category", list(Category))
def test_every_category_has_a_reply_in_both_languages(category: Category) -> None:
    for language in ("en", "ar"):
        reply = safe_reply(category, language)
        assert len(reply) > 40
    assert "123" in safe_reply(Category.SELF_HARM, "en")  # Egypt's ambulance number
    assert "123" in safe_reply(Category.CHEST_PAIN, "ar")


def test_unknown_language_falls_back_to_english() -> None:
    assert safe_reply(Category.PREGNANCY, "fr") == safe_reply(Category.PREGNANCY, "en")


def test_normalize() -> None:
    assert normalize("  أنا   حامِل ") == "انا حامل"
    assert normalize("I'M PREGNANT") == "i'm pregnant"
    assert normalize("إغماء ى ة") == "اغماء ي ه"
