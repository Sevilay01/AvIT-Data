"""Headless Chromium acceptance against an isolated localhost mock database."""

import argparse
import csv
import hashlib
import io
import json
import secrets
import tempfile
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from scripts.common import ROOT, configure_console
from scripts.runtime_smoke import server


def prepare_database(path):
    from alembic.config import Config

    from alembic import command
    from app.database import Database
    from app.models import Device, MonitoringResult, utc_now
    from app.services.users import create_user

    url = "sqlite:///" + path.as_posix()
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    database = Database(url)
    passwords = {role: secrets.token_urlsafe(32) for role in ("admin", "viewer")}
    try:
        with database.session_factory() as db:
            for role, password in passwords.items():
                create_user(
                    db, username="verification-" + role, password=password,
                    role=role, now=utc_now(),
                )
            device = Device(name="Türkçe grafik cihazı", ip_address="192.0.2.1")
            db.add(device)
            db.flush()
            for index, (outcome, latency) in enumerate(
                (("reply", 1.25), ("no_reply", None), ("reply", 2.5))
            ):
                db.add(MonitoringResult(
                    device_id=device.id, target_ip=device.ip_address,
                    probe_mode="mock", outcome=outcome, latency_ms=latency,
                    checked_at=utc_now() - timedelta(seconds=30 - index),
                    evaluated=True,
                ))
            db.commit()
    finally:
        database.dispose()
    return passwords


def login(page, origin, username, password):
    from playwright.sync_api import expect

    page.goto(origin + "/login")
    expect(page.locator('input[name="username"]')).to_be_visible()
    page.locator('input[name="username"]').fill(username)
    page.locator('input[name="password"]').fill(password)
    page.locator('input[name="password"]').press("Enter")
    expect(page).to_have_url(origin + "/")


