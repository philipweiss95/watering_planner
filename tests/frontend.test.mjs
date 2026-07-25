import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { api, ApiError } from "../public/js/api.js";
import { buildDashboardModel, buildTimeline } from "../public/js/dashboard.js";
import { buildDiagnosticRows } from "../public/js/diagnostics.js";
import { buildForecastModel } from "../public/js/forecast.js";
import { validateHoses } from "../public/js/hoses.js";
import { normalizeView } from "../public/js/navigation.js";
import {
  filterPlants,
  plantForm,
  plantPayload,
  plantPayloadFromFormData,
  plantSupplyStatus,
  restorePlant,
} from "../public/js/plants.js";
import { previewSchedule, validateRefillWindows } from "../public/js/settings.js";
import { confirmDialog, escapeHTML } from "../public/js/ui.js";

class FakeNode {
  constructor(tagName = "", text = "") {
    this.tagName = tagName.toUpperCase();
    this.textContent = text;
    this.children = [];
    this.dataset = {};
    this.style = { setProperty() {} };
    this.listeners = {};
    this.open = false;
  }

  append(...children) {
    this.children.push(...children);
  }

  replaceChildren(...children) {
    this.children = [...children];
  }

  setAttribute(name, value) {
    this[name] = value;
  }

  addEventListener(name, handler) {
    this.listeners[name] = handler;
  }

  click() {
    this.listeners.click?.({ target: this, preventDefault() {} });
  }

  focus() {}

  showModal() {
    this.open = true;
  }

  close() {
    this.open = false;
  }
}

function installFakeDOM() {
  const nodes = new Map();
  globalThis.Node = FakeNode;
  globalThis.document = {
    createElement: (tag) => new FakeNode(tag),
    createElementNS: (_namespace, tag) => new FakeNode(tag),
    createTextNode: (text) => new FakeNode("#text", text),
    getElementById: (id) => nodes.get(id) || null,
  };
  return nodes;
}

function findByName(node, name) {
  if (node.name === name) return node;
  for (const child of node.children || []) {
    const found = findByName(child, name);
    if (found) return found;
  }
  return null;
}

test("navigation accepts known views and rejects arbitrary hashes", () => {
  assert.equal(normalizeView("#forecast"), "forecast");
  assert.equal(normalizeView("system"), "system");
  assert.equal(normalizeView("<script>"), "dashboard");
});

test("dashboard prioritizes concrete action states", () => {
  const baseState = {
    weather_status: { last_successful_fetch_at: "2026-07-25T08:00:00Z", stale: false },
    home_assistant: { configured: true, last_error: "" },
    balcony: { timezone_name: "Europe/Berlin" },
    planner_config: { supply_warning_days: 3 },
  };
  const baseEvaluation = {
    tank: { empty_soon: false },
    pump: { consumed_per_cycle_ml: 800 },
    depletion: {},
    remaining_cycles_today: 2,
    recommended_cycles_today: 4,
    automation: { windows: ["07:00", "11:00", "15:00", "19:00"], next_window: "15:00" },
    cycles_completed_today: 2,
  };
  assert.equal(buildDashboardModel(baseState, baseEvaluation).title, "Nächster Lauf 15:00");
  assert.equal(buildDashboardModel(baseState, { ...baseEvaluation, tank: { empty_soon: true } }).action, "fill-main");
  assert.equal(
    buildDashboardModel({ ...baseState, weather_status: { stale: true } }, baseEvaluation).action,
    "reload-weather",
  );
  assert.equal(
    buildDashboardModel({ ...baseState, home_assistant: { configured: true, last_error: "timeout" } }, baseEvaluation).action,
    "test-ha",
  );
  const farShortage = {
    ...baseEvaluation,
    depletion: { first_unserved_watering_at: "2026-08-20T15:00:00+02:00", first_unserved_is_estimated: true },
  };
  assert.equal(
    buildDashboardModel(baseState, farShortage, new Date("2026-07-25T10:00:00+02:00")).title,
    "Nächster Lauf 15:00",
  );
  assert.equal(
    buildDashboardModel(
      baseState,
      { ...farShortage, automation: { ...farShortage.automation, run_now: true, active_window: "11:00" } },
      new Date("2026-07-25T11:00:00+02:00"),
    ).kicker,
    "Jetzt fällig",
  );
  assert.equal(
    buildDashboardModel(
      baseState,
      {
        ...baseEvaluation,
        remaining_cycles_today: 0,
        depletion: { first_unserved_watering_at: "2026-07-27T15:00:00+02:00" },
      },
      new Date("2026-07-25T10:00:00+02:00"),
    ).action,
    "forecast",
  );
  assert.equal(
    buildDashboardModel(
      baseState,
      { ...baseEvaluation, automation: { ...baseEvaluation.automation, paused: true } },
    ).title,
    "Bewässerung fortsetzen",
  );
  assert.equal(
    buildDashboardModel(
      baseState,
      {
        ...baseEvaluation,
        automation: { ...baseEvaluation.automation, catch_up: true },
        refill: { status: "window_missed", blocked: true, severity: "critical" },
      },
    ).title,
    "Bewässerung prüfen",
  );
  assert.notEqual(
    buildDashboardModel(
      { ...baseState, home_assistant: { configured: false, last_error: "" } },
      baseEvaluation,
    ).action,
    "test-ha",
  );
});

