"use strict";

const state = { devices: [], latestChecks: new Map(), selectedDeviceId: null };
const mode = document.body.dataset.monitorMode;
const isAdmin = document.body.dataset.userRole === "admin";
const csrfToken = document.querySelector("meta[name='csrf-token']")?.content || "";
const addForm = document.querySelector("#add-form");
const editForm = document.querySelector("#edit-form");
const editPanel = document.querySelector("#edit-panel");
const devicesList = document.querySelector("#devices-list");
const devicesStatus = document.querySelector("#devices-status");
const historyList = document.querySelector("#history-list");
const historyStatus = document.querySelector("#history-status");
const auditList = document.querySelector("#audit-list");
const auditStatus = document.querySelector("#audit-status");

let sessionExpired = false;
function expireSession() {
  if (!sessionExpired) {
    sessionExpired = true;
    window.dispatchEvent(new Event("session-expired"));
    window.location.assign("/login");
  }
}
async function request(path, options = {}) {
  if (sessionExpired) throw new Error("Oturum sona erdi.");
  const method = (options.method || "GET").toUpperCase();
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) headers["X-CSRF-Token"] = csrfToken;
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401) {
    expireSession();
    throw new Error("Oturum sona erdi.");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map((item) => item.msg).join(" · ")
      : data.detail;
    throw new Error(detail || `İstek başarısız oldu (${response.status}).`);
  }
  return data;
}

function formatDate(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("tr-TR", { dateStyle: "short", timeStyle: "medium", timeZone: "Europe/Istanbul" }).format(new Date(value));
}

function outcomeLabel(outcome) {
  return { reply: "Yanıt alındı", no_reply: "Yanıt alınamadı", error: "Kontrol hatası" }[outcome] || outcome;
}

function auditOutcomeLabel(outcome) {
  return { success: "Başarılı", failure: "Başarısız", denied: "Reddedildi" }[outcome] || outcome;
}

function makeBadge(label, variant) {
  const badge = document.createElement("span");
  badge.className = `badge ${variant}`;
  badge.textContent = label;
  return badge;
}

function makeButton(label, variant, handler, disabled = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `button ${variant}`;
  button.textContent = label;
  button.disabled = disabled;
  button.addEventListener("click", handler);
  return button;
}

function latestCheckText(check) {
  if (!check) return "Güncel ölçüm yok · Henüz kontrol edilmedi.";
  if (check.probe_mode !== mode || !check.is_current) return "Güncel ölçüm yok";
  const stale = Date.now() - new Date(check.checked_at).getTime() > freshnessSeconds * 1000;
  const latency = check.latency_ms === null ? "" : ` · ${check.latency_ms} ms`;
  const simulation = check.probe_mode === "mock" ? " · simülasyon" : "";
  return `${stale ? "Güncel ölçüm yok · " : ""}Son kontrol: ${outcomeLabel(check.outcome)}${latency}${simulation} · ${formatDate(check.checked_at)}`;
}

function renderDevices() {
  devicesList.replaceChildren();
  if (!state.devices.length) {
    devicesStatus.textContent = isAdmin
      ? "Henüz cihaz yok. Formdan ilk cihazı ekleyin."
      : "Henüz görüntülenecek cihaz yok.";
    return;
  }
  devicesStatus.textContent = `${state.devices.length} cihaz gösteriliyor.`;
  for (const device of state.devices) {
    const card = document.createElement("article");
    card.className = `device-card${device.is_active ? "" : " inactive"}`;
    const summary = document.createElement("div");
    const title = document.createElement("div");
    title.className = "device-title";
    const name = document.createElement("strong");
    name.textContent = device.name;
    title.append(name, makeBadge(device.is_active ? "Aktif" : "Pasif", "neutral"));
    if (mode === "mock") title.append(makeBadge("MOCK", "mock"));
    const meta = document.createElement("p");
    meta.className = "device-meta";
    meta.textContent = [device.ip_address, device.device_type, device.location].filter(Boolean).join(" · ");
    const last = document.createElement("p");
    last.className = "last-check";
    last.textContent = latestCheckText(state.latestChecks.get(device.id));
    summary.append(title, meta, last);
    const actions = document.createElement("div");
    actions.className = "actions";
    actions.append(makeButton("Geçmiş", "secondary", () => loadHistory(device.id)));
    if (isAdmin) {
      actions.append(
        makeButton("Düzenle", "secondary", () => openEdit(device)),
        makeButton("Şimdi kontrol et", "primary", (event) => runCheck(device, event.currentTarget), !device.is_active),
      );
      if (device.is_active) actions.append(makeButton("Pasife al", "danger", () => deactivate(device)));
    }
    card.append(summary, actions);
    devicesList.append(card);
  }
}

