import { dateLabel, liters, statusLabel, time } from "./format.js";
import { badge, element, emptyState } from "./ui.js";

const SVG_NS = "http://www.w3.org/2000/svg";

function svgElement(tag, attributes = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, String(value));
  return node;
}

export function buildForecastModel(depletion, capacities = {}) {
  const events = Array.isArray(depletion?.forecast_events) ? depletion.forecast_events : [];
  const mainCapacity = Math.max(1, Number(capacities.main || depletion?.main_available_ml || 1));
  const refillCapacity = Math.max(1, Number(capacities.refill || depletion?.refill_available_ml || 1));
  const parsed = events.map((event, index) => ({
    ...event,
    index,
    timestamp: new Date(event.at).getTime(),
    mainPercent: Number(event.main_tank_after_ml || 0) / mainCapacity * 100,
    refillPercent: Number(event.refill_tank_after_ml || 0) / refillCapacity * 100,
  })).filter((event) => Number.isFinite(event.timestamp));
  const groups = new Map();
  for (const event of parsed) {
    const date = event.date || new Date(event.timestamp).toISOString().slice(0, 10);
    if (!groups.has(date)) groups.set(date, []);
    groups.get(date).push(event);
  }
  const projected = Array.isArray(depletion?.projected_days) ? depletion.projected_days.slice(0, 16) : [];
  const dates = projected.length
    ? projected.map((day) => day.date)
    : [...groups.keys()].slice(0, 16);
  let mainAfter = Number(depletion?.main_available_ml || 0);
  let refillAfter = Number(depletion?.refill_available_ml || 0);
  const days = dates.map((date) => {
    const dayEvents = groups.get(date) || [];
    if (dayEvents.length) {
      mainAfter = Number(dayEvents.at(-1)?.main_tank_after_ml || 0);
      refillAfter = Number(dayEvents.at(-1)?.refill_tank_after_ml || 0);
    }
    return {
      date,
      events: dayEvents,
      watering: dayEvents.filter((event) => event.event_type === "watering").length,
      refills: dayEvents.filter((event) => event.event_type === "refill" && Number(event.transferred_ml || 0) > 0).length,
      unserved: dayEvents.some((event) => event.status === "unserved" && event.event_type === "watering"),
      mainAfterMl: mainAfter,
      refillAfterMl: refillAfter,
    };
  });
  return {
    events: parsed,
    days,
    firstUnservedAt: depletion?.first_unserved_watering_at || "",
    estimated: Boolean(depletion?.estimated_after_forecast),
  };
}

