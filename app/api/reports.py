from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import text

from app.api.devices import Authenticated, DatabaseSession, get_device_or_404
from app.services.reporting import ExportLimitError, ReportFilters, measurements_csv, metrics

router = APIRouter(prefix="/api/devices", tags=["ölçüm raporları"])


def report_filters(
    request: Request,
    user: Authenticated,
    start: str | None = None,
    end: str | None = None,
    probe_mode: Literal["mock", "icmp"] | None = None,
    source: Literal["manual", "scheduled", "all"] = "all",
) -> ReportFilters:
    try:
        return ReportFilters.build(
            start,
            end,
            probe_mode or request.app.state.settings.monitor_mode,
            source,
            request.app.state.clock(),
        )
    except (ValueError, OverflowError) as exc:
        raise HTTPException(422, str(exc)) from exc


Filters = Annotated[ReportFilters, Depends(report_filters)]


@router.get("/{device_id}/metrics")
def device_metrics(
    device_id: int, request: Request, response: Response, db: DatabaseSession, filters: Filters
):
    response.headers["Cache-Control"] = "no-store"
    # Explicit read snapshot keeps aggregates and capped points consistent. No probe calls.
    db.commit()
    db.execute(text("BEGIN"))
    try:
        device = get_device_or_404(device_id, db)
        result = metrics(db, device_id, filters)
        result["device"] = {"id": device.id, "name": device.name, "is_active": device.is_active}
        result["graph"]["gap_threshold_seconds"] = (
            request.app.state.settings.monitor_interval_seconds * 2
        )
        return result
    finally:
        db.rollback()


@router.get("/{device_id}/measurements.csv")
def device_csv(device_id: int, request: Request, db: DatabaseSession, filters: Filters):
    db.commit()
    db.execute(text("BEGIN"))
    try:
        device = get_device_or_404(device_id, db)
        content = measurements_csv(db, device, filters, request.app.state.settings.csv_max_rows)
    except ExportLimitError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        db.rollback()
    filename = f"device-{device_id}-measurements-{filters.start:%Y%m%d}-{filters.end:%Y%m%d}.csv"
    return Response(
        content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Data-Scope": "retained-measurements-only; period-coverage=unknown",
        },
    )
