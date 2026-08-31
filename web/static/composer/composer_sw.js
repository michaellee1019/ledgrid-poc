const CACHE_NAME = 'composer-shell-v1';
const ROOT_SHELL = '/';
const SHELL = [
  ROOT_SHELL,
  '/static/css/composer_slice.css?v=composer-shell-v1',
  '/static/js/composer_preview_scheduler.js?v=composer-shell-v1',
  '/static/js/composer_slice.js?v=composer-shell-v1',
  '/static/js/composer_navigation.js?v=composer-shell-v1',
  '/static/js/composer_state_explanation.js?v=composer-shell-v1',
  '/static/js/composer_shell.js?v=composer-shell-v1',
  '/static/composer/manifest.webmanifest?v=composer-shell-v1',
  '/static/composer/icon.svg?v=composer-shell-v1',
  '/static/composer/offline.html?v=composer-shell-v1',
];

self.addEventListener('install', (event) => event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL))));
self.addEventListener('activate', (event) => event.waitUntil(caches.keys().then((names) => Promise.all(names.filter((name) => name.startsWith('composer-shell-') && name !== CACHE_NAME).map((name) => caches.delete(name)))).then(() => self.clients.claim())));
self.addEventListener('message', (event) => { if (event.data && event.data.type === 'composer-shell-activate') self.skipWaiting(); });
self.addEventListener('fetch', (event) => {
  const request = event.request; const url = new URL(request.url);
  if (url.origin !== self.location.origin || !url.pathname.startsWith('/api/')) return;
  event.respondWith(fetch(request).catch(async () => {
    const client = event.clientId && await self.clients.get(event.clientId);
    if (client) client.postMessage({type:'composer-server-unavailable'});
    return new Response(JSON.stringify({error:'Local Composer server unavailable.'}), {status:503, headers:{'Content-Type':'application/json', 'Cache-Control':'no-store'}});
  }));
});
self.addEventListener('fetch', (event) => {
  const request = event.request; const url = new URL(request.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith('/api/') || request.method !== 'GET') return;
  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).catch(async () => (await caches.match(ROOT_SHELL)) || caches.match('/static/composer/offline.html?v=composer-shell-v1')));
    return;
  }
  const shellKey = `${url.pathname}${url.search}`;
  if (SHELL.includes(shellKey)) event.respondWith(fetch(request).catch(() => caches.match(shellKey)));
});
