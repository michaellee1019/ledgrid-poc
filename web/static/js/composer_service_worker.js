'use strict';

const CACHE_PREFIX = 'ledgrid-composer-shell-';
const CACHE_VERSION = 'v15';
const CACHE_NAME = `${CACHE_PREFIX}${CACHE_VERSION}`;
const RUNTIME_CACHE_NAME = `${CACHE_NAME}-python-runtime`;
const OFFLINE_MANIFEST_URL = '/static/generated/composer/offline_assets.json';
const OFFLINE_METADATA_URL = '/.ledgrid-composer/offline-metadata';
const BOOTSTRAP_URL = '/api/v1/composer/bootstrap';
const PROFILE_ARTIFACT_PATH = /^\/api\/v1\/installation-profiles\/([0-9a-f]{64})\/artifact$/;
const PYODIDE_VERSION = '314.0.5';
const PYODIDE_BASE_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
const REQUIRED_PYTHON_PACKAGES = Object.freeze(['numpy', 'pillow']);
const SHELL_ASSETS = [
    '/composer',
    '/composer-service-worker.js',
    '/static/css/composer.css',
    '/static/js/composer_compositor.js',
    '/static/js/composer_state.js',
    '/static/js/composer_runtime.js',
    '/static/js/composer.js',
    '/static/js/composer_native_worker.js',
    '/static/js/composer_python_worker.js',
    '/static/generated/composer/aurora_curtains_native.wasm',
    '/static/generated/composer/compiled_rainbow.wasm',
    '/static/generated/composer/ledgrid_python_runtime.zip',
    '/static/generated/composer/offline_assets.json',
    '/static/composer.webmanifest',
    '/static/icons/composer-180.png',
    '/static/icons/composer-512.png',
    '/static/icons/composer.svg',
];
const SHELL_ASSET_SET = new Set(SHELL_ASSETS);
let metadataUpdate = Promise.resolve();

function hex(bytes) {
    return Array.from(new Uint8Array(bytes), (value) => value.toString(16).padStart(2, '0')).join('');
}

async function responseDigest(response) {
    const payload = await response.clone().arrayBuffer();
    return {
        bytes: payload.byteLength,
        sha256: hex(await crypto.subtle.digest('SHA-256', payload)),
    };
}

async function verifiedProfileArtifact(response, expectedDigest) {
    const payload = new Uint8Array(await response.clone().arrayBuffer());
    if (
        payload.byteLength < 100
        || payload[0] !== 0x4c
        || payload[1] !== 0x47
        || payload[2] !== 0x49
        || payload[3] !== 0x50
    ) {
        throw new Error('The installation-profile artifact is not a complete LGIP document.');
    }
    const embedded = hex(payload.slice(68, 100));
    if (embedded !== expectedDigest) {
        throw new Error('The installation-profile artifact identity does not match its URL.');
    }
    const canonical = payload.slice();
    canonical.fill(0, 68, 100);
    const calculated = hex(await crypto.subtle.digest('SHA-256', canonical));
    if (calculated !== expectedDigest) {
        throw new Error('The installation-profile artifact failed its embedded digest check.');
    }
    return {
        bytes: payload.byteLength,
        sha256: hex(await crypto.subtle.digest('SHA-256', payload)),
        contentDigest: expectedDigest,
    };
}

async function readMetadata() {
    const cache = await caches.open(CACHE_NAME);
    const response = await cache.match(OFFLINE_METADATA_URL);
    if (!response) return null;
    try {
        return await response.json();
    } catch (_error) {
        return null;
    }
}

async function writeMetadata(value) {
    const cache = await caches.open(CACHE_NAME);
    await cache.put(OFFLINE_METADATA_URL, new Response(JSON.stringify(value), {
        headers: {'Content-Type': 'application/json', 'Cache-Control': 'no-store'},
    }));
    return value;
}