async function loadDevices() {
  devicesStatus.className = "state-message";
  devicesStatus.textContent = "Cihazlar yükleniyor…";
  devicesList.replaceChildren();
  try {
    const page = await request("/api/devices?limit=50&offset=0");
    state.devices = page.items;
    const latest = await Promise.all(state.devices.map(async (device) => {
      const checks = await request(`/api/devices/${device.id}/checks?limit=1&offset=0`);
      const latest = checks.items[0];
      return [device.id, latest?.target_version === device.target_version ? latest : null];
    }));
    state.latestChecks = new Map(latest);
    renderDevices();
    if (state.selectedDeviceId) await loadHistory(state.selectedDeviceId);
  } catch (error) {
    devicesStatus.className = "state-message error";
    devicesStatus.textContent = `Cihazlar alınamadı: ${error.message}`;
  }
}

async function loadHistory(deviceId) {
  window.selectReportDevice?.(deviceId);
  state.selectedDeviceId = deviceId;
  const device = state.devices.find((item) => item.id === deviceId);
  historyStatus.className = "state-message";
  historyStatus.textContent = "Kontrol geçmişi yükleniyor…";
  historyList.replaceChildren();
  try {
    const page = await request(`/api/devices/${deviceId}/checks?limit=20&offset=0`);
    document.querySelector("#detail-title").textContent = `${device?.name || "Cihaz"} · Kontrol geçmişi`;
    if (!page.items.length) {
      historyStatus.textContent = "Bu cihaz için henüz kontrol sonucu yok.";
      return;
    }
    historyStatus.textContent = `En yeni ${page.items.length} sonuç gösteriliyor.`;
    for (const check of page.items) {
      const row = document.createElement("div");
      row.className = "history-row";
      const result = document.createElement("div");
      result.append(makeBadge(outcomeLabel(check.outcome), check.outcome));
      if (check.probe_mode === "mock") result.append(" ", makeBadge("MOCK", "mock"));
      const detail = document.createElement("span");
      detail.className = "history-detail";
      detail.textContent = check.error_message || (check.latency_ms === null ? `Hedef: ${check.target_ip}` : `${check.target_ip} · ${check.latency_ms} ms`);
      const time = document.createElement("time");
      time.dateTime = check.checked_at;
      time.textContent = formatDate(check.checked_at);
      row.append(result, detail, time);
      historyList.append(row);
    }
  } catch (error) {
    historyStatus.className = "state-message error";
    historyStatus.textContent = `Geçmiş alınamadı: ${error.message}`;
  }
}

async function runCheck(device, button) {
  button.disabled = true;
  button.textContent = "Kontrol ediliyor…";
  try {
    const result = await request(`/api/devices/${device.id}/check`, { method: "POST" });
    state.latestChecks.set(device.id, result);
    renderDevices();
    await loadHistory(device.id);
    await Promise.all([loadOverview(), loadAlarms()]);
    await loadAuditLogs();
  } catch (error) {
    devicesStatus.className = "state-message error";
    devicesStatus.textContent = `Kontrol başlatılamadı: ${error.message}`;
    button.disabled = false;
    button.textContent = "Şimdi kontrol et";
  }
}

function formPayload(form, includeActive = false) {
  const values = new FormData(form);
  const payload = {
    name: values.get("name"), ip_address: values.get("ip_address"),
    device_type: values.get("device_type") || null, location: values.get("location") || null,
    description: values.get("description") || null,
  };
  if (includeActive) payload.is_active = values.get("is_active") === "on";
  return payload;
}

function setFormMessage(selector, text, variant = "") {
  const message = document.querySelector(selector);
  message.className = `form-message ${variant}`.trim();
  message.textContent = text;
}

if (addForm) addForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = addForm.querySelector("button[type='submit']");
  button.disabled = true;
  setFormMessage("#add-message", "Kaydediliyor…");
  try {
    await request("/api/devices", { method: "POST", body: JSON.stringify(formPayload(addForm)) });
    addForm.reset();
    setFormMessage("#add-message", "Cihaz kaydedildi.", "success");
    await loadDevices();
    await loadAuditLogs();
  } catch (error) {
    setFormMessage("#add-message", error.message, "error");
  } finally { button.disabled = false; }
});

