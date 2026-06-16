let state = null;
let latestEvaluation = null;
let shortcuts = null;
let wateringEvents = [];
let editingPlantId = null;

const IGNORED_SUGGESTIONS_KEY = "wateringPlannerIgnoredSuggestions";

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
  const response = await api("/api/watering-events?limit=20");
  wateringEvents = response.events || [];
}

function renderState() {
  const tank = state.balcony.tank_current_ml;
  const tankMax = state.balcony.tank_capacity_ml;
  const refillTank = state.balcony.refill_tank_current_ml || 0;
  const refillTankMax = state.balcony.refill_tank_capacity_ml || 30000;
  $("#tankStatus").textContent = `${Math.round((tank / tankMax) * 100)}% Haupt · ${Math.round((refillTank / refillTankMax) * 100)}% Vorrat`;

  const catalogSelect = $("#catalogSelect");
  catalogSelect.innerHTML = state.catalog
    .map((plant) => `<option value="${plant.id}">${plant.name}</option>`)
    .join("");
  $("#plantHoseSelect").innerHTML = hoseSelectOptions();

  const balconyForm = $("#balconyForm");
  for (const [key, value] of Object.entries(state.balcony)) {
    const field = balconyForm.elements[key];
    if (field) field.value = value;
  }
  balconyForm.elements.tank_capacity_liters.value = Number(state.balcony.tank_capacity_ml || 0) / 1000;
  balconyForm.elements.refill_pump_ml_per_min.value = Number(state.balcony.refill_pump_ml_per_min || 0);
  balconyForm.elements.refill_automation_enabled.checked = state.settings?.refill_automation_enabled !== false;
  balconyForm.elements.refill_schedule_times.value = (state.settings?.refill_schedule_times || ["03:00", "06:00"]).join(", ");
  balconyForm.elements.refill_cooldown_minutes_per_liter.value = state.settings?.refill_cooldown_minutes_per_liter ?? 30;
  balconyForm.elements.watering_amount_percent.value = state.settings?.watering_amount_percent ?? 100;

  $("#outletEditor").innerHTML = state.outlets
    .map(
      (outlet) => `
        <div class="outlet-row" data-outlet-id="${outlet.id}">
          <label>
            Bezeichnung
            <input name="outlet_name_${outlet.id}" value="${outlet.name}">
          </label>
          <label>
            Wasser je Zyklus in ml
            <input type="number" min="0" step="1" name="outlet_ml_${outlet.id}" value="${outlet.ml_per_run}">
          </label>
        </div>
      `,
    )
    .join("");

  $("#wallEditor").innerHTML = `
    ${["north", "east", "south", "west"]
      .map((side) => {
        const wall = state.walls.find((item) => item.side === side) || { height_m: 0 };
        return `
          <label>${sideLabel(side)}seite in Metern
            <input type="number" step="0.05" min="0" name="wall_${side}" value="${wall.height_m}">
          </label>
        `;
      })
      .join("")}
  `;
  updateWateringAmountPreview();

  renderShortcuts();
  renderWateringLog();
  renderHoses();
  renderPlants();
  renderBalconyPlan();
  renderEvaluation(latestEvaluation);
  updateDerivedWaterPreview($("#plantForm"));
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
  const assignments = latestEvaluation?.connection_plan?.assignments || [];
  const evaluatedPlants = latestEvaluation?.plants || [];
  $("#plants").innerHTML = state.plants.length
    ? state.plants
        .map(
          (plant) => {
            if (plant.id === editingPlantId) return renderPlantEditForm(plant);
            const assignment = assignments.find((item) => item.plant_id === plant.id);
            const ignored = assignment && isSuggestionIgnored(assignment);
            const evaluated = evaluatedPlants.find((item) => item.id === plant.id);
            const outletText = plant.hose_count
              ? `Anschluss: ${hoseLabel(plant.hose_numbers)} · ${plant.outlet_summary} · ${plant.configured_ml_per_cycle} ml/Zyklus`
              : "Noch keine Schläuche angeschlossen";
            const connectionText = ignored ? "" : assignment?.connection_note || plant.connection_note || "";
            const needText = evaluated
              ? `Berechneter Bedarf heute: ${evaluated.need_ml} ml`
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
                ${renderConnectionCompare(plant, assignment, ignored)}
                <p class="meta">${outletText}</p>
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
  bindDerivedWaterPreviews();
  bindIgnoreSuggestionButtons();
}

function renderConnectionCompare(plant, assignment, ignored = false) {
  const status = ignored ? "ok" : assignment?.connection_status || plant.connection_status || "ok";
  const currentMl = Math.round(plant.configured_ml_per_cycle || 0);
  const recommendedMl = ignored ? currentMl : Math.round(assignment?.ml_per_cycle || currentMl);
  const currentLabel = plant.hose_numbers
    ? hoseLabel(plant.hose_numbers)
    : "Kein Schlauch";
  const suggestedLabel = ignored ? currentLabel : assignment?.tube_label || "Noch kein Vorschlag";
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
      ${assignment && !ignored && assignment.connection_status !== "ok" ? `<button class="secondary small-button ignore-suggestion-button" data-ignore-suggestion="${plant.id}" type="button">Ignorieren</button>` : ""}
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
      <form class="plant-edit-form" data-plant-edit-form="${plant.id}" data-water-connection-form>
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
            Wuchsgröße
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
            Topfvolumen in Litern
            <input type="number" name="pot_liters" min="1" step="1" value="${plant.pot_liters}">
          </label>
          <label>
            Topf-Eigenschaften
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
            Angeschlossene Schläuche
            <select name="hose_numbers" multiple size="5">
              ${hoseSelectOptions(plant.hose_numbers)}
            </select>
          </label>
          <div class="derived-water-preview" data-derived-water-preview></div>
        </div>
        <button type="submit" class="primary">Speichern</button>
      </form>
    </article>
  `;
}

function plantPayloadFromForm(form) {
  const payload = formData(form);
  payload.pot_liters = Number(payload.pot_liters);
  payload.hose_numbers = selectedHoseNumbers(form);
  return payload;
}

function outletOptions(selectedOutletId = null) {
  return state.outlets
    .map(
      (outlet) =>
        `<option value="${outlet.id}" ${outlet.id === selectedOutletId ? "selected" : ""}>${outlet.name} · ${outlet.ml_per_run} ml je Schlauch</option>`,
    )
    .join("");
}

function hoseNumbers(value) {
  const raw = Array.isArray(value) ? value.join(",") : String(value || "");
  return [...new Set(raw.match(/\d+/g) || [])];
}

function hoseLabel(value) {
  const numbers = hoseNumbers(value);
  return `${numbers.length === 1 ? "Schlauch" : "Schläuche"} ${numbers.join(", ")}`;
}

function updateDerivedWaterPreview(form) {
  const preview = form?.querySelector("[data-derived-water-preview]");
  if (!preview) return;
  const selected = selectedHoseNumbers(form);
  const hoses = state?.hoses?.filter((hose) => selected.includes(hose.number)) || [];
  const totalMl = hoses.reduce((sum, hose) => sum + Number(hose.ml_per_run || 0), 0);
  const detail = hoses.length
    ? hoses.map((hose) => `${hose.number}: ${hose.outlet_name} (${hose.ml_per_run} ml)`).join(" · ")
    : "Noch keine Schläuche ausgewählt";
  preview.innerHTML = `
    <span>Automatisch berechnete Wassermenge</span>
    <strong>${totalMl} ml pro Zyklus</strong>
    <small>${detail}</small>
  `;
}

function selectedHoseNumbers(form) {
  const field = form?.elements.hose_numbers;
  if (!field) return [];
  if (field.multiple) return Array.from(field.selectedOptions).map((option) => option.value);
  return hoseNumbers(field.value);
}

function hoseSelectOptions(selectedNumbers = []) {
  const selected = new Set(hoseNumbers(selectedNumbers));
  return (state?.hoses || [])
    .map((hose) => {
      const assigned = hose.plant_name ? ` · aktuell ${hose.plant_name}` : "";
      return `<option value="${escapeAttribute(hose.number)}" ${selected.has(hose.number) ? "selected" : ""}>Schlauch ${hose.number} · ${hose.outlet_name} · ${hose.ml_per_run} ml${assigned}</option>`;
    })
    .join("");
}

function renderHoses() {
  const editor = $("#hoseEditor");
  if (!editor) return;
  editor.innerHTML = state.hoses.length
    ? state.hoses.map((hose) => hoseRow(hose)).join("")
    : `<p class="meta">Noch keine Schläuche angelegt.</p>`;
  bindHoseRemoveButtons();
}

function hoseRow(hose = {}) {
  return `
    <div class="hose-row" data-hose-row>
      <label>
        Schlauchnummer
        <input name="hose_number" value="${escapeAttribute(hose.number || "")}" placeholder="z.B. 12">
      </label>
      <label>
        Output
        <select name="hose_outlet_id">${outletOptions(hose.outlet_id)}</select>
      </label>
      <span class="hose-assignment">${hose.plant_name ? `Pflanze: ${hose.plant_name}` : "Noch keiner Pflanze zugeordnet"}</span>
      <button class="delete small-button" data-remove-hose type="button">Entfernen</button>
    </div>
  `;
}

function bindHoseRemoveButtons() {
  document.querySelectorAll("[data-remove-hose]").forEach((button) => {
    if (button.dataset.removeHoseBound) return;
    button.addEventListener("click", () => {
      button.closest("[data-hose-row]").remove();
    });
    button.dataset.removeHoseBound = "true";
  });
}

function bindDerivedWaterPreviews() {
  document.querySelectorAll("[data-water-connection-form]").forEach((form) => {
    if (!form.dataset.waterPreviewBound) {
      form.addEventListener("input", () => updateDerivedWaterPreview(form));
      form.addEventListener("change", () => updateDerivedWaterPreview(form));
      form.dataset.waterPreviewBound = "true";
    }
    updateDerivedWaterPreview(form);
  });
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
  const activeAssignments = visibleConnectionAssignments(result.connection_plan?.assignments || []);
  const urgentActions = activeAssignments.filter((item) => item.connection_status === "urgent");
  const changeActions = activeAssignments.filter((item) => item.connection_status === "change");

  const status = $("#runStatus");
  status.textContent = `${result.remaining_cycles_today} offen`;
  status.className = "status-pill";
  status.classList.add(result.should_run ? "go" : "stop");

  $("#dashboardCards").innerHTML = renderDashboardCards(result, urgentActions, changeActions);
  $("#configSummary").innerHTML = renderConfigSummary(result, urgentActions, changeActions);
  $("#dashboardContext").innerHTML = renderDashboardContext(result, urgentActions, changeActions);
  $("#tankQuickActions").innerHTML = renderTankQuickActions(result);
  $("#calculationFlow").innerHTML = renderCalculationFlow(result, urgentActions, changeActions);
  $("#result").innerHTML = `
    <div class="decision-layout ${result.run_now ? "go" : result.should_run ? "wait" : "stop"}">
      <div class="decision-copy">
        <span class="decision-kicker">${result.run_now ? "Aktion möglich" : result.should_run ? "Heute einplanen" : "Kein Lauf nötig"}</span>
        <strong>${result.run_now ? "Jetzt pumpen" : result.should_run ? "Bedarf vorhanden" : "Heute pausieren"}</strong>
        <span>${result.reason}</span>
        <span>${result.automation?.summary || ""}</span>
      </div>
      ${renderAutomationControls(result)}
    </div>
    ${renderActionSummary(urgentActions, changeActions)}
  `;
  bindAutomationControls();
  bindTankFillButtons();
  bindIgnoreSuggestionButtons();
  updateWateringAmountPreview();
  renderWateringLog();
  renderPlants();

  const configuredRouting = result.routing.length
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
        .join("")
    : `<p class="meta">Noch keine Verschlauchung berechenbar.</p>`;
  $("#routing").innerHTML = configuredRouting + renderRoutingAssignments(result);
  bindIgnoreSuggestionButtons();
}

function renderTankQuickActions(result) {
  const refill = result.refill || {};
  const refillTank = refill.refill_tank || {};
  return `
    <div class="tank-action-card">
      <div>
        <span class="metric-label">Tankstände</span>
        <strong>${formatLiters(result.tank.current_ml)} Haupt · ${formatLiters(refillTank.current_ml || 0)} Vorrat</strong>
      </div>
      <div class="tank-fill-actions">
        <button class="secondary small-button" data-fill-tank="main" type="button">Haupttank voll</button>
        <button class="secondary small-button" data-fill-tank="refill" type="button">Vorratstank voll</button>
      </div>
    </div>
  `;
}

function renderAutomationControls(result) {
  if (!result.automation) return "";
  const paused = result.automation.paused;
  const manualRun = result.manual_run || {};
  const manualRefill = result.manual_refill || {};
  const manualRefillHint = manualRefill.available
    ? `${manualRefill.summary || "Nachfülllauf bereit."} Laufzeit ${formatDuration(manualRefill.duration_seconds || 0)}.`
    : (manualRefill.reason || "Startet die zweite Pumpe über Home Assistant.");
  return `
    <div class="automation-controls">
      <div class="manual-run-card ${manualRun.available ? "available" : "blocked"}">
        <span class="metric-label">Manueller Durchlauf</span>
        <button class="primary" data-manual-run type="button" ${manualRun.available ? "" : "disabled"}>Zyklus jetzt starten</button>
        <span class="manual-run-hint">${manualRun.reason || "Startet sofort einen vollständigen Pumpzyklus."}</span>
      </div>
      <div class="manual-run-card ${manualRefill.available ? "available" : "blocked"}">
        <span class="metric-label">Manuelle Nachfüllung</span>
        <button class="secondary" data-manual-refill type="button" ${manualRefill.available ? "" : "disabled"}>Nachfüllung starten</button>
        <span class="manual-run-hint">${manualRefillHint}</span>
      </div>
      <div class="automation-secondary">
        <button class="secondary small-button" data-pause-automation type="button" ${paused ? "disabled" : ""}>Heute pausieren</button>
        <button class="secondary small-button" data-resume-automation type="button" ${paused ? "" : "disabled"}>Pause aufheben</button>
      </div>
    </div>
  `;
}

function renderDashboardContext(result, urgentActions, changeActions) {
  const next = nextDashboardAction(result, urgentActions, changeActions);
  const automation = result.automation || {};
  return `
    <div class="next-action-card ${next.tone}">
      <span class="metric-label">Nächster Schritt</span>
      <strong>${next.title}</strong>
      <p>${next.detail}</p>
    </div>
    <div class="schedule-card">
      <div class="schedule-heading">
        <span class="metric-label">Tagesplan</span>
        <strong>${result.cycles_completed_today}/${result.recommended_cycles_today} erledigt</strong>
      </div>
      ${renderScheduleRail(automation.windows || [], result.cycles_completed_today, automation.due_cycles || 0)}
      <p>${automation.summary || "Noch kein Zeitfenster berechnet."}</p>
    </div>
  `;
}

function nextDashboardAction(result, urgentActions, changeActions) {
  if (result.refill?.refill_tank?.empty) {
    return {
      tone: "urgent",
      title: "Vorratstank auffüllen",
      detail: "Die Nachfüllpumpe wird nicht gestartet, solange der 30-l-Tank leer ist.",
    };
  }
  if (result.tank.empty_soon) {
    return {
      tone: "urgent",
      title: "Tank auffüllen",
      detail: result.tank.warning || "Der nächste vollständige Pumpzyklus ist sonst blockiert.",
    };
  }
  if (result.refill?.limited_by_refill_tank) {
    return {
      tone: "warn",
      title: "Nachfüllung begrenzt",
      detail: `${formatLiters(result.refill.planned_transfer_ml)} von ${formatLiters(result.refill.requested_ml)} können aus dem Vorratstank nachlaufen.`,
    };
  }
  if (result.refill?.run_now) {
    return {
      tone: "go",
      title: "Nachfüllpumpe bereit",
      detail: `Home Assistant kann ${formatLiters(result.refill.planned_transfer_ml)} in ${formatDuration(result.refill.duration_seconds)} nachfüllen.`,
    };
  }
  if (urgentActions.length) {
    return {
      tone: "urgent",
      title: "Schläuche prüfen",
      detail: `${urgentActions[0].plant_name} braucht eine andere feste Verbindung.`,
    };
  }
  if (result.run_now) {
    return {
      tone: "go",
      title: "Zyklus kann starten",
      detail: result.manual_run?.available ? "Home Assistant ist bereit für einen vollständigen Durchlauf." : result.manual_run?.reason || result.reason,
    };
  }
  if (result.automation?.paused) {
    return {
      tone: "wait",
      title: "Automatik pausiert",
      detail: "Heb die Pause auf, wenn der Planer heute wieder automatisch laufen soll.",
    };
  }
  if (changeActions.length) {
    return {
      tone: "warn",
      title: "Anschluss optimieren",
      detail: `${changeActions.length} Empfehlung${changeActions.length === 1 ? "" : "en"} warten auf der Schlauchseite.`,
    };
  }
  if (result.remaining_cycles_today > 0 && !result.automation?.next_window) {
    return {
      tone: "wait",
      title: "Heute kein Fenster mehr",
      detail: result.automation?.summary || "Offene Zyklen werden erst im nächsten Tagesplan wieder verteilt.",
    };
  }
  if (result.remaining_cycles_today > 0) {
    return {
      tone: "wait",
      title: "Auf nächstes Fenster warten",
      detail: result.automation?.next_window ? `Der nächste geplante Lauf ist um ${result.automation.next_window}.` : result.reason,
    };
  }
  return {
    tone: "ok",
    title: "Heute ist alles ruhig",
    detail: result.reason,
  };
}

function renderScheduleRail(windows, completed, due) {
  if (!windows.length) return `<div class="schedule-rail empty">Kein Lauf geplant</div>`;
  return `
    <div class="schedule-rail">
      ${windows
        .map((time, index) => {
          const status = index < completed ? "done" : index < due ? "due" : "open";
          return `<span class="schedule-dot ${status}" title="${time}"><small>${time}</small></span>`;
        })
        .join("")}
    </div>
  `;
}

function bindAutomationControls() {
  document.querySelectorAll("[data-manual-run]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!window.confirm("Jetzt sofort einen vollständigen Pumpzyklus über Home Assistant starten?")) return;
      button.disabled = true;
      try {
        const response = await api("/api/manual-run", {
          method: "POST",
          body: JSON.stringify({ auto_weather: true }),
        });
        window.alert(response.message);
        await evaluateCurrent();
      } catch (error) {
        window.alert(error.message);
        button.disabled = false;
      }
    });
  });
  document.querySelectorAll("[data-manual-refill]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!window.confirm("Jetzt einen Nachfülllauf über Home Assistant starten?")) return;
      button.disabled = true;
      try {
        const response = await api("/api/manual-refill", {
          method: "POST",
          body: JSON.stringify({ auto_weather: true }),
        });
        window.alert(response.message);
        await evaluateCurrent();
      } catch (error) {
        window.alert(error.message);
        button.disabled = false;
      }
    });
  });
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
  const wateringAmount = state.settings?.watering_amount_percent ?? 100;
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
        <span>Gießmenge</span>
        <strong>${formatPercent(wateringAmount)} %</strong>
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

function renderCalculationFlow(result, urgentActions, changeActions) {
  const weather = result.weather || result.inputs;
  const wateringAmount = state.settings?.watering_amount_percent ?? 100;
  const totalNeedMl = result.plants.reduce((sum, plant) => sum + Number(plant.need_ml || 0), 0);
  const topPlant = result.plants.reduce((current, plant) => Number(plant.need_ml || 0) > Number(current?.need_ml || 0) ? plant : current, null);
  const connectionsUsed = result.routing.reduce((sum, route) => sum + Number(route.connections_used || 0), 0);
  const connectionsLimit = result.routing.reduce((sum, route) => sum + Number(route.connections_limit || 0), 0);
  const issueText = urgentActions.length
    ? `${urgentActions.length} dringend`
    : changeActions.length
      ? `${changeActions.length} empfohlen`
      : "passt";
  return `
    <div class="calc-steps">
      <article class="calc-step">
        <span class="step-number">1</span>
        <div>
          <strong>Wetter und Lage</strong>
          <p>${formatWeatherSource(weather)} · ${Math.round(weather.temperature_c)}&deg;C · ${Math.round(Number(weather.rain_mm || 0) * 10) / 10} mm Regen · ${Math.round(weather.wind_kmh)} km/h Wind</p>
          <small>${formatCompass(result.inputs.orientation_deg)} · ${state.balcony.width_m} x ${state.balcony.depth_m} m · ET0 ${Math.round(Number(result.inputs.et0_mm || 0) * 10) / 10} mm</small>
        </div>
      </article>
      <article class="calc-step">
        <span class="step-number">2</span>
        <div>
          <strong>Pflanzenbedarf</strong>
          <p>${formatLiters(totalNeedMl)} heute für ${result.plants.length} Pflanze${result.plants.length === 1 ? "" : "n"}</p>
          <small>${formatPercent(wateringAmount)} % Gießmenge · ${topPlant ? `${topPlant.custom_name}: ${topPlant.need_ml} ml` : "Noch keine Pflanzen angelegt"}</small>
        </div>
      </article>
      <article class="calc-step">
        <span class="step-number">3</span>
        <div>
          <strong>Feste Schläuche</strong>
          <p>${formatLiters(result.pump.delivered_per_cycle_ml)} pro gemeinsamem Zyklus</p>
          <small>${connectionsUsed}/${connectionsLimit} Anschlüsse genutzt · Anschlussplan ${issueText}</small>
        </div>
      </article>
      <article class="calc-step final">
        <span class="step-number">4</span>
        <div>
          <strong>Heutige Entscheidung</strong>
          <p>${result.remaining_cycles_today} von ${result.recommended_cycles_today} Zyklen offen</p>
          <small>${formatLiters(result.pump.delivered_if_remaining_ml)} noch geplant · Nachfüllung ${formatLiters(result.refill?.planned_transfer_ml || 0)} um ${result.refill?.scheduled_time || "03:00"}</small>
        </div>
      </article>
    </div>
    <div class="influence-board">
      <div>
        <span class="metric-label">Direkt steuerbar</span>
        <div class="influence-chips">
          <span>Gießmenge</span>
          <span>Pflanzen</span>
          <span>Positionen</span>
          <span>Schläuche</span>
          <span>Tank</span>
        </div>
      </div>
      <div>
        <span class="metric-label">Automatisch bewertet</span>
        <div class="influence-chips muted">
          <span>Wetter</span>
          <span>Sonne</span>
          <span>Windschutz</span>
          <span>Topfmodell</span>
          <span>ganze Zyklen</span>
        </div>
      </div>
    </div>
  `;
}

function formatWeatherSource(weather) {
  if (weather.weather_source === "open-meteo" || weather.source === "open-meteo") return "Open-Meteo";
  if (weather.weather_source === "manual" || weather.source === "manual") return "Manuell";
  return "Wetter";
}

function formatCompass(degrees) {
  const value = Number(degrees || 0);
  const labels = [
    [337.5, "Nord"],
    [292.5, "Nordwest"],
    [247.5, "West"],
    [202.5, "Südwest"],
    [157.5, "Süd"],
    [112.5, "Südost"],
    [67.5, "Ost"],
    [22.5, "Nordost"],
    [0, "Nord"],
  ];
  const label = labels.find(([limit]) => value >= limit)?.[1] || "Nord";
  return `${label} (${Math.round(value)}°)`;
}

function renderDashboardCards(result, urgentActions, changeActions) {
  const weather = result.weather || result.inputs;
  const tankPercent = result.tank.percent ?? Math.round((result.tank.current_ml / Math.max(result.tank.capacity_ml, 1)) * 100);
  const refill = result.refill || {};
  const refillTank = refill.refill_tank || {};
  const depletion = result.depletion || {};
  const remainingLiters = (result.pump.delivered_if_remaining_ml / 1000).toFixed(1);
  const perCycleLiters = (result.pump.delivered_per_cycle_ml / 1000).toFixed(2);
  const wateringAmount = state.settings?.watering_amount_percent ?? 100;
  const calibrationText = `${formatPercent(wateringAmount)}% Versorgung`;
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
    <article class="metric-card tank-metric ${result.tank.empty_soon ? "urgent" : result.tank.low ? "warn" : ""}">
      ${icon("tank")}
      <div>
        <p class="metric-label">Haupttank</p>
        <strong>${tankPercent}%</strong>
        <p class="metric-detail">${result.tank.warning || `${Math.round(result.tank.after_recommended_ml / 1000)} l nach Plan`}</p>
      </div>
    </article>
    <article class="metric-card refill-metric ${refillTank.empty ? "urgent" : refill.limited_by_refill_tank || refillTank.low ? "warn" : "ok"}">
      ${icon("tank")}
      <div>
        <p class="metric-label">Vorratstank</p>
        <strong>${refillTank.percent ?? 0}%</strong>
        <p class="metric-detail">${refill.summary || `${formatLiters(refill.planned_transfer_ml || 0)} um ${refill.scheduled_time || "03:00"}`}</p>
      </div>
    </article>
    <article class="metric-card depletion-metric ${depletion.all_empty_at ? "warn" : "ok"}">
      ${icon("clock")}
      <div>
        <p class="metric-label">Alles leer</p>
        <strong>${depletion.all_empty_at ? formatDateTime(depletion.all_empty_at) : "--"}</strong>
        <p class="metric-detail">${depletion.total_available_ml !== undefined ? `${formatLiters(depletion.total_available_ml)} Gesamtvorrat` : "Noch keine Reichweite"}</p>
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
        <p class="metric-detail">${automation.paused ? "Automatik pausiert" : automation.catch_up ? "verpasster Lauf wird nachgeholt" : "nächster verteilter Lauf"}</p>
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

function formatPercent(value) {
  return Number(Number(value).toFixed(1)).toLocaleString("de-DE");
}

function updateWateringAmountPreview() {
  const form = $("#balconyForm");
  const preview = $("#wateringAmountPreview");
  const label = $("#wateringAmountLabel");
  if (!form || !preview || !label) return;

  const amount = Number(form.elements.watering_amount_percent.value || 100);
  const savedAmount = Number(state?.settings?.watering_amount_percent || 100);
  const standardDailyMl = latestEvaluation
    ? latestEvaluation.plants.reduce((sum, plant) => sum + Number(plant.daily_need_ml || 0), 0) / savedAmount * 100
    : 0;
  const projectedDailyMl = standardDailyMl * amount / 100;
  const relativeText = wateringAmountTitle(amount);
  const adjustmentText = amount === 100
    ? "Standardversorgung für deine Anlage."
    : `${formatPercent(Math.abs(amount - 100))} % ${amount > 100 ? "mehr" : "weniger"} als Standard.`;
  const litersText = latestEvaluation
    ? `Bei aktuell geladenem Wetter sind das ungefähr ${formatLiters(projectedDailyMl)} statt ${formatLiters(standardDailyMl)} Pflanzenbedarf pro Tag.`
    : "Nach dem Laden der Wetterdaten siehst du hier eine Vorschau in Litern.";

  label.textContent = `${formatPercent(amount)} %`;
  preview.innerHTML = `
    <strong>${relativeText}</strong>
    <span>${adjustmentText}</span>
    <span>${litersText}</span>
    <span>Der Faktor verändert den berechneten Tagesbedarf. Die App macht daraus ganze Pumpzyklen.</span>
  `;
}

function wateringAmountTitle(amount) {
  if (amount < 90) return "Sparsame Korrektur";
  if (amount < 130) return "Neuer Standardbedarf";
  if (amount < 170) return "Leicht erhöhte Versorgung";
  if (amount < 230) return "Trockenheitskorrektur";
  if (amount < 300) return "Starke Trockenheitskorrektur";
  return "Akute Trockenheitskorrektur";
}

function formatLiters(ml) {
  return `${Number(ml / 1000).toLocaleString("de-DE", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} l`;
}

function formatDuration(seconds) {
  const totalSeconds = Math.max(0, Number(seconds || 0));
  const minutes = Math.floor(totalSeconds / 60);
  const rest = totalSeconds % 60;
  if (minutes && rest) return `${minutes} min ${rest} s`;
  if (minutes) return `${minutes} min`;
  return `${rest} s`;
}

function formatDateTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value || "--";
  return new Intl.DateTimeFormat("de-DE", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
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
          <div class="log-icon ${event.event_type || "watering"}">${icon(eventLogIcon(event))}</div>
          <div class="log-main">
            <strong>${event.title || "Bewässerung"} · ${formatEventTime(event.ran_at)}</strong>
            <span>${event.detail || eventLogDetail(event)}</span>
          </div>
          <div class="log-weather">
            <span>${sourceLabel(event.source)}</span>
            <span>${eventLogMeta(event)}</span>
          </div>
        </article>
      `,
    )
    .join("");
}

function eventLogIcon(event) {
  if (event.event_type === "refill" || event.event_type === "tank_fill") return "tank";
  return "drop";
}

function eventLogDetail(event) {
  const amount = Number(event.amount_ml ?? event.delivered_ml ?? event.transferred_ml ?? 0);
  return `${Math.round(amount)} ml`;
}

function eventLogMeta(event) {
  if (event.event_type === "refill" && Number(event.duration_seconds || 0) > 0) {
    return `${Math.round(Number(event.duration_seconds))} s`;
  }
  if (event.event_type === "tank_fill") {
    return formatLiters(event.amount_ml || 0);
  }
  const temperature = event.temperature_c === null ? "--" : `${Math.round(Number(event.temperature_c) * 10) / 10}&deg;C`;
  const rain = event.rain_mm === null ? "--" : `${Math.round(Number(event.rain_mm) * 10) / 10} mm`;
  return `${temperature} · ${rain}`;
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
    automatic: "Automatik",
    home_assistant: "Home Assistant",
    homekit: "Home Assistant",
    shortcut: "Kurzbefehl",
    manual: "Manuell",
    ui: "Interface",
  }[source] || source;
}

function renderActionSummary(urgentActions, changeActions) {
  if (!urgentActions.length && !changeActions.length) {
    return `<div class="action-summary ok">Keine offenen Anschlussmeldungen.</div>`;
  }
  const urgentList = urgentActions.map((item) => item.plant_name).join(", ");
  const changeList = changeActions.slice(0, 4).map((item) => item.plant_name).join(", ");
  return `
    <div class="action-summary ${urgentActions.length ? "urgent" : "warn"}">
      ${urgentActions.length ? `<strong>${urgentActions.length} dringende Handlungsempfehlung${urgentActions.length === 1 ? "" : "en"}</strong><span>${urgentList}</span>` : ""}
      ${changeActions.length ? `<strong>${changeActions.length} Anschlussänderung${changeActions.length === 1 ? "" : "en"} empfohlen</strong><span>${changeList}${changeActions.length > 4 ? " ..." : ""}</span>` : ""}
      <div class="action-buttons">
        ${[...urgentActions, ...changeActions].map((item) => `<button class="secondary small-button" data-ignore-suggestion="${item.plant_id}" type="button">${item.plant_name} ignorieren</button>`).join("")}
      </div>
    </div>
  `;
}

function renderRoutingAssignments(result) {
  if (!result.connection_plan?.assignments?.length) return "";
  const assignments = visibleConnectionAssignments(result.connection_plan.assignments);
  const urgent = assignments.filter((item) => item.connection_status === "urgent");
  const changes = assignments.filter((item) => item.connection_status === "change");
  const ok = result.connection_plan.assignments.filter((item) => item.connection_status === "ok");
  const sorted = [...urgent, ...changes, ...ok];
  return `
    <div class="assignment-list">
      <strong>${result.connection_plan.summary}</strong>
      ${sorted
        .map(
          (item) => `
            <div class="assignment-row ${item.connection_status}">
              <div>
                <strong>${item.plant_name}</strong>
                <p class="meta">Bestand ${item.current_ml_per_cycle} ml/Zyklus · Vorschlag ${item.tube_label} · Bedarf ${item.need_ml} ml · Differenz ${item.difference_ml} ml</p>
                ${item.connection_note ? `<p class="meta ${connectionClass(item)}">${item.connection_note}</p>` : ""}
              </div>
              <div class="assignment-actions">
                <span class="connection-chip ${item.connection_status}">${connectionStatusLabel(item.connection_status)}</span>
                ${item.connection_status !== "ok" ? `<button class="secondary small-button" data-ignore-suggestion="${item.plant_id}" type="button">Ignorieren</button>` : ""}
              </div>
            </div>
          `,
        )
        .join("")}
    </div>
  `;
}

function visibleConnectionAssignments(assignments) {
  return assignments.filter((item) => !isSuggestionIgnored(item));
}

function ignoredSuggestionMap() {
  try {
    return JSON.parse(localStorage.getItem(IGNORED_SUGGESTIONS_KEY) || "{}");
  } catch {
    return {};
  }
}

function saveIgnoredSuggestionMap(map) {
  localStorage.setItem(IGNORED_SUGGESTIONS_KEY, JSON.stringify(map));
}

function plantSuggestionSignature(plant) {
  if (!plant) return "";
  return JSON.stringify({
    catalog_id: plant.catalog_id,
    size: plant.size,
    pot_liters: Number(plant.pot_liters),
    pot_type: plant.pot_type,
    hose_numbers: hoseNumbers(plant.hose_numbers).join(","),
    configured_ml_per_cycle: Number(plant.configured_ml_per_cycle || 0),
    pos_x: Number(plant.pos_x || 0).toFixed(3),
    pos_y: Number(plant.pos_y || 0).toFixed(3),
  });
}

function isSuggestionIgnored(assignment) {
  if (!assignment || assignment.connection_status === "ok") return false;
  const plant = state?.plants?.find((item) => item.id === assignment.plant_id);
  return ignoredSuggestionMap()[assignment.plant_id] === plantSuggestionSignature(plant);
}

function ignoreSuggestion(plantId) {
  const plant = state?.plants?.find((item) => item.id === Number(plantId));
  if (!plant) return;
  const map = ignoredSuggestionMap();
  map[plant.id] = plantSuggestionSignature(plant);
  saveIgnoredSuggestionMap(map);
  renderEvaluation(latestEvaluation);
}

function bindIgnoreSuggestionButtons() {
  document.querySelectorAll("[data-ignore-suggestion]").forEach((button) => {
    if (button.dataset.ignoreBound) return;
    button.addEventListener("click", () => ignoreSuggestion(button.dataset.ignoreSuggestion));
    button.dataset.ignoreBound = "true";
  });
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
    <p class="meta">Sofort einen Zyklus starten</p>
    <code>${shortcuts.manual_run_url}</code>
    <ol>
      ${shortcuts.manual_run_steps.map((step) => `<li>${shortcutStep(step)}</li>`).join("")}
    </ol>
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
  const payload = plantPayloadFromForm(event.currentTarget);
  try {
    if (!payload.custom_name) {
      const selected = state.catalog.find((plant) => plant.id === payload.catalog_id);
      payload.custom_name = selected ? selected.name : "Pflanze";
    }
    const response = await api("/api/plants", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    state = response;
    event.currentTarget.reset();
    renderState();
    updateDerivedWaterPreview(event.currentTarget);
    await evaluateWithPayload(currentManualWeatherPayload());
  } catch (error) {
    $("#result").innerHTML = `<strong>Pflanze konnte nicht angelegt werden</strong><span>${error.message}</span>`;
  } finally {
    submitButton.disabled = false;
  }
});

$("#addHoseButton").addEventListener("click", () => {
  const editor = $("#hoseEditor");
  if (editor.querySelector(".meta")) editor.innerHTML = "";
  editor.insertAdjacentHTML("beforeend", hoseRow());
  bindHoseRemoveButtons();
});

$("#hoseForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const submitButton = event.currentTarget.querySelector("button[type='submit']");
  submitButton.disabled = true;
  try {
    const hoses = Array.from(event.currentTarget.querySelectorAll("[data-hose-row]")).map((row) => ({
      number: row.querySelector("[name='hose_number']").value,
      outlet_id: Number(row.querySelector("[name='hose_outlet_id']").value),
    }));
    state = await api("/api/hoses", {
      method: "POST",
      body: JSON.stringify({ hoses }),
    });
    renderState();
    await evaluateCurrent();
  } catch (error) {
    $("#result").innerHTML = `<strong>Schläuche konnten nicht gespeichert werden</strong><span>${error.message}</span>`;
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
    "tank_capacity_liters",
    "refill_pump_ml_per_min",
    "refill_cooldown_minutes_per_liter",
    "watering_amount_percent",
  ]);
  payload.tank_capacity_ml = Math.round(payload.tank_capacity_liters * 1000);
  payload.refill_tank_capacity_ml = 30000;
  payload.refill_automation_enabled = event.currentTarget.elements.refill_automation_enabled.checked;
  payload.refill_schedule_times = String(payload.refill_schedule_times || "")
    .split(/[;,]/)
    .map((item) => item.trim())
    .filter(Boolean);
  delete payload.tank_capacity_liters;
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

$("#balconyForm").elements.watering_amount_percent.addEventListener("input", updateWateringAmountPreview);

$("#locateButton").addEventListener("click", () => {
  if (!navigator.geolocation) return;
  navigator.geolocation.getCurrentPosition((position) => {
    const form = $("#balconyForm");
    form.elements.latitude.value = position.coords.latitude.toFixed(6);
    form.elements.longitude.value = position.coords.longitude.toFixed(6);
    form.elements.timezone_name.value = Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Berlin";
  });
});

function bindTankFillButtons() {
  document.querySelectorAll("[data-fill-tank]").forEach((button) => {
    button.addEventListener("click", async () => {
      const tank = button.dataset.fillTank;
      const tankName = tank === "refill" ? "Vorratstank" : "Haupttank";
      if (!window.confirm(`${tankName} wirklich als vollständig gefüllt markieren? Der rechnerische Füllstand wird auf 100% gesetzt.`)) return;
      button.disabled = true;
      try {
        state = await api(`/api/tanks/${tank}/fill`, { method: "POST", body: JSON.stringify({}) });
        renderState();
        await evaluateCurrent();
      } catch (error) {
        $("#result").innerHTML = `<strong>Tankstand konnte nicht aktualisiert werden</strong><span>${error.message}</span>`;
      } finally {
        button.disabled = false;
      }
    });
  });
}

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
  if (event.key === "2") setActiveView("plants");
  if (event.key === "3") setActiveView("hoses");
  if (event.key === "4") setActiveView("settings");
  if (event.key === "5") setActiveView("info");
  if (event.key.toLowerCase() === "r") await evaluateCurrent();
});

const initialView = window.location.hash.replace("#", "");
if (["dashboard", "plants", "hoses", "settings", "info"].includes(initialView)) {
  setActiveView(initialView);
}

loadState().catch((error) => {
  $("#result").innerHTML = `<strong>Fehler</strong><span>${error.message}</span>`;
});
