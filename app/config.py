from __future__ import annotations

from functools import lru_cache
from ipaddress import IPv4Network, IPv6Network, ip_network
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed application configuration."""

    database_url: str = "sqlite:///./network_monitor.db"
    monitor_mode: Literal["mock", "icmp"] = "mock"
    monitor_interval_seconds: int = Field(default=60, ge=5, le=86400)
    alarm_threshold: int = Field(default=3, ge=1, le=100)
    mock_demo: bool = False
    csv_max_rows: int = Field(default=50000, ge=1, le=50000)
    ping_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    allowed_target_cidrs: str = "127.0.0.1/32,::1/128"
    max_concurrent_checks: int = Field(default=5, ge=1, le=50)
    app_base_url: str = "http://127.0.0.1:8000"
    session_absolute_hours: int = Field(default=8, ge=1, le=168)
    session_idle_minutes: int = Field(default=30, ge=1, le=1440)
    login_session_minutes: int = Field(default=10, ge=1, le=60)
    login_max_attempts: int = Field(default=5, ge=1, le=20)
    login_window_seconds: int = Field(default=300, ge=10, le=3600)
    login_lock_seconds: int = Field(default=300, ge=10, le=3600)
    session_cookie_name: str = "avit_session"
    csrf_cookie_name: str = "avit_csrf"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator(
        "database_url",
        "allowed_target_cidrs",
        "app_base_url",
        "session_cookie_name",
        "csrf_cookie_name",
    )
    @classmethod
    def value_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("değer boş olamaz")
        return value

    @model_validator(mode="after")
    def validate_application_origin(self) -> Settings:
        if self.monitor_mode != "mock" and self.monitor_interval_seconds < 60:
            raise ValueError("ICMP kontrol aralığı en az 60 saniye olmalı")
        if self.mock_demo and self.monitor_mode != "mock":
            raise ValueError("MOCK_DEMO yalnızca mock modunda kullanılabilir")
        parsed = urlsplit(self.app_base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("APP_BASE_URL geçerli bir http/https adresi olmalı")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("APP_BASE_URL yalnızca origin içermeli")
        local_hosts = {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme == "http" and parsed.hostname not in local_hosts:
            raise ValueError("HTTP yalnızca yerel geliştirme adreslerinde kullanılabilir")
        return self

    @property
    def allowed_networks(self) -> tuple[IPv4Network | IPv6Network, ...]:
        entries = (item.strip() for item in self.allowed_target_cidrs.split(","))
        return tuple(ip_network(item, strict=False) for item in entries if item)

    @property
    def app_origin(self) -> str:
        parsed = urlsplit(self.app_base_url)
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")

    @property
    def cookie_secure(self) -> bool:
        return urlsplit(self.app_base_url).scheme == "https"


@lru_cache
def get_settings() -> Settings:
    return Settings()
