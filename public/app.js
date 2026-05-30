let state = null;
let latestEvaluation = null;
let shortcuts = null;
let wateringEvents = [];
let editingPlantId = null;

const $ = (selector) => document.querySelector(selector);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ error: response.statusText }));
    throw new Error(error.error || response.statusText);
  }
  return response.json();
}

function formData(form) {
  return Object.fromEntries(new FormData(form).entries());
}

function numberFields(payload, fields) {
  for (const field of fields) {
    payload[field] = Number(payload[field]);
  }
  return payload;
}

async function loadState() {
  state = await api("/api/state");
  shortcuts = await api(`/api/shortcuts?base_url=${encodeURIComponent(window.location.origin)}`);
  await loadWateringEvents();
  renderState();
  await evaluateCurrent();
}

async function loadWateringEvents() {
  const response = await api("/api/watering-events?limit=12");
  wateringEvents = response.events || [];
}

function renderState() {
  const tank = state.balcony.tank_current_ml;
  const tankMax = state.balcony.tank_capacity_ml;
  $("#tankStatus").textContent = `${Math.round((tank / tankMax) * 100)}% Tank`;

  const catalogSelect = $("#catalogSelect");
  catalogSelect.innerHTML = state.catalog
    .map((plant) => `<option value="${plant.id}">${plant.name}</option>`)
    .join("");

  const balconyForm = $("#balconyForm");
  for (const [key, value] of Object.entries(state.balcony)) {
    const field = balconyForm.elements[key];
    if (field) field.value = value;
  }

  $("#outletEditor").innerHTML = state.outlets
    .map(
      (outlet) => `
        <div class="outlet-row" data-outlet-id="${outlet.id}">
          <label>Ausgang <input name="outlet_name_${outlet.id}" value="${outlet.name}"></label>
          <label>ml pro Lauf <input type="number" name="outlet_ml_${outlet.id}" value="${outlet.ml_per_run}"></label>
        </div>
      `,
    )
    .join("");

  $("#wallEditor").innerHTML = `
    <div class="subgrid-title">Wände</div>
    ${["north", "east", "south", "west"]
      .map((side) => {
        const wall = state.walls.find((item) => item.side === side) || { height_m: 0 };
        return `
          <label>${sideLabel(side)}
            <input type="number" step="0.05" min="0" name="wall_${side}" value="${wall.height_m}">
          </label>
        `;
      })
      .join("")}
  `;

  renderShortcuts();
  renderWateringLog();
  renderPlants();
  renderBalconyPlan();
  renderEvaluation(latestEvaluation);
}

function setActiveView(viewName) {
  document.querySelectorAll("[data-view]").forEach((view) => {
    view.classList.toggle("active", view.dataset.view === viewName);
  });
  document.querySelectorAll("[data-view-target]").forEach((button) => {
    const active = button.dataset.viewTarget === viewName;
    button.classList.toggle("active", active);
    if (active) {
      button.setAttribute("aria-current", "page");
    } else {
      button.removeAttribute("aria-current");
    }
  });
  window.location.hash = viewName;
}

