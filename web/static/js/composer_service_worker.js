'use strict';

const CACHE_PREFIX = 'ledgrid-composer-shell-';
const PREVIOUS_CACHE_VERSION = 'v20';
const CACHE_VERSION = 'v21';
const CACHE_NAME = `${CACHE_PREFIX}${CACHE_VERSION}`;
const STAGING_CACHE_NAME = `${CACHE_NAME}-staging`;
const RUNTIME_CACHE_NAME = `${CACHE_NAME}-python-runtime`;
const OFFLINE_MANIFEST_URL = '/static/generated/composer/offline_assets.json';
const OFFLINE_METADATA_URL = '/.ledgrid-composer/offline-metadata';
const PROFILE_ARTIFACT_PATH = /^\/api\/v1\/installation-profiles\/([0-9a-f]{64})\/artifact$/;
const BUNDLED_BOOTSTRAP_URL = '/static/generated/composer/bootstrap.v1.json';
const BUNDLED_PROFILE_URL = '/static/generated/composer/installation_profile_ce457a14efd131395507c449f35a7701ca78ddca059620dc3757806ef553ca6a.bin';
const BUNDLED_PROFILE_PATH = /^\/static\/generated\/composer\/installation_profile_([0-9a-f]{64})\.bin$/;
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
    '/static/js/composer_sha256.js',
    '/static/js/composer.js',
    '/static/js/composer_native_worker.js',
    '/static/js/composer_python_worker.js',
    '/static/generated/composer/aurora_curtains_native.wasm',
    '/static/generated/composer/compiled_rainbow.wasm',
    '/static/generated/composer/bootstrap.v1.json',
    '/static/generated/composer/installation_profile_ce457a14efd131395507c449f35a7701ca78ddca059620dc3757806ef553ca6a.bin',
    '/static/generated/composer/ledgrid_python_runtime.zip',
    '/static/generated/composer/offline_assets.json',
    '/static/composer.webmanifest',
    '/static/icons/composer-180.png',
    '/static/icons/composer-512.png',
    '/static/icons/composer.svg',
];
const SHELL_ASSET_SET = new Set(SHELL_ASSETS);
const OBSERVED_SHELL_ASSETS = new Set(['/composer', OFFLINE_MANIFEST_URL]);
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

function profileArtifactDigest(pathname) {
    return PROFILE_ARTIFACT_PATH.exec(pathname)?.[1]
        || BUNDLED_PROFILE_PATH.exec(pathname)?.[1]
        || null;
}

async function readMetadata(cacheName = CACHE_NAME) {
    const cache = await caches.open(cacheName);
    const response = await cache.match(OFFLINE_METADATA_URL);
    if (!response) return null;
    try {
        return await response.json();
    } catch (_error) {
        return null;
    }
}

