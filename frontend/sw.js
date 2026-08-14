/**
 * AgyMobile Service Worker
 * Cache-First Static Asset Caching, Cache Versioning & Network Fallback
 */

const CACHE_VERSION = 'agymobile-v1.0.0';
const PRECACHE_ASSETS = [
  '/',
  '/index.html',
  '/manifest.json',
  '/css/app.css',
  '/css/terminal.css',
  '/css/editor.css',
  '/js/app.js',
  '/js/state.js',
  '/js/api.js',
  '/js/ws.js',
  '/js/components/auth_modal.js',
  '/js/components/dashboard.js',
  '/js/components/subagents.js',
  '/js/components/tool_approval.js',
  '/js/components/artifacts.js',
  '/js/components/file_explorer.js',
  '/js/components/terminal.js',
  '/js/components/telemetry.js',
  '/js/lib/ansi_up.js',
  '/js/lib/qrcode.js',
  '/assets/icons/icon-192.png',
  '/assets/icons/icon-512.png'
];

// 1. Install Event: Precache App Shell
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION)
      .then((cache) => {
        // Use individual add with catch to prevent a single missing file from breaking installation
        return Promise.allSettled(
          PRECACHE_ASSETS.map((url) =>
            cache.add(url).catch((err) => {
              console.warn(`[SW] Precache failed for ${url}:`, err);
            })
          )
        );
      })
      .then(() => self.skipWaiting())
  );
});

// 2. Activate Event: Purge Outdated Cache Generations
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((cacheNames) => {
        return Promise.all(
          cacheNames.map((name) => {
            if (name !== CACHE_VERSION) {
              console.log(`[SW] Purging old cache: ${name}`);
              return caches.delete(name);
            }
          })
        );
      })
      .then(() => self.clients.claim())
  );
});

// 3. Fetch Event: Cache-First for static assets, Network-First for API
self.addEventListener('fetch', (event) => {
  const request = event.request;
  const url = new URL(request.url);

  // Exclude non-GET and dynamic API/WebSocket paths from static caching
  if (request.method !== 'GET' || url.pathname.startsWith('/api/') || url.pathname.startsWith('/ws')) {
    event.respondWith(
      fetch(request).catch(() => {
        if (url.pathname.startsWith('/api/')) {
          return new Response(
            JSON.stringify({ error: 'Offline', message: 'Desktop server unreachable. Check connection.' }),
            {
              status: 503,
              headers: { 'Content-Type': 'application/json' }
            }
          );
        }
        return new Response('Network Error', { status: 503 });
      })
    );
    return;
  }

  // Cache-First Strategy for static assets with Network Fallback
  event.respondWith(
    caches.match(request).then((cachedResponse) => {
      if (cachedResponse) {
        // Return cached asset, fetch update in background (Stale-while-revalidate pattern)
        fetch(request).then((networkResponse) => {
          if (networkResponse && networkResponse.status === 200) {
            caches.open(CACHE_VERSION).then((cache) => cache.put(request, networkResponse));
          }
        }).catch(() => {/* Offline background update failure ignored */});
        return cachedResponse;
      }

      // Not in cache, fetch from network and cache
      return fetch(request).then((networkResponse) => {
        if (!networkResponse || networkResponse.status !== 200 || networkResponse.type !== 'basic') {
          return networkResponse;
        }
        const responseToCache = networkResponse.clone();
        caches.open(CACHE_VERSION).then((cache) => {
          cache.put(request, responseToCache);
        });
        return networkResponse;
      }).catch(() => {
        // If navigation request fails offline, fallback to index.html
        if (request.mode === 'navigate') {
          return caches.match('/index.html');
        }
        return new Response('Resource unavailable offline', { status: 503, statusText: 'Offline' });
      });
    })
  );
});
