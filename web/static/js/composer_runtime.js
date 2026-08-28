(function attachComposerRuntime(global) {
    'use strict';

    const PYTHON_ENGINE = 'python-pyodide-wasm';
    const DEFAULT_TIMEOUT_MS = 20000;
    const DEFAULT_INIT_TIMEOUT_MS = 90000;
    const DEFAULT_RESTART_LIMIT = 2;
    const DEFAULT_RESTART_WINDOW_MS = 60000;
    const MAX_RESTART_LIMIT = 3;
    const MIN_RESTART_WINDOW_MS = 1000;
    const MAX_RESTART_WINDOW_MS = 300000;
    const sharedPythonHosts = new Map();
    const installationProfileArtifacts = new Map();
    let runtimeSequence = 0;

    class ComposerRuntimeError extends Error {
        constructor(message, detail = null) {
            super(message);
            this.name = 'ComposerRuntimeError';
            this.detail = detail;
        }
    }

    function errorMessage(error, fallback = 'The browser renderer stopped unexpectedly.') {
        if (error instanceof Error && error.message) return error.message;
        return String(error || fallback);
    }

    function errorKind(error) {
        return error?.detail?.faultKind || (error?.detail?.timeout ? 'timeout' : 'unknown');
    }

    function isPythonRuntime(runtime) {
        return runtime?.kind === 'python' || runtime?.engine === PYTHON_ENGINE;
    }

    function safeInstancePart(value) {
        const part = String(value || 'primary').replace(/[^A-Za-z0-9_.-]/g, '_');
        return (part || 'primary').slice(0, 40);
    }

    function boundedInteger(value, fallback, minimum, maximum) {
        const numeric = Number(value);
        if (!Number.isSafeInteger(numeric)) return fallback;
        return Math.min(maximum, Math.max(minimum, numeric));
    }

    function snapshotValue(value, seen = new Map()) {
        if (value === null || typeof value !== 'object') return value;
        if (seen.has(value)) {
            throw new ComposerRuntimeError('Renderer parameters must not contain cycles.');
        }
        const result = Array.isArray(value) ? [] : {};
        seen.set(value, result);
        for (const key of Object.keys(value)) result[key] = snapshotValue(value[key], seen);
        seen.delete(value);
        return result;
    }

    function sameSnapshotValue(left, right) {
        if (Object.is(left, right)) return true;
        if (Array.isArray(left) || Array.isArray(right)) {
            return Array.isArray(left)
                && Array.isArray(right)
                && left.length === right.length
                && left.every((value, index) => sameSnapshotValue(value, right[index]));
        }
        if (
            !left || !right
            || typeof left !== 'object'
            || typeof right !== 'object'
        ) return false;
        const leftKeys = Object.keys(left);
        const rightKeys = Object.keys(right);
        return leftKeys.length === rightKeys.length && leftKeys.every((key) => (
            Object.prototype.hasOwnProperty.call(right, key)
            && sameSnapshotValue(left[key], right[key])
        ));
    }

    function parameterDelta(current, requested) {
        const delta = {};
        for (const key of Object.keys(requested)) {
            if (
                !Object.prototype.hasOwnProperty.call(current, key)
                || !sameSnapshotValue(current[key], requested[key])
            ) delta[key] = snapshotValue(requested[key]);
        }
        return delta;
    }

    function installationProfileDescriptor(value) {
        if (!value || typeof value !== 'object') return null;
        const digest = String(value.digest || '').toLowerCase();
        if (!/^[0-9a-f]{64}$/.test(digest) || /^0+$/.test(digest)) return null;
        if (typeof value.artifactUrl !== 'string' || !value.artifactUrl) return null;
        return Object.freeze({digest, artifactUrl: value.artifactUrl});
    }

    function installationProfileArtifact(profile) {
        const location = global.location;
        if (!location?.href || !location?.origin) {
            return Promise.reject(new ComposerRuntimeError(
                'The managed installation-profile origin is unavailable.',
            ));
        }
        let url;
        try {
            url = new URL(profile.artifactUrl, location.href);
        } catch (_error) {
            return Promise.reject(new ComposerRuntimeError(
                'The managed installation-profile artifact URL is invalid.',
            ));
        }
        if (url.origin !== location.origin) {
            return Promise.reject(new ComposerRuntimeError(
                'The managed installation-profile artifact must be same-origin.',
            ));
        }
        const key = `${profile.digest}\n${url.href}`;
        let pending = installationProfileArtifacts.get(key);
        if (!pending) {
            pending = (async () => {
                const delivered = await serviceWorkerRequest({
                    type: 'INSTALLATION_PROFILE_ARTIFACT',
                    artifactUrl: url.href,
                    digest: profile.digest,
                });
                if (delivered.type === 'INSTALLATION_PROFILE_ARTIFACT') {
                    if (
                        delivered.digest !== profile.digest
                        || !(delivered.bytes instanceof ArrayBuffer)
                    ) {
                        throw new ComposerRuntimeError(
                            'The offline worker returned an invalid installation-profile artifact.',
                        );
                    }
                    return Object.freeze({
                        bytes: delivered.bytes,
                        etag: delivered.etag || null,
                    });
                }
                if (delivered.type !== 'OFFLINE_STATUS') {
                    throw new ComposerRuntimeError(
                        delivered.reason || 'The offline worker could not provide the installation profile.',
                    );
                }
                const response = await global.fetch(url.href, {
                    cache: 'no-store',
                    headers: {'Accept': 'application/octet-stream'},
                });
                if (!response.ok) {
                    throw new ComposerRuntimeError(
                        `Could not load the selected installation profile (${response.status}).`,
                    );
                }
                if (response.url && new URL(response.url).origin !== location.origin) {
                    throw new ComposerRuntimeError(
                        'The managed installation-profile response must be same-origin.',
                    );
                }
                return Object.freeze({
                    bytes: await response.arrayBuffer(),
                    etag: response.headers.get('ETag'),
                });
            })().catch((error) => {
                installationProfileArtifacts.delete(key);
                throw error;
            });
            installationProfileArtifacts.set(key, pending);
        }
        return pending;
    }

    class RuntimeWorkerHost {
        constructor(workerUrl, options = {}) {
            this.workerUrl = workerUrl;
            this.name = options.name || 'ledgrid-composer-runtime';
            this.shared = Boolean(options.shared);
            this.maxRestarts = boundedInteger(
                options.maxRestarts, DEFAULT_RESTART_LIMIT, 0, MAX_RESTART_LIMIT,
            );
            this.restartWindowMs = boundedInteger(
                options.restartWindowMs,
                DEFAULT_RESTART_WINDOW_MS,
                MIN_RESTART_WINDOW_MS,
                MAX_RESTART_WINDOW_MS,
            );
            this.pending = new Map();
            this.sequence = 0;
            this.generation = 0;
            this.restartTimes = [];
            this.restartPromise = null;
            this.worker = null;
            this.fault = null;
            this.clientCount = 0;
            this.recoveryState = 'starting';
            this.lastFault = null;
            this.lastFaultKind = null;
            this._start();
        }

        _start() {
            let worker;
            try {
                worker = new Worker(this.workerUrl, {name: this.name, type: 'module'});
            } catch (error) {
                this.fault = new ComposerRuntimeError(
                    `Could not start the browser renderer worker: ${errorMessage(error)}`,
                    {workerFault: true, faultKind: 'startup', cause: error},
                );
                this.recoveryState = 'faulted';
                this.lastFault = this.fault.message;
                this.lastFaultKind = 'startup';
                throw this.fault;
            }
            this.worker = worker;
            this.fault = null;
            this.recoveryState = 'healthy';
            worker.addEventListener('message', (event) => {
                if (this.worker === worker) this._onMessage(event.data);
            });
            worker.addEventListener('error', (event) => {
                if (this.worker !== worker) return;
                if (typeof event.preventDefault === 'function') event.preventDefault();
                this._fail(new ComposerRuntimeError(
                    event.message || 'The browser renderer stopped unexpectedly.',
                    {workerFault: true, faultKind: 'worker-error'},
                ));
            });
            worker.addEventListener('messageerror', () => {
                if (this.worker !== worker) return;
                this._fail(new ComposerRuntimeError(
                    'The browser renderer returned an unreadable response.',
                    {workerFault: true, faultKind: 'message-error'},
                ));
            });
        }

        _onMessage(message) {
            if (!message || typeof message !== 'object') return;
            const entry = this.pending.get(message.requestId);
            if (!entry) return;
            global.clearTimeout(entry.timer);
            this.pending.delete(message.requestId);
            if (message.type === 'error') {
                entry.reject(new ComposerRuntimeError(
                    message.error || message.message || 'Browser renderer error.',
                    {response: message, workerFault: false},
                ));
                return;
            }
            entry.resolve(message);
        }

        _fail(error, {recordFault = true, state = null} = {}) {
            if (!this.fault) this.fault = error;
            if (recordFault) {
                this.lastFault = errorMessage(error);
                this.lastFaultKind = errorKind(error);
            }
            this.recoveryState = state || (error?.detail?.disposed ? 'disposed' : 'faulted');
            const worker = this.worker;
            this.worker = null;
            if (worker) worker.terminate();
            for (const entry of this.pending.values()) {
                global.clearTimeout(entry.timer);
                entry.reject(this.fault);
            }
            this.pending.clear();
        }

        request(message, timeoutMs = DEFAULT_TIMEOUT_MS) {
            if (this.fault || !this.worker) {
                return Promise.reject(this.fault || new ComposerRuntimeError(
                    'The browser renderer worker is unavailable.', {workerFault: true},
                ));
            }
            const requestId = `w${this.generation}-${++this.sequence}`;
            return new Promise((resolve, reject) => {
                const timer = global.setTimeout(() => {
                    const error = new ComposerRuntimeError(
                        `The renderer did not answer within ${Math.round(timeoutMs / 1000)} seconds.`,
                        {workerFault: true, timeout: true, faultKind: 'timeout'},
                    );
                    this._fail(error);
                    reject(error);
                }, timeoutMs);
                this.pending.set(requestId, {resolve, reject, timer});
                try {
                    this.worker.postMessage({...message, requestId});
                } catch (error) {
                    const wrapped = new ComposerRuntimeError(
                        `Could not send work to the browser renderer: ${errorMessage(error)}`,
                        {workerFault: true, faultKind: 'post-message', cause: error},
                    );
                    this._fail(wrapped);
                }
            });
        }

        async restart(reason = null) {
            if (this.restartPromise) return this.restartPromise;
            this.restartPromise = (async () => {
                const now = Date.now();
                this.restartTimes = this.restartTimes.filter(
                    (value) => now - value <= this.restartWindowMs,
                );
                if (this.restartTimes.length >= this.maxRestarts) {
                    const restartLabel = this.maxRestarts === 1 ? 'restart' : 'restarts';
                    const exhausted = new ComposerRuntimeError(
                        `The renderer stopped repeatedly; automatic recovery is limited to ${this.maxRestarts} ${restartLabel} within the recovery window.`,
                        {workerFault: true, recoveryExhausted: true, cause: reason},
                    );
                    this.fault = exhausted;
                    this.recoveryState = 'exhausted';
                    this.lastFault = errorMessage(reason);
                    this.lastFaultKind = errorKind(reason);
                    throw exhausted;
                }
                this.restartTimes.push(now);
                this.lastFault = errorMessage(reason);
                this.lastFaultKind = errorKind(reason);
                this.recoveryState = 'recovering';
                this._fail(new ComposerRuntimeError('Renderer restarting.', {
                    workerFault: true, cause: reason,
                }), {recordFault: false, state: 'recovering'});
                this.generation += 1;
                this._start();
                return this.generation;
            })().finally(() => {
                this.restartPromise = null;
            });
            return this.restartPromise;
        }

        terminate(reason = 'Renderer disposed.') {
            this._fail(new ComposerRuntimeError(reason, {workerFault: true, disposed: true}));
        }

        restrictRecoveryPolicy(options = {}) {
            this.maxRestarts = Math.min(
                this.maxRestarts,
                boundedInteger(options.maxRestarts, DEFAULT_RESTART_LIMIT, 0, MAX_RESTART_LIMIT),
            );
            this.restartWindowMs = Math.max(
                this.restartWindowMs,
                boundedInteger(
                    options.restartWindowMs,
                    DEFAULT_RESTART_WINDOW_MS,
                    MIN_RESTART_WINDOW_MS,
                    MAX_RESTART_WINDOW_MS,
                ),
            );
        }

        diagnostics() {
            return Object.freeze({
                shared: this.shared,
                generation: this.generation,
                restarts: this.restartTimes.length,
                pendingRequests: this.pending.size,
                clients: this.clientCount,
                healthy: Boolean(this.worker && !this.fault),
                recovery: Object.freeze({
                    state: this.recoveryState,
                    attempts: this.restartTimes.length,
                    limit: this.maxRestarts,
                    windowMs: this.restartWindowMs,
                    exhausted: this.recoveryState === 'exhausted',
                    lastFault: this.lastFault,
                    lastFaultKind: this.lastFaultKind,
                }),
            });
        }
    }

    function sharedPythonHost(runtime, options) {
        const profile = installationProfileDescriptor(options.installationProfile);
        const key = `${runtime.worker_url}\n${runtime.asset_url || ''}\n${profile?.digest || ''}\n${profile?.artifactUrl || ''}`;
        let host = sharedPythonHosts.get(key);
        if (!host) {
            host = new RuntimeWorkerHost(runtime.worker_url, {
                name: 'ledgrid-python-session',
                shared: true,
                maxRestarts: options.maxRestarts,
                restartWindowMs: options.restartWindowMs,
            });
            sharedPythonHosts.set(key, host);
        } else {
            host.restrictRecoveryPolicy(options);
        }
        host.clientCount += 1;
        return {host, key};
    }

    function serviceWorkerRequest(message, timeoutMs = 10000) {
        const controller = global.navigator?.serviceWorker?.controller;
        if (!controller || typeof global.MessageChannel !== 'function') {
            return Promise.resolve({
                type: 'OFFLINE_STATUS',
                readyOffline: false,
                reason: 'The offline worker is not controlling this page yet.',
            });
        }
        return new Promise((resolve, reject) => {
            const channel = new global.MessageChannel();
            const timer = global.setTimeout(() => {
                reject(new ComposerRuntimeError('The offline worker did not answer.'));
            }, timeoutMs);
            channel.port1.onmessage = (event) => {
                global.clearTimeout(timer);
                resolve(event.data || {});
            };
            controller.postMessage(message, [channel.port2]);
        });
    }

    class ComposerRuntime {
        constructor(component, geometry, options = {}) {
            this.component = component;
            this.geometry = geometry;
            this.timeoutMs = options.timeoutMs || DEFAULT_TIMEOUT_MS;
            this.initTimeoutMs = options.initTimeoutMs || DEFAULT_INIT_TIMEOUT_MS;
            this.options = options;
            this.installationProfile = installationProfileDescriptor(options.installationProfile);
            this.host = null;
            this.hostKey = null;
            this.hostShared = false;
            this.ready = false;
            this.disposed = false;
            this.engine = component?.browser_runtime?.kind || 'browser worker';
            this.runtimeId = (++runtimeSequence).toString(36);
            this.instances = new Map();
            this.renderGenerations = new Map();
            this.latestRenders = new Map();
            this.attachPromise = null;
        }

        _scopedInstanceId(instanceId = 'primary') {
            return `c${this.runtimeId}.${safeInstancePart(instanceId)}`;
        }

        _descriptor(component, params, publicId) {
            const runtime = component?.browser_runtime || {};
            return {
                publicId,
                scopedId: this._scopedInstanceId(publicId),
                pluginId: component.plugin_id,
                className: component.class_name,
                assetUrl: runtime.asset_url || null,
                params: snapshotValue(params || {}),
                installationProfile: this.installationProfile,
                initializedGeneration: -1,
            };
        }

        _acquireHost() {
            const runtime = this.component?.browser_runtime || {};
            if (!this.installationProfile) {
                throw new ComposerRuntimeError(
                    'The browser renderer requires an exact managed installation-profile artifact.',
                );
            }
            if (!runtime.supported || !runtime.worker_url) {
                throw new ComposerRuntimeError(
                    runtime.reason || 'This component has no browser renderer.',
                );
            }
            if (isPythonRuntime(runtime)) {
                const acquired = sharedPythonHost(runtime, this.options);
                this.host = acquired.host;
                this.hostKey = acquired.key;
                this.hostShared = true;
            } else {
                this.host = new RuntimeWorkerHost(runtime.worker_url, {
                    name: `ledgrid-${this.component.key || this.component.plugin_id}`,
                    shared: false,
                    maxRestarts: this.options.maxRestarts,
                    restartWindowMs: this.options.restartWindowMs,
                });
                this.host.clientCount = 1;
            }
        }

        async _initializeDescriptor(descriptor) {
            const profileArtifact = await installationProfileArtifact(
                descriptor.installationProfile,
            );
            const response = await this.host.request({
                type: 'init',
                instanceId: descriptor.scopedId,
                pluginId: descriptor.pluginId,
                className: descriptor.className,
                geometry: this.geometry,
                params: descriptor.params,
                assetUrl: descriptor.assetUrl,
                installationProfile: descriptor.installationProfile,
                installationProfileArtifact: profileArtifact,
            }, this.initTimeoutMs);
            if (response.type !== 'ready') {
                throw new ComposerRuntimeError(`Unexpected renderer response: ${String(response.type)}`);
            }
            descriptor.initializedGeneration = this.host.generation;
            if (response.engine) this.engine = response.engine;
            descriptor.lastResponse = {...response, instanceId: descriptor.publicId};
            return descriptor.lastResponse;
        }

        async _ensureAttached() {
            if (!this.host) throw new ComposerRuntimeError('The browser renderer is not available.');
            const stale = Array.from(this.instances.values()).filter(
                (item) => item.initializedGeneration !== this.host.generation,
            );
            if (!stale.length) return;
            if (this.attachPromise) {
                await this.attachPromise;
                return this._ensureAttached();
            }
            this.attachPromise = (async () => {
                for (const descriptor of stale) await this._initializeDescriptor(descriptor);
            })().finally(() => {
                this.attachPromise = null;
            });
            return this.attachPromise;
        }

        async _withRecovery(operation) {
            try {
                await this._ensureAttached();
                return await operation();
            } catch (error) {
                if (!error?.detail?.workerFault || error?.detail?.recoveryExhausted || this.disposed) {
                    throw error;
                }
                await this.host.restart(error);
                for (const descriptor of this.instances.values()) {
                    descriptor.initializedGeneration = -1;
                }
                await this._ensureAttached();
                return operation();
            }
        }

        async init(params = {}) {
            if (this.host) this.dispose();
            this.disposed = false;
            this._acquireHost();
            const descriptor = this._descriptor(this.component, params, 'primary');
            this.instances.set('primary', descriptor);
            try {
                await this._withRecovery(() => Promise.resolve(null));
                this.ready = true;
                return descriptor.lastResponse;
            } catch (error) {
                this.dispose();
                throw error;
            }
        }

        async initInstance(component, params = {}, instanceId = 'primary') {
            if (!this.host || !this.ready) {
                throw new ComposerRuntimeError('The browser renderer is not ready.');
            }
            const runtime = component?.browser_runtime || {};
            if (
                runtime.worker_url !== this.component?.browser_runtime?.worker_url
                || runtime.asset_url !== this.component?.browser_runtime?.asset_url
            ) {
                throw new ComposerRuntimeError(
                    'A shared renderer instance must use the same browser worker and runtime asset.',
                );
            }
            const publicId = safeInstancePart(instanceId);
            const prior = this.instances.get(publicId);
            if (prior) await this.disposeInstance(publicId);
            const descriptor = this._descriptor(component, params, publicId);
            this.instances.set(publicId, descriptor);
            try {
                await this._withRecovery(() => Promise.resolve(null));
                return descriptor.lastResponse;
            } catch (error) {
                this.instances.delete(publicId);
                throw error;
            }
        }

        _nextRenderGeneration(publicId) {
            const generation = (this.renderGenerations.get(publicId) || 0) + 1;
            this.renderGenerations.set(publicId, generation);
            return generation;
        }

        async _resolveLatest(publicId, generation, rawPromise) {
            const response = await rawPromise;
            const latest = this.latestRenders.get(publicId);
            if (response.type === 'obsolete' || (latest && generation !== latest.generation)) {
                if (!latest || latest.generation === generation) {
                    throw new ComposerRuntimeError('The renderer discarded an obsolete frame.', {
                        obsolete: true, generation,
                    });
                }
                return this._resolveLatest(publicId, latest.generation, latest.promise);
            }
            if (response.type !== 'frame') {
                throw new ComposerRuntimeError(`Unexpected renderer response: ${String(response.type)}`);
            }
            if (response.engine) this.engine = response.engine;
            return {...response, instanceId: publicId};
        }

        _render(publicId, elapsed, frameIndex, params, wallTime = null) {
            if (!this.host || !this.ready) {
                return Promise.reject(new ComposerRuntimeError('The browser renderer is not ready.'));
            }
            const descriptor = this.instances.get(publicId);
            if (!descriptor) {
                return Promise.reject(new ComposerRuntimeError(
                    `Renderer instance ${publicId} is not initialized.`,
                ));
            }
            const requestedParams = snapshotValue(params || {});
            const requestParams = parameterDelta(descriptor.params, requestedParams);
            descriptor.params = snapshotValue({...descriptor.params, ...requestedParams});
            const generation = this._nextRenderGeneration(publicId);
            const rawPromise = this._withRecovery(() => {
                if (this.renderGenerations.get(publicId) !== generation) {
                    return Promise.resolve({
                        type: 'obsolete', instanceId: descriptor.scopedId, generation,
                    });
                }
                return this.host.request({
                    type: 'render',
                    instanceId: descriptor.scopedId,
                    generation,
                    elapsed,
                    frameIndex,
                    params: requestParams,
                    wallTime,
                }, this.timeoutMs);
            });
            this.latestRenders.set(publicId, {generation, promise: rawPromise});
            return this._resolveLatest(publicId, generation, rawPromise);
        }

        render(elapsed, frameIndex, params) {
            return this._render('primary', elapsed, frameIndex, params);
        }

        renderInstance(instanceId, elapsed, frameIndex, params, wallTime = null) {
            return this._render(safeInstancePart(instanceId), elapsed, frameIndex, params, wallTime);
        }

        renderInstances(requests) {
            if (!this.host || !this.ready) {
                return Promise.reject(new ComposerRuntimeError('The browser renderer is not ready.'));
            }
            if (!isPythonRuntime(this.component?.browser_runtime)) {
                return Promise.reject(new ComposerRuntimeError(
                    'Batched instance rendering requires the shared Python worker.',
                ));
            }
            if (!Array.isArray(requests) || requests.length < 1 || requests.length > 8) {
                return Promise.reject(new ComposerRuntimeError(
                    'Batched instance rendering requires 1-8 requests.',
                ));
            }
            const prepared = [];
            const publicIds = new Set();
            try {
                for (const request of requests) {
                    const publicId = safeInstancePart(request?.instanceId || 'primary');
                    if (publicIds.has(publicId)) {
                        throw new ComposerRuntimeError(
                            'Batched instance rendering requires distinct instance IDs.',
                        );
                    }
                    publicIds.add(publicId);
                    const descriptor = this.instances.get(publicId);
                    if (!descriptor) {
                        throw new ComposerRuntimeError(
                            `Renderer instance ${publicId} is not initialized.`,
                        );
                    }
                    const requestedParams = snapshotValue(request?.params || {});
                    const requestParams = parameterDelta(descriptor.params, requestedParams);
                    descriptor.params = snapshotValue({...descriptor.params, ...requestedParams});
                    prepared.push({
                        publicId,
                        descriptor,
                        generation: this._nextRenderGeneration(publicId),
                        elapsed: request?.elapsed,
                        frameIndex: request?.frameIndex,
                        params: requestParams,
                        wallTime: request?.wallTime ?? null,
                    });
                }
            } catch (error) {
                return Promise.reject(error);
            }

            const rawPromise = this._withRecovery(() => {
                if (prepared.some((item) => (
                    this.renderGenerations.get(item.publicId) !== item.generation
                ))) {
                    return Promise.reject(new ComposerRuntimeError(
                        'The renderer discarded an obsolete composed frame.',
                        {obsolete: true},
                    ));
                }
                return this.host.request({
                    type: 'renderBatch',
                    renders: prepared.map((item) => ({
                        instanceId: item.descriptor.scopedId,
                        generation: item.generation,
                        elapsed: item.elapsed,
                        frameIndex: item.frameIndex,
                        params: item.params,
                        wallTime: item.wallTime,
                    })),
                }, this.timeoutMs);
            });
            const resultPromise = rawPromise.then((response) => {
                if (response.type === 'obsoleteBatch') {
                    throw new ComposerRuntimeError(
                        'The renderer discarded an obsolete composed frame.',
                        {obsolete: true, response},
                    );
                }
                if (
                    response.type !== 'frameBatch'
                    || !Array.isArray(response.frames)
                    || response.frames.length !== prepared.length
                    || !(response.pixels instanceof ArrayBuffer)
                ) {
                    throw new ComposerRuntimeError('The renderer returned an invalid frame batch.');
                }
                if (response.engine) this.engine = response.engine;
                return response.frames.map((frame, index) => {
                    const item = prepared[index];
                    if (
                        frame.instanceId !== item.descriptor.scopedId
                        || frame.generation !== item.generation
                        || this.renderGenerations.get(item.publicId) !== item.generation
                        || !Number.isSafeInteger(frame.byteOffset)
                        || !Number.isSafeInteger(frame.byteLength)
                        || frame.byteOffset < 0
                        || frame.byteLength < 1
                        || frame.byteOffset + frame.byteLength > response.pixels.byteLength
                    ) {
                        throw new ComposerRuntimeError(
                            'The renderer discarded an obsolete or invalid composed frame.',
                            {obsolete: true, response: frame},
                        );
                    }
                    return {
                        ...frame,
                        type: 'frame',
                        instanceId: item.publicId,
                        pixels: new Uint8Array(
                            response.pixels, frame.byteOffset, frame.byteLength,
                        ),
                    };
                });
            });
            prepared.forEach((item, index) => {
                const instancePromise = resultPromise.then((frames) => frames[index]);
                instancePromise.catch(() => null);
                this.latestRenders.set(item.publicId, {
                    generation: item.generation,
                    promise: instancePromise,
                });
            });
            return resultPromise;
        }

        async disposeInstance(instanceId) {
            const publicId = safeInstancePart(instanceId);
            if (!this.host || !this.ready || publicId === 'primary') return null;
            const descriptor = this.instances.get(publicId);
            this.instances.delete(publicId);
            this.renderGenerations.delete(publicId);
            this.latestRenders.delete(publicId);
            if (!descriptor || descriptor.initializedGeneration !== this.host.generation) return null;
            try {
                const response = await this.host.request({
                    type: 'dispose', instanceId: descriptor.scopedId,
                }, this.timeoutMs);
                return response.type === 'disposed' ? {...response, instanceId: publicId} : null;
            } catch (_error) {
                return null;
            }
        }

        async recover() {
            if (!this.host || this.disposed) {
                throw new ComposerRuntimeError('The browser renderer is not available.');
            }
            await this.host.restart(new ComposerRuntimeError(
                'Recovery requested.', {workerFault: true, faultKind: 'manual'},
            ));
            for (const descriptor of this.instances.values()) descriptor.initializedGeneration = -1;
            await this._ensureAttached();
            this.ready = true;
            return this.diagnostics();
        }

        async prepareOffline() {
            const runtime = this.component?.browser_runtime || {};
            if (!isPythonRuntime(runtime)) {
                return ComposerRuntime.offlineStatus();
            }
            if (!this.host || !this.ready) {
                throw new ComposerRuntimeError('The Python browser renderer is not ready.');
            }
            const response = await this._withRecovery(() => this.host.request({
                type: 'prepare',
                assetUrl: runtime.asset_url || null,
                packages: ['numpy', 'pillow'],
            }, this.initTimeoutMs));
            if (response.type !== 'prepared') {
                throw new ComposerRuntimeError(`Unexpected renderer response: ${String(response.type)}`);
            }
            return serviceWorkerRequest({
                type: 'PYTHON_RUNTIME_READY',
                pyodideVersion: response.pyodideVersion,
                packages: response.packages,
            });
        }

        diagnostics() {
            return Object.freeze({
                engine: this.engine,
                ready: this.ready,
                disposed: this.disposed,
                rendererInstances: this.instances.size,
                installationProfileDigest: this.installationProfile?.digest || null,
                worker: this.host?.diagnostics() || null,
            });
        }

        dispose() {
            if (this.disposed && !this.host) return;
            this.disposed = true;
            this.ready = false;
            const host = this.host;
            const descriptors = Array.from(this.instances.values());
            this.instances.clear();
            this.renderGenerations.clear();
            this.latestRenders.clear();
            if (host) {
                for (const descriptor of descriptors) {
                    if (descriptor.initializedGeneration === host.generation && host.worker) {
                        host.request({type: 'dispose', instanceId: descriptor.scopedId}, this.timeoutMs)
                            .catch(() => null);
                    }
                }
                host.clientCount = Math.max(0, host.clientCount - 1);
                if (!this.hostShared) host.terminate();
            }
            this.host = null;
            this.hostKey = null;
            this.hostShared = false;
        }

        static offlineStatus() {
            return serviceWorkerRequest({type: 'OFFLINE_STATUS'});
        }

        static diagnostics() {
            const workers = Array.from(sharedPythonHosts.values()).map((host) => host.diagnostics());
            return Object.freeze({
                sharedPythonWorkers: workers.length,
                liveSharedPythonWorkers: workers.filter((item) => item.healthy).length,
                workers,
            });
        }

        static shutdownSharedWorkers() {
            for (const host of sharedPythonHosts.values()) host.terminate('Shared renderer shutdown.');
            sharedPythonHosts.clear();
        }
    }

    global.LEDGridComposerRuntime = Object.freeze({ComposerRuntime, ComposerRuntimeError});
})(window);
