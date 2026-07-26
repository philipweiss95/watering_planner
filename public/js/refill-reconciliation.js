import { api } from "./api.js";
import { element, formDialog, showToast } from "./ui.js";

function setConditionalField(wrapper, input, enabled) {
  wrapper.hidden = !enabled;
  input.disabled = !enabled;
  input.required = enabled;
}

export function reconciliationForm(run, state) {
  const mainCapacity = Number(state?.balcony?.tank_capacity_ml || 0);
  const refillCapacity = Number(
    state?.balcony?.refill_tank_capacity_ml || 0,
  );
  const unbookedOptions = [
    {
      value: "no_transfer",
      text: "Kein Wasser übertragen",
    },
    {
      value: "full_transfer",
      text: "Freigegebene Menge vollständig übertragen",
    },
    {
      value: "measured_transfer",
      text: "Übertragene Menge gemessen",
    },
  ];
  const reviewOptions = [
    {
      value: "tank_levels_corrected",
      text: "Tankstände direkt korrigiert",
    },
    {
      value: "cancelled_after_review",
      text: "Nach Prüfung aufgelöst",
    },
  ];
  const options = run.status === "completed"
    ? reviewOptions
    : [...unbookedOptions, ...reviewOptions];
  const mode = element("select", {
    name: "mode",
    required: true,
    attrs: { "aria-label": "Ergebnis der manuellen Prüfung" },
  }, options.map((option) => element("option", option)));
  mode.value = options[0]?.value || "";
  const measuredInput = element("input", {
    name: "measured_transfer_ml",
    type: "number",
    min: 1,
    max: 1000000,
    step: 1,
  });
  const mainInput = element("input", {
    name: "main_tank_current_ml",
    type: "number",
    min: 0,
    max: mainCapacity,
    step: 1,
  });
  const refillInput = element("input", {
    name: "refill_tank_current_ml",
    type: "number",
    min: 0,
    max: refillCapacity,
    step: 1,
  });
  const measuredField = element("label", {}, [
    "Gemessene Menge (ml)",
    measuredInput,
  ]);
  const mainField = element("label", {}, [
    "Haupttank aktuell (ml)",
    mainInput,
  ]);
  const refillField = element("label", {}, [
    "Vorratstank aktuell (ml)",
    refillInput,
  ]);
  const updateFields = () => {
    const measured = mode.value === "measured_transfer";
    const corrected = mode.value === "tank_levels_corrected";
    setConditionalField(measuredField, measuredInput, measured);
    setConditionalField(mainField, mainInput, corrected);
    setConditionalField(refillField, refillInput, corrected);
  };
  mode.addEventListener("change", updateFields);
  updateFields();
  return element("form", { className: "form-grid" }, [
    element("p", {
      className: "muted",
      text: `Lauf ${run.run_id}: Bitte nur nach Prüfung von Pumpe und Tankständen auflösen.`,
    }),
    element("label", {}, ["Prüfergebnis", mode]),
    measuredField,
    mainField,
    refillField,
    element("label", {}, [
      "Notiz",
      element("textarea", {
        name: "note",
        maxLength: 500,
        rows: 3,
      }),
    ]),
  ]);
}

function requiredMl(values, name, label, { positive = false } = {}) {
  const raw = values.get(name);
  if (raw === null || String(raw).trim() === "") {
    throw new Error(`${label} darf nicht leer sein`);
  }
  const numeric = Number(raw);
  if (
    !Number.isFinite(numeric)
    || !Number.isInteger(numeric)
    || numeric < (positive ? 1 : 0)
  ) {
    throw new Error(`${label} ist ungültig`);
  }
  return numeric;
}

export function reconciliationPayload(values) {
  const mode = String(values.get("mode") || "");
  const payload = {
    mode,
    note: String(values.get("note") || ""),
  };
  if (mode === "measured_transfer") {
    payload.measured_transfer_ml = requiredMl(
      values,
      "measured_transfer_ml",
      "Gemessene Menge",
      { positive: true },
    );
  } else if (mode === "tank_levels_corrected") {
    payload.main_tank_current_ml = requiredMl(
      values,
      "main_tank_current_ml",
      "Haupttankstand",
    );
    payload.refill_tank_current_ml = requiredMl(
      values,
      "refill_tank_current_ml",
      "Vorratstankstand",
    );
  }
  return payload;
}

export async function reconcileRefillRun(
  run,
  state,
  {
    client = api,
    refresh = async () => {},
  } = {},
) {
  if (!run?.run_id) {
    showToast("Der ungeklärte Nachfülllauf konnte nicht geladen werden", {
      error: true,
    });
    return false;
  }
  const values = await formDialog({
    title: "Nachfülllauf auflösen",
    form: reconciliationForm(run, state),
    submitText: "Abgleich speichern",
  });
  if (!values) return false;
  try {
    await client.post(
      `/api/refill/runs/${encodeURIComponent(run.run_id)}/reconcile`,
      reconciliationPayload(values),
    );
    showToast("Nachfülllauf wurde abgeglichen");
    await refresh();
    return true;
  } catch (error) {
    showToast(error.message, { error: true });
    await refresh();
    return false;
  }
}
