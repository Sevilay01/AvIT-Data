"""Shared, read-only filters and bounded reports over stored measurements."""

import csv
import io
import math
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import String, and_, case, func, select, type_coerce

from app.models import MonitoringResult
from app.services.timestamps import iso_utc
from app.services.timestamps import timestamp_key as stored_timestamp_key

GRAPH_LIMIT = 2000
MAX_RANGE = timedelta(days=30)
CSV_COLUMNS = (
    "measurement_id",
    "device_id",
    "device_name",
    "target_ip",
    "checked_at_utc",
    "probe_mode",
    "trigger_source",
    "outcome",
    "latency_ms",
    "data_scope",
)
ISO_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})"
)


def parse_timestamp(value: str) -> datetime:
    if not ISO_TIMESTAMP.fullmatch(value):
        raise ValueError(
            "Tarih, saat dilimi içeren ISO 8601 biçiminde olmalı (örn. 2026-09-12T09:00:00+03:00)."
        )
    return datetime.fromisoformat(value).astimezone(UTC)


@dataclass(frozen=True)
class ReportFilters:
    start: datetime
    end: datetime
    probe_mode: str
    source: str

    @classmethod
    def build(cls, start, end, probe_mode, source, now):
        if (start is None) != (end is None):
            raise ValueError("Başlangıç ve bitiş birlikte verilmelidir.")
        end_at = parse_timestamp(end) if end is not None else now.astimezone(UTC)
        start_at = parse_timestamp(start) if start is not None else end_at - timedelta(hours=24)
        if not start_at < end_at:
            raise ValueError("Başlangıç bitişten önce olmalıdır.")
        if end_at - start_at > MAX_RANGE:
            raise ValueError("Sorgu aralığı en fazla 30 gün olabilir.")
        return cls(start_at, end_at, probe_mode, source)

    def as_dict(self):
        return {
            "start": iso_utc(self.start),
            "end": iso_utc(self.end),
            "probe_mode": self.probe_mode,
            "source": self.source,
        }


def timestamp_key():
    return stored_timestamp_key(MonitoringResult.checked_at)


def conditions(device_id: int, filters: ReportFilters):
    clauses = [
        MonitoringResult.device_id == device_id,
        MonitoringResult.probe_mode == filters.probe_mode,
        timestamp_key() >= iso_utc(filters.start),
        timestamp_key() < iso_utc(filters.end),
    ]
    # Coarse indexed bounds reduce the normalization work without losing precision.
    clauses.extend(
        [
            type_coerce(MonitoringResult.checked_at, String) >= iso_utc(filters.start)[:19],
            type_coerce(MonitoringResult.checked_at, String) <= iso_utc(filters.end)[:19] + "Z",
        ]
    )
    if filters.source != "all":
        clauses.append(MonitoringResult.source == filters.source)
    return clauses


def valid_rtt(value):
    return value is not None and math.isfinite(value) and value >= 0


def point_data(result):
    latency = (
        result.latency_ms if result.outcome == "reply" and valid_rtt(result.latency_ms) else None
    )
    return {
        "measurement_id": result.id,
        "checked_at": iso_utc(result.checked_at),
        "target_ip": result.target_ip,
        "target_version": result.target_version,
        "is_current": result.is_current,
        "probe_mode": result.probe_mode,
        "source": result.source,
        "outcome": result.outcome,
        "latency_ms": latency,
    }


def metrics(db, device_id, filters):
    clauses = conditions(device_id, filters)
    r = MonitoringResult
    valid = and_(r.outcome == "reply", r.latency_ms >= 0, r.latency_ms <= sys.float_info.max)
    latency = case((valid, r.latency_ms), else_=None)
    counts = (
        db.execute(
            select(
                func.count().label("total"),
                func.count(case((r.outcome == "reply", 1))).label("reply"),
                func.count(case((r.outcome == "no_reply", 1))).label("no_reply"),
                func.count(case((r.outcome == "error", 1))).label("error"),
                func.count(latency).label("rtt_count"),
                func.min(latency).label("min_rtt_ms"),
                func.max(latency).label("max_rtt_ms"),
            ).where(*clauses)
        )
        .mappings()
        .one()
    )
    summary = dict(counts)
    # Scale before averaging, avoiding float overflow on otherwise finite RTTs.
    maximum = summary["max_rtt_ms"]
    summary["avg_rtt_ms"] = (
        db.scalar(select(func.avg(latency / maximum)).where(*clauses)) * maximum
        if maximum
        else maximum
    )
    denominator = summary["reply"] + summary["no_reply"]
    summary["response_rate"] = summary["reply"] / denominator * 100 if denominator else None
    rows = list(
        db.scalars(
            select(r)
            .where(*clauses)
            .order_by(timestamp_key().desc(), r.id.desc())
            .limit(GRAPH_LIMIT)
        )
    )
    rows.reverse()
    return {
        "filters": filters.as_dict(),
        "data_scope": {
            "basis": "retained_measurements_only",
            "complete_period": False,
            "notice": "Rapor yalnızca saklanan ölçümleri kapsar. Eksik veya silinmiş geçmiş "
                      "başarılı kontrol ya da tam dönem erişilebilirliği sayılmaz.",
        },
        "summary": summary,
        "graph": {
            "limit": GRAPH_LIMIT,
            "total_count": summary["total"],
            "shown_count": len(rows),
            "truncated": summary["total"] > GRAPH_LIMIT,
            "first_checked_at": iso_utc(rows[0].checked_at) if rows else None,
            "last_checked_at": iso_utc(rows[-1].checked_at) if rows else None,
            "points": [point_data(row) for row in rows],
        },
    }


def csv_text(value: str) -> str:
    # Excel-oriented defense: ignore leading whitespace/format/control characters
    # when detecting a formula, then prefix the ORIGINAL cell with an apostrophe.
    significant = value.lstrip()
    while significant and (
        significant[0].isspace() or unicodedata.category(significant[0]).startswith("C")
    ):
        significant = significant[1:]
    if significant.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


class ExportLimitError(ValueError):
    pass


def measurements_csv(db, device, filters, max_rows):
    rows = list(
        db.scalars(
            select(MonitoringResult)
            .where(*conditions(device.id, filters))
            .order_by(timestamp_key(), MonitoringResult.id)
            .limit(max_rows + 1)
        )
    )
    if len(rows) > max_rows:
        raise ExportLimitError(
            f"CSV en fazla {max_rows:,} ölçüm içerebilir. Tarih aralığını daraltın."
        )
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for row in rows:
        writer.writerow(
            [
                row.id,
                row.device_id,
                csv_text(device.name),
                csv_text(row.target_ip),
                iso_utc(row.checked_at),
                row.probe_mode,
                row.source,
                row.outcome,
                str(row.latency_ms).replace(".", ",") if valid_rtt(row.latency_ms) else "",
                "retained_only_period_coverage_unknown",
            ]
        )
    return buffer.getvalue().encode("utf-8-sig")
