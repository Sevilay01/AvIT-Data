import csv
import hashlib
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from app.models import Alarm, Device, MonitoringResult
from app.services.reporting import GRAPH_LIMIT, csv_text

START = datetime(2026, 9, 11, 6, tzinfo=UTC)
FILTERS = {
    "start": "2026-09-11T06:00:00Z",
    "end": "2026-09-11T07:00:00Z",
    "probe_mode": "mock",
    "source": "all",
}


def seed(app, rows=(), name="İstanbul ölçüm cihazı", active=True):
    with app.state.database.session_factory() as db:
        device = Device(name=name, ip_address="192.0.2.99", is_active=active)
        db.add(device)
        db.flush()
        for index, values in enumerate(rows):
            record = {
                "device_id": device.id,
                "target_ip": "192.0.2.1",
                "probe_mode": "mock",
                "source": "manual",
                "outcome": "reply",
                "latency_ms": 10.5,
                "checked_at": START + timedelta(seconds=index),
                "target_version": 1,
            }
            record.update(values)
            db.add(MonitoringResult(**record))
        db.commit()
        return device.id


def report(client, device_id, **overrides):
    return client.get(f"/api/devices/{device_id}/metrics", params=FILTERS | overrides)


def export(client, device_id, **overrides):
    return client.get(f"/api/devices/{device_id}/measurements.csv", params=FILTERS | overrides)


def csv_rows(response):
    assert response.status_code == 200, response.text
    assert response.content.startswith(b"\xef\xbb\xbf")
    return list(
        csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"), newline=""), delimiter=";")
    )


def test_summary_uses_all_outcomes_and_only_valid_success_rtt(app, viewer_client):
    device_id = seed(
        app,
        [
            {"latency_ms": 10},
            {"latency_ms": 20},
            {"latency_ms": None},
            {"outcome": "no_reply", "latency_ms": 900},
            {"outcome": "error", "latency_ms": 800},
            {"outcome": "error", "latency_ms": None},
        ],
    )
    data = report(viewer_client, device_id).json()
    assert data["summary"] == {
        "total": 6,
        "reply": 3,
        "no_reply": 1,
        "error": 2,
        "rtt_count": 2,
        "min_rtt_ms": 10,
        "max_rtt_ms": 20,
        "avg_rtt_ms": 15,
        "response_rate": 75,
    }
    assert [p["latency_ms"] for p in data["graph"]["points"]] == [10, 20, None, None, None, None]


@pytest.mark.parametrize("suffix", ["metrics", "measurements.csv"])
@pytest.mark.parametrize(
    "overrides",
    [
        {"start": "2026-09-11T06:00:00"},
        {"start": "not-a-date"},
        {"end": "2026-09-11"},
        {"start": "1789106400"},
        {"start": "2026-09-11T07:00:00Z"},
        {"end": "2026-09-10T07:00:00Z"},
        {"end": "2026-10-11T06:00:00.000001Z"},
        {"probe_mode": "all"},
        {"source": "cli"},
        {"start": "2026-09-11T06:00:00+25:00"},
    ],
)
def test_invalid_filters_rejected(app, viewer_client, suffix, overrides):
    device_id = seed(app)
    response = viewer_client.get(f"/api/devices/{device_id}/{suffix}", params=FILTERS | overrides)
    assert response.status_code == 422
    assert "Content-Disposition" not in response.headers


def test_default_24h_and_partial_range_rejected(app, viewer_client, clock):
    device_id = seed(app)
    data = viewer_client.get(f"/api/devices/{device_id}/metrics").json()
    filters = data["filters"]
    assert datetime.fromisoformat(filters["end"]) == clock()
    assert datetime.fromisoformat(filters["start"]) == clock() - timedelta(hours=24)
    assert (
        viewer_client.get(
            f"/api/devices/{device_id}/metrics", params={"start": FILTERS["start"]}
        ).status_code
        == 422
    )
    assert report(viewer_client, device_id, end="2026-10-11T06:00:00Z").status_code == 200


