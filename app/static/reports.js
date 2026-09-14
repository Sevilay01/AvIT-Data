"use strict";

(() => {
  const el = (id) => document.getElementById(id);
  const zone = "Europe/Istanbul";
  const timeFormat = new Intl.DateTimeFormat("tr-TR", {
    timeZone: zone, dateStyle: "short", timeStyle: "medium",
  });
  const partsFormat = new Intl.DateTimeFormat("en-CA", {
    timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  });
  const numberFormat = new Intl.NumberFormat("tr-TR", {maximumFractionDigits: 3});
  const sourceLabel = (source) => ({manual: "Manuel", scheduled: "Zamanlanmış", all: "Tümü"}[source]);
  const dateText = (value) => value ? `${timeFormat.format(new Date(value))} (${zone})` : "—";
  const numberText = (value) => value === null ? "—" : numberFormat.format(value);
  let selectedDevice = null;
  let generation = 0;
  let controller = null;
  let downloadController = null;
  let snapshot = null;
  let chart = null;

  function localInput(value) {
    const parts = Object.fromEntries(partsFormat.formatToParts(new Date(value)).map(p => [p.type, p.value]));
    return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}`;
  }

  function inputUTC(value) {
    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?$/.test(value)) throw new Error("Geçerli başlangıç ve bitiş girin.");
    const full = value.length === 16 ? `${value}:00` : value;
    const desired = Date.parse(`${full}Z`);
    let guess = desired;
    for (let i = 0; i < 3; i++) guess += desired - Date.parse(`${localInput(guess)}Z`);
    if (!Number.isFinite(guess) || localInput(guess) !== full) throw new Error("Europe/Istanbul için geçersiz yerel saat.");
    return new Date(guess).toISOString();
  }

  function invalidate() {
    generation++;
    controller?.abort();
    downloadController?.abort();
    snapshot = null;
    el("report-csv").disabled = true;
    el("report-csv-status").textContent = "";
    el("report-summary").textContent = "";
    el("report-range").textContent = "";
    el("report-coverage").textContent = "";
    el("report-rows").replaceChildren();
    chart?.destroy();
    chart = null;
  }

  function readFilters() {
    const period = el("report-period").value;
    let start, end;
    if (period === "custom") {
      start = inputUTC(el("report-start").value);
      end = inputUTC(el("report-end").value);
    } else {
      const endMs = Math.floor(Date.now() / 1000) * 1000;
      end = new Date(endMs).toISOString();
      start = new Date(endMs - Number(period) * 3600000).toISOString();
    }
    el("report-start").value = localInput(start);
    el("report-end").value = localInput(end);
    return {start, end, probe_mode: el("report-mode").value, source: el("report-source").value};
  }

  function draw(data) {
    const graph = data.graph;
    const points = graph.points.map(point => ({...point, x: Date.parse(point.checked_at), y: point.latency_ms}));
    const firstTime = Date.parse(graph.first_checked_at || data.filters.start);
    const lastTime = Date.parse(graph.last_checked_at || data.filters.end);
    if (typeof Chart === "undefined") throw new Error("Yerel Chart.js dosyası yüklenemedi.");
    chart = new Chart(el("latency-chart"), {
      type: "line",
      data: {datasets: [{
        label: `${data.filters.probe_mode.toUpperCase()} · RTT (ms)`, data: points,
        borderColor: "#0b5c5a", backgroundColor: "#0b5c5a", borderWidth: 2,
        pointRadius: graph.shown_count > 500 ? 2 : 4, pointHitRadius: 8,
        spanGaps: false, tension: 0,
        segment: {borderColor: (context) => {
          const first = points[context.p0DataIndex];
          const second = points[context.p1DataIndex];
          const separated = first.target_ip !== second.target_ip
            || first.target_version !== second.target_version
            || first.is_current !== second.is_current
            || second.x - first.x > graph.gap_threshold_seconds * 1000;
          return separated ? "transparent" : "#0b5c5a";
        }},
      }]},
      options: {
        responsive: true, maintainAspectRatio: false, animation: false, parsing: false,
        scales: {
          x: {type: "linear", min: firstTime === lastTime ? firstTime - 1000 : firstTime,
            max: firstTime === lastTime ? lastTime + 1000 : lastTime,
            title: {display: true, text: `Ölçüm zamanı · ${zone}`},
            ticks: {maxTicksLimit: 6, callback: value => timeFormat.format(new Date(value))}},
          y: {beginAtZero: true, title: {display: true, text: "RTT (ms)"}},
        },
        plugins: {tooltip: {callbacks: {
          title: items => dateText(items[0].raw.checked_at),
          label: item => {
            const point = item.raw;
            return [`Hedef IP: ${point.target_ip}`, `Sonuç: ${outcomeLabel(point.outcome)}`,
              `Mod: ${point.probe_mode.toUpperCase()}`, `Kaynak: ${sourceLabel(point.source)}`,
              `RTT: ${numberText(point.latency_ms)} ms`];
          },
        }}},
      },
    });
  }

  function showReport(data) {
    el("report-device").textContent = `${data.device.name} · Cihaz #${data.device.id}${data.device.is_active ? "" : " · Pasif (geçmiş)"}`;
    el("report-range").textContent = `${data.filters.probe_mode.toUpperCase()}${data.filters.probe_mode === "mock" ? " / SİMÜLASYON" : ""} · ${sourceLabel(data.filters.source)} · ${dateText(data.filters.start)} dahil → ${dateText(data.filters.end)} hariç`;
    const summary = data.summary;
    el("report-summary").textContent = `Seçili dönemde saklanan ölçümler: Toplam kontrol ${summary.total} · Yanıt ${summary.reply} · Yanıtsız ${summary.no_reply} · Kontrol hatası ${summary.error} · RTT bulunan başarılı ölçüm ${summary.rtt_count} · Ortalama ${numberText(summary.avg_rtt_ms)} ms · Min ${numberText(summary.min_rtt_ms)} ms · Maks ${numberText(summary.max_rtt_ms)} ms · Ölçümler içindeki yanıt oranı ${summary.response_rate === null ? "—" : "%" + numberText(summary.response_rate)}. ${data.data_scope.notice}`;
    const graph = data.graph;
    el("report-coverage").textContent = `${graph.truncated ? "Grafikte en yeni 2.000 ölçüm gösteriliyor. " : ""}Toplam ${graph.total_count}, gösterilen ${graph.shown_count} (üst sınır ${graph.limit}). Grafiğin gerçek ölçüm aralığı: ${dateText(graph.first_checked_at)} → ${dateText(graph.last_checked_at)}. Çizgi boşluğu eşiği: ${graph.gap_threshold_seconds} saniye.`;
    for (const point of graph.points.slice(-20)) {
      const row = document.createElement("tr");
      for (const value of [dateText(point.checked_at), point.target_ip, outcomeLabel(point.outcome),
        point.probe_mode.toUpperCase(), sourceLabel(point.source), numberText(point.latency_ms)]) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.append(cell);
      }
      el("report-rows").append(row);
    }
    draw(data);
    const hasRTT = graph.points.some(point => point.latency_ms !== null);
    el("report-status").textContent = !graph.shown_count ? "Seçili filtrelerde ölçüm yok. CSV yalnızca başlık içerir."
      : !hasRTT ? "Bu ölçümlerde çizilebilir RTT yok; sonuçları ayrıntı tablosunda inceleyin."
        : graph.shown_count === 1 ? "Tek ölçüm bir nokta olarak gösteriliyor." : "Rapor hazır.";
  }

  async function loadReport() {
    invalidate();
    if (selectedDevice === null || sessionExpired) return;
    const ticket = generation;
    controller = new AbortController();
    el("report-status").textContent = "Ölçüm raporu yükleniyor…";
    try {
      const filters = readFilters();
      const data = await request(`/api/devices/${selectedDevice}/metrics?${new URLSearchParams(filters)}`, {signal: controller.signal});
      if (ticket !== generation || sessionExpired) return;
      showReport(data);
      snapshot = Object.freeze({deviceId: selectedDevice, filters: Object.freeze({...data.filters})});
      el("report-csv").disabled = false;
    } catch (error) {
      if (ticket === generation && error.name !== "AbortError") el("report-status").textContent = `Rapor alınamadı: ${error.message}`;
    }
  }

  window.selectReportDevice = (deviceId) => {
    if (deviceId === selectedDevice) return;
    selectedDevice = deviceId;
    el("report-fields").disabled = false;
    el("report-device").textContent = `Cihaz #${deviceId}`;
    loadReport();
  };
  el("report-form").addEventListener("submit", event => { event.preventDefault(); loadReport(); });
  el("report-form").addEventListener("change", () => {
    invalidate();
    el("report-custom").classList.toggle("hidden", el("report-period").value !== "custom");
    el("report-status").textContent = "Filtre değişti. Raporu görmek için Filtreleri uygula seçin.";
  });
  // Editing a date must invalidate even before blur/change fires.
  for (const id of ["report-start", "report-end"]) el(id).addEventListener("input", invalidate);
  el("report-csv").addEventListener("click", async () => {
    if (!snapshot || sessionExpired) return;
    const selected = snapshot;
    const ticket = generation;
    downloadController?.abort();
    downloadController = new AbortController();
    el("report-csv").disabled = true;
    el("report-csv-status").textContent = "CSV hazırlanıyor…";
    try {
      // Use the displayed concrete interval; never recalculate a relative period here.
      const response = await fetch(`/api/devices/${selected.deviceId}/measurements.csv?${new URLSearchParams(selected.filters)}`, {signal: downloadController.signal});
      if (response.status === 401) { expireSession(); return; }
      if (!response.ok) {
        const error = await response.json();
        throw new Error(typeof error.detail === "string" ? error.detail : "CSV alınamadı.");
      }
      const blob = await response.blob();
      if (ticket !== generation || sessionExpired) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      const filename = response.headers.get("Content-Disposition")?.match(/filename="([a-zA-Z0-9.-]+)"/);
      link.download = filename?.[1] || `device-${selected.deviceId}-measurements.csv`;
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      el("report-csv-status").textContent = "CSV hazırlandı; indirme tarayıcıya iletildi.";
    } catch (error) {
      if (ticket === generation && error.name !== "AbortError") el("report-csv-status").textContent = error.message;
    } finally {
      if (ticket === generation && !sessionExpired) el("report-csv").disabled = !snapshot;
    }
  });
  window.addEventListener("session-expired", invalidate);
  window.addEventListener("pagehide", invalidate);
})();
