import { api } from "./api.js";
import {
  badge,
  confirmDialog,
  element,
  formDialog,
  progress,
  showToast,
} from "./ui.js";

let updateCheck = null;
const UPDATE_PHASES = [
  { phase: "release", label: "Release suchen" },
  { phase: "download", label: "Paket laden" },
  { phase: "verify", label: "Paket prüfen" },
  { phase: "backup", label: "Sicherung erstellen" },
  { phase: "files", label: "Dateien übernehmen" },
  { phase: "build", label: "Container bauen" },
  { phase: "restart", label: "Planner starten" },
  { phase: "handoff", label: "Updater aktivieren" },
];
const UPDATE_POLL_INTERVAL_MS = 1000;
const UPDATE_RECONNECT_INTERVAL_MS = 2000;
let updaterBinding = null;
let updatePollTimer = null;
let updatePollInFlight = false;
let activeInstallVersion = "";
let activeInstallRequestedAt = 0;

export function buildUpdateProgressModel(operation = {}, reconnecting = false) {
  const declaredTotal = Number(operation.totalSteps);
  const total = Number.isFinite(declaredTotal) && declaredTotal > 0
    ? declaredTotal
    : UPDATE_PHASES.length;
  const declaredStep = Number(operation.step);
  const step = Number.isFinite(declaredStep)
    ? Math.max(0, Math.min(total, declaredStep))
    : 0;
  const completed = operation.status === "ok" && operation.phase === "complete"
    ? total
    : Math.max(0, step - 1);
  const activeIndex = operation.status === "running"
    ? Math.max(0, step - 1)
    : -1;
  const phases = UPDATE_PHASES.map((item, index) => ({
    ...item,
    state: index < completed
      ? "complete"
      : index === activeIndex
        ? "current"
        : "pending",
  }));
  return {
    step,
    total,
    percent: operation.status === "running"
      ? Math.max(3, completed / total * 100)
      : completed / total * 100,
    reconnecting,
    phases,
  };
}

export function parseReleaseNotes(notes) {
  const entries = [];
  let current = "";
  const flush = () => {
    if (current) entries.push(current);
    current = "";
  };
  for (const rawLine of String(notes || "").split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || /^#{1,6}\s+/.test(line)) {
      flush();
      continue;
    }
    const bullet = line.match(/^[-*]\s+(.+)$/);
    if (bullet) {
      flush();
      current = bullet[1];
    } else if (current) {
      current = `${current} ${line}`;
    } else {
      current = line;
    }
  }
  flush();
  return entries;
}

function releaseFrom(updater) {
  if (updateCheck?.release) return updateCheck.release;
  const operation = updater?.lastOperation;
  if (!operation?.releaseNotes) return null;
  return {
    version: operation.targetVersion,
    name: operation.releaseName,
    notes: operation.releaseNotes,
    publishedAt: operation.releasePublishedAt,
  };
}

function renderRelease(updater) {
  const release = releaseFrom(updater);
  const container = document.getElementById("updateRelease");
  const notes = parseReleaseNotes(release?.notes);
  container.hidden = !notes.length;
  if (!notes.length) {
    document.getElementById("updateReleaseNotes").replaceChildren();
    return;
  }
  const version = release?.version || "";
  document.getElementById("updateReleaseTitle").textContent = version
    ? `Änderungen in Version ${version}`
    : "Änderungen";
  document.getElementById("updateReleaseVersion").textContent = version
    ? `v${version}`
    : "Release";
  document.getElementById("updateReleaseNotes").replaceChildren(
    element("ul", {}, notes.map((note) => element("li", { text: note }))),
  );
}

function summary(tone, title, description, trailing = null) {
  return element("article", { className: `update-summary ${tone}` }, [
    element("span", {
      className: `status-dot ${tone}`,
      attrs: { role: "img", "aria-label": title },
    }),
    element("div", { className: "update-summary-copy" }, [
      element("h3", { text: title }),
      element("p", { text: description }),
    ]),
    trailing,
  ]);
}

