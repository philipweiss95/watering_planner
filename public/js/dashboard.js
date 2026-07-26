import {
  clamp,
  dateTime,
  liters,
  percent,
  relativeAge,
  time,
} from "./format.js";
import { badge, element, icon, progress } from "./ui.js";

function nextUnfinishedWindow(evaluation) {
  const automation = evaluation?.automation || {};
  const completed = Number(evaluation?.cycles_completed_today || 0);
  const windows = Array.isArray(automation.windows) ? automation.windows : [];
  return windows[completed] || automation.next_window || "";
}

function localDateKey(value, timezone) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone || "Europe/Berlin",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(value);
  const get = (type) => parts.find((part) => part.type === type)?.value || "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

export function daysUntil(value, now = new Date(), timezone = "Europe/Berlin") {
  const target = new Date(value);
  if (Number.isNaN(target.getTime())) return null;
  const targetDay = Date.parse(`${localDateKey(target, timezone)}T00:00:00Z`);
  const currentDay = Date.parse(`${localDateKey(now, timezone)}T00:00:00Z`);
  return Math.max(0, Math.round((targetDay - currentDay) / 86400000));
}

export function buildTimeline(evaluation, now = new Date()) {
  const automation = evaluation?.automation || {};
  const completed = Number(evaluation?.cycles_completed_today || 0);
  const windows = Array.isArray(automation.windows) ? automation.windows : [];
  const blocked = Boolean(
    evaluation?.tank?.empty_soon
    || automation.paused
    || (!evaluation?.should_run && Number(evaluation?.remaining_cycles_today || 0) > 0),
  );
  const nowTime = now.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
  return windows.map((window, index) => {
    let status = "planned";
    if (index < completed) status = "done";
    else if (blocked) status = "blocked";
    else if (automation.run_now && index === completed) status = "due";
    else if (index === completed && window < nowTime && automation.catch_up) status = "missed";
    return { time: window, status };
  });
}

export function buildManualActionsModel(state, evaluation) {
  const watering = evaluation?.manual_run || {};
  const refill = evaluation?.manual_refill || {};
  const refillState = evaluation?.refill || {};
  const balcony = state?.balcony || {};
  const homeAssistantConfigured = Boolean(
    state?.home_assistant?.configured,
  );
  const plannedRefill = Number(refill.planned_transfer_ml || 0);
  const refillAvailable = typeof refill.available === "boolean"
    ? refill.available
    : Boolean(
      homeAssistantConfigured
      && plannedRefill > 0
      && !refillState.active_run
      && !refillState.manual_review_required
      && !refillState.cooldown_active,
    );
  return [
    {
      id: "manual-watering",
      action: "manual-run",
      icon: "play",
      label: "Jetzt gießen",
      available: Boolean(watering.available),
      reason: watering.reason || (
        evaluation
          ? "Manueller Gießlauf ist derzeit nicht freigegeben."
          : "Tagesplan wird geladen."
      ),
    },
    {
      id: "manual-refill",
      action: "manual-refill",
      icon: "container",
      label: "Tank nachfüllen",
      available: refillAvailable,
      reason: refill.reason || refill.summary || (
        plannedRefill > 0
          ? `${liters(plannedRefill)} sind vorgesehen.`
          : "Der Haupttank benötigt aktuell keine Nachfüllung."
      ),
    },
    {
      id: "fill-main-tank",
      action: "fill-main",
      icon: "droplets",
      label: "Haupttank nachgefüllt",
      available: Number(balcony.tank_capacity_ml || 0) > 0
        && Number(balcony.tank_current_ml || 0)
          < Number(balcony.tank_capacity_ml || 0),
      reason: Number(balcony.tank_capacity_ml || 0) <= 0
        ? "Für den Haupttank ist keine Größe konfiguriert."
        : Number(balcony.tank_current_ml || 0)
            >= Number(balcony.tank_capacity_ml || 0)
          ? "Der Haupttank ist bereits als voll markiert."
          : `Aktuell ${percent(
            Number(balcony.tank_current_ml || 0)
              / Number(balcony.tank_capacity_ml) * 100,
          )} · auf 100 % setzen.`,
    },
    {
      id: "fill-refill-tank",
      action: "fill-refill",
      icon: "container",
      label: "Vorratstank nachgefüllt",
      available: Number(balcony.refill_tank_capacity_ml || 0) > 0
        && Number(balcony.refill_tank_current_ml || 0)
          < Number(balcony.refill_tank_capacity_ml || 0),
      reason: Number(balcony.refill_tank_capacity_ml || 0) <= 0
        ? "Für den Vorratstank ist keine Größe konfiguriert."
        : Number(balcony.refill_tank_current_ml || 0)
            >= Number(balcony.refill_tank_capacity_ml || 0)
          ? "Der Vorratstank ist bereits als voll markiert."
          : `Aktuell ${percent(
            Number(balcony.refill_tank_current_ml || 0)
              / Number(balcony.refill_tank_capacity_ml) * 100,
          )} · auf 100 % setzen.`,
    },
  ];
}

