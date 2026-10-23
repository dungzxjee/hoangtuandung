from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


def validate_strong_password(password: str) -> str:
    if not (
        any(character.islower() for character in password)
        and any(character.isupper() for character in password)
        and any(character.isdigit() for character in password)
    ):
        raise ValueError("Mật khẩu phải có chữ hoa, chữ thường và chữ số")
    return password


class LoginInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    remember_me: bool = False


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=12, max_length=128)
    role: str = Field(pattern="^(admin|employee)$")

    @field_validator("password")
    @classmethod
    def password_is_strong(cls, password: str) -> str:
        return validate_strong_password(password)


class UserRegister(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("password")
    @classmethod
    def password_is_strong(cls, password: str) -> str:
        return validate_strong_password(password)


class ForgotPasswordInput(BaseModel):
    email: EmailStr


class ResetPasswordInput(BaseModel):
    token: str = Field(min_length=32, max_length=128)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("password")
    @classmethod
    def password_is_strong(cls, password: str) -> str:
        return validate_strong_password(password)


class CustomerInput(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    company: str | None = Field(default=None, max_length=150)
    address: str | None = Field(default=None, max_length=255)
    notes: str | None = None


class LeadInput(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    source: str | None = Field(default=None, max_length=100)
    status: str = Field(default="new", pattern="^(new|contacted|qualified|converted|lost)$")
    notes: str | None = None


class InteractionInput(BaseModel):
    customer_id: int
    kind: str = Field(pattern="^(call|email|meeting|note)$")
    occurred_at: datetime | None = None
    notes: str = Field(min_length=1)


class TaskInput(BaseModel):
    customer_id: int | None = None
    title: str = Field(min_length=1, max_length=200)
    due_date: date | None = None
    priority: str = Field(default="normal", pattern="^(low|normal|high|urgent)$")
    status: str = Field(default="open", pattern="^(open|in_progress|done|cancelled)$")
    description: str | None = None


class AppointmentInput(BaseModel):
    customer_id: int
    title: str = Field(min_length=1, max_length=200)
    starts_at: datetime
    reminder_at: datetime | None = None
    location: str | None = Field(default=None, max_length=255)
    status: str = Field(default="scheduled", pattern="^(scheduled|completed|cancelled)$")
    notes: str | None = None


class RecordOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    created_at: datetime


class LoginOutput(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict[str, int | str]