function updateMetadata(mutator) {
    metadataUpdate = metadataUpdate.then(async () => {
        const current = await readMetadata();
        if (!current || current.cacheVersion !== CACHE_VERSION) return current;
        return writeMetadata(await mutator(current));
    });
    return metadataUpdate;
}

async function fetchRequired(url) {
    const response = await fetch(url, {cache: 'reload'});
    if (!response.ok || !['basic', 'cors'].includes(response.type)) {
        throw new Error(`Offline asset ${url} returned ${response.status || response.type}`);
    }
    return response;
}

async function installVersionedShell() {
    try {
        const manifestResponse = await fetchRequired(OFFLINE_MANIFEST_URL);
        const manifest = await manifestResponse.clone().json();
        if (
            manifest?.schema !== 'ledgrid.composer-offline-assets'
            || manifest.schemaVersion !== 1
            || manifest.cacheVersion !== CACHE_VERSION
        ) {
            throw new Error('The composer offline asset manifest is incompatible.');
        }
        if (manifest.pythonRuntime?.version !== PYODIDE_VERSION) {
            throw new Error('The composer and offline manifest disagree on Pyodide.');
        }
        const expected = new Map(
            (manifest.localAssets || []).map((asset) => [asset.url, asset]),
        );
        const cache = await caches.open(CACHE_NAME);
        const verifiedShell = {};
        for (const url of SHELL_ASSETS) {
            const response = url === OFFLINE_MANIFEST_URL
                ? manifestResponse.clone()
                : await fetchRequired(url);
            const digest = await responseDigest(response);
            const declared = expected.get(url);
            if (declared && (
                declared.sha256 !== digest.sha256 || declared.bytes !== digest.bytes
            )) {
                throw new Error(`Offline asset digest mismatch: ${url}`);
            }
            await cache.put(url, response.clone());
            verifiedShell[url] = {
                ...digest,
                expected: Boolean(declared),
            };
        }
        await writeMetadata({
            schema: 'ledgrid.composer-offline-cache-state',
            schemaVersion: 1,
            cacheVersion: CACHE_VERSION,
            shellComplete: true,
            verifiedShell,
            bootstrap: null,
            pythonRuntime: {
                baseUrl: PYODIDE_BASE_URL,
                packages: [],
                prepared: false,
                version: PYODIDE_VERSION,
            },
            pythonAssets: {},
        });
        await self.skipWaiting();
    } catch (error) {
        await caches.delete(CACHE_NAME);
        await caches.delete(RUNTIME_CACHE_NAME);
        throw error;
    }
}

self.addEventListener('install', (event) => {
    event.waitUntil(installVersionedShell());
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys()
            .then((names) => Promise.all(names.filter((name) => (
                name.startsWith(CACHE_PREFIX)
                && name !== CACHE_NAME
                && name !== RUNTIME_CACHE_NAME
            )).map((name) => caches.delete(name))))
            .then(() => self.clients.claim())
    );
});

async function rememberBootstrap(response) {
    const digest = await responseDigest(response);
    await updateMetadata((metadata) => ({
        ...metadata,
        bootstrap: digest,
    }));
}

async function networkFirst(request, fallbackKey = request) {
    try {
        const response = await fetch(request);
        if (!response.ok) throw new Error(`Network response was ${response.status}`);
        if (response.type === 'basic') {
            const cache = await caches.open(CACHE_NAME);
            await cache.put(fallbackKey, response.clone());
            if (fallbackKey === BOOTSTRAP_URL) await rememberBootstrap(response);
        }
        return response;
    } catch (error) {
        const cached = await caches.match(fallbackKey);
        if (cached) return cached;
        throw error;
    }
}

