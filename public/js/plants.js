import { api } from "./api.js";
import { liters, number } from "./format.js";
import { badge, confirmDialog, element, emptyState, formDialog, icon, progress, showToast } from "./ui.js";

const ignoredKey = "watering-planner-ignored-connections";
let activeFilter = "all";

export const PLANT_SIZE_OPTIONS = Object.freeze([
  ["small", "Klein"],
  ["medium", "Mittel"],
  ["large", "Groß"],
  ["tree", "Baum/Strauch"],
]);

function loadIgnored() {
  try {
    return new Set(JSON.parse(localStorage.getItem(ignoredKey) || "[]").map(Number));
  } catch {
    return new Set();
  }
}

function saveIgnored(values) {
  try {
    localStorage.setItem(ignoredKey, JSON.stringify([...values]));
  } catch {
    // The UI remains usable when private browsing blocks storage.
  }
}

export function plantSupplyStatus(plant) {
  const need = Math.max(0, Number(plant.need_ml ?? plant.daily_need_ml ?? 0));
  const delivered = Math.max(0, Number(plant.delivered_ml ?? 0));
  const ratio = need > 0 ? delivered / need : (delivered > 0 ? 2 : 1);
  if (ratio < 0.88) return { key: "under", label: "Zu wenig", tone: "danger", ratio };
  if (ratio > 1.2) return { key: "over", label: "Zu viel", tone: "warning", ratio };
  return { key: "fit", label: "Passend", tone: "success", ratio };
}

export function filterPlants(plants, filter) {
  return plants.filter((plant) => {
    const status = plantSupplyStatus(plant);
    if (filter === "under") return status.key === "under";
    if (filter === "over") return status.key === "over";
    if (filter === "action") return status.key !== "fit" || ["change", "urgent"].includes(plant.connection_status);
    return true;
  });
}

export function connectionRecommendation(plant, unassigned = []) {
  const currentCount = Number(plant.hose_count || 0);
  const recommendedCount = Array.isArray(plant.suggested_tubes)
    ? plant.suggested_tubes.reduce((sum, tube) => sum + Number(tube.count || 0), 0)
    : Number(plant.suggested_tubes || 0);
  const unavailable = unassigned.includes(plant.custom_name) || unassigned.includes(plant.catalog_name);
  if (unavailable) {
    return { action: "unavailable", title: "Keine passende Kombination", text: "Die Anschlussgrenzen erlauben derzeit keine ausreichende Lösung." };
  }
  if (plant.connection_status === "ok" || !plant.connection_status) {
    return { action: "none", title: "Anschluss passt", text: plant.outlet_summary || "Keine Änderung nötig." };
  }
  if (recommendedCount > currentCount || plant.connection_action === "add_hose") {
    return { action: "add", title: "Zusätzlichen Schlauch ergänzen", text: plant.connection_note || plant.suggested_tube_label || "" };
  }
  if (recommendedCount < currentCount) {
    return { action: "remove", title: "Schlauch entfernen", text: plant.connection_note || plant.suggested_tube_label || "" };
  }
  return { action: "rewire", title: "Ausgang wechseln", text: plant.connection_note || plant.suggested_tube_label || "" };
}

export function plantPayload(plant, overrides = {}) {
  return {
    catalog_id: String(plant.catalog_id ?? ""),
    custom_name: plant.custom_name || "",
    size: plant.size || "medium",
    pot_liters: Number(plant.pot_liters || 10),
    pot_type: plant.pot_type || "overflow",
    hose_numbers: plant.hose_numbers || "",
    pos_x: Number(plant.pos_x ?? 0.5),
    pos_y: Number(plant.pos_y ?? 0.5),
    ...overrides,
  };
}

function formField(labelText, control) {
  return element("label", {}, [element("span", { text: labelText }), control]);
}