function renderPlants() {
  const assignments = latestEvaluation?.routing_plan?.assignments || [];
  const evaluatedPlants = latestEvaluation?.plants || [];
  $("#plants").innerHTML = state.plants.length
    ? state.plants
        .map(
          (plant) => {
            if (plant.id === editingPlantId) return renderPlantEditForm(plant);
            const assignment = assignments.find((item) => item.plant_id === plant.id);
            const evaluated = evaluatedPlants.find((item) => item.id === plant.id);
            const outletText = assignment
              ? `Fester Anschluss: ${assignment.tube_label} pro Zyklus`
              : "Schläuche werden automatisch berechnet";
            const connectionText = assignment?.connection_note || plant.connection_note || "";
            const needText = evaluated
              ? `Bedarf heute ${evaluated.need_ml} ml · ET₀ ${evaluated.water_model.reference_et0_mm} mm · Windfaktor ${evaluated.water_model.wind_factor} · Krone ${evaluated.water_model.canopy_area_m2} m²`
              : "Bedarf wird nach Wetterdaten berechnet";
            return `
              <article class="plant-card">
                <div class="plant-top">
                  <div>
                    <strong>${plant.custom_name}</strong>
                    <p class="meta">${plant.catalog_name} · ${plant.pot_liters} l · ${potLabel(plant.pot_type)}</p>
                  </div>
                  <div class="plant-actions">
                    <button class="secondary small-button" data-edit="${plant.id}" type="button">Bearbeiten</button>
                    <button class="delete" data-delete="${plant.id}" type="button">Entfernen</button>
                  </div>
                </div>
                ${renderConnectionCompare(plant, assignment)}
                <p class="meta">${outletText} · Position ${Math.round(plant.pos_x * 100)}/${Math.round(plant.pos_y * 100)}</p>
                ${connectionText ? `<p class="meta ${connectionClass(assignment)}">${connectionText}</p>` : ""}
                <p class="meta">${needText}</p>
              </article>
            `;
          },
        )
        .join("")
    : `<p class="meta">Noch keine Pflanzen angelegt.</p>`;

  document.querySelectorAll("[data-edit]").forEach((button) => {
    button.addEventListener("click", () => {
      editingPlantId = Number(button.dataset.edit);
      renderPlants();
    });
  });

  document.querySelectorAll("[data-cancel-edit]").forEach((button) => {
    button.addEventListener("click", () => {
      editingPlantId = null;
      renderPlants();
    });
  });

  document.querySelectorAll("[data-plant-edit-form]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const submitButton = event.currentTarget.querySelector("button[type='submit']");
      submitButton.disabled = true;
      try {
        const plantId = event.currentTarget.dataset.plantEditForm;
        state = await api(`/api/plants/${plantId}`, {
          method: "PUT",
          body: JSON.stringify(plantPayloadFromForm(event.currentTarget)),
        });
        editingPlantId = null;
        renderState();
        await evaluateCurrent();
      } catch (error) {
        $("#result").innerHTML = `<strong>Pflanze konnte nicht gespeichert werden</strong><span>${error.message}</span>`;
      } finally {
        submitButton.disabled = false;
      }
    });
  });

  document.querySelectorAll("[data-delete]").forEach((button) => {
    button.addEventListener("click", async () => {
      state = await api(`/api/plants/${button.dataset.delete}`, { method: "DELETE" });
      renderState();
      await evaluateCurrent();
    });
  });
}

function renderConnectionCompare(plant, assignment) {
  const status = assignment?.connection_status || plant.connection_status || "ok";
  const currentMl = Math.round(plant.target_ml_per_cycle || plant.ml_per_run || 0);
  const recommendedMl = Math.round(assignment?.ml_per_cycle || currentMl);
  const currentLabel = plant.hose_numbers
    ? `Schlauch ${plant.hose_numbers}`
    : "Kein Schlauch";
  const suggestedLabel = assignment?.tube_label || "Noch kein Vorschlag";
  return `
    <div class="connection-compare ${status}">
      <div class="connection-side">
        <span class="compare-label">Bestand</span>
        <strong>${currentLabel}</strong>
        <span>${currentMl} ml/Zyklus</span>
      </div>
      <div class="connection-side suggested">
        <span class="compare-label">Vorschlag</span>
        <strong>${suggestedLabel}</strong>
        <span>${recommendedMl} ml/Zyklus</span>
      </div>
      <span class="connection-chip ${status}">${connectionStatusLabel(status)}</span>
    </div>
  `;
}

function connectionStatusLabel(status) {
  if (status === "urgent") return "Dringend";
  if (status === "change") return "Ändern";
  return "Passt";
}

function connectionClass(assignment) {
  const status = assignment?.connection_status;
  if (status === "urgent") return "urgent-text";
  if (status === "change") return "warn-text";
  return "";
}

