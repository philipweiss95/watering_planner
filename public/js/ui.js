const SVG_NS = "http://www.w3.org/2000/svg";

const iconPaths = {
  activity: ["M22 12h-4l-3 9L9 3l-3 9H2"],
  alert: ["M10.3 2.9 1.8 17a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 2.9a2 2 0 0 0-3.4 0Z", "M12 9v4", "M12 17h.01"],
  bell: ["M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9", "M13.7 21a2 2 0 0 1-3.4 0"],
  "calendar-clock": ["M8 2v4", "M16 2v4", "M3 10h18", "M17 14v3l2 1", "M21 13.5V6a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h8.5", "M16 19a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z"],
  "chart-line": ["M3 3v18h18", "m19 9-5 5-4-4-3 3"],
  check: ["m20 6-11 11-5-5"],
  "chevron-down": ["m6 9 6 6 6-6"],
  clock: ["M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Z", "M12 6v6l4 2"],
  container: ["M4 4h16l-2 16H6L4 4Z", "M4 9h16"],
  database: ["M12 2c5 0 9 1.8 9 4s-4 4-9 4-9-1.8-9-4 4-4 9-4Z", "M3 6v6c0 2.2 4 4 9 4s9-1.8 9-4V6", "M3 12v6c0 2.2 4 4 9 4s9-1.8 9-4v-6"],
  droplets: ["M7 16a4 4 0 0 0 4-4c0-2-4-8-4-8s-4 6-4 8a4 4 0 0 0 4 4Z", "M17 20a4 4 0 0 0 4-4c0-2-4-8-4-8-1.2 1.9-2.3 4"],
  edit: ["M12 20h9", "M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"],
  "git-branch": ["M6 3a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z", "M18 15a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z", "M6 9v3a6 6 0 0 0 6 6h3", "M18 3v9"],
  gauge: ["M20 13a8 8 0 1 0-16 0", "M12 13l4-4", "M4 18h16"],
  history: ["M3 12a9 9 0 1 0 3-6.7L3 8", "M3 3v5h5", "M12 7v5l3 2"],
  info: ["M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Z", "M12 10v6", "M12 7h.01"],
  "layout-dashboard": ["M3 3h7v9H3Z", "M14 3h7v5h-7Z", "M14 12h7v9h-7Z", "M3 16h7v5H3Z"],
  locate: ["M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Z", "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z", "M12 2v2", "M12 20v2", "M2 12h2", "M20 12h2"],
  "mail-check": ["M22 13V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h8", "m22 16-5.5 5L14 18.5", "m22 7-10 6L2 7"],
  "map-pin": ["M20 10c0 5-8 12-8 12S4 15 4 10a8 8 0 1 1 16 0Z", "M12 10a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z"],
  pause: ["M8 5v14", "M16 5v14"],
  play: ["m5 3 14 9-14 9V3Z"],
  plus: ["M12 5v14", "M5 12h14"],
  "refresh-cw": ["M20 11a8 8 0 1 0-2.3 5.7L20 14", "M20 19v-5h-5"],
  settings: ["M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z", "M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.1 3.6-.2-.1a1.7 1.7 0 0 0-2 .2l-.5.3a1.7 1.7 0 0 0-.9 1.5v.2H10v-.2A1.7 1.7 0 0 0 9 21l-.5-.3a1.7 1.7 0 0 0-2-.2l-.2.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9l-.3-.5a1.7 1.7 0 0 0-1.5-.9h-.2V9.4h.2a1.7 1.7 0 0 0 1.5-.9l.3-.5a1.7 1.7 0 0 0-.3-1.9L4.2 6l2.1-3.6.2.1a1.7 1.7 0 0 0 2-.2L9 2a1.7 1.7 0 0 0 1-1.5V.3h4v.2a1.7 1.7 0 0 0 .9 1.5l.5.3a1.7 1.7 0 0 0 2 .2l.2-.1L19.8 6l-.1.1a1.7 1.7 0 0 0-.3 1.9l.3.5a1.7 1.7 0 0 0 1.5.9h.2v4.2h-.2a1.7 1.7 0 0 0-1.5.9Z"],
  sprout: ["M7 20h10", "M12 20v-8", "M12 12C6 12 4 8 4 4c5 0 8 2 8 8Z", "M12 12c0-5 3-8 8-8 0 4-2 8-8 8Z"],
  sun: ["M12 4V2", "M12 22v-2", "m4.9 4.9-1.4-1.4", "m20.5 20.5-1.4-1.4", "M4 12H2", "M22 12h-2", "m4.9 19.1-1.4 1.4", "m20.5 3.5-1.4 1.4", "M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10Z"],
  trash: ["M3 6h18", "M8 6V4h8v2", "M19 6l-1 15H6L5 6", "M10 11v6", "M14 11v6"],
  undo: ["M9 7 4 12l5 5", "M4 12h9a7 7 0 0 1 7 7"],
  wrench: ["M14.7 6.3a4 4 0 0 0-5-5L12 3.6 8.6 7 6.3 4.7a4 4 0 0 0 5 5L3 18l3 3 8.3-8.3a4 4 0 0 0 5-5L17 10l-3-3 2.7-2.7Z"],
  x: ["M18 6 6 18", "M6 6l12 12"],
};

