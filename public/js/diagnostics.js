import { relativeAge } from "./format.js";
import { badge, element, emptyState } from "./ui.js";

function refillRunMessage(refill) {
  const active = refill.active_run;
  if (active) {
    const amount = Number(active.authorized_transfer_ml || active.planned_transfer_ml || 0);
    const liters = (amount / 1000).toLocaleString("de-DE", { maximumFractionDigits: 2 });
    const state = active.status === "running" ? "läuft" : "ist reserviert";
    const startedAt = active.started_at || active.authorized_at;
    const elapsed = Math.max(0, Number(active.elapsed_seconds || 0));
    const elapsedText = elapsed >= 60
      ? `${Math.floor(elapsed / 60)} min`
      : `${elapsed} s`;
    const limits = {
      refill_tank: "durch Vorrat begrenzt",
      main_tank_capacity: "durch Tankkapazität begrenzt",
      window_remaining: "durch Fensterrestzeit begrenzt",
    };
    const limitText = (active.limit_reasons || [])
      .map((reason) => limits[reason] || reason)
      .join(", ");
    return `Nachfülllauf ${state}: ${liters} l. Start ${relativeAge(startedAt)}, ${elapsedText} verstrichen. Abschluss erwartet ${relativeAge(active.expected_complete_at)}${limitText ? `, ${limitText}` : ""}.`;
  }
  const uncertain = refill.uncertain_runs?.[0];
  if (uncertain) {
    const note = uncertain.consistency_note || uncertain.error_text || "Abschlussmeldung fehlt.";
    return `${note} Tankstände manuell prüfen.`;
  }
  return refill.summary || refill.blocked_reason || "Bereit";
}

export function buildDiagnosticRows(state, evaluation, diagnostics, updater) {
  const weather = {
    ...(state?.weather_status || {}),
    ...(diagnostics?.weather || {}),
  };
  const ha = state?.home_assistant || {};
  const smtp = diagnostics?.smtp || state?.notifications || {};
  const refill = evaluation?.refill || {};
  const automation = evaluation?.automation || {};
  const updaterError = updater?.error;
  return [
    {
      id: "weather",
      label: "Wetterdienst",
      status: weather.cache_fallback
        ? "warning"
        : weather.last_error
          ? "danger"
          : weather.last_successful_fetch_at
            ? "success"
            : "danger",
      lastContact: weather.last_successful_fetch_at,
      message: weather.cache_fallback
        ? `${weather.last_error || "Wetterdienst nicht erreichbar"} Letzte gültige Daten werden verwendet.`
        : weather.last_error || (weather.last_successful_fetch_at ? "Open-Meteo erreichbar" : "Noch kein erfolgreicher Abruf"),
      action: "reload-weather",
      actionLabel: "Neu laden",
    },
    {
      id: "weather-age",
      label: "Datenalter",
      status: weather.stale || weather.cache_fallback ? "warning" : weather.last_successful_fetch_at ? "success" : "danger",
      lastContact: weather.last_successful_fetch_at,
      message: weather.stale
        ? `Älter als ${weather.stale_after_minutes || 0} Minuten`
        : weather.cache_fallback
          ? "Letzte gültige Daten"
          : "Aktuell",
      action: "reload-weather",
      actionLabel: "Aktualisieren",
    },
    {
      id: "home-assistant",
      label: "Home Assistant",
      status: !ha.configured ? "warning" : ha.last_error ? "danger" : "success",
      lastContact: ha.last_successful_contact_at,
      message: !ha.configured ? "Nicht konfiguriert" : ha.last_error || "Bereit",
      action: "test-ha",
      actionLabel: "Testen",
    },
    {
      id: "watering",
      label: "Bewässerungsautomatik",
      status: automation.paused ? "warning" : evaluation ? "success" : "danger",
      lastContact: evaluation?.calculated_at,
      message: automation.paused ? "Pausiert" : automation.summary || "Keine aktuelle Auswertung",
      action: "toggle-automation",
      actionLabel: automation.paused ? "Fortsetzen" : "Pausieren",
    },
    {
      id: "refill",
      label: "Nachfüllautomatik",
      status: refill.manual_review_required || refill.severity === "critical"
        ? "danger"
        : refill.active_run || refill.severity === "warning" || refill.status === "disabled"
          ? "warning"
          : "success",
      lastContact: refill.active_run?.authorized_at
        || refill.last_successful_run?.completed_at
        || refill.last_failed_run?.completed_at
        || refill.last_event?.ran_at,
      message: refillRunMessage(refill),
      action: refill.manual_review_required
        ? "reconcile-refill"
        : refill.active_run
          ? ""
          : "manual-refill",
      actionPayload: refill.uncertain_runs?.[0] || null,
      actionLabel: refill.manual_review_required ? "Auflösen" : "Nachfüllen",
    },
    {
      id: "smtp",
      label: "SMTP / E-Mail",
      status: smtp.enabled && smtp.configured ? "success" : smtp.configuration_error ? "danger" : "warning",
      lastContact: diagnostics?.notification_log?.find((item) => item.status === "sent")?.sent_at,
      message: smtp.configuration_error || (smtp.enabled ? (smtp.configured ? "Bereit" : "Unvollständig konfiguriert") : "Deaktiviert"),
      action: "test-email",
      actionLabel: "Test-E-Mail",
    },
    {
      id: "database",
      label: "Datenbank",
      status: state ? "success" : "danger",
      lastContact: evaluation?.calculated_at,
      message: state ? "Lesen und Schreiben bereit" : "Nicht erreichbar",
      action: "",
    },
    {
      id: "updater",
      label: "Updater",
      status: updaterError ? "danger" : updater?.configured ? "success" : "warning",
      lastContact: updater?.checkedAt || updater?.updatedAt,
      message: updaterError ? "Nicht erreichbar" : updater?.configured ? "Verbunden" : "Nicht eingerichtet",
      action: "open-updater",
      actionLabel: "Öffnen",
    },
  ];
}

