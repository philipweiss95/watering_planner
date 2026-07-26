import { api } from "./api.js";
import { number } from "./format.js";
import { element, icon, inlineError, showToast } from "./ui.js";

function minutesFromTime(value) {
  const match = /^(\d{2}):(\d{2})$/.exec(String(value || ""));
  if (!match) return NaN;
  return Number(match[1]) * 60 + Number(match[2]);
}

function timeFromMinutes(value) {
  const minutes = Math.round(value);
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}

export function previewSchedule(start, end, cycles, minimumInterval = 1) {
  const startMinutes = minutesFromTime(start);
  const endMinutes = minutesFromTime(end);
  const count = Math.max(0, Number(cycles) || 0);
  const interval = Math.max(1, Number(minimumInterval) || 1);
  if (!Number.isFinite(startMinutes) || !Number.isFinite(endMinutes) || endMinutes <= startMinutes) {
    return { times: [], error: "Das Zeitfenster muss am selben Tag enden und nach dem Beginn liegen." };
  }
  if (count <= 0) return { times: [], error: "" };
  if (count === 1) return { times: [timeFromMinutes(startMinutes)], error: "" };
  const spacing = (endMinutes - startMinutes) / (count - 1);
  if (spacing < interval) {
    return { times: [], error: "Für diese Anzahl ist der Mindestabstand im Zeitfenster zu groß." };
  }
  return {
    times: Array.from({ length: count }, (_, index) => timeFromMinutes(startMinutes + spacing * index)),
    error: "",
  };
}

export function validateRefillWindows(windows) {
  const normalized = windows.map((window, index) => ({
    ...window,
    index,
    startMinutes: minutesFromTime(window.start),
    endMinutes: minutesFromTime(window.end),
  }));
  for (const window of normalized) {
    if (!Number.isFinite(window.startMinutes) || !Number.isFinite(window.endMinutes) || window.endMinutes <= window.startMinutes) {
      return "Jedes Nachfüllfenster muss am selben Tag enden und nach dem Beginn liegen.";
    }
  }
  normalized.sort((left, right) => left.startMinutes - right.startMinutes);
  for (let index = 1; index < normalized.length; index += 1) {
    if (normalized[index].startMinutes < normalized[index - 1].endMinutes) {
      return "Nachfüllfenster dürfen sich nicht überschneiden.";
    }
  }
  return "";
}

function named(form, name) {
  return form.elements.namedItem(name);
}

function setValue(form, name, value) {
  const field = named(form, name);
  if (!field) return;
  if (field.type === "checkbox") field.checked = Boolean(value);
  else field.value = value ?? "";
}

function refillWindowRow(window = { start: "06:00", end: "07:00" }) {
  const row = element("div", { className: "window-row", dataset: { refillWindow: "true" } });
  const start = element("input", { type: "time", value: window.start || "", required: true, dataset: { field: "start" }, attrs: { "aria-label": "Beginn des Nachfüllfensters" } });
  const end = element("input", { type: "time", value: window.end || "", required: true, dataset: { field: "end" }, attrs: { "aria-label": "Ende des Nachfüllfensters" } });
  const remove = element("button", { className: "icon-button", type: "button", title: "Fenster entfernen", attrs: { "aria-label": "Nachfüllfenster entfernen" } }, icon("trash"));
  remove.addEventListener("click", () => row.remove());
  row.append(
    element("label", {}, [element("span", { text: "Beginn" }), start]),
    element("label", {}, [element("span", { text: "Ende" }), end]),
    remove,
  );
  return row;
}

function outletRow(outlet) {
  const row = element("div", { className: "outlet-row", dataset: { outletId: String(outlet.id) } });
  row.append(
    element("label", {}, [element("span", { text: "Bezeichnung" }), element("input", { value: outlet.name, dataset: { field: "name" }, required: true })]),
    element("label", {}, [element("span", { text: "Menge je Lauf (ml)" }), element("input", { type: "number", min: "0", value: outlet.ml_per_run, dataset: { field: "ml" }, required: true })]),
  );
  return row;
}

function wallRow(wall) {
  const labels = { north: "Nord", south: "Süd", east: "Ost", west: "West" };
  const row = element("div", { className: "wall-row", dataset: { wallSide: wall.side } });
  row.append(
    element("label", {}, [element("span", { text: "Seite" }), element("input", { value: labels[wall.side] || wall.side, disabled: true })]),
    element("label", {}, [element("span", { text: "Höhe (m)" }), element("input", { type: "number", min: "0", step: "0.1", value: wall.height_m, dataset: { field: "height" }, required: true })]),
  );
  return row;
}

