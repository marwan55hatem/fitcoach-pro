import enum


class ConsentType(enum.StrEnum):
    HEALTH_DATA = "health_data"
    BODY_PHOTOS = "body_photos"
    AI_PROCESSING = "ai_processing"
    NOTIFICATIONS = "notifications"


class Sex(enum.StrEnum):
    MALE = "male"
    FEMALE = "female"


class Goal(enum.StrEnum):
    LEAN_BULK = "lean_bulk"
    MAINTAIN = "maintain"
    CUT = "cut"
    RECOMP = "recomp"


class Experience(enum.StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class ActivityLevel(enum.StrEnum):
    SEDENTARY = "sedentary"
    LIGHT = "light"
    MODERATE = "moderate"
    ACTIVE = "active"
    VERY_ACTIVE = "very_active"


class Equipment(enum.StrEnum):
    """Canonical equipment vocabulary shared by onboarding and the exercise library."""

    BODY_WEIGHT = "body_weight"
    DUMBBELL = "dumbbell"
    BARBELL = "barbell"
    EZ_BAR = "ez_bar"
    CABLE = "cable"
    MACHINE = "machine"
    KETTLEBELL = "kettlebell"
    BANDS = "bands"
    BENCH = "bench"
    PULL_UP_BAR = "pull_up_bar"
    OTHER = "other"


class BodyArea(enum.StrEnum):
    NECK = "neck"
    SHOULDER = "shoulder"
    ELBOW = "elbow"
    WRIST_HAND = "wrist_hand"
    UPPER_BACK = "upper_back"
    LOWER_BACK = "lower_back"
    HIP = "hip"
    KNEE = "knee"
    ANKLE_FOOT = "ankle_foot"
    OTHER = "other"


class Units(enum.StrEnum):
    METRIC = "metric"
    IMPERIAL = "imperial"


class RiskLevel(enum.StrEnum):
    OK = "ok"
    CAUTION = "caution"
    BLOCKED = "blocked"


class Severity(enum.StrEnum):
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"


class BudgetLevel(enum.StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FileKind(enum.StrEnum):
    INBODY = "inbody"
    BODY_PHOTO = "body_photo"
    FOOD = "food"
    CHAT = "chat"


class Mechanic(enum.StrEnum):
    COMPOUND = "compound"
    ISOLATION = "isolation"


class ProgramStatus(enum.StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class MealSlot(enum.StrEnum):
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"
    SNACK = "snack"
    PRE_WORKOUT = "pre_workout"
    POST_WORKOUT = "post_workout"
    BED = "bed"


class MealSource(enum.StrEnum):
    TEXT = "text"
    PHOTO = "photo"
    MANUAL = "manual"
    CATALOG = "catalog"


class PhotoView(enum.StrEnum):
    FRONT = "front"
    SIDE = "side"
    BACK = "back"


class MessageRole(enum.StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class RecommendationStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"  # superseded by a newer review, or left too long


class ChannelType(enum.StrEnum):
    TELEGRAM = "telegram"
    WEBPUSH = "webpush"


class NotificationStatus(enum.StrEnum):
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"
