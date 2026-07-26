import { api } from "./js/api.js";
import { renderDashboard } from "./js/dashboard.js";
import { renderDiagnostics } from "./js/diagnostics.js";
import { renderForecast } from "./js/forecast.js";
import { renderHistory } from "./js/history.js";
import { initHoses, renderHoses } from "./js/hoses.js";
import { initNavigation } from "./js/navigation.js";
import { initPlants, renderPlants } from "./js/plants.js";
import { reconcileRefillRun } from "./js/refill-reconciliation.js";
import {
  createRefreshCoordinator,
  loadRefreshSnapshot,
  refreshIssueMessage,
} from "./js/refresh.js";
import { initSettings, renderSettings } from "./js/settings.js";
import { getStore, setStore } from "./js/store.js";
import { confirmDialog, hydrateIcons, showToast } from "./js/ui.js";
import { initUpdater, renderUpdater } from "./js/updater.js";

// Calibration requests use measured_level_percent in the settings module.
// Keep startup position deterministic for the installed iPhone PWA.
window.scrollTo(0, 0);

let navigate = () => {};
let manualWateringPending = false;
let manualRefillPending = false;

function currentData() {
  return getStore();
}

async function reloadWeather() {
  const result = await refreshAll({ forceWeather: true });
  if (result?.failed) return;
  if (result?.evaluationError) return;
  if (result?.forceError) {
    showToast(result.forceError.message, { error: true });
  } else if (
    result?.weatherStatus?.cache_fallback
    || result?.weatherStatus?.last_error
  ) {
    showToast(
      "Wetterdienst nicht erreichbar. Letzte gültige Daten bleiben aktiv.",
      { error: true },
    );
  } else {
    showToast("Wetterdaten aktualisiert");
  }
}

function renderAll() {
  const data = getStore();
  renderDashboard(data.state, data.evaluation, {
    "fill-main": fillMainTank,
    "fill-refill": fillRefillTank,
    "reload-weather": reloadWeather,
    "test-ha": testHomeAssistant,
    forecast: () => navigate("forecast"),
    "manual-run": runManualWatering,
    "manual-refill": runManualRefill,
    "resume-automation": toggleAutomation,
  });
  if (data.state) {
    renderForecast(data.state, data.evaluation);
    renderPlants(data.state, data.evaluation, refreshAll);
    renderHoses(data.state, refreshAll);
    renderSettings(data.state, data.evaluation, refreshAll);
  }
  renderHistory(data.events);
  renderDiagnostics(data.state, data.evaluation, data.notificationDiagnostics, data.updater, {
    "reload-weather": reloadWeather,
    "test-ha": testHomeAssistant,
    "test-email": testEmail,
    "toggle-automation": toggleAutomation,
    "manual-refill": runManualRefill,
    "reconcile-refill": (run) => reconcileRefillRun(
      run,
      getStore().state,
      {
        refresh: () => refreshAll({
          weather: false,
          afterMutation: true,
        }),
      },
    ),
    "open-updater": () => navigate("info"),
  });
  renderUpdater(data.state, data.updater);
  document.getElementById("headerSubtitle").textContent = data.evaluation?.weather?.simulation ? "Simulation" : "Meine Terrasse";
}

async function performRefresh(options = {}) {
  document.getElementById("headerStatus").textContent = "Wird aktualisiert";
  try {
    const snapshot = await loadRefreshSnapshot(api, getStore(), {
      ...options,
      onState: (state, refreshState) => {
        document.documentElement.dataset.appVersion =
          `v${state.version || "1.5.5"}`;
        setStore({
          state,
          loading: false,
          ...(refreshState.evaluationPending
            ? { evaluation: null }
            : {}),
        });
        renderAll();
      },
    });
    setStore(snapshot.patch);
    renderAll();
    const issueMessage = refreshIssueMessage(snapshot);
    if (issueMessage) showToast(issueMessage, { error: true });
    return snapshot;
  } catch (error) {
    setStore({ loading: false, error: error.message });
    document.getElementById("headerStatus").textContent = "Server nicht erreichbar";
    showToast("Daten konnten nicht geladen werden", { error: true });
    return { failed: true, error };
  }
}

const coordinatedRefresh = createRefreshCoordinator(performRefresh);

function refreshAll(options = {}) {
  return coordinatedRefresh(options);
}

async function fillMainTank() {
  const accepted = await confirmDialog({
    title: "Haupttank auffüllen",
    message: "Den rechnerischen Haupttankstand auf 100 Prozent setzen?",
    confirmText: "Als voll markieren",
  });
  if (!accepted) return;
  try {
    await api.post("/api/tanks/main/fill", {});
    showToast("Haupttank als voll markiert");
    await refreshAll({ afterMutation: true });
  } catch (error) {
    showToast(error.message, { error: true });
  }
}

