import { api } from "./api.js";
import { liters } from "./format.js";
import { badge, confirmDialog, element, emptyState, icon, showToast } from "./ui.js";

export function validateHoses(hoses, outlets, plants, maxPerOutlet = 12) {
  const warnings = [];
  const numbers = new Map();
  const outletIds = new Set(outlets.map((item) => Number(item.id)));
  const plantIds = new Set(plants.map((item) => Number(item.id)));
  const counts = new Map();
  for (const [index, hose] of hoses.entries()) {
    const number = String(hose.number || "").trim();
    if (!number) warnings.push({ code: "missing-number", index, message: "Jeder Schlauch braucht eine Nummer." });
    if (numbers.has(number)) warnings.push({ code: "duplicate", index, message: `Schlauchnummer ${number} ist doppelt.` });
    numbers.set(number, index);
    const outletId = Number(hose.outlet_id);
    if (!outletIds.has(outletId)) warnings.push({ code: "invalid-outlet", index, message: `Schlauch ${number || index + 1} hat einen ungültigen Ausgang.` });
    counts.set(outletId, (counts.get(outletId) || 0) + 1);
    if (hose.plant_id !== "" && hose.plant_id !== null && hose.plant_id !== undefined && !plantIds.has(Number(hose.plant_id))) {
      warnings.push({ code: "invalid-plant", index, message: `Schlauch ${number || index + 1} verweist auf eine unbekannte Pflanze.` });
    }
    if (hose.plant_id === "" || hose.plant_id === null || hose.plant_id === undefined) {
      warnings.push({ code: "unassigned", index, message: `Schlauch ${number || index + 1} ist nicht zugeordnet.` });
    }
  }
  for (const [outletId, count] of counts) {
    if (count > maxPerOutlet) {
      const outlet = outlets.find((item) => Number(item.id) === outletId);
      warnings.push({ code: "outlet-limit", message: `${outlet?.name || "Ausgang"}: ${count} von maximal ${maxPerOutlet} Anschlüssen.` });
    }
  }
  for (const plant of plants) {
    const supplied = hoses.some((hose) => Number(hose.plant_id) === Number(plant.id));
    if (!supplied) warnings.push({ code: "plant-unserved", message: `${plant.custom_name} wäre ohne Schlauch.` });
  }
  return warnings;
}

function hoseDataFromForm(table) {
  return [...table.querySelectorAll(".hose-row[data-row]")].map((row) => ({
    number: row.querySelector("[data-field=number]").value.trim(),
    outlet_id: Number(row.querySelector("[data-field=outlet]").value),
    plant_id: row.querySelector("[data-field=plant]").value,
  }));
}

function cell(label, child, className = "") {
  return element("div", { className: `hose-cell ${className}`.trim() }, [
    element("span", { className: "hose-cell-label", text: label }),
    child,
  ]);
}

function optionList(select, items, selectedValue, labelKey = "name", includeEmpty = false) {
  if (includeEmpty) select.append(element("option", { value: "", text: "Nicht zugeordnet", selected: selectedValue === "" || selectedValue === null }));
  for (const item of items) {
    select.append(element("option", {
      value: String(item.id),
      text: item[labelKey],
      selected: Number(selectedValue) === Number(item.id),
    }));
  }
}

function hoseRow(hose, state, onRemove) {
  const row = element("div", { className: "hose-row", dataset: { row: "true" }, attrs: { role: "row" } });
  const numberInput = element("input", { type: "text", value: hose.number || "", required: true, maxLength: 12, dataset: { field: "number" }, attrs: { "aria-label": "Schlauchnummer" } });
  const outletSelect = element("select", { dataset: { field: "outlet" }, attrs: { "aria-label": `Ausgang für Schlauch ${hose.number || "neu"}` } });
  optionList(outletSelect, state.outlets || [], hose.outlet_id);
  const selectedOutlet = state.outlets?.find((outlet) => Number(outlet.id) === Number(hose.outlet_id));
  const amount = element("span", { text: liters(selectedOutlet?.ml_per_run || 0) });
  const plantSelect = element("select", { dataset: { field: "plant" }, attrs: { "aria-label": `Pflanze für Schlauch ${hose.number || "neu"}` } });
  optionList(plantSelect, state.plants || [], hose.plant_id ?? "", "custom_name", true);
  const status = badge(hose.plant_id ? "Zugeordnet" : "Frei", hose.plant_id ? "success" : "warning");
  outletSelect.addEventListener("change", () => {
    const outlet = state.outlets.find((item) => Number(item.id) === Number(outletSelect.value));
    amount.textContent = liters(outlet?.ml_per_run || 0);
  });
  plantSelect.addEventListener("change", () => {
    status.textContent = plantSelect.value ? "Zugeordnet" : "Frei";
    status.className = `status-badge ${plantSelect.value ? "success" : "warning"}`;
  });
  const remove = element("button", { className: "icon-button", type: "button", title: "Schlauch löschen", attrs: { "aria-label": `Schlauch ${hose.number || "neu"} löschen` } }, icon("trash"));
  remove.addEventListener("click", () => onRemove(row, hose));
  row.append(
    cell("Nummer", numberInput),
    cell("Ausgang", outletSelect),
    cell("Wassermenge", amount),
    cell("Pflanze", plantSelect),
    cell("Status", status),
    cell("Aktion", remove, "hose-actions"),
  );
  return row;
}