def test_offset_equivalence_half_open_and_microsecond_precision(app, viewer_client):
    device_id = seed(
        app,
        [
            {"checked_at": START - timedelta(microseconds=1)},
            {"checked_at": START},
            {"checked_at": START + timedelta(microseconds=1)},
            {"checked_at": START + timedelta(microseconds=1000)},
            {"checked_at": START + timedelta(seconds=1)},
        ],
    )
    # Include legacy millisecond text alongside whole and microsecond timestamps.
    with app.state.database.session_factory() as db:
        db.execute(
            text("UPDATE monitoring_results SET checked_at='2026-09-11T06:00:00.001Z' WHERE id=4")
        )
        db.commit()
    first = report(viewer_client, device_id, end="2026-09-11T06:00:01Z").json()
    same = report(
        viewer_client, device_id, start="2026-09-11T09:00:00+03:00", end="2026-09-11T02:00:01-04:00"
    ).json()
    assert first == same
    assert [p["measurement_id"] for p in first["graph"]["points"]] == [2, 3, 4]
    narrow = report(
        viewer_client,
        device_id,
        start="2026-09-11T06:00:00.000001Z",
        end="2026-09-11T06:00:00.001000Z",
    ).json()
    assert [p["measurement_id"] for p in narrow["graph"]["points"]] == [3]
    assert [
        r["measurement_id"]
        for r in csv_rows(
            export(
                viewer_client,
                device_id,
                start="2026-09-11T06:00:00.000001Z",
                end="2026-09-11T06:00:00.001000Z",
            )
        )
    ] == ["3"]


@pytest.mark.parametrize(
    "mode,source,expected",
    [("mock", "manual", 1), ("mock", "scheduled", 1), ("icmp", "all", 2), ("mock", "all", 2)],
)
def test_mode_source_and_inactive_history(app, viewer_client, mode, source, expected):
    device_id = seed(
        app,
        [{"probe_mode": m, "source": s} for m in ["mock", "icmp"] for s in ["manual", "scheduled"]],
        active=False,
    )
    result = report(viewer_client, device_id, probe_mode=mode, source=source)
    assert result.status_code == 200
    data = result.json()
    assert not data["device"]["is_active"]
    assert data["summary"]["total"] == expected
    rows = csv_rows(export(viewer_client, device_id, probe_mode=mode, source=source))
    assert len(rows) == expected
    assert all(row["probe_mode"] == mode for row in rows)
    if source != "all":
        assert all(row["trigger_source"] == source for row in rows)
    assert [p["measurement_id"] for p in data["graph"]["points"]] == [
        int(r["measurement_id"]) for r in rows
    ]


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"outcome": "error", "latency_ms": None}],
        [{"latency_ms": None}],
        [{"latency_ms": float("inf")}],
        [{"latency_ms": 0}],
    ],
)
def test_empty_missing_nonfinite_and_zero_rtt(app, viewer_client, rows):
    device_id = seed(app, rows)
    summary = report(viewer_client, device_id).json()["summary"]
    has_zero = bool(rows and rows[0]["latency_ms"] == 0)
    assert summary["rtt_count"] == int(has_zero)
    assert summary["avg_rtt_ms"] == (0 if has_zero else None)
    assert summary["min_rtt_ms"] == (0 if has_zero else None)
    assert summary["max_rtt_ms"] == (0 if has_zero else None)
    assert summary["response_rate"] == (100 if rows and rows[0].get("outcome") != "error" else None)
    exported = csv_rows(export(viewer_client, device_id))
    if rows:
        assert exported[0]["latency_ms"] == ("0,0" if has_zero else "")


def test_newest_2000_graph_and_full_period_summary(app, viewer_client):
    rows = [{"latency_ms": 200 if i < 5 else 10} for i in range(GRAPH_LIMIT + 5)]
    device_id = seed(app, rows)
    data = report(viewer_client, device_id).json()
    assert data["summary"]["total"] == 2005
    assert data["summary"]["max_rtt_ms"] == 200
    assert data["summary"]["avg_rtt_ms"] == pytest.approx((5 * 200 + 2000 * 10) / 2005)
    graph = data["graph"]
    assert graph["limit"] == graph["shown_count"] == 2000
    assert graph["total_count"] == 2005 and graph["truncated"]
    assert graph["points"][0]["measurement_id"] == 6
    assert graph["points"][-1]["measurement_id"] == 2005
    assert graph["first_checked_at"] == graph["points"][0]["checked_at"]
    assert graph["last_checked_at"] == graph["points"][-1]["checked_at"]
    assert len(csv_rows(export(viewer_client, device_id))) == 2005


