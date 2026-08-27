(function composerApplication() {
    'use strict';

    const {ComposerRuntime} = window.LEDGridComposerRuntime || {};
    const $ = (id) => document.getElementById(id);
    const STORAGE_PREFIX = 'ledgrid.browser-composer.v1';
    const SAMPLE_FRAMES = 48;
    const state = {
        bootstrap: null,
        component: null,
        params: {},
        originalParams: {},
        selectedPreset: null,
        compare: 'draft',
        playing: true,
        elapsed: 0,
        frameIndex: 0,
        fps: 30,
        lastAnimationTime: 0,
        lastRenderTime: 0,
        renderInFlight: false,
        needsRender: true,
        runtimes: {draft: null, original: null},
        originalRuntimePromise: null,
        compareGeneration: 0,
        frames: {draft: null, original: null},
        runtimeGeneration: 0,
        history: [],
        historyIndex: -1,
        catalogFilter: 'all',
        query: '',
        checkerGeneration: 0,
        autosaveTimer: null,
    };

    function clone(value) {
        return JSON.parse(JSON.stringify(value ?? null));
    }

    function humanize(value) {
        return String(value || '').replaceAll('_', ' ').replace(/\b\w/g, (match) => match.toUpperCase());
    }

    function safeNumber(value, fallback = 0) {
        const number = Number(value);
        return Number.isFinite(number) ? number : fallback;
    }

    function formatTime(seconds) {
        const value = Math.max(0, safeNumber(seconds));
        const minutes = Math.floor(value / 60);
        const remainder = (value % 60).toFixed(1).padStart(4, '0');
        return `${minutes}:${remainder}`;
    }

    function toast(message, kind = 'info') {
        const item = document.createElement('div');
        item.className = `toast ${kind}`;
        item.textContent = message;
        $('toastRegion').appendChild(item);
        window.setTimeout(() => item.remove(), 3600);
    }

    function assertBootstrap(payload) {
        if (!payload || payload.schema !== 'ledgrid.browser-composer-bootstrap' || payload.schema_version !== 1) {
            throw new Error('The composer catalog uses an unsupported schema.');
        }
        const geometry = payload.geometry || {};
        const width = Number(geometry.strip_count);
        const height = Number(geometry.leds_per_strip);
        const total = Number(geometry.total_leds);
        if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1 || total !== width * height) {
            throw new Error('The composer catalog contains invalid wall geometry.');
        }
        if (!Array.isArray(payload.components)) throw new Error('The composer catalog has no component list.');
        return payload;
    }

    async function loadBootstrap() {
        const response = await fetch('/api/v1/composer/bootstrap', {headers: {'Accept': 'application/json'}});
        if (!response.ok) throw new Error(`Catalog request failed (${response.status}).`);
        state.bootstrap = assertBootstrap(await response.json());
        configureCanvas();
        renderCatalog();
        const lastKey = localStorage.getItem(`${STORAGE_PREFIX}.last-component`);
        const preferred = state.bootstrap.components.find((item) => item.key === lastKey && item.browser_runtime?.supported)
            || state.bootstrap.components.find((item) => item.browser_runtime?.supported);
        if (preferred) await selectComponent(preferred);
        else showCatalogUnavailable('No components currently declare a supported browser runtime.');
    }

    function configureCanvas() {
        const {strip_count: width, leds_per_strip: height} = state.bootstrap.geometry;
        const canvas = $('previewCanvas');
        canvas.width = width;
        canvas.height = height;
    }

    function runtimeKind(component) {
        const kind = component?.browser_runtime?.kind;
        return kind === 'native' ? 'native' : 'python';
    }

    function matchesCatalog(component) {
        const filterMatches = state.catalogFilter === 'all' || runtimeKind(component) === state.catalogFilter;
        const haystack = [component.name, component.description, component.plugin_id, component.provider, component.role]
            .filter(Boolean).join(' ').toLowerCase();
        return filterMatches && haystack.includes(state.query.toLowerCase());
    }

    function renderCatalog() {
        const host = $('componentList');
        host.replaceChildren();
        const visible = state.bootstrap.components.filter(matchesCatalog);
        $('catalogCount').textContent = String(visible.length);
        host.setAttribute('aria-busy', 'false');
        if (!visible.length) {
            const empty = document.createElement('p');
            empty.className = 'catalog-empty';
            empty.textContent = 'No renderers match that search.';
            host.appendChild(empty);
            return;
        }
        visible.forEach((component) => {
            const runtime = component.browser_runtime || {};
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'component-card';
            button.setAttribute('role', 'option');
            button.setAttribute('aria-selected', String(component.key === state.component?.key));
            button.disabled = !runtime.supported;
            if (!runtime.supported) button.title = runtime.reason || 'Browser rendering is unavailable.';

            const icon = document.createElement('span');
            icon.className = 'component-icon';
            icon.setAttribute('aria-hidden', 'true');
            icon.textContent = component.icon || (runtime.kind === 'native' ? '⚙' : '✦');
            const copy = document.createElement('span');
            copy.className = 'component-copy';
            const name = document.createElement('strong');
            name.textContent = component.name || humanize(component.plugin_id);
            const meta = document.createElement('small');
            meta.textContent = runtime.supported
                ? `${component.role ? humanize(component.role) + ' · ' : ''}${runtime.kind === 'native' ? 'C++ → Wasm' : 'Python → Pyodide'}`
                : (runtime.reason || 'Browser runtime unavailable');
            copy.append(name, meta);
            const chip = document.createElement('span');
            chip.className = `runtime-chip${runtime.supported ? '' : ' unsupported'}`;
            chip.textContent = runtime.supported ? (runtime.kind === 'native' ? 'Wasm' : 'Py') : 'Server';
            button.append(icon, copy, chip);
            button.addEventListener('click', () => selectComponent(component));
            host.appendChild(button);
        });
    }

    function presetParams(preset) {
        return clone(preset?.params || preset?.parameters || preset?.parameter_overrides || {});
    }

    function presetIdentity(preset, index = 0) {
        return preset?.preset_id || preset?.id || `preset-${index}`;
    }

    function renderPresets() {
        const host = $('presetList');
        host.replaceChildren();
        const presets = Array.isArray(state.component?.presets) ? state.component.presets : [];
        $('presetCount').textContent = String(presets.length);
        presets.forEach((preset, index) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'preset-button';
            button.setAttribute('aria-current', String(state.selectedPreset === presetIdentity(preset, index)));
            const name = document.createElement('strong');
            name.textContent = preset.name || humanize(presetIdentity(preset, index));
            const description = document.createElement('small');
            description.textContent = preset.description || preset.category || 'Curated starting point';
            button.append(name, description);
            button.addEventListener('click', () => applyPreset(preset, index));
            host.appendChild(button);
        });
        if (!presets.length) {
            const empty = document.createElement('p');
            empty.className = 'catalog-empty';
            empty.textContent = 'No curated presets for this renderer.';
            host.appendChild(empty);
        }
    }

    function defaultParams(component) {
        const result = {...clone(component.defaults || {})};
        Object.entries(component.parameter_schema || {}).forEach(([key, contract]) => {
            if (!(key in result) && contract && Object.prototype.hasOwnProperty.call(contract, 'default')) {
                result[key] = clone(contract.default);
            }
        });
        return result;
    }

    function loadAutosave(component) {
        try {
            const saved = JSON.parse(localStorage.getItem(`${STORAGE_PREFIX}.draft.${component.key}`));
            if (!saved || saved.component_key !== component.key || typeof saved.params !== 'object') return null;
            return saved;
        } catch (_error) {
            return null;
        }
    }

    async function selectComponent(component, options = {}) {
        if (!component?.browser_runtime?.supported) return;
        if (state.component?.key === component.key && !options.force) return;
        state.checkerGeneration += 1;
        state.component = component;
        state.selectedPreset = null;
        localStorage.setItem(`${STORAGE_PREFIX}.last-component`, component.key);
        const saved = options.ignoreAutosave ? null : loadAutosave(component);
        const defaults = defaultParams(component);
        state.params = clone(saved?.params || defaults);
        state.originalParams = clone(saved?.original_params || defaults);
        $('presetName').value = saved?.name || `${component.name || humanize(component.plugin_id)} draft`;
        state.elapsed = 0;
        state.frameIndex = 0;
        state.frames = {draft: null, original: null};
        resetHistory();
        resetChecker();
        updateComponentCopy();
        renderCatalog();
        renderPresets();
        renderParameterControls();
        setComposerEnabled(true);
        if (!options.deferRuntime) await startRuntimes();
        scheduleAutosave();
        requestRender();
    }

    function applyPreset(preset, index) {
        state.selectedPreset = presetIdentity(preset, index);
        const params = {...defaultParams(state.component), ...presetParams(preset)};
        state.params = clone(params);
        state.originalParams = clone(params);
        $('presetName').value = preset.name || humanize(state.selectedPreset);
        state.elapsed = 0;
        state.frameIndex = 0;
        state.frames = {draft: null, original: null};
        resetHistory();
        renderPresets();
        renderParameterControls();
        resetChecker();
        restartRuntimesAtCurrentState();
        scheduleAutosave();
        toast(`Loaded ${$('presetName').value}.`);
    }

    function updateComponentCopy() {
        const component = state.component;
        $('stageHeading').textContent = component.name || humanize(component.plugin_id);
        $('componentDescription').textContent = component.description || 'Browser-rendered animation component.';
        const runtime = component.browser_runtime || {};
        $('provenanceTitle').textContent = runtime.kind === 'native' ? 'C++ compiled to WebAssembly' : 'Python running in Pyodide';
        $('provenanceDetail').textContent = 'This authored renderer preview runs locally. It is not camera feedback or framebuffer readback from the installed wall.';
        $('previewPlaceholder').hidden = true;
    }

    function showCatalogUnavailable(message) {
        $('stageHeading').textContent = 'Browser renderer unavailable';
        $('componentDescription').textContent = message;
        $('previewPlaceholder').hidden = false;
        $('saveState').textContent = 'Nothing to edit';
        setComposerEnabled(false);
    }

    function setComposerEnabled(enabled) {
        ['playButton', 'timeline', 'resetButton', 'runCheckerButton', 'copyButton', 'exportButton'].forEach((id) => {
            $(id).disabled = !enabled;
        });
    }

    function setEngineState(status, title, detail) {
        const badge = $('engineBadge');
        badge.dataset.state = status;
        badge.querySelector('strong').textContent = title;
        badge.querySelector('small').textContent = detail;
    }

    async function startRuntimes() {
        disposeRuntimes();
        const generation = ++state.runtimeGeneration;
        setEngineState('loading', 'Loading locally', state.component.browser_runtime.kind === 'native' ? 'Preparing WebAssembly' : 'Preparing Python');
        try {
            const geometry = state.bootstrap.geometry;
            const draft = new ComposerRuntime(state.component, geometry);
            state.runtimes = {draft, original: null};
            await draft.init(state.params);
            if (state.compare !== 'draft') await ensureOriginalRuntime();
            if (generation !== state.runtimeGeneration) return;
            const engine = draft.engine || state.component.browser_runtime.kind;
            setEngineState('ready', 'Local renderer', engine);
            $('playButton').disabled = false;
            $('timeline').disabled = false;
            $('runCheckerButton').disabled = false;
            state.needsRender = true;
        } catch (error) {
            if (generation !== state.runtimeGeneration) return;
            setEngineState('error', 'Renderer error', 'Preview stopped');
            $('componentDescription').textContent = error.message;
            $('previewPlaceholder').hidden = false;
            $('previewPlaceholder').querySelector('strong').textContent = 'Local renderer could not start';
            $('previewPlaceholder').querySelector('small').textContent = error.message;
            $('playButton').disabled = true;
            $('timeline').disabled = true;
            $('runCheckerButton').disabled = true;
        }
    }

    function disposeRuntimes() {
        state.runtimes.draft?.dispose();
        state.runtimes.original?.dispose();
        state.runtimes = {draft: null, original: null};
        state.originalRuntimePromise = null;
    }

    function ensureOriginalRuntime() {
        if (state.runtimes.original?.ready) return Promise.resolve(state.runtimes.original);
        if (state.originalRuntimePromise) return state.originalRuntimePromise;
        const generation = state.runtimeGeneration;
        const runtime = new ComposerRuntime(state.component, state.bootstrap.geometry);
        state.runtimes.original = runtime;
        state.originalRuntimePromise = runtime.init(state.originalParams).then(() => {
            if (generation !== state.runtimeGeneration) {
                runtime.dispose();
                throw new Error('Reference renderer was replaced.');
            }
            return runtime;
        }).catch((error) => {
            if (state.runtimes.original === runtime) state.runtimes.original = null;
            runtime.dispose();
            throw error;
        }).finally(() => {
            state.originalRuntimePromise = null;
        });
        return state.originalRuntimePromise;
    }

    function restartRuntimesAtCurrentState() {
        startRuntimes().then(requestRender);
    }

    function renderParameterControls() {
        const host = $('parameterList');
        host.replaceChildren();
        const schema = state.component?.parameter_schema || {};
        const entries = Object.entries(schema);
        $('parameterEmpty').hidden = entries.length > 0;
        entries.forEach(([key, contract]) => host.appendChild(parameterControl(key, contract || {})));
    }

    function parameterControl(key, contract) {
        const type = String(contract.type || typeof state.params[key]).toLowerCase();
        const wrapper = document.createElement('div');
        wrapper.className = 'parameter-control';
        const labelRow = document.createElement('div');
        labelRow.className = 'parameter-label-row';
        const label = document.createElement('label');
        label.htmlFor = `parameter-${key}`;
        label.textContent = humanize(key);
        const readout = document.createElement('output');
        readout.className = 'parameter-value';
        readout.htmlFor = `parameter-${key}`;
        labelRow.append(label, readout);
        wrapper.appendChild(labelRow);

        let input;
        if (type === 'bool' || type === 'boolean') {
            wrapper.classList.add('switch-control');
            input = document.createElement('input');
            input.type = 'checkbox';
            input.checked = Boolean(state.params[key]);
            input.setAttribute('role', 'switch');
            readout.textContent = input.checked ? 'On' : 'Off';
            input.addEventListener('change', () => {
                updateParam(key, input.checked);
                readout.textContent = input.checked ? 'On' : 'Off';
                commitHistory();
            });
        } else if ((contract.options || contract.enum) && Array.isArray(contract.options || contract.enum)) {
            input = document.createElement('select');
            const options = contract.options || contract.enum;
            options.forEach((value) => {
                const option = document.createElement('option');
                option.value = String(value);
                option.textContent = humanize(value);
                option.selected = String(state.params[key]) === String(value);
                input.appendChild(option);
            });
            readout.textContent = humanize(state.params[key]);
            input.addEventListener('change', () => {
                const option = options.find((candidate) => String(candidate) === input.value);
                updateParam(key, option ?? input.value);
                readout.textContent = humanize(input.value);
                commitHistory();
            });
        } else if (['float', 'number', 'int', 'integer'].includes(type) && contract.min != null && contract.max != null) {
            const range = document.createElement('div');
            range.className = 'range-wrap';
            input = document.createElement('input');
            input.type = 'range';
            input.min = String(contract.min);
            input.max = String(contract.max);
            input.step = String(contract.step ?? (type === 'int' || type === 'integer' ? 1 : sensibleStep(contract.min, contract.max)));
            input.value = String(state.params[key] ?? contract.default ?? contract.min);
            const number = document.createElement('input');
            number.type = 'number';
            number.min = input.min;
            number.max = input.max;
            number.step = input.step;
            number.value = input.value;
            const applyNumeric = (raw) => {
                const value = numericValue(raw, type, contract);
                input.value = String(value);
                number.value = String(value);
                readout.textContent = formatParameterValue(value, input.step);
                updateParam(key, value);
            };
            input.addEventListener('input', () => applyNumeric(input.value));
            input.addEventListener('change', commitHistory);
            number.addEventListener('change', () => { applyNumeric(number.value); commitHistory(); });
            readout.textContent = formatParameterValue(input.value, input.step);
            range.append(input, number);
            wrapper.appendChild(range);
        } else if (type === 'object' || typeof state.params[key] === 'object') {
            input = document.createElement('textarea');
            input.value = JSON.stringify(state.params[key] ?? contract.default ?? {}, null, 2);
            readout.textContent = 'JSON';
            input.addEventListener('change', () => {
                try {
                    const parsed = JSON.parse(input.value);
                    input.removeAttribute('aria-invalid');
                    updateParam(key, parsed);
                    commitHistory();
                } catch (_error) {
                    input.setAttribute('aria-invalid', 'true');
                    toast(`${humanize(key)} must be valid JSON.`, 'error');
                }
            });
        } else {
            input = document.createElement('input');
            input.type = ['float', 'number', 'int', 'integer'].includes(type) ? 'number' : 'text';
            input.value = state.params[key] ?? contract.default ?? '';
            if (contract.min != null) input.min = contract.min;
            if (contract.max != null) input.max = contract.max;
            input.step = type === 'int' || type === 'integer' ? '1' : 'any';
            readout.textContent = String(input.value || '—');
            input.addEventListener('change', () => {
                const value = input.type === 'number' ? numericValue(input.value, type, contract) : input.value;
                updateParam(key, value);
                readout.textContent = String(value || '—');
                commitHistory();
            });
        }
        input.id = `parameter-${key}`;
        input.name = key;
        if (!wrapper.contains(input)) wrapper.appendChild(input);
        if (contract.description) {
            const description = document.createElement('p');
            description.className = 'parameter-description';
            description.textContent = contract.description;
            wrapper.appendChild(description);
        }
        return wrapper;
    }

    function sensibleStep(minimum, maximum) {
        const range = Math.abs(Number(maximum) - Number(minimum));
        if (range <= 1) return 0.01;
        if (range <= 10) return 0.05;
        if (range <= 100) return 1;
        return Math.max(1, Math.round(range / 100));
    }

    function numericValue(raw, type, contract) {
        let value = type === 'int' || type === 'integer' ? Math.round(Number(raw)) : Number(raw);
        if (!Number.isFinite(value)) value = safeNumber(contract.default);
        if (contract.min != null) value = Math.max(Number(contract.min), value);
        if (contract.max != null) value = Math.min(Number(contract.max), value);
        return value;
    }

    function formatParameterValue(value, step) {
        const numeric = Number(value);
        const precision = String(step).includes('.') ? Math.min(3, String(step).split('.')[1].length) : 0;
        return Number.isFinite(numeric) ? numeric.toFixed(precision) : String(value);
    }

    function updateParam(key, value) {
        state.params[key] = value;
        state.selectedPreset = null;
        renderPresets();
        resetChecker();
        scheduleAutosave();
        requestRender();
    }

    function snapshot() {
        return {params: clone(state.params), name: $('presetName').value};
    }

    function resetHistory() {
        state.history = [snapshot()];
        state.historyIndex = 0;
        updateHistoryButtons();
    }

    function commitHistory() {
        const next = snapshot();
        if (JSON.stringify(next) === JSON.stringify(state.history[state.historyIndex])) return;
        state.history = state.history.slice(0, state.historyIndex + 1);
        state.history.push(next);
        if (state.history.length > 60) state.history.shift();
        state.historyIndex = state.history.length - 1;
        updateHistoryButtons();
    }

    function restoreHistory(index) {
        const entry = state.history[index];
        if (!entry) return;
        state.historyIndex = index;
        state.params = clone(entry.params);
        $('presetName').value = entry.name;
        state.selectedPreset = null;
        renderParameterControls();
        renderPresets();
        resetChecker();
        updateHistoryButtons();
        scheduleAutosave();
        requestRender();
    }

    function updateHistoryButtons() {
        $('undoButton').disabled = !state.component || state.historyIndex <= 0;
        $('redoButton').disabled = !state.component || state.historyIndex >= state.history.length - 1;
    }

    function scheduleAutosave() {
        if (!state.component) return;
        $('saveState').textContent = 'Saving locally…';
        window.clearTimeout(state.autosaveTimer);
        state.autosaveTimer = window.setTimeout(() => {
            const payload = {
                schema: 'ledgrid.browser-composer-draft',
                schema_version: 1,
                component_key: state.component.key,
                provider: state.component.provider,
                plugin_id: state.component.plugin_id,
                name: $('presetName').value,
                params: state.params,
                original_params: state.originalParams,
                saved_at: new Date().toISOString(),
            };
            try {
                localStorage.setItem(`${STORAGE_PREFIX}.draft.${state.component.key}`, JSON.stringify(payload));
                $('saveState').textContent = 'Saved on this device';
            } catch (_error) {
                $('saveState').textContent = 'Local save unavailable';
            }
        }, 300);
    }

    function requestRender() {
        state.needsRender = true;
    }

    function animationLoop(now) {
        window.requestAnimationFrame(animationLoop);
        if (!state.component || !state.runtimes.draft?.ready) return;
        if (!state.lastAnimationTime) state.lastAnimationTime = now;
        const delta = Math.min(.1, Math.max(0, (now - state.lastAnimationTime) / 1000));
        state.lastAnimationTime = now;
        if (state.playing) {
            state.elapsed = (state.elapsed + delta) % 20;
            $('timeline').value = String(state.elapsed);
            $('timecode').textContent = formatTime(state.elapsed);
            state.needsRender = true;
        }
        const interval = 1000 / state.fps;
        if (!state.renderInFlight && state.needsRender && now - state.lastRenderTime >= interval) {
            state.lastRenderTime = now;
            renderCurrentFrame();
        }
    }

    async function renderCurrentFrame() {
        state.renderInFlight = true;
        state.needsRender = false;
        const generation = state.runtimeGeneration;
        const elapsed = state.elapsed;
        const frameIndex = state.frameIndex++;
        try {
            const requests = [];
            if (state.compare !== 'original') {
                requests.push(state.runtimes.draft.render(elapsed, frameIndex, clone(state.params)).then((frame) => ['draft', frame]));
            }
            if (state.compare !== 'draft') {
                requests.push(state.runtimes.original.render(elapsed, frameIndex, clone(state.originalParams)).then((frame) => ['original', frame]));
            }
            const results = await Promise.all(requests);
            if (generation !== state.runtimeGeneration) return;
            results.forEach(([kind, frame]) => { state.frames[kind] = validatedFrame(frame); });
            drawPreview();
        } catch (error) {
            if (generation === state.runtimeGeneration) {
                setEngineState('error', 'Preview paused', error.message);
                state.playing = false;
                syncPlayButton();
            }
        } finally {
            if (generation === state.runtimeGeneration) state.renderInFlight = false;
        }
    }

    function validatedFrame(frame) {
        const width = Number(frame.width);
        const height = Number(frame.height);
        const pixels = frame.pixels instanceof ArrayBuffer ? new Uint8Array(frame.pixels) : null;
        if (!pixels || !Number.isInteger(width) || !Number.isInteger(height) || pixels.length !== width * height * 3) {
            throw new Error('Renderer returned an invalid RGB frame.');
        }
        return {...frame, width, height, pixels};
    }

    function canonicalImageData(frame) {
        const rgba = new Uint8ClampedArray(frame.width * frame.height * 4);
        for (let strip = 0; strip < frame.width; strip += 1) {
            for (let led = 0; led < frame.height; led += 1) {
                const source = (strip * frame.height + led) * 3;
                const destination = ((frame.height - 1 - led) * frame.width + strip) * 4;
                rgba[destination] = frame.pixels[source];
                rgba[destination + 1] = frame.pixels[source + 1];
                rgba[destination + 2] = frame.pixels[source + 2];
                rgba[destination + 3] = 255;
            }
        }
        return new ImageData(rgba, frame.width, frame.height);
    }

    function frameCanvas(frame) {
        const canvas = document.createElement('canvas');
        canvas.width = frame.width;
        canvas.height = frame.height;
        canvas.getContext('2d').putImageData(canonicalImageData(frame), 0, 0);
        return canvas;
    }

    function drawPreview() {
        const canvas = $('previewCanvas');
        const context = canvas.getContext('2d', {alpha: false});
        context.imageSmoothingEnabled = false;
        context.fillStyle = '#020202';
        context.fillRect(0, 0, canvas.width, canvas.height);
        if (state.compare === 'draft' && state.frames.draft) {
            context.putImageData(canonicalImageData(state.frames.draft), 0, 0);
        } else if (state.compare === 'original' && state.frames.original) {
            context.putImageData(canonicalImageData(state.frames.original), 0, 0);
        } else if (state.compare === 'split' && state.frames.original && state.frames.draft) {
            const left = frameCanvas(state.frames.original);
            const right = frameCanvas(state.frames.draft);
            const split = Math.floor(canvas.width / 2);
            context.drawImage(left, 0, 0, split, canvas.height, 0, 0, split, canvas.height);
            context.drawImage(right, split, 0, canvas.width - split, canvas.height, split, 0, canvas.width - split, canvas.height);
            context.fillStyle = 'rgba(255,255,255,.72)';
            context.fillRect(split, 0, 1, canvas.height);
        }
        $('previewPlaceholder').hidden = true;
    }

    function syncPlayButton() {
        $('playButton').setAttribute('aria-label', state.playing ? 'Pause preview' : 'Play preview');
        $('playButton').querySelector('span').textContent = state.playing ? 'Ⅱ' : '▶';
    }

    async function setCompare(mode) {
        const generation = ++state.compareGeneration;
        if (mode !== 'draft' && !state.runtimes.original?.ready) {
            setEngineState('loading', 'Loading reference', state.component.browser_runtime.kind === 'native' ? 'Preparing second Wasm instance' : 'Preparing isolated Python');
            try {
                await ensureOriginalRuntime();
            } catch (error) {
                if (generation === state.compareGeneration) {
                    setEngineState('error', 'Compare unavailable', error.message);
                    toast(error.message, 'error');
                }
                return;
            }
            if (generation !== state.compareGeneration) return;
            setEngineState('ready', 'Local renderer', state.runtimes.draft.engine);
        }
        state.compare = mode;
        document.querySelectorAll('[data-compare]').forEach((button) => {
            button.setAttribute('aria-pressed', String(button.dataset.compare === mode));
        });
        $('splitLegend').hidden = mode !== 'split';
        requestRender();
    }

    function schemaCheck(component, params) {
        const problems = [];
        Object.entries(component.parameter_schema || {}).forEach(([key, contract]) => {
            const value = params[key];
            if (value === undefined) {
                problems.push(`${humanize(key)} is missing`);
                return;
            }
            const type = String(contract.type || '').toLowerCase();
            if ((type === 'bool' || type === 'boolean') && typeof value !== 'boolean') problems.push(`${humanize(key)} must be on or off`);
            if (['float', 'number', 'int', 'integer'].includes(type)) {
                if (!Number.isFinite(Number(value))) problems.push(`${humanize(key)} must be a number`);
                if ((type === 'int' || type === 'integer') && !Number.isInteger(Number(value))) problems.push(`${humanize(key)} must be a whole number`);
                if (contract.min != null && Number(value) < Number(contract.min)) problems.push(`${humanize(key)} is below its minimum`);
                if (contract.max != null && Number(value) > Number(contract.max)) problems.push(`${humanize(key)} is above its maximum`);
            }
            const options = contract.options || contract.enum;
            if (Array.isArray(options) && !options.some((option) => option === value || String(option) === String(value))) {
                problems.push(`${humanize(key)} is not a declared option`);
            }
            if (type === 'object' && (typeof value !== 'object' || value === null || Array.isArray(value))) problems.push(`${humanize(key)} must be an object`);
        });
        return problems;
    }

    function updateMetric(name, value, status, detail = null) {
        const row = document.querySelector(`[data-metric="${name}"]`);
        row.dataset.state = status;
        row.querySelector('dd').textContent = value;
        if (detail) row.querySelector('small').textContent = detail;
    }

    function resetChecker() {
        $('checkerDot').removeAttribute('data-state');
        $('checkSummary').dataset.grade = 'idle';
        $('checkGauge').textContent = '—';
        $('checkHeadline').textContent = 'Not checked yet';
        $('checkSummaryCopy').textContent = 'Run a local sample after tuning.';
        document.querySelectorAll('[data-metric]').forEach((row) => {
            delete row.dataset.state;
            row.querySelector('dd').textContent = 'Waiting';
        });
    }

    async function runChecker() {
        if (!state.component || !ComposerRuntime) return;
        const generation = ++state.checkerGeneration;
        const button = $('runCheckerButton');
        button.disabled = true;
        $('checkerProgress').hidden = false;
        $('checkerProgressBar').value = 0;
        $('checkerProgressValue').textContent = '0%';
        $('checkerProgressLabel').textContent = 'Preparing isolated renderer…';
        const schemaProblems = schemaCheck(state.component, state.params);
        const runtime = new ComposerRuntime(state.component, state.bootstrap.geometry, {timeoutMs: 30000});
        const renderTimes = [];
        let previous = null;
        let deltaTotal = 0;
        let deltaMax = 0;
        let changedPairs = 0;
        let luminanceTotal = 0;
        let luminancePeak = 0;
        let clippingChannels = 0;
        let channelCount = 0;
        let peakCurrent = 0;
        try {
            await runtime.init(clone(state.params));
            for (let index = 0; index < SAMPLE_FRAMES; index += 1) {
                if (generation !== state.checkerGeneration) throw new Error('Check replaced.');
                const frame = validatedFrame(await runtime.render(index / 12, index, clone(state.params)));
                const pixels = frame.pixels;
                renderTimes.push(safeNumber(frame.renderMs));
                let frameLuminance = 0;
                let frameCurrent = 0;
                for (let offset = 0; offset < pixels.length; offset += 3) {
                    const red = pixels[offset];
                    const green = pixels[offset + 1];
                    const blue = pixels[offset + 2];
                    const luminance = (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255;
                    frameLuminance += luminance;
                    luminancePeak = Math.max(luminancePeak, luminance);
                    frameCurrent += (red + green + blue) / 255 * .02;
                    if (red === 255) clippingChannels += 1;
                    if (green === 255) clippingChannels += 1;
                    if (blue === 255) clippingChannels += 1;
                    channelCount += 3;
                }
                luminanceTotal += frameLuminance / (pixels.length / 3);
                peakCurrent = Math.max(peakCurrent, frameCurrent);
                if (previous) {
                    let difference = 0;
                    for (let offset = 0; offset < pixels.length; offset += 1) difference += Math.abs(pixels[offset] - previous[offset]);
                    const normalized = difference / (pixels.length * 255);
                    deltaTotal += normalized;
                    deltaMax = Math.max(deltaMax, normalized);
                    // Slow, broad gradients can move meaningfully while their
                    // wall-wide mean delta remains below a tenth of a percent.
                    if (normalized > .0001) changedPairs += 1;
                }
                previous = pixels.slice();
                const completed = index + 1;
                $('checkerProgressBar').value = completed;
                $('checkerProgressValue').textContent = `${Math.round(completed / SAMPLE_FRAMES * 100)}%`;
                $('checkerProgressLabel').textContent = `Sampling frame ${completed} of ${SAMPLE_FRAMES}`;
            }
            const deltas = SAMPLE_FRAMES - 1;
            const motion = deltaTotal / deltas;
            const averageLuminance = luminanceTotal / SAMPLE_FRAMES;
            const clipping = clippingChannels / Math.max(1, channelCount);
            const sortedTimes = renderTimes.slice().sort((a, b) => a - b);
            const p95 = sortedTimes[Math.min(sortedTimes.length - 1, Math.ceil(sortedTimes.length * .95) - 1)];
            const warnings = [];
            const failures = [];

            updateMetric('schema', schemaProblems.length ? `${schemaProblems.length} issue${schemaProblems.length === 1 ? '' : 's'}` : 'Valid', schemaProblems.length ? 'fail' : 'pass', schemaProblems[0] || 'All declared values and bounds pass.');
            if (schemaProblems.length) failures.push('schema');

            const motionStatus = changedPairs === 0 ? 'warn' : 'pass';
            updateMetric('motion', changedPairs === 0 ? 'Still' : `${(motion * 100).toFixed(1)}% mean`, motionStatus, `${changedPairs} of ${deltas} sampled transitions changed.`);
            if (changedPairs === 0) warnings.push('motion');

            const luminanceStatus = averageLuminance > .72 ? 'warn' : 'pass';
            updateMetric('luminance', `${(averageLuminance * 100).toFixed(0)}% avg`, luminanceStatus, `${(luminancePeak * 100).toFixed(0)}% peak perceived luminance.`);
            if (luminanceStatus === 'warn') warnings.push('luminance');

            const clippingStatus = clipping > .4 ? 'fail' : clipping > .15 ? 'warn' : 'pass';
            updateMetric('clipping', `${(clipping * 100).toFixed(1)}%`, clippingStatus, 'Share of RGB channels pinned at 255. Black channels are not counted as clipping.');
            if (clippingStatus === 'fail') failures.push('clipping'); else if (clippingStatus === 'warn') warnings.push('clipping');

            const strobeStatus = deltaMax > .38 ? 'fail' : deltaMax > .22 ? 'warn' : 'pass';
            updateMetric('strobe', `${(deltaMax * 100).toFixed(1)}% max`, strobeStatus, strobeStatus === 'pass' ? 'No large full-wall jump in this sample.' : 'Large temporal delta; inspect the animation at full size.');
            if (strobeStatus === 'fail') failures.push('temporal delta'); else if (strobeStatus === 'warn') warnings.push('temporal delta');

            const currentStatus = peakCurrent > 180 ? 'fail' : peakCurrent > 120 ? 'warn' : 'pass';
            updateMetric('current', `${peakCurrent.toFixed(1)} A peak`, currentStatus, `Uncalibrated ${state.bootstrap.geometry.total_leds}-pixel RGB upper model at 5 V.`);
            if (currentStatus === 'fail') failures.push('estimated current'); else if (currentStatus === 'warn') warnings.push('estimated current');

            const renderStatus = p95 > 33 ? 'fail' : p95 > 16.7 ? 'warn' : 'pass';
            updateMetric('render', `${p95.toFixed(2)} ms`, renderStatus, `${runtime.engine}; measured on this browser, not receiver hardware.`);
            if (renderStatus === 'fail') failures.push('render time'); else if (renderStatus === 'warn') warnings.push('render time');

            const grade = failures.length ? 'fail' : warnings.length ? 'warn' : 'pass';
            const score = Math.max(0, 100 - failures.length * 24 - warnings.length * 8);
            $('checkSummary').dataset.grade = grade;
            $('checkGauge').textContent = String(score);
            $('checkerDot').dataset.state = grade;
            $('checkHeadline').textContent = grade === 'pass' ? 'Local sample looks healthy' : grade === 'warn' ? 'Review a few cautions' : 'Resolve local check failures';
            $('checkSummaryCopy').textContent = failures.length
                ? `Failed: ${failures.join(', ')}.`
                : warnings.length ? `Review: ${warnings.join(', ')}.` : `${SAMPLE_FRAMES} frames passed the local heuristics.`;
            $('saveState').textContent = 'Saved on this device';
        } catch (error) {
            if (generation === state.checkerGeneration && error.message !== 'Check replaced.') {
                $('checkSummary').dataset.grade = 'fail';
                $('checkGauge').textContent = '!';
                $('checkHeadline').textContent = 'Checker could not finish';
                $('checkSummaryCopy').textContent = error.message;
                $('checkerDot').dataset.state = 'fail';
            }
        } finally {
            runtime.dispose();
            if (generation === state.checkerGeneration) {
                $('checkerProgress').hidden = true;
                button.disabled = false;
            }
        }
    }

    function exportedPreset() {
        const cleanName = $('presetName').value.trim() || `${state.component.name} draft`;
        const presetId = cleanName.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '') || 'browser_draft';
        return {
            version: 2,
            preset_id: presetId,
            name: cleanName,
            animation: state.component.plugin_id,
            provider: state.component.provider,
            params: clone(state.params),
            provenance: {
                authored_with: 'ledgrid-browser-preset-composer',
                renderer_kind: state.component.browser_runtime.kind,
                note: 'Previewed locally in a browser worker; not verified on the physical installation.',
            },
        };
    }

    async function copyJson() {
        const text = JSON.stringify(exportedPreset(), null, 2);
        try {
            await navigator.clipboard.writeText(text);
            toast('Preset JSON copied.');
        } catch (_error) {
            const area = document.createElement('textarea');
            area.value = text;
            document.body.appendChild(area);
            area.select();
            document.execCommand('copy');
            area.remove();
            toast('Preset JSON copied.');
        }
    }

    function exportJson() {
        const preset = exportedPreset();
        const blob = new Blob([`${JSON.stringify(preset, null, 2)}\n`], {type: 'application/json'});
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = `${preset.preset_id}.json`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(link.href);
        toast('Preset exported.');
    }

    async function importJson(file) {
        try {
            const payload = JSON.parse(await file.text());
            const pluginId = payload.animation || payload.plugin_id;
            const provider = payload.provider;
            const matches = state.bootstrap.components.filter((item) => item.plugin_id === pluginId && (!provider || item.provider === provider));
            if (matches.length !== 1) throw new Error('The imported preset does not identify one browser-ready component.');
            const component = matches[0];
            if (!component.browser_runtime?.supported) throw new Error(component.browser_runtime?.reason || 'That renderer is not available in the browser.');
            await selectComponent(component, {force: true, ignoreAutosave: true, deferRuntime: true});
            state.params = {...defaultParams(component), ...presetParams(payload)};
            state.originalParams = clone(state.params);
            $('presetName').value = payload.name || humanize(payload.preset_id || pluginId);
            const problems = schemaCheck(component, state.params);
            if (problems.length) throw new Error(`Imported preset is invalid: ${problems[0]}.`);
            resetHistory();
            renderParameterControls();
            resetChecker();
            await startRuntimes();
            scheduleAutosave();
            toast('Preset imported and rendered locally.');
        } catch (error) {
            toast(error.message, 'error');
        } finally {
            $('importFile').value = '';
        }
    }

    function selectInspectorTab(name) {
        const checking = name === 'checker';
        $('controlsTab').setAttribute('aria-selected', String(!checking));
        $('checkerTab').setAttribute('aria-selected', String(checking));
        $('controlsPanel').hidden = checking;
        $('checkerPanel').hidden = !checking;
    }

    function selectMobileView(name) {
        const target = name === 'check' ? 'tune' : name;
        document.querySelectorAll('.mobile-view').forEach((view) => view.classList.toggle('is-active', view.dataset.mobileView === target));
        document.querySelectorAll('[data-mobile-target]').forEach((button) => {
            if (button.dataset.mobileTarget === name) button.setAttribute('aria-current', 'page');
            else button.removeAttribute('aria-current');
        });
        if (name === 'check') selectInspectorTab('checker');
        else if (name === 'tune') selectInspectorTab('controls');
    }

    function bindEvents() {
        $('componentSearch').addEventListener('input', (event) => { state.query = event.target.value; renderCatalog(); });
        document.querySelectorAll('[data-filter]').forEach((button) => button.addEventListener('click', () => {
            state.catalogFilter = button.dataset.filter;
            document.querySelectorAll('[data-filter]').forEach((item) => item.setAttribute('aria-pressed', String(item === button)));
            renderCatalog();
        }));
        document.querySelectorAll('[data-compare]').forEach((button) => button.addEventListener('click', () => setCompare(button.dataset.compare)));
        $('playButton').addEventListener('click', () => { state.playing = !state.playing; state.lastAnimationTime = performance.now(); syncPlayButton(); requestRender(); });
        $('timeline').addEventListener('input', (event) => {
            state.elapsed = safeNumber(event.target.value);
            $('timecode').textContent = formatTime(state.elapsed);
            requestRender();
        });
        $('fpsSelect').addEventListener('change', (event) => { state.fps = safeNumber(event.target.value, 30); });
        $('undoButton').addEventListener('click', () => restoreHistory(state.historyIndex - 1));
        $('redoButton').addEventListener('click', () => restoreHistory(state.historyIndex + 1));
        $('resetButton').addEventListener('click', () => {
            state.params = clone(state.originalParams);
            state.selectedPreset = null;
            renderParameterControls();
            renderPresets();
            commitHistory();
            resetChecker();
            scheduleAutosave();
            requestRender();
        });
        $('presetName').addEventListener('input', scheduleAutosave);
        $('presetName').addEventListener('change', commitHistory);
        $('importButton').addEventListener('click', () => $('importFile').click());
        $('importFile').addEventListener('change', (event) => event.target.files[0] && importJson(event.target.files[0]));
        $('copyButton').addEventListener('click', copyJson);
        $('exportButton').addEventListener('click', exportJson);
        $('controlsTab').addEventListener('click', () => selectInspectorTab('controls'));
        $('checkerTab').addEventListener('click', () => selectInspectorTab('checker'));
        $('runCheckerButton').addEventListener('click', runChecker);
        document.querySelectorAll('[data-mobile-target]').forEach((button) => button.addEventListener('click', () => selectMobileView(button.dataset.mobileTarget)));
        document.addEventListener('keydown', (event) => {
            const modifier = navigator.platform?.toLowerCase().includes('mac') ? event.metaKey : event.ctrlKey;
            if (!modifier || event.altKey) return;
            if (event.key.toLowerCase() === 'z') {
                event.preventDefault();
                restoreHistory(state.historyIndex + (event.shiftKey ? 1 : -1));
            } else if (event.key.toLowerCase() === 's') {
                event.preventDefault();
                if (state.component) exportJson();
            }
        });
        window.addEventListener('beforeunload', disposeRuntimes);
    }

    async function registerServiceWorker() {
        if (!('serviceWorker' in navigator) || !window.isSecureContext) return;
        try {
            await navigator.serviceWorker.register('/composer-service-worker.js', {scope: '/'});
        } catch (error) {
            console.info('Composer offline shell is not available:', error.message);
        }
    }

    async function initialize() {
        bindEvents();
        syncPlayButton();
        window.requestAnimationFrame(animationLoop);
        registerServiceWorker();
        try {
            if (!ComposerRuntime) throw new Error('The browser runtime adapter did not load.');
            await loadBootstrap();
        } catch (error) {
            showCatalogUnavailable(error.message);
            setEngineState('error', 'Catalog error', 'Refresh to retry');
            $('componentList').setAttribute('aria-busy', 'false');
            const empty = document.createElement('p');
            empty.className = 'catalog-empty';
            empty.textContent = error.message;
            $('componentList').replaceChildren(empty);
        }
    }

    initialize();
})();
