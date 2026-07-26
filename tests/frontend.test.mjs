import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { api, ApiError } from "../public/js/api.js";
import { buildDashboardModel, buildTimeline } from "../public/js/dashboard.js";
import {
  buildDiagnosticRows,
  renderDiagnostics,
} from "../public/js/diagnostics.js";
import {
  buildForecastModel,
  renderForecast,
  VISIBLE_FORECAST_DAYS,
} from "../public/js/forecast.js";
import { persistHoses, validateHoses } from "../public/js/hoses.js";
import { normalizeView } from "../public/js/navigation.js";
import {
  filterPlants,
  PLANT_SIZE_OPTIONS,
  plantForm,
  plantPayload,
  plantPayloadFromFormData,
  plantSupplyStatus,
  restorePlant,
} from "../public/js/plants.js";
import {
  createRefreshCoordinator,
  loadRefreshSnapshot,
  mergeWeatherStatus,
  refreshIssueMessage,
} from "../public/js/refresh.js";
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

function nodeText(node) {
  return [
    node.textContent || "",
    ...(node.children || []).map(nodeText),
  ].join(" ");
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
      {
        ...baseState,
        planner_config: { supply_warning_days: 30 },
      },
      {
        ...farShortage,
        remaining_cycles_today: 0,
        automation: { windows: [], next_window: "" },
      },
      new Date("2026-07-25T10:00:00+02:00"),
    ).action,
    "forecast",
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

test("forecast chart and day list stop after 16 local calendar days", () => {
  const start = Date.UTC(2026, 2, 20);
  const dateAt = (index) => new Date(start + index * 86400000).toISOString().slice(0, 10);
  const projectedDays = Array.from({ length: 45 }, (_value, index) => ({
    date: dateAt(index),
    estimated: index >= 16,
  }));
  const forecastEvents = projectedDays.map((day, index) => ({
    at: `${day.date}T07:00:00${day.date < "2026-03-29" ? "+01:00" : "+02:00"}`,
    date: day.date,
    event_type: "watering",
    main_tank_after_ml: Math.max(0, 10_000 - index * 200),
    refill_tank_after_ml: 20_000,
    status: index === 39 ? "unserved" : "successful",
    estimated_weather: index >= 16,
  }));
  const depletion = {
    main_available_ml: 10_000,
    refill_available_ml: 20_000,
    first_unserved_watering_at: forecastEvents[39].at,
    estimated_after_forecast: true,
    projected_days: projectedDays,
    forecast_events: forecastEvents,
  };

  const model = buildForecastModel(depletion, { main: 10_000, refill: 20_000 });
  assert.equal(model.horizonDays, VISIBLE_FORECAST_DAYS);
  assert.equal(model.horizonStartDate, "2026-03-20");
  assert.equal(model.horizonEndDate, "2026-04-04");
  assert.equal(model.days.length, 16);
  assert.equal(model.events.length, 16);
  assert.equal(model.events.at(-1).calendarDate, "2026-04-04");
  assert.equal(model.firstUnservedAt, "");
  assert.equal(model.estimated, false);

  const thirtyDayModel = buildForecastModel({
    ...depletion,
    first_unserved_watering_at: forecastEvents[29].at,
    projected_days: projectedDays.slice(0, 30),
    forecast_events: forecastEvents.slice(0, 30).map((event, index) => ({
      ...event,
      status: index === 29 ? "unserved" : event.status,
    })),
  }, { main: 10_000, refill: 20_000 });
  assert.equal(thirtyDayModel.days.length, 16);
  assert.equal(thirtyDayModel.events.length, 16);
  assert.equal(thirtyDayModel.firstUnservedAt, "");

  const nodes = installFakeDOM();
  for (const [id, tag] of [
    ["forecastLegend", "div"],
    ["forecastChart", "svg"],
    ["forecastChartTitle", "title"],
    ["forecastChartDescription", "desc"],
    ["forecastChartNote", "p"],
    ["forecastDayList", "div"],
  ]) nodes.set(id, new FakeNode(tag));
  renderForecast(
    { balcony: { tank_capacity_ml: 10_000, refill_tank_capacity_ml: 20_000 } },
    { depletion },
  );

  const markers = nodes.get("forecastChart").children.filter((child) =>
    ["event-watering", "event-refill", "event-unserved"].includes(child.class));
  assert.equal(markers.length, 16);
  assert.equal(nodes.get("forecastDayList").children.length, 16);
  assert.match(nodes.get("forecastChartNote").textContent, /Alle dargestellten/);
  assert.doesNotMatch(nodes.get("forecastChartNote").textContent, /nicht versorgbarer Lauf/);
});

