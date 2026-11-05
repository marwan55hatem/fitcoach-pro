"""Red flags in what the user writes, checked BEFORE any model call (pure).

English, Egyptian Arabic and Franco-Arabic (Arabic written in Latin letters and digits).
A match short-circuits the coach: the user gets a fixed, safe reply in their language
and the conversation stays in that category's mode (no numbers, see a professional, or
crisis guidance). Patterns favour catching too much over missing a real emergency.
"""

import enum
import re
from dataclasses import dataclass
from typing import Final

_DIACRITICS = re.compile("[ً-ْٰـ]")  # harakat, dagger alef, tatweel
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_LETTERS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه"})


class Category(enum.StrEnum):
    SELF_HARM = "self_harm"
    CHEST_PAIN = "chest_pain"
    FAINTING = "fainting"
    EATING_DISORDER = "eating_disorder"
    PREGNANCY = "pregnancy"
    MINOR = "minor"
    NUMBNESS = "numbness"
    SEVERE_PAIN = "severe_pain"
    DOPING = "doping"
    MEDICATION = "medication"


class CoachMode(enum.StrEnum):
    CRISIS = "crisis"  # urgent help first, no coaching on this
    NO_NUMBERS = "no_numbers"  # no calories, weights or targets on this topic
    REFER = "refer"  # stop advising on it; a doctor or professional decides


@dataclass(frozen=True)
class RedFlag:
    category: Category
    mode: CoachMode


def normalize(text: str) -> str:
    """Lower case, Western digits, one Arabic spelling per letter, single spaces."""
    text = _DIACRITICS.sub("", text.lower().translate(_ARABIC_DIGITS))
    return " ".join(text.translate(_LETTERS).split())


def _any(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in patterns))


_BODY_PART_AR = "(ركبتي|كتفي|ضهري|ظهري|كوعي|رسغي|رقبتي|وسطي|ركبي)"
_HURTS_AR = "(بتوجعني|بيوجعني|واجعاني|واجعني|بتوجعنى)"

# In priority order: the first match wins, the most urgent first.
_PATTERNS: Final[list[tuple[Category, re.Pattern[str]]]] = [
    (
        Category.SELF_HARM,
        _any(
            r"\b(kill|hurt|harm|cut)(ing)? myself\b",
            r"\bsuicid",
            r"\bwant(ed)? to die\b",
            r"\b(end|ending) (my|it all|my own) life\b",
            r"\bself[- ]?harm",
            r"\b3a?y(e|i)?za? a?moo?t\b",
            r"\b(nefsi|nifsi) a?moo?t\b",
            r"\b(ante|enta|anta)(7|h)a?r",
            "عايزه? اموت",
            "نفسي اموت",
            "انتحار",
            "انتحر",
            "اموت نفسي",
            "(اذي|اوذي|ااذي) نفسي",
        ),
    ),
    (
        Category.CHEST_PAIN,
        _any(
            r"\bchest (pain|pains|hurts?|aches?|tight(ness)?|pressure)\b",
            r"\b(pain|tightness|pressure) in (my|the) chest\b",
            r"\bchest (feels|is|was|gets|got) (tight|heavy|painful)",
            r"\bsadr[iy]? \w*w[ae]?g[ae]?3",  # byewga3ni, wage3ni, bewga3
            r"\b(alam|wag3|w[ae]ga3) (fe|f|fi)l? ?sadr",
            "صدري (بيوجعني|واجعني|بيوجعنى|تاقل|مكتوم)",
            "(الم|وجع|ضيق|كتمه) في (الصدر|صدري)",
        ),
    ),
    (
        Category.FAINTING,
        _any(
            r"\b(fainted|faint|fainting|passed out|pass out|passing out|blacked out|black out)\b",
            r"\bdizz(y|iness)\b",
            r"\blight-?headed",
            r"\bd[ao]w?kha\b",
            r"\ba?gh?ma (3al|3l|3la)",
            "دوخه",
            "دايخ",
            "اغمي علي",
            "مغمي",
        ),
    ),
    (
        Category.EATING_DISORDER,
        _any(
            r"\b(make|making|made) myself (throw up|vomit|sick|puke)\b",
            r"\b(throw|throwing) up after (eating|meals?|food)\b",
            r"\bpurg(e|ing)\b",
            r"\bstarv(e|ing) myself\b",
            r"\bhaven'?t eaten (in|for) \d+ days\b",
            r"\bnot eat(ing)? (for|in) (days|\d+ days)\b",
            r"\blaxatives?\b",
            r"\bbing(e|ing)\b",
            "(بطرش|برجع|بستفرغ|بتقيا|بتقيأ).{0,15}(الاكل|ما اكل|ماكل)",
            "(بجوع|مجوع) نفسي",
            "مش باكل (خالص|بالايام)",
            "ملينات",
        ),
    ),
    (
        Category.PREGNANCY,
        _any(
            r"\bpregnan(t|cy)\b",
            r"\bexpecting a baby\b",
            r"\bpostpartum\b",
            r"\bjust gave birth\b",
            r"\b7am(el|la|l)\b",
            "حامل",
            "بعد الولاده",
        ),
    ),
    (
        Category.MINOR,
        _any(
            r"\b(i'?m|i am|im)\s+(1[0-7]|[5-9])(\s*(years?|yrs?)( old)?|\s*yo)?\b(?!\s*(kg|kilo|lbs?|reps?|sets?|min|%))",
            r"\b(1[0-7]) (years? old|yo)\b",
            r"\b3andi (1[0-7]) (sana|sanin|sneen|snin)\b",
            "عندي (1[0-7]) سن",
            "(سني|عمري) (1[0-7])\\b",
        ),
    ),
    (
        Category.NUMBNESS,
        _any(
            r"\bnumb(ness)?\b",
            r"\btingl(e|es|ing)\b",
            r"\bpins and needles\b",
            r"\btanmee?l\b",
            r"\bmet?namel",
            "تنميل",
            "منمله?",
        ),
    ),
    (
        Category.SEVERE_PAIN,
        _any(
            r"\bsharp pain\b",
            r"\bsevere pain\b",
            r"\b(heard|felt) a (pop|snap|crack)\b",
            r"\b(hurts|pain|aches?) (a lot|really bad|so bad|badly)\b",
            r"\bcan'?t (move|bend|straighten|lift) (my|it)\b",
            r"\bwag3 (shedeed|gamed)\b",
            f"{_BODY_PART_AR} {_HURTS_AR} (جامد|اوي|جدا)",
            "(الم|وجع) شديد",
        ),
    ),
    (
        Category.DOPING,
        _any(
            r"\b(steroids?|anabolics?|trenbolone|tren|dianabol|dbol|winstrol|anavar|"
            r"clenbuterol|clen|sarms?|hgh|growth hormone|deca|peds?)\b",
            r"\btest(osterone)? (cycle|injections?|shots?|booster)\b",
            r"\b(7o2an|ho2an|hormon(at|es)?|monashet(at)?|mnshtat)\b",
            "منشطات",
            "هرمونات",
            "حقن",
            "ستيرويد",
            "كلين",
        ),
    ),
    (
        Category.MEDICATION,
        _any(
            r"\b(medication|medications|medicine|meds|pills?|ibuprofen|paracetamol|"
            r"panadol|brufen|cataflam|voltaren|aspirin|painkillers?|antibiotics?|insulin|"
            r"metformin|antidepressants?|prescription)\b",
            r"\b(dawa|mosaken|mosakken|mesakken)\b",
            "(^| )(دوا|دواء|علاج)( |$)",
            "مسكن",
            "حبوب",
            "بنادول",
            "كتافلام",
            "بروفين",
            "فولتارين",
        ),
    ),
]