function statusText(status) {
  return { success: "Bereit", warning: "Prüfen", danger: "Fehler" }[status] || "Unbekannt";
}

export function renderDiagnostics(state, evaluation, diagnostics, updater, actions = {}) {
  const rows = buildDiagnosticRows(state, evaluation, diagnostics, updater);
  const container = document.getElementById("diagnosticList");
  container.replaceChildren(...rows.map((row) => {
    const action = row.action && actions[row.action]
      ? element("button", { className: "secondary compact-command", type: "button", text: row.actionLabel })
      : badge(statusText(row.status), row.status);
    if (row.action && actions[row.action]) {
      action.addEventListener(
        "click",
        () => actions[row.action](row.actionPayload),
      );
    }
    return element("article", { className: "diagnostic-row" }, [
      element("span", { className: `status-dot ${row.status}`, attrs: { "aria-label": statusText(row.status), role: "img" } }),
      element("div", {}, [element("h2", { text: row.label }), badge(statusText(row.status), row.status)]),
      element("div", { className: "diagnostic-copy" }, [
        element("p", { text: row.message }),
        element("p", { text: row.lastContact ? `Letzter Kontakt: ${relativeAge(row.lastContact)}` : "Noch kein Kontakt" }),
      ]),
      action,
    ]);
  }));
  renderNotificationLog(diagnostics?.notification_log || []);
}

export function renderNotificationLog(entries) {
  const container = document.getElementById("notificationLog");
  if (!entries.length) {
    container.replaceChildren(emptyState("Noch keine Benachrichtigungen protokolliert."));
    return;
  }
  container.replaceChildren(...entries.map((entry) => {
    const tone = entry.status === "failed" ? "danger" : entry.event_state === "resolved" ? "success" : entry.severity === "critical" ? "danger" : entry.severity === "warning" ? "warning" : "info";
    return element("article", { className: "notification-item" }, [
      badge(entry.event_state === "resolved" ? "Entwarnung" : entry.severity === "critical" ? "Kritisch" : entry.severity === "warning" ? "Warnung" : "Info", tone),
      element("div", {}, [
        element("strong", { text: entry.subject || entry.alert_key }),
        element("p", { text: entry.error || entry.message || "" }),
      ]),
      element("time", { text: relativeAge(entry.created_at), dateTime: entry.created_at }),
    ]);
  }));
}