export function buildDashboardModel(state, evaluation, now = new Date()) {
  if (!state) {
    return {
      tone: "neutral",
      icon: "clock",
      kicker: "Nächster Schritt",
      title: "Plan wird geladen",
      meta: "Wetter und Anlage werden geprüft.",
      action: "",
    };
  }
  if (evaluation?.tank?.empty_soon) {
    return {
      tone: "danger", icon: "droplets", kicker: "Handlung nötig",
      title: "Haupttank auffüllen",
      meta: `Für einen vollständigen Lauf werden ${liters(evaluation.pump?.consumed_per_cycle_ml)} benötigt.`,
      action: "fill-main",
    };
  }
  const weather = state.weather_status || {};
  if (!weather.last_successful_fetch_at || weather.stale) {
    return {
      tone: "warning", icon: "sun", kicker: "Handlung nötig",
      title: "Wetterdaten aktualisieren",
      meta: weather.last_successful_fetch_at ? `Letzter Abruf ${relativeAge(weather.last_successful_fetch_at, now)}` : "Noch kein erfolgreicher Wetterabruf",
      action: "reload-weather",
    };
  }
  if (!evaluation) {
    return {
      tone: "danger", icon: "alert", kicker: "Plan nicht verfügbar",
      title: "Wetterdienst prüfen",
      meta: "Der Tagesplan konnte nicht berechnet werden.",
      action: "reload-weather",
    };
  }
  const remaining = Number(evaluation.remaining_cycles_today || 0);
  const total = Number(evaluation.recommended_cycles_today || 0);
  const homeAssistant = state.home_assistant || {};
  const firstUnserved = evaluation.depletion?.first_unserved_watering_at;
  if (homeAssistant.configured && homeAssistant.last_error) {
    return {
      tone: "danger", icon: "activity", kicker: "Handlung nötig",
      title: "Home Assistant prüfen",
      meta: "Die letzte Verbindung ist fehlgeschlagen.",
      action: "test-ha",
    };
  }
  if (evaluation.automation?.run_now) {
    return {
      tone: "info", icon: "play", kicker: "Jetzt fällig",
      title: `Bewässerung ${time(evaluation.automation.active_window || nextUnfinishedWindow(evaluation))}`,
      meta: `${remaining} von ${total} Läufen offen`,
      action: "manual-run",
    };
  }
  const refill = evaluation.refill || {};
  const refillBlocked = Boolean(
    refill.status === "window_missed"
    || (refill.blocked && refill.schedule_due),
  );
  if (evaluation.automation?.paused) {
    return {
      tone: "warning", icon: "pause", kicker: "Automatik pausiert",
      title: "Bewässerung fortsetzen",
      meta: `${remaining} von ${total} Läufen offen`,
      action: "resume-automation",
    };
  }
  if (evaluation.automation?.catch_up && remaining > 0) {
    return {
      tone: "danger", icon: "alert", kicker: "Lauf verpasst",
      title: "Bewässerung prüfen",
      meta: evaluation.automation.summary || `${remaining} Läufe sind noch offen.`,
      action: "manual-run",
    };
  }
  if (refillBlocked) {
    return {
      tone: refill.severity === "critical" ? "danger" : "warning",
      icon: "container",
      kicker: refill.status === "window_missed" ? "Nachfüllung verpasst" : "Nachfüllung blockiert",
      title: refill.status === "refill_tank_empty" ? "Vorratstank auffüllen" : "Nachfüllung prüfen",
      meta: refill.summary || "Der erforderliche Nachfülllauf konnte nicht stattfinden.",
      action: refill.status === "refill_tank_empty" ? "fill-refill" : "",
    };
  }
  const nextWindow = nextUnfinishedWindow(evaluation);
  if (remaining > 0 && nextWindow) {
    return {
      tone: "success", icon: "clock", kicker: "Nächster Lauf",
      title: `Nächster Lauf ${time(nextWindow)}`,
      meta: `${remaining} von ${total} Läufen offen · Kein Eingreifen nötig`,
      action: "",
    };
  }
  const timezone = state?.balcony?.timezone_name || "Europe/Berlin";
  const warningDays = Number(state?.planner_config?.supply_warning_days ?? 3);
  const unservedInDays = firstUnserved ? daysUntil(firstUnserved, now, timezone) : null;
  if (firstUnserved && unservedInDays !== null && unservedInDays <= warningDays) {
    const estimated = Boolean(evaluation.depletion?.first_unserved_is_estimated);
    return {
      tone: "warning", icon: "alert", kicker: estimated ? "Vorausschau · geschätzt" : "Vorausschau",
      title: "Wasservorrat einplanen",
      meta: `Erster nicht versorgbarer Lauf ${dateTime(firstUnserved)}`,
      action: "forecast",
    };
  }
  return {
    tone: "success", icon: "check", kicker: "Heute",
    title: total ? "Tagesplan abgeschlossen" : "Kein Lauf geplant",
    meta: total ? `${total} von ${total} Läufen erledigt` : "Kein Eingreifen nötig",
    action: "",
  };
}

