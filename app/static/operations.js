"use strict";

(() => {
  let maintenanceOffset = 0;
  let notificationOffset = 0;
  let alarmId = null;
  const $ = (selector) => document.querySelector(selector);
  const notificationLabels = {
    disabled: "Kapalı modda kaydedildi", pending: "Gönderim bekliyor", sending: "Gönderiliyor",
    retry: "Yeniden denenecek", accepted: "SMTP kabul etti", mock_sent: "MOCK kaydı tamamlandı",
    failed: "Denemeler tükendi", suppressed: "Bastırıldı", discarded: "Gönderilmeden kapatıldı",
  };
  const eventLabels = {opened: "Alarm açıldı", resolved: "Alarm çözüldü",
    current_status: "Güncel durum", resolved_summary: "Çözülme özeti"};
  // These inputs are explicitly Europe/Istanbul, independent of the browser's zone.
  const toUTC = (value) => new Date(`${value}+03:00`).toISOString();

  window.renderOperationsHealth = (data) => {
    const op = data.operations;
    const heartbeat = op.heartbeat || {};
    const cards = [
      ["Zamanlayıcı son çalışması", formatDate(heartbeat.last_scheduler_at)],
      ["Son tamamlanan tarama", formatDate(heartbeat.last_scan_completed_at)],
      ["Son tamamlanan kontrol", formatDate(heartbeat.last_check_completed_at)],
      ["Gecikmiş / ölçümsüz cihaz", data.overdue_checks],
      ["Güncel teknik kontrol hatası", data.technical_errors],
      ["Bekleyen bildirim", op.pending_notifications],
      ["Başarısız / yeniden denenecek", `${op.failed_notifications} / ${op.retry_notifications}`],
      ["Bildirim modu", op.notification_mode.toUpperCase()],
      ["Geri yükleme güvenliği", op.recovery_hold ? "Karantina: mock, gönderim bekletiliyor" : "Normal"],
      ["Bastırma sonrası uzlaştırma bekleyen", op.awaiting_reconciliation],
      ["Gönderici son çalışması", formatDate(op.notification_last_tick_at)],
    ];
    const container = $("#operations-health");
    container.replaceChildren();
    for (const [label, value] of cards) {
      const card = document.createElement("div");
      card.className = "health-card";
      const title = document.createElement("span");
      const result = document.createElement("strong");
      title.textContent = label;
      result.textContent = value;
      card.append(title, result);
      container.append(card);
    }
    $("#operations-warning").textContent = [
      !data.running ? "Otomatik izleme yönetici tarafından duraklatılmış. Başlangıçta otomatik başlamaz." : "",
      data.scheduler_overdue ? "Zamanlayıcı gecikmiş; uygulamanın açık olması sağlıklı izleme kanıtı değildir." : "",
      data.overdue_checks ? "Güncel olmayan cihaz verileri arıza veya iyileşme kanıtı değildir." : "",
      op.awaiting_reconciliation ? "Bakım/susturma bitince güncel ölçüm gerekli olabilir." : "",
      !op.notification_worker_running ? "Bildirim çalışanı çalışmıyor." : "",
      op.notification_worker_error || "",
    ].filter(Boolean).join(" ");
    const select = $("#maintenance-device");
    if (select) {
      const signature = JSON.stringify(data.devices.map(d => [d.device_id, d.name]));
      if (select.dataset.signature !== signature) {
        const chosen = select.value;
        select.replaceChildren();
        for (const device of data.devices) {
          const option = document.createElement("option");
          option.value = device.device_id;
          option.textContent = `#${device.device_id} · ${device.name}`;
          select.append(option);
        }
        if ([...select.options].some(o => o.value === chosen)) select.value = chosen;
        select.dataset.signature = signature;
      }
    }
  };

  async function mutate(path, payload, messageSelector) {
    await request(path, {method: "POST", ...(payload ? {body: JSON.stringify(payload)} : {})});
    $(messageSelector).textContent = "İşlem kaydedildi.";
    await Promise.all([refreshOperations(), loadOverview(), loadAlarms(), loadAuditLogs()]);
  }

  function intervalRow(row, kind) {
    const article = document.createElement("article");
    article.className = "operation-row";
    const text = document.createElement("p");
    const stateLabel = row.cancelled_at ? "İptal edildi" : row.active ? "Etkin" : "Planlandı / sona erdi";
    text.textContent = `#${row.id}${row.device_id ? ` · Cihaz #${row.device_id}` : ""} · ${stateLabel} · ${formatDate(row.starts_at)} – ${formatDate(row.ends_at)} · ${row.reason}`;
    article.append(text);
    if (isAdmin && !row.cancelled_at && new Date(row.ends_at) > new Date()) {
      article.append(makeButton("İptal et", "secondary", () => {
        mutate(`/api/${kind}/${row.id}/cancel`, null, kind === "maintenance" ? "#maintenance-message" : "#silence-message")
          .catch(error => $(kind === "maintenance" ? "#maintenance-message" : "#silence-message").textContent = error.message);
      }));
    }
    return article;
  }

  async function loadMaintenance() {
    const data = await request(`/api/maintenance?limit=10&offset=${maintenanceOffset}`);
    $("#maintenance-list").replaceChildren(...data.items.map(r => intervalRow(r, "maintenance")));
    $("#maintenance-prev").disabled = maintenanceOffset === 0;
    $("#maintenance-next").disabled = maintenanceOffset + data.items.length >= data.total;
  }
  async function loadSilences() {
    if (!alarmId) return;
    const selected = alarmId;
    const data = await request(`/api/alarms/${selected}/silences`);
    if (selected !== alarmId) return;
    $("#silence-list").replaceChildren(...data.items.map(r => intervalRow(r, "silences")));
  }
  window.selectOperationsAlarm = (alarm) => {
    alarmId = alarm.id;
    $("#silence-title").textContent = `Alarm #${alarm.id} · ${alarm.silenced ? "Susturuldu" : "Etkin susturma yok"} · ${alarm.in_maintenance ? "Bakımda" : "Bakım dışında"}`;
    $("#silence-form")?.classList.toggle("hidden", alarm.status !== "open");
    loadSilences().catch(error => $("#silence-message").textContent = error.message);
  };

  async function loadNotifications() {
    const params = new URLSearchParams({limit: "10", offset: String(notificationOffset)});
    if ($("#notifications-alarm").value) params.set("alarm_id", $("#notifications-alarm").value);
    const data = await request(`/api/notifications?${params}`);
    $("#notifications-status").textContent = `${data.total} kayıt · Europe/Istanbul (UTC+03:00)`;
    const nodes = data.items.map(row => {
      const node = document.createElement("article");
      node.className = "operation-row";
      const title = document.createElement("strong");
      const detail = document.createElement("p");
      title.textContent = `Alarm #${row.alarm_id} · ${eventLabels[row.event_type]} · ${notificationLabels[row.status]}`;
      detail.textContent = `${formatDate(row.created_at)} · ${row.delivery_mode.toUpperCase()} · Deneme: ${row.attempts} · Sonraki: ${formatDate(row.next_attempt_at)} · Olay: ${row.event_id}${row.suppression_reason ? ` · ${row.suppression_reason}` : ""}${row.safe_error ? ` · ${row.safe_error}` : ""}${row.reconciled_at ? " · Uzlaştırıldı" : ""}`;
      node.append(title, detail);
      return node;
    });
    $("#notifications-list").replaceChildren(...nodes);
    $("#notifications-prev").disabled = notificationOffset === 0;
    $("#notifications-next").disabled = notificationOffset + data.items.length >= data.total;
  }

  $("#maintenance-form")?.addEventListener("submit", async event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await mutate("/api/maintenance", {device_id: Number(form.get("device_id")),
        starts_at: toUTC(form.get("starts_at")), ends_at: toUTC(form.get("ends_at")),
        reason: form.get("reason")}, "#maintenance-message");
    } catch (error) { $("#maintenance-message").textContent = error.message; }
  });
  $("#silence-form")?.addEventListener("submit", async event => {
    event.preventDefault();
    if (!alarmId) return;
    const form = new FormData(event.currentTarget);
    try {
      await mutate(`/api/alarms/${alarmId}/silences`, {ends_at: toUTC(form.get("ends_at")),
        reason: form.get("reason")}, "#silence-message");
    } catch (error) { $("#silence-message").textContent = error.message; }
  });
  for (const direction of ["prev", "next"]) {
    $("#maintenance-" + direction).addEventListener("click", () => {
      maintenanceOffset = Math.max(0, maintenanceOffset + (direction === "prev" ? -10 : 10));
      loadMaintenance().catch(error => $("#maintenance-message").textContent = error.message);
    });
    $("#notifications-" + direction).addEventListener("click", () => {
      notificationOffset = Math.max(0, notificationOffset + (direction === "prev" ? -10 : 10));
      loadNotifications().catch(error => $("#notifications-status").textContent = error.message);
    });
  }
  $("#notifications-alarm").addEventListener("change", () => {
    notificationOffset = 0;
    loadNotifications().catch(error => $("#notifications-status").textContent = error.message);
  });
  async function refreshOperations() {
    await Promise.all([loadMaintenance(), loadNotifications(), loadSilences()]);
  }
  async function poll() {
    try { await refreshOperations(); }
    catch (error) { $("#notifications-status").textContent = `İşletim verileri yenilenemedi: ${error.message}`; }
    finally { if (!sessionExpired) window.setTimeout(poll, 5000); }
  }
  poll();
})();