function renderBalconyPlan(state, onChanged) {
  const plan = document.getElementById("balconyPlan");
  plan.replaceChildren();
  for (const plant of state.plants || []) {
    const marker = element("button", {
      className: "balcony-plant",
      type: "button",
      text: String(plant.custom_name || "?").slice(0, 2).toUpperCase(),
      title: plant.custom_name,
      attrs: { "aria-label": `${plant.custom_name} verschieben` },
    });
    marker.style.left = `calc(${Math.max(0, Math.min(1, Number(plant.pos_x ?? 0.5))) * 100}% - 22px)`;
    marker.style.top = `calc(${Math.max(0, Math.min(1, Number(plant.pos_y ?? 0.5))) * 100}% - 22px)`;
    marker.addEventListener("pointerdown", (event) => {
      marker.setPointerCapture(event.pointerId);
    });
    marker.addEventListener("pointermove", (event) => {
      if (!marker.hasPointerCapture(event.pointerId)) return;
      const bounds = plan.getBoundingClientRect();
      const x = Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width));
      const y = Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height));
      marker.style.left = `calc(${x * 100}% - 22px)`;
      marker.style.top = `calc(${y * 100}% - 22px)`;
      marker.dataset.x = String(x);
      marker.dataset.y = String(y);
    });
    marker.addEventListener("pointerup", async (event) => {
      marker.releasePointerCapture(event.pointerId);
      if (marker.dataset.x === undefined) return;
      try {
        await api.post(`/api/plants/${plant.id}/position`, {
          pos_x: Number(marker.dataset.x),
          pos_y: Number(marker.dataset.y),
        });
        showToast("Position gespeichert");
        await onChanged({ weather: false, afterMutation: true });
      } catch (error) {
        showToast(error.message, { error: true });
      }
    });
    plan.append(marker);
  }
}

function currentRefillWindows() {
  return [...document.querySelectorAll("[data-refill-window]")].map((row) => ({
    start: row.querySelector("[data-field=start]").value,
    end: row.querySelector("[data-field=end]").value,
  }));
}

function currentOutlets(state) {
  return [...document.querySelectorAll("[data-outlet-id]")].map((row) => ({
    id: Number(row.dataset.outletId),
    name: row.querySelector("[data-field=name]").value.trim(),
    ml_per_run: Number(row.querySelector("[data-field=ml]").value),
    max_connections: state.outlets.find((item) => Number(item.id) === Number(row.dataset.outletId))?.max_connections,
  }));
}

function currentWalls() {
  return [...document.querySelectorAll("[data-wall-side]")].map((row) => ({
    side: row.dataset.wallSide,
    height_m: Number(row.querySelector("[data-field=height]").value),
  }));
}

function plannerConfigFromForm(form, previous) {
  return {
    ...previous,
    watering_window_start: named(form, "watering_window_start").value,
    watering_window_end: named(form, "watering_window_end").value,
    max_cycles_per_day: Number(named(form, "max_cycles_per_day").value),
    watering_min_interval_minutes: Number(named(form, "watering_min_interval_minutes").value),
    refill_windows: currentRefillWindows(),
    refill_min_interval_minutes: Number(named(form, "refill_min_interval_minutes").value),
    refill_strategy: named(form, "refill_strategy").value,
    refill_fraction: Number(named(form, "refill_fraction_percent").value) / 100,
    refill_target_ml: Math.round(Number(named(form, "refill_target_liters").value) * 1000),
    weather_stale_after_minutes: Number(named(form, "weather_stale_after_minutes").value),
    weather_cache_minutes: Number(named(form, "weather_cache_minutes").value),
    missed_watering_tolerance_minutes: Number(named(form, "missed_watering_tolerance_minutes").value),
    notification_cooldown_minutes: Number(named(form, "notification_cooldown_minutes").value),
    notification_retry_minutes: Number(named(form, "notification_retry_minutes").value),
    notification_resolved_enabled: named(form, "notification_resolved_enabled").checked,
    supply_warning_days: Number(named(form, "supply_warning_days").value),
    notification_worker_interval_seconds: Number(named(form, "notification_worker_interval_seconds").value),
  };
}