function statusCard(title, value, meta, stateName, progressValue = null) {
  const card = element("article", { className: "status-card" });
  const head = element("div", { className: "status-card-head" }, [
    element("h2", { text: title }),
    badge(stateName === "success" ? "OK" : stateName === "warning" ? "Prüfen" : stateName === "danger" ? "Problem" : "Info", stateName),
  ]);
  card.append(head, element("strong", { className: "status-value", text: value }));
  if (progressValue !== null) card.append(progress(progressValue, stateName));
  card.append(element("p", { className: "status-meta", text: meta }));
  return card;
}

export function buildTankStatusModel(state, evaluation) {
  const balcony = state?.balcony || {};
  const mainCapacity = Math.max(
    1,
    Number(
      balcony.tank_capacity_ml
      || evaluation?.tank?.capacity_ml
      || 1,
    ),
  );
  const mainCurrent = clamp(
    Number(balcony.tank_current_ml || 0),
    0,
    mainCapacity,
  );
  const refillCapacity = Math.max(
    1,
    Number(balcony.refill_tank_capacity_ml || 1),
  );
  const refillCurrent = clamp(
    Number(balcony.refill_tank_current_ml || 0),
    0,
    refillCapacity,
  );
  const mainPercent = mainCurrent / mainCapacity * 100;
  const refillPercent = refillCurrent / refillCapacity * 100;
  const mainTone = evaluation?.tank?.empty_soon
    ? "danger"
    : evaluation?.tank?.low
      ? "warning"
      : "success";
  const refillTone = refillPercent <= 10
    ? "danger"
    : refillPercent <= 25
      ? "warning"
      : "success";
  return {
    tone: mainTone === "danger" || refillTone === "danger"
      ? "danger"
      : mainTone === "warning" || refillTone === "warning"
        ? "warning"
        : "success",
    levels: [
      {
        label: "Haupttank",
        current: mainCurrent,
        capacity: mainCapacity,
        percent: mainPercent,
        tone: mainTone,
      },
      {
        label: "Vorratstank",
        current: refillCurrent,
        capacity: refillCapacity,
        percent: refillPercent,
        tone: refillTone,
      },
    ],
  };
}

function tankStatusCard(state, evaluation) {
  const model = buildTankStatusModel(state, evaluation);
  const card = element("article", {
    className: "status-card tank-status-card",
  });
  card.append(
    element("div", { className: "status-card-head" }, [
      element("h2", { text: "Tankfüllstände" }),
      badge(
        model.tone === "success"
          ? "OK"
          : model.tone === "warning"
            ? "Prüfen"
            : "Problem",
        model.tone,
      ),
    ]),
    element(
      "div",
      { className: "tank-levels" },
      model.levels.map((level) => element(
        "div",
        { className: "tank-level" },
        [
          element("div", { className: "tank-level-heading" }, [
            element("strong", { text: level.label }),
            element("span", { text: percent(level.percent) }),
          ]),
          progress(level.percent, level.tone),
          element("p", {
            text: `${liters(level.current)} von ${liters(level.capacity)}`,
          }),
        ],
      )),
    ),
  );
  return card;
}

export function buildHomeAssistantStatusModel(homeAssistant, now = new Date()) {
  const status = homeAssistant || {};
  if (!status.configured) {
    return {
      tone: "warning",
      value: "Nicht eingerichtet",
      meta: "Keine Home-Assistant-Webhooks konfiguriert.",
    };
  }
  if (status.last_error) {
    return {
      tone: "danger",
      value: "Verbindung fehlgeschlagen",
      meta: status.last_error,
    };
  }
  if (!status.last_successful_contact_at) {
    return {
      tone: "warning",
      value: "Eingerichtet",
      meta: "Noch kein erfolgreicher Verbindungstest · unter System testen",
    };
  }
  return {
    tone: "success",
    value: "Kontakt bestätigt",
    meta: `Zuletzt erfolgreich ${relativeAge(
      status.last_successful_contact_at,
      now,
    )}`,
  };
}

