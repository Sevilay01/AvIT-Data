from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import User, UserSession
from app.services.monitoring import MonitoringService
from app.services.security import resolve_session, token_matches


def get_db(request: Request) -> Generator[Session, None, None]:
    yield from request.app.state.database.session()


def get_monitoring_service(request: Request) -> MonitoringService:
    return request.app.state.monitoring_service


@dataclass(frozen=True, slots=True)
class AuthContext:
    user: User
    session: UserSession


def request_source(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def trusted_origin(request: Request, settings: Settings) -> bool:
    supplied = request.headers.get("origin")
    if supplied:
        return supplied.rstrip("/") == settings.app_origin
    referer = request.headers.get("referer")
    if not referer:
        return False
    parsed = urlsplit(referer)
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/") == settings.app_origin


def validate_csrf_request(
    request: Request,
    settings: Settings,
    user_session: UserSession,
    csrf_token: str | None,
) -> None:
    if not trusted_origin(request, settings):
        raise HTTPException(status_code=403, detail="İstek kaynağı doğrulanamadı.")
    if not csrf_token or not token_matches(csrf_token, user_session.csrf_token_hash):
        raise HTTPException(status_code=403, detail="CSRF doğrulaması başarısız.")


def get_optional_auth(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> AuthContext | None:
    settings: Settings = request.app.state.settings
    user_session = resolve_session(
        db,
        request.cookies.get(settings.session_cookie_name),
        now=request.app.state.clock(),
        settings=settings,
    )
    if user_session is None or user_session.user is None:
        return None
    return AuthContext(user=user_session.user, session=user_session)


def require_authenticated(
    context: Annotated[AuthContext | None, Depends(get_optional_auth)],
) -> AuthContext:
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Kimlik doğrulaması gerekli.",
        )
    return context


def require_admin(
    context: Annotated[AuthContext, Depends(require_authenticated)],
) -> AuthContext:
    if context.user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bu işlem için yönetici yetkisi gerekli.",
        )
    return context


def require_csrf(
    request: Request,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    csrf_token: Annotated[
        str | None,
        Header(
            alias="X-CSRF-Token",
            description=(
                "Ana web ekranındaki csrf-token meta alanından alınan, mevcut oturuma bağlı token."
            ),
        ),
    ] = None,
) -> AuthContext:
    settings: Settings = request.app.state.settings
    validate_csrf_request(request, settings, context.session, csrf_token)
    return context


def require_admin_mutation(
    context: Annotated[AuthContext, Depends(require_csrf)],
) -> AuthContext:
    if context.user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bu işlem için yönetici yetkisi gerekli.",
        )
    return context