def exercise(directory, output):
    from playwright.sync_api import expect, sync_playwright

    from app.models import utc_now

    path = directory / "browser.db"
    passwords = prepare_database(path)
    page_errors, external_requests = [], []
    with server(path, directory) as client, sync_playwright() as playwright:
        client.login(passwords["admin"])
        device = client.json(
            "/api/devices", body={"name": "Alarm demo", "ip_address": "192.0.2.2"},
            expected=201,
        )
        for _ in range(4):
            client.json(f"/api/devices/{device['id']}/check", body={})
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                accept_downloads=True, timezone_id="Europe/Istanbul",
                viewport={"width": 1440, "height": 1000}, service_workers="block",
            )
            origin = urlsplit(client.origin)

            def restrict_requests(route):
                requested = urlsplit(route.request.url)
                if (requested.scheme, requested.netloc) != (origin.scheme, origin.netloc):
                    external_requests.append(requested.netloc)
                    route.abort()
                else:
                    route.continue_()

            context.route("**/*", restrict_requests)
            page = context.new_page()
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            login(page, client.origin, "verification-admin", passwords["admin"])
            card = page.locator("#devices-list .device-card").filter(
                has_text="Türkçe grafik cihazı"
            )
            expect(card).to_be_visible()
            card.get_by_role("button", name="Geçmiş", exact=True).click()
            expect(page.locator("#report-status")).to_have_text("Rapor hazır.")
            expect(page.locator("#report-csv")).to_be_enabled()
            page.wait_for_function(
                "() => Chart.getChart('latency-chart')?.data.datasets[0].data.length === 3"
            )
            points = page.evaluate(
                "() => Chart.getChart('latency-chart').data.datasets[0].data.map(p => p.y)"
            )
            assert points == [1.25, None, 2.5]
            assert page.evaluate(
                "() => Chart.getChart('latency-chart').data.datasets[0].spanGaps"
            ) is False

            page.locator("#report-source").select_option("scheduled")
            expect(page.locator("#report-csv")).to_be_disabled()
            page.locator("#report-form").get_by_role("button", name="Filtreleri uygula").click()
            expect(page.locator("#report-status")).to_contain_text("ölçüm yok")
            page.locator("#report-source").select_option("manual")
            page.locator("#report-form").get_by_role("button", name="Filtreleri uygula").click()
            expect(page.locator("#report-status")).to_have_text("Rapor hazır.")
            with page.expect_download() as download_info:
                page.locator("#report-csv").click()
            download = download_info.value
            csv_path = directory / "download.csv"
            download.save_as(csv_path)
            assert download.failure() is None
            content = csv_path.read_bytes()
            rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig")), delimiter=";"))
            assert len(rows) == 3
            assert [r["latency_ms"] for r in rows] == ["1,25", "", "2,5"]
            assert all(r["device_name"] == "Türkçe grafik cihazı" for r in rows)

            # Enter Istanbul wall times through the real forms.
            now = utc_now()
            start = (now + timedelta(hours=3, minutes=1)).strftime("%Y-%m-%dT%H:%M:%S")
            end = (now + timedelta(hours=3, minutes=10)).strftime("%Y-%m-%dT%H:%M:%S")
            expect(page.locator("#maintenance-device option").first).to_be_attached()
            page.locator('#maintenance-form input[name="starts_at"]').fill(start)
            page.locator('#maintenance-form input[name="ends_at"]').fill(end)
            page.locator('#maintenance-form textarea[name="reason"]').fill("Tarayıcı kabul bakımı")
            page.locator("#maintenance-form").get_by_role("button", name="Bakım oluştur").click()
            expect(page.locator("#maintenance-message")).to_have_text("İşlem kaydedildi.")
            maintenance = page.locator("#maintenance-list .operation-row").filter(
                has_text="Tarayıcı kabul bakımı"
            )
            expect(maintenance).to_be_visible()
            maintenance.get_by_role("button", name="İptal et").click()
            expect(maintenance).to_contain_text("İptal edildi")

            page.locator("#alarms-list").get_by_role("button", name="Alarm detayı").first.click()
            expect(page.locator("#silence-form")).to_be_visible()
            page.locator('#silence-form input[name="ends_at"]').fill(end)
            page.locator('#silence-form textarea[name="reason"]').fill("Tarayıcı kabul susturması")
            page.locator("#silence-form").get_by_role("button", name="Süreli sustur").click()
            expect(page.locator("#silence-message")).to_have_text("İşlem kaydedildi.")
            silence = page.locator("#silence-list .operation-row").filter(
                has_text="Tarayıcı kabul susturması"
            )
            expect(silence).to_be_visible()
            silence.get_by_role("button", name="İptal et").click()
            expect(silence).to_contain_text("İptal edildi")
            page.locator("#report-panel").screenshot(path=str(output / "graph.png"))

            page.get_by_role("button", name="Çıkış yap").click()
            expect(page).to_have_url(client.origin + "/login")
            login(page, client.origin, "verification-viewer", passwords["viewer"])
            expect(page.locator("#add-form")).to_have_count(0)
            expect(page.locator("#maintenance-form")).to_have_count(0)
            expect(page.locator("#monitor-start")).to_have_count(0)
            expect(page.locator("#devices-list")).to_contain_text("Türkçe grafik cihazı")
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(output / "viewer-mobile.png"), full_page=True)
            assert not page_errors, page_errors
            assert not external_requests, external_requests
            return {
                "status": "passed", "browser": "Chromium " + browser.version,
                "transport": "isolated localhost HTTP; mock probes and notifications",
                "graph_points": points, "source_filter": "passed",
                "csv_button_download_to_disk": "passed", "csv_rows": len(rows),
                "csv_sha256": hashlib.sha256(content).hexdigest(),
                "maintenance_and_silence_forms": "passed",
                "admin_viewer_ui": "passed", "login_logout": "passed",
                "page_errors": 0, "external_page_requests": 0,
                "desktop_viewport": "1440x1000", "mobile_viewport": "390x844",
                "manual_visual_review": "not_run", "excel": "not_run",
                "real_smtp": "not_run", "company_network": "not_run",
            }
        finally:
            browser.close()


def main():
    configure_console()
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="avit-browser-") as name:
        result = exercise(Path(name), output)
    (output / "acceptance.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("Chromium: grafik, CSV indirme, bakım/susturma ve rol arayüzü geçti.")


if __name__ == "__main__":
    main()