export function renderDashboard(state, evaluation, actions = {}) {
  const model = buildDashboardModel(state, evaluation);
  const card = document.getElementById("nextActionCard");
  card.className = `next-card status-${model.tone === "info" ? "neutral" : model.tone}`;
  document.getElementById("nextActionIcon").replaceChildren(icon(model.icon));
  document.getElementById("nextActionKicker").textContent = model.kicker;
  document.getElementById("nextActionTitle").textContent = model.title;
  document.getElementById("nextActionMeta").textContent = model.meta;
  const buttons = document.getElementById("nextActionButtons");
  buttons.replaceChildren();
  const actionLabels = {
    "fill-main": "Tank auffüllen",
    "fill-refill": "Vorrat auffüllen",
    "reload-weather": "Neu laden",
    "test-ha": "Verbindung testen",
    forecast: "Prognose öffnen",
    "manual-run": "Jetzt starten",
    "resume-automation": "Fortsetzen",
  };
  if (model.action && actions[model.action]) {
    const button = element("button", { className: "primary", type: "button", text: actionLabels[model.action] });
    button.addEventListener("click", actions[model.action]);
    buttons.append(button);
  }

  const timeline = buildTimeline(evaluation);
  const list = document.getElementById("dayTimeline");
  list.replaceChildren(...timeline.map((item, index) => {
    const labels = { done: "Erledigt", due: "Jetzt fällig", planned: "Geplant", missed: "Verpasst", blocked: "Blockiert" };
    return element("li", { className: `timeline-item ${item.status}` }, [
      element("strong", { text: item.time }),
      element("span", { text: `${index + 1}. Lauf · ${labels[item.status]}` }),
    ]);
  }));
  if (!timeline.length) list.append(element("li", { className: "empty-state", text: "Heute sind keine Läufe geplant." }));
  document.getElementById("timelineSummary").textContent =
    `${Number(evaluation?.cycles_completed_today || 0)} erledigt · ${Number(evaluation?.remaining_cycles_today || 0)} offen`;

  const manualContainer = document.getElementById("manualActions");
  manualContainer.replaceChildren(
    ...buildManualActionsModel(state, evaluation).map((item) => {
      const button = element(
        "button",
        {
          className: item.available
            ? "secondary manual-action-button"
            : "secondary manual-action-button",
          type: "button",
          disabled: !item.available,
          attrs: {
            "aria-describedby": `${item.id}-reason`,
            title: item.available ? item.label : item.reason,
          },
        },
        [icon(item.icon), element("span", { text: item.label })],
      );
      if (item.available && actions[item.action]) {
        button.addEventListener("click", actions[item.action]);
      }
      return element("div", { className: "manual-action" }, [
        button,
        element("p", {
          id: `${item.id}-reason`,
          text: item.reason,
        }),
      ]);
    }),
  );

  const weather = evaluation?.weather || {};
  const weatherCurrent = weather.current || {};
  const weatherStatus = state?.weather_status || {};
  const homeAssistant = state?.home_assistant || {};
  const homeAssistantStatus = buildHomeAssistantStatusModel(homeAssistant);
  const cards = [
    tankStatusCard(state, evaluation),
    statusCard(
      weather.simulation ? "Wetter · Simulation" : "Wetter",
      evaluation ? `${Number(weatherCurrent.temperature_c ?? evaluation?.inputs?.temperature_c ?? 0).toFixed(1)} °C` : "Nicht verfügbar",
      evaluation ? `${Number(weatherCurrent.rain_mm ?? evaluation?.inputs?.rain_mm ?? 0).toFixed(1)} mm Regen · ${relativeAge(weatherStatus.last_successful_fetch_at)}` : "Wetterdaten neu laden",
      weather.simulation
        || weatherStatus.stale
        || weatherStatus.cache_fallback
        || weatherStatus.last_error
        ? "warning"
        : "success",
    ),
    statusCard(
      "Reichweite",
      evaluation?.depletion?.last_supported_watering_at
        ? dateTime(evaluation.depletion.last_supported_watering_at)
        : "Keine Grenze",
      evaluation?.depletion?.first_unserved_watering_at ? "Danach fehlt Wasser" : "Im Prognosezeitraum versorgt",
      evaluation?.depletion?.first_unserved_watering_at ? "warning" : "success",
    ),
    statusCard(
      "Home Assistant",
      homeAssistantStatus.value,
      homeAssistantStatus.meta,
      homeAssistantStatus.tone,
    ),
  ];
  document.getElementById("dashboardStatusGrid").replaceChildren(...cards);
  document.getElementById("headerStatus").textContent =
    model.tone === "danger" || model.tone === "warning"
      ? model.title
      : (evaluation?.weather?.simulation ? "Simulation aktiv" : "Anlage bereit");
}
