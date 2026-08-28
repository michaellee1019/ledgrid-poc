(() => {
    'use strict';

    const EMPTY = 0;
    const FOLIAGE = 1;
    const PLANTER = 2;
    const TOOL_VALUES = {erase: EMPTY, foliage: FOLIAGE, planter_bowls: PLANTER};

    class PlantMaskPainter {
        constructor() {
            this.canvas = document.getElementById('painterCanvas');
            this.ctx = this.canvas.getContext('2d', {alpha: false});
            this.layout = {stripCount: 1, ledsPerStrip: 1, totalLeds: 1};
            this.maskState = new Uint8Array(1);
            this.colors = {
                empty: [0, 0, 0],
                foliage: [48, 220, 96],
                planter_bowls: [255, 72, 190],
            };
            this.cellWidth = Number(document.getElementById('zoomRange').value) || 16;
            this.cellHeight = this.cellWidth;
            this.cellGap = 1;
            this.activeTool = 'foliage';
            this.history = [];
            this.maxHistory = 60;
            this.dirty = false;
            this.savedSignature = '';
            this.isPainting = false;
            this.strokeChanged = false;
            this.lastStrokeCoord = null;
            this.previewTimer = null;
            this.previewInFlight = false;
            this.previewPending = false;
            this.lastPreviewSignature = '';
            this.initialized = false;
            this.mirrorActive = false;
            this.liveSessionEntered = false;
            this.modeChanging = false;
            this.leaveCleanupSent = false;
            this.presetCatalog = [];
            this.bindEvents();
        }

        bindEvents() {
            this.canvas.addEventListener('contextmenu', (event) => event.preventDefault());
            this.canvas.addEventListener('pointerdown', (event) => this.pointerDown(event));
            this.canvas.addEventListener('pointermove', (event) => this.pointerMove(event));
            this.canvas.addEventListener('pointerup', (event) => this.pointerUp(event));
            this.canvas.addEventListener('pointercancel', (event) => this.pointerUp(event));

            document.querySelectorAll('[data-tool]').forEach((button) => {
                button.addEventListener('click', () => this.selectTool(button.dataset.tool));
            });
            document.getElementById('undoBtn').addEventListener('click', () => this.undo());
            document.getElementById('mirrorWallBtn').addEventListener(
                'click', () => this.startMirroring(),
            );
            document.getElementById('returnToDraftBtn').addEventListener(
                'click', () => this.returnToDraft(),
            );
            document.getElementById('painterPresetSelect').addEventListener(
                'change', () => this.updateControls(),
            );
            document.getElementById('painterPresetName').addEventListener(
                'input', () => this.updateControls(),
            );
            document.getElementById('loadPainterPresetBtn').addEventListener(
                'click', () => this.loadSelectedPreset(),
            );
            document.getElementById('savePainterPresetBtn').addEventListener(
                'click', () => this.savePreset(),
            );

            const zoom = document.getElementById('zoomRange');
            zoom.addEventListener('input', () => {
                this.cellWidth = Number(zoom.value) || 16;
                this.cellHeight = this.cellWidth;
                document.getElementById('zoomLabel').textContent = `${this.cellWidth}px`;
                this.resizeCanvas();
                this.render();
            });

            window.addEventListener('keydown', (event) => {
                const modifier = event.metaKey || event.ctrlKey;
                if (modifier && event.key.toLowerCase() === 'z') {
                    event.preventDefault();
                    this.undo();
                } else if (!modifier && event.key === '1') {
                    this.selectTool('foliage');
                } else if (!modifier && event.key === '2') {
                    this.selectTool('planter_bowls');
                } else if (!modifier && event.key.toLowerCase() === 'e') {
                    this.selectTool('erase');
                }
            });

            window.addEventListener('beforeunload', (event) => {
                if (this.dirty) {
                    event.preventDefault();
                    event.returnValue = '';
                }
            });
            window.addEventListener('pagehide', () => this.cleanupLiveOutputOnLeave());
            window.addEventListener('ledgrid:live-status', (event) => {
                this.reconcileLiveStatus(event.detail);
            });
        }

        reconcileLiveStatus(status) {
            if (!this.mirrorActive || this.modeChanging || !status) return;
            const painterIsLive = status.mode === 'painter' || status.painter_active;
            if (painterIsLive) return;
            this.mirrorActive = false;
            this.liveSessionEntered = false;
            this.cancelPendingPreview();
            this.updateOutputState();
            this.setStatus('Painter output stopped. Your canvas remains a private draft.', 'success');
        }

        async initialize() {
            try {
                const response = await fetch('/api/painter/masks', {cache: 'no-store'});
                const payload = await this.responseJson(response);
                this.loadPayload(payload);
                this.initialized = true;
                this.updateControls();
                this.updateOutputState();
                await this.loadPresetCatalog();
                this.setStatus('Draft loaded. The wall output was not changed.', 'success');
            } catch (error) {
                console.error('Failed to initialize mask painter', error);
                this.setStatus(error.message || 'Failed to load masks', 'error');
            }
        }

        async responseJson(response) {
            let payload = {};
            try {
                payload = await response.json();
            } catch (_error) {
                // The HTTP status below still gives a useful fallback message.
            }
            if (!response.ok) {
                throw new Error(payload.error || `Request failed (HTTP ${response.status})`);
            }
            return payload;
        }

        loadPayload(payload) {
            const info = payload.led_info || {};
            const stripCount = Number(info.strip_count);
            const ledsPerStrip = Number(info.leds_per_strip);
            const totalLeds = Number(info.total_leds);
            if (!Number.isInteger(stripCount) || !Number.isInteger(ledsPerStrip)
                    || totalLeds !== stripCount * ledsPerStrip) {
                throw new Error('The selected managed profile contains invalid wall geometry');
            }

            this.layout = {stripCount, ledsPerStrip, totalLeds};
            this.maskState = new Uint8Array(totalLeds);
            for (const index of payload.masks?.foliage || []) {
                if (Number.isInteger(index) && index >= 0 && index < totalLeds) {
                    this.maskState[index] = FOLIAGE;
                }
            }
            for (const index of payload.masks?.planter_bowls || []) {
                if (Number.isInteger(index) && index >= 0 && index < totalLeds) {
                    this.maskState[index] = PLANTER;
                }
            }
            for (const maskType of payload.mask_types || []) {
                if (Array.isArray(maskType.color) && maskType.color.length >= 3) {
                    this.colors[maskType.id] = maskType.color.slice(0, 3).map(this.clampByte);
                }
            }
            this.applySwatches();
            this.history = [];
            this.savedSignature = this.previewSignature();
            this.dirty = false;
            this.resizeCanvas();
            this.render();
            this.updateControls();
        }

        clampByte(value) {
            const parsed = Number(value);
            return Number.isFinite(parsed) ? Math.max(0, Math.min(255, Math.round(parsed))) : 0;
        }

        applySwatches() {
            for (const id of ['foliage', 'planter_bowls']) {
                const swatch = document.querySelector(`[data-swatch="${id}"]`);
                const color = this.colors[id];
                if (swatch && color) {
                    swatch.style.backgroundColor = `rgb(${color.join(',')})`;
                }
            }
        }

        resizeCanvas() {
            this.canvas.width = this.layout.stripCount * (this.cellWidth + this.cellGap);
            this.canvas.height = this.layout.ledsPerStrip * (this.cellHeight + this.cellGap);
            document.getElementById('layoutBadge').textContent =
                `${this.layout.stripCount} × ${this.layout.ledsPerStrip}`;
            const surfaceExplanation = document.getElementById('surfaceExplanation');
            if (this.layout.stripCount === 32 && this.layout.ledsPerStrip === 138) {
                surfaceExplanation.textContent =
                    'This editor covers the 32 × 138 plant-mask surface. The installation has '
                    + '33 output columns; its extra column is outside plant-mask calibration '
                    + 'and is not edited here.';
            } else {
                surfaceExplanation.textContent =
                    `The selected managed profile defines this ${this.layout.stripCount} × `
                    + `${this.layout.ledsPerStrip} editable surface. The installation has 33 × 138 `
                    + 'output pixels; only pixels represented by the mask geometry are edited here.';
            }
        }

        indexToCoord(index) {
            const strip = Math.floor(index / this.layout.ledsPerStrip);
            const led = index % this.layout.ledsPerStrip;
            return {strip, row: this.layout.ledsPerStrip - 1 - led};
        }

        coordToIndex(strip, row) {
            if (strip < 0 || strip >= this.layout.stripCount
                    || row < 0 || row >= this.layout.ledsPerStrip) {
                return null;
            }
            const led = this.layout.ledsPerStrip - 1 - row;
            return strip * this.layout.ledsPerStrip + led;
        }

        pointerCoord(event) {
            const rect = this.canvas.getBoundingClientRect();
            const x = (event.clientX - rect.left) * this.canvas.width / rect.width;
            const y = (event.clientY - rect.top) * this.canvas.height / rect.height;
            const strip = Math.floor(x / (this.cellWidth + this.cellGap));
            const row = Math.floor(y / (this.cellHeight + this.cellGap));
            return this.coordToIndex(strip, row) === null ? null : {strip, row};
        }

        render() {
            this.ctx.fillStyle = 'rgb(0,0,0)';
            this.ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
            for (let index = 0; index < this.layout.totalLeds; index += 1) {
                const value = this.maskState[index];
                const color = value === FOLIAGE
                    ? this.colors.foliage
                    : value === PLANTER ? this.colors.planter_bowls : this.colors.empty;
                this.drawPixel(index, color);
            }
            this.updateCounts();
        }

        drawPixel(index, color) {
            const {strip, row} = this.indexToCoord(index);
            this.ctx.fillStyle = `rgb(${color[0]},${color[1]},${color[2]})`;
            this.ctx.fillRect(
                strip * (this.cellWidth + this.cellGap),
                row * (this.cellHeight + this.cellGap),
                this.cellWidth,
                this.cellHeight,
            );
        }

        selectTool(tool) {
            if (!(tool in TOOL_VALUES)) {
                return;
            }
            this.activeTool = tool;
            document.querySelectorAll('[data-tool]').forEach((button) => {
                const active = button.dataset.tool === tool;
                button.classList.toggle('active', active);
                button.setAttribute('aria-pressed', active ? 'true' : 'false');
            });
        }

        pointerDown(event) {
            if (event.button !== 0 && event.pointerType !== 'touch') {
                return;
            }
            const coord = this.pointerCoord(event);
            if (!coord) {
                return;
            }
            event.preventDefault();
            this.history.push(this.maskState.slice());
            if (this.history.length > this.maxHistory) {
                this.history.shift();
            }
            this.isPainting = true;
            this.strokeChanged = false;
            this.lastStrokeCoord = coord;
            this.canvas.classList.add('is-painting');
            this.canvas.setPointerCapture(event.pointerId);
            this.paintLine(coord, coord);
            this.updateControls();
        }

        pointerMove(event) {
            if (!this.isPainting) {
                return;
            }
            const coord = this.pointerCoord(event);
            if (!coord) {
                return;
            }
            event.preventDefault();
            this.paintLine(this.lastStrokeCoord, coord);
            this.lastStrokeCoord = coord;
        }

        pointerUp(event) {
            if (!this.isPainting) {
                return;
            }
            this.isPainting = false;
            this.lastStrokeCoord = null;
            this.canvas.classList.remove('is-painting');
            if (this.canvas.hasPointerCapture(event.pointerId)) {
                this.canvas.releasePointerCapture(event.pointerId);
            }
            if (!this.strokeChanged) {
                this.history.pop();
                this.updateControls();
            }
            this.schedulePreview(0);
        }

        paintLine(start, end) {
            let x0 = start.strip;
            let y0 = start.row;
            const x1 = end.strip;
            const y1 = end.row;
            const dx = Math.abs(x1 - x0);
            const sx = x0 < x1 ? 1 : -1;
            const dy = -Math.abs(y1 - y0);
            const sy = y0 < y1 ? 1 : -1;
            let error = dx + dy;
            let changed = false;

            while (true) {
                const index = this.coordToIndex(x0, y0);
                if (index !== null) {
                    changed = this.paintIndex(index) || changed;
                }
                if (x0 === x1 && y0 === y1) {
                    break;
                }
                const doubled = 2 * error;
                if (doubled >= dy) {
                    error += dy;
                    x0 += sx;
                }
                if (doubled <= dx) {
                    error += dx;
                    y0 += sy;
                }
            }

            if (changed) {
                this.strokeChanged = true;
                this.updateDirtyState();
                this.updateCounts();
                this.updateControls();
                this.schedulePreview(55);
            }
        }

        paintIndex(index) {
            const value = TOOL_VALUES[this.activeTool];
            if (this.maskState[index] === value) {
                return false;
            }
            this.maskState[index] = value;
            const color = value === FOLIAGE
                ? this.colors.foliage
                : value === PLANTER ? this.colors.planter_bowls : this.colors.empty;
            this.drawPixel(index, color);
            return true;
        }

        updateCounts() {
            let foliage = 0;
            let planter = 0;
            for (const value of this.maskState) {
                foliage += value === FOLIAGE ? 1 : 0;
                planter += value === PLANTER ? 1 : 0;
            }
            document.getElementById('foliageCount').textContent = foliage.toLocaleString();
            document.getElementById('planterCount').textContent = planter.toLocaleString();
        }

        updateControls() {
            document.getElementById('undoBtn').disabled = this.history.length === 0;
            document.getElementById('mirrorWallBtn').disabled =
                !this.initialized || this.mirrorActive || this.modeChanging;
            document.getElementById('returnToDraftBtn').disabled = this.modeChanging;
            const presetSelect = document.getElementById('painterPresetSelect');
            const presetName = document.getElementById('painterPresetName');
            document.getElementById('loadPainterPresetBtn').disabled =
                !this.initialized || this.mirrorActive || this.modeChanging || !presetSelect.value;
            document.getElementById('savePainterPresetBtn').disabled =
                !this.initialized || this.modeChanging || !presetName.value.trim();
        }

        updateOutputState() {
            const state = document.getElementById('outputState');
            const badge = document.getElementById('outputStateBadge');
            const title = document.getElementById('outputStateTitle');
            const description = document.getElementById('outputStateDescription');
            const mirrorButton = document.getElementById('mirrorWallBtn');
            const draftButton = document.getElementById('returnToDraftBtn');

            state.classList.toggle('is-live', this.mirrorActive);
            state.setAttribute('aria-busy', this.modeChanging ? 'true' : 'false');
            badge.textContent = this.mirrorActive ? 'Live' : 'Draft';
            title.textContent = this.mirrorActive ? 'Mirroring on the wall' : 'Editing privately';
            description.textContent = this.mirrorActive
                ? 'Every completed stroke is sent to the installation until you return to draft.'
                : 'Changes stay in this browser until you save a private Painter frame preset or choose Mirror to wall. Saving never publishes or selects a managed profile.';
            mirrorButton.classList.toggle('d-none', this.mirrorActive);
            draftButton.classList.toggle('d-none', !this.mirrorActive);
            this.updateControls();
        }

        updateDirtyState() {
            this.dirty = this.previewSignature() !== this.savedSignature;
        }

        undo() {
            if (!this.history.length) {
                return;
            }
            this.maskState = this.history.pop();
            this.updateDirtyState();
            this.render();
            this.updateControls();
            this.setStatus(
                this.mirrorActive
                    ? 'Undid the last stroke. Updating the wall mirror…'
                    : 'Undid the last stroke. The change remains in this draft.',
            );
            this.schedulePreview(0);
        }

        frameAsList() {
            const frame = new Array(this.layout.totalLeds);
            for (let index = 0; index < this.layout.totalLeds; index += 1) {
                const value = this.maskState[index];
                const color = value === FOLIAGE
                    ? this.colors.foliage
                    : value === PLANTER ? this.colors.planter_bowls : this.colors.empty;
                frame[index] = color.slice();
            }
            return frame;
        }

        previewSignature() {
            // FNV-1a is enough to skip byte-identical full-frame sends.
            let hash = 2166136261;
            for (const value of this.maskState) {
                hash ^= value;
                hash = Math.imul(hash, 16777619);
            }
            return `${this.maskState.length}:${hash >>> 0}`;
        }

        schedulePreview(delay = 70) {
            if (!this.mirrorActive) {
                this.previewPending = false;
                return;
            }
            this.previewPending = true;
            if (this.previewTimer) {
                return;
            }
            this.previewTimer = window.setTimeout(() => {
                this.previewTimer = null;
                this.pushPreview();
            }, delay);
        }

        async pushPreview(force = false, allowDraftStart = false) {
            if (!this.mirrorActive && !allowDraftStart) {
                return false;
            }
            if (this.previewInFlight) {
                this.previewPending = true;
                return false;
            }
            const signature = this.previewSignature();
            if (!force && signature === this.lastPreviewSignature) {
                this.previewPending = false;
                return true;
            }

            this.previewInFlight = true;
            this.previewPending = false;
            try {
                const response = await fetch('/api/painter/frame', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        led_info: {
                            strip_count: this.layout.stripCount,
                            leds_per_strip: this.layout.ledsPerStrip,
                        },
                        frame_data: this.frameAsList(),
                    }),
                });
                await this.responseJson(response);
                this.lastPreviewSignature = signature;
                return true;
            } catch (error) {
                console.error('Failed to synchronize wall preview', error);
                this.setStatus(`Wall sync failed: ${error.message}`, 'error');
                return false;
            } finally {
                this.previewInFlight = false;
                if (this.mirrorActive
                        && (this.previewPending
                            || this.previewSignature() !== this.lastPreviewSignature)) {
                    this.schedulePreview(0);
                }
            }
        }

        async startMirroring() {
            if (!this.initialized || this.mirrorActive || this.modeChanging) {
                return;
            }
            this.modeChanging = true;
            this.liveSessionEntered = true;
            this.updateOutputState();
            this.setStatus('Starting the wall mirror…');
            const started = await this.pushPreview(true, true);
            this.modeChanging = false;
            if (!started) {
                this.liveSessionEntered = false;
                this.updateOutputState();
                this.setStatus('Could not start mirroring. This draft is still private.', 'error');
                return;
            }

            this.mirrorActive = true;
            this.leaveCleanupSent = false;
            this.updateOutputState();
            if (this.previewSignature() !== this.lastPreviewSignature) {
                this.schedulePreview(0);
            }
            this.setStatus(
                'Wall mirror is live. Completed strokes now update the installation.',
                'success',
            );
        }

        async returnToDraft() {
            if (!this.mirrorActive || this.modeChanging) {
                return;
            }
            this.modeChanging = true;
            this.mirrorActive = false;
            this.cancelPendingPreview();
            this.updateOutputState();
            this.setStatus('Stopping painter output…');
            try {
                const response = await fetch('/api/stop', {method: 'POST'});
                await this.responseJson(response);
                this.modeChanging = false;
                this.liveSessionEntered = false;
                this.updateOutputState();
                this.setStatus(
                    'Returned to draft. Painter output on the wall has stopped.',
                    'success',
                );
            } catch (error) {
                console.error('Failed to stop painter output', error);
                this.modeChanging = false;
                this.mirrorActive = true;
                this.updateOutputState();
                this.setStatus(
                    `Could not confirm that wall output stopped: ${error.message}`,
                    'error',
                );
            }
        }

        cancelPendingPreview() {
            if (this.previewTimer) {
                window.clearTimeout(this.previewTimer);
                this.previewTimer = null;
            }
            this.previewPending = false;
        }

        cleanupLiveOutputOnLeave() {
            if (!this.liveSessionEntered || this.leaveCleanupSent) {
                return;
            }
            this.leaveCleanupSent = true;
            this.mirrorActive = false;
            this.liveSessionEntered = false;
            this.cancelPendingPreview();
            if (navigator.sendBeacon) {
                navigator.sendBeacon('/api/stop');
                return;
            }
            fetch('/api/stop', {method: 'POST', keepalive: true}).catch(() => {});
        }

        async loadPresetCatalog(selectedId = '') {
            try {
                const response = await fetch('/api/painter/presets', {cache: 'no-store'});
                const payload = await this.responseJson(response);
                this.presetCatalog = Array.isArray(payload.presets) ? payload.presets : [];
                const select = document.getElementById('painterPresetSelect');
                select.replaceChildren(new Option(
                    this.presetCatalog.length ? 'Choose a Painter frame preset…' : 'No saved presets',
                    '',
                ));
                this.presetCatalog.forEach((preset) => {
                    select.add(new Option(preset.name || preset.preset_id, preset.preset_id));
                });
                if (selectedId && this.presetCatalog.some((preset) => preset.preset_id === selectedId)) {
                    select.value = selectedId;
                }
                this.updateControls();
            } catch (error) {
                console.error('Failed to load painter presets', error);
                this.setStatus('Painter frame presets are temporarily unavailable. Your draft is unaffected.', 'error');
            }
        }

        pixelMaskValue(pixel) {
            if (!Array.isArray(pixel)) return EMPTY;
            const matches = (color) => color.every((value, index) => Number(pixel[index]) === value);
            if (matches(this.colors.foliage)) return FOLIAGE;
            if (matches(this.colors.planter_bowls)) return PLANTER;
            return EMPTY;
        }

        async loadSelectedPreset() {
            const select = document.getElementById('painterPresetSelect');
            if (!select.value || this.mirrorActive || this.modeChanging) return;
            const button = document.getElementById('loadPainterPresetBtn');
            button.disabled = true;
            this.setStatus('Loading preset into this private draft…');
            try {
                const response = await fetch(`/api/painter/presets/${encodeURIComponent(select.value)}`, {cache: 'no-store'});
                const payload = await this.responseJson(response);
                const info = payload.led_info || {};
                const sameGeometry = Number(info.strip_count) === this.layout.stripCount
                    && Number(info.leds_per_strip) === this.layout.ledsPerStrip
                    && Array.isArray(payload.frame_data)
                    && payload.frame_data.length === this.layout.totalLeds;
                if (!sameGeometry) throw new Error('This Painter preset uses different frame geometry.');
                this.history.push(this.maskState.slice());
                if (this.history.length > this.maxHistory) this.history.shift();
                this.maskState = Uint8Array.from(payload.frame_data, (pixel) => this.pixelMaskValue(pixel));
                this.updateDirtyState();
                this.render();
                this.updateControls();
                this.setStatus(`${payload.name || 'Preset'} loaded as a private draft.`, 'success');
            } catch (error) {
                console.error('Failed to load painter preset', error);
                this.setStatus(error.message || 'Failed to load preset', 'error');
                this.updateControls();
            }
        }

        async savePreset() {
            const input = document.getElementById('painterPresetName');
            const name = input.value.trim();
            if (!name || !this.initialized || this.modeChanging) return;
            const button = document.getElementById('savePainterPresetBtn');
            button.disabled = true;
            this.setStatus('Saving this private Painter draft as a frame preset…');
            try {
                const response = await fetch('/api/painter/presets', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        name,
                        led_info: {
                            strip_count: this.layout.stripCount,
                            leds_per_strip: this.layout.ledsPerStrip,
                            total_leds: this.layout.totalLeds,
                        },
                        frame_data: this.frameAsList(),
                    }),
                });
                const payload = await this.responseJson(response);
                input.value = '';
                await this.loadPresetCatalog(payload.preset?.preset_id || '');
                this.setStatus(`${payload.preset?.name || name} saved. The wall was not changed.`, 'success');
            } catch (error) {
                console.error('Failed to save painter preset', error);
                this.setStatus(error.message || 'Failed to save preset', 'error');
                this.updateControls();
            }
        }

        setStatus(message, kind = '') {
            const status = document.getElementById('paintStatus');
            status.textContent = message;
            status.classList.toggle('error', kind === 'error');
            status.classList.toggle('success', kind === 'success');
        }
    }

    window.addEventListener('DOMContentLoaded', () => {
        const painter = new PlantMaskPainter();
        painter.initialize();
        window.plantMaskPainter = painter;
    });
})();
