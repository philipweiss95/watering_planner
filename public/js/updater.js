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

export function renderUpdater(state, updater) {
  const installedVersion = state?.version || "1.5.2";
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
    const step = Number(operation.step || 0);
    const total = Number(operation.totalSteps || 8);
    status.append(
      summary(
        "warning",
        `Update auf ${operation.targetVersion || "die neue Version"} läuft`,
        operation.message || "Das Update wird vorbereitet.",
        badge(`${step}/${total}`, "warning"),
      ),
      progress(total ? (step / total) * 100 : 0, "warning"),
    );
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
    const accepted = await confirmDialog({
      title: "Update installieren",
      message: `Version ${updateCheck?.release?.version || ""} installieren? Der Planner wird neu gestartet.`,
      confirmText: "Installieren",
    });
    if (!accepted) return;
    try {
      await api.post("/api/update/install", { confirm: true });
      updateCheck = null;
      showToast("Update wurde gestartet");
    } catch (error) {
      showToast(error.message, { error: true });
    }
  });
}
