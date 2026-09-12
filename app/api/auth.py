from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.dependencies import (
    AuthContext,
    get_db,
    request_source,
    require_authenticated,
    validate_csrf_request,
)
from app.models import User
from app.schemas import CurrentUserRead
from app.services.audit import add_audit_log
from app.services.security import (
    DUMMY_PASSWORD_HASH,
    SessionTokens,
    create_session,
    normalize_username,
    resolve_session,
    token_matches,
    verify_password,
)

router = APIRouter(tags=["kimlik doğrulama"])
DatabaseSession = Annotated[Session, Depends(get_db)]
Authenticated = Annotated[AuthContext, Depends(require_authenticated)]


def set_session_cookies(
    response: HTMLResponse | RedirectResponse,
    settings: Settings,
    tokens: SessionTokens,
    *,
    max_age: int,
) -> None:
    cookie_options = {
        "max_age": max_age,
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": "lax",
        "path": "/",
    }
    response.set_cookie(settings.session_cookie_name, tokens.session_id, **cookie_options)
    response.set_cookie(settings.csrf_cookie_name, tokens.csrf_token, **cookie_options)


def clear_session_cookies(response: HTMLResponse | RedirectResponse, settings: Settings) -> None:
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        settings.csrf_cookie_name,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def render_login(
    request: Request,
    templates: Jinja2Templates,
    csrf_token: str,
    *,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    response = templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"csrf_token": csrf_token, "error": error},
        status_code=status_code,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def register_auth_routes(application, templates: Jinja2Templates) -> None:
    @application.get("/login", response_class=HTMLResponse, include_in_schema=False)
    def login_page(request: Request, db: DatabaseSession):
        settings: Settings = request.app.state.settings
        now = request.app.state.clock()
        raw_session_id = request.cookies.get(settings.session_cookie_name)
        user_session = resolve_session(
            db,
            raw_session_id,
            now=now,
            settings=settings,
            allow_preauth=True,
            touch=False,
        )
        if user_session is not None and user_session.user_id is not None:
            return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

        csrf_token = request.cookies.get(settings.csrf_cookie_name)
        if (
            user_session is None
            or csrf_token is None
            or not token_matches(csrf_token, user_session.csrf_token_hash)
        ):
            if user_session is not None:
                user_session.revoked_at = now
            _, tokens = create_session(
                db,
                user_id=None,
                now=now,
                lifetime=timedelta(minutes=settings.login_session_minutes),
            )
            db.commit()
            response = render_login(request, templates, tokens.csrf_token)
            set_session_cookies(
                response,
                settings,
                tokens,
                max_age=settings.login_session_minutes * 60,
            )
            return response
        return render_login(request, templates, csrf_token)

    @application.post("/login", response_class=HTMLResponse, include_in_schema=False)
    def login(
        request: Request,
        username: Annotated[str, Form()],
        password: Annotated[str, Form()],
        db: DatabaseSession,
        csrf_token: Annotated[str | None, Form()] = None,
    ):
        settings: Settings = request.app.state.settings
        now = request.app.state.clock()
        preauth_session = resolve_session(
            db,
            request.cookies.get(settings.session_cookie_name),
            now=now,
            settings=settings,
            allow_preauth=True,
            touch=False,
        )
        if preauth_session is None or preauth_session.user_id is not None:
            raise HTTPException(status_code=403, detail="Geçerli giriş oturumu bulunamadı.")
        validate_csrf_request(request, settings, preauth_session, csrf_token)

        try:
            normalized_username = normalize_username(username)
            audit_username = normalized_username
        except ValueError:
            normalized_username = "invalid:" + hashlib.sha256(username.encode()).hexdigest()
            audit_username = None
        source = request_source(request)
        retry_after = request.app.state.login_rate_limiter.retry_after(
            normalized_username, source, now
        )
        if retry_after is not None:
            add_audit_log(
                db,
                now=now,
                source="web",
                action="auth.login",
                outcome="denied",
                actor_username=audit_username,
                target_type="user",
                target_id=audit_username,
                metadata={"reason": "rate_limited"},
            )
            db.commit()
            response = render_login(
                request,
                templates,
                csrf_token or "",
                error="Çok fazla başarısız deneme yapıldı. Lütfen daha sonra tekrar deneyin.",
                status_code=429,
            )
            response.headers["Retry-After"] = str(retry_after)
            return response

        user = db.scalar(select(User).where(User.username == normalized_username))
        password_length_is_valid = 15 <= len(password) <= 128
        password_hash = (
            user.password_hash
            if user is not None and password_length_is_valid
            else DUMMY_PASSWORD_HASH
        )
        password_is_valid = verify_password(
            password if password_length_is_valid else "invalid-password-length",
            password_hash,
        )
        if user is None or not user.is_active or not password_is_valid:
            request.app.state.login_rate_limiter.record_failure(normalized_username, source, now)
            add_audit_log(
                db,
                now=now,
                source="web",
                action="auth.login",
                outcome="failure",
                actor_user_id=user.id if user is not None else None,
                actor_username=audit_username,
                target_type="user",
                target_id=audit_username,
                metadata={"reason": "invalid_credentials"},
            )
            db.commit()
            return render_login(
                request,
                templates,
                csrf_token,
                error="Kullanıcı adı veya parola hatalı.",
                status_code=401,
            )

        request.app.state.login_rate_limiter.reset_account(normalized_username)
        preauth_session.revoked_at = now
        _, tokens = create_session(
            db,
            user_id=user.id,
            now=now,
            lifetime=timedelta(hours=settings.session_absolute_hours),
        )
        add_audit_log(
            db,
            now=now,
            source="web",
            action="auth.login",
            outcome="success",
            actor_user_id=user.id,
            actor_username=user.username,
            target_type="user",
            target_id=user.id,
        )
        db.commit()
        response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
        set_session_cookies(
            response,
            settings,
            tokens,
            max_age=settings.session_absolute_hours * 3600,
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @application.post("/logout", include_in_schema=False)
    def logout(
        request: Request,
        context: Authenticated,
        db: DatabaseSession,
        csrf_token: Annotated[str | None, Form()] = None,
    ):
        settings: Settings = request.app.state.settings
        validate_csrf_request(request, settings, context.session, csrf_token)
        now = request.app.state.clock()
        context.session.revoked_at = now
        add_audit_log(
            db,
            now=now,
            source="web",
            action="auth.logout",
            outcome="success",
            actor_user_id=context.user.id,
            actor_username=context.user.username,
            target_type="session",
            target_id=context.session.id,
        )
        db.commit()
        response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
        clear_session_cookies(response, settings)
        response.headers["Cache-Control"] = "no-store"
        return response

    application.include_router(router)


@router.get("/api/auth/me", response_model=CurrentUserRead)
def current_user(context: Authenticated) -> CurrentUserRead:
    return CurrentUserRead(
        id=context.user.id,
        username=context.user.username,
        role=context.user.role,
    )
