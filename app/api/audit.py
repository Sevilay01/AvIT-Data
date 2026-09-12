from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.dependencies import AuthContext, get_db, require_admin
from app.models import AuditLog
from app.schemas import AuditLogPage, AuditLogRead

router = APIRouter(prefix="/api/audit-logs", tags=["denetim"])
DatabaseSession = Annotated[Session, Depends(get_db)]
Admin = Annotated[AuthContext, Depends(require_admin)]


def serialize_log(log: AuditLog) -> AuditLogRead:
    metadata = json.loads(log.metadata_json) if log.metadata_json else None
    return AuditLogRead(
        id=log.id,
        occurred_at=log.occurred_at,
        actor_user_id=log.actor_user_id,
        actor_username=log.actor_username,
        source=log.source,
        action=log.action,
        target_type=log.target_type,
        target_id=log.target_id,
        outcome=log.outcome,
        metadata=metadata,
    )


@router.get("", response_model=AuditLogPage)
def list_audit_logs(
    db: DatabaseSession,
    admin: Admin,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditLogPage:
    del admin
    total = db.scalar(select(func.count()).select_from(AuditLog)) or 0
    logs = db.scalars(
        select(AuditLog)
        .order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return AuditLogPage(
        items=[serialize_log(log) for log in logs],
        total=total,
        limit=limit,
        offset=offset,
    )
