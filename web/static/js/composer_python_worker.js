/* Python/Wasm animation worker. The composer must create this as type: "module". */

'use strict';

const PYODIDE_VERSION = '314.0.5';
const PYODIDE_BASE_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
const ENGINE = 'python-pyodide-wasm';
const RUNTIME_ROOT = '/ledgrid_python_runtime';
const PROFILE_PATH = `${RUNTIME_ROOT}/selected_installation_profile.bin`;
const PREPARED_PACKAGES = Object.freeze(['numpy', 'pillow']);
const PROFILE_DIGEST_OFFSET = 68;
const PROFILE_DIGEST_BYTES = 32;
const PROFILE_MIN_BYTES = 328;

let pyodidePromise = null;
let runtimeAssetUrl = null;
let runtimeReady = false;
let messageQueue = Promise.resolve();
let pillowPromise = null;
let verifiedProfile = null;
const latestRenderGeneration = new Map();

function errorMessage(error) {
    if (error instanceof Error && error.message) return error.message;
    return String(error || 'Unknown Python browser renderer error');
}

function postError(requestId, instanceId, error) {
    self.postMessage({
        type: 'error',
        requestId,
        instanceId: instanceId || 'primary',
        generation: null,
        engine: ENGINE,
        error: errorMessage(error),
    });
}

function renderGeneration(message) {
    const value = Number(message.generation);
    if (!Number.isSafeInteger(value) || value < 1) {
        throw new Error('Render generation must be a positive safe integer.');
    }
    return value;
}

function postObsolete(message, generation) {
    self.postMessage({
        type: 'obsolete',
        requestId: message.requestId,
        instanceId: message.instanceId || 'primary',
        generation,
        latestGeneration: latestRenderGeneration.get(message.instanceId || 'primary'),
        engine: ENGINE,
    });
}

function batchRenders(message) {
    const renders = message?.renders;
    if (!Array.isArray(renders) || renders.length < 1 || renders.length > 8) {
        throw new Error('Render batch must contain 1-8 requests.');
    }
    const instanceIds = renders.map((render) => render?.instanceId || 'primary');
    if (new Set(instanceIds).size !== instanceIds.length) {
        throw new Error('Render batch instance IDs must be distinct.');
    }
    renders.forEach(renderGeneration);
    return renders;
}

function batchIsObsolete(renders) {
    return renders.some((render) => (
        render.generation < (
            latestRenderGeneration.get(render.instanceId || 'primary')
            || render.generation
        )
    ));
}

function postObsoleteBatch(message, renders) {
    self.postMessage({
        type: 'obsoleteBatch',
        requestId: message.requestId,
        renders: renders.map((render) => ({
            instanceId: render.instanceId || 'primary',
            generation: render.generation,
            latestGeneration: latestRenderGeneration.get(render.instanceId || 'primary'),
        })),
        engine: ENGINE,
    });
}

function validatedAssetUrl(value) {
    if (typeof value !== 'string' || !value) {
        throw new Error('The Python browser renderer source bundle is unavailable.');
    }
    const url = new URL(value, self.location.href);
    if (url.origin !== self.location.origin) {
        throw new Error('The Python browser renderer source bundle must be same-origin.');
    }
    return url.href;
}

function digestHex(bytes) {
    return Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('');
}

function canonicalProfileHeader(bytes) {
    if (!(bytes instanceof Uint8Array) || bytes.byteLength < PROFILE_MIN_BYTES || bytes.byteLength > 65535) {
        throw new Error('The selected installation-profile artifact has an invalid byte count.');
    }
    if (String.fromCharCode(...bytes.subarray(0, 4)) !== 'LGIP') {
        throw new Error('The selected installation-profile artifact is not LGIP.');
    }
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const exact = [
        [view.getUint16(4, false), 1, 'format version'],
        [view.getUint16(6, false), 112, 'fixed header size'],
        [view.getUint32(8, false), 0, 'flags'],
        [view.getUint16(12, false), 33, 'global strip count'],
        [view.getUint16(14, false), 138, 'LED height'],
        [view.getUint16(16, false), 0, 'strip origin'],
        [view.getUint16(18, false), 33, 'represented strip count'],
        [view.getUint32(20, false), 4554, 'pixel count'],
        [bytes[25], 7, 'globe-region count'],
        [view.getUint16(26, false), 9, 'section count'],
        [view.getUint16(28, false), 24, 'section entry size'],
        [view.getUint16(30, false), 0, 'reserved header'],
        [view.getUint32(32, false), bytes.byteLength, 'declared byte count'],
    ];
    for (const [actual, expected, label] of exact) {
        if (actual !== expected) throw new Error(`LGIP ${label} is invalid.`);
    }
    if (bytes.subarray(100, 112).some((value) => value !== 0)) {
        throw new Error('LGIP reserved header bytes are nonzero.');
    }
}