function linePoints(events, key, width, height, margins) {
  if (!events.length) return "";
  const min = events[0].timestamp;
  const max = Math.max(min + 1, events.at(-1).timestamp);
  return events.map((event) => {
    const x = margins.left + (event.timestamp - min) / (max - min) * (width - margins.left - margins.right);
    const y = margins.top + (100 - Math.max(0, Math.min(100, event[key]))) / 100 * (height - margins.top - margins.bottom);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
}

export function renderForecast(state, evaluation) {
  const depletion = evaluation?.depletion || {};
  const model = buildForecastModel(depletion, {
    main: state?.balcony?.tank_capacity_ml,
    refill: state?.balcony?.refill_tank_capacity_ml,
  });
  const legend = document.getElementById("forecastLegend");
  const legendItem = (label, swatch) => element("span", { className: "legend-item" }, [
    element("span", { className: `legend-swatch ${swatch}`.trim() }),
    element("span", { text: label }),
  ]);
  legend.replaceChildren(
    legendItem("Haupttank", ""),
    legendItem("Vorratstank", "refill"),
    legendItem("Nicht versorgbar", "unserved"),
  );

  const chart = document.getElementById("forecastChart");
  const title = document.getElementById("forecastChartTitle");
  const description = document.getElementById("forecastChartDescription");
  chart.replaceChildren(title, description);
  if (model.events.length) {
    const width = 900;
    const height = 360;
    const margins = { left: 52, right: 24, top: 24, bottom: 50 };
    for (const value of [0, 25, 50, 75, 100]) {
      const y = margins.top + (100 - value) / 100 * (height - margins.top - margins.bottom);
      chart.append(
        svgElement("line", { x1: margins.left, x2: width - margins.right, y1: y, y2: y, class: "grid-line" }),
        Object.assign(svgElement("text", { x: 8, y: y + 4, class: "axis-label" }), { textContent: `${value}%` }),
      );
    }
    if (model.estimated) {
      chart.append(svgElement("rect", {
        x: width * 0.82, y: margins.top, width: width * 0.18 - margins.right,
        height: height - margins.top - margins.bottom, class: "estimated-area",
      }));
      const estimatedLabel = svgElement("text", { x: width * 0.83, y: margins.top + 18, class: "axis-label" });
      estimatedLabel.textContent = "Extrapoliert";
      chart.append(estimatedLabel);
    }
    chart.append(
      svgElement("polyline", { points: linePoints(model.events, "mainPercent", width, height, margins), class: "main-line" }),
      svgElement("polyline", { points: linePoints(model.events, "refillPercent", width, height, margins), class: "refill-line" }),
    );
    const min = model.events[0].timestamp;
    const max = Math.max(min + 1, model.events.at(-1).timestamp);
    model.events.forEach((event, index) => {
      const x = margins.left + (event.timestamp - min) / (max - min) * (width - margins.left - margins.right);
      const y = margins.top + (100 - Math.max(0, Math.min(100, event.mainPercent))) / 100 * (height - margins.top - margins.bottom);
      const className = event.status === "unserved" && event.event_type === "watering"
        ? "event-unserved"
        : event.event_type === "refill" ? "event-refill" : "event-watering";
      const marker = svgElement(event.event_type === "refill" ? "rect" : "circle", event.event_type === "refill"
        ? { x: x - 4, y: y - 4, width: 8, height: 8, class: className }
        : { cx: x, cy: y, r: event.status === "unserved" ? 6 : 4, class: className });
      const markerTitle = svgElement("title");
      markerTitle.textContent = `${dateLabel(event.at)} ${time(event.at)}: ${statusLabel(event.status)}, Haupttank ${liters(event.main_tank_after_ml)}`;
      marker.append(markerTitle);
      chart.append(marker);
      if (index === 0 || index === model.events.length - 1 || index % Math.max(1, Math.round(model.events.length / 7)) === 0) {
        const label = svgElement("text", { x, y: height - 17, class: "axis-label", "text-anchor": "middle" });
        label.textContent = dateLabel(event.at, { weekday: undefined });
        chart.append(label);
      }
    });
  }

  const note = document.getElementById("forecastChartNote");
  note.textContent = model.firstUnservedAt
    ? `Erster nicht versorgbarer Lauf: ${dateLabel(model.firstUnservedAt)} um ${time(model.firstUnservedAt)}.`
    : "Alle dargestellten Bewässerungsläufe sind versorgbar.";
  if (model.estimated) note.textContent += " Der Bereich nach der sicheren Wetterprognose ist extrapoliert.";

  const dayList = document.getElementById("forecastDayList");
  if (!model.days.length) {
    dayList.replaceChildren(emptyState("Noch keine Prognoseereignisse verfügbar."));
    return;
  }
  dayList.replaceChildren(...model.days.map((day) => {
    const copy = day.watering
      ? `${day.watering} Bewässerung${day.watering === 1 ? "" : "en"}${day.refills ? ` · ${day.refills} Nachfüllung${day.refills === 1 ? "" : "en"}` : ""}`
      : (day.refills ? `${day.refills} Nachfüllung${day.refills === 1 ? "" : "en"}` : "Kein Lauf");
    return element("article", { className: "forecast-day" }, [
      element("div", {}, [element("strong", { text: dateLabel(day.date) })]),
      element("div", {}, [
        element("span", { text: copy }),
        element("small", { text: `Haupttank ${liters(day.mainAfterMl)} · Vorrat ${liters(day.refillAfterMl)}` }),
      ]),
      badge(day.unserved ? "Nicht versorgbar" : "Versorgt", day.unserved ? "danger" : "success"),
    ]);
  }));
}
