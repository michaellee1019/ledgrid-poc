/* Python/Wasm animation worker. The composer must create this as type: "module". */

'use strict';

const PYODIDE_VERSION = '314.0.5';
const PYODIDE_BASE_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
const ENGINE = 'python-pyodide-wasm';
const RUNTIME_ROOT = '/ledgrid_python_runtime';
const PREPARED_PACKAGES = Object.freeze(['numpy', 'pillow']);

let pyodidePromise = null;
let runtimeAssetUrl = null;
let runtimeReady = false;
let messageQueue = Promise.resolve();
let pillowPromise = null;
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
    const pyodide = await ensureRuntime(message.assetUrl);
    await ensurePluginPackages(pyodide, message.pluginId);
    pyodide.globals.set('_ledgrid_payload_json', JSON.stringify({
        instanceId: message.instanceId || 'primary',
        pluginId: message.pluginId,
        className: message.className,
        geometry: message.geometry,
        params: message.params || {},
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
    const resultJson = await pyodide.runPythonAsync(
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
    self.postMessage({
        type: 'prepared',
        requestId: message.requestId,
        engine: ENGINE,
        pyodideVersion: PYODIDE_VERSION,
        packages: [...PREPARED_PACKAGES],
    });
}

async function dispatch(message) {
    if (!message || typeof message !== 'object') {
        throw new Error('Worker messages must be objects.');
    }
    if (message.type === 'init') return initialize(message);
    if (message.type === 'render') return render(message);
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
    messageQueue = messageQueue
        .then(() => dispatch(message))
        .catch((error) => postError(message?.requestId, message?.instanceId, error));
};
