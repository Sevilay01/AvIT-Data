from __future__ import annotations

import hashlib
import hmac
import math
import secrets
import threading
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta

from pwdlib import PasswordHash
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import UserSession

PASSWORD_HASHER = PasswordHash.recommended()
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash(secrets.token_urlsafe(32))


def normalize_username(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    if not 3 <= len(normalized) <= 64:
        raise ValueError("Kullanıcı adı 3-64 karakter olmalı.")
    if not all(
        character.isalnum()
        or unicodedata.category(character).startswith("M")
        or character in "._-"
        for character in normalized
    ):
        raise ValueError("Kullanıcı adı yalnızca harf, rakam, nokta, alt çizgi ve tire içerebilir.")
    return normalized


def validate_password(password: str) -> str:
    if not 15 <= len(password) <= 128:
        raise ValueError("Parola 15-128 karakter olmalı.")
    return password


def hash_password(password: str) -> str:
    password_hash = PASSWORD_HASHER.hash(validate_password(password))
    if not password_hash.startswith("$argon2id$"):
        raise RuntimeError("Parola Argon2id ile hashlenemedi.")
    return password_hash


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(password, password_hash)
    except (TypeError, ValueError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches(token: str, expected_hash: str) -> bool:
    return hmac.compare_digest(token_hash(token), expected_hash)


@dataclass(frozen=True, slots=True)
class SessionTokens:
    session_id: str
    csrf_token: str
    expires_at: datetime


def create_session(
    db: Session,
    *,
    user_id: int | None,
    now: datetime,
    lifetime: timedelta,
) -> tuple[UserSession, SessionTokens]:
    session_id = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    expires_at = now + lifetime
    session = UserSession(
        token_hash=token_hash(session_id),
        csrf_token_hash=token_hash(csrf_token),
        user_id=user_id,
        created_at=now,
        last_activity_at=now,
        expires_at=expires_at,
    )
    db.add(session)
    return session, SessionTokens(session_id, csrf_token, expires_at)


def resolve_session(
    db: Session,
    raw_session_id: str | None,
    *,
    now: datetime,
    settings: Settings,
    allow_preauth: bool = False,
    touch: bool = True,
) -> UserSession | None:
    if not raw_session_id:
        return None
    session = db.scalar(
        select(UserSession).where(UserSession.token_hash == token_hash(raw_session_id))
    )
    if session is None or session.revoked_at is not None:
        return None
    if session.expires_at <= now:
        session.revoked_at = now
        db.commit()
        return None
    if session.user_id is None:
        return session if allow_preauth else None
    if session.last_activity_at + timedelta(minutes=settings.session_idle_minutes) <= now:
        session.revoked_at = now
        db.commit()
        return None
    if session.user is None or not session.user.is_active:
        session.revoked_at = now
        db.commit()
        return None
    if touch:
        session.last_activity_at = now
        db.commit()
    return session


def revoke_user_sessions(db: Session, user_id: int, now: datetime) -> None:
    db.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )


class LoginRateLimiter:
    def __init__(self, settings: Settings) -> None:
        self.max_attempts = settings.login_max_attempts
        self.window_seconds = settings.login_window_seconds
        self.lock_seconds = settings.login_lock_seconds
        self._failures: dict[str, list[datetime]] = {}
        self._blocked_until: dict[str, datetime] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _keys(account: str, source: str) -> tuple[str, str]:
        return f"account:{account}", f"source:{source}"

    def _cleanup(self, now: datetime) -> None:
        cutoff = now - timedelta(seconds=self.window_seconds)
        for key, attempts in list(self._failures.items()):
            recent = [attempt for attempt in attempts if attempt > cutoff]
            if recent:
                self._failures[key] = recent
            else:
                self._failures.pop(key, None)
        for key, blocked_until in list(self._blocked_until.items()):
            if blocked_until <= now:
                self._blocked_until.pop(key, None)

    def retry_after(self, account: str, source: str, now: datetime) -> int | None:
        with self._lock:
            self._cleanup(now)
            waits = []
            for key in self._keys(account, source):
                blocked_until = self._blocked_until.get(key)
                if blocked_until is None:
                    continue
                if blocked_until <= now:
                    self._blocked_until.pop(key, None)
                    self._failures.pop(key, None)
                    continue
                waits.append(math.ceil((blocked_until - now).total_seconds()))
            return max(waits) if waits else None

    def record_failure(self, account: str, source: str, now: datetime) -> None:
        cutoff = now - timedelta(seconds=self.window_seconds)
        with self._lock:
            self._cleanup(now)
            for key in self._keys(account, source):
                recent = [attempt for attempt in self._failures.get(key, []) if attempt > cutoff]
                recent.append(now)
                self._failures[key] = recent
                if len(recent) >= self.max_attempts:
                    self._blocked_until[key] = now + timedelta(seconds=self.lock_seconds)

    def reset_account(self, account: str) -> None:
        with self._lock:
            key = f"account:{account}"
            self._failures.pop(key, None)
            self._blocked_until.pop(key, None)