function renderPlantEditForm(plant) {
  return `
    <article class="plant-card editing">
      <form class="plant-edit-form" data-plant-edit-form="${plant.id}">
        <div class="plant-top">
          <strong>${plant.custom_name}</strong>
          <button class="secondary small-button" data-cancel-edit type="button">Abbrechen</button>
        </div>
        <div class="grid">
          <label>
            Pflanzenart
            <select name="catalog_id">
              ${state.catalog.map((item) => `<option value="${item.id}" ${item.id === plant.catalog_id ? "selected" : ""}>${item.name}</option>`).join("")}
            </select>
          </label>
          <label>
            Name
            <input name="custom_name" value="${escapeAttribute(plant.custom_name)}">
          </label>
          <label>
            Größe
            <select name="size">
              ${[
                ["small", "Klein"],
                ["medium", "Mittel"],
                ["large", "Groß"],
                ["tree", "Baum/Strauch"],
              ]
                .map(([value, label]) => `<option value="${value}" ${value === plant.size ? "selected" : ""}>${label}</option>`)
                .join("")}
            </select>
          </label>
          <label>
            Topf Liter
            <input type="number" name="pot_liters" min="1" step="1" value="${plant.pot_liters}">
          </label>
          <label>
            Topfart
            <select name="pot_type">
              ${[
                ["reservoir", "Mit Wasserdepot"],
                ["overflow", "Mit Überlaufschutz"],
                ["reservoir_overflow", "Depot und Überlaufschutz"],
                ["closed", "Geschlossener Topf"],
              ]
                .map(([value, label]) => `<option value="${value}" ${value === plant.pot_type ? "selected" : ""}>${label}</option>`)
                .join("")}
            </select>
          </label>
          <label>
            Ausgang
            <select name="outlet_id">
              ${state.outlets.map((outlet) => `<option value="${outlet.id}" ${outlet.id === plant.outlet_id ? "selected" : ""}>${outlet.name} · ${outlet.ml_per_run} ml</option>`).join("")}
            </select>
          </label>
          <label>
            Schlauch
            <input name="hose_numbers" value="${escapeAttribute(plant.hose_numbers || "")}" placeholder="z.B. 12, 5, 16">
          </label>
          <label>
            ml pro Pumpzyklus
            <input type="number" name="target_ml_per_cycle" min="0" step="1" value="${plant.target_ml_per_cycle ?? ""}">
          </label>
        </div>
        <button type="submit" class="primary">Speichern</button>
      </form>
    </article>
  `;
}

function plantPayloadFromForm(form) {
  const payload = formData(form);
  payload.pot_liters = Number(payload.pot_liters);
  payload.outlet_id = Number(payload.outlet_id);
  payload.target_ml_per_cycle = payload.target_ml_per_cycle === "" ? null : Number(payload.target_ml_per_cycle);
  return payload;
}