async function writeMetadata(value, cacheName = CACHE_NAME) {
    const cache = await caches.open(cacheName);
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

function declaredShellAssets(manifest) {
    if (!Array.isArray(manifest.localAssets)) {
        throw new Error('The composer offline asset manifest has no local asset list.');
    }
    const expected = new Map();
    for (const asset of manifest.localAssets) {
        if (
            !asset
            || typeof asset.url !== 'string'
            || !SHELL_ASSET_SET.has(asset.url)
            || OBSERVED_SHELL_ASSETS.has(asset.url)
            || !Number.isSafeInteger(asset.bytes)
            || asset.bytes <= 0
            || !/^[0-9a-f]{64}$/.test(asset.sha256)
            || expected.has(asset.url)
        ) {
            throw new Error('The composer offline asset manifest contains an invalid entry.');
        }
        expected.set(asset.url, asset);
    }
    for (const url of SHELL_ASSETS) {
        if (!OBSERVED_SHELL_ASSETS.has(url) && !expected.has(url)) {
            throw new Error(`Offline asset is missing a required digest: ${url}`);
        }
    }
    return expected;
}

async function verifyCachedShell(cacheName, suppliedMetadata = null) {
    const metadata = suppliedMetadata || await readMetadata(cacheName);
    if (
        !metadata
        || metadata.schema !== 'ledgrid.composer-offline-cache-state'
        || metadata.schemaVersion !== 1
        || metadata.cacheVersion !== CACHE_VERSION
        || !metadata.shellComplete
    ) {
        throw new Error(`Offline cache ${cacheName} has no complete generation marker.`);
    }
    const cache = await caches.open(cacheName);
    const recordedEntries = Object.entries(metadata.verifiedShell || {});
    if (recordedEntries.length !== SHELL_ASSETS.length) {
        throw new Error(`Offline cache ${cacheName} has an incomplete asset inventory.`);
    }
    for (const url of SHELL_ASSETS) {
        const recorded = metadata.verifiedShell?.[url];
        const response = await cache.match(url);
        if (!recorded || !response) {
            throw new Error(`Offline cache ${cacheName} is missing ${url}.`);
        }
        if (recorded.expected !== !OBSERVED_SHELL_ASSETS.has(url)) {
            throw new Error(`Offline cache ${cacheName} has invalid digest provenance for ${url}.`);
        }
        const digest = await responseDigest(response);
        if (digest.sha256 !== recorded.sha256 || digest.bytes !== recorded.bytes) {
            throw new Error(`Offline cache ${cacheName} failed verification for ${url}.`);
        }
    }
    return metadata;
}

async function promoteVerifiedShell(metadata) {
    await verifyCachedShell(STAGING_CACHE_NAME, metadata);
    await caches.delete(CACHE_NAME);
    const staging = await caches.open(STAGING_CACHE_NAME);
    const current = await caches.open(CACHE_NAME);
    try {
        for (const url of SHELL_ASSETS) {
            const response = await staging.match(url);
            if (!response) throw new Error(`Staged offline asset disappeared: ${url}`);
            await current.put(url, response);
        }
        await writeMetadata(metadata, CACHE_NAME);
        await verifyCachedShell(CACHE_NAME, metadata);
    } catch (error) {
        await caches.delete(CACHE_NAME);
        throw error;
    }
}

async function installVersionedShell() {
    await caches.delete(STAGING_CACHE_NAME);
    try {
        const manifestResponse = await fetchRequired(OFFLINE_MANIFEST_URL);
        const manifest = await manifestResponse.clone().json();
        if (
            manifest?.schema !== 'ledgrid.composer-offline-assets'
            || manifest.schemaVersion !== 1
            || manifest.cacheVersion !== CACHE_VERSION
            || manifest.previousCacheVersion !== PREVIOUS_CACHE_VERSION
        ) {
            throw new Error('The composer offline asset manifest is incompatible.');
        }
        if (manifest.pythonRuntime?.version !== PYODIDE_VERSION) {
            throw new Error('The composer and offline manifest disagree on Pyodide.');
        }
        const expected = declaredShellAssets(manifest);
        const cache = await caches.open(STAGING_CACHE_NAME);
        const verifiedShell = {};
        for (const url of SHELL_ASSETS) {
            const response = url === OFFLINE_MANIFEST_URL
                ? manifestResponse.clone()
                : await fetchRequired(url);
            const digest = await responseDigest(response);
            const declared = expected.get(url);
            if (
                (!declared && !OBSERVED_SHELL_ASSETS.has(url))
                || (declared && (
                    declared.sha256 !== digest.sha256 || declared.bytes !== digest.bytes
                ))
            ) {
                throw new Error(`Offline asset digest mismatch: ${url}`);
            }
            await cache.put(url, response.clone());
            verifiedShell[url] = {
                ...digest,
                expected: Boolean(declared),
            };
        }
        const metadata = {
            schema: 'ledgrid.composer-offline-cache-state',
            schemaVersion: 1,
            cacheVersion: CACHE_VERSION,
            previousCacheVersion: PREVIOUS_CACHE_VERSION,
            shellComplete: true,
            verifiedShell,
            activeProfile: null,
            pythonRuntime: {
                baseUrl: PYODIDE_BASE_URL,
                packages: [],
                prepared: false,
                version: PYODIDE_VERSION,
            },
            pythonAssets: {},
        };
        await writeMetadata(metadata, STAGING_CACHE_NAME);
        await promoteVerifiedShell(metadata);
        await caches.delete(STAGING_CACHE_NAME);
        await self.skipWaiting();
    } catch (error) {
        await caches.delete(STAGING_CACHE_NAME);
        await caches.delete(CACHE_NAME);
        await caches.delete(RUNTIME_CACHE_NAME);
        throw error;
    }
}

async function refreshOpenComposerClients() {
    if (typeof self.clients.matchAll !== 'function') return;
    const clients = await self.clients.matchAll({type: 'window', includeUncontrolled: true});
    await Promise.all(clients.map(async (client) => {
        const url = new URL(client.url);
        if (url.origin !== self.location.origin || url.pathname !== '/composer') return;
        await client.navigate('/composer');
    }));
}

self.addEventListener('install', (event) => {
    event.waitUntil(installVersionedShell());
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        verifyCachedShell(CACHE_NAME)
            .then(() => caches.keys())
            .then((names) => Promise.all(names.filter((name) => (
                name.startsWith(CACHE_PREFIX)
                && name !== CACHE_NAME
                && name !== RUNTIME_CACHE_NAME
            )).map((name) => caches.delete(name))))
            .then(() => self.clients.claim())
            .then(() => refreshOpenComposerClients())
    );
});

