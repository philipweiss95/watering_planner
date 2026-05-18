let state = null;
let latestEvaluation = null;
let shortcuts = null;

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
  renderState();
  await evaluateCurrent();
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
  renderPlants();
  renderBalconyPlan();
  renderEvaluation(latestEvaluation);
}

function renderPlants() {
  const assignments = latestEvaluation?.routing_plan?.assignments || [];
  const evaluatedPlants = latestEvaluation?.plants || [];
  $("#plants").innerHTML = state.plants.length
    ? state.plants
        .map(
          (plant) => {
            const assignment = assignments.find((item) => item.plant_id === plant.id);
            const evaluated = evaluatedPlants.find((item) => item.id === plant.id);
            const outletText = assignment
              ? `Vorschlag: ${assignment.tube_label} pro Zyklus`
              : "Schläuche werden automatisch berechnet";
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
                  <button class="delete" data-delete="${plant.id}" type="button">Entfernen</button>
                </div>
                <p class="meta">${outletText} · Position ${Math.round(plant.pos_x * 100)}/${Math.round(plant.pos_y * 100)}</p>
                <p class="meta">${needText}</p>
              </article>
            `;
          },
        )
        .join("")
    : `<p class="meta">Noch keine Pflanzen angelegt.</p>`;

  document.querySelectorAll("[data-delete]").forEach((button) => {
    button.addEventListener("click", async () => {
      state = await api(`/api/plants/${button.dataset.delete}`, { method: "DELETE" });
      renderState();
      await evaluateCurrent();
    });
  });
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

  const status = $("#runStatus");
  status.textContent = `${result.remaining_cycles_today} offen`;
  status.className = "status-pill";

  $("#result").innerHTML = `
    <strong>Heute vsl. ${result.recommended_cycles_today} ${result.recommended_cycles_today === 1 ? "Zyklus" : "Zyklen"}</strong>
    <span>${result.cycles_completed_today} erledigt, ${result.remaining_cycles_today} offen · ${result.pump.delivered_per_cycle_ml} ml pro gemeinsamem Pumpenlauf.</span>
    <span>${weatherLine(result)} · ${rainSummary(result)} · Sonne am Balkon ca. ${result.sun.sun_hours} h.</span>
    <span>Tank aktuell ${result.tank.current_ml} ml, nach offenen Zyklen ca. ${result.tank.after_recommended_ml} ml.</span>
  `;
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

function renderRoutingAssignments(result) {
  if (!result.routing_plan?.assignments?.length) return "";
  return `
    <div class="assignment-list">
      <strong>${result.routing_plan.summary}</strong>
      ${result.routing_plan.assignments
        .map(
          (item) => `
            <p class="meta">${item.plant_name}: ${item.tube_label}, Bedarf ${item.need_ml} ml, geliefert ${item.delivered_ml} ml, Differenz ${item.difference_ml} ml</p>
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

loadState().catch((error) => {
  $("#result").innerHTML = `<strong>Fehler</strong><span>${error.message}</span>`;
});