async function cachePinnedPythonRuntime(request) {
    const cache = await caches.open(RUNTIME_CACHE_NAME);
    const cached = await cache.match(request);
    if (cached) return cached;
    const response = await fetch(request);
    if (!response.ok || response.type !== 'cors') return response;
    const digest = await responseDigest(response);
    await cache.put(request, response.clone());
    await updateMetadata((metadata) => ({
        ...metadata,
        pythonAssets: {
            ...(metadata.pythonAssets || {}),
            [request.url]: digest,
        },
    }));
    return response;
}

async function cacheImmutableProfileArtifact(request, expectedDigest) {
    const cache = await caches.open(CACHE_NAME);
    const cached = await cache.match(request);
    if (cached) {
        await verifiedProfileArtifact(cached, expectedDigest);
        return cached;
    }
    const response = await fetch(request, {cache: 'no-cache'});
    if (!response.ok || response.type !== 'basic') return response;
    const identity = await verifiedProfileArtifact(response, expectedDigest);
    await cache.put(request, response.clone());
    await updateMetadata((metadata) => ({
        ...metadata,
        profileArtifacts: {
            ...(metadata.profileArtifacts || {}),
            [new URL(request.url).pathname]: identity,
        },
    }));
    return response;
}

async function verifiedOfflineStatus() {
    const metadata = await readMetadata();
    if (!metadata || metadata.cacheVersion !== CACHE_VERSION || !metadata.shellComplete) {
        return {
            type: 'OFFLINE_STATUS', readyOffline: false,
            reason: 'The versioned composer shell has not finished caching.',
        };
    }
    const shell = await caches.open(CACHE_NAME);
    for (const [url, recorded] of Object.entries(metadata.verifiedShell || {})) {
        const response = await shell.match(url);
        if (!response) {
            return {type: 'OFFLINE_STATUS', readyOffline: false, reason: `Missing offline asset: ${url}`};
        }
        const digest = await responseDigest(response);
        if (digest.sha256 !== recorded.sha256 || digest.bytes !== recorded.bytes) {
            return {type: 'OFFLINE_STATUS', readyOffline: false, reason: `Offline asset changed: ${url}`};
        }
    }
    const bootstrap = await shell.match(BOOTSTRAP_URL);
    if (!bootstrap || !metadata.bootstrap) {
        return {
            type: 'OFFLINE_STATUS', readyOffline: false,
            reason: 'Open the composer online once to cache its renderer catalog.',
        };
    }
    const bootstrapDigest = await responseDigest(bootstrap);
    if (
        bootstrapDigest.sha256 !== metadata.bootstrap.sha256
        || bootstrapDigest.bytes !== metadata.bootstrap.bytes
    ) {
        return {type: 'OFFLINE_STATUS', readyOffline: false, reason: 'The cached renderer catalog could not be verified.'};
    }
    let bootstrapPayload;
    try {
        bootstrapPayload = await bootstrap.clone().json();
    } catch (_error) {
        return {type: 'OFFLINE_STATUS', readyOffline: false, reason: 'The cached renderer catalog is unreadable.'};
    }
    const profileArtifactUrl = bootstrapPayload?.capabilities?.server_actions
        ?.installation_profile_artifact_url;
    let profilePath;
    try {
        profilePath = new URL(profileArtifactUrl, self.location.origin).pathname;
    } catch (_error) {
        profilePath = '';
    }
    const profileMatch = PROFILE_ARTIFACT_PATH.exec(profilePath);
    const recordedProfile = metadata.profileArtifacts?.[profilePath];
    const cachedProfile = profileMatch ? await shell.match(profilePath) : null;
    if (!profileMatch || !recordedProfile || !cachedProfile) {
        return {
            type: 'OFFLINE_STATUS', readyOffline: false,
            reason: 'Open a managed installation profile online before working offline.',
        };
    }
    try {
        const actualProfile = await verifiedProfileArtifact(cachedProfile, profileMatch[1]);
        if (
            actualProfile.sha256 !== recordedProfile.sha256
            || actualProfile.bytes !== recordedProfile.bytes
            || actualProfile.contentDigest !== recordedProfile.contentDigest
        ) {
            throw new Error('recorded profile identity changed');
        }
    } catch (_error) {
        return {
            type: 'OFFLINE_STATUS', readyOffline: false,
            reason: 'The cached installation profile could not be verified.',
        };
    }
    const prepared = metadata.pythonRuntime || {};
    if (
        !prepared.prepared
        || prepared.version !== PYODIDE_VERSION
        || !REQUIRED_PYTHON_PACKAGES.every((name) => prepared.packages?.includes(name))
    ) {
        return {
            type: 'OFFLINE_STATUS', readyOffline: false,
            reason: 'Prepare the pinned Python renderer and packages for offline use.',
        };
    }
    const runtime = await caches.open(RUNTIME_CACHE_NAME);
    const pythonAssets = Object.entries(metadata.pythonAssets || {});
    const entrypoint = `${PYODIDE_BASE_URL}pyodide.mjs`;
    if (!metadata.pythonAssets?.[entrypoint]) {
        return {
            type: 'OFFLINE_STATUS', readyOffline: false,
            reason: 'The pinned Python runtime entrypoint is not cached.',
        };
    }
    for (const [url, recorded] of pythonAssets) {
        const response = await runtime.match(url);
        if (!response) {
            return {type: 'OFFLINE_STATUS', readyOffline: false, reason: `Missing Python runtime asset: ${url}`};
        }
        const digest = await responseDigest(response);
        if (digest.sha256 !== recorded.sha256 || digest.bytes !== recorded.bytes) {
            return {type: 'OFFLINE_STATUS', readyOffline: false, reason: `Python runtime asset changed: ${url}`};
        }
    }
    return {
        type: 'OFFLINE_STATUS',
        readyOffline: true,
        cacheVersion: CACHE_VERSION,
        pythonAssetCount: pythonAssets.length,
        reason: 'Ready offline',
    };
}

