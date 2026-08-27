'use strict';

const ENGINE = 'receiver-native-cpp-wasm';
const PLUGIN_ID = 'aurora_curtains_native';
const DEFAULT_PARAMETERS = Object.freeze({
    brightness: 0.42,
    curtain_width: 7,
    layers: 3,
    motion: 0.34,
    shimmer: true,
});

let wasm = null;
let activeParams = { ...DEFAULT_PARAMETERS };

function exported(name) {
    const value = wasm?.exports?.[name] || wasm?.exports?.[`_${name}`];
    if (typeof value !== 'function') {
        throw new Error(`Native preview module is missing ${name}`);
    }
    return value;
}

function resolvedGeometry(geometry) {
    const width = Number(
        geometry?.strip_count ?? geometry?.global_strips ?? geometry?.globalStrips ?? geometry?.width ?? 33
    );
    const height = Number(
        geometry?.leds_per_strip ?? geometry?.ledsPerStrip ?? geometry?.height ?? 138
    );
    if (width !== 33 || height !== 138) {
        throw new Error(`Aurora native preview requires 33×138 geometry, got ${width}×${height}`);
    }
    return { width, height };
}

function resolvedParameters(params = {}) {
    const value = { ...DEFAULT_PARAMETERS, ...(params || {}) };
    value.brightness = Number(value.brightness);
    value.curtain_width = Number(value.curtain_width);
    value.layers = Number(value.layers);
    value.motion = Number(value.motion);
    if (!Number.isFinite(value.brightness) || value.brightness < 0.04 || value.brightness > 1) {
        throw new Error('brightness must be between 0.04 and 1');
    }
    if (!Number.isInteger(value.curtain_width) || value.curtain_width < 2 || value.curtain_width > 14) {
        throw new Error('curtain_width must be an integer between 2 and 14');
    }
    if (!Number.isInteger(value.layers) || value.layers < 1 || value.layers > 5) {
        throw new Error('layers must be an integer between 1 and 5');
    }
    if (!Number.isFinite(value.motion) || value.motion < 0.02 || value.motion > 1) {
        throw new Error('motion must be between 0.02 and 1');
    }
    if (typeof value.shimmer !== 'boolean') {
        throw new Error('shimmer must be a boolean');
    }
    return value;
}

function applyParameters(params) {
    activeParams = resolvedParameters(params);
    const status = exported('lg_browser_set_parameters')(
        activeParams.brightness,
        activeParams.curtain_width,
        activeParams.layers,
        activeParams.motion,
        activeParams.shimmer ? 1 : 0,
    );
    if (status !== 0) {
        throw new Error(`Native preview rejected parameters (bridge error ${exported('lg_browser_last_error')()})`);
    }
}

function splitUnsigned64(value, label) {
    const numeric = Number(value);
    if (!Number.isSafeInteger(numeric) || numeric < 0) {
        throw new Error(`${label} must be a non-negative safe integer`);
    }
    const high = Math.floor(numeric / 0x100000000);
    const low = numeric - high * 0x100000000;
    return [low >>> 0, high >>> 0];
}

async function initialize(message) {
    if (message.pluginId !== PLUGIN_ID) {
        throw new Error(`Unsupported receiver-native plugin: ${message.pluginId || 'unknown'}`);
    }
    if (!message.assetUrl) {
        throw new Error('Native preview assetUrl is required');
    }
    const geometry = resolvedGeometry(message.geometry);
    const assetUrl = new URL(message.assetUrl, self.location.href);
    const response = await fetch(assetUrl);
    if (!response.ok) {
        throw new Error(`Unable to load native preview (${response.status})`);
    }
    const module = await WebAssembly.compile(await response.arrayBuffer());
    const imports = {};
    for (const item of WebAssembly.Module.imports(module)) {
        if (item.module === 'wasi_snapshot_preview1' && item.name === 'proc_exit') {
            imports.wasi_snapshot_preview1 ||= {};
            imports.wasi_snapshot_preview1.proc_exit = (code) => {
                throw new Error(`Native preview exited (${code})`);
            };
            continue;
        }
        throw new Error(`Unsupported native preview import: ${item.module}.${item.name}`);
    }
    wasm = await WebAssembly.instantiate(module, imports);
    if (!(wasm.exports.memory instanceof WebAssembly.Memory)) {
        throw new Error('Native preview module does not export memory');
    }
    if (exported('lg_browser_init')(geometry.width, geometry.height) !== 0) {
        throw new Error(`Native preview initialization failed (bridge error ${exported('lg_browser_last_error')()})`);
    }
    applyParameters(message.params);
    self.postMessage({
        type: 'ready',
        requestId: message.requestId,
        width: geometry.width,
        height: geometry.height,
        engine: ENGINE,
    });
}

function render(message) {
    if (!wasm) {
        throw new Error('Native preview has not been initialized');
    }
    if (message.params) {
        applyParameters({ ...activeParams, ...message.params });
    }
    const elapsed = Number(message.elapsed);
    if (!Number.isFinite(elapsed) || elapsed < 0) {
        throw new Error('elapsed must be a non-negative number of seconds');
    }
    const sceneTimeUs = Math.round(elapsed * 1_000_000);
    const [timeLow, timeHigh] = splitUnsigned64(sceneTimeUs, 'scene time');
    const [frameLow, frameHigh] = splitUnsigned64(message.frameIndex ?? 0, 'frame index');
    const started = performance.now();
    const status = exported('lg_browser_render')(timeLow, timeHigh, frameLow, frameHigh);
    const renderMs = performance.now() - started;
    if (status !== 0) {
        throw new Error(`Native preview render failed (bridge error ${exported('lg_browser_last_error')()})`);
    }
    const width = exported('lg_browser_width')();
    const height = exported('lg_browser_height')();
    const pointer = exported('lg_browser_pixels')();
    const size = exported('lg_browser_pixels_size')();
    if (size !== width * height * 3 || pointer + size > wasm.exports.memory.buffer.byteLength) {
        throw new Error('Native preview returned an invalid framebuffer');
    }
    // Copy before transfer: transferring a view of Wasm memory would detach the
    // renderer's own memory. Pixels remain canonical strip-major RGB.
    const pixels = new Uint8Array(wasm.exports.memory.buffer, pointer, size).slice();
    self.postMessage({
        type: 'frame',
        requestId: message.requestId,
        pixels: pixels.buffer,
        width,
        height,
        changed: Boolean(exported('lg_browser_changed')()),
        renderMs,
        engine: ENGINE,
    }, [pixels.buffer]);
}

async function handleMessage(message) {
    if (message?.type === 'init') {
        await initialize(message);
        return;
    }
    if (message?.type === 'render') {
        render(message);
        return;
    }
    throw new Error(`Unsupported native preview message: ${message?.type || 'unknown'}`);
}

let queue = Promise.resolve();
self.onmessage = (event) => {
    const message = event.data;
    queue = queue.then(() => handleMessage(message)).catch((error) => {
        self.postMessage({
            type: 'error',
            requestId: message?.requestId,
            error: error instanceof Error ? error.message : String(error),
            engine: ENGINE,
        });
    });
};
