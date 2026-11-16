from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Plain pattern check: EmailStr rejects reserved TLDs such as `.local` used by the demo accounts.
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
RoleName = Literal["admin", "data_engineer", "analyst"]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LoginIn(BaseModel):
    email: str = Field(pattern=EMAIL_PATTERN, max_length=255)
    password: str = Field(min_length=1, max_length=256)


class UserOut(ORM):
    id: str
    email: str
    full_name: str
    role: str
    is_active: bool
    last_login_at: datetime | None = None
    created_at: datetime


class MeOut(BaseModel):
    user: UserOut
    role_label: str
    permissions: list[str]


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    me: MeOut


class UserCreate(BaseModel):
    email: str = Field(pattern=EMAIL_PATTERN, max_length=255)
    full_name: str = Field(min_length=1, max_length=120)
    role: RoleName
    password: str = Field(min_length=8, max_length=256)


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    role: RoleName | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=256)


class AuditEventOut(ORM):
    id: str
    actor_email: str | None
    action: str
    resource_type: str
    resource_id: str | None
    details: dict
    correlation_id: str | None
    created_at: datetime


class Page(BaseModel):
    items: list
    total: int
    limit: int
    offset: int