export function plantForm(state, plant = null) {
  const form = element("form", { className: "field-grid" });
  const name = element("input", { name: "custom_name", required: true, maxLength: 80, value: plant?.custom_name || "" });
  const catalog = element("select", { name: "catalog_id", required: true });
  for (const item of state.catalog || []) {
    catalog.append(element("option", {
      value: String(item.id),
      text: item.name,
      selected: String(plant?.catalog_id ?? "") === String(item.id),
    }));
  }
  const size = element("select", { name: "size" });
  PLANT_SIZE_OPTIONS.forEach(([value, label]) => {
    size.append(element("option", { value, text: label, selected: (plant?.size || "medium") === value }));
  });
  const potType = element("select", { name: "pot_type" });
  [["overflow", "Mit Ablauf"], ["reservoir", "Wasserspeicher"], ["reservoir_overflow", "Speicher mit Überlauf"], ["closed", "Geschlossen"]].forEach(([value, label]) => {
    potType.append(element("option", { value, text: label, selected: (plant?.pot_type || "overflow") === value }));
  });
  form.append(
    formField("Name", name),
    formField("Art", catalog),
    formField("Größe", size),
    formField("Topfvolumen (l)", element("input", { name: "pot_liters", type: "number", min: "0.1", step: "0.1", required: true, value: plant?.pot_liters || 10 })),
    formField("Topfart", potType),
  );
  return form;
}

export function plantPayloadFromFormData(data, plant = null) {
  return plantPayload(plant || {}, {
    catalog_id: String(data.get("catalog_id") ?? ""),
    custom_name: String(data.get("custom_name") ?? ""),
    size: String(data.get("size") ?? ""),
    pot_liters: Number(data.get("pot_liters")),
    pot_type: String(data.get("pot_type") ?? ""),
  });
}

export async function restorePlant(snapshot, client = api) {
  return client.post("/api/plants", plantPayload(snapshot));
}

async function editPlant(state, plant, onChanged) {
  const form = plantForm(state, plant);
  const data = await formDialog({ title: plant ? "Pflanze bearbeiten" : "Pflanze hinzufügen", form, submitText: "Speichern" });
  if (!data) return;
  const payload = plantPayloadFromFormData(data, plant);
  if (plant) await api.put(`/api/plants/${plant.id}`, payload);
  else await api.post("/api/plants", payload);
  showToast(plant ? "Pflanze gespeichert" : "Pflanze hinzugefügt");
  await onChanged({ afterMutation: true });
}

async function removePlant(state, plant, onChanged) {
  const confirmed = await confirmDialog({
    title: "Pflanze löschen",
    message: `${plant.custom_name} wird entfernt. Zugeordnete Schläuche bleiben erhalten und werden frei.`,
    confirmText: "Pflanze löschen",
    dangerous: true,
  });
  if (!confirmed) return;
  const snapshot = plantPayload(plant);
  await api.delete(`/api/plants/${plant.id}`);
  await onChanged({ afterMutation: true });
  showToast("Pflanze gelöscht", {
    actionLabel: "Rückgängig",
    onAction: async () => {
      await restorePlant(snapshot);
      await onChanged({ afterMutation: true });
      showToast("Pflanze wiederhergestellt");
    },
  });
}

