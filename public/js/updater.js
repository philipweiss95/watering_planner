import { api } from "./api.js";
import { confirmDialog, element, formDialog, showToast } from "./ui.js";

let updateCheck = null;

export function renderUpdater(state, updater) {
  document.getElementById("versionBadge").textContent = `v${state?.version || "1.5.0"}`;
  const status = document.getElementById("updaterStatus");
  status.replaceChildren();
  if (!updater || updater.error) {
    status.append(element("p", { text: updater?.error ? "Updater nicht erreichbar." : "Updaterstatus wird geladen." }));
  } else if (!updater.configured) {
    status.append(element("p", { text: "Updater ist noch nicht verbunden." }));
  } else {
    status.append(element("p", { text: `Installiert: ${state?.version || "unbekannt"}` }));
    if (updateCheck?.updateAvailable) {
      status.append(element("strong", { text: `Version ${updateCheck.release?.version || ""} verfügbar` }));
    } else if (updateCheck) {
      status.append(element("strong", { text: "Aktuelle Version installiert" }));
    }
  }
  document.getElementById("updateCheckButton").disabled = !updater?.configured;
  document.getElementById("updateInstallButton").disabled = !updateCheck?.updateAvailable;
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