function escapeAttribute(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function renderBalconyPlan() {
  const plan = $("#balconyPlan");
  const width = Number(state.balcony.width_m || 1);
  const depth = Number(state.balcony.depth_m || 1);
  plan.style.aspectRatio = `${Math.max(width, 0.5)} / ${Math.max(depth, 0.5)}`;
  const wallMap = Object.fromEntries(state.walls.map((wall) => [wall.side, Number(wall.height_m)]));
  plan.innerHTML = `
    <div class="plan-arrow" style="transform: rotate(${Number(state.balcony.orientation_deg || 180)}deg)">↑</div>
    ${["north", "east", "south", "west"]
      .map((side) => `<div class="wall wall-${side}" style="${wallStyle(side, wallMap[side] || 0)}"></div>`)
      .join("")}
    ${state.plants
      .map(
        (plant) => `
          <button class="plant-pin" data-plant-id="${plant.id}" style="left:${plant.pos_x * 100}%; top:${plant.pos_y * 100}%;" title="${plant.custom_name}">
            ${plant.custom_name.slice(0, 2)}
          </button>
        `,
      )
      .join("")}
  `;
  plan.querySelectorAll(".plant-pin").forEach((pin) => {
    pin.addEventListener("pointerdown", startDragPlant);
  });
}

function wallStyle(side, height) {
  const size = Math.max(3, Math.min(18, height * 8));
  if (side === "north" || side === "south") return `height:${size}px`;
  return `width:${size}px`;
}

function startDragPlant(event) {
  const pin = event.currentTarget;
  const plan = $("#balconyPlan");
  pin.setPointerCapture(event.pointerId);
  const move = (moveEvent) => {
    const rect = plan.getBoundingClientRect();
    const x = Math.min(1, Math.max(0, (moveEvent.clientX - rect.left) / rect.width));
    const y = Math.min(1, Math.max(0, (moveEvent.clientY - rect.top) / rect.height));
    pin.style.left = `${x * 100}%`;
    pin.style.top = `${y * 100}%`;
    pin.dataset.x = x;
    pin.dataset.y = y;
  };
  const done = async () => {
    pin.removeEventListener("pointermove", move);
    pin.removeEventListener("pointerup", done);
    const pos_x = Number(pin.dataset.x ?? parseFloat(pin.style.left) / 100);
    const pos_y = Number(pin.dataset.y ?? parseFloat(pin.style.top) / 100);
    state = await api(`/api/plants/${pin.dataset.plantId}/position`, {
      method: "POST",
      body: JSON.stringify({ pos_x, pos_y }),
    });
    renderState();
    await evaluateCurrent();
  };
  pin.addEventListener("pointermove", move);
  pin.addEventListener("pointerup", done);
}

function potLabel(type) {
  return {
    reservoir: "Wasserdepot",
    overflow: "Überlaufschutz",
    reservoir_overflow: "Depot und Überlaufschutz",
    closed: "geschlossen",
  }[type] || type;
}

async function evaluateCurrent() {
  const payload = numberFields(formData($("#evaluateForm")), ["temperature_c", "rain_mm", "wind_kmh", "sunshine_hours"]);
  payload.auto_weather = $("#evaluateForm").elements.auto_weather.checked;
  return evaluateWithPayload(payload);
}

async function evaluateWithPayload(payload) {
  latestEvaluation = await api("/api/evaluate", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  renderEvaluation(latestEvaluation);
  return latestEvaluation;
}

function currentManualWeatherPayload() {
  const payload = numberFields(formData($("#evaluateForm")), ["temperature_c", "rain_mm", "wind_kmh", "sunshine_hours"]);
  payload.auto_weather = false;
  return payload;
}

function renderEvaluation(result) {
  if (!result) return;
  const urgentActions = result.connection_plan?.assignments?.filter((item) => item.connection_status === "urgent") || [];
  const changeActions = result.connection_plan?.assignments?.filter((item) => item.connection_status === "change") || [];

  const status = $("#runStatus");
  status.textContent = `${result.remaining_cycles_today} offen`;
  status.className = "status-pill";
  status.classList.add(result.should_run ? "go" : "stop");

  $("#dashboardCards").innerHTML = renderDashboardCards(result, urgentActions, changeActions);
  $("#configSummary").innerHTML = renderConfigSummary(result, urgentActions, changeActions);
  $("#result").innerHTML = `
    <div class="decision-copy">
      <strong>${result.run_now ? "Jetzt pumpen" : result.should_run ? "Bedarf vorhanden" : "Heute pausieren"}</strong>
      <span>${result.reason}</span>
      <span>${result.automation?.summary || ""}</span>
    </div>
    ${renderAutomationControls(result)}
    ${renderActionSummary(urgentActions, changeActions)}
  `;
  bindAutomationControls();
  renderWateringLog();
  renderPlants();

  $("#routing").innerHTML = result.routing.length
    ? result.routing
        .map(
          (route) => `
            <article class="route">
              <div class="route-top">
                <div>
                  <strong>Ausgang ${route.name}</strong>
                  <p class="meta">${route.plants.join(", ")}</p>
                </div>
                <span class="badge">${route.connections_used}/${route.connections_limit}</span>
              </div>
              <p class="meta">${route.ml_per_run} ml-Ausgang · Bedarf ${route.need_ml} ml · geliefert ${route.delivered_ml} ml bei ${result.recommended_cycles_today} Zyklen</p>
            </article>
          `,
        )
        .join("") + renderRoutingAssignments(result)
    : `<p class="meta">Noch keine Verschlauchung berechenbar.</p>`;
}

function renderAutomationControls(result) {
  if (!result.automation) return "";
  const paused = result.automation.paused;
  return `
    <div class="automation-controls">
      <button class="secondary small-button" data-pause-automation type="button" ${paused ? "disabled" : ""}>Heute pausieren</button>
      <button class="secondary small-button" data-resume-automation type="button" ${paused ? "" : "disabled"}>Pause aufheben</button>
    </div>
  `;
}

function bindAutomationControls() {
  document.querySelectorAll("[data-pause-automation]").forEach((button) => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      await api("/api/automation/pause", { method: "POST", body: JSON.stringify({ scope: "today" }) });
      await evaluateCurrent();
    });
  });
  document.querySelectorAll("[data-resume-automation]").forEach((button) => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      await api("/api/automation/resume", { method: "POST", body: JSON.stringify({}) });
      await evaluateCurrent();
    });
  });
}

