const CACHE_NAME = 'ledgrid-composer-shell-v4';
const SHELL_ASSETS = [
    '/composer',
    '/static/css/composer.css',
    '/static/js/composer_runtime.js',
    '/static/js/composer.js',
    '/static/js/composer_native_worker.js',
    '/static/js/composer_python_worker.js',
    '/static/generated/composer/aurora_curtains_native.wasm',
    '/static/generated/composer/ledgrid_python_runtime.zip',
    '/static/composer.webmanifest',
    '/static/icons/composer-180.png',
    '/static/icons/composer-512.png',
    '/static/icons/composer.svg',
];

self.addEventListener('install', (event) => {
    event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys()
            .then((names) => Promise.all(names.filter((name) => name.startsWith('ledgrid-composer-shell-') && name !== CACHE_NAME).map((name) => caches.delete(name))))
            .then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', (event) => {
    if (event.request.method !== 'GET') return;
    const url = new URL(event.request.url);
    if (url.origin !== self.location.origin) return;

    if (url.pathname === '/api/v1/composer/bootstrap') {
        event.respondWith(
            fetch(event.request)
                .then((response) => {
                    if (response.ok) caches.open(CACHE_NAME).then((cache) => cache.put(event.request, response.clone()));
                    return response;
                })
                .catch(() => caches.match(event.request))
        );
        return;
    }

    const composerAsset = url.pathname === '/composer'
        || url.pathname === '/composer-service-worker.js'
        || url.pathname.startsWith('/static/');
    if (!composerAsset) return;
    event.respondWith(
        fetch(event.request)
            .then((response) => {
                if (response.ok) caches.open(CACHE_NAME).then((cache) => cache.put(event.request, response.clone()));
                return response;
            })
            .catch(() => caches.match(event.request))
    );
});
