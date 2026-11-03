"""Import every model so Base.metadata is complete (Alembic autogenerate relies on this)."""

from app.models.auth import RefreshToken
from app.models.body import Checkin, InbodyScan, ProgressPhoto, WeightLog
from app.models.coach import CoachNote, Conversation, Message, Recommendation
from app.models.files import StoredFile
from app.models.notifications import NotificationChannel, NotificationLog, Reminder
from app.models.nutrition import MealLog, MealOption, NutritionSettings, WaterLog
from app.models.system import AiUsage, AuditLog
from app.models.training import (
    Exercise,
    Program,
    ProgramDay,
    ProgramExercise,
    SetLog,
    WorkoutSession,
)
from app.models.user import (
    Consent,
    DietaryPreference,
    HealthScreening,
    Limitation,
    Profile,
    User,
)

__all__ = [
    "AiUsage",
    "AuditLog",
    "Checkin",
    "CoachNote",
    "Consent",
    "Conversation",
    "DietaryPreference",
    "Exercise",
    "HealthScreening",
    "InbodyScan",
    "Limitation",
    "MealLog",
    "MealOption",
    "Message",
    "NotificationChannel",
    "NotificationLog",
    "NutritionSettings",
    "Profile",
    "Program",
    "ProgramDay",
    "ProgramExercise",
    "ProgressPhoto",
    "Recommendation",
    "RefreshToken",
    "Reminder",
    "SetLog",
    "StoredFile",
    "User",
    "WaterLog",
    "WeightLog",
    "WorkoutSession",
]