_MODES: Final = {
    Category.SELF_HARM: CoachMode.CRISIS,
    Category.EATING_DISORDER: CoachMode.NO_NUMBERS,
}

_REPLIES: Final[dict[Category, dict[str, str]]] = {
    Category.SELF_HARM: {
        "en": (
            "I'm really glad you told me, and I'm sorry you're going through this. You "
            "deserve support from a person right now. If you might act on these "
            "thoughts, call 123 (ambulance in Egypt) or go to the nearest emergency "
            "room now. You can also find a free, confidential helpline at "
            "findahelpline.com, or talk to someone you trust today. Training can wait."
        ),
        "ar": (
            "أنا مبسوط إنك قلتلي، وآسف إنك حاسس كده. انت محتاج دعم من حد بجد دلوقتي. لو "
            "ممكن تأذي نفسك، كلّم 123 (الإسعاف) أو روح أقرب طوارئ فورًا. وتقدر تلاقي خط "
            "دعم مجاني وسري على findahelpline.com، أو كلّم حد بتثق فيه النهارده. التمرين "
            "يستنى."
        ),
    },
    Category.CHEST_PAIN: {
        "en": (
            "Chest pain or tightness during exercise needs a doctor's check before you "
            "train again. If it's happening now, or comes with shortness of breath, "
            "sweating or pain spreading to your arm or jaw, call 123 (ambulance in "
            "Egypt) right away. I won't give training advice on this until a doctor "
            "has cleared you."
        ),
        "ar": (
            "الألم أو الضيق في الصدر وقت التمرين محتاج كشف عند دكتور قبل ما تتمرن تاني. لو "
            "بيحصل دلوقتي، أو معاه نهجان أو عرق أو وجع رايح للدراع أو الفك، كلّم 123 "
            "(الإسعاف) فورًا. مش هديك نصايح تمرين في ده غير لما الدكتور يسمحلك."
        ),
    },
    Category.FAINTING: {
        "en": (
            "Fainting or dizziness during or after training is a reason to stop and see "
            "a doctor before your next session. Sit or lie down, drink water, and don't "
            "train alone until you've been checked. If you lose consciousness again or "
            "feel chest pain, call 123."
        ),
        "ar": (
            "الإغماء أو الدوخة وقت التمرين أو بعده سبب إنك توقف وتكشف عند دكتور قبل التمرين "
            "الجاي. اقعد أو نام واشرب مية، وماتتمرنش لوحدك لحد ما تكشف. لو أغمى عليك تاني أو "
            "حسيت بوجع في صدرك، كلّم 123."
        ),
    },
    Category.EATING_DISORDER: {
        "en": (
            "Thank you for telling me. What you describe can really hurt your health, "
            "and you deserve proper support with it. Please talk to a doctor or a "
            "dietitian who works with eating concerns. I'll keep helping with training, "
            "but I won't give calorie or diet numbers on this."
        ),
        "ar": (
            "شكرًا إنك قلتلي. اللي بتوصفه ممكن يأذي صحتك بجد، وانت تستاهل دعم حقيقي فيه. "
            "ياريت تتكلم مع دكتور أو أخصائي تغذية بيتعامل مع مشاكل الأكل. هكمل معاك في "
            "التمرين، بس مش هديك أرقام سعرات أو دايت في الموضوع ده."
        ),
    },
    Category.PREGNANCY: {
        "en": (
            "Congratulations! Training during and after pregnancy is often possible, "
            "but what is safe depends on you and your pregnancy, so your doctor or "
            "midwife should guide it. Please check with them before training or "
            "changing your diet. I won't give training or calorie advice on this."
        ),
        "ar": (
            "مبروك! التمرين في الحمل وبعده غالبًا ممكن، بس الآمن بيختلف من حالة لحالة، "
            "فدكتورتك هي اللي تحدده. اسألها قبل ما تتمرني أو تغيّري أكلك. مش هديكي نصايح "
            "تمرين أو سعرات في ده."
        ),
    },
    Category.MINOR: {
        "en": (
            "FitCoach is made for adults 18 and over. Teenagers are still growing, so "
            "training and nutrition should be planned with a qualified coach and a "
            "parent or guardian involved. Please talk to them before following any plan."
        ),
        "ar": (
            "FitCoach معمول للي عندهم 18 سنة أو أكتر. اللي أصغر لسه بيكبروا، فالتمرين والأكل "
            "لازم يتخططوا مع كوتش متخصص ومع حد من الأهل. اتكلم معاهم قبل ما تمشي على أي "
            "برنامج."
        ),
    },
    Category.NUMBNESS: {
        "en": (
            "Numbness or tingling can mean a nerve is being pinched or irritated. Stop "
            "the exercise that brings it on and have it checked by a doctor or "
            "physiotherapist before loading it again. If it comes suddenly on one side "
            "of the body, call 123."
        ),
        "ar": (
            "التنميل ممكن يكون معناه إن فيه عصب متضغوط أو متهيّج. وقّف التمرين اللي بيعمله "
            "واكشف عند دكتور أو أخصائي علاج طبيعي قبل ما تشيل عليه تاني. لو جه فجأة في "
            "ناحية واحدة من الجسم، كلّم 123."
        ),
    },
    Category.SEVERE_PAIN: {
        "en": (
            "Sharp or severe pain, or a pop, is a sign to stop that exercise now. "
            "Don't train through it: have it checked by a doctor or physiotherapist "
            "first. Until then I'll leave that area alone; tell me if you want to "
            "train something that doesn't hurt."
        ),
        "ar": (
            "الوجع الجامد أو الحاد، أو لو سمعت طقة، معناه توقف التمرين ده فورًا. ماتكملش "
            "عليه: اكشف عند دكتور أو أخصائي علاج طبيعي الأول. لحد ساعتها هسيب المنطقة دي، "
            "وقولي لو عايز تتمرن حاجة مش بتوجعك."
        ),
    },
    Category.DOPING: {
        "en": (
            "I don't give advice on steroids, hormones or other banned substances. They "
            "carry real risks for your heart, liver and hormones. If you're thinking "
            "about them, please talk to a doctor first. I'm happy to help you progress "
            "with training, food and sleep."
        ),
        "ar": (
            "مش بدّي نصايح عن المنشطات أو الهرمونات أو أي حاجة ممنوعة. ليها مخاطر حقيقية على "
            "القلب والكبد والهرمونات. لو بتفكر فيها، اتكلم مع دكتور الأول. ومبسوط أساعدك "
            "تتقدم بالتمرين والأكل والنوم."
        ),
    },
    Category.MEDICATION: {
        "en": (
            "I can't advise on medication: whether to take, stop or change a medicine "
            "is for your doctor or pharmacist. Please ask them, and tell them about "
            "your training too."
        ),
        "ar": (
            "مقدرش أنصحك في الأدوية: تاخد دوا أو توقفه أو تغيّره ده قرار دكتورك أو "
            "الصيدلي. اسألهم، وقولهم كمان إنك بتتمرن."
        ),
    },
}


def detect(text: str) -> RedFlag | None:
    """The most urgent red flag in the text, or None."""
    clean = normalize(text)
    if not clean:
        return None
    for category, pattern in _PATTERNS:
        if pattern.search(clean):
            return RedFlag(category, _MODES.get(category, CoachMode.REFER))
    return None


def safe_reply(category: Category, language: str) -> str:
    replies = _REPLIES[category]
    return replies.get(language, replies["en"])
