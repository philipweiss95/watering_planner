const CACHE_NAME = "watering-planner-1.5.6";
const APP_SHELL = [
  "/",
  "/index.html",
  "/styles.css?v=1.5.6",
  "/css/tokens.css?v=1.5.6",
  "/css/base.css?v=1.5.6",
  "/css/layout.css?v=1.5.6",
  "/css/components.css?v=1.5.6",
  "/css/views.css?v=1.5.6",
  "/css/responsive.css?v=1.5.6",
  "/app.js?v=1.5.6",
  "/js/api.js",
  "/js/dashboard.js",
  "/js/diagnostics.js",
  "/js/forecast.js",
  "/js/format.js",
  "/js/history.js",
  "/js/hoses.js",
  "/js/navigation.js",
  "/js/plants.js",
  "/js/refill-reconciliation.js",
  "/js/refresh.js",
  "/js/settings.js",
  "/js/store.js",
  "/js/ui.js",
  "/js/updater.js",
  "/manifest.webmanifest",
  "/icons/app-icon-180.png",
  "/icons/app-icon-192.png",
  "/icons/app-icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;
  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response.ok) {
          const copy = response.clone();
          event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.put(request, copy)));
        }
        return response;
      })
      .catch(() => caches.match(request).then((cached) => cached || caches.match("/index.html"))),
  );
});