function renderSchedulePreview(form, evaluation) {
  const maximumCycles = Number(named(form, "max_cycles_per_day").value || 0);
  const maximumCheck = previewSchedule(
    named(form, "watering_window_start").value,
    named(form, "watering_window_end").value,
    maximumCycles,
    named(form, "watering_min_interval_minutes").value,
  );
  const actualCycles = evaluation
    ? Math.min(maximumCycles, Math.max(0, Number(evaluation.recommended_cycles_today || 0)))
    : Math.min(maximumCycles, 4);
  const preview = previewSchedule(
    named(form, "watering_window_start").value,
    named(form, "watering_window_end").value,
    actualCycles,
    named(form, "watering_min_interval_minutes").value,
  );
  const container = document.getElementById("schedulePreview");
  container.replaceChildren();
  const error = maximumCheck.error || preview.error;
  inlineError(container, error);
  if (!error && actualCycles > 0) {
    container.append(element("strong", { text: `${actualCycles} ${actualCycles === 1 ? "Lauf" : "Läufe"}:` }));
    container.append(...preview.times.map((value) => element("span", { className: "schedule-time", text: value })));
  } else if (!error) {
    container.append(element("span", { text: "Heute entstehen keine Gießzeitpunkte." }));
  }
  return { ...preview, error };
}

export function renderSettings(state, evaluation, onChanged = async () => {}) {
  const form = document.getElementById("settingsForm");
  const config = state.planner_config || {};
  const balcony = state.balcony || {};
  const saved = state.settings || {};
  [
    ["watering_amount_percent", saved.watering_amount_percent],
    ["watering_window_start", config.watering_window_start],
    ["watering_window_end", config.watering_window_end],
    ["max_cycles_per_day", config.max_cycles_per_day],
    ["watering_min_interval_minutes", config.watering_min_interval_minutes],
    ["tank_capacity_liters", Number(balcony.tank_capacity_ml || 0) / 1000],
    ["refill_tank_capacity_liters", Number(balcony.refill_tank_capacity_ml || 0) / 1000],
    ["refill_automation_enabled", saved.refill_automation_enabled],
    ["refill_min_interval_minutes", config.refill_min_interval_minutes],
    ["refill_strategy", config.refill_strategy],
    ["refill_fraction_percent", Number(config.refill_fraction || 0) * 100],
    ["refill_target_liters", Number(config.refill_target_ml || 0) / 1000],
    ["orientation_deg", balcony.orientation_deg],
    ["width_m", balcony.width_m],
    ["depth_m", balcony.depth_m],
    ["latitude", balcony.latitude],
    ["longitude", balcony.longitude],
    ["timezone_name", balcony.timezone_name || "Europe/Berlin"],
    ["refill_pump_ml_per_min", balcony.refill_pump_ml_per_min],
    ["main_pump_calibration_factor", saved.main_pump_calibration_factor],
    ["supply_warning_days", config.supply_warning_days],
    ["notification_cooldown_minutes", config.notification_cooldown_minutes],
    ["notification_retry_minutes", config.notification_retry_minutes],
    ["notification_resolved_enabled", config.notification_resolved_enabled],
    ["weather_stale_after_minutes", config.weather_stale_after_minutes],
    ["weather_cache_minutes", config.weather_cache_minutes],
    ["missed_watering_tolerance_minutes", config.missed_watering_tolerance_minutes],
    ["notification_worker_interval_seconds", config.notification_worker_interval_seconds],
  ].forEach(([name, value]) => setValue(form, name, value));
  const editor = document.getElementById("refillWindowEditor");
  editor.replaceChildren(...(config.refill_windows || []).map(refillWindowRow));
  document.getElementById("outletEditor").replaceChildren(...(state.outlets || []).map(outletRow));
  document.getElementById("wallEditor").replaceChildren(...(state.walls || []).map(wallRow));
  renderBalconyPlan(state, onChanged);
  renderSchedulePreview(form, evaluation);
  document.getElementById("settingsSaveStatus").textContent = "Gespeichert";
  toggleRefillStrategy(form);
}

function toggleRefillStrategy(form) {
  const strategy = named(form, "refill_strategy").value;
  for (const field of form.querySelectorAll("[data-refill-value]")) {
    field.hidden = field.dataset.refillValue !== strategy;
  }
}

