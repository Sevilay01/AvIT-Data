from __future__ import annotations

from datetime import datetime
from ipaddress import ip_address

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def normalize_ip(value: str) -> str:
    try:
        return str(ip_address(value.strip()))
    except ValueError as exc:
        raise ValueError("geçerli bir IPv4 veya IPv6 adresi girin") from exc


def clean_required_text(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("boş olamaz")
    return value


def clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class DeviceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    ip_address: str = Field(min_length=2, max_length=45)
    device_type: str | None = Field(default=None, max_length=50)
    location: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    is_active: bool = True

    _normalize_name = field_validator("name")(clean_required_text)
    _normalize_ip = field_validator("ip_address")(normalize_ip)
    _normalize_optional = field_validator(
        "device_type", "location", "description"
    )(clean_optional_text)


class DeviceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    ip_address: str | None = Field(default=None, min_length=2, max_length=45)
    device_type: str | None = Field(default=None, max_length=50)
    location: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        return clean_required_text(value) if value is not None else None

    @field_validator("ip_address")
    @classmethod
    def normalize_address(cls, value: str | None) -> str | None:
        return normalize_ip(value) if value is not None else None

    _normalize_optional = field_validator(
        "device_type", "location", "description"
    )(clean_optional_text)

    @model_validator(mode="after")
    def required_fields_cannot_be_null(self) -> DeviceUpdate:
        for field_name in ("name", "ip_address"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} null olamaz")
        return self


class DeviceRead(BaseModel):
    id: int
    name: str
    ip_address: str
    device_type: str | None
    location: str | None
    description: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    target_version: int

    model_config = ConfigDict(from_attributes=True)


class DevicePage(BaseModel):
    items: list[DeviceRead]
    total: int
    limit: int
    offset: int


class MonitoringResultRead(BaseModel):
    source: str
    target_version: int
    is_current: bool
    id: int
    device_id: int
    target_ip: str
    probe_mode: str
    outcome: str
    latency_ms: float | None
    checked_at: datetime
    error_message: str | None

    model_config = ConfigDict(from_attributes=True)


class MonitoringResultPage(BaseModel):
    items: list[MonitoringResultRead]
    total: int
    limit: int
    offset: int


class HealthResponse(BaseModel):
    status: str


class CurrentUserRead(BaseModel):
    id: int
    username: str
    role: str


class AuditLogRead(BaseModel):
    id: int
    occurred_at: datetime
    actor_user_id: int | None
    actor_username: str | None
    source: str
    action: str
    target_type: str | None
    target_id: str | None
    outcome: str
    metadata: dict[str, object] | None


class AuditLogPage(BaseModel):
    items: list[AuditLogRead]
    total: int
    limit: int
    offset: int