def test_csv_roundtrip_turkish_quoting_historical_ip_and_headers(app, admin_client):
    name = 'Şube; "İstanbul"\r\nÖlçüm'
    device_id = seed(
        app,
        [
            {"target_ip": "192.0.2.5", "latency_ms": 1.25},
            {"target_ip": "192.0.2.6", "latency_ms": None, "target_version": 2},
        ],
        name=name,
    )
    response = export(admin_client, device_id)
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"].startswith('attachment; filename="device-')
    assert response.headers["cache-control"] == "no-store"
    rows = csv_rows(response)
    assert [row["device_name"] for row in rows] == [name, name]
    assert [row["target_ip"] for row in rows] == ["192.0.2.5", "192.0.2.6"]
    assert [row["latency_ms"] for row in rows] == ["1,25", ""]
    assert rows[0]["checked_at_utc"] == "2026-09-11T06:00:00.000000Z"
    data = report(admin_client, device_id).json()
    assert [point["target_version"] for point in data["graph"]["points"]] == [1, 2]


@pytest.mark.parametrize(
    "name", ["=1+1", "+1", "-1", "@SUM(1)", " \t\r\n=1+1", "\x01+1", "\u200b\ufeff @SUM(1)"]
)
def test_formula_defense_only_on_export(app, viewer_client, name):
    device_id = seed(app, [{}], name=name)
    assert csv_rows(export(viewer_client, device_id))[0]["device_name"] == "'" + name
    with app.state.database.session_factory() as db:
        assert db.get(Device, device_id).name == name
    assert csv_text("\x00\x01+1") == "'\x00\x01+1"
    assert csv_text("İstanbul; normal") == "İstanbul; normal"


def test_export_limit_rejects_before_attachment_without_truncation(app, viewer_client):
    app.state.settings.csv_max_rows = 2
    device_id = seed(app, [{}, {}, {}])
    response = export(viewer_client, device_id)
    assert response.status_code == 422
    assert "daralt" in response.json()["detail"]
    assert "Content-Disposition" not in response.headers
    assert len(csv_rows(export(viewer_client, device_id, end="2026-09-11T06:00:02Z"))) == 2


@pytest.mark.parametrize("suffix", ["metrics", "measurements.csv"])
def test_anonymous_and_missing_device(client, viewer_client, suffix):
    # A separate anonymous client cookie jar is needed (fixtures share the live client).
    viewer_client.cookies.clear()
    assert client.get(f"/api/devices/999/{suffix}", params=FILTERS).status_code == 401


@pytest.mark.parametrize("suffix", ["metrics", "measurements.csv"])
def test_authenticated_missing_device(viewer_client, suffix):
    assert viewer_client.get(f"/api/devices/999/{suffix}", params=FILTERS).status_code == 404


def test_report_reads_do_not_trigger_monitoring(app, viewer_client):
    device_id = seed(app, [{}])

    class ForbiddenProvider:
        mode = "mock"

        async def probe(self, target):
            pytest.fail("Reports must never probe")

    app.state.monitoring_service.provider = ForbiddenProvider()
    assert report(viewer_client, device_id).status_code == 200
    assert export(viewer_client, device_id).status_code == 200
    assert not app.state.scheduler.running
    with app.state.database.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(MonitoringResult)) == 1
        assert db.scalar(select(func.count()).select_from(Alarm)) == 0


def test_local_chart_distribution_integrity_and_viewer_controls(viewer_client):
    root = Path("app/static/vendor/chartjs-4.5.1")
    provenance = json.loads((root / "provenance.json").read_text())
    content = viewer_client.get("/static/vendor/chartjs-4.5.1/chart.umd.js").content
    assert hashlib.sha256(content).hexdigest() == provenance["chart_umd_sha256"]
    assert b"Chart.js v4.5.1" in content
    page = viewer_client.get("/").text
    assert "report-csv" in page and "report-form" in page
    assert "vendor/chartjs-4.5.1/chart.umd.js" in page
    assert "cdn.jsdelivr" not in page
    assert "monitor-start" not in page