async function networkFirst(request, fallbackKey = request) {
    try {
        const response = await fetch(request);
        if (!response.ok) throw new Error(`Network response was ${response.status}`);
        if (response.type === 'basic') {
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
    const pathname = new URL(request.url, self.location.origin).pathname;
    const cached = await cache.match(pathname);
    if (cached) {
        await verifiedProfileArtifact(cached, expectedDigest);
        return cached;
    }
    const response = await fetch(request, {cache: 'no-cache'});
    if (!response.ok || response.type !== 'basic') return response;
    const identity = await verifiedProfileArtifact(response, expectedDigest);
    await cache.put(pathname, response.clone());
    await updateMetadata((metadata) => ({
        ...metadata,
        profileArtifacts: {
            ...(metadata.profileArtifacts || {}),
            [new URL(request.url).pathname]: identity,
        },
    }));
    return response;
}

async function deliverInstallationProfileArtifact(message) {
    const digest = String(message.digest || '').toLowerCase();
    if (!/^[0-9a-f]{64}$/.test(digest) || /^0+$/.test(digest)) {
        throw new Error('The requested installation-profile digest is invalid.');
    }
    const url = new URL(message.artifactUrl, self.location.origin);
    const pathDigest = profileArtifactDigest(url.pathname);
    if (url.origin !== self.location.origin || pathDigest !== digest) {
        throw new Error('The requested installation-profile artifact is not the selected same-origin digest.');
    }
    const response = await cacheImmutableProfileArtifact(new Request(url.href, {
        cache: 'no-store',
        headers: {'Accept': 'application/octet-stream'},
    }), digest);
    if (!response.ok) {
        throw new Error(`Could not load the selected installation profile (${response.status}).`);
    }
    await verifiedProfileArtifact(response, digest);
    return {
        type: 'INSTALLATION_PROFILE_ARTIFACT',
        digest,
        etag: response.headers.get('ETag'),
        bytes: await response.arrayBuffer(),
    };
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
    const bootstrap = await shell.match(BUNDLED_BOOTSTRAP_URL);
    if (!bootstrap) {
        return {
            type: 'OFFLINE_STATUS', readyOffline: false,
            reason: 'The bundled renderer catalog is missing.',
        };
    }
    let bootstrapPayload;
    try {
        bootstrapPayload = await bootstrap.clone().json();
    } catch (_error) {
        return {type: 'OFFLINE_STATUS', readyOffline: false, reason: 'The cached renderer catalog is unreadable.'};
    }
    if (
        bootstrapPayload?.schema !== 'ledgrid.browser-composer-bootstrap'
        || bootstrapPayload?.artifact?.kind !== 'bundled'
        || bootstrapPayload?.artifact?.version !== 1
    ) {
        return {type: 'OFFLINE_STATUS', readyOffline: false, reason: 'The bundled renderer catalog is incompatible.'};
    }
    const activeProfile = metadata.activeProfile;
    const profilePath = activeProfile?.path;
    const expectedProfileDigest = profileArtifactDigest(profilePath || '');
    const cachedProfile = profilePath ? await shell.match(profilePath) : null;
    if (!expectedProfileDigest || expectedProfileDigest !== activeProfile?.contentDigest || !cachedProfile) {
        return {
            type: 'OFFLINE_STATUS', readyOffline: false,
            reason: 'Prepare the exact selected installation profile for offline use.',
        };
    }
    try {
        const actualProfile = await verifiedProfileArtifact(cachedProfile, expectedProfileDigest);
        if (
            actualProfile.sha256 !== activeProfile.sha256
            || actualProfile.bytes !== activeProfile.bytes
            || actualProfile.contentDigest !== activeProfile.contentDigest
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
    const runtimeUrls = Array.isArray(message.runtimeUrls)
        ? [...new Set(message.runtimeUrls)].sort()
        : [];
    const entrypoint = `${PYODIDE_BASE_URL}pyodide.mjs`;
    const valid = message.pyodideVersion === PYODIDE_VERSION
        && REQUIRED_PYTHON_PACKAGES.every((name) => packages.includes(name))
        && runtimeUrls.includes(entrypoint)
        && runtimeUrls.every((url) => typeof url === 'string' && url.startsWith(PYODIDE_BASE_URL));
    if (!valid) {
        return {
            type: 'OFFLINE_STATUS', readyOffline: false,
            reason: 'The prepared Python runtime does not match the pinned offline policy.',
        };
    }
    try {
        for (const url of runtimeUrls) {
            const response = await cachePinnedPythonRuntime(new Request(url, {mode: 'cors'}));
            if (!response.ok || response.type !== 'cors') {
                throw new Error(`Python runtime asset is unavailable: ${url}`);
            }
        }
    } catch (error) {
        return {
            type: 'OFFLINE_STATUS', readyOffline: false,
            reason: error instanceof Error ? error.message : String(error),
        };
    }
    let profileUrl;
    try {
        profileUrl = new URL(message.installationProfile?.artifactUrl, self.location.origin);
    } catch (_error) {
        profileUrl = null;
    }
    const profileDigest = String(message.installationProfile?.digest || '').toLowerCase();
    const pathDigest = profileUrl ? profileArtifactDigest(profileUrl.pathname) : null;
    const shell = await caches.open(CACHE_NAME);
    const cachedProfile = profileUrl ? await shell.match(profileUrl.pathname) : null;
    if (
        !profileUrl
        || profileUrl.origin !== self.location.origin
        || pathDigest !== profileDigest
        || !cachedProfile
    ) {
        return {
            type: 'OFFLINE_STATUS', readyOffline: false,
            reason: 'The exact selected installation profile is not cached.',
        };
    }
    let profileIdentity;
    try {
        profileIdentity = await verifiedProfileArtifact(cachedProfile, profileDigest);
    } catch (_error) {
        return {
            type: 'OFFLINE_STATUS', readyOffline: false,
            reason: 'The exact selected installation profile failed verification.',
        };
    }
    await updateMetadata((metadata) => ({
        ...metadata,
        activeProfile: {
            ...profileIdentity,
            path: profileUrl.pathname,
        },
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
        return;
    }
    if (event.data?.type === 'INSTALLATION_PROFILE_ARTIFACT') {
        event.waitUntil(deliverInstallationProfileArtifact(event.data).then((artifact) => {
            port?.postMessage(artifact, [artifact.bytes]);
        }).catch((error) => port?.postMessage({
            type: 'INSTALLATION_PROFILE_ARTIFACT_ERROR',
            reason: error instanceof Error ? error.message : String(error),
        })));
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

    const profileDigest = profileArtifactDigest(url.pathname);
    if (profileDigest) {
        event.respondWith(cacheImmutableProfileArtifact(event.request, profileDigest));
        return;
    }

    // Every API read except an immutable profile artifact remains network-only.
    // The renderer catalog is a generated static shell asset, so reachability,
    // capability refresh, status, save, validation, and activation are never cached.
    if (url.pathname.startsWith('/api/')) return;

    if (event.request.mode === 'navigate' && url.pathname === '/composer') {
        event.respondWith(networkFirst(event.request, '/composer'));
        return;
    }

    if (SHELL_ASSET_SET.has(url.pathname)) {
        event.respondWith(caches.match(url.pathname).then((cached) => cached || fetch(event.request)));
    }
});
