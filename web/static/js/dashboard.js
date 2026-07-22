
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
            this.fetchIntervalMs = 150;   // Limit backend polling to ~6-7 FPS
            this.renderTimer = null;
            this.fetchInFlight = false;
            this.lastFrameData = null;
            this.previewParams = null;

            // LED configuration - will be updated from server
            this.stripCount = Number.isFinite(INITIAL_STRIP_COUNT) && INITIAL_STRIP_COUNT > 0 ? INITIAL_STRIP_COUNT : 1;
            this.ledsPerStrip = Number.isFinite(INITIAL_LEDS_PER_STRIP) && INITIAL_LEDS_PER_STRIP > 0 ? INITIAL_LEDS_PER_STRIP : 1;
            this.totalLeds = this.stripCount * this.ledsPerStrip;

            // Rendering configuration
            this.ledSize = 6;
            this.ledSpacing = 0.5;
            this.stripSpacing = 1;
            this.canvas.addEventListener('click', (event) => this.requestHoleAtEvent(event));
            this._onResize = () => this.syncDisplayWidth();
            window.addEventListener('resize', this._onResize);

            this.setupCanvas();
            this.initialize();
        }

        requestHoleAtEvent(event) {
            const rect = this.canvas.getBoundingClientRect();
            if (rect.width <= 0 || rect.height <= 0) return;
            const x = Math.max(0, Math.min(this.stripCount - 1,
                ((event.clientX - rect.left) / rect.width) * this.stripCount));
            const displayY = Math.max(0, Math.min(this.ledsPerStrip - 1,
                ((event.clientY - rect.top) / rect.height) * this.ledsPerStrip));
            requestHoleAt(x, displayY);
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
                    const url = hasParams
                        ? `/api/preview/${this.previewAnimation}/with_params`
                        : `/api/preview/${this.previewAnimation}`;
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
                clearTimeout(this.renderTimer);
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
    const PLANT_MODIFIERS = [
        ['Visual', 'illuminate', 'Illuminate'], ['Visual', 'shadow', 'Shadow'],
        ['Visual', 'refract', 'Refract'], ['Field', 'attractor', 'Attractor'],
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
        if (INITIAL_STATUS) {
            syncControlPanel(INITIAL_STATUS);
            syncGlobalSpeedFromStatus(INITIAL_STATUS);
            syncPlantModifiersFromStatus(INITIAL_STATUS);
        }
        startStatsPolling();
    });

    function formatSpeed(value) {
        const speed = Number(value);
        if (!Number.isFinite(speed)) return '';
        if (speed >= 100) return speed.toFixed(0);
        if (speed >= 10) return speed.toFixed(1).replace(/\.0$/, '');
        if (speed >= 1) return speed.toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
        return speed.toPrecision(2).replace(/0+$/, '').replace(/\.$/, '');
    }

    function previewGlobalSpeed(value) {
        const number = document.getElementById('globalSpeedNumber');
        if (number && document.activeElement !== number) number.value = formatSpeed(value);
        const range = document.getElementById('globalSpeedRange');
        const multiplier = Number(value);
        if (range && Number.isFinite(multiplier) && multiplier > 0) {
            range.value = multiplierToSpeedPosition(multiplier);
        }
        document.querySelectorAll('.speed-preset').forEach(button => button.classList.remove('active'));
    }

    function speedPositionToMultiplier(position) {
        return 10 ** ((Number(position) - 50) / 50);
    }

    function multiplierToSpeedPosition(multiplier) {
        return Math.max(0, Math.min(100, 50 + 50 * Math.log10(Number(multiplier))));
    }

    function previewGlobalSpeedFromPosition(position) {
        const multiplier = speedPositionToMultiplier(position);
        const number = document.getElementById('globalSpeedNumber');
        if (number) number.value = formatSpeed(multiplier);
        document.querySelectorAll('.speed-preset').forEach(button => button.classList.remove('active'));
    }

    function setGlobalSpeedFromPosition(position) {
        setGlobalSpeed(speedPositionToMultiplier(position));
    }

    function syncGlobalSpeedFromStatus(status) {
        const range = document.getElementById('globalSpeedRange');
        if (!range || !status) return;
        const scale = Number(status.animation_speed_scale);
        if (!Number.isFinite(scale) || scale <= 0 || !Number.isFinite(SPEED_BASELINE)) return;
        const multiplier = scale / SPEED_BASELINE;
        if (document.activeElement !== range) range.value = multiplierToSpeedPosition(multiplier);
        const number = document.getElementById('globalSpeedNumber');
        if (number && document.activeElement !== number) number.value = formatSpeed(multiplier);
        document.querySelectorAll('.speed-preset').forEach(button => {
            const match = (button.getAttribute('onclick') || '').match(/[\d.]+/);
            const buttonValue = match ? Number(match[0]) : NaN;
            button.classList.toggle('active', Math.abs(buttonValue - multiplier) < .05);
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
            highlightControlSelection(animationName);
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

    function requestHoleAt(x, y) {
        fetch('/api/hole', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({x, y})
        })
            .then(r => r.json())
            .then(result => {
                if (result.success && typeof showToast === 'function') {
                    showToast(`Punched hole at (${Math.round(x)}, ${Math.round(y)})`, 'success');
                }
            })
            .catch(error => console.error('Failed to punch positioned hole', error));
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
            syncPlantModifiersFromStatus(data);
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
            showControlPlaceholder('Start or select an animation to adjust its live controls.', {clearControls: true});
            return;
        }
        if (controlSelectedAnimation === runningAnimation) {
            return;
        }
        controlSelectedAnimation = runningAnimation;
        highlightControlSelection(runningAnimation);
        loadControlParameters(runningAnimation, {
            placeholderMessage: `Loading controls for ${humanizeParamName(runningAnimation)}...`
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
        const { showPlaceholder = true, placeholderMessage = null } = options;
        if (showPlaceholder) {
            showControlPlaceholder(placeholderMessage || 'Loading controls...', {clearControls: true});
        }
        fetch(`/api/animations/${name}`)
            .then(r => r.json())
            .then(info => {
                if (info && info.parameters && Object.keys(info.parameters).length) {
                    renderControlParameterControls(info.parameters, info.current_params || {});
                    hideControlPlaceholder();
                    loadControlPresets(name);
                } else {
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
        const parameterSnapshot = {};
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
        const response = await fetch(`/api/animations/${encodeURIComponent(animationName)}/presets`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                name: presetName,
                category: categoryInput.value.trim() || 'Personal',
                description: descriptionInput.value.trim(),
                params: controlParameterStore[animationName] || {}
            })
        });
        const payload = await response.json();
        if (!response.ok) {
            showToast(payload.error || 'Failed to save preset.', 'error');
            return;
        }
        input.value = '';
        categoryInput.value = '';
        descriptionInput.value = '';
        await loadControlPresets(animationName);
        document.getElementById('controlPresetSelect').value = payload.preset.preset_id;
        showToast(`Saved preset: ${payload.preset.name}`, 'success');
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