function operationTime(value) {
  const date = new Date(value || "");
  if (Number.isNaN(date.getTime())) return "gerade eben";
  return date.toLocaleTimeString("de-DE", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function installProgress(updater) {
  const operation = updater?.lastOperation || {};
  const model = buildUpdateProgressModel(
    operation,
    Boolean(updater?.liveConnectionPending),
  );
  const target = operation.targetVersion || "die neue Version";
  const liveText = model.reconnecting
    ? "Planner wird neu gestartet · Verbindung wird automatisch wiederhergestellt"
    : `Live-Status · aktualisiert um ${operationTime(operation.updatedAt)}`;
  return element("section", { className: "update-progress-shell" }, [
    summary(
      "warning",
      `Update auf ${target} läuft`,
      model.reconnecting
        ? "Die Anwendung ist während des Neustarts kurz nicht erreichbar. Die Installation läuft weiter."
        : operation.message || "Das Update wird vorbereitet.",
      badge(`${model.step}/${model.total}`, "warning"),
    ),
    progress(model.percent, "warning active"),
    element(
      "ol",
      {
        className: "update-progress-steps",
        attrs: { "aria-label": "Installationsfortschritt" },
      },
      model.phases.map((item, index) => element(
        "li",
        {
          className: `update-progress-step ${item.state}`,
          attrs: item.state === "current"
            ? { "aria-current": "step" }
            : {},
        },
        [
          element("span", {
            className: "update-progress-index",
            text: item.state === "complete" ? "✓" : String(index + 1),
          }),
          element("span", { text: item.label }),
        ],
      )),
    ),
    element("p", { className: "update-progress-live", text: liveText }),
  ]);
}

function operationIsFresh(operation) {
  if (!activeInstallVersion) return false;
  if (
    operation?.targetVersion
    && operation.targetVersion !== activeInstallVersion
  ) {
    return false;
  }
  if (!activeInstallRequestedAt) return true;
  const updatedAt = Date.parse(operation?.updatedAt || "");
  return Number.isFinite(updatedAt)
    && updatedAt >= activeInstallRequestedAt - 2000;
}

function stopUpdatePolling() {
  if (updatePollTimer !== null) {
    globalThis.clearTimeout(updatePollTimer);
    updatePollTimer = null;
  }
  activeInstallVersion = "";
  activeInstallRequestedAt = 0;
}

function scheduleUpdateStatusPoll(delay = UPDATE_POLL_INTERVAL_MS) {
  if (
    !updaterBinding
    || !activeInstallVersion
    || updatePollInFlight
    || updatePollTimer !== null
  ) {
    return;
  }
  updatePollTimer = globalThis.setTimeout(pollUpdateStatus, delay);
}

async function pollUpdateStatus() {
  updatePollTimer = null;
  if (!updaterBinding || !activeInstallVersion || updatePollInFlight) return;
  updatePollInFlight = true;
  let retryDelay = 0;
  try {
    const updater = await api.get("/api/update/status");
    const nextUpdater = { ...updater, liveConnectionPending: false };
    updaterBinding.setUpdater(nextUpdater);
    const operation = nextUpdater.lastOperation || {};
    if (operation.status === "running" || !operationIsFresh(operation)) {
      retryDelay = UPDATE_POLL_INTERVAL_MS;
    } else {
      const target = activeInstallVersion;
      const succeeded = operation.status === "ok"
        && operation.phase === "complete";
      stopUpdatePolling();
      updateCheck = null;
      if (succeeded) {
        showToast(`Update auf ${target} abgeschlossen. Seite wird neu geladen.`);
        globalThis.setTimeout(() => globalThis.location?.reload?.(), 1500);
      } else {
        showToast(
          operation.message || `Update auf ${target} fehlgeschlagen`,
          { error: true },
        );
      }
    }
  } catch {
    const current = updaterBinding.getData().updater || {};
    updaterBinding.setUpdater({
      ...current,
      liveConnectionPending: true,
    });
    retryDelay = UPDATE_RECONNECT_INTERVAL_MS;
  } finally {
    updatePollInFlight = false;
    if (retryDelay) scheduleUpdateStatusPoll(retryDelay);
  }
}

export function renderUpdater(state, updater) {
  const installedVersion = state?.version || "1.5.7";
  document.getElementById("versionBadge").textContent = `v${installedVersion}`;
  const status = document.getElementById("updaterStatus");
  const operation = updater?.lastOperation;
  const installing = operation?.status === "running";
  status.replaceChildren();
  if (!updater || updater.error) {
    status.append(summary(
      updater?.error ? "danger" : "",
      updater?.error ? "Updater nicht erreichbar" : "Updaterstatus wird geladen",
      updater?.error
        ? "Der interne Update-Dienst antwortet nicht. Der Planner selbst kann weiter verwendet werden."
        : "Die Verbindung zum internen Update-Dienst wird geprüft.",
    ));
  } else if (!updater.configured) {
    status.append(summary(
      "warning",
      "Updater noch nicht verbunden",
      "Einmalig mit dem GitHub-Repository verbinden, um stabile Updates zu suchen und zu installieren.",
    ));
  } else if (installing) {
    status.append(installProgress(updater));
    if (!activeInstallVersion) {
      activeInstallVersion = operation.targetVersion || "unbekannt";
      activeInstallRequestedAt = 0;
    }
    scheduleUpdateStatusPoll();
  } else if (
    operation?.type === "install"
    && operation.status === "error"
    && !updateCheck
  ) {
    status.append(summary(
      "danger",
      `Update auf ${operation.targetVersion || "die neue Version"} fehlgeschlagen`,
      operation.message || "Die bisherige Version bleibt aktiv.",
      badge("Fehler", "danger"),
    ));
  } else if (
    operation?.type === "install"
    && operation.status === "ok"
    && operation.phase === "complete"
    && !updateCheck
  ) {
    status.append(summary(
      "success",
      `Update auf ${operation.targetVersion || installedVersion} abgeschlossen`,
      operation.message || "Die neue Version ist installiert und aktiv.",
      badge("Fertig", "success"),
    ));
  } else if (updateCheck?.updateAvailable) {
    const targetVersion = updateCheck.release?.version || "";
    status.append(summary(
      "success",
      `Version ${targetVersion} ist verfügbar`,
      `Installiert ist Version ${installedVersion}. Vor der Installation werden die Programmdateien gesichert.`,
      badge(`${installedVersion} → ${targetVersion}`, "success"),
    ));
  } else if (updateCheck) {
    status.append(summary(
      "success",
      "Die installierte Version ist aktuell",
      `Version ${updateCheck.currentVersion || installedVersion} ist das neueste stabile Release.`,
      badge("Aktuell", "success"),
    ));
  } else {
    status.append(summary(
      "success",
      `Version ${installedVersion} ist installiert`,
      `Stabiler Update-Kanal · ${updater.repository || "Repository verbunden"}`,
      badge("Verbunden", "success"),
    ));
  }
  renderRelease(updater);
  const setupButton = document.getElementById("updateSetupButton");
  setupButton.textContent = updater?.configured ? "Verbindung ändern" : "Updater verbinden";
  setupButton.disabled = installing;
  document.getElementById("updateCheckButton").disabled = !updater?.configured || installing;
  document.getElementById("updateInstallButton").disabled = !updateCheck?.updateAvailable || installing;
}

export function initUpdater(getData, setUpdater) {
  updaterBinding = { getData, setUpdater };
  document.getElementById("updateSetupButton").addEventListener("click", async () => {
    const current = getData().updater || {};
    const form = element("form", { className: "field-grid" });
    const repository = element("input", {
      name: "repository",
      required: true,
      value: current.repository || "philipweiss95/watering_planner",
      attrs: { autocomplete: "off" },
    });
    const token = element("input", {
      name: "githubToken",
      type: "password",
      required: true,
      minLength: 20,
      attrs: { autocomplete: "new-password" },
    });
    form.append(
      element("label", {}, [element("span", { text: "Repository" }), repository]),
      element("label", {}, [element("span", { text: "GitHub-Token" }), token]),
    );
    const values = await formDialog({
      title: current.configured ? "Updater neu verbinden" : "Updater verbinden",
      form,
      submitText: "Verbinden",
    });
    if (!values) return;
    try {
      const result = await api.post("/api/update/setup", {
        repository: String(values.get("repository")),
        githubToken: String(values.get("githubToken")),
      });
      token.value = "";
      updateCheck = null;
      setUpdater(result);
      showToast("Updater verbunden");
    } catch (error) {
      showToast(error.message, { error: true });
    }
  });
  document.getElementById("updateCheckButton").addEventListener("click", async () => {
    try {
      updateCheck = await api.post("/api/update/check", {});
      const { state, updater } = getData();
      renderUpdater(state, updater);
      showToast(updateCheck.updateAvailable ? "Update verfügbar" : "Version ist aktuell");
    } catch (error) {
      showToast(error.message, { error: true });
    }
  });
  document.getElementById("updateInstallButton").addEventListener("click", async () => {
    const targetVersion = updateCheck?.release?.version || "";
    const accepted = await confirmDialog({
      title: "Update installieren",
      message: `Version ${targetVersion} installieren? Der Planner wird neu gestartet.`,
      confirmText: "Installieren",
    });
    if (!accepted) return;
    const previousUpdater = getData().updater || {};
    activeInstallVersion = targetVersion || "unbekannt";
    activeInstallRequestedAt = Date.now();
    setUpdater({
      ...previousUpdater,
      liveConnectionPending: false,
      lastOperation: {
        type: "install",
        status: "running",
        phase: "queued",
        step: 0,
        totalSteps: UPDATE_PHASES.length,
        targetVersion,
        message: "Installationsauftrag wird an den Updater übergeben.",
        updatedAt: new Date(activeInstallRequestedAt).toISOString(),
      },
    });
    try {
      await api.post("/api/update/install", { confirm: true });
      updateCheck = null;
      showToast("Update wurde gestartet");
      scheduleUpdateStatusPoll(250);
    } catch (error) {
      stopUpdatePolling();
      setUpdater(previousUpdater);
      showToast(error.message, { error: true });
    }
  });
}
