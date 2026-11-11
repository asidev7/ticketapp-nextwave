{% load static %}/* NextWave scanner service worker — caches the app shell only.
   Scan requests (/api/…) always go to the network. */
const CACHE = "nwp-scanner-v2";
const SHELL = [
  "{% static 'css/scanner.css' %}",
  "{% static 'js/scanner.js' %}",
  "{% static 'js/pwa.js' %}",
  "{% static 'fonts/anton-latin.woff2' %}",
  "{% static 'fonts/montserrat-latin-500.woff2' %}",
  "{% static 'fonts/montserrat-latin-700.woff2' %}",
  "{% static 'img/icon-192.png' %}",
  "https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .catch(() => null)
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/scan/photo/")) return;

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(() => new Response(
        "<!doctype html><meta name=viewport content='width=device-width'><body style='font-family:Arial;padding:24px'><h1>Offline</h1><p>Reconnect to the internet to scan tickets.</p>",
        { headers: { "Content-Type": "text/html; charset=utf-8" } }
      ))
    );
    return;
  }

  event.respondWith(caches.match(request).then((hit) => hit || fetch(request)));
});