async function markPythonRuntimeReady(message) {
    const packages = Array.isArray(message.packages) ? [...new Set(message.packages)].sort() : [];
    const valid = message.pyodideVersion === PYODIDE_VERSION
        && REQUIRED_PYTHON_PACKAGES.every((name) => packages.includes(name));
    if (!valid) {
        return {
            type: 'OFFLINE_STATUS', readyOffline: false,
            reason: 'The prepared Python runtime does not match the pinned offline policy.',
        };
    }
    await updateMetadata((metadata) => ({
        ...metadata,
        pythonRuntime: {
            ...metadata.pythonRuntime,
            packages,
            prepared: true,
            version: PYODIDE_VERSION,
        },
    }));
    return verifiedOfflineStatus();
}

self.addEventListener('message', (event) => {
    if (event.data?.type === 'SKIP_WAITING') {
        event.waitUntil(self.skipWaiting());
        return;
    }
    const port = event.ports?.[0];
    if (event.data?.type === 'OFFLINE_STATUS') {
        event.waitUntil(verifiedOfflineStatus().then((status) => port?.postMessage(status)));
        return;
    }
    if (event.data?.type === 'PYTHON_RUNTIME_READY') {
        event.waitUntil(markPythonRuntimeReady(event.data).then((status) => port?.postMessage(status)));
    }
});

self.addEventListener('fetch', (event) => {
    if (event.request.method !== 'GET') return;
    const url = new URL(event.request.url);

    if (url.href.startsWith(PYODIDE_BASE_URL)) {
        event.respondWith(cachePinnedPythonRuntime(event.request));
        return;
    }
    if (url.origin !== self.location.origin) return;

    const profileMatch = PROFILE_ARTIFACT_PATH.exec(url.pathname);
    if (profileMatch) {
        event.respondWith(cacheImmutableProfileArtifact(event.request, profileMatch[1]));
        return;
    }

    // Bootstrap and content-addressed installation profiles are the only API
    // reads stored for offline work. Reachability, status, save, validation,
    // and activation requests always go to the network.
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