async function fillRefillTank() {
  const accepted = await confirmDialog({
    title: "Vorratstank auffüllen",
    message: "Den rechnerischen Vorratstankstand auf 100 Prozent setzen?",
    confirmText: "Als voll markieren",
  });
  if (!accepted) return;
  try {
    await api.post("/api/tanks/refill/fill", {});
    showToast("Vorratstank als voll markiert");
    await refreshAll({ afterMutation: true });
  } catch (error) {
    showToast(error.message, { error: true });
  }
}

function uniqueRunId(type) {
  const suffix = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `ui-${type}-${suffix}`;
}

async function runManualWatering() {
  if (manualWateringPending) return;
  const evaluation = getStore().evaluation;
  if (!evaluation?.manual_run?.available) {
    showToast(
      evaluation?.manual_run?.reason
        || "Manuelles Gießen ist derzeit nicht möglich",
      { error: true },
    );
    return;
  }
  const accepted = await confirmDialog({
    title: "Bewässerung starten",
    message: `Einen vollständigen Pumpenlauf mit ${Math.round(evaluation?.pump?.consumed_per_cycle_ml || 0)} ml kalibriertem Tankverbrauch anfordern?`,
    confirmText: "Lauf starten",
  });
  if (!accepted) return;
  manualWateringPending = true;
  try {
    await api.post("/api/manual-run", { auto_weather: true, run_id: uniqueRunId("watering") });
    showToast("Pumpenlauf an Home Assistant übergeben");
    await refreshAll({ afterMutation: true });
  } catch (error) {
    showToast(error.message, { error: true });
  } finally {
    manualWateringPending = false;
  }
}

async function runManualRefill() {
  if (manualRefillPending) return;
  const evaluation = getStore().evaluation;
  const refill = evaluation?.manual_refill || {};
  const availabilitySpecified =
    typeof refill.available === "boolean";
  const fallbackAvailable = Boolean(
    getStore().state?.home_assistant?.configured
    && Number(refill.planned_transfer_ml || 0) > 0
    && !evaluation?.refill?.active_run
    && !evaluation?.refill?.manual_review_required
    && !evaluation?.refill?.cooldown_active,
  );
  if (
    availabilitySpecified
      ? !refill.available
      : !fallbackAvailable
  ) {
    showToast(evaluation?.manual_refill?.reason || "Nachfüllung ist derzeit nicht möglich", { error: true });
    return;
  }
  const accepted = await confirmDialog({
    title: "Nachfüllung starten",
    message: `Geplante Nachfüllmenge ${Math.round(refill.planned_transfer_ml || 0)} ml an Home Assistant übergeben?`,
    confirmText: "Nachfüllung starten",
  });
  if (!accepted) return;
  manualRefillPending = true;
  try {
    await api.post("/api/manual-refill", { auto_weather: true, run_id: uniqueRunId("refill") });
    showToast("Nachfüllung an Home Assistant übergeben");
    await refreshAll({ afterMutation: true });
  } catch (error) {
    showToast(error.message, { error: true });
  } finally {
    manualRefillPending = false;
  }
}

async function toggleAutomation() {
  const paused = Boolean(getStore().evaluation?.automation?.paused);
  try {
    await api.post(paused ? "/api/automation/resume" : "/api/automation/pause", {});
    showToast(paused ? "Bewässerungsautomatik fortgesetzt" : "Bewässerungsautomatik bis morgen pausiert");
    await refreshAll({ weather: false, afterMutation: true });
  } catch (error) {
    showToast(error.message, { error: true });
  }
}

async function testHomeAssistant() {
  try {
    const result = await api.post("/api/diagnostics/home-assistant/test", {});
    showToast(result.reachable ? "Home Assistant ist erreichbar" : "Home Assistant ist nicht erreichbar", { error: !result.reachable });
  } catch {
    showToast("Home Assistant ist nicht erreichbar", { error: true });
  }
  await refreshAll({ weather: false, afterMutation: true });
}

async function testEmail() {
  try {
    await api.post("/api/notifications/test", {});
    showToast("Test-E-Mail gesendet");
  } catch (error) {
    showToast(error.message, { error: true });
  }
  await refreshAll({ weather: false, afterMutation: true });
}

function bindControls() {
  document.getElementById("refreshDashboardButton").addEventListener("click", () => refreshAll());
  document.getElementById("refreshHistoryButton").addEventListener("click", () => refreshAll({ weather: false }));
  document.getElementById("refreshSystemButton").addEventListener("click", async () => {
    try {
      await api.post("/api/diagnostics/notifications/check", {});
    } catch {
      // A failed condition check is reflected in the refreshed system rows.
    }
    await refreshAll({ weather: false, afterMutation: true });
  });
  initPlants(currentData, refreshAll);
  initHoses(() => getStore().state, refreshAll);
  initSettings(currentData, refreshAll, (evaluation) => {
    setStore({ evaluation, simulation: true });
    renderAll();
    navigate("dashboard");
  });
  initUpdater(currentData, (updater) => {
    setStore({ updater });
    renderUpdater(getStore().state, updater);
  });
}

hydrateIcons();
navigate = initNavigation();
bindControls();

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}

refreshAll();