function openEdit(device) {
  state.selectedDeviceId = device.id;
  editForm.elements.name.value = device.name;
  editForm.elements.ip_address.value = device.ip_address;
  editForm.elements.device_type.value = device.device_type || "";
  editForm.elements.location.value = device.location || "";
  editForm.elements.description.value = device.description || "";
  editForm.elements.is_active.checked = device.is_active;
  setFormMessage("#edit-message", "");
  editPanel.classList.remove("hidden");
  editPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

if (editForm) editForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = editForm.querySelector("button[type='submit']");
  button.disabled = true;
  try {
    await request(`/api/devices/${state.selectedDeviceId}`, { method: "PATCH", body: JSON.stringify(formPayload(editForm, true)) });
    setFormMessage("#edit-message", "Değişiklikler kaydedildi.", "success");
    await loadDevices();
    await loadAuditLogs();
  } catch (error) {
    setFormMessage("#edit-message", error.message, "error");
  } finally { button.disabled = false; }
});

async function deactivate(device) {
  if (!window.confirm(`${device.name} pasife alınsın mı? Kontrol geçmişi korunacaktır.`)) return;
  try {
    await request(`/api/devices/${device.id}`, { method: "PATCH", body: JSON.stringify({ is_active: false }) });
    await loadDevices();
    await loadAuditLogs();
  } catch (error) {
    devicesStatus.className = "state-message error";
    devicesStatus.textContent = `Cihaz pasife alınamadı: ${error.message}`;
  }
}

async function loadAuditLogs() {
  if (!isAdmin) return;
  auditStatus.className = "state-message";
  auditStatus.textContent = "Denetim kayıtları yükleniyor…";
  auditList.replaceChildren();
  try {
    const page = await request("/api/audit-logs?limit=50&offset=0");
    auditStatus.textContent = page.items.length ? `En yeni ${page.items.length} kayıt gösteriliyor.` : "Henüz denetim kaydı yok.";
    for (const log of page.items) {
      const row = document.createElement("div");
      row.className = "history-row audit-row";
      const result = document.createElement("div");
      result.append(makeBadge(auditOutcomeLabel(log.outcome), log.outcome === "success" ? "reply" : "error"));
      const detail = document.createElement("span");
      detail.className = "history-detail";
      detail.textContent = `${log.action} · ${log.actor_username || "anonim"} · ${log.target_type || "—"} ${log.target_id || ""}`.trim();
      const time = document.createElement("time");
      time.dateTime = log.occurred_at;
      time.textContent = formatDate(log.occurred_at);
      row.append(result, detail, time);
      auditList.append(row);
    }
  } catch (error) {
    auditStatus.className = "state-message error";
    auditStatus.textContent = `Denetim kayıtları alınamadı: ${error.message}`;
  }
}

document.querySelector("#refresh-button").addEventListener("click", loadDevices);
document.querySelector("#cancel-edit")?.addEventListener("click", () => editPanel.classList.add("hidden"));
document.querySelector("#audit-refresh-button")?.addEventListener("click", loadAuditLogs);
let freshnessSeconds = 120;
loadDevices();
loadAuditLogs();

let alarmOffset = 0;
let selectedAlarmId = null;
const alarmStatusLabel = (value) => ({open: "Açık", resolved: "Çözüldü", closed: "İdari kapalı"}[value]);

async function loadOverview() {
  const data = await request("/api/summary");
  freshnessSeconds = data.interval_seconds * 2;
  window.renderOperationsHealth?.(data);
  state.latestChecks = new Map(data.devices.map(item => [item.device_id, item.latest]));
  renderDevices();
  document.querySelector("#overview-status").textContent =
    `${data.probe_mode.toUpperCase()}${data.probe_mode === "mock" ? " / simülasyon" : ""} · Aktif cihaz: ${data.active_devices} · Açık alarm: ${data.open_alarms} · ${data.running ? "Çalışıyor" : "Duraklatılmış"} · Aralık: ${data.interval_seconds} sn · Son tarama: ${formatDate(data.last_scan_at)}`;
  if (isAdmin) {
    document.querySelector("#monitor-start").disabled = data.running;
    document.querySelector("#monitor-stop").disabled = !data.running;
  }
  document.querySelector("#monitor-message").textContent = data.last_error || "";
  const container = document.querySelector("#overview-devices");
  container.replaceChildren();
  for (const item of data.devices) {
    const row = document.createElement("div");
    row.className = "history-row";
    const name = document.createElement("strong");
    name.textContent = item.name;
    const detail = document.createElement("span");
    detail.textContent = `${item.monitoring_state === "paused" ? "İzleme yönetici tarafından duraklatıldı · " : ""}${item.is_active ? "" : "Pasif · "}${item.fresh ? outcomeLabel(item.status) : "Yeni ölçüm gelmiyor / güncel ölçüm yok"} · Son sonuç: ${item.latest ? outcomeLabel(item.latest.outcome) : "—"} · ${formatDate(item.latest?.checked_at)}${item.latest?.error_message ? " · " + item.latest.error_message : ""}`;
    row.append(name, detail, makeButton("Cihaz detayı / geçmiş", "secondary", () => {
      loadHistory(item.device_id);
      document.querySelector("#detail-panel").scrollIntoView({behavior: "smooth"});
    }));
    container.append(row);
  }
}