function installationProfileDescriptor(value) {
    const digest = String(value?.digest || '').toLowerCase();
    if (!/^[0-9a-f]{64}$/.test(digest) || /^0+$/.test(digest)) {
        throw new Error('The browser renderer requires a selected managed installation-profile digest.');
    }
    if (typeof value?.artifactUrl !== 'string' || !value.artifactUrl) {
        throw new Error('The selected installation-profile artifact URL is unavailable.');
    }
    const url = new URL(value.artifactUrl, self.location.href);
    if (url.origin !== self.location.origin) {
        throw new Error('The selected installation-profile artifact must be same-origin.');
    }
    return {digest, url: url.href};
}

async function verifyInstallationProfile(value, suppliedArtifact = null) {
    const expected = installationProfileDescriptor(value);
    if (verifiedProfile) {
        if (verifiedProfile.digest !== expected.digest || verifiedProfile.url !== expected.url) {
            throw new Error('This Python worker is already bound to a different installation profile.');
        }
        return verifiedProfile;
    }
    if (!self.crypto?.subtle) {
        throw new Error('Cryptographic verification is unavailable; installation profile rejected.');
    }
    let bytes;
    let etag;
    if (suppliedArtifact?.bytes instanceof ArrayBuffer) {
        bytes = new Uint8Array(suppliedArtifact.bytes);
        etag = typeof suppliedArtifact.etag === 'string' ? suppliedArtifact.etag : null;
    } else {
        const response = await fetch(expected.url, {
            cache: 'no-store',
            headers: {'Accept': 'application/octet-stream'},
        });
        if (!response.ok) {
            throw new Error(`Could not load the selected installation profile (${response.status}).`);
        }
        etag = response.headers.get('ETag');
        bytes = new Uint8Array(await response.arrayBuffer());
    }
    etag = etag?.replace(/^W\//, '').replace(/^"|"$/g, '');
    const managedArtifactPath = new URL(expected.url).pathname
        === `/api/v1/installation-profiles/${expected.digest}/artifact`;
    if (managedArtifactPath && etag && etag !== expected.digest) {
        throw new Error('The installation-profile response ETag does not match the selected digest.');
    }
    canonicalProfileHeader(bytes);
    const embedded = digestHex(bytes.subarray(
        PROFILE_DIGEST_OFFSET,
        PROFILE_DIGEST_OFFSET + PROFILE_DIGEST_BYTES,
    ));
    const digestInput = bytes.slice();
    digestInput.fill(0, PROFILE_DIGEST_OFFSET, PROFILE_DIGEST_OFFSET + PROFILE_DIGEST_BYTES);
    const computed = digestHex(new Uint8Array(await self.crypto.subtle.digest('SHA-256', digestInput)));
    if (embedded !== expected.digest || computed !== expected.digest) {
        throw new Error('The selected LGIP artifact failed content-digest verification.');
    }
    verifiedProfile = Object.freeze({...expected, bytes});
    return verifiedProfile;
}

async function ensurePyodide() {
    if (!pyodidePromise) {
        pyodidePromise = (async () => {
            const module = await import(`${PYODIDE_BASE_URL}pyodide.mjs`);
            const pyodide = await module.loadPyodide({indexURL: PYODIDE_BASE_URL});
            await pyodide.loadPackage('numpy');
            return pyodide;
        })();
    }
    return pyodidePromise;
}

async function ensureRuntime(assetUrl) {
    const resolvedUrl = validatedAssetUrl(assetUrl);
    if (runtimeReady) {
        if (resolvedUrl !== runtimeAssetUrl) {
            throw new Error('This worker is already bound to a different Python source bundle.');
        }
        return ensurePyodide();
    }

    const sourcePromise = fetch(resolvedUrl, {cache: 'no-cache'}).then((response) => {
        if (!response.ok) {
            throw new Error(`Could not load Python renderer sources (${response.status}).`);
        }
        return response.arrayBuffer();
    });
    const [pyodide, archive] = await Promise.all([ensurePyodide(), sourcePromise]);
    pyodide.FS.mkdirTree(RUNTIME_ROOT);
    pyodide.unpackArchive(new Uint8Array(archive), 'zip', {extractDir: RUNTIME_ROOT});
    await pyodide.runPythonAsync(`
import sys
if ${JSON.stringify(RUNTIME_ROOT)} not in sys.path:
    sys.path.insert(0, ${JSON.stringify(RUNTIME_ROOT)})
from ledgrid_browser_runtime import BrowserPreviewRuntime
_ledgrid_browser_runtime = BrowserPreviewRuntime()
`);
    runtimeAssetUrl = resolvedUrl;
    runtimeReady = true;
    return pyodide;
}

async function ensurePluginPackages(pyodide, pluginId) {
    if (pluginId !== 'gif_animation') return;
    if (!pillowPromise) pillowPromise = pyodide.loadPackage('pillow');
    await pillowPromise;
}

async function ensurePreparedPackages(pyodide) {
    await pyodide.loadPackage('numpy');
    if (!pillowPromise) pillowPromise = pyodide.loadPackage('pillow');
    await pillowPromise;
}

function copyPythonBytes(value) {
    let converted = value;
    if (value && typeof value.toJs === 'function') {
        converted = value.toJs();
        value.destroy();
    }
    if (converted instanceof Uint8Array) return converted.slice();
    if (converted instanceof ArrayBuffer) return new Uint8Array(converted.slice(0));
    return Uint8Array.from(converted);
}

async function initialize(message) {
    const [pyodide, profile] = await Promise.all([
        ensureRuntime(message.assetUrl),
        verifyInstallationProfile(
            message.installationProfile,
            message.installationProfileArtifact,
        ),
    ]);
    await ensurePluginPackages(pyodide, message.pluginId);
    pyodide.FS.writeFile(PROFILE_PATH, profile.bytes);
    pyodide.globals.set('_ledgrid_profile_path', PROFILE_PATH);
    pyodide.globals.set('_ledgrid_profile_digest', profile.digest);
    await pyodide.runPythonAsync(
        '_ledgrid_browser_runtime.bind_installation_profile_path(_ledgrid_profile_path, _ledgrid_profile_digest)'
    );
    pyodide.globals.set('_ledgrid_payload_json', JSON.stringify({
        instanceId: message.instanceId || 'primary',
        pluginId: message.pluginId,
        className: message.className,
        geometry: message.geometry,
        params: message.params || {},
        installationProfileDigest: profile.digest,
    }));
    const resultJson = await pyodide.runPythonAsync(
        '_ledgrid_browser_runtime.initialize_json(_ledgrid_payload_json)'
    );
    const result = JSON.parse(resultJson);
    self.postMessage({type: 'ready', requestId: message.requestId, ...result});
}

async function render(message) {
    if (!runtimeReady) {
        throw new Error('The Python browser renderer has not been initialized.');
    }
    const instanceId = message.instanceId || 'primary';
    const generation = renderGeneration(message);
    if (generation < (latestRenderGeneration.get(instanceId) || generation)) {
        postObsolete(message, generation);
        return;
    }
    const pyodide = await ensurePyodide();
    pyodide.globals.set('_ledgrid_payload_json', JSON.stringify({
        instanceId: message.instanceId || 'primary',
        elapsed: message.elapsed,
        frameIndex: message.frameIndex,
        params: message.params || {},
        wallTime: message.wallTime ?? null,
    }));
    const resultJson = pyodide.runPython(
        '_ledgrid_browser_runtime.render_json(_ledgrid_payload_json)'
    );
    if (generation < (latestRenderGeneration.get(instanceId) || generation)) {
        postObsolete(message, generation);
        return;
    }
    const result = JSON.parse(resultJson);
    pyodide.globals.set('_ledgrid_instance_id', result.instanceId);
    const pythonBytes = pyodide.runPython(
        '_ledgrid_browser_runtime.frame_bytes_for(_ledgrid_instance_id)'
    );
    const pixels = copyPythonBytes(pythonBytes);
    self.postMessage({
        type: 'frame',
        requestId: message.requestId,
        instanceId: result.instanceId,
        generation,
        pixels: pixels.buffer,
        width: result.width,
        height: result.height,
        changed: result.changed,
        role: result.role,
        frameFormat: result.frameFormat,
        wallClockFixed: result.wallClockFixed,
        renderMs: result.renderMs,
        engine: ENGINE,
    }, [pixels.buffer]);
}

async function renderBatch(message) {
    if (!runtimeReady) {
        throw new Error('The Python browser renderer has not been initialized.');
    }
    const renders = batchRenders(message);
    if (batchIsObsolete(renders)) {
        postObsoleteBatch(message, renders);
        return;
    }
    const pyodide = await ensurePyodide();
    pyodide.globals.set('_ledgrid_payload_json', JSON.stringify({
        renders: renders.map((render) => ({
            instanceId: render.instanceId || 'primary',
            elapsed: render.elapsed,
            frameIndex: render.frameIndex,
            params: render.params || {},
            wallTime: render.wallTime ?? null,
        })),
    }));
    const resultJson = pyodide.runPython(
        '_ledgrid_browser_runtime.render_batch_json(_ledgrid_payload_json)'
    );
    if (batchIsObsolete(renders)) {
        postObsoleteBatch(message, renders);
        return;
    }
    const frames = JSON.parse(resultJson);
    if (!Array.isArray(frames) || frames.length !== renders.length) {
        throw new Error('The Python renderer returned an invalid frame batch.');
    }
    const pythonBytes = pyodide.runPython(
        '_ledgrid_browser_runtime.batch_frame_bytes'
    );
    const pixels = copyPythonBytes(pythonBytes);
    let expectedOffset = 0;
    frames.forEach((frame, index) => {
        const render = renders[index];
        if (
            frame.instanceId !== (render.instanceId || 'primary')
            || !Number.isSafeInteger(frame.byteOffset)
            || !Number.isSafeInteger(frame.byteLength)
            || frame.byteOffset !== expectedOffset
            || frame.byteLength < 1
            || frame.byteOffset + frame.byteLength > pixels.byteLength
        ) {
            throw new Error('The Python renderer returned invalid frame-batch offsets.');
        }
        frame.generation = render.generation;
        expectedOffset += frame.byteLength;
    });
    if (expectedOffset !== pixels.byteLength) {
        throw new Error('The Python renderer returned trailing frame-batch bytes.');
    }
    self.postMessage({
        type: 'frameBatch',
        requestId: message.requestId,
        frames,
        pixels: pixels.buffer,
        engine: ENGINE,
    }, [pixels.buffer]);
}

async function dispose(message) {
    if (!runtimeReady) {
        self.postMessage({
            type: 'disposed',
            requestId: message.requestId,
            instanceId: message.instanceId || 'primary',
            engine: ENGINE,
            disposed: false,
            remainingInstances: 0,
        });
        return;
    }
    const pyodide = await ensurePyodide();
    pyodide.globals.set('_ledgrid_payload_json', JSON.stringify({
        instanceId: message.instanceId || 'primary',
    }));
    const resultJson = await pyodide.runPythonAsync(
        '_ledgrid_browser_runtime.dispose_json(_ledgrid_payload_json)'
    );
    const result = JSON.parse(resultJson);
    latestRenderGeneration.delete(message.instanceId || 'primary');
    self.postMessage({type: 'disposed', requestId: message.requestId, ...result});
}

async function prepare(message) {
    const pyodide = await ensureRuntime(message.assetUrl);
    const requested = Array.isArray(message.packages) ? message.packages : [];
    for (const name of requested) {
        if (!PREPARED_PACKAGES.includes(name)) {
            throw new Error(`Unsupported offline Python package: ${String(name)}`);
        }
    }
    await ensurePreparedPackages(pyodide);
    const runtimeUrls = [...new Set([
        `${PYODIDE_BASE_URL}pyodide.mjs`,
        ...performance.getEntriesByType('resource')
            .map((entry) => entry.name)
            .filter((url) => typeof url === 'string' && url.startsWith(PYODIDE_BASE_URL)),
    ])].sort();
    self.postMessage({
        type: 'prepared',
        requestId: message.requestId,
        engine: ENGINE,
        pyodideVersion: PYODIDE_VERSION,
        packages: [...PREPARED_PACKAGES],
        runtimeUrls,
    });
}

async function dispatch(message) {
    if (!message || typeof message !== 'object') {
        throw new Error('Worker messages must be objects.');
    }
    if (message.type === 'init') return initialize(message);
    if (message.type === 'render') return render(message);
    if (message.type === 'renderBatch') return renderBatch(message);
    if (message.type === 'dispose') return dispose(message);
    if (message.type === 'prepare') return prepare(message);
    throw new Error(`Unsupported Python renderer message: ${String(message.type)}`);
}

self.onmessage = (event) => {
    const message = event.data;
    if (message?.type === 'render') {
        try {
            const generation = renderGeneration(message);
            const instanceId = message.instanceId || 'primary';
            latestRenderGeneration.set(
                instanceId,
                Math.max(generation, latestRenderGeneration.get(instanceId) || 0),
            );
        } catch (error) {
            postError(message?.requestId, message?.instanceId, error);
            return;
        }
    }
    if (message?.type === 'renderBatch') {
        try {
            for (const render of batchRenders(message)) {
                const generation = renderGeneration(render);
                const instanceId = render.instanceId || 'primary';
                latestRenderGeneration.set(
                    instanceId,
                    Math.max(generation, latestRenderGeneration.get(instanceId) || 0),
                );
            }
        } catch (error) {
            postError(message?.requestId, 'batch', error);
            return;
        }
    }
    messageQueue = messageQueue
        .then(() => dispatch(message))
        .catch((error) => postError(message?.requestId, message?.instanceId, error));
};
