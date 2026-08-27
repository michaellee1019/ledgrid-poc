const CACHE_PREFIX = 'ledgrid-composer-shell-';
const CACHE_NAME = `${CACHE_PREFIX}v7`;
const SHELL_ASSETS = [
    '/composer',
    '/static/css/composer.css',
    '/static/js/composer_compositor.js',
    '/static/js/composer_runtime.js',
    '/static/js/composer.js',
    '/static/js/composer_native_worker.js',
    '/static/js/composer_python_worker.js',
    '/static/generated/composer/aurora_curtains_native.wasm',
    '/static/generated/composer/compiled_rainbow.wasm',
    '/static/generated/composer/ledgrid_python_runtime.zip',
    '/static/composer.webmanifest',
    '/static/icons/composer-180.png',
    '/static/icons/composer-512.png',
    '/static/icons/composer.svg',
];
const SHELL_ASSET_SET = new Set(SHELL_ASSETS);
const BOOTSTRAP_URL = '/api/v1/composer/bootstrap';

self.addEventListener('install', (event) => {
    event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys()
            .then((names) => Promise.all(names.filter((name) => name.startsWith(CACHE_PREFIX) && name !== CACHE_NAME).map((name) => caches.delete(name))))
            .then(() => self.clients.claim())
    );
});

self.addEventListener('message', (event) => {
    if (event.data?.type === 'SKIP_WAITING') self.skipWaiting();
});

async function networkFirst(request, fallbackKey = request) {
    try {
        const response = await fetch(request);
        if (response.ok && response.type === 'basic') {
            const cache = await caches.open(CACHE_NAME);
            await cache.put(fallbackKey, response.clone());
        }
        return response;
    } catch (error) {
        const cached = await caches.match(fallbackKey);
        if (cached) return cached;
        throw error;
    }
}

self.addEventListener('fetch', (event) => {
    if (event.request.method !== 'GET') return;
    const url = new URL(event.request.url);
    if (url.origin !== self.location.origin) return;

    // Bootstrap is the only API read stored for an offline catalog. Reachability,
    // status, save, validation, and activation requests always go to the network.
    if (url.pathname.startsWith('/api/') && url.pathname !== BOOTSTRAP_URL) return;

    if (url.pathname === BOOTSTRAP_URL) {
        event.respondWith(networkFirst(event.request, BOOTSTRAP_URL));
        return;
    }

    if (event.request.mode === 'navigate' && url.pathname === '/composer') {
        event.respondWith(networkFirst(event.request, '/composer'));
        return;
    }

    if (SHELL_ASSET_SET.has(url.pathname)) {
        event.respondWith(caches.match(url.pathname).then((cached) => cached || fetch(event.request)));
    }
});
