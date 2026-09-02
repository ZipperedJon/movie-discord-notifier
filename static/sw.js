/*
 * Service worker for home-screen installs.
 *
 * Deliberately network-only. Its job is to make the app installable and let it
 * run standalone; it caches nothing, because a self-updating app that also
 * caches its own pages is how you end up staring at a version you already
 * replaced. Everything is served fresh from the Pi, which is on your LAN anyway.
 */

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (event) => {
  event.waitUntil(
    // Clear anything a previous version of this worker may have cached.
    caches.keys()
      .then((keys) => Promise.all(keys.map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// A fetch handler is required for the browser to treat the app as installable.
self.addEventListener('fetch', (event) => {
  event.respondWith(fetch(event.request));
});