function renderConfigSummary(result, urgentActions, changeActions) {
  const connectionsUsed = result.routing.reduce((sum, route) => sum + Number(route.connections_used || 0), 0);
  const connectionsLimit = result.routing.reduce((sum, route) => sum + Number(route.connections_limit || 0), 0);
  const currentMl = result.pump.delivered_per_cycle_ml;
  const statusText = urgentActions.length
    ? `${urgentActions.length} dringend`
    : changeActions.length
      ? `${changeActions.length} empfohlen`
      : "passt";
  return `
    <article class="config-card plants">
      ${icon("leaf")}
      <div>
        <span>Pflanzen</span>
        <strong>${result.plants.length}</strong>
      </div>
    </article>
    <article class="config-card route">
      ${icon("route")}
      <div>
        <span>Anschlüsse</span>
        <strong>${connectionsUsed}/${connectionsLimit}</strong>
      </div>
    </article>
    <article class="config-card water">
      ${icon("drop")}
      <div>
        <span>Je Zyklus</span>
        <strong>${currentMl} ml</strong>
      </div>
    </article>
    <article class="config-card action ${urgentActions.length ? "urgent" : changeActions.length ? "warn" : "ok"}">
      ${icon("cycles")}
      <div>
        <span>Handlung</span>
        <strong>${statusText}</strong>
      </div>
    </article>
  `;
}

function renderDashboardCards(result, urgentActions, changeActions) {
  const weather = result.weather || result.inputs;
  const tankPercent = Math.round((result.tank.current_ml / Math.max(result.tank.capacity_ml, 1)) * 100);
  const remainingLiters = (result.pump.delivered_if_remaining_ml / 1000).toFixed(1);
  const perCycleLiters = (result.pump.delivered_per_cycle_ml / 1000).toFixed(2);
  const calibrationFactor = result.plants[0]?.water_model?.calibration_factor;
  const seasonalFactor = result.plants[0]?.water_model?.seasonal_factor;
  const calibrationText = calibrationFactor && seasonalFactor
    ? `${Math.round(calibrationFactor * 100)}% Basis · ${Math.round(seasonalFactor * 100)}% Saison`
    : "Kalibriertes Modell";
  const automation = result.automation || {};
  return `
    <article class="metric-card primary-metric ${result.should_run ? "go" : "stop"}">
      ${icon("cycles")}
      <div>
        <p class="metric-label">Zyklen heute</p>
        <strong>${result.remaining_cycles_today}<span>/${result.recommended_cycles_today}</span></strong>
        <p class="metric-detail">${result.cycles_completed_today} erledigt</p>
      </div>
    </article>
    <article class="metric-card water-metric">
      ${icon("drop")}
      <div>
        <p class="metric-label">Offene Menge</p>
        <strong>${remainingLiters} l</strong>
        <p class="metric-detail">${perCycleLiters} l je Lauf</p>
      </div>
    </article>
    <article class="metric-card tank-metric">
      ${icon("tank")}
      <div>
        <p class="metric-label">Tank</p>
        <strong>${tankPercent}%</strong>
        <p class="metric-detail">${Math.round(result.tank.after_recommended_ml / 1000)} l nach Plan</p>
      </div>
    </article>
    <article class="metric-card weather-metric">
      ${icon("sun")}
      <div>
        <p class="metric-label">Wetter</p>
        <strong>${Math.round(weather.temperature_c)}&deg;C</strong>
        <p class="metric-detail">${Math.round(weather.rain_mm * 10) / 10} mm Regen · ${Math.round(weather.wind_kmh)} km/h</p>
      </div>
    </article>
    <article class="metric-card setup-metric ${urgentActions.length ? "urgent" : changeActions.length ? "warn" : "ok"}">
      ${icon("route")}
      <div>
        <p class="metric-label">Anschlüsse</p>
        <strong>${urgentActions.length ? urgentActions.length : changeActions.length}</strong>
        <p class="metric-detail">${urgentActions.length ? "dringend" : changeActions.length ? "Änderungen" : "Bestand passt"}</p>
      </div>
    </article>
    <article class="metric-card automation-metric ${automation.run_now ? "go" : automation.paused ? "stop" : "ok"}">
      ${icon("clock")}
      <div>
        <p class="metric-label">Home Assistant</p>
        <strong>${automation.run_now ? "Jetzt" : automation.next_window || "--"}</strong>
        <p class="metric-detail">${automation.paused ? "Automatik pausiert" : automation.shortfall_prevention ? "vorgezogen bei knappen Fenstern" : "nächstes Zeitfenster"}</p>
      </div>
    </article>
    <article class="metric-card model-metric">
      ${icon("leaf")}
      <div>
        <p class="metric-label">Modell</p>
        <strong>${result.plants.length}</strong>
        <p class="metric-detail">${calibrationText}</p>
      </div>
    </article>
  `;
}

