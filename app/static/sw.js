// Minimal service worker: enables PWA install (iOS "Add to Home Screen" /
// desktop install) and offline caching of static assets. Data pages are
// always fetched live so the dashboard never shows stale jobs silently.
const CACHE = 'careercopilot-v1';
const STATIC_ASSETS = ['/static/style.css', '/static/manifest.json'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(STATIC_ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(event.request).then((hit) => hit || fetch(event.request))
    );
  }
});
