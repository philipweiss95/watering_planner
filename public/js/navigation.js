export const VIEW_NAMES = [
  "dashboard",
  "forecast",
  "plants",
  "hoses",
  "history",
  "settings",
  "system",
  "info",
];

export function normalizeView(value) {
  const requested = String(value || "").replace(/^#/, "");
  return VIEW_NAMES.includes(requested) ? requested : "dashboard";
}

export function initNavigation(onNavigate = () => {}) {
  const buttons = [...document.querySelectorAll("[data-view-target]")];
  const views = [...document.querySelectorAll("[data-view]")];

  function navigate(value, updateHash = true) {
    const selected = normalizeView(value);
    for (const view of views) {
      view.classList.toggle("active", view.dataset.view === selected);
    }
    for (const button of buttons) {
      const active = button.dataset.viewTarget === selected;
      button.classList.toggle("active", active);
      if (active) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    }
    if (updateHash && location.hash !== `#${selected}`) {
      history.replaceState(null, "", `#${selected}`);
    }
    window.scrollTo(0, 0);
    onNavigate(selected);
    return selected;
  }

  for (const button of buttons) {
    button.addEventListener("click", () => navigate(button.dataset.viewTarget));
  }
  window.addEventListener("hashchange", () => navigate(location.hash, false));
  navigate(location.hash, false);
  return navigate;
}