test("forecast chart spans empty remainder days and marks extrapolation", () => {
  const start = Date.UTC(2026, 9, 24);
  const projectedDays = Array.from({ length: 16 }, (_value, index) => ({
    date: new Date(start + index * 86400000)
      .toISOString()
      .slice(0, 10),
    estimated: index >= 8,
  }));
  const depletion = {
    main_available_ml: 10_000,
    refill_available_ml: 20_000,
    projected_days: projectedDays,
    forecast_events: [
      {
        at: `${projectedDays[0].date}T07:00:00+02:00`,
        date: projectedDays[0].date,
        event_type: "watering",
        main_tank_after_ml: 9_000,
        refill_tank_after_ml: 20_000,
        status: "successful",
      },
      {
        at: `${projectedDays[3].date}T07:00:00+01:00`,
        date: projectedDays[3].date,
        event_type: "watering",
        main_tank_after_ml: 7_000,
        refill_tank_after_ml: 20_000,
        status: "successful",
      },
    ],
  };
  const model = buildForecastModel(
    depletion,
    {
      main: 10_000,
      refill: 20_000,
      timezone: "Europe/Berlin",
    },
  );
  assert.equal(
    model.horizonEndPosition - model.horizonStartPosition,
    16,
  );
  assert.equal(model.events.length, 2);
  assert.equal(model.days.at(-1).events.length, 0);
  assert.equal(model.days[7].estimated, false);
  assert.equal(model.days[8].estimated, true);
  assert.equal(model.days.at(-1).estimated, true);

  const nodes = installFakeDOM();
  for (const [id, tag] of [
    ["forecastLegend", "div"],
    ["forecastChart", "svg"],
    ["forecastChartTitle", "title"],
    ["forecastChartDescription", "desc"],
    ["forecastChartNote", "p"],
    ["forecastDayList", "div"],
  ]) nodes.set(id, new FakeNode(tag));
  renderForecast(
    {
      balcony: {
        tank_capacity_ml: 10_000,
        refill_tank_capacity_ml: 20_000,
        timezone_name: "Europe/Berlin",
      },
    },
    { depletion },
  );

  const chart = nodes.get("forecastChart");
  const mainLine = chart.children.find(
    (child) => child.class === "main-line",
  );
  assert.equal(mainLine.points.trim().split(" ").at(-1).split(",")[0], "876.0");
  const estimatedArea = chart.children.find(
    (child) => child.class === "estimated-area",
  );
  assert.ok(Number(estimatedArea.width) > 400);
  assert.equal(
    Number(estimatedArea.x) + Number(estimatedArea.width),
    876,
  );
  assert.match(
    nodes.get("forecastDayList").children[8].className,
    /estimated/,
  );
  assert.match(
    nodeText(nodes.get("forecastDayList").children[8]),
    /Geschätzt/,
  );
  assert.match(
    nodeText(nodes.get("forecastDayList").children.at(-1)),
    /Geschätzt/,
  );
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
    size: "tree",
    pot_liters: 30,
    pot_type: "overflow",
  };
  const form = plantForm(state, existing);
  const catalog = findByName(form, "catalog_id");
  assert.equal(catalog.children.find((option) => option.selected).value, "olive");
  const size = findByName(form, "size");
  assert.equal(size.children.find((option) => option.selected).value, "tree");
  assert.deepEqual(PLANT_SIZE_OPTIONS.at(-1), ["tree", "Baum/Strauch"]);

  const values = new Map([
    ["catalog_id", "olive"],
    ["custom_name", "Olive neu"],
    ["size", "tree"],
    ["pot_liters", "35"],
    ["pot_type", "reservoir"],
  ]);
  const created = plantPayloadFromFormData({ get: (key) => values.get(key) });
  const edited = plantPayloadFromFormData({ get: (key) => values.get(key) }, existing);
  assert.equal(created.catalog_id, "olive");
  assert.equal(edited.catalog_id, "olive");
  assert.equal(plantPayload(existing).catalog_id, "olive");
  assert.equal(created.size, "tree");
  assert.equal(edited.size, "tree");
  assert.equal(plantPayload(existing).size, "tree");

  let restored;
  await restorePlant(existing, {
    post(path, payload) {
      restored = { path, payload };
      return Promise.resolve({ id: 9 });
    },
  });
  assert.equal(restored.path, "/api/plants");
  assert.equal(restored.payload.catalog_id, "olive");
  assert.equal(restored.payload.size, "tree");
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

test("hose persistence sends one atomic snapshot and no plant updates", async () => {
  const calls = [];
  const client = {
    post(path, payload) {
      calls.push({ method: "POST", path, payload });
      return Promise.resolve({});
    },
    put(path, payload) {
      calls.push({ method: "PUT", path, payload });
      return Promise.resolve({});
    },
  };
  await persistHoses(
    [{ number: "90", outlet_id: 2, plant_id: 15 }],
    {
      outlets: [{ id: 2, name: "Mittel", max_connections: 12 }],
      plants: [{
        id: 15,
        catalog_id: "olive",
        custom_name: "Olivenbaum Altbestand",
        size: "tree",
        pot_liters: 82,
        pot_type: "reservoir_overflow",
      }],
    },
    client,
  );
  assert.equal(calls.length, 1);
  assert.equal(calls[0].path, "/api/hoses");
  assert.deepEqual(calls[0].payload, {
    hoses: [{ number: "90", outlet_id: 2, plant_id: 15 }],
  });
  assert.equal(calls.some((call) => call.method === "PUT"), false);
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
  const activeRows = buildDiagnosticRows(
    { weather_status: {}, home_assistant: {}, notifications: {} },
    {
      automation: {},
      refill: {
        enabled: true,
        active_run: {
          status: "running",
          authorized_transfer_ml: 833,
          authorized_at: "2026-07-25T08:00:00Z",
          started_at: "2026-07-25T08:00:02Z",
          expected_complete_at: "2026-07-25T08:00:52Z",
          elapsed_seconds: 25,
          limit_reasons: ["window_remaining"],
        },
      },
    },
    {},
    {},
  );
  const activeRefill = activeRows.find((row) => row.id === "refill");
  assert.equal(activeRefill.status, "warning");
  assert.equal(activeRefill.action, "");
  assert.match(activeRefill.message, /0,83 l/);
  assert.match(activeRefill.message, /25 s verstrichen/);
  assert.match(activeRefill.message, /Fensterrestzeit/);
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

test("diagnostics expose a safe manual refill reconciliation action", () => {
  const uncertainRun = {
    run_id: "review-refill-1",
    status: "expired",
    needs_manual_review: true,
    error_text: "Abschlussmeldung fehlt.",
  };
  const evaluation = {
    automation: {},
    refill: {
      manual_review_required: true,
      uncertain_runs: [uncertainRun],
    },
  };
  const row = buildDiagnosticRows(
    { weather_status: {}, home_assistant: {}, notifications: {} },
    evaluation,
    {},
    {},
  ).find((item) => item.id === "refill");
  assert.equal(row.action, "reconcile-refill");
  assert.equal(row.actionLabel, "Auflösen");
  assert.equal(row.actionPayload, uncertainRun);

  const nodes = installFakeDOM();
  const diagnosticList = new FakeNode("section");
  nodes.set("diagnosticList", diagnosticList);
  nodes.set("notificationLog", new FakeNode("section"));
  let selected = null;
  renderDiagnostics(
    { weather_status: {}, home_assistant: {}, notifications: {} },
    evaluation,
    { notification_log: [] },
    {},
    {
      "reconcile-refill": (run) => {
        selected = run;
      },
    },
  );
  diagnosticList.children[4].children[3].click();
  assert.equal(selected.run_id, "review-refill-1");
});

test("weather status merges successful refresh and cache fallback metadata", () => {
  const successful = mergeWeatherStatus(
    {
      last_successful_fetch_at: "2026-07-25T08:00:00Z",
      last_error: "old failure",
      stale: true,
    },
    {
      fetched_at: "2026-07-25T09:00:00Z",
      cache_fallback: false,
      stale: false,
    },
    {
      last_successful_fetch_at: "2026-07-25T09:00:00Z",
      last_error: "",
      stale: false,
    },
  );
  assert.equal(
    successful.last_successful_fetch_at,
    "2026-07-25T09:00:00Z",
  );
  assert.equal(successful.last_error, "");
  assert.equal(successful.stale, false);

  const fallback = mergeWeatherStatus(successful, {
    cache_fallback: true,
    weather_error: "Wetterdienst nicht erreichbar.",
    data_age_minutes: 17,
  });
  assert.equal(fallback.cache_fallback, true);
  assert.equal(
    fallback.last_error,
    "Wetterdienst nicht erreichbar.",
  );
  assert.equal(fallback.data_age_minutes, 17);
});

test("failed forced weather refresh keeps data and exposes diagnostics", async () => {
  const previous = {
    evaluation: {
      weather: { fetched_at: "2026-07-25T08:00:00Z" },
    },
    events: [{ id: 1 }],
    notificationDiagnostics: {},
    updater: {},
  };
  const calls = [];
  const apiClient = {
    async get(path) {
      calls.push(path);
      if (path === "/api/weather?force=true&evaluate=true&slot=morning") {
        throw new Error("Wetterdienst nicht erreichbar.");
      }
      if (path === "/api/state") {
        return {
          weather_status: {
            last_successful_fetch_at: "2026-07-25T08:00:00Z",
            last_error: "",
          },
        };
      }
      if (path === "/api/watering-events?limit=50") {
        return { events: [{ id: 2 }] };
      }
      if (path === "/api/update/status") return { configured: true };
      if (path === "/api/diagnostics/notifications") {
        return {
          weather: {
            last_successful_fetch_at: "2026-07-25T08:00:00Z",
            last_attempt_at: "2026-07-25T09:00:00Z",
            last_error: "Wetterdienst nicht erreichbar.",
            cache_fallback: true,
            stale: false,
          },
        };
      }
      throw new Error(`Unexpected path: ${path}`);
    },
  };

  const result = await loadRefreshSnapshot(
    apiClient,
    previous,
    { forceWeather: true },
  );
  assert.equal(
    result.forceError.message,
    "Wetterdienst nicht erreichbar.",
  );
  assert.equal(result.patch.evaluation, previous.evaluation);
  assert.equal(
    result.patch.state.weather_status.cache_fallback,
    true,
  );
  assert.equal(
    result.patch.state.weather_status.last_error,
    "Wetterdienst nicht erreichbar.",
  );
  assert.equal(
    calls.includes("/api/homekit/check?auto=true&slot=morning"),
    false,
  );
  assert.ok(
    calls.indexOf("/api/diagnostics/notifications")
      > calls.indexOf("/api/state"),
  );
});

test("successful forced refresh and cache fallback reach state", async () => {
  async function runScenario({ fallback }) {
    let evaluationFetches = 0;
    const weather = {
      fetched_at: "2026-07-25T09:00:00Z",
      cache_hit: fallback,
      cache_fallback: fallback,
      stale: false,
      ...(fallback
        ? {
          weather_error:
            "Wetterdienst vorübergehend nicht erreichbar.",
        }
        : {}),
    };
    const apiClient = {
      async get(path) {
        if (
          path
          === "/api/weather?force=true&evaluate=true&slot=morning"
        ) {
          return {
            weather,
            evaluation: { weather, automation: {}, refill: {} },
          };
        }
        if (path === "/api/state") {
          return {
            version: "1.5.0",
            weather_status: {
              last_successful_fetch_at: "2026-07-25T08:00:00Z",
            },
          };
        }
        if (path === "/api/homekit/check?auto=true&slot=morning") {
          evaluationFetches += 1;
          return { weather };
        }
        if (path === "/api/watering-events?limit=50") {
          return { events: [] };
        }
        if (path === "/api/update/status") return {};
        if (path === "/api/diagnostics/notifications") {
          return {
            weather: {
              last_successful_fetch_at:
                "2026-07-25T09:00:00Z",
              last_error: fallback
                ? "Wetterdienst vorübergehend nicht erreichbar."
                : "",
              stale: false,
            },
          };
        }
        throw new Error(`Unexpected path: ${path}`);
      },
    };
    const result = await loadRefreshSnapshot(
      apiClient,
      { events: [] },
      { forceWeather: true },
    );
    assert.equal(evaluationFetches, 0);
    return result;
  }

  const successful = await runScenario({ fallback: false });
  assert.equal(successful.forceError, null);
  assert.equal(
    successful.patch.state.weather_status
      .last_successful_fetch_at,
    "2026-07-25T09:00:00Z",
  );
  assert.equal(
    successful.patch.state.weather_status.last_error,
    "",
  );
  assert.equal(
    successful.patch.state.weather_status.cache_fallback,
    false,
  );

  const fallback = await runScenario({ fallback: true });
  assert.equal(fallback.forceError, null);
  assert.equal(
    fallback.patch.state.weather_status.cache_fallback,
    true,
  );
  assert.match(
    fallback.patch.state.weather_status.last_error,
    /vorübergehend/,
  );
});

test("evaluation failure removes the old plan and reports a clear issue", async () => {
  const previous = {
    evaluation: {
      calculated_at: "2026-07-25T08:00:00Z",
      automation: { windows: ["09:00"] },
    },
    events: [],
    notificationDiagnostics: {},
    updater: {},
  };
  const apiClient = {
    async get(path) {
      if (path === "/api/state") {
        return {
          weather_status: {
            last_successful_fetch_at: "2026-07-25T10:00:00Z",
            last_error: "",
            stale: false,
          },
        };
      }
      if (path === "/api/homekit/check?auto=true&slot=morning") {
        throw new Error("Auswertung fehlgeschlagen.");
      }
      if (path === "/api/watering-events?limit=50") {
        return { events: [] };
      }
      if (path === "/api/update/status") return {};
      if (path === "/api/diagnostics/notifications") {
        return {
          weather: {
            last_successful_fetch_at: "2026-07-25T10:00:00Z",
            last_error: "",
            stale: false,
          },
        };
      }
      throw new Error(`Unexpected path: ${path}`);
    },
  };

  let intermediate;
  const result = await loadRefreshSnapshot(
    apiClient,
    previous,
    {
      onState(state, refreshState) {
        intermediate = { state, ...refreshState };
      },
    },
  );
  assert.equal(intermediate.evaluationPending, true);
  assert.equal(result.patch.evaluation, null);
  assert.equal(result.patch.error, "Auswertung fehlgeschlagen.");
  assert.equal(
    result.patch.state.weather_status.last_successful_fetch_at,
    "2026-07-25T10:00:00Z",
  );
  assert.equal(
    refreshIssueMessage(result),
    "Der Tagesplan konnte nicht aktualisiert werden.",
  );
  const dashboard = buildDashboardModel(
    result.patch.state,
    result.patch.evaluation,
  );
  assert.equal(dashboard.kicker, "Plan nicht verfügbar");
  assert.equal(dashboard.title, "Wetterdienst prüfen");
});

test("failed diagnostics cannot overwrite fresher state weather", async () => {
  const previous = {
    evaluation: null,
    events: [],
    notificationDiagnostics: {
      weather: {
        last_successful_fetch_at: "2026-07-24T08:00:00Z",
        last_error: "Alter Diagnosefehler",
        cache_fallback: true,
      },
      notification_log: [],
    },
    updater: {},
  };
  const apiClient = {
    async get(path) {
      if (path === "/api/state") {
        return {
          weather_status: {
            last_successful_fetch_at: "2026-07-25T10:00:00Z",
            last_error: "",
            cache_fallback: false,
            stale: false,
          },
        };
      }
      if (path === "/api/homekit/check?auto=true&slot=morning") {
        return { automation: {}, refill: {} };
      }
      if (path === "/api/watering-events?limit=50") {
        return { events: [] };
      }
      if (path === "/api/update/status") return {};
      if (path === "/api/diagnostics/notifications") {
        throw new Error("Diagnose nicht erreichbar.");
      }
      throw new Error(`Unexpected path: ${path}`);
    },
  };

  const result = await loadRefreshSnapshot(apiClient, previous);
  assert.equal(result.patch.notificationDiagnostics.weather, null);
  assert.equal(
    result.patch.state.weather_status.last_successful_fetch_at,
    "2026-07-25T10:00:00Z",
  );
  assert.equal(result.patch.state.weather_status.last_error, "");
  const weatherRow = buildDiagnosticRows(
    result.patch.state,
    result.patch.evaluation,
    result.patch.notificationDiagnostics,
    result.patch.updater,
  ).find((row) => row.id === "weather");
  assert.equal(weatherRow.status, "success");
  assert.equal(weatherRow.message, "Open-Meteo erreichbar");
});

test("refresh coordinator serializes normal and forced requests", async () => {
  const calls = [];
  const releases = [];
  let active = 0;
  let maximumActive = 0;
  const refresh = createRefreshCoordinator((options) => {
    calls.push(options);
    active += 1;
    maximumActive = Math.max(maximumActive, active);
    return new Promise((resolve) => {
      releases.push(() => {
        active -= 1;
        resolve(options);
      });
    });
  });

  const normal = refresh();
  await Promise.resolve();
  const normalDuplicate = refresh();
  const forced = refresh({ forceWeather: true });
  const forcedDuplicate = refresh({ forceWeather: true });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].forceWeather, false);

  releases[0]();
  await Promise.all([normal, normalDuplicate]);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls.length, 2);
  assert.equal(calls[1].forceWeather, true);
  assert.equal(maximumActive, 1);

  const forcedWhileActive = refresh({ forceWeather: true });
  releases[1]();
  await Promise.all([forced, forcedDuplicate, forcedWhileActive]);
  assert.equal(calls.length, 2);
  assert.equal(maximumActive, 1);
});

