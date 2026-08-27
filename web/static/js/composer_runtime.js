(function attachComposerRuntime(global) {
    'use strict';

    class ComposerRuntimeError extends Error {
        constructor(message, detail = null) {
            super(message);
            this.name = 'ComposerRuntimeError';
            this.detail = detail;
        }
    }

    class ComposerRuntime {
        constructor(component, geometry, options = {}) {
            this.component = component;
            this.geometry = geometry;
            this.timeoutMs = options.timeoutMs || 20000;
            this.initTimeoutMs = options.initTimeoutMs || 90000;
            this.worker = null;
            this.ready = false;
            this.pending = new Map();
            this.sequence = 0;
            this.engine = component?.browser_runtime?.kind || 'browser worker';
        }

        async init(params = {}) {
            const runtime = this.component?.browser_runtime || {};
            if (!runtime.supported || !runtime.worker_url) {
                throw new ComposerRuntimeError(runtime.reason || 'This component has no browser renderer.');
            }
            this.dispose();
            try {
                this.worker = new Worker(runtime.worker_url, {
                    name: `ledgrid-${this.component.key || this.component.plugin_id}`,
                    type: 'module',
                });
            } catch (error) {
                throw new ComposerRuntimeError(`Could not start the ${runtime.kind || 'browser'} worker.`, error);
            }
            this.worker.addEventListener('message', (event) => this.onMessage(event.data));
            this.worker.addEventListener('error', (event) => {
                this.rejectAll(new ComposerRuntimeError(event.message || 'The browser renderer stopped unexpectedly.'));
            });
            const response = await this.request({
                type: 'init',
                pluginId: this.component.plugin_id,
                className: this.component.class_name,
                geometry: this.geometry,
                params,
                assetUrl: runtime.asset_url || null,
            }, this.initTimeoutMs);
            this.ready = true;
            if (response.engine) this.engine = response.engine;
            return response;
        }

        render(elapsed, frameIndex, params) {
            if (!this.worker || !this.ready) {
                return Promise.reject(new ComposerRuntimeError('The browser renderer is not ready.'));
            }
            return this.request({type: 'render', elapsed, frameIndex, params});
        }

        request(message, timeoutMs = this.timeoutMs) {
            if (!this.worker) {
                return Promise.reject(new ComposerRuntimeError('The browser renderer is not available.'));
            }
            const requestId = `${Date.now().toString(36)}-${++this.sequence}`;
            return new Promise((resolve, reject) => {
                const timer = global.setTimeout(() => {
                    this.pending.delete(requestId);
                    reject(new ComposerRuntimeError(`The renderer did not answer within ${Math.round(timeoutMs / 1000)} seconds.`));
                }, timeoutMs);
                this.pending.set(requestId, {resolve, reject, timer});
                this.worker.postMessage({...message, requestId});
            });
        }

        onMessage(message) {
            if (!message || typeof message !== 'object') return;
            const entry = this.pending.get(message.requestId);
            if (!entry) return;
            global.clearTimeout(entry.timer);
            this.pending.delete(message.requestId);
            if (message.type === 'error') {
                entry.reject(new ComposerRuntimeError(message.error || message.message || 'Browser renderer error.', message));
                return;
            }
            if (message.type !== 'ready' && message.type !== 'frame') {
                entry.reject(new ComposerRuntimeError(`Unexpected renderer response: ${String(message.type)}`));
                return;
            }
            if (message.engine) this.engine = message.engine;
            entry.resolve(message);
        }

        rejectAll(error) {
            this.ready = false;
            for (const entry of this.pending.values()) {
                global.clearTimeout(entry.timer);
                entry.reject(error);
            }
            this.pending.clear();
        }

        dispose() {
            this.rejectAll(new ComposerRuntimeError('Renderer replaced.'));
            if (this.worker) this.worker.terminate();
            this.worker = null;
            this.ready = false;
        }
    }

    global.LEDGridComposerRuntime = Object.freeze({ComposerRuntime, ComposerRuntimeError});
})(window);