function icon(name) {
  const paths = {
    cycles: `<path d="M5 12a7 7 0 0 1 12-5"/><path d="M17 3v4h-4"/><path d="M19 12a7 7 0 0 1-12 5"/><path d="M7 21v-4h4"/>`,
    drop: `<path d="M12 3C9 7 6 10.5 6 14a6 6 0 0 0 12 0c0-3.5-3-7-6-11Z"/>`,
    tank: `<path d="M7 5h10a3 3 0 0 1 3 3v8a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3V8a3 3 0 0 1 3-3Z"/><path d="M7 14h10"/><path d="M8 8h.01"/><path d="M16 8h.01"/>`,
    sun: `<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.9 4.9 1.4 1.4"/><path d="m17.7 17.7 1.4 1.4"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m4.9 19.1 1.4-1.4"/><path d="m17.7 6.3 1.4-1.4"/>`,
    route: `<path d="M6 19a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z"/><path d="M18 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z"/><path d="M8.5 15.5h3.2a3 3 0 0 0 3-3v-1"/><path d="M15.5 8.5h-3.2a3 3 0 0 0-3 3v1"/>`,
    leaf: `<path d="M20 4C12 4 6 8.5 6 15a5 5 0 0 0 5 5c6.5 0 9-8 9-16Z"/><path d="M6 20c2-6 6-9 12-12"/>`,
    clock: `<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>`,
  };
  return `<span class="metric-icon"><svg viewBox="0 0 24 24" aria-hidden="true">${paths[name]}</svg></span>`;
}

function renderWateringLog() {
  const target = $("#wateringLog");
  if (!target) return;
  if (!wateringEvents.length) {
    target.innerHTML = `
      <div class="empty-log">
        ${icon("drop")}
        <div>
          <strong>Noch keine Läufe verbucht</strong>
          <span>Home Assistant schreibt nach jedem Pumpzyklus automatisch einen Eintrag.</span>
        </div>
      </div>
    `;
    return;
  }
  target.innerHTML = wateringEvents
    .map(
      (event) => `
        <article class="log-row">
          <div class="log-icon">${icon("drop")}</div>
          <div class="log-main">
            <strong>${formatEventTime(event.ran_at)}</strong>
            <span>${sourceLabel(event.source)} · ${Math.round(event.delivered_ml)} ml</span>
          </div>
          <div class="log-weather">
            <span>${event.temperature_c === null ? "--" : `${Math.round(Number(event.temperature_c) * 10) / 10}&deg;C`}</span>
            <span>${event.rain_mm === null ? "--" : `${Math.round(Number(event.rain_mm) * 10) / 10} mm`}</span>
          </div>
        </article>
      `,
    )
    .join("");
}

function formatEventTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("de-DE", {
    weekday: "short",
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function sourceLabel(source) {
  return {
    homekit: "Home Assistant",
    shortcut: "Kurzbefehl",
    manual: "Manuell",
  }[source] || source;
}

function renderActionSummary(urgentActions, changeActions) {
  if (!urgentActions.length && !changeActions.length) {
    return `<div class="action-summary ok">Fester Anschlussplan passt zum aktuellen Bestand.</div>`;
  }
  const urgentList = urgentActions.map((item) => item.plant_name).join(", ");
  const changeList = changeActions.slice(0, 4).map((item) => item.plant_name).join(", ");
  return `
    <div class="action-summary ${urgentActions.length ? "urgent" : "warn"}">
      ${urgentActions.length ? `<strong>${urgentActions.length} dringende Handlungsempfehlung${urgentActions.length === 1 ? "" : "en"}</strong><span>${urgentList}</span>` : ""}
      ${changeActions.length ? `<strong>${changeActions.length} Anschlussänderung${changeActions.length === 1 ? "" : "en"} empfohlen</strong><span>${changeList}${changeActions.length > 4 ? " ..." : ""}</span>` : ""}
    </div>
  `;
}

function renderRoutingAssignments(result) {
  if (!result.routing_plan?.assignments?.length) return "";
  const urgent = result.routing_plan.assignments.filter((item) => item.connection_status === "urgent");
  const changes = result.routing_plan.assignments.filter((item) => item.connection_status === "change");
  const ok = result.routing_plan.assignments.filter((item) => item.connection_status === "ok");
  const sorted = [...urgent, ...changes, ...ok];
  return `
    <div class="assignment-list">
      <strong>${result.routing_plan.summary}</strong>
      ${sorted
        .map(
          (item) => `
            <div class="assignment-row ${item.connection_status}">
              <div>
                <strong>${item.plant_name}</strong>
                <p class="meta">Bestand ${item.current_ml_per_cycle} ml/Zyklus · Vorschlag ${item.tube_label} · Bedarf ${item.need_ml} ml · Differenz ${item.difference_ml} ml</p>
                ${item.connection_note ? `<p class="meta ${connectionClass(item)}">${item.connection_note}</p>` : ""}
              </div>
              <span class="connection-chip ${item.connection_status}">${connectionStatusLabel(item.connection_status)}</span>
            </div>
          `,
        )
        .join("")}
    </div>
  `;
}

function sideLabel(side) {
  return { north: "Nord", east: "Ost", south: "Süd", west: "West" }[side] || side;
}

function weatherLine(result) {
  const weather = result.weather || result.inputs;
  const source = weather.source === "open-meteo" ? "Open-Meteo" : "manuell";
  const et0 = weather.et0_mm || result.inputs.et0_mm;
  return `${source}, ${Math.round(weather.temperature_c * 10) / 10} °C, ${Math.round(weather.rain_mm * 10) / 10} mm Regen, ${Math.round(weather.wind_kmh)} km/h Wind, ET₀ ${Math.round(et0 * 10) / 10} mm`;
}

function rainSummary(result) {
  const rain = Number((result.weather || result.inputs).rain_mm || 0);
  if (rain <= 0.2) return "kein relevanter Niederschlag";
  if (rain < result.thresholds.rain_mm) return `Niederschlag unter Schwelle ${result.thresholds.rain_mm} mm`;
  return `Niederschlag deckt voraussichtlich genug Bedarf`;
}

function renderShortcuts() {
  if (!shortcuts) return;
  $("#shortcutBox").innerHTML = `
    <p class="meta">Prüfen-URL</p>
    <code>${shortcuts.check_url}</code>
    <p class="meta">Nach Pumpenlauf verbuchen</p>
    <code>${shortcuts.mark_run_url}</code>
    <ol>
      ${shortcuts.steps.map((step) => `<li>${shortcutStep(step)}</li>`).join("")}
    </ol>
  `;
}

function shortcutStep(step) {
  if (step.action === "URL") return `Aktion „URL“ mit <code>${step.value}</code>`;
  if (step.action === "Inhalte von URL abrufen" && step.method === "POST") {
    return `Aktion „Inhalte von URL abrufen“: POST an <code>${step.url}</code>, JSON <code>${JSON.stringify(step.body)}</code>`;
  }
  if (step.key) return `${step.action}: <code>${step.key}</code>`;
  if (step.seconds) return `${step.action}: ${step.seconds} Sekunden`;
  return `${step.action}${step.value ? `: ${step.value}` : ""}`;
}

$("#evaluateForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  await evaluateCurrent();
});

$("#plantForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const submitButton = event.currentTarget.querySelector("button[type='submit']");
  submitButton.disabled = true;
  const payload = numberFields(formData(event.currentTarget), ["pot_liters"]);
  try {
    if (!payload.custom_name) {
      const selected = state.catalog.find((plant) => plant.id === payload.catalog_id);
      payload.custom_name = selected ? selected.name : "Pflanze";
    }
    const response = await api("/api/plants", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    state = {
      balcony: response.balcony,
      catalog: response.catalog,
      outlets: response.outlets,
      walls: response.walls,
      plants: response.plants,
      cycles_completed_today: response.cycles_completed_today,
    };
    event.currentTarget.reset();
    renderState();
    await evaluateWithPayload(currentManualWeatherPayload());
  } catch (error) {
    $("#result").innerHTML = `<strong>Pflanze konnte nicht angelegt werden</strong><span>${error.message}</span>`;
  } finally {
    submitButton.disabled = false;
  }
});

$("#balconyForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = numberFields(formData(event.currentTarget), [
    "width_m",
    "depth_m",
    "orientation_deg",
    "latitude",
    "longitude",
    "tank_capacity_ml",
    "tank_current_ml",
  ]);
  payload.timezone_name = payload.timezone_name || Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Berlin";
  payload.outlets = state.outlets.map((outlet) => ({
    id: outlet.id,
    name: payload[`outlet_name_${outlet.id}`],
    ml_per_run: Number(payload[`outlet_ml_${outlet.id}`]),
  }));
  for (const outlet of state.outlets) {
    delete payload[`outlet_name_${outlet.id}`];
    delete payload[`outlet_ml_${outlet.id}`];
  }
  payload.walls = ["north", "east", "south", "west"].map((side) => ({
    side,
    height_m: Number(payload[`wall_${side}`] || 0),
  }));
  for (const wall of payload.walls) {
    delete payload[`wall_${wall.side}`];
  }
  state = await api("/api/balcony", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  renderState();
  await evaluateCurrent();
});