function plantCard(state, evaluation, plant, onChanged) {
  const status = plantSupplyStatus(plant);
  const unassigned = evaluation?.connection_plan?.unassigned_plants || [];
  const recommendation = connectionRecommendation(plant, unassigned);
  const ignored = loadIgnored();
  const isIgnored = ignored.has(Number(plant.id));
  const card = element("article", { className: "plant-card" });
  card.append(
    element("div", { className: "plant-title" }, [
      element("h2", { text: plant.custom_name }),
      element("p", { text: `${plant.catalog_name || "Pflanze"} · ${number(plant.pot_liters, 1)} l` }),
    ]),
    element("div", { className: "plant-supply" }, [
      element("div", { className: "supply-values" }, [
        element("span", { text: `Bedarf ${liters(plant.need_ml ?? plant.daily_need_ml)}` }),
        element("strong", { text: `Heute ${liters(plant.delivered_ml)}` }),
      ]),
      progress(status.ratio * 100, status.tone),
      element("p", { text: `${status.label} · ${number((plant.delivered_ml || 0) - (plant.need_ml ?? plant.daily_need_ml ?? 0))} ml` }),
    ]),
  );
  if (isIgnored && recommendation.action !== "none") {
    const undo = element("button", { className: "secondary compact-command", type: "button", text: "Wieder anzeigen" });
    undo.addEventListener("click", () => {
      ignored.delete(Number(plant.id));
      saveIgnored(ignored);
      renderPlants(state, evaluation, onChanged);
    });
    card.append(element("div", { className: "connection-action" }, [
      element("strong", { text: "Empfehlung ignoriert" }),
      element("p", { text: recommendation.title }),
      undo,
    ]));
  } else {
    const recommendationBox = element("div", { className: "connection-action" }, [
      element("strong", { text: recommendation.title }),
      element("p", { text: recommendation.text }),
    ]);
    if (recommendation.action !== "none" && recommendation.action !== "unavailable") {
      const ignore = element("button", { className: "secondary compact-command", type: "button", text: "Ignorieren" });
      ignore.addEventListener("click", () => {
        ignored.add(Number(plant.id));
        saveIgnored(ignored);
        renderPlants(state, evaluation, onChanged);
        showToast("Empfehlung ausgeblendet", {
          actionLabel: "Rückgängig",
          onAction: () => {
            ignored.delete(Number(plant.id));
            saveIgnored(ignored);
            renderPlants(state, evaluation, onChanged);
          },
        });
      });
      recommendationBox.append(ignore);
    }
    card.append(recommendationBox);
  }
  const actions = element("div", { className: "plant-actions" });
  const edit = element("button", { className: "icon-button", type: "button", title: "Pflanze bearbeiten", attrs: { "aria-label": `${plant.custom_name} bearbeiten` } }, icon("edit"));
  const remove = element("button", { className: "icon-button", type: "button", title: "Pflanze löschen", attrs: { "aria-label": `${plant.custom_name} löschen` } }, icon("trash"));
  edit.addEventListener("click", () => editPlant(state, plant, onChanged).catch((error) => showToast(error.message, { error: true })));
  remove.addEventListener("click", () => removePlant(state, plant, onChanged).catch((error) => showToast(error.message, { error: true })));
  actions.append(edit, remove);
  card.append(actions);

  const model = plant.water_model || {};
  card.append(element("details", { className: "plant-details" }, [
    element("summary", { text: "Modelldetails" }),
    element("div", { className: "plant-detail-grid" }, [
      element("span", { text: `ET₀ ${number(model.reference_et0_mm ?? evaluation?.inputs?.et0_mm, 1)} mm` }),
      element("span", { text: `Sonne ${number(plant.sun_hours ?? plant.sunshine_hours, 1)} h` }),
      element("span", { text: `Topffaktor ${number(model.pot_factor ?? plant.pot_factor, 2)}` }),
      element("span", { text: `Schläuche ${plant.hose_numbers || "keine"}` }),
    ]),
  ]));
  return card;
}

export function renderPlants(state, evaluation, onChanged = async () => {}) {
  const source = evaluation?.plants || state?.plants || [];
  const plants = filterPlants(source, activeFilter);
  const list = document.getElementById("plantList");
  list.replaceChildren(...plants.map((plant) => plantCard(state, evaluation, plant, onChanged)));
  if (!plants.length) list.append(emptyState(source.length ? "Keine Pflanzen in diesem Filter." : "Noch keine Pflanzen angelegt."));
}

export function initPlants(getData, onChanged) {
  const filters = document.getElementById("plantFilters");
  filters.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-filter]");
    if (!button) return;
    activeFilter = button.dataset.filter;
    for (const item of filters.querySelectorAll("button")) item.classList.toggle("active", item === button);
    for (const item of filters.querySelectorAll("button")) item.setAttribute("aria-pressed", item === button ? "true" : "false");
    const { state, evaluation } = getData();
    renderPlants(state, evaluation, onChanged);
  });
  document.getElementById("addPlantButton").addEventListener("click", () => {
    const { state } = getData();
    editPlant(state, null, onChanged).catch((error) => showToast(error.message, { error: true }));
  });
}