test("refresh after mutation is queued behind an active read", async () => {
  const calls = [];
  const releases = [];
  const refresh = createRefreshCoordinator((options) => {
    calls.push(options);
    return new Promise((resolve) => {
      releases.push(() => resolve(options));
    });
  });

  const activeRead = refresh();
  await Promise.resolve();
  const requiredRead = refresh({
    weather: false,
    afterMutation: true,
  });
  const duplicateRead = refresh();
  assert.equal(calls.length, 1);

  releases[0]();
  await Promise.all([activeRead, duplicateRead]);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls.length, 2);
  assert.equal(calls[1].afterMutation, true);
  assert.equal(calls[1].weather, false);

  releases[1]();
  await requiredRead;
  assert.equal(calls.length, 2);
});

test("weather fallback renders newer diagnostics and reload action", () => {
  const nodes = installFakeDOM();
  const diagnosticList = new FakeNode("section");
  const notificationLog = new FakeNode("section");
  nodes.set("diagnosticList", diagnosticList);
  nodes.set("notificationLog", notificationLog);
  let reloads = 0;
  renderDiagnostics(
    {
      weather_status: {
        last_successful_fetch_at: "2026-07-25T08:00:00Z",
        last_error: "",
      },
      home_assistant: {},
      notifications: {},
    },
    { automation: {}, refill: {} },
    {
      weather: {
        last_successful_fetch_at: "2026-07-25T08:00:00Z",
        last_error: "Wetterdienst nicht erreichbar.",
        cache_fallback: true,
        stale: false,
      },
      notification_log: [],
    },
    {},
    {
      "reload-weather": () => {
        reloads += 1;
      },
    },
  );

  const weatherRow = diagnosticList.children[0];
  assert.match(weatherRow.children[0].className, /warning/);
  assert.match(nodeText(weatherRow), /Letzte gültige Daten/);
  weatherRow.children[3].click();
  assert.equal(reloads, 1);
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