test("timeline distinguishes completed, due and blocked cycles", () => {
  const due = buildTimeline({
    cycles_completed_today: 1,
    remaining_cycles_today: 2,
    should_run: true,
    automation: { windows: ["07:00", "12:00", "17:00"], run_now: true },
    tank: {},
  }, new Date("2026-07-25T12:30:00+02:00"));
  assert.deepEqual(due.map((item) => item.status), ["done", "due", "planned"]);
  const blocked = buildTimeline({
    cycles_completed_today: 0,
    remaining_cycles_today: 1,
    should_run: true,
    automation: { windows: ["17:00"] },
    tank: { empty_soon: true },
  });
  assert.equal(blocked[0].status, "blocked");
});

test("forecast model preserves tank paths and first unserved event", () => {
  const model = buildForecastModel({
    first_unserved_watering_at: "2026-07-26T15:00:00+02:00",
    estimated_after_forecast: true,
    forecast_events: [
      {
        at: "2026-07-25T07:00:00+02:00", date: "2026-07-25", event_type: "refill",
        main_tank_after_ml: 8000, refill_tank_after_ml: 14000, status: "estimated",
      },
      {
        at: "2026-07-26T15:00:00+02:00", date: "2026-07-26", event_type: "watering",
        main_tank_after_ml: 200, refill_tank_after_ml: 14000, status: "unserved",
        estimated_weather: true,
      },
    ],
  }, { main: 10000, refill: 20000 });
  assert.equal(model.days.length, 2);
  assert.equal(model.events[0].mainPercent, 80);
  assert.equal(model.events[1].refillPercent, 70);
  assert.equal(model.days[1].unserved, true);
  assert.equal(model.estimated, true);
  assert.equal(model.estimatedStartTimestamp, new Date("2026-07-26T15:00:00+02:00").getTime());
});

test("schedule preview distributes cycles and detects impossible windows", () => {
  assert.deepEqual(previewSchedule("07:00", "19:00", 4, 30).times, ["07:00", "11:00", "15:00", "19:00"]);
  assert.match(previewSchedule("19:00", "07:00", 2, 30).error, /selben Tag/);
  assert.match(previewSchedule("07:00", "08:00", 4, 30).error, /Mindestabstand/);
  assert.equal(validateRefillWindows([{ start: "01:00", end: "02:00" }, { start: "01:30", end: "03:00" }]), "Nachfüllfenster dürfen sich nicht überschneiden.");
});

test("plant filters use actual daily supply", () => {
  const plants = [
    { id: 1, need_ml: 100, delivered_ml: 70 },
    { id: 2, need_ml: 100, delivered_ml: 100 },
    { id: 3, need_ml: 100, delivered_ml: 140 },
  ];
  assert.equal(plantSupplyStatus(plants[0]).key, "under");
  assert.equal(filterPlants(plants, "under")[0].id, 1);
  assert.equal(filterPlants(plants, "over")[0].id, 3);
  assert.equal(filterPlants(plants, "all").length, 3);
});

test("plant form and create, edit, restore payload keep olive as a string", async () => {
  installFakeDOM();
  const state = { catalog: [{ id: "olive", name: "Olive" }, { id: "tomato", name: "Tomate" }] };
  const existing = {
    id: 4,
    catalog_id: "olive",
    custom_name: "Olive",
    size: "medium",
    pot_liters: 30,
    pot_type: "overflow",
  };
  const form = plantForm(state, existing);
  const catalog = findByName(form, "catalog_id");
  assert.equal(catalog.children.find((option) => option.selected).value, "olive");

  const values = new Map([
    ["catalog_id", "olive"],
    ["custom_name", "Olive neu"],
    ["size", "large"],
    ["pot_liters", "35"],
    ["pot_type", "reservoir"],
  ]);
  const created = plantPayloadFromFormData({ get: (key) => values.get(key) });
  const edited = plantPayloadFromFormData({ get: (key) => values.get(key) }, existing);
  assert.equal(created.catalog_id, "olive");
  assert.equal(edited.catalog_id, "olive");
  assert.equal(plantPayload(existing).catalog_id, "olive");

  let restored;
  await restorePlant(existing, {
    post(path, payload) {
      restored = { path, payload };
      return Promise.resolve({ id: 9 });
    },
  });
  assert.equal(restored.path, "/api/plants");
  assert.equal(restored.payload.catalog_id, "olive");
});

