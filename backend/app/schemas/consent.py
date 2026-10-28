import datetime as dt

from pydantic import BaseModel, Field

from app.models.enums import ConsentType


class ConsentStatus(BaseModel):
    type: ConsentType
    granted: bool
    granted_at: dt.datetime | None
    revoked_at: dt.datetime | None


class ConsentsResponse(BaseModel):
    consents: list[ConsentStatus]


class ConsentsUpdate(BaseModel):
    """Partial update: only the listed types change. true = grant, false = revoke."""

    consents: dict[ConsentType, bool] = Field(min_length=1)