export function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

export function element(tag, options = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(options)) {
    if (value === null || value === undefined) continue;
    if (key === "className") node.className = value;
    else if (key === "text") node.textContent = String(value);
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key === "attrs") {
      for (const [name, attrValue] of Object.entries(value)) {
        node.setAttribute(name, String(attrValue));
      }
    } else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else {
      node[key] = value;
    }
  }
  for (const child of Array.isArray(children) ? children : [children]) {
    if (child === null || child === undefined) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function icon(name, label = "") {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "icon");
  if (label) {
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", label);
  } else {
    svg.setAttribute("aria-hidden", "true");
  }
  for (const pathValue of iconPaths[name] || iconPaths.info) {
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", pathValue);
    svg.append(path);
  }
  return svg;
}

export function hydrateIcons(root = document) {
  for (const placeholder of root.querySelectorAll("[data-icon]")) {
    placeholder.replaceChildren(icon(placeholder.dataset.icon));
  }
}

export function badge(text, state = "") {
  return element("span", { className: `status-badge ${state}`.trim(), text });
}

export function progress(value, state = "") {
  const width = Math.max(0, Math.min(100, Number(value) || 0));
  const fill = element("span");
  fill.style.setProperty("--progress", `${width}%`);
  return element("div", {
    className: `progress ${state}`.trim(),
    attrs: { role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(Math.round(width)) },
  }, fill);
}

let dialogResolve = null;

export function confirmDialog({ title, message, confirmText = "Bestätigen", dangerous = false }) {
  const dialog = document.getElementById("appDialog");
  const titleNode = document.getElementById("dialogTitle");
  const content = document.getElementById("dialogContent");
  const actions = document.getElementById("dialogActions");
  if (!dialog || typeof dialog.showModal !== "function") {
    return Promise.resolve(false);
  }
  if (dialogResolve) dialogResolve(false);
  titleNode.textContent = title;
  content.replaceChildren(element("p", { text: message }));
  const cancel = element("button", { className: "secondary", type: "button", text: "Abbrechen" });
  const accept = element("button", {
    className: dangerous ? "danger-button" : "primary",
    type: "button",
    text: confirmText,
  });
  actions.replaceChildren(cancel, accept);
  return new Promise((resolve) => {
    dialogResolve = resolve;
    const finish = (answer) => {
      if (!dialog.open) return;
      dialogResolve = null;
      dialog.close();
      resolve(answer);
    };
    cancel.addEventListener("click", () => finish(false), { once: true });
    accept.addEventListener("click", () => finish(true), { once: true });
    dialog.querySelector("[value=cancel]").onclick = () => finish(false);
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      finish(false);
    }, { once: true });
    dialog.showModal();
    accept.focus();
  });
}

export function formDialog({ title, form, submitText = "Speichern" }) {
  const dialog = document.getElementById("appDialog");
  const titleNode = document.getElementById("dialogTitle");
  const content = document.getElementById("dialogContent");
  const actions = document.getElementById("dialogActions");
  if (!dialog || typeof dialog.showModal !== "function") return Promise.resolve(null);
  titleNode.textContent = title;
  content.replaceChildren(form);
  const cancel = element("button", { className: "secondary", type: "button", text: "Abbrechen" });
  const accept = element("button", { className: "primary", type: "button", text: submitText });
  actions.replaceChildren(cancel, accept);
  return new Promise((resolve) => {
    const finish = (result) => {
      if (!dialog.open) return;
      dialog.close();
      resolve(result);
    };
    cancel.addEventListener("click", () => finish(null), { once: true });
    dialog.querySelector("[value=cancel]").onclick = () => finish(null);
    accept.addEventListener("click", () => {
      if (!form.reportValidity()) return;
      finish(new FormData(form));
    });
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      finish(null);
    }, { once: true });
    dialog.showModal();
  });
}

export function showToast(message, options = {}) {
  const region = document.getElementById("toastRegion");
  if (!region) return null;
  const toast = element("div", { className: "toast", attrs: { role: options.error ? "alert" : "status" } });
  toast.append(element("span", { text: message }));
  if (options.actionLabel && typeof options.onAction === "function") {
    const action = element("button", { type: "button", text: options.actionLabel });
    action.addEventListener("click", async () => {
      toast.remove();
      await options.onAction();
    }, { once: true });
    toast.append(action);
  }
  region.append(toast);
  const timeout = options.timeout ?? (options.actionLabel ? 8000 : 4200);
  setTimeout(() => toast.remove(), timeout);
  return toast;
}

export function inlineError(container, message) {
  const current = container.querySelector(".inline-error");
  if (!message) {
    current?.remove();
    return;
  }
  const node = current || element("p", { className: "inline-error" });
  node.textContent = message;
  if (!current) container.append(node);
}

export function emptyState(text) {
  return element("div", { className: "empty-state", text });
}
