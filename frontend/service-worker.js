const STATIC_CACHE = "fieldnote-card-shell-v1";
const PRIVATE_CACHE = "fieldnote-private-card-v1";
const CARD_ASSETS = ["/health-card.css", "/health-card.js", "/tokens.css", "/icons.svg"];

self.addEventListener("install", event => {
  event.waitUntil(caches.open(STATIC_CACHE).then(cache => cache.addAll(CARD_ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith("fieldnote-card-shell-") && key !== STATIC_CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});

self.addEventListener("message", event => {
  if (event.data?.type === "PURGE_PRIVATE_CARD") event.waitUntil(caches.delete(PRIVATE_CACHE));
});

function isPrivateCardData(url) {
  return /^\/api\/v1\/profiles\/[0-9a-f-]{36}\/emergency-card$/i.test(url.pathname);
}

async function cachePrivateResponse(request, response) {
  if (!response.ok) return response;
  const headers = new Headers(response.headers);
  headers.delete("Cache-Control");
  headers.delete("Set-Cookie");
  headers.delete("Content-Length");
  headers.delete("Content-Encoding");
  const cachedCopy = new Response(await response.clone().arrayBuffer(), { status: response.status, statusText: response.statusText, headers });
  await caches.open(PRIVATE_CACHE).then(cache => cache.put(request, cachedCopy));
  return response;
}

self.addEventListener("fetch", event => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (isPrivateCardData(url)) {
    event.respondWith(fetch(request).then(response => cachePrivateResponse(request, response)).catch(async () => {
      const cached = await caches.match(request);
      if (!cached) return new Response(JSON.stringify({ error: "This card has not been saved offline on this device." }), { status: 503, headers: { "Content-Type": "application/json; charset=utf-8" } });
      const headers = new Headers(cached.headers);
      headers.set("X-Fieldnote-Offline", "1");
      return new Response(await cached.arrayBuffer(), { status: cached.status, statusText: cached.statusText, headers });
    }));
    return;
  }

  if (url.pathname === "/health-card.html") {
    event.respondWith(fetch(request).then(async response => {
      if (response.ok) await caches.open(STATIC_CACHE).then(cache => cache.put(request, response.clone()));
      return response;
    }).catch(async () => (await caches.match(request)) || (await caches.match("/health-card.html"))));
    return;
  }

  if (CARD_ASSETS.includes(url.pathname)) {
    event.respondWith(caches.match(request).then(cached => cached || fetch(request).then(async response => {
      if (response.ok) await caches.open(STATIC_CACHE).then(cache => cache.put(request, response.clone()));
      return response;
    })));
  }
});
