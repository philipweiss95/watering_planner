export function number(value, digits = 0) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return "–";
  return new Intl.NumberFormat("de-DE", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(parsed);
}

export function liters(valueMl, digits = 1) {
  const parsed = Number(valueMl);
  return Number.isFinite(parsed) ? `${number(parsed / 1000, digits)} l` : "–";
}

export function percent(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? `${Math.round(parsed)} %` : "–";
}

export function dateTime(value, options = {}) {
  if (!value) return "–";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "–";
  return new Intl.DateTimeFormat("de-DE", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    ...options,
  }).format(parsed);
}

export function dateLabel(value, options = {}) {
  if (!value) return "–";
  const parsed = new Date(value.length === 10 ? `${value}T12:00:00` : value);
  if (Number.isNaN(parsed.getTime())) return "–";
  return new Intl.DateTimeFormat("de-DE", {
    weekday: "short",
    day: "2-digit",
    month: "2-digit",
    ...options,
  }).format(parsed);
}

export function time(value) {
  if (!value) return "–";
  if (/^\d{2}:\d{2}/.test(value)) return value.slice(0, 5);
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "–";
  return new Intl.DateTimeFormat("de-DE", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
}

export function relativeAge(value, now = new Date()) {
  if (!value) return "Nie";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "Unbekannt";
  const minutes = Math.max(0, Math.round((now.getTime() - parsed.getTime()) / 60000));
  if (minutes < 2) return "Gerade eben";
  if (minutes < 60) return `Vor ${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `Vor ${hours} Std.`;
  return `Vor ${Math.round(hours / 24)} Tagen`;
}

export function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, Number(value) || 0));
}

export function statusLabel(status) {
  return {
    successful: "Erfolgreich",
    estimated: "Geschätzt",
    unserved: "Nicht versorgbar",
    done: "Erledigt",
    due: "Fällig",
    planned: "Geplant",
    missed: "Verpasst",
    blocked: "Blockiert",
    success: "Bereit",
    warning: "Warnung",
    danger: "Fehler",
  }[status] || String(status || "Unbekannt");
}