$("#locateButton").addEventListener("click", () => {
  if (!navigator.geolocation) return;
  navigator.geolocation.getCurrentPosition((position) => {
    const form = $("#balconyForm");
    form.elements.latitude.value = position.coords.latitude.toFixed(6);
    form.elements.longitude.value = position.coords.longitude.toFixed(6);
    form.elements.timezone_name.value = Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Berlin";
  });
});

$("#refreshLogButton").addEventListener("click", async () => {
  await loadWateringEvents();
  renderWateringLog();
});

document.querySelectorAll("[data-view-target]").forEach((button) => {
  button.addEventListener("click", () => setActiveView(button.dataset.viewTarget));
});

document.addEventListener("keydown", async (event) => {
  const target = event.target;
  const typing = target && ["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName);
  if (typing || event.metaKey || event.ctrlKey || event.altKey) return;
  if (event.key === "1") setActiveView("dashboard");
  if (event.key === "2") setActiveView("config");
  if (event.key === "3") setActiveView("info");
  if (event.key.toLowerCase() === "r") await evaluateCurrent();
});

const initialView = window.location.hash.replace("#", "");
if (["dashboard", "config", "info"].includes(initialView)) {
  setActiveView(initialView);
}

loadState().catch((error) => {
  $("#result").innerHTML = `<strong>Fehler</strong><span>${error.message}</span>`;
});