test("hose validation reports duplicates, invalid outlets, limits and uncovered plants", () => {
  const warnings = validateHoses(
    [
      { number: "1", outlet_id: 1, plant_id: 1 },
      { number: "1", outlet_id: 99, plant_id: "" },
    ],
    [{ id: 1, name: "15 ml" }],
    [{ id: 1, custom_name: "Minze" }, { id: 2, custom_name: "Tomate" }],
    1,
  );
  const codes = new Set(warnings.map((item) => item.code));
  assert.ok(codes.has("duplicate"));
  assert.ok(codes.has("invalid-outlet"));
  assert.ok(codes.has("unassigned"));
  assert.ok(codes.has("plant-unserved"));
});

test("diagnostics provide all required system rows without secrets", () => {
  const rows = buildDiagnosticRows(
    {
      weather_status: { stale: false, last_successful_fetch_at: "2026-07-25T08:00:00Z" },
      home_assistant: { configured: true, last_error: "" },
      notifications: { enabled: true, configured: true },
    },
    { calculated_at: "2026-07-25T08:01:00Z", automation: {}, refill: { enabled: true } },
    { smtp: { enabled: true, configured: true }, worker_running: true, notification_log: [] },
    { configured: true },
  );
  assert.deepEqual(rows.map((row) => row.id), [
    "weather", "weather-age", "home-assistant", "watering", "refill", "smtp", "database", "updater",
  ]);
  assert.doesNotMatch(JSON.stringify(rows), /password|webhook/i);
  const emptyRefill = buildDiagnosticRows(
    { weather_status: {}, home_assistant: {}, notifications: {} },
    {
      automation: {},
      refill: {
        enabled: true,
        status: "refill_tank_empty",
        severity: "critical",
        blocked: true,
        blocked_reason: "refill_tank_empty",
        summary: "Vorratstank ist leer.",
      },
    },
    {},
    {},
  ).find((row) => row.id === "refill");
  assert.equal(emptyRefill.status, "danger");
});

test("API errors and custom dialog confirmation are observable", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => ({
    ok: false,
    status: 422,
    async json() {
      return { error: "Ungültige Pflanzenart" };
    },
  });
  await assert.rejects(api.post("/api/plants", {}), (error) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.status, 422);
    assert.equal(error.message, "Ungültige Pflanzenart");
    return true;
  });
  globalThis.fetch = originalFetch;

  const nodes = installFakeDOM();
  const dialog = new FakeNode("dialog");
  const nativeCancel = new FakeNode("button");
  dialog.querySelector = () => nativeCancel;
  nodes.set("appDialog", dialog);
  nodes.set("dialogTitle", new FakeNode("h2"));
  nodes.set("dialogContent", new FakeNode("div"));
  const actions = new FakeNode("div");
  nodes.set("dialogActions", actions);

  const accepted = confirmDialog({ title: "Löschen", message: "Sicher?" });
  actions.children[1].click();
  assert.equal(await accepted, true);
  const cancelled = confirmDialog({ title: "Löschen", message: "Sicher?" });
  actions.children[0].click();
  assert.equal(await cancelled, false);
});

test("escaping neutralizes user supplied markup", () => {
  assert.equal(
    escapeHTML('<img src=x onerror="alert(1)"> & Tom'),
    "&lt;img src=x onerror=&quot;alert(1)&quot;&gt; &amp; Tom",
  );
});

test("frontend sources use custom feedback and include mobile boundaries", async () => {
  const [app, ui, plants, hoses, responsive, index] = await Promise.all([
    readFile(new URL("../public/app.js", import.meta.url), "utf8"),
    readFile(new URL("../public/js/ui.js", import.meta.url), "utf8"),
    readFile(new URL("../public/js/plants.js", import.meta.url), "utf8"),
    readFile(new URL("../public/js/hoses.js", import.meta.url), "utf8"),
    readFile(new URL("../public/css/responsive.css", import.meta.url), "utf8"),
    readFile(new URL("../public/index.html", import.meta.url), "utf8"),
  ]);
  const scripts = `${app}\n${ui}\n${plants}\n${hoses}`;
  assert.doesNotMatch(scripts, /window\.(alert|confirm)\s*\(/);
  assert.doesNotMatch(scripts, /\.innerHTML\s*=/);
  assert.match(ui, /confirmDialog/);
  assert.match(`${plants}\n${hoses}`, /Rückgängig/);
  assert.match(responsive, /max-width:\s*390px/);
  assert.match(responsive, /safe-area-inset-bottom/);
  assert.match(index, /id="forecastChart"/);
  assert.match(app, /\/api\/notifications\/test/);
});