function warningNodes(warnings) {
  const unique = [...new Map(warnings.map((warning) => [warning.message, warning])).values()];
  return unique.map((warning) => element("div", { className: "inline-alert" }, [
    icon("alert"),
    element("span", { text: warning.message }),
  ]));
}

export async function persistHoses(rows, state, client = api) {
  const hardWarnings = validateHoses(rows, state.outlets || [], state.plants || []).filter((item) =>
    ["missing-number", "duplicate", "invalid-outlet", "invalid-plant", "outlet-limit"].includes(item.code));
  if (hardWarnings.length) throw new Error(hardWarnings[0].message);
  await client.post("/api/hoses", { hoses: rows.map(({ number, outlet_id }) => ({ number, outlet_id })) });
  for (const plant of state.plants || []) {
    const assigned = rows.filter((row) => Number(row.plant_id) === Number(plant.id)).map((row) => row.number).join(", ");
    await client.put(`/api/plants/${plant.id}`, {
      catalog_id: plant.catalog_id,
      custom_name: plant.custom_name,
      size: plant.size,
      pot_liters: plant.pot_liters,
      pot_type: plant.pot_type,
      hose_numbers: assigned,
    });
  }
}

export function renderHoses(state, onChanged = async () => {}) {
  const table = document.getElementById("hoseTable");
  const warnings = document.getElementById("hoseWarnings");
  table.replaceChildren(element("div", { className: "hose-row header", attrs: { role: "row" } }, [
    element("span", { text: "Nummer" }), element("span", { text: "Ausgang" }),
    element("span", { text: "Menge" }), element("span", { text: "Pflanze" }),
    element("span", { text: "Status" }), element("span", { text: "" }),
  ]));
  const refreshWarnings = () => {
    const rows = hoseDataFromForm(table);
    warnings.replaceChildren(...warningNodes(validateHoses(rows, state.outlets || [], state.plants || [])));
  };
  const removeRow = async (row, hose) => {
    const accepted = await confirmDialog({
      title: "Schlauch löschen",
      message: `Schlauch ${hose.number || "neu"} wird entfernt. Betroffene Pflanzen können dadurch unversorgt sein.`,
      confirmText: "Schlauch löschen",
      dangerous: true,
    });
    if (!accepted) return;
    const snapshot = {
      number: row.querySelector("[data-field=number]").value,
      outlet_id: Number(row.querySelector("[data-field=outlet]").value),
      plant_id: row.querySelector("[data-field=plant]").value,
    };
    row.remove();
    refreshWarnings();
    try {
      await persistHoses(hoseDataFromForm(table), state);
      await onChanged({ afterMutation: true });
      showToast("Schlauch gelöscht", {
        actionLabel: "Rückgängig",
        onAction: async () => {
          const currentState = await api.get("/api/state");
          const restored = [...currentState.hoses, snapshot];
          await persistHoses(restored, currentState);
          await onChanged({ afterMutation: true });
          showToast("Schlauch wiederhergestellt");
        },
      });
    } catch (error) {
      showToast(error.message, { error: true });
      await onChanged({ afterMutation: true });
    }
  };
  for (const hose of state.hoses || []) table.append(hoseRow(hose, state, removeRow));
  if (!(state.hoses || []).length) table.append(emptyState("Noch keine Schläuche eingerichtet."));
  refreshWarnings();
  table.onchange = refreshWarnings;
  table.oninput = refreshWarnings;
}

export function initHoses(getState, onChanged) {
  document.getElementById("addHoseButton").addEventListener("click", () => {
    const state = getState();
    const table = document.getElementById("hoseTable");
    table.querySelector(".empty-state")?.remove();
    const highest = Math.max(0, ...(state.hoses || []).map((hose) => Number(hose.number) || 0));
    table.append(hoseRow({ number: String(highest + 1), outlet_id: state.outlets?.[0]?.id, plant_id: null }, state, (row) => row.remove()));
    table.querySelector(".hose-row:last-child input")?.focus();
  });
  document.getElementById("hoseForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const state = getState();
    try {
      await persistHoses(hoseDataFromForm(document.getElementById("hoseTable")), state);
      showToast("Schlauchzuordnung gespeichert");
      await onChanged({ afterMutation: true });
    } catch (error) {
      showToast(error.message, { error: true });
      document.getElementById("hoseWarnings").prepend(...warningNodes([{ message: error.message }]));
    }
  });
}
