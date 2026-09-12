from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import User
from app.services.audit import add_audit_log
from app.services.security import (
    hash_password,
    normalize_username,
    revoke_user_sessions,
)


class UserManagementError(ValueError):
    pass


def create_user(
    db: Session,
    *,
    username: str,
    password: str,
    role: str,
    now: datetime,
) -> User:
    normalized_username = normalize_username(username)
    if role not in {"admin", "viewer"}:
        raise UserManagementError("Rol admin veya viewer olmalı.")
    if db.scalar(select(User.id).where(User.username == normalized_username)):
        raise UserManagementError("Bu kullanıcı adı zaten kayıtlı.")
    user = User(
        username=normalized_username,
        password_hash=hash_password(password),
        role=role,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    try:
        db.flush()
        add_audit_log(
            db,
            now=now,
            source="cli",
            action="user.create",
            outcome="success",
            actor_username="cli",
            target_type="user",
            target_id=user.id,
            metadata={"role": role, "username": normalized_username},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise UserManagementError("Bu kullanıcı adı zaten kayıtlı.") from exc
    db.refresh(user)
    return user


def reset_user_password(
    db: Session,
    *,
    username: str,
    password: str,
    now: datetime,
) -> User:
    normalized_username = normalize_username(username)
    user = db.scalar(select(User).where(User.username == normalized_username))
    if user is None:
        raise UserManagementError("Kullanıcı bulunamadı.")
    user.password_hash = hash_password(password)
    user.updated_at = now
    revoke_user_sessions(db, user.id, now)
    add_audit_log(
        db,
        now=now,
        source="cli",
        action="user.password_reset",
        outcome="success",
        actor_username="cli",
        target_type="user",
        target_id=user.id,
        metadata={"username": user.username},
    )
    db.commit()
    db.refresh(user)
    return user


def deactivate_user(db: Session, *, username: str, now: datetime) -> User:
    normalized_username = normalize_username(username)
    user = db.scalar(select(User).where(User.username == normalized_username))
    if user is None:
        raise UserManagementError("Kullanıcı bulunamadı.")
    if not user.is_active:
        raise UserManagementError("Kullanıcı zaten pasif.")
    if user.role == "admin":
        active_admins = db.scalar(
            select(func.count()).select_from(User).where(User.role == "admin", User.is_active)
        ) or 0
        if active_admins <= 1:
            add_audit_log(
                db,
                now=now,
                source="cli",
                action="user.deactivate",
                outcome="denied",
                actor_username="cli",
                target_type="user",
                target_id=user.id,
                metadata={"reason": "last_active_admin", "username": user.username},
            )
            db.commit()
            raise UserManagementError("Son aktif yönetici pasife alınamaz.")
    user.is_active = False
    user.updated_at = now
    revoke_user_sessions(db, user.id, now)
    add_audit_log(
        db,
        now=now,
        source="cli",
        action="user.deactivate",
        outcome="success",
        actor_username="cli",
        target_type="user",
        target_id=user.id,
        metadata={"username": user.username},
    )
    db.commit()
    db.refresh(user)
    return user
