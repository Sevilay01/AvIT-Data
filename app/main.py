from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from app.api.audit import router as audit_router
from app.api.auth import register_auth_routes
from app.api.devices import router as devices_router
from app.api.monitoring import router as monitoring_router
from app.api.reports import router as reports_router
from app.config import Settings, get_settings
from app.database import Database
from app.dependencies import AuthContext, get_optional_auth, require_admin
from app.models import utc_now
from app.schemas import HealthResponse
from app.services.monitoring import build_monitoring_service
from app.services.process_lock import DatabaseProcessLock
from app.services.scheduler import MonitoringScheduler
from app.services.security import LoginRateLimiter, token_matches

APP_DIRECTORY = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=APP_DIRECTORY / "templates")
OptionalAuth = Annotated[AuthContext | None, Depends(get_optional_auth)]
Admin = Annotated[AuthContext, Depends(require_admin)]


def create_app(
    settings: Settings | None = None,
    *,
    clock: Callable[[], datetime] = utc_now,
) -> FastAPI:
    app_settings = settings or get_settings()
    database = Database(app_settings.database_url)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        process_lock = DatabaseProcessLock(app_settings.database_url)
        process_lock.acquire()
        try:
            scheduler = MonitoringScheduler(
                database, application.state.monitoring_service, app_settings, clock
            )
            application.state.scheduler = scheduler
            scheduler.reset()
            try:
                yield
            finally:
                await scheduler.stop()
        finally:
            application.state.database.dispose()
            process_lock.release()

    application = FastAPI(
        title="AvITData Kurumsal Ağ İzleme",
        version="0.4.0",
        description=(
            "Yerel geliştirme için manuel cihaz kontrolü prototipi. Yazma uçları oturum "
            "cookie'sine ek olarak ana web ekranındaki csrf-token meta değerinin "
            "X-CSRF-Token başlığında gönderilmesini gerektirir."
        ),
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.state.settings = app_settings
    application.state.database = database
    application.state.monitoring_service = build_monitoring_service(app_settings)
    application.state.clock = clock
    application.state.login_rate_limiter = LoginRateLimiter(app_settings)
    application.include_router(monitoring_router)
    application.include_router(reports_router)
    application.include_router(devices_router)
    application.include_router(audit_router)
    application.mount(
        "/static",
        StaticFiles(directory=APP_DIRECTORY / "static"),
        name="static",
    )

    register_auth_routes(application, templates)

    @application.get("/", response_class=HTMLResponse, include_in_schema=False)
    def dashboard(
        request: Request,
        context: OptionalAuth,
    ) -> HTMLResponse:
        if context is None:
            return RedirectResponse("/login", status_code=303)
        csrf_token = request.cookies.get(app_settings.csrf_cookie_name)
        if not csrf_token or not token_matches(csrf_token, context.session.csrf_token_hash):
            return RedirectResponse("/login", status_code=303)
        response = templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "monitor_mode": app_settings.monitor_mode,
                "current_user": context.user,
                "csrf_token": csrf_token,
            },
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @application.get("/health", response_model=HealthResponse, tags=["sistem"])
    def health() -> HealthResponse:
        with database.session_factory() as session:
            session.execute(text("SELECT 1"))
        return HealthResponse(status="ok")

    @application.get("/docs", include_in_schema=False)
    def swagger_ui(admin: Admin):
        del admin
        return get_swagger_ui_html(
            openapi_url="/openapi.json",
            title="AvITData API belgeleri",
        )

    @application.get("/openapi.json", include_in_schema=False)
    def openapi_schema(admin: Admin) -> JSONResponse:
        del admin
        return JSONResponse(application.openapi())

    return application


app = create_app()