async function showAlarm(id) {
  selectedAlarmId = id;
  const alarm = await request(`/api/alarms/${id}`);
  window.selectOperationsAlarm?.(alarm);
  const detail = document.querySelector("#alarm-detail");
  detail.replaceChildren();
  const heading = document.createElement("h3");
  heading.textContent = `Alarm #${alarm.id} · ${alarm.alarm_type} · ${alarm.probe_mode.toUpperCase()}`;
  const text = document.createElement("p");
  const reasons = {reply_received: "Yanıt alındı", target_changed: "Hedef IP değişti", device_deactivated: "Cihaz pasife alındı"};
  text.textContent = `Hedef: ${alarm.target_ip} · ${alarmStatusLabel(alarm.status)} · İlk yanıtsızlık: ${formatDate(alarm.first_no_reply_at)} · Açılış: ${formatDate(alarm.opened_at)} · Son ilgili gözlem: ${formatDate(alarm.last_observed_at)} · Kapanış: ${formatDate(alarm.ended_at)} · Gerekçe: ${reasons[alarm.end_reason] || "—"} · Görüldü: ${formatDate(alarm.acknowledged_at)} · Kullanıcı ID: ${alarm.acknowledged_by || "—"}`;
  detail.append(heading, text, makeButton("Cihaz geçmişi", "secondary", () => loadHistory(alarm.device_id)));
  if (isAdmin && !alarm.acknowledged_at) detail.append(makeButton("Görüldü", "primary", async () => {
    try {
      await request(`/api/alarms/${id}/acknowledge`, {method: "POST"});
      await loadAlarms();
      await showAlarm(id);
    } catch (error) { document.querySelector("#alarms-status").textContent = error.message; }
  }));
}

async function loadAlarms() {
  const params = new URLSearchParams({limit: "10", offset: String(alarmOffset), probe_mode: document.querySelector("#alarm-mode-filter").value});
  const status = document.querySelector("#alarm-status-filter").value;
  if (status) params.set("status", status);
  const data = await request(`/api/alarms?${params}`);
  document.querySelector("#alarms-status").textContent = data.total ? `${data.total} alarm · ${alarmOffset + 1}–${alarmOffset + data.items.length}` : "Henüz alarm yok.";
  document.querySelector("#alarms-prev").disabled = alarmOffset === 0;
  document.querySelector("#alarms-next").disabled = alarmOffset + data.items.length >= data.total;
  const container = document.querySelector("#alarms-list");
  container.replaceChildren();
  for (const alarm of data.items) {
    const row = document.createElement("div");
    row.className = "history-row";
    const text = document.createElement("span");
    text.textContent = `#${alarm.id} · ${alarm.target_ip} · ${alarm.probe_mode.toUpperCase()} · ${alarmStatusLabel(alarm.status)} · ${alarm.acknowledged_at ? "Görüldü" : "Görülmedi"} · ${alarm.in_maintenance ? "Bakımda · " : ""}${alarm.silenced ? "Susturuldu · " : ""}${formatDate(alarm.opened_at)}`;
    row.append(text, makeButton("Alarm detayı", "secondary", () => showAlarm(alarm.id).catch(showPanelError)));
    container.append(row);
  }
  if (selectedAlarmId) await showAlarm(selectedAlarmId);
}

function showPanelError(error) {
  document.querySelector("#monitor-message").textContent = `Veri yenilenemedi: ${error.message}`;
}
for (const operation of ["start", "stop"]) document.querySelector(`#monitor-${operation}`)?.addEventListener("click", async () => {
  try {
    await request(`/api/monitoring/${operation}`, {method: "POST"});
    await loadOverview();
  } catch (error) { showPanelError(error); }
});
for (const selector of ["#alarm-status-filter", "#alarm-mode-filter"]) document.querySelector(selector).addEventListener("change", () => {
  alarmOffset = 0;
  loadAlarms().catch(showPanelError);
});
document.querySelector("#alarms-prev").addEventListener("click", () => { alarmOffset = Math.max(0, alarmOffset - 10); loadAlarms().catch(showPanelError); });
document.querySelector("#alarms-next").addEventListener("click", () => { alarmOffset += 10; loadAlarms().catch(showPanelError); });
async function refreshPanels() {
  try { await Promise.all([loadOverview(), loadAlarms()]); }
  catch (error) { showPanelError(error); }
  finally { if (!sessionExpired) window.setTimeout(refreshPanels, 5000); }
}
refreshPanels();
