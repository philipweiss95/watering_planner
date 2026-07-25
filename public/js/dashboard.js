import { dateTime, liters, percent, relativeAge, time } from "./format.js";
import { badge, element, icon, progress } from "./ui.js";

function nextUnfinishedWindow(evaluation) {
  const automation = evaluation?.automation || {};
  const completed = Number(evaluation?.cycles_completed_today || 0);
  const windows = Array.isArray(automation.windows) ? automation.windows : [];
  return windows[completed] || automation.next_window || "";
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

  if (evaluation.refill?.refill_tank?.empty) {
    return {
      tone: "warning", icon: "container", kicker: "Handlung nötig",
      title: "Vorratstank auffüllen",
      meta: "Für automatische Nachfüllungen steht kein Wasser bereit.",
      action: "fill-refill",
    };
  }
  if (homeAssistant.configured && homeAssistant.last_error) {
    return {
      tone: "danger", icon: "activity", kicker: "Handlung nötig",
      title: "Home Assistant prüfen",
      meta: "Die letzte Verbindung ist fehlgeschlagen.",
      action: "test-ha",
    };
  }
  if (firstUnserved) {
    return {
      tone: "warning", icon: "alert", kicker: "Vorausschau",
      title: "Wasservorrat einplanen",
      meta: `Erster nicht versorgbarer Lauf ${dateTime(firstUnserved)}`,
      action: "forecast",
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
  const nextWindow = nextUnfinishedWindow(evaluation);
  if (remaining > 0 && nextWindow) {
    return {
      tone: "success", icon: "clock", kicker: "Nächster Lauf",
      title: `Nächster Lauf ${time(nextWindow)}`,
      meta: `${remaining} von ${total} Läufen offen · Kein Eingreifen nötig`,
      action: "",
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

  const mainCurrent = Number(state?.balcony?.tank_current_ml || 0);
  const mainCapacity = Math.max(1, Number(state?.balcony?.tank_capacity_ml || evaluation?.tank?.capacity_ml || 1));
  const refillCurrent = Number(state?.balcony?.refill_tank_current_ml || 0);
  const refillCapacity = Math.max(1, Number(state?.balcony?.refill_tank_capacity_ml || 1));
  const weather = evaluation?.weather || {};
  const weatherCurrent = weather.current || {};
  const weatherStatus = state?.weather_status || {};
  const homeAssistant = state?.home_assistant || {};
  const cards = [
    statusCard(
      "Tanks",
      `${liters(mainCurrent)} / ${liters(refillCurrent)}`,
      `Haupttank ${percent(mainCurrent / mainCapacity * 100)} · Vorrat ${percent(refillCurrent / refillCapacity * 100)}`,
      evaluation?.tank?.empty_soon ? "danger" : evaluation?.tank?.low ? "warning" : "success",
      mainCurrent / mainCapacity * 100,
    ),
    statusCard(
      weather.simulation ? "Wetter · Simulation" : "Wetter",
      evaluation ? `${Number(weatherCurrent.temperature_c ?? evaluation?.inputs?.temperature_c ?? 0).toFixed(1)} °C` : "Nicht verfügbar",
      evaluation ? `${Number(weatherCurrent.rain_mm ?? evaluation?.inputs?.rain_mm ?? 0).toFixed(1)} mm Regen · ${relativeAge(weatherStatus.last_successful_fetch_at)}` : "Wetterdaten neu laden",
      weather.simulation || weatherStatus.stale ? "warning" : "success",
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
      "Anlage",
      evaluation?.automation?.paused ? "Pausiert" : "Automatik aktiv",
      homeAssistant.configured
        ? (homeAssistant.last_error || `Home Assistant ${relativeAge(homeAssistant.last_successful_contact_at)}`)
        : "Home Assistant nicht konfiguriert",
      homeAssistant.last_error ? "danger" : homeAssistant.configured ? "success" : "warning",
    ),
  ];
  document.getElementById("dashboardStatusGrid").replaceChildren(...cards);
  document.getElementById("headerStatus").textContent =
    model.tone === "danger" || model.tone === "warning"
      ? model.title
      : (evaluation?.weather?.simulation ? "Simulation aktiv" : "Anlage bereit");
}
