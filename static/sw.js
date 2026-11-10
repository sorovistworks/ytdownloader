// Minimal service worker: it lets phones install the site as an app.
// It caches nothing, so the page and downloads always come fresh from the server.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
