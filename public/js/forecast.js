import { dateLabel, liters, statusLabel, time } from "./format.js";
import { badge, element, emptyState } from "./ui.js";

const SVG_NS = "http://www.w3.org/2000/svg";
export const VISIBLE_FORECAST_DAYS = 16;

function svgElement(tag, attributes = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, String(value));
  return node;
}

function calendarDateKey(value) {
  if (!value) return "";
  const source = String(value);
  const prefix = source.match(/^(\d{4}-\d{2}-\d{2})(?:$|T)/)?.[1];
  if (prefix) {
    const parsed = new Date(`${prefix}T00:00:00Z`);
    if (!Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === prefix) return prefix;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "";
  const year = parsed.getFullYear();
  const month = String(parsed.getMonth() + 1).padStart(2, "0");
  const day = String(parsed.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function calendarOrdinal(value) {
  const key = calendarDateKey(value);
  if (!key) return Number.NaN;
  const [year, month, day] = key.split("-").map(Number);
  return Math.floor(Date.UTC(year, month - 1, day) / 86400000);
}

function calendarDateFromOrdinal(value) {
  return new Date(value * 86400000).toISOString().slice(0, 10);
}

function localTimeFraction(value, timezone) {
  const source = String(value || "");
  const direct = source.match(/T(\d{2}):(\d{2})(?::(\d{2}(?:\.\d+)?))?/);
  if (direct) {
    return (
      Number(direct[1]) * 3600
      + Number(direct[2]) * 60
      + Number(direct[3] || 0)
    ) / 86400;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return 0;
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: timezone || "Europe/Berlin",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(parsed);
  const numberPart = (type) =>
    Number(parts.find((part) => part.type === type)?.value || 0);
  return (
    numberPart("hour") * 3600
    + numberPart("minute") * 60
    + numberPart("second")
  ) / 86400;
}

export function buildForecastModel(depletion, capacities = {}) {
  const events = Array.isArray(depletion?.forecast_events) ? depletion.forecast_events : [];
  const mainCapacity = Math.max(1, Number(capacities.main || depletion?.main_available_ml || 1));
  const refillCapacity = Math.max(1, Number(capacities.refill || depletion?.refill_available_ml || 1));
  const timezone = capacities.timezone || "Europe/Berlin";
  const allParsed = events.map((event, index) => ({
    ...event,
    index,
    timestamp: new Date(event.at).getTime(),
    calendarDate: calendarDateKey(event.date || event.at),
    mainPercent: Number(event.main_tank_after_ml || 0) / mainCapacity * 100,
    refillPercent: Number(event.refill_tank_after_ml || 0) / refillCapacity * 100,
  }))
    .filter((event) => Number.isFinite(event.timestamp) && event.calendarDate)
    .map((event) => ({
      ...event,
      axisPosition: calendarOrdinal(event.calendarDate)
        + localTimeFraction(event.at, timezone),
    }))
    .sort((left, right) => left.timestamp - right.timestamp || left.index - right.index);
  const projected = Array.isArray(depletion?.projected_days)
    ? depletion.projected_days
      .map((day) => ({ ...day, date: calendarDateKey(day?.date) }))
      .filter((day) => day.date)
    : [];
  const availableDates = projected.length
    ? projected.map((day) => day.date)
    : allParsed.map((event) => event.calendarDate);
  const ordinals = availableDates.map(calendarOrdinal).filter(Number.isFinite);
  const firstOrdinal = ordinals.length ? Math.min(...ordinals) : Number.NaN;
  const lastOrdinal = ordinals.length ? Math.max(...ordinals) : Number.NaN;
  const visibleDayCount = Number.isFinite(firstOrdinal) && Number.isFinite(lastOrdinal)
    ? Math.min(VISIBLE_FORECAST_DAYS, lastOrdinal - firstOrdinal + 1)
    : 0;
  const dates = Array.from(
    { length: visibleDayCount },
    (_value, index) => calendarDateFromOrdinal(firstOrdinal + index),
  );
  const visibleDates = new Set(dates);
  const parsed = allParsed.filter((event) => visibleDates.has(event.calendarDate));
  const projectedByDate = new Map(projected.map((day) => [day.date, day]));
  const groups = new Map();
  for (const event of parsed) {
    const date = event.calendarDate;
    if (!groups.has(date)) groups.set(date, []);
    groups.get(date).push(event);
  }
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
      estimated: Boolean(
        projectedByDate.get(date)?.estimated
        || dayEvents.some((event) => event.estimated_weather),
      ),
      mainAfterMl: mainAfter,
      refillAfterMl: refillAfter,
    };
  });
  const projectedEstimatedDate = projected.find(
    (day) => visibleDates.has(day.date) && day.estimated,
  )?.date;
  const estimatedDate = projectedEstimatedDate
    || parsed.find((event) => event.estimated_weather)?.calendarDate
    || "";
  const firstUnservedAt = String(depletion?.first_unserved_watering_at || "");
  const visibleUnserved = parsed.find(
    (event) => event.event_type === "watering" && event.status === "unserved",
  );
  return {
    events: parsed,
    days,
    firstUnservedAt: visibleDates.has(calendarDateKey(firstUnservedAt))
      ? firstUnservedAt
      : (visibleUnserved?.at || ""),
    estimated: Boolean(estimatedDate),
    estimatedStartTimestamp: estimatedDate
      ? (parsed.find((event) => event.calendarDate >= estimatedDate)?.timestamp || null)
      : null,
    estimatedStartPosition: estimatedDate
      ? calendarOrdinal(estimatedDate)
      : null,
    horizonStartDate: dates[0] || "",
    horizonEndDate: dates.at(-1) || "",
    horizonDays: dates.length,
    horizonStartPosition: Number.isFinite(firstOrdinal)
      ? firstOrdinal
      : null,
    horizonEndPosition: Number.isFinite(firstOrdinal)
      ? firstOrdinal + dates.length
      : null,
  };
}

function xCoordinate(position, min, max, width, margins) {
  return margins.left
    + (position - min) / Math.max(1, max - min)
      * (width - margins.left - margins.right);
}

function linePoints(events, key, width, height, margins, min, max) {
  if (!events.length) return "";
  const points = events.map((event) => {
    const x = xCoordinate(
      event.axisPosition,
      min,
      max,
      width,
      margins,
    );
    const y = margins.top + (100 - Math.max(0, Math.min(100, event[key]))) / 100 * (height - margins.top - margins.bottom);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const final = events.at(-1);
  if (final.axisPosition < max) {
    const y = margins.top + (100 - Math.max(0, Math.min(100, final[key]))) / 100 * (height - margins.top - margins.bottom);
    points.push(`${(width - margins.right).toFixed(1)},${y.toFixed(1)}`);
  }
  return points.join(" ");
}

export function renderForecast(state, evaluation) {
  const depletion = evaluation?.depletion || {};
  const model = buildForecastModel(depletion, {
    main: state?.balcony?.tank_capacity_ml,
    refill: state?.balcony?.refill_tank_capacity_ml,
    timezone: state?.balcony?.timezone_name,
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
    const min = model.horizonStartPosition;
    const max = model.horizonEndPosition;
    for (const value of [0, 25, 50, 75, 100]) {
      const y = margins.top + (100 - value) / 100 * (height - margins.top - margins.bottom);
      chart.append(
        svgElement("line", { x1: margins.left, x2: width - margins.right, y1: y, y2: y, class: "grid-line" }),
        Object.assign(svgElement("text", { x: 8, y: y + 4, class: "axis-label" }), { textContent: `${value}%` }),
      );
    }
    if (model.estimated) {
      const estimatedStart = model.estimatedStartPosition ?? max;
      const estimatedX = xCoordinate(
        estimatedStart,
        min,
        max,
        width,
        margins,
      );
      chart.append(svgElement("rect", {
        x: estimatedX, y: margins.top, width: Math.max(0, width - margins.right - estimatedX),
        height: height - margins.top - margins.bottom, class: "estimated-area",
      }));
      const estimatedLabel = svgElement("text", { x: estimatedX + 8, y: margins.top + 18, class: "axis-label" });
      estimatedLabel.textContent = "Extrapoliert";
      chart.append(estimatedLabel);
    }
    chart.append(
      svgElement("polyline", {
        points: linePoints(
          model.events,
          "mainPercent",
          width,
          height,
          margins,
          min,
          max,
        ),
        class: "main-line",
      }),
      svgElement("polyline", {
        points: linePoints(
          model.events,
          "refillPercent",
          width,
          height,
          margins,
          min,
          max,
        ),
        class: "refill-line",
      }),
    );
    model.events.forEach((event) => {
      const x = xCoordinate(
        event.axisPosition,
        min,
        max,
        width,
        margins,
      );
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
    });
    const labelInterval = Math.max(
      1,
      Math.ceil(model.days.length / 6),
    );
    const labelIndexes = new Set([
      ...model.days.map((_day, index) => index)
        .filter((index) => index % labelInterval === 0),
      model.days.length - 1,
    ]);
    for (const index of [...labelIndexes].sort((left, right) => left - right)) {
      const day = model.days[index];
      if (!day) continue;
      const x = xCoordinate(
        calendarOrdinal(day.date) + 0.5,
        min,
        max,
        width,
        margins,
      );
      const label = svgElement("text", {
        x,
        y: height - 17,
        class: "axis-label",
        "text-anchor": "middle",
      });
      label.textContent = dateLabel(
        day.date,
        { weekday: undefined },
      );
      chart.append(label);
    }
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
    const statusBadge = day.unserved
      ? badge("Nicht versorgbar", "danger")
      : day.estimated
        ? badge("Geschätzt", "warning")
        : badge("Versorgt", "success");
    return element("article", {
      className: `forecast-day${day.estimated ? " estimated" : ""}`,
    }, [
      element("div", {}, [element("strong", { text: dateLabel(day.date) })]),
      element("div", {}, [
        element("span", { text: copy }),
        element("small", { text: `Haupttank ${liters(day.mainAfterMl)} · Vorrat ${liters(day.refillAfterMl)}` }),
      ]),
      statusBadge,
    ]);
  }));
}
