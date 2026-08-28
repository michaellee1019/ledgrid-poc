(function attachComposerRuntime(global) {
    'use strict';

    const PYTHON_ENGINE = 'python-pyodide-wasm';
    const DEFAULT_TIMEOUT_MS = 20000;
    const DEFAULT_INIT_TIMEOUT_MS = 90000;
    const DEFAULT_RESTART_LIMIT = 2;
    const DEFAULT_RESTART_WINDOW_MS = 60000;
    const sharedPythonHosts = new Map();
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

    function isPythonRuntime(runtime) {
        return runtime?.kind === 'python' || runtime?.engine === PYTHON_ENGINE;
    }

    function safeInstancePart(value) {
        const part = String(value || 'primary').replace(/[^A-Za-z0-9_.-]/g, '_');
        return (part || 'primary').slice(0, 40);
    }

    function installationProfileDescriptor(value) {
        if (!value || typeof value !== 'object') return null;
        const digest = String(value.digest || '').toLowerCase();
        if (!/^[0-9a-f]{64}$/.test(digest) || /^0+$/.test(digest)) return null;
        if (typeof value.artifactUrl !== 'string' || !value.artifactUrl) return null;
        return Object.freeze({digest, artifactUrl: value.artifactUrl});
    }

    class RuntimeWorkerHost {
        constructor(workerUrl, options = {}) {
            this.workerUrl = workerUrl;
            this.name = options.name || 'ledgrid-composer-runtime';
            this.shared = Boolean(options.shared);
            this.maxRestarts = options.maxRestarts ?? DEFAULT_RESTART_LIMIT;
            this.restartWindowMs = options.restartWindowMs ?? DEFAULT_RESTART_WINDOW_MS;
            this.pending = new Map();
            this.sequence = 0;
            this.generation = 0;
            this.restartTimes = [];
            this.restartPromise = null;
            this.worker = null;
            this.fault = null;
            this.clientCount = 0;
            this._start();
        }

        _start() {
            try {
                this.worker = new Worker(this.workerUrl, {name: this.name, type: 'module'});
            } catch (error) {
                this.fault = new ComposerRuntimeError(
                    `Could not start the browser renderer worker: ${errorMessage(error)}`,
                    {workerFault: true, cause: error},
                );
                throw this.fault;
            }
            this.fault = null;
            this.worker.addEventListener('message', (event) => this._onMessage(event.data));
            this.worker.addEventListener('error', (event) => {
                if (typeof event.preventDefault === 'function') event.preventDefault();
                this._fail(new ComposerRuntimeError(
                    event.message || 'The browser renderer stopped unexpectedly.',
                    {workerFault: true},
                ));
            });
            this.worker.addEventListener('messageerror', () => {
                this._fail(new ComposerRuntimeError(
                    'The browser renderer returned an unreadable response.',
                    {workerFault: true},
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

        _fail(error) {
            if (!this.fault) this.fault = error;
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
                        {workerFault: true, timeout: true},
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
                        {workerFault: true, cause: error},
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
                    throw new ComposerRuntimeError(
                        `The renderer stopped repeatedly; automatic recovery is limited to ${this.maxRestarts} restarts per minute.`,
                        {workerFault: true, recoveryExhausted: true, cause: reason},
                    );
                }
                this.restartTimes.push(now);
                this._fail(new ComposerRuntimeError('Renderer restarting.', {
                    workerFault: true, cause: reason,
                }));
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

        diagnostics() {
            return Object.freeze({
                shared: this.shared,
                generation: this.generation,
                restarts: this.restartTimes.length,
                pendingRequests: this.pending.size,
                clients: this.clientCount,
                healthy: Boolean(this.worker && !this.fault),
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
                params: {...(params || {})},
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
            const response = await this.host.request({
                type: 'init',
                instanceId: descriptor.scopedId,
                pluginId: descriptor.pluginId,
                className: descriptor.className,
                geometry: this.geometry,
                params: descriptor.params,
                assetUrl: descriptor.assetUrl,
                installationProfile: descriptor.installationProfile,
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
            descriptor.params = {...descriptor.params, ...(params || {})};
            const generation = this._nextRenderGeneration(publicId);
            const rawPromise = this._withRecovery(() => this.host.request({
                type: 'render',
                instanceId: descriptor.scopedId,
                generation,
                elapsed,
                frameIndex,
                params: params || {},
                wallTime,
            }, this.timeoutMs));
            this.latestRenders.set(publicId, {generation, promise: rawPromise});
            return this._resolveLatest(publicId, generation, rawPromise);
        }

        render(elapsed, frameIndex, params) {
            return this._render('primary', elapsed, frameIndex, params);
        }

        renderInstance(instanceId, elapsed, frameIndex, params, wallTime = null) {
            return this._render(safeInstancePart(instanceId), elapsed, frameIndex, params, wallTime);
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
            await this.host.restart(new ComposerRuntimeError('Recovery requested.'));
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
