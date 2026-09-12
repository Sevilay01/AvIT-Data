from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog

FORBIDDEN_METADATA_TERMS = (
    "password",
    "parola",
    "hash",
    "session",
    "oturum",
    "csrf",
    "cookie",
    "token",
    "body",
    "gövde",
)


def safe_metadata(metadata: dict[str, Any] | None) -> str | None:
    if not metadata:
        return None
    for key in metadata:
        lowered = key.casefold()
        if any(term in lowered for term in FORBIDDEN_METADATA_TERMS):
            raise ValueError("Denetim metadata alanında gizli veri anahtarı kullanılamaz.")
    serialized = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if len(serialized) > 1000:
        raise ValueError("Denetim metadata alanı 1000 karakteri aşamaz.")
    return serialized


def add_audit_log(
    db: Session,
    *,
    now: datetime,
    source: str,
    action: str,
    outcome: str,
    actor_user_id: int | None = None,
    actor_username: str | None = None,
    target_type: str | None = None,
    target_id: str | int | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    log = AuditLog(
        occurred_at=now,
        actor_user_id=actor_user_id,
        actor_username=actor_username,
        source=source,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        outcome=outcome,
        metadata_json=safe_metadata(metadata),
    )
    db.add(log)
    return log