export function initSettings(getData, onChanged, onSimulation) {
  const form = document.getElementById("settingsForm");
  const markChanged = () => {
    document.getElementById("settingsSaveStatus").textContent = "Ungespeicherte Änderungen";
    const { evaluation } = getData();
    renderSchedulePreview(form, evaluation);
  };
  form.addEventListener("input", markChanged);
  form.addEventListener("change", (event) => {
    if (event.target.name === "refill_strategy") toggleRefillStrategy(form);
    markChanged();
  });
  document.getElementById("addRefillWindowButton").addEventListener("click", () => {
    document.getElementById("refillWindowEditor").append(refillWindowRow());
    markChanged();
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const { state } = getData();
    const preview = renderSchedulePreview(form, getData().evaluation);
    const refillError = validateRefillWindows(currentRefillWindows());
    if (preview.error || refillError) {
      showToast(preview.error || refillError, { error: true });
      return;
    }
    const balcony = state.balcony || {};
    const payload = {
      orientation_deg: Number(named(form, "orientation_deg").value),
      width_m: Number(named(form, "width_m").value),
      depth_m: Number(named(form, "depth_m").value),
      latitude: Number(named(form, "latitude").value),
      longitude: Number(named(form, "longitude").value),
      timezone_name: named(form, "timezone_name").value || "Europe/Berlin",
      tank_capacity_ml: Math.round(Number(named(form, "tank_capacity_liters").value) * 1000),
      refill_tank_capacity_ml: Math.round(Number(named(form, "refill_tank_capacity_liters").value) * 1000),
      refill_pump_ml_per_min: Number(named(form, "refill_pump_ml_per_min").value),
      outlets: currentOutlets(state),
      walls: currentWalls(),
      watering_amount_percent: Number(named(form, "watering_amount_percent").value),
      refill_automation_enabled: named(form, "refill_automation_enabled").checked,
      main_pump_calibration_factor: Number(named(form, "main_pump_calibration_factor").value),
      planner_config: plannerConfigFromForm(form, state.planner_config),
      tank_current_ml: balcony.tank_current_ml,
      refill_tank_current_ml: balcony.refill_tank_current_ml,
    };
    try {
      await api.post("/api/balcony", payload);
      showToast("Einstellungen gespeichert");
      document.getElementById("settingsSaveStatus").textContent = "Gespeichert";
      await onChanged({ afterMutation: true });
    } catch (error) {
      document.getElementById("settingsSaveStatus").textContent = error.message;
      showToast(error.message, { error: true });
    }
  });
  document.getElementById("locateButton").addEventListener("click", () => {
    if (!navigator.geolocation) {
      showToast("Standortbestimmung wird nicht unterstützt", { error: true });
      return;
    }
    navigator.geolocation.getCurrentPosition((position) => {
      setValue(form, "latitude", position.coords.latitude.toFixed(6));
      setValue(form, "longitude", position.coords.longitude.toFixed(6));
      markChanged();
      showToast("Standort übernommen");
    }, () => showToast("Standort konnte nicht bestimmt werden", { error: true }));
  });
  document.getElementById("calibrateMainButton").addEventListener("click", async () => {
    try {
      await api.post("/api/calibration/main", {
        measured_level_percent: Number(document.getElementById("mainCalibrationLevel").value),
      });
      showToast("Haupttank kalibriert");
      await onChanged({ afterMutation: true });
    } catch (error) {
      showToast(error.message, { error: true });
    }
  });
  document.getElementById("calibrateRefillButton").addEventListener("click", async () => {
    try {
      await api.post("/api/calibration/refill", {
        measured_level_percent: Number(document.getElementById("refillCalibrationLevel").value),
      });
      showToast("Vorratstank kalibriert");
      await onChanged({ afterMutation: true });
    } catch (error) {
      showToast(error.message, { error: true });
    }
  });
  document.getElementById("weatherSimulationButton").addEventListener("click", async () => {
    const simulation = document.getElementById("weatherSimulationForm");
    const value = (name) => Number(simulation.querySelector(`[name=${name}]`).value);
    try {
      const evaluation = await api.post("/api/evaluate", {
        temperature_c: value("temperature_c"),
        rain_mm: value("rain_mm"),
        wind_kmh: value("wind_kmh"),
        sunshine_hours: value("sunshine_hours"),
      });
      onSimulation(evaluation);
      showToast("Simulation aktiv");
    } catch (error) {
      showToast(error.message, { error: true });
    }
  });
}
