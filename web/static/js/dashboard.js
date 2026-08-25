
    // Animation Renderer
    class LEDAnimationRenderer {
        constructor(canvasId) {
            this.canvas = document.getElementById(canvasId);
            this.ctx = this.canvas.getContext('2d');
            this.isRunning = false;
            this.frameCount = 0;
            this.lastFrameTime = 0;
            this.fps = 0;

            // Preview mode settings
            this.previewMode = false;
            this.previewAnimation = null;
            this.fpsCounter = 0;
            this.lastFpsTime = Date.now();
            this.fetchIntervalMs = IS_LOCAL_DASHBOARD ? 0 : 150;
            this.renderTimer = null;
            this.fetchInFlight = false;
            this.lastFrameData = null;
            this.previewParams = null;
            this.interactionTypes = [];

            // LED configuration - will be updated from server
            this.stripCount = Number.isFinite(INITIAL_STRIP_COUNT) && INITIAL_STRIP_COUNT > 0 ? INITIAL_STRIP_COUNT : 1;
            this.ledsPerStrip = Number.isFinite(INITIAL_LEDS_PER_STRIP) && INITIAL_LEDS_PER_STRIP > 0 ? INITIAL_LEDS_PER_STRIP : 1;
            this.totalLeds = this.stripCount * this.ledsPerStrip;

            // Rendering configuration
            this.ledSize = 6;
            this.ledSpacing = 0.5;
            this.stripSpacing = 1;
            this.canvas.addEventListener('click', (event) => this.requestInteractionAtEvent(event));
            this._onResize = () => this.syncDisplayWidth();
            window.addEventListener('resize', this._onResize);

            this.setupCanvas();
            this.initialize();
        }

        requestInteractionAtEvent(event) {
            if (!this.interactionTypes.includes('primary')) return;
            const rect = this.canvas.getBoundingClientRect();
            if (rect.width <= 0 || rect.height <= 0) return;
            const x = Math.max(0, Math.min(this.stripCount - 1,
                ((event.clientX - rect.left) / rect.width) * this.stripCount));
            const displayY = Math.max(0, Math.min(this.ledsPerStrip - 1,
                ((event.clientY - rect.top) / rect.height) * this.ledsPerStrip));
            requestAnimationInteraction(
                x, displayY, this.previewMode, this.previewAnimation,
                this.previewParams
            );
        }

        async initialize() {
            await this.syncLayoutFromStatus();
            this.setupCanvas();
            this.syncDisplayWidth();
            requestAnimationFrame(() => this.syncDisplayWidth());
            this.startRendering();
        }

        applyLedInfo(ledInfo) {
            if (!ledInfo || typeof ledInfo !== 'object') {
                return false;
            }

            const stripCount = Number(ledInfo.strip_count);
            const ledsPerStrip = Number(ledInfo.leds_per_strip);
            if (!Number.isFinite(stripCount) || !Number.isFinite(ledsPerStrip) ||
                stripCount <= 0 || ledsPerStrip <= 0) {
                return false;
            }

            const providedTotal = Number(ledInfo.total_leds);
            const totalLeds = Number.isFinite(providedTotal) && providedTotal > 0
                ? providedTotal
                : stripCount * ledsPerStrip;
            const changed = this.stripCount !== stripCount ||
                this.ledsPerStrip !== ledsPerStrip ||
                this.totalLeds !== totalLeds;
            if (!changed) {
                return false;
            }

            this.stripCount = stripCount;
            this.ledsPerStrip = ledsPerStrip;
            this.totalLeds = totalLeds;
            this.setupCanvas();
            return true;
        }

        async syncLayoutFromStatus() {
            try {
                const response = await fetch('/api/status');
                if (!response.ok) {
                    return;
                }
                const status = await response.json();
                this.applyLedInfo(status && status.led_info);
            } catch (error) {
                console.warn('Failed to load LED layout from /api/status', error);
            }
        }

        setupCanvas() {
            // Match the canvas dimensions to the exact grid footprint at a fixed 2x UI scale.
            const totalWidth = this.stripCount * (this.ledSize + this.stripSpacing);
            const totalHeight = this.ledsPerStrip * (this.ledSize + this.ledSpacing);
            this.scale = 2;

            this.actualLedSize = Math.max(1, this.ledSize * this.scale);
            this.actualLedSpacing = this.ledSpacing * this.scale;
            this.actualStripSpacing = this.stripSpacing * this.scale;

            const gridPixelWidth = this.stripCount * (this.actualLedSize + this.actualStripSpacing);
            const gridPixelHeight = this.ledsPerStrip * (this.actualLedSize + this.actualLedSpacing);
            this.canvas.width = Math.max(1, Math.ceil(gridPixelWidth));
            this.canvas.height = Math.max(1, Math.ceil(gridPixelHeight));
            this.syncDisplayWidth();
        }

        getTargetDisplayWidth() {
            const previewContainer = document.getElementById('rendererContainer');
            const previewWidth = previewContainer ? previewContainer.clientWidth : 0;
            const card = document.querySelector('#tab-animations .animation-card');
            const cardWidth = card ? card.getBoundingClientRect().width : 0;
            const controlButton = document.querySelector('#tab-controls .control-animation-btn');
            const controlButtonWidth = controlButton ? controlButton.getBoundingClientRect().width : 0;
            const targetWidth = cardWidth > 0 ? cardWidth : controlButtonWidth;

            if (targetWidth > 0 && previewWidth > 0) {
                return Math.min(targetWidth, previewWidth);
            }
            if (targetWidth > 0) {
                return targetWidth;
            }
            if (previewWidth > 0) {
                return previewWidth;
            }
            return 0;
        }

        syncDisplayWidth() {
            const displayWidth = this.getTargetDisplayWidth();
            if (displayWidth <= 0) {
                return;
            }
            this.canvas.style.width = `${Math.round(displayWidth)}px`;
            this.canvas.style.maxWidth = '100%';
            this.canvas.style.height = 'auto';
        }

        async fetchFrameData() {
            try {
                if (this.previewMode && this.previewAnimation) {
                    // In preview mode, fetch preview data for specific animation
                    const hasParams = this.previewParams && Object.keys(this.previewParams).length > 0;
                    const baseUrl = hasParams
                        ? `/api/preview/${this.previewAnimation}/with_params`
                        : `/api/preview/${this.previewAnimation}`;
                    const vibeQuery = globalVibeId
                        ? `?vibe=${encodeURIComponent(globalVibeId)}` : '';
                    const url = `${baseUrl}${vibeQuery}`;
                    const options = hasParams ? {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(this.previewParams)
                    } : undefined;
                    const response = await fetch(url, options);
                    const data = await response.json();
                    return data;
                } else {
                    // Normal mode - fetch current running animation
                    const response = await fetch('/api/frame');
                    const data = await response.json();
                    return data;
                }
            } catch (error) {
                console.error('Error fetching frame data:', error);
                return null;
            }
        }

        setPreviewAnimation(animationName, params = null) {
            this.previewAnimation = animationName;
            this.setPreviewParams(params);
            console.log(`Preview animation set to: ${animationName}`);
        }

        setPreviewParams(params = null) {
            if (params && typeof params === 'object') {
                this.previewParams = {...params};
            } else {
                this.previewParams = null;
            }
        }

        togglePreviewMode() {
            this.previewMode = !this.previewMode;
            console.log(`Preview mode: ${this.previewMode ? 'ON' : 'OFF'}`);

            // Update status display
            this.updateStatusDisplay();

            return this.previewMode;
        }

        renderFrame(frameData) {
            this.interactionTypes = Array.isArray(frameData?.interaction_types)
                ? frameData.interaction_types : [];
            this.updateInteractionAffordance();
            if (frameData && frameData.led_info) {
                this.applyLedInfo(frameData.led_info);
            }

            if (!frameData || !Array.isArray(frameData.frame_data)) {
                this.renderNoAnimation();
                return;
            }

            // Clear canvas
            this.ctx.fillStyle = '#000000';
            this.ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);

            // Render LEDs
            const colors = frameData.frame_data;
            for (let strip = 0; strip < this.stripCount; strip++) {
                for (let led = 0; led < this.ledsPerStrip; led++) {
                    const pixelIndex = strip * this.ledsPerStrip + led;
                    if (pixelIndex < colors.length) {
                        const [r, g, b] = colors[pixelIndex];
                        this.renderLED(strip, led, r, g, b);
                    }
                }
            }

            // Update frame counter
            this.frameCount++;
            this.updateFPS();
        }

        updateInteractionAffordance() {
            const enabled = this.interactionTypes.includes('primary');
            this.canvas.classList.toggle('interactive-preview-canvas', enabled);
            const hint = document.getElementById('previewInteractionHint');
            if (hint) {
                hint.hidden = !enabled;
                hint.textContent = this.previewMode ? 'Click to stir preview' : 'Click to interact';
            }
        }

        renderLED(strip, led, r, g, b) {
            const x = strip * (this.actualLedSize + this.actualStripSpacing);
            const y = (this.ledsPerStrip - 1 - led) * (this.actualLedSize + this.actualLedSpacing);

            this.ctx.fillStyle = `rgb(${r}, ${g}, ${b})`;
            this.ctx.fillRect(x, y, this.actualLedSize, this.actualLedSize);
        }

        renderNoAnimation() {
            // Clear canvas and show "no animation" state
            this.ctx.fillStyle = '#1a1a1a';
            this.ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);

            // Draw grid pattern to show LED layout
            this.ctx.strokeStyle = '#333333';
            this.ctx.lineWidth = 0.5;

            for (let strip = 0; strip < this.stripCount; strip++) {
                for (let led = 0; led < Math.min(this.ledsPerStrip, 100); led += 10) {
                    const x = strip * (this.actualLedSize + this.actualStripSpacing);
                    const y = (this.ledsPerStrip - 1 - led) * (this.actualLedSize + this.actualLedSpacing);
                    this.ctx.strokeRect(x, y, this.actualLedSize, this.actualLedSize);
                }
            }
        }

        updateFPS() {
            this.fpsCounter++;
            const now = Date.now();
            if (now - this.lastFpsTime >= 1000) {
                this.fps = this.fpsCounter;
                this.fpsCounter = 0;
                this.lastFpsTime = now;

                // Update UI
                document.getElementById('rendererFPS').textContent = `${this.fps} FPS`;
                document.getElementById('rendererFrameCount').textContent = `${this.frameCount} frames`;
            }
        }

        async renderLoop() {
            if (!this.isRunning) return;

            if (this.fetchInFlight) {
                this.scheduleNextFrame();
                return;
            }

            this.fetchInFlight = true;
            let frameData = null;
            try {
                frameData = await this.fetchFrameData();
            } catch (error) {
                console.error('Error in render loop:', error);
            }
            this.fetchInFlight = false;

            if (frameData) {
                this.lastFrameData = frameData;
                this.renderFrame(frameData);
            } else if (this.lastFrameData) {
                this.renderFrame(this.lastFrameData);
            } else {
                this.renderNoAnimation();
            }

            // Update status
            this.updateStatusDisplay(frameData || this.lastFrameData);

            // Schedule next frame
            this.scheduleNextFrame();
        }

        scheduleNextFrame() {
            if (!this.isRunning) return;
            if (IS_LOCAL_DASHBOARD) {
                this.renderTimer = requestAnimationFrame(() => this.renderLoop());
                return;
            }
            if (this.renderTimer) {
                clearTimeout(this.renderTimer);
            }
            this.renderTimer = setTimeout(() => this.renderLoop(), this.fetchIntervalMs);
        }

        updateStatusDisplay(frameData = null) {
            const statusEl = document.getElementById('rendererStatus');

            if (this.previewMode) {
                statusEl.textContent = `Preview: ${this.previewAnimation || 'None selected'}`;
                statusEl.className = 'text-primary'; // Blue for preview mode
            } else if (frameData && frameData.is_running) {
                statusEl.textContent = `Running: ${frameData.current_animation || 'Unknown'}`;
                statusEl.className = 'text-success'; // Green for running
            } else {
                statusEl.textContent = 'No animation';
                statusEl.className = 'text-muted'; // Gray for stopped
            }
        }

        startRendering() {
            if (this.isRunning) return;
            this.isRunning = true;
            this.scheduleNextFrame();
        }

        stopRendering() {
            this.isRunning = false;
            if (this.renderTimer) {
                if (IS_LOCAL_DASHBOARD) cancelAnimationFrame(this.renderTimer);
                else clearTimeout(this.renderTimer);
                this.renderTimer = null;
            }
            this.fetchInFlight = false;
        }

        reset() {
            this.frameCount = 0;
            this.fps = 0;
            this.fpsCounter = 0;
            this.lastFpsTime = Date.now();
            this.lastFrameData = null;
            document.getElementById('rendererFrameCount').textContent = '0 frames';
            document.getElementById('rendererFPS').textContent = '0 FPS';
        }
    }

    // Global renderer & stats polling
    let animationRenderer = null;
    let statsPollTimer = null;
    let latestStatusJson = '';
    let controlSelectedAnimation = null;
    let controlParameterUpdateTimeout = null;
    let controlParameterStore = {};
    let controlParameterSchema = {};
    let currentPresetSelection = null;
    function vibeIdFromStatus(status) {
        const vibe = status?.vibe?.state || status?.vibe || {};
        return vibe.id || vibe.vibe_id || 'neutral';
    }
    let globalVibeId = vibeIdFromStatus(INITIAL_STATUS);
    const PLANT_MODIFIERS = [
        ['Visual', 'illuminate', 'Illuminate'], ['Visual', 'shadow', 'Shadow'],
        ['Visual', 'refract', 'Refract'], ['Visual', 'hue_shift', 'Hue shift'],
        ['Visual', 'liquid_glass', 'Liquid glass'], ['Field', 'attractor', 'Attractor'],
        ['Field', 'repulsor', 'Repulsor'], ['Field', 'slow_zone', 'Slow zone'],
        ['Surface', 'obstacle', 'Obstacle'], ['Surface', 'portal', 'Portal'],
        ['Surface', 'bumper', 'Bumper'], ['Surface', 'hazard', 'Hazard / lava'],
        ['Surface', 'habitat', 'Habitat'], ['Lifecycle', 'emitter', 'Emitter'],
    ];
    let globalPlantModifiers = INITIAL_STATUS?.plant_modifiers || {version: 1, active: [], strengths: {}};
    let plantModifierSupport = new Set(INITIAL_STATUS?.animation_info?.plant_modifier_support || []);

    // Initialize renderer when page loads
    document.addEventListener('DOMContentLoaded', function() {
        animationRenderer = new LEDAnimationRenderer('ledCanvas');
        initializeGeneratedPreviews();
        const previewPresetName = document.getElementById('previewPresetName');
        previewPresetName?.addEventListener('keydown', event => {
            if (event.key === 'Enter') {
                event.preventDefault();
                savePreviewPreset();
            } else if (event.key === 'Escape') {
                closePreviewPresetComposer();
            }
        });
        if (INITIAL_STATUS) {
            syncControlPanel(INITIAL_STATUS);
            syncGlobalSpeedFromStatus(INITIAL_STATUS);
            syncGlobalVibeFromStatus(INITIAL_STATUS);
            syncPlantModifiersFromStatus(INITIAL_STATUS);
            syncReceiverHybridStatus(INITIAL_STATUS);
        }
        const sceneBackground = document.getElementById('sceneBackgroundSelect');
        sceneBackground?.addEventListener('change', syncSceneProviderControls);
        syncSceneProviderControls();
        startStatsPolling();
    });

    function initializeGeneratedPreviews() {
        const previews = document.querySelectorAll('img.generated-preview[data-loop-src]');
        if (!('IntersectionObserver' in window)) {
            previews.forEach(image => { image.src = image.dataset.loopSrc; });
            return;
        }
        const observer = new IntersectionObserver(entries => {
            entries.forEach(entry => {
                const image = entry.target;
                const desired = entry.isIntersecting
                    ? image.dataset.loopSrc
                    : image.dataset.posterSrc;
                if (desired && image.getAttribute('src') !== desired) image.src = desired;
            });
        }, {rootMargin: '160px 0px', threshold: 0.01});
        previews.forEach(image => observer.observe(image));
        refreshPendingGeneratedPreviews(observer);
    }

    async function refreshPendingGeneratedPreviews(observer) {
        const pending = Array.from(document.querySelectorAll(
            'img.preset-preview[data-preview-status="pending"]'
        ));
        if (!pending.length) return;
        const animationNames = [...new Set(pending.map(image => image.dataset.animation))];
        await Promise.all(animationNames.map(async animationName => {
            try {
                const response = await fetch(`/api/animations/${encodeURIComponent(animationName)}/presets`);
                if (!response.ok) return;
                const payload = await response.json();
                const byId = Object.fromEntries((payload.presets || []).map(preset => [preset.preset_id, preset]));
                pending.filter(image => image.dataset.animation === animationName).forEach(image => {
                    const preview = byId[image.dataset.presetId]?.preview;
                    if (!preview || preview.status !== 'ready') return;
                    image.dataset.posterSrc = preview.poster_url;
                    image.dataset.loopSrc = preview.loop_url;
                    image.dataset.previewStatus = 'ready';
                    image.src = preview.loop_url;
                    observer.observe(image);
                });
            } catch (error) {
                console.warn('Failed to refresh generated previews', error);
            }
        }));
        if (document.querySelector('img.preset-preview[data-preview-status="pending"]')) {
            window.setTimeout(() => refreshPendingGeneratedPreviews(observer), 5000);
        }
    }

    function formatSpeed(value) {
        const speed = Number(value);
        if (!Number.isFinite(speed)) return '';
        if (speed >= 100) return speed.toFixed(0);
        if (speed >= 10) return speed.toFixed(1).replace(/\.0$/, '');
        if (speed >= 1) return speed.toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
        return speed.toPrecision(2).replace(/0+$/, '').replace(/\.$/, '');
    }

    function previewGlobalSpeed(value) {
        const multiplier = Number(value);
        document.querySelectorAll('.global-speed-number').forEach(number => {
            if (document.activeElement !== number) number.value = formatSpeed(value);
        });
        if (Number.isFinite(multiplier) && multiplier > 0) {
            document.querySelectorAll('.global-speed-range').forEach(range => {
                if (document.activeElement !== range) {
                    range.value = multiplierToSpeedPosition(multiplier);
                }
            });
        }
        document.querySelectorAll('.speed-preset').forEach(button => {
            button.classList.remove('active');
            button.setAttribute('aria-pressed', 'false');
        });
    }

    function speedPositionToMultiplier(position) {
        return 10 ** ((Number(position) - 50) / 50);
    }

    function multiplierToSpeedPosition(multiplier) {
        return Math.max(0, Math.min(100, 50 + 50 * Math.log10(Number(multiplier))));
    }

    function previewGlobalSpeedFromPosition(position) {
        const multiplier = speedPositionToMultiplier(position);
        document.querySelectorAll('.global-speed-range').forEach(range => {
            if (document.activeElement !== range) range.value = position;
        });
        document.querySelectorAll('.global-speed-number').forEach(number => {
            if (document.activeElement !== number) number.value = formatSpeed(multiplier);
        });
        document.querySelectorAll('.speed-preset').forEach(button => {
            button.classList.remove('active');
            button.setAttribute('aria-pressed', 'false');
        });
    }

    function setGlobalSpeedFromPosition(position) {
        setGlobalSpeed(speedPositionToMultiplier(position));
    }

    function syncGlobalSpeedFromStatus(status) {
        const ranges = document.querySelectorAll('.global-speed-range');
        if (!ranges.length || !status) return;
        const scale = Number(status.animation_speed_scale);
        if (!Number.isFinite(scale) || scale <= 0 || !Number.isFinite(SPEED_BASELINE)) return;
        const multiplier = scale / SPEED_BASELINE;
        ranges.forEach(range => {
            if (document.activeElement !== range) range.value = multiplierToSpeedPosition(multiplier);
        });
        document.querySelectorAll('.global-speed-number').forEach(number => {
            if (document.activeElement !== number) number.value = formatSpeed(multiplier);
        });
        document.querySelectorAll('.speed-preset').forEach(button => {
            const match = (button.getAttribute('onclick') || '').match(/[\d.]+/);
            const buttonValue = match ? Number(match[0]) : NaN;
            const active = Math.abs(buttonValue - multiplier) < .05;
            button.classList.toggle('active', active);
            button.setAttribute('aria-pressed', String(active));
        });
    }

    async function setGlobalSpeed(value) {
        const multiplier = Number(value);
        if (!Number.isFinite(multiplier) || multiplier <= 0) {
            showToast('Tempo must be a positive number.', 'info');
            return;
        }
        previewGlobalSpeed(multiplier);
        try {
            const response = await fetch('/api/config/animation-speed', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({multiplier})
            });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.error || 'Unable to change speed');
            showToast(`Global tempo set to ${formatSpeed(multiplier)}×`, 'success');
        } catch (error) {
            showToast(error.message, 'error');
        }
    }

    function setGlobalSpeedPreset(value) {
        setGlobalSpeed(value);
    }

    function syncGlobalVibeFromStatus(status) {
        if (!status?.vibe) return;
        globalVibeId = vibeIdFromStatus(status);
        const select = document.getElementById('globalVibeSelect');
        if (select && document.activeElement !== select) select.value = globalVibeId;
        const diagnostic = status.vibe.diagnostic;
        const message = document.getElementById('vibeDiagnostic');
        if (message) {
            message.hidden = !diagnostic;
            message.textContent = diagnostic ? String(
                diagnostic.message || diagnostic.reason || diagnostic.code
                || (typeof diagnostic === 'object' ? JSON.stringify(diagnostic) : diagnostic)
            ) : '';
        }
    }

    async function setGlobalVibe(vibeId) {
        const previous = globalVibeId;
        globalVibeId = vibeId;
        try {
            const response = await fetch('/api/config/vibe', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({vibe: vibeId})
            });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.error || 'Unable to change vibe');
            const requested = payload.requested_vibe || {};
            globalVibeId = requested.id || requested.vibe_id || vibeId;
            if (animationRenderer?.previewMode && animationRenderer.previewAnimation) {
                animationRenderer.lastFrameData = null;
            }
            showToast(`${humanizeParamName(globalVibeId)} vibe selected`, 'success');
        } catch (error) {
            globalVibeId = previous;
            const select = document.getElementById('globalVibeSelect');
            if (select) select.value = previous;
            showToast(error.message, 'error');
        }
    }

    function syncPlantModifiersFromStatus(status) {
        if (!status?.plant_modifiers) return;
        globalPlantModifiers = status.plant_modifiers;
        plantModifierSupport = new Set(status.animation_info?.plant_modifier_support || []);
        renderPlantModifierControls();
    }

    function renderPlantModifierControls() {
        const host = document.getElementById('plantModifierControls');
        if (!host) return;
        host.innerHTML = '';
        let lastGroup = '';
        PLANT_MODIFIERS.forEach(([group, id, label]) => {
            if (group !== lastGroup) {
                const heading = document.createElement('div');
                heading.className = 'small fw-bold mt-1';
                heading.textContent = group;
                host.appendChild(heading);
                lastGroup = group;
            }
            const active = globalPlantModifiers.active.includes(id);
            const supported = plantModifierSupport.has(id);
            const row = document.createElement('div');
            row.className = `d-flex align-items-center gap-2 ${supported ? '' : 'opacity-50'}`;
            row.innerHTML = `<input type="checkbox" id="plantModifier-${id}" ${active ? 'checked' : ''} ${supported ? '' : 'disabled'} aria-label="${label}">`
                + `<label class="small flex-grow-1" for="plantModifier-${id}">${label}</label>`
                + `<input type="range" min="0" max="1" step="0.05" value="${globalPlantModifiers.strengths[id] ?? (id === 'obstacle' ? 1 : .5)}" ${active && supported ? '' : 'disabled'} aria-label="${label} strength">`;
            const [toggle, slider] = row.querySelectorAll('input');
            toggle.addEventListener('change', () => changePlantModifier(id, toggle.checked));
            slider.addEventListener('change', () => changePlantStrength(id, Number(slider.value)));
            host.appendChild(row);
        });
        const unsupported = globalPlantModifiers.active.filter(id => !plantModifierSupport.has(id));
        const message = document.getElementById('plantModifierUnsupported');
        if (message) message.textContent = unsupported.length ? `Unsupported here: ${unsupported.join(', ')}` : '';
    }

    function changePlantModifier(id, enabled) {
        const field = new Set(['attractor', 'repulsor', 'slow_zone']);
        const surface = new Set(['obstacle', 'portal', 'bumper', 'hazard', 'habitat']);
        let active = globalPlantModifiers.active.filter(item => item !== id);
        if (enabled) {
            if (field.has(id)) active = active.filter(item => !field.has(item));
            if (surface.has(id)) active = active.filter(item => !surface.has(item));
            active.push(id);
        }
        globalPlantModifiers = {...globalPlantModifiers, active};
        sendPlantModifiers();
    }

    function changePlantStrength(id, strength) {
        globalPlantModifiers = {...globalPlantModifiers,
            strengths: {...globalPlantModifiers.strengths, [id]: strength}};
        sendPlantModifiers();
    }

    async function sendPlantModifiers() {
        renderPlantModifierControls();
        try {
            const response = await fetch('/api/config/plant-modifiers', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({plant_modifiers: globalPlantModifiers})
            });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.error || 'Unable to change plant modifiers');
            globalPlantModifiers = payload.plant_modifiers;
            renderPlantModifierControls();
            showToast('Plant modifiers updated', 'success');
        } catch (error) {
            showToast(error.message, 'error');
        }
    }

    async function playDashboardPreset(animationName, presetId, button) {
        if (button) button.disabled = true;
        try {
            const response = await fetch(
                `/api/animations/${encodeURIComponent(animationName)}/presets/${encodeURIComponent(presetId)}/apply`,
                {method: 'POST'}
            );
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.error || 'Unable to load preset');
            controlSelectedAnimation = animationName;
            controlParameterStore[animationName] = {...(payload.preset.params || {})};
            highlightControlSelection(animationName);
            setCurrentPresetSelection(animationName, payload.preset);
            loadControlParameters(animationName, {
                showPlaceholder: false,
                currentParams: payload.preset.params || {}
            });
            showToast(`Playing ${payload.preset.name}`, 'success');
        } catch (error) {
            showToast(error.message, 'error');
        } finally {
            if (button) button.disabled = false;
        }
    }

    function openAnimationControls(animationName) {
        selectControlAnimation(animationName);
        const collapseElement = document.getElementById('controlsCollapse');
        if (collapseElement) {
            bootstrap.Collapse.getOrCreateInstance(collapseElement, {toggle: false}).show();
            setTimeout(() => collapseElement.scrollIntoView({behavior: 'smooth', block: 'start'}), 150);
        }
    }

    // Renderer control functions
    function toggleRenderer() {
        const container = document.getElementById('rendererContainer');
        const button = document.getElementById('toggleRenderer');

        if (container.style.display === 'none') {
            container.style.display = 'block';
            button.innerHTML = '<i class="fas fa-eye"></i> Hide';
            if (animationRenderer) {
                animationRenderer.syncDisplayWidth();
                animationRenderer.startRendering();
            }
        } else {
            container.style.display = 'none';
            button.innerHTML = '<i class="fas fa-eye-slash"></i> Show';
            if (animationRenderer) {
                animationRenderer.stopRendering();
            }
        }
    }

    function resetRenderer() {
        if (animationRenderer) {
            animationRenderer.reset();
        }
    }

    function togglePreviewMode() {
        if (animationRenderer) {
            const isPreviewMode = animationRenderer.togglePreviewMode();
            const button = document.getElementById('previewToggle');
            const buttonText = document.getElementById('previewToggleText');

            if (isPreviewMode) {
                button.className = 'btn btn-primary btn-sm';
                buttonText.textContent = 'Live Mode';
                if (controlSelectedAnimation) {
                    syncPreviewParameters(controlSelectedAnimation);
                }
            } else {
                button.className = 'btn btn-outline-primary btn-sm';
                buttonText.textContent = 'Preview Mode';
            }
        }
    }

    function previewAnimation(animationName) {
        if (animationRenderer) {
            if (IS_LOCAL_DASHBOARD) {
                if (animationRenderer.previewMode) togglePreviewMode();
                const params = controlParameterStore[animationName] || {};
                startAnimation(animationName, params);
                return;
            }
            // Enable preview mode if not already enabled
            if (!animationRenderer.previewMode) {
                togglePreviewMode();
            }

            // Set the animation to preview
            const params = controlParameterStore[animationName] || null;
            animationRenderer.setPreviewAnimation(animationName, params);
        }
    }

    // Handle animation card clicks with feedback
    function startAnimation(name, config = {}) {
        // Show loading state
        const cards = document.querySelectorAll('.animation-card');
        cards.forEach(card => card.style.opacity = '0.6');

        return fetch(`/api/start/${name}`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(config)
        })
        .then(r => r.json())
        .then(result => {
            if (result.success) {
                showToast(`Started animation: ${name}`, 'success');
            } else {
                showToast(`Failed to start animation: ${name}`, 'error');
            }
            return result;
        })
        .catch(error => {
            showToast(`Error: ${error.message}`, 'error');
            return {success: false, error: error.message};
        })
        .finally(() => {
            cards.forEach(card => card.style.opacity = '1');
        });
    }

    function requestRandomHole() {
        fetch('/api/hole', {method: 'POST'})
            .then(r => r.json())
            .then(result => {
                if (result.success) {
                    if (typeof showToast === 'function') {
                        showToast('Punched a random hole', 'success');
                    }
                }
            })
            .catch(error => console.error('Failed to punch hole', error));
    }

    function requestAnimationInteraction(x, y, previewMode, previewAnimation, previewParams) {
        const isPreview = Boolean(previewMode && previewAnimation);
        const url = isPreview
            ? `/api/preview/${encodeURIComponent(previewAnimation)}/interaction`
            : '/api/interaction';
        const body = {kind: 'primary', x, y, strength: 1};
        if (isPreview && previewParams) body.params = previewParams;
        fetch(url, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body)
        })
            .then(r => r.json())
            .then(result => {
                if (result.success && typeof showToast === 'function') {
                    showToast(isPreview ? 'Stirred preview' : 'Interaction sent', 'success');
                }
            })
            .catch(error => console.error('Failed to send animation interaction', error));
    }

    function startStatsPolling() {
        fetchSceneStats();
        if (statsPollTimer) {
            clearInterval(statsPollTimer);
        }
        statsPollTimer = setInterval(fetchSceneStats, 2000);
    }

    async function fetchSceneStats() {
        try {
            const response = await fetch('/api/status');
            if (!response.ok) return;
            const data = await response.json();
            latestStatusJson = JSON.stringify(data, null, 2);
            updateSceneStatsPanel(data);
            updateStatusJson(data);
            syncControlPanel(data);
            syncGlobalSpeedFromStatus(data);
            syncGlobalVibeFromStatus(data);
            syncPlantModifiersFromStatus(data);
            syncReceiverHybridStatus(data);
        } catch (err) {
            console.error('Failed to fetch stats', err);
        }
    }

    function updateSceneStatsPanel(payload) {
        if (!payload) return;
        const stats = payload.animation_stats || payload.stats || {};
        safeSetText('statFill', stats.fill_ratio != null ? formatPercent(stats.fill_ratio) : '--');
        const expected = stats.expected_ratio != null ? formatPercent(stats.expected_ratio) : '--';
        safeSetText('statExpected', expected);
        safeSetText('statHole', stats.hole_active ? 'Yes' : 'No');
        const bubbleRise = stats.max_bubble_rise ? `${(stats.max_bubble_rise || 0).toFixed(1)}px` : '0px';
        const bubbleLabel = `${stats.bubble_count || 0} (${bubbleRise})`;
        safeSetText('statBubbles', bubbleLabel);
        safeSetText('statSpray', stats.spray_particle_count != null ? stats.spray_particle_count : '--');
        safeSetText('statSpawnAllowed', stats.spawn_allowed === false ? 'Paused' : 'Yes');
        const hashValue = payload.animation_hash || '';
        let shortHash = '--';
        if (hashValue) {
            shortHash = hashValue.length > 15 ? `${hashValue.slice(0, 15)}…` : hashValue;
        }
        safeSetText('statHash', shortHash);
        const hashEl = document.getElementById('statHash');
        if (hashEl) {
            hashEl.title = hashValue || 'No hash available';
        }
    }

    function updateStatusJson(payload) {
        const el = document.getElementById('statusJson');
        if (el) {
            el.textContent = latestStatusJson || JSON.stringify(payload, null, 2);
        }
    }

    function copyStatusJson() {
        if (!latestStatusJson) {
            showToast('No status data yet', 'info');
            return;
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(latestStatusJson)
                .then(() => showToast('Status JSON copied', 'success'))
                .catch(err => {
                    console.error('Clipboard copy failed', err);
                    fallbackCopy(latestStatusJson);
                });
        } else {
            fallbackCopy(latestStatusJson);
        }
    }

    function fallbackCopy(text) {
        const textarea = document.createElement('textarea');
        textarea.value = text;
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        document.body.removeChild(textarea);
        showToast('Status JSON copied', 'success');
    }

    function safeSetText(id, value) {
        const el = document.getElementById(id);
        if (el) {
            el.textContent = value;
        }
    }

    function formatPercent(value) {
        return `${Math.round(value * 100)}%`;
    }

    function humanizeParamName(name) {
        return name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    }

    function showControlPlaceholder(message, options = {}) {
        const placeholder = document.getElementById('controlParametersPlaceholder');
        const card = document.getElementById('controlParametersCard');
        if (options.clearControls) {
            const container = document.getElementById('controlParametersContainer');
            if (container) {
                container.innerHTML = '';
            }
        }
        if (placeholder) {
            placeholder.textContent = message;
            placeholder.style.display = 'block';
        }
        if (card) {
            card.style.display = 'none';
        }
    }

    function hideControlPlaceholder() {
        const placeholder = document.getElementById('controlParametersPlaceholder');
        const card = document.getElementById('controlParametersCard');
        if (placeholder) {
            placeholder.style.display = 'none';
        }
        if (card) {
            card.style.display = 'block';
        }
    }

    function syncControlPanel(status) {
        if (!status) return;
        const runningAnimation = status.is_running ? status.current_animation : null;
        if (!runningAnimation) {
            if (controlSelectedAnimation !== null) {
                controlSelectedAnimation = null;
                highlightControlSelection(null);
            }
            setCurrentPresetSelection(null, null);
            showControlPlaceholder('Start or select an animation to adjust its live controls.', {clearControls: true});
            return;
        }
        setCurrentPresetSelection(runningAnimation, status.current_preset || null);
        if (controlSelectedAnimation === runningAnimation) {
            return;
        }
        controlSelectedAnimation = runningAnimation;
        const liveParams = status.animation_info?.current_params;
        if (liveParams && typeof liveParams === 'object' && !Array.isArray(liveParams)) {
            controlParameterStore[runningAnimation] = {...liveParams};
        }
        highlightControlSelection(runningAnimation);
        loadControlParameters(runningAnimation, {
            placeholderMessage: `Loading controls for ${humanizeParamName(runningAnimation)}...`,
            currentParams: controlParameterStore[runningAnimation]
        });
    }

    function highlightControlSelection(name) {
        document.querySelectorAll('.control-animation-btn').forEach(btn => {
            const isActive = Boolean(name && btn.dataset.animation === name);
            btn.classList.toggle('btn-primary', isActive);
            btn.classList.toggle('btn-outline-primary', !isActive);
        });
        document.querySelectorAll('[data-animation-card]').forEach(card => {
            card.classList.toggle('active', Boolean(name && card.dataset.animationCard === name));
        });
    }

    function syncPreviewParameters(animationName) {
        if (!animationRenderer || !animationRenderer.previewMode || !animationName) {
            return;
        }
        const params = controlParameterStore[animationName] || null;
        if (animationRenderer.previewAnimation !== animationName) {
            animationRenderer.setPreviewAnimation(animationName, params);
        } else {
            animationRenderer.setPreviewParams(params);
        }
    }

    function selectControlAnimation(name, options = {}) {
        controlSelectedAnimation = name;
        delete controlParameterStore[name];
        setCurrentPresetSelection(name, null);
        highlightControlSelection(name);
        if (animationRenderer && animationRenderer.previewMode) {
            const params = controlParameterStore[name] || null;
            animationRenderer.setPreviewAnimation(name, params);
        }
        const { skipStart = false, placeholderMessage = null } = options;
        const message = placeholderMessage || (skipStart
            ? 'Loading controls...'
            : 'Starting animation and loading controls...');
        showControlPlaceholder(message, {clearControls: true});
        const startPromise = skipStart ? Promise.resolve({success: true}) : startAnimation(name);
        startPromise.then(result => {
            if (result.success) {
                loadControlParameters(name, {showPlaceholder: false});
            } else {
                controlSelectedAnimation = null;
                highlightControlSelection(null);
                showControlPlaceholder('Unable to load controls. Start the animation to try again.', {clearControls: true});
            }
        }).catch(error => {
            console.error('Failed to select animation', error);
            controlSelectedAnimation = null;
            highlightControlSelection(null);
            showControlPlaceholder('Unable to load controls right now.', {clearControls: true});
        });
    }

    function loadControlParameters(name, options = {}) {
        const { showPlaceholder = true, placeholderMessage = null, currentParams = null } = options;
        if (showPlaceholder) {
            showControlPlaceholder(placeholderMessage || 'Loading controls...', {clearControls: true});
        }
        fetch(`/api/animations/${name}`)
            .then(r => r.json())
            .then(info => {
                if (info && info.parameters && Object.keys(info.parameters).length) {
                    const allParams = currentParams || info.current_params || {};
                    controlParameterStore[name] = {...allParams};
                    const globalMappings = new Set(Object.keys(
                        info.vibe?.legacy_parameter_mappings || {}
                    ));
                    globalMappings.add('vibe');
                    globalMappings.add('vibe_id');
                    const visibleSchema = Object.fromEntries(
                        Object.entries(info.parameters).filter(([key]) => !globalMappings.has(key))
                    );
                    renderControlParameterControls(visibleSchema, allParams);
                    hideControlPlaceholder();
                    loadControlPresets(name);
                } else {
                    controlParameterSchema = info?.parameters || {};
                    controlParameterStore[name] = currentParams || info?.current_params || {};
                    updatePreviewPresetAction();
                    showControlPlaceholder('This animation does not expose live controls.', {clearControls: true});
                }
            })
            .catch(error => {
                console.error('Failed to load controls', error);
                showControlPlaceholder('Failed to load controls. Please try again.', {clearControls: true});
            });
    }

    const PARAMETER_OPTIONS = {
        axis: ['horizontal', 'vertical', 'diagonal'],
        emoji: ['smile', 'heart'],
        fit_mode: ['stretch', 'contain', 'cover'],
        brightness_mode: ['rgb', 'luma'],
        destruct_on_loop_action: ['reseed', 'restart', 'glider_storm'],
        'living_ecosystem:palette': ['natural', 'golden_hour', 'autumn', 'moonlit', 'boreal', 'bioluminescent', 'ultraviolet', 'ember']
    };

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, character => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
        })[character]);
    }

    function parameterOptions(name, info) {
        if (Array.isArray(info.options) && info.options.length) return info.options;
        return PARAMETER_OPTIONS[`${controlSelectedAnimation}:${name}`] || PARAMETER_OPTIONS[name] || null;
    }

    function numericStep(info) {
        if (info.type === 'int') return 1;
        const span = Number(info.max) - Number(info.min);
        if (span <= 1) return .01;
        if (span <= 10) return .05;
        return .1;
    }

    function parameterPresets(info) {
        if (!info || !info.presets || Array.isArray(info.presets) || typeof info.presets !== 'object') {
            return [];
        }
        return Object.entries(info.presets).filter(([, value]) => {
            if (info.type === 'int') return Number.isInteger(value);
            return info.type === 'float' && Number.isFinite(value);
        });
    }

    function matchingParameterPreset(value, info) {
        const numericValue = Number(value);
        const match = parameterPresets(info).find(([, presetValue]) => (
            Math.abs(Number(presetValue) - numericValue) < 1e-9
        ));
        return match ? match[0] : '';
    }

    function usesLogEasing(info) {
        const minimum = Number(info.min);
        const maximum = Number(info.max);
        return minimum > 0 && maximum / minimum >= 20;
    }

    function parameterToSlider(value, info) {
        const minimum = Number(info.min);
        const maximum = Number(info.max);
        const numeric = Number(value);
        if (usesLogEasing(info)) {
            return 100 * Math.log(numeric / minimum) / Math.log(maximum / minimum);
        }
        return 100 * (numeric - minimum) / (maximum - minimum || 1);
    }

    function sliderToParameter(position, info) {
        const minimum = Number(info.min);
        const maximum = Number(info.max);
        const ratio = Number(position) / 100;
        const value = usesLogEasing(info)
            ? minimum * ((maximum / minimum) ** ratio)
            : minimum + (maximum - minimum) * ratio;
        return info.type === 'int' ? Math.round(value) : Number(value.toFixed(3));
    }

    function handleParameterRangeInput(name, position, type, mirrorInputId) {
        const value = sliderToParameter(position, controlParameterSchema[name]);
        const mirror = document.getElementById(mirrorInputId);
        if (mirror) mirror.value = value;
        syncParameterPreset(name, value);
        updateControlParameter(name, value, type);
    }

    function handleNumberInput(name, value, type, mirrorSliderId) {
        if (value === '' || value === null) return;
        const converted = type === 'int' ? parseInt(value, 10) : parseFloat(value);
        if (!Number.isFinite(converted)) return;
        const slider = document.getElementById(mirrorSliderId);
        if (slider && controlParameterSchema[name]) {
            slider.value = Math.max(0, Math.min(100, parameterToSlider(converted, controlParameterSchema[name])));
        }
        syncParameterPreset(name, converted);
        updateControlParameter(name, converted, type);
    }

    function syncParameterPreset(name, value) {
        const select = document.getElementById(`control-${name}-preset`);
        if (select && controlParameterSchema[name]) {
            select.value = matchingParameterPreset(value, controlParameterSchema[name]);
        }
    }

    function applyParameterPreset(name, presetName, type, sliderId, numberInputId) {
        const info = controlParameterSchema[name];
        if (!info || !Object.prototype.hasOwnProperty.call(info.presets || {}, presetName)) return;
        const value = info.presets[presetName];
        const slider = document.getElementById(sliderId);
        const numberInput = document.getElementById(numberInputId);
        if (slider) slider.value = Math.max(0, Math.min(100, parameterToSlider(value, info)));
        if (numberInput) numberInput.value = value;
        updateControlParameter(name, value, type);
    }

    function rgbToHex(red, green, blue) {
        return '#' + [red, green, blue].map(value => Math.max(0, Math.min(255, Number(value) || 0)).toString(16).padStart(2, '0')).join('').toUpperCase();
    }

    function hexToRgb(hex) {
        const value = String(hex).replace('#', '');
        return {red: parseInt(value.slice(0, 2), 16), green: parseInt(value.slice(2, 4), 16), blue: parseInt(value.slice(4, 6), 16)};
    }

    function handleColorInput(prefix, hex) {
        const rgb = hexToRgb(hex);
        const params = {};
        ['red', 'green', 'blue'].forEach(channel => {
            const name = `${prefix}${channel}`;
            params[name] = rgb[channel];
            const input = document.getElementById(`control-${name}-value`);
            if (input) input.value = rgb[channel];
        });
        const label = document.getElementById(`control-${prefix}hex`);
        if (label) label.textContent = hex.toUpperCase();
        updateControlParametersBatch(params);
    }

    function handleColorChannelInput(prefix, channel, value) {
        const numeric = Math.max(0, Math.min(255, parseInt(value, 10) || 0));
        updateControlParameter(`${prefix}${channel}`, numeric, 'int');
        const values = ['red', 'green', 'blue'].map(name => {
            const input = document.getElementById(`control-${prefix}${name}-value`);
            return input ? input.value : 0;
        });
        const hex = rgbToHex(...values);
        const picker = document.getElementById(`control-${prefix}color`);
        const label = document.getElementById(`control-${prefix}hex`);
        if (picker) picker.value = hex;
        if (label) label.textContent = hex;
    }

    function renderColorControl(prefix, values) {
        const wrapper = document.createElement('div');
        wrapper.className = 'parameter-control parameter-control-wide';
        const label = prefix ? humanizeParamName(prefix.replace(/_$/, '')) : 'Color';
        const hex = rgbToHex(values.red, values.green, values.blue);
        wrapper.innerHTML = `
            <div class="d-flex align-items-center justify-content-between gap-3 mb-2">
                <div><div class="parameter-label">${escapeHtml(label)}</div><div class="parameter-description">Choose visually or tune exact RGB channels.</div></div>
                <div class="d-flex align-items-center gap-2"><span class="color-hex" id="control-${prefix}hex">${hex}</span><input class="color-well" id="control-${prefix}color" type="color" value="${hex}" aria-label="${escapeHtml(label)} color" oninput="handleColorInput('${prefix}', this.value)"></div>
            </div>
            <div class="row g-2">
                ${['red', 'green', 'blue'].map(channel => `<div class="col-4"><label class="small text-muted text-uppercase">${channel[0]}</label><input class="form-control channel-input" id="control-${prefix}${channel}-value" type="number" min="0" max="255" step="1" value="${values[channel]}" oninput="handleColorChannelInput('${prefix}', '${channel}', this.value)"></div>`).join('')}
            </div>`;
        return wrapper;
    }

    function resetControlParameter(name) {
        const info = controlParameterSchema[name];
        if (!info) return;
        updateControlParameter(name, info.default, info.type);
        renderControlParameterControls(controlParameterSchema, controlParameterStore[controlSelectedAnimation] || {});
    }

    function renderControlParameterControls(schema, currentParams) {
        const container = document.getElementById('controlParametersContainer');
        if (!container) return;
        container.innerHTML = '';
        controlParameterSchema = schema;
        const parameterSnapshot = {
            ...(controlSelectedAnimation ? controlParameterStore[controlSelectedAnimation] : {}),
            ...(currentParams || {})
        };
        Object.entries(schema).forEach(([name, info]) => {
            parameterSnapshot[name] = currentParams[name] ?? info.default;
        });

        const title = document.getElementById('controlStudioTitle');
        if (title) title.textContent = `Shape ${humanizeParamName(controlSelectedAnimation || 'the scene')}`;

        const colorNames = new Set();
        Object.keys(schema).filter(name => name.endsWith('red')).forEach(redName => {
            const prefix = redName.slice(0, -3);
            const greenName = `${prefix}green`;
            const blueName = `${prefix}blue`;
            if (!schema[greenName] || !schema[blueName]) return;
            colorNames.add(redName); colorNames.add(greenName); colorNames.add(blueName);
            container.appendChild(renderColorControl(prefix, {
                red: parameterSnapshot[redName], green: parameterSnapshot[greenName], blue: parameterSnapshot[blueName]
            }));
        });

        Object.entries(schema).forEach(([paramName, paramInfo]) => {
            if (paramName === 'speed' || paramName === 'plant_aware' || paramName === 'plant_modifiers' || colorNames.has(paramName)) return;
            const currentValue = parameterSnapshot[paramName];
            const prettyName = humanizeParamName(paramName);
            const controlDiv = document.createElement('div');
            controlDiv.className = 'parameter-control';
            const inputId = `control-${paramName}`;
            const numberInputId = `${inputId}-value`;
            const options = parameterOptions(paramName, paramInfo);
            let inputHtml = '';
            if (options) {
                inputHtml = `<select class="form-select" id="${inputId}" onchange="updateControlParameter('${paramName}', this.value, 'str')">${options.map(option => `<option value="${escapeHtml(option)}"${String(option) === String(currentValue) ? ' selected' : ''}>${humanizeParamName(String(option))}</option>`).join('')}</select>`;
            } else if (paramInfo.type === 'float' || paramInfo.type === 'int') {
                const hasRange = Number.isFinite(Number(paramInfo.min)) && Number.isFinite(Number(paramInfo.max));
                const presets = parameterPresets(paramInfo);
                const presetSelect = presets.length ? `<select class="form-select form-select-sm parameter-preset" id="${inputId}-preset" aria-label="${escapeHtml(prettyName)} preset" onchange="applyParameterPreset('${paramName}', this.value, '${paramInfo.type}', '${inputId}', '${numberInputId}')"><option value="">Custom</option>${presets.map(([name, value]) => `<option value="${escapeHtml(name)}"${name === matchingParameterPreset(currentValue, paramInfo) ? ' selected' : ''}>${escapeHtml(humanizeParamName(name))} (${escapeHtml(value)})</option>`).join('')}</select>` : '';
                inputHtml = `${presetSelect}${hasRange ? `<input type="range" class="form-range" id="${inputId}" min="0" max="100" step="1" value="${parameterToSlider(currentValue, paramInfo)}" oninput="handleParameterRangeInput('${paramName}', this.value, '${paramInfo.type}', '${numberInputId}')">` : ''}<div class="input-group input-group-sm"><input type="number" class="form-control parameter-value" value="${escapeHtml(currentValue)}" step="${numericStep(paramInfo)}" id="${numberInputId}" oninput="handleNumberInput('${paramName}', this.value, '${paramInfo.type}', '${inputId}')"><span class="input-group-text">${paramInfo.type === 'int' ? 'whole' : 'exact'}</span></div>`;
            } else if (paramInfo.type === 'bool') {
                inputHtml = `<div class="form-check form-switch pt-1"><input class="form-check-input" type="checkbox" role="switch" id="${inputId}" ${currentValue ? 'checked' : ''} onchange="updateControlParameter('${paramName}', this.checked, 'bool')"><label class="form-check-label fw-semibold" for="${inputId}">${currentValue ? 'On' : 'Off'}</label></div>`;
            } else {
                inputHtml = `<input type="text" class="form-control" id="${inputId}" value="${escapeHtml(currentValue)}" onchange="updateControlParameter('${paramName}', this.value, 'str')">`;
            }
            controlDiv.innerHTML = `<div class="d-flex justify-content-between gap-2"><div><div class="parameter-label">${escapeHtml(prettyName)}</div><div class="parameter-description">${escapeHtml(paramInfo.description || '')}</div></div><button class="btn btn-link btn-sm text-muted p-0 align-self-start" type="button" onclick="resetControlParameter('${paramName}')" title="Reset to ${escapeHtml(paramInfo.default)}"><i class="fas fa-rotate-left"></i></button></div>${inputHtml}`;
            container.appendChild(controlDiv);
        });

        if (controlSelectedAnimation) {
            controlParameterStore[controlSelectedAnimation] = parameterSnapshot;
            syncPreviewParameters(controlSelectedAnimation);
            updatePreviewPresetAction();
        }
    }

    async function loadControlPresets(name = controlSelectedAnimation) {
        const select = document.getElementById('controlPresetSelect');
        if (!select || !name) return;
        try {
            const response = await fetch(`/api/animations/${encodeURIComponent(name)}/presets`);
            const payload = await response.json();
            const presets = Array.isArray(payload.presets) ? payload.presets : [];
            select.innerHTML = '';
            const placeholder = document.createElement('option');
            placeholder.value = '';
            placeholder.textContent = presets.length ? 'Choose a preset…' : 'No saved presets';
            select.appendChild(placeholder);
            presets.forEach(preset => {
                const option = document.createElement('option');
                option.value = preset.preset_id;
                option.textContent = preset.name;
                select.appendChild(option);
            });
        } catch (error) {
            console.error('Failed to load presets', error);
        }
    }

    function setCurrentPresetSelection(animationName, preset) {
        if (!animationName) {
            currentPresetSelection = null;
        } else if (preset && preset.animation === animationName) {
            currentPresetSelection = {...preset, animation: animationName};
        } else {
            currentPresetSelection = {animation: animationName, is_dirty: true};
        }
        updatePreviewPresetAction();
    }

    function updatePreviewPresetAction() {
        const current = document.getElementById('previewPresetCurrent');
        const currentName = document.getElementById('previewPresetCurrentName');
        const saveButton = document.getElementById('previewSavePresetButton');
        const context = document.getElementById('previewPresetContext');
        if (!current || !currentName || !saveButton || !context) return;

        const animationName = controlSelectedAnimation;
        const isCurrentPreset = Boolean(
            animationName && currentPresetSelection &&
            currentPresetSelection.animation === animationName &&
            currentPresetSelection.preset_id && !currentPresetSelection.is_dirty
        );
        current.hidden = !isCurrentPreset;
        saveButton.hidden = isCurrentPreset;
        currentName.textContent = isCurrentPreset ? currentPresetSelection.name : '';
        saveButton.disabled = !animationName || !Object.prototype.hasOwnProperty.call(controlParameterStore, animationName);

        if (!animationName) {
            context.textContent = 'Play an animation to save its current look.';
        } else if (isCurrentPreset) {
            context.textContent = humanizeParamName(animationName);
            closePreviewPresetComposer();
        } else if (currentPresetSelection?.name) {
            context.textContent = `Modified from ${currentPresetSelection.name} · ${humanizeParamName(animationName)}`;
        } else {
            context.textContent = `Save the current ${humanizeParamName(animationName)} look.`;
        }
    }

    function openPreviewPresetComposer() {
        const composer = document.getElementById('previewPresetComposer');
        const input = document.getElementById('previewPresetName');
        if (!composer || !input || !controlSelectedAnimation) return;
        composer.hidden = false;
        input.value = '';
        input.focus();
    }

    function closePreviewPresetComposer() {
        const composer = document.getElementById('previewPresetComposer');
        const input = document.getElementById('previewPresetName');
        if (composer) composer.hidden = true;
        if (input) input.value = '';
    }

    async function saveAnimationPreset(name, category = 'Personal', description = '') {
        const animationName = controlSelectedAnimation;
        if (!animationName || !name) {
            showToast('Select an animation and enter a preset name.', 'info');
            return null;
        }
        const params = controlParameterStore[animationName];
        if (!params || typeof params !== 'object') {
            showToast('The current animation settings are still loading.', 'info');
            return null;
        }
        const response = await fetch(`/api/animations/${encodeURIComponent(animationName)}/presets`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name, category, description, params})
        });
        const payload = await response.json();
        if (!response.ok) {
            showToast(payload.error || 'Failed to save preset.', 'error');
            return null;
        }
        setCurrentPresetSelection(animationName, payload.preset);
        await loadControlPresets(animationName);
        const select = document.getElementById('controlPresetSelect');
        if (select) select.value = payload.preset.preset_id;
        showToast(`Saved preset: ${payload.preset.name}`, 'success');
        return payload.preset;
    }

    async function savePreviewPreset() {
        const input = document.getElementById('previewPresetName');
        const confirm = document.getElementById('previewPresetConfirm');
        const presetName = input?.value.trim() || '';
        if (!presetName) {
            showToast('Enter a name for this preset.', 'info');
            input?.focus();
            return;
        }
        if (confirm) confirm.disabled = true;
        try {
            const saved = await saveAnimationPreset(presetName);
            if (saved) closePreviewPresetComposer();
        } finally {
            if (confirm) confirm.disabled = false;
        }
    }

    async function saveControlPreset() {
        const input = document.getElementById('controlPresetName');
        const categoryInput = document.getElementById('controlPresetCategory');
        const descriptionInput = document.getElementById('controlPresetDescription');
        const presetName = input.value.trim();
        const animationName = controlSelectedAnimation;
        if (!animationName || !presetName) {
            showToast('Select an animation and enter a preset name.', 'info');
            return;
        }
        const saved = await saveAnimationPreset(
            presetName,
            categoryInput.value.trim() || 'Personal',
            descriptionInput.value.trim()
        );
        if (!saved) return;
        input.value = '';
        categoryInput.value = '';
        descriptionInput.value = '';
    }

    async function applyControlPreset() {
        const select = document.getElementById('controlPresetSelect');
        const presetId = select.value;
        const animationName = controlSelectedAnimation;
        if (!animationName || !presetId) return;
        const response = await fetch(
            `/api/animations/${encodeURIComponent(animationName)}/presets/${encodeURIComponent(presetId)}/apply`,
            {method: 'POST'}
        );
        const payload = await response.json();
        if (!response.ok) {
            showToast(payload.error || 'Failed to load preset.', 'error');
            return;
        }
        renderControlParameterControls(controlParameterSchema, payload.preset.params || {});
        document.getElementById('controlPresetName').value = payload.preset.name || '';
        document.getElementById('controlPresetCategory').value = payload.preset.category || '';
        document.getElementById('controlPresetDescription').value = payload.preset.description || '';
        setCurrentPresetSelection(animationName, payload.preset);
        showToast(`Loaded preset: ${payload.preset.name}`, 'success');
    }

    async function deleteControlPreset() {
        const select = document.getElementById('controlPresetSelect');
        const presetId = select.value;
        const animationName = controlSelectedAnimation;
        if (!animationName || !presetId) return;
        const presetName = select.options[select.selectedIndex].textContent;
        if (!window.confirm(`Delete preset "${presetName}"?`)) return;
        const response = await fetch(
            `/api/animations/${encodeURIComponent(animationName)}/presets/${encodeURIComponent(presetId)}`,
            {method: 'DELETE'}
        );
        if (response.ok) {
            await loadControlPresets(animationName);
            showToast(`Deleted preset: ${presetName}`, 'success');
        }
    }

    function updateControlParameter(name, value, type) {
        let convertedValue = value;
        if (type === 'int') {
            convertedValue = parseInt(value, 10);
        } else if (type === 'float') {
            convertedValue = parseFloat(value);
        } else if (type === 'bool') {
            if (typeof value === 'string') {
                convertedValue = value === 'true' || value === '1';
            } else {
                convertedValue = Boolean(value);
            }
        }

        if ((type === 'int' || type === 'float') && Number.isNaN(convertedValue)) {
            return;
        }

        updateControlParametersBatch({[name]: convertedValue});
    }

    function updateControlParametersBatch(params) {
        if (!params || !Object.keys(params).length) return;
        if (controlParameterUpdateTimeout) clearTimeout(controlParameterUpdateTimeout);
        if (controlSelectedAnimation) {
            if (!controlParameterStore[controlSelectedAnimation]) controlParameterStore[controlSelectedAnimation] = {};
            Object.assign(controlParameterStore[controlSelectedAnimation], params);
            if (currentPresetSelection?.animation === controlSelectedAnimation) {
                currentPresetSelection = {...currentPresetSelection, is_dirty: true};
                updatePreviewPresetAction();
            }
            if (animationRenderer && animationRenderer.previewMode && animationRenderer.previewAnimation === controlSelectedAnimation) {
                animationRenderer.setPreviewParams(controlParameterStore[controlSelectedAnimation]);
            }
        }
        controlParameterUpdateTimeout = setTimeout(() => {
            updateParameters(params).then(result => {
                if (!result.success) console.error('Failed to update parameters:', params);
            });
        }, 120);
    }

    function showToast(message, type = 'info') {
        // Simple toast notification
        const toast = document.createElement('div');
        toast.className = `alert alert-${type === 'error' ? 'danger' : type === 'success' ? 'success' : 'info'} position-fixed`;
        toast.style.cssText = 'top: 20px; right: 20px; z-index: 9999; min-width: 300px;';
        toast.innerHTML = `${message} <button type="button" class="btn-close" onclick="this.parentElement.remove()"></button>`;
        document.body.appendChild(toast);

        // Auto-remove after 3 seconds
        setTimeout(() => toast.remove(), 3000);
    }

    function sceneComponentDescriptor(componentId, provider = null) {
        const matches = SCENE_COMPONENT_CATALOG.filter(item => (
            item.plugin_id === componentId
            && (provider === null || item.provider === provider)
        ));
        return matches.length === 1 ? matches[0] : null;
    }

    function sceneComponentRef(componentId, authoredParameters = null, expectedProvider = null) {
        const descriptor = sceneComponentDescriptor(componentId, expectedProvider);
        if (!descriptor) {
            throw new Error(`Unknown or ambiguous scene component: ${expectedProvider || '?'}:${componentId}`);
        }
        const provider = descriptor.provider || 'python';
        const overrides = authoredParameters ? {...authoredParameters} : {};
        const ref = {
            plugin_id: componentId,
            provider,
            parameter_overrides: overrides,
            resolved_parameters: provider === 'receiver_native'
                ? {...(descriptor.defaults || {}), ...overrides}
                : overrides
        };
        if (provider === 'receiver_native') {
            const build = descriptor.build || {};
            ref.bundle_digest = build.contract_digest || build.bundle_digest;
            ref.expected_payload_digest = build.expected_payload_digest;
            if (!ref.bundle_digest || !ref.expected_payload_digest) {
                throw new Error(`${componentId} is missing its receiver contract binding.`);
            }
        }
        return ref;
    }

    function sceneProviderLabel(provider) {
        return provider === 'receiver_native' ? 'Receiver native' : 'Host Python';
    }

    function sceneRoleLabel(role) {
        return String(role || 'component')
            .split('_')
            .map(part => part ? part[0].toUpperCase() + part.slice(1) : '')
            .join(' ');
    }

    function renderReceiverParameterControls(descriptor, authoredParameters = null) {
        const host = document.getElementById('sceneReceiverParameterControls');
        if (!host) return;
        host.replaceChildren();
        const schema = descriptor?.parameter_schema || {};
        const values = {...(descriptor?.defaults || {}), ...(authoredParameters || {})};
        Object.entries(schema).forEach(([parameterId, contract]) => {
            const column = document.createElement('div');
            column.className = 'col-6';
            const inputId = `sceneReceiverParameter-${parameterId}`;
            const label = document.createElement('label');
            label.className = 'form-label';
            label.htmlFor = inputId;
            label.textContent = parameterId.replaceAll('_', ' ');
            const input = document.createElement('input');
            input.id = inputId;
            input.dataset.nativeParam = parameterId;
            input.dataset.parameterType = contract.type;
            input.className = contract.type === 'bool'
                ? 'form-check-input d-block mt-2'
                : 'form-control';
            if (contract.type === 'bool') {
                input.type = 'checkbox';
                input.checked = Boolean(values[parameterId]);
            } else {
                input.type = 'number';
                if (contract.min != null) input.min = contract.min;
                if (contract.max != null) input.max = contract.max;
                input.step = contract.type === 'int' ? '1' : 'any';
                input.value = values[parameterId] ?? contract.default ?? 0;
            }
            if (contract.description) input.title = contract.description;
            column.append(label, input);
            host.appendChild(column);
        });
    }

    function syncSceneProviderControls(authoredParameters = null) {
        const selected = document.getElementById('sceneBackgroundSelect')?.selectedOptions?.[0];
        const componentId = selected?.dataset.componentId || selected?.value;
        const descriptor = sceneComponentDescriptor(componentId, selected?.dataset.provider || null);
        const receiverNative = descriptor?.provider === 'receiver_native';
        const fallbackField = document.getElementById('scenePythonFallbackField');
        const receiverParameters = document.getElementById('sceneReceiverParameters');
        if (fallbackField) fallbackField.hidden = !receiverNative;
        if (receiverParameters) receiverParameters.hidden = !receiverNative;
        renderReceiverParameterControls(
            receiverNative ? descriptor : null,
            authoredParameters
        );

        const providerBadge = document.getElementById('sceneBackgroundProvider');
        if (providerBadge) {
            providerBadge.textContent = descriptor
                ? sceneProviderLabel(descriptor.provider)
                : 'Provider unavailable';
            providerBadge.dataset.providerLabel = descriptor?.provider || 'unavailable';
        }
        safeSetText(
            'sceneBackgroundRole',
            descriptor ? sceneRoleLabel(descriptor.role) : 'Role unavailable'
        );
        const compatibility = descriptor?.scene_compatibility || {};
        const availability = document.getElementById('sceneBackgroundAvailability');
        if (availability) {
            availability.textContent = compatibility.selectable
                ? 'Scene selectable'
                : 'Catalog only';
            availability.className = `badge component-availability-badge ${
                compatibility.selectable ? 'is-selectable' : 'is-catalog-only'
            }`;
        }
        safeSetText(
            'sceneBackgroundDiagnostic',
            compatibility.diagnostic
                || (descriptor ? 'Compatible with the fixed background slot.' : 'Choose a compatible background.')
        );
    }

    function receiverBackgroundParameters(backgroundId, provider) {
        if (sceneComponentDescriptor(backgroundId, provider)?.provider !== 'receiver_native') return null;
        const result = {};
        document.querySelectorAll('[data-native-param]').forEach(input => {
            const parameterId = input.dataset.nativeParam;
            if (input.dataset.parameterType === 'bool') {
                result[parameterId] = input.checked;
            } else {
                result[parameterId] = Number(input.value);
            }
        });
        return result;
    }

    function clockOverlayParameters() {
        const showSeconds = document.getElementById('sceneClockShowSeconds');
        const format24h = document.getElementById('sceneClock24Hour');
        if (!showSeconds && !format24h) return null;
        return {
            show_seconds: showSeconds ? showSeconds.checked : true,
            format_24h: format24h ? format24h.checked : false
        };
    }

    function editedScenePayload() {
        const backgroundOption = document.getElementById('sceneBackgroundSelect')?.selectedOptions?.[0];
        const backgroundId = backgroundOption?.dataset.componentId || backgroundOption?.value;
        const backgroundProvider = backgroundOption?.dataset.provider || null;
        if (!backgroundId) throw new Error('Choose a compatible background.');
        const background = sceneComponentRef(
            backgroundId,
            receiverBackgroundParameters(backgroundId, backgroundProvider),
            backgroundProvider
        );
        let fallback = {...background};
        if (background.provider === 'receiver_native') {
            const fallbackOption = document.getElementById('scenePythonFallbackSelect')?.selectedOptions?.[0];
            const fallbackId = fallbackOption?.dataset.componentId || fallbackOption?.value;
            if (!fallbackId) throw new Error('Choose a Python fallback for receiver playback.');
            fallback = sceneComponentRef(fallbackId, null, 'python');
        }
        const overlays = [];
        if (document.getElementById('sceneOverlayEnabled')?.checked) {
            const stalePolicy = document.getElementById('sceneStalePolicy')?.value || 'hold';
            const stale = {policy: stalePolicy};
            if (stalePolicy === 'clear_after_lease') stale.lease_ms = 1000;
            overlays.push({
                slot_id: 'clock_overlay',
                component: sceneComponentRef(
                    'clock_overlay', clockOverlayParameters(), 'python'
                ),
                enabled: true,
                opacity: Number(document.getElementById('sceneOverlayOpacity')?.value || 255),
                placement: {
                    strip_translation: Number(document.getElementById('sceneOverlayStripOffset')?.value || 0),
                    led_translation: Number(document.getElementById('sceneOverlayLedOffset')?.value || 0),
                    clip_policy: 'clip_to_wall'
                },
                stale_policy: stale
            });
        }
        return {
            schema: 'ledgrid.scene-state',
            schema_version: 1,
            revision: Date.now(),
            background,
            overlays,
            known_python_fallback: fallback
        };
    }

    async function sceneRequest(url, options) {
        const response = await fetch(url, options);
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || 'Scene request failed.');
        return payload;
    }

    async function startEditedScene() {
        try {
            const scene = editedScenePayload();
            await sceneRequest('/api/v1/scene', {
                method: 'PUT', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(scene)
            });
            showToast('Scene start requested.', 'success');
        } catch (error) {
            showToast(error.message, 'error');
        }
    }

    async function previewEditedScene() {
        try {
            const payload = await sceneRequest('/api/v1/scene/preview', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    scene: editedScenePayload(),
                    vibe: globalVibeId,
                    plant_modifiers: globalPlantModifiers
                })
            });
            if (animationRenderer) {
                animationRenderer.lastFrameData = payload;
                animationRenderer.renderFrame(payload);
            }
            const notice = document.getElementById('scenePreviewNotice');
            if (notice) {
                const receiverSimulation = payload.background_provider === 'receiver_native';
                notice.hidden = !receiverSimulation;
                const label = document.getElementById('scenePreviewLabel');
                if (label && receiverSimulation) {
                    label.textContent = payload.preview_label
                        || 'Host simulation preview — not receiver framebuffer readback';
                }
            }
            showToast('Preview rendered without changing the live scene.', 'success');
        } catch (error) {
            showToast(error.message, 'error');
        }
    }

    async function stopEditedScene() {
        try {
            await sceneRequest('/api/v1/scene', {method: 'DELETE'});
            showToast('Scene stop requested.', 'success');
        } catch (error) {
            showToast(error.message, 'error');
        }
    }

    async function recoverReceiverNative() {
        try {
            await sceneRequest('/api/v1/receiver-native/recover', {method: 'POST'});
            showToast('Receiver-native recovery to the recorded Python fallback requested.', 'success');
        } catch (error) {
            showToast(error.message, 'error');
        }
    }

    function loadSceneIntoEditor(scene) {
        const background = document.getElementById('sceneBackgroundSelect');
        if (background) {
            const option = Array.from(background.options).find(item => (
                (item.dataset.componentId || item.value) === scene.background.plugin_id
                && item.dataset.provider === scene.background.provider
            ));
            background.selectedIndex = option ? option.index : -1;
        }
        const fallback = document.getElementById('scenePythonFallbackSelect');
        if (fallback && scene.background.provider === 'receiver_native') {
            const option = Array.from(fallback.options).find(item => (
                (item.dataset.componentId || item.value)
                    === scene.known_python_fallback.plugin_id
                && item.dataset.provider === 'python'
            ));
            fallback.selectedIndex = option ? option.index : -1;
        }
        const receiverParameters = {
            ...(scene.background.resolved_parameters || {}),
            ...(scene.background.parameter_overrides || {})
        };
        syncSceneProviderControls(receiverParameters);
        const overlay = Array.isArray(scene.overlays) ? scene.overlays[0] : null;
        document.getElementById('sceneOverlayEnabled').checked = Boolean(overlay?.enabled);
        if (!overlay) return;
        const clockParameters = overlay.component.parameter_overrides || {};
        const showSeconds = document.getElementById('sceneClockShowSeconds');
        const format24h = document.getElementById('sceneClock24Hour');
        if (showSeconds && clockParameters.show_seconds != null) {
            showSeconds.checked = Boolean(clockParameters.show_seconds);
        }
        if (format24h && clockParameters.format_24h != null) {
            format24h.checked = Boolean(clockParameters.format_24h);
        }
        document.getElementById('sceneOverlayOpacity').value = overlay.opacity;
        document.getElementById('sceneOverlayStripOffset').value = overlay.placement.strip_translation;
        document.getElementById('sceneOverlayLedOffset').value = overlay.placement.led_translation;
        document.getElementById('sceneStalePolicy').value = overlay.stale_policy.policy;
    }

    function syncReceiverHybridStatus(status) {
        const host = document.getElementById('receiverHybridStatus');
        if (!host) return;
        const scene = status?.scene || {};
        const receiver = scene.receiver || status?.receiver_hybrid || null;
        const publisher = receiver?.publisher || {};
        const driver = receiver?.driver || {};
        const receiverMode = ['receiver_hybrid', 'receiver_native'].includes(
            scene.provider_mode
        );
        const managedNative = scene.provider_mode === 'receiver_native';
        const state = document.getElementById('receiverAgreementState');

        let stateLabel = 'Host Python scene';
        let stateKind = 'host';
        if (receiverMode) {
            if (receiver?.fallback_active) {
                stateLabel = 'Degraded · fallback active';
                stateKind = 'degraded';
            } else if (receiver?.healthy === true) {
                stateLabel = 'Agreed';
                stateKind = 'healthy';
            } else if (receiver?.healthy === false) {
                stateLabel = 'Degraded';
                stateKind = 'degraded';
            } else {
                stateLabel = 'Awaiting receiver proof';
                stateKind = 'waiting';
            }
        }
        host.dataset.state = stateKind;
        if (state) {
            state.textContent = stateLabel;
            state.dataset.state = stateKind;
        }
        safeSetText(
            'receiverForegroundLease',
            Number.isFinite(Number(publisher.lease_ms)) ? `${publisher.lease_ms} ms` : '--'
        );
        safeSetText(
            'receiverForegroundGeneration',
            publisher.generation != null ? publisher.generation : '--'
        );
        safeSetText(
            'receiverFallbackState',
            receiver?.fallback_active ? 'Active · Python fallback' : 'Standby'
        );
        safeSetText(
            'receiverTelemetryState',
            receiver?.telemetry_complete === true
                ? 'Complete'
                : receiver?.telemetry_complete === false
                    ? 'Incomplete · degraded return path'
                    : '--'
        );
        safeSetText(
            'receiverReleaseAcceptance',
            receiver?.release_acceptance === true
                ? 'Accepted'
                : receiver?.release_acceptance === false
                    ? 'Not proven'
                    : '--'
        );
        safeSetText('receiverTransportPolicy', receiver?.transport_policy || '--');
        safeSetText(
            'receiverNativeOperation',
            managedNative
                ? `${driver.operation || 'unknown'} · ${driver.state || 'unknown'}`
                : 'Static compiled background'
        );
        safeSetText(
            'receiverNativeArtifact',
            managedNative && driver.bundle_digest
                ? `${String(driver.bundle_digest).slice(0, 12)}… / ${String(driver.payload_digest || '').slice(0, 12)}…`
                : '--'
        );
        safeSetText(
            'receiverNativeProgress',
            managedNative && Number.isFinite(Number(driver.progress))
                ? `${Math.round(Number(driver.progress) * 100)}%`
                : '--'
        );
        const recovery = document.getElementById('receiverNativeRecovery');
        if (recovery) {
            recovery.hidden = !managedNative;
            recovery.disabled = !managedNative || receiver?.fallback_active === true;
        }

        safeSetText('receiverQuarantineState', 'Not supported in this phase');

        const agreement = [];
        if (receiver?.source_scene_revision != null) {
            agreement.push(`scene ${receiver.source_scene_revision}`);
        }
        if (receiver?.context_revision != null) {
            agreement.push(`context ${receiver.context_revision}`);
        }
        if (Array.isArray(receiver?.readable_devices)) {
            agreement.push(`readable ${receiver.readable_devices.join(',') || 'none'}`);
        }
        if (Array.isArray(receiver?.unverified_devices) && receiver.unverified_devices.length) {
            agreement.push(`unverified ${receiver.unverified_devices.join(',')}`);
        }
        if (publisher.last_operation) agreement.push(publisher.last_operation);
        if (receiver?.error) agreement.push(`error: ${receiver.error}`);
        if (driver?.error) agreement.push(`native error: ${driver.error}`);
        if (publisher.last_error) agreement.push(`error: ${publisher.last_error}`);
        safeSetText('receiverHybridDetail', agreement.join(' · ') || '--');
    }

    async function saveScenePreset() {
        const input = document.getElementById('scenePresetName');
        const name = input?.value.trim();
        if (!name) {
            showToast('Enter a scene preset name.', 'info');
            input?.focus();
            return;
        }
        try {
            const payload = await sceneRequest('/api/v1/scene-presets', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name, scene: editedScenePayload()})
            });
            const select = document.getElementById('scenePresetSelect');
            let option = Array.from(select.options).find(item => item.value === payload.preset.preset_id);
            if (!option) {
                option = document.createElement('option');
                option.value = payload.preset.preset_id;
                select.appendChild(option);
            }
            option.textContent = payload.preset.name;
            select.value = payload.preset.preset_id;
            input.value = '';
            showToast(`Saved scene preset: ${payload.preset.name}`, 'success');
        } catch (error) {
            showToast(error.message, 'error');
        }
    }

    async function applyScenePreset() {
        const presetId = document.getElementById('scenePresetSelect')?.value;
        if (!presetId) return;
        try {
            const encoded = encodeURIComponent(presetId);
            const preset = await sceneRequest(`/api/v1/scene-presets/${encoded}`);
            loadSceneIntoEditor(preset.scene);
            await sceneRequest(`/api/v1/scene-presets/${encoded}/apply`, {method: 'POST'});
            showToast(`Applied scene preset: ${preset.name}`, 'success');
        } catch (error) {
            showToast(error.message, 'error');
        }
    }
