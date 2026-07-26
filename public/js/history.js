import { dateTime, liters } from "./format.js";
import { badge, element, emptyState, icon } from "./ui.js";

export function renderHistory(events = []) {
  const container = document.getElementById("historyList");
  if (!events.length) {
    container.replaceChildren(emptyState("Noch keine Anlagenereignisse verbucht."));
    return;
  }
  container.replaceChildren(...events.map((event) => {
    const type = event.event_type || "watering";
    const title = event.title || (type === "refill" ? "Nachfüllung" : type === "tank_fill" ? "Tank aufgefüllt" : "Bewässerung");
    const amount = Number(event.amount_ml || event.transferred_ml || 0);
    return element("article", { className: "history-item" }, [
      element("time", { dateTime: event.ran_at, text: dateTime(event.ran_at) }),
      element("span", { className: "history-icon" }, icon(type === "refill" ? "refresh-cw" : "droplets")),
      element("div", { className: "history-copy" }, [
        element("h2", { text: title }),
        element("p", { text: event.detail || (event.source ? `Quelle: ${event.source}` : "") }),
      ]),
      badge(amount ? liters(amount) : "Status", type === "watering" ? "info" : "success"),
    ]);
  }));
}
