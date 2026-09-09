"""
Schema Pydantic: bentuk data yang masuk (request) dan keluar (response) API.
"""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, HttpUrl, EmailStr, Field, model_validator


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)


class UserOut(BaseModel):
    id: int
    email: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class TargetCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    url: HttpUrl
    interval_seconds: int = Field(default=10, ge=3, le=3600)
    check_type: Literal["http", "content", "ping", "tcp"] = "http"
    tcp_port: int | None = Field(default=None, ge=1, le=65535)
    expected_content: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_check_type_params(self):
        if self.check_type == "tcp" and not self.tcp_port:
            raise ValueError("check_type 'tcp' membutuhkan tcp_port")
        if self.check_type == "content" and not self.expected_content:
            raise ValueError("check_type 'content' membutuhkan expected_content")
        return self


class TargetOut(BaseModel):
    id: int
    name: str
    url: str
    interval_seconds: int
    is_active: bool
    check_type: str
    tcp_port: int | None
    expected_content: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class CheckResultOut(BaseModel):
    id: int
    target_id: int
    checked_at: datetime
    status_code: int | None
    response_time_ms: float | None
    is_up: bool
    error_message: str | None
    response_size_bytes: int | None
    is_anomaly: bool
    anomaly_z_score: float | None

    class Config:
        from_attributes = True


class TargetSummary(BaseModel):
    """Ringkasan status terkini sebuah target, untuk ditampilkan di dashboard."""
    target: TargetOut
    latest_check: CheckResultOut | None
    uptime_percent_24h: float
    avg_response_time_ms: float | None
    trend: dict | None = None


class AlertRuleCreate(BaseModel):
    is_enabled: bool = True
    failure_threshold: int = Field(default=2, ge=1, le=20)
    cooldown_minutes: int = Field(default=30, ge=1, le=1440)
    notify_email: EmailStr | None = None


class AlertRuleOut(BaseModel):
    id: int
    target_id: int
    is_enabled: bool
    failure_threshold: int
    cooldown_minutes: int
    notify_email: str | None
    consecutive_failures: int
    last_alert_sent_at: datetime | None
    created_at: datetime

    class Config:
        from_attributes = True


class ChatQuestion(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)


class ChatAnswer(BaseModel):
    answer: str
