(function composerApplication() {
    'use strict';

    const {ComposerRuntime} = window.LEDGridComposerRuntime || {};
    const ComposerState = window.LEDGridComposerState || {};
    const $ = (id) => document.getElementById(id);
    const STORAGE_PREFIX = 'ledgrid.browser-composer.v1';
    const SAMPLE_FRAMES = 48;
    const PLANT_MODIFIER_GROUPS = Object.freeze([
        {id: 'visual', name: 'Light & material', mode: 'multiple', modifiers: ['illuminate', 'shadow', 'refract', 'hue_shift', 'liquid_glass']},
        {id: 'field', name: 'Field · choose one', mode: 'exclusive', modifiers: ['attractor', 'repulsor', 'slow_zone']},
        {id: 'surface', name: 'Surface · choose one', mode: 'exclusive', modifiers: ['obstacle', 'portal', 'bumper', 'hazard', 'habitat']},
        {id: 'source', name: 'Source', mode: 'multiple', modifiers: ['emitter']},
    ]);
    const CLOCK_STARTING_POINTS = Object.freeze([
        {key: 'composer:clock:amber-digital', name: 'Amber digital', params: {face: 'digital', palette: 'amber', format_24h: false, show_seconds: true, position_y: .5, scale: 1, glow: .45, brightness: 1, opacity: 1, backdrop_opacity: 0}},
        {key: 'composer:clock:quiet-analog', name: 'Quiet analog', params: {face: 'analog', palette: 'mono', format_24h: false, show_seconds: false, position_y: .32, scale: 1, glow: .18, brightness: .72, opacity: .88, backdrop_opacity: 0}},
        {key: 'composer:clock:high-contrast', name: 'High contrast 24h', params: {face: 'digital', palette: 'ice', format_24h: true, show_seconds: false, position_y: .5, scale: 2, glow: .28, brightness: 1, opacity: 1, backdrop_opacity: .58, backdrop_padding: 2}},
    ]);
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
        runtimes: {draft: null, original: null, overlay: null},
        originalRuntimePromise: null,
        overlayRuntimePromise: null,
        overlayMode: null,
        compareGeneration: 0,
        frames: {draft: null, original: null, overlay: null, composed: null},
        runtimeGeneration: 0,
        history: [],
        historyIndex: -1,
        catalogFilter: 'all',
        query: '',
        checkerGeneration: 0,
        draftGeneration: 0,
        documentRevision: 1,
        checkResult: null,
        autosaveTimer: null,
        connectivityTimer: null,
        serverOnline: false,
        serverChecking: true,
        busyAction: null,
        lastSavedPreset: null,
        globalSettings: {
            observed: null,
            draft: null,
            dirty: false,
            loading: false,
            applying: false,
            pendingObservation: false,
            pendingSince: 0,
        },
        masks: {
            loaded: false,
            ledInfo: null,
            cells: null,
            savedCells: null,
            history: [],
            tool: 1,
            dirty: false,
            painting: false,
            lastCell: null,
            zoom: 6,
        },
        layers: {
            clockEnabled: false,
            clockOpacity: 220,
            clockParams: {},
            clockPresetKey: '',
            fallbackKey: null,
        },
    };

    function clone(value) {
        return ComposerState.clone ? ComposerState.clone(value) : JSON.parse(JSON.stringify(value ?? null));
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

    async function requestJson(url, options = {}) {
        let response;
        try {
            response = await fetch(url, {
                ...options,
                headers: {
                    'Accept': 'application/json',
                    ...(options.body ? {'Content-Type': 'application/json'} : {}),
                    ...(options.headers || {}),
                },
                cache: 'no-store',
            });
        } catch (_error) {
            const error = new Error('The wall server is unreachable. Your local draft is still safe.');
            error.code = 'offline';
            throw error;
        }
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            const error = new Error(payload.error || `Server request failed (${response.status}).`);
            error.status = response.status;
            error.code = payload.code;
            error.payload = payload;
            throw error;
        }
        return payload;
    }

    function globalActions() {
        return state.bootstrap?.capabilities?.server_actions || {};
    }

    function vibeProfiles() {
        return Array.isArray(state.bootstrap?.vibe_profiles) ? state.bootstrap.vibe_profiles : [];
    }

    function vibeProfile(vibeId = state.globalSettings.draft?.vibeId) {
        return vibeProfiles().find((profile) => profile.vibe_id === vibeId)
            || vibeProfiles().find((profile) => profile.vibe_id === 'neutral')
            || {vibe_id: 'neutral', tempo_scale: 1, luminance_scale: 1};
    }

    function canonicalPlantModifiers(value) {
        const contract = state.bootstrap?.global_control_contract || {};
        const ids = Array.isArray(contract.plant_modifier_ids)
            ? contract.plant_modifier_ids
            : PLANT_MODIFIER_GROUPS.flatMap((group) => group.modifiers);
        const rawActive = Array.isArray(value?.active) ? value.active : [];
        const activeSet = new Set(rawActive.filter((id) => ids.includes(id)));
        for (const group of PLANT_MODIFIER_GROUPS.filter((item) => item.mode === 'exclusive')) {
            const selected = group.modifiers.filter((id) => activeSet.has(id));
            selected.slice(1).forEach((id) => activeSet.delete(id));
        }
        const active = ids.filter((id) => activeSet.has(id));
        const strengths = {};
        active.forEach((id) => {
            const fallback = id === 'obstacle' ? 1 : .5;
            strengths[id] = Math.max(0, Math.min(1, safeNumber(value?.strengths?.[id], fallback)));
        });
        return {version: 1, active, strengths};
    }

    function statusVibeId(status) {
        const raw = status?.vibe?.state || status?.vibe || {};
        const id = raw.vibe_id || raw.id;
        return vibeProfiles().some((profile) => profile.vibe_id === id) ? id : 'neutral';
    }

    function normalizedGlobalSettings(status = {}) {
        const baseline = safeNumber(state.bootstrap?.global_control_contract?.operator_speed_baseline, .3) || .3;
        const brightness = status.brightness == null ? 128 : Math.round(safeNumber(status.brightness, 128));
        const targetFps = safeNumber(status.target_fps, 0) > 0 ? Math.round(status.target_fps) : 30;
        const speedScale = safeNumber(status.animation_speed_scale, baseline);
        return {
            vibeId: statusVibeId(status),
            brightness: Math.max(0, Math.min(255, brightness)),
            targetFps: Math.max(1, Math.min(200, targetFps)),
            speedMultiplier: Math.max(.25, Math.min(3, speedScale / baseline)),
            plantModifiers: canonicalPlantModifiers(
                status.plant_modifiers || state.bootstrap?.installation_profile?.plant_modifiers,
            ),
        };
    }

    function globalSettingsEqual(left, right) {
        return JSON.stringify(left || null) === JSON.stringify(right || null);
    }

    function loadStoredGlobalDraft() {
        try {
            const stored = JSON.parse(localStorage.getItem(`${STORAGE_PREFIX}.global-draft`));
            if (!stored || typeof stored !== 'object') return null;
            return {
                vibeId: vibeProfiles().some((profile) => profile.vibe_id === stored.vibeId) ? stored.vibeId : 'neutral',
                brightness: Math.max(0, Math.min(255, Math.round(safeNumber(stored.brightness, 128)))),
                targetFps: Math.max(1, Math.min(200, Math.round(safeNumber(stored.targetFps, 30)))),
                speedMultiplier: Math.max(.25, Math.min(3, safeNumber(stored.speedMultiplier, 1))),
                plantModifiers: canonicalPlantModifiers(stored.plantModifiers),
            };
        } catch (_error) {
            return null;
        }
    }

    function persistGlobalDraft() {
        if (!state.globalSettings.draft) return;
        localStorage.setItem(`${STORAGE_PREFIX}.global-draft`, JSON.stringify(state.globalSettings.draft));
    }

    function initializeGlobalSettings() {
        const bootstrapState = normalizedGlobalSettings({
            plant_modifiers: state.bootstrap?.installation_profile?.plant_modifiers,
            vibe: {state: {vibe_id: 'neutral'}},
            brightness: 128,
            target_fps: 30,
            animation_speed_scale: state.bootstrap?.global_control_contract?.operator_speed_baseline,
        });
        state.globalSettings.observed = bootstrapState;
        state.globalSettings.draft = loadStoredGlobalDraft() || clone(bootstrapState);
        state.globalSettings.dirty = !globalSettingsEqual(state.globalSettings.observed, state.globalSettings.draft);
        renderGlobalSettings();
    }

    function globalChangeList() {
        const observed = state.globalSettings.observed;
        const draft = state.globalSettings.draft;
        if (!observed || !draft) return [];
        const changes = [];
        if (observed.vibeId !== draft.vibeId) changes.push({id: 'vibe', label: 'Vibe', before: humanize(observed.vibeId), after: humanize(draft.vibeId)});
        if (!globalSettingsEqual(observed.plantModifiers, draft.plantModifiers)) {
            const describe = (plant) => plant.active.length ? plant.active.map(humanize).join(', ') : 'Off';
            changes.push({id: 'plant', label: 'Plant behavior', before: describe(observed.plantModifiers), after: describe(draft.plantModifiers)});
        }
        if (observed.brightness !== draft.brightness) changes.push({id: 'brightness', label: 'Master brightness', before: `${observed.brightness} / 255`, after: `${draft.brightness} / 255`});
        if (observed.targetFps !== draft.targetFps) changes.push({id: 'targetFps', label: 'Target frame rate', before: `${observed.targetFps} fps`, after: `${draft.targetFps} fps`});
        if (Math.abs(observed.speedMultiplier - draft.speedMultiplier) > .001) changes.push({id: 'speed', label: 'Operator speed', before: `${observed.speedMultiplier.toFixed(2)}×`, after: `${draft.speedMultiplier.toFixed(2)}×`});
        return changes;
    }

    function renderVibeOptions(draft) {
        const host = $('vibeOptions');
        if (!host) return;
        host.replaceChildren();
        vibeProfiles().forEach((profile) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.setAttribute('role', 'radio');
            button.setAttribute('aria-checked', String(profile.vibe_id === draft.vibeId));
            button.textContent = humanize(profile.vibe_id);
            button.addEventListener('click', () => updateGlobalDraft((next) => { next.vibeId = profile.vibe_id; }));
            host.appendChild(button);
        });
        const profile = vibeProfile(draft.vibeId);
        $('vibeReadout').textContent = humanize(draft.vibeId);
        $('vibeProfileDetail').textContent = `${profile.tempo_scale.toFixed(2)}× atmosphere tempo · ${Math.round(profile.luminance_scale * 100)}% vibe luminance · palette semantics apply on compatible wall runtimes.`;
    }

    function renderPlantModifiers(draft) {
        const host = $('plantModifierGroups');
        if (!host) return;
        host.replaceChildren();
        const active = new Set(draft.plantModifiers.active);
        PLANT_MODIFIER_GROUPS.forEach((group) => {
            const section = document.createElement('section');
            section.className = 'plant-group';
            const heading = document.createElement('h3');
            heading.textContent = group.name;
            section.appendChild(heading);
            group.modifiers.forEach((modifier) => {
                const row = document.createElement('div');
                row.className = `modifier-row${active.has(modifier) ? ' is-active' : ''}`;
                const toggle = document.createElement('button');
                toggle.type = 'button';
                toggle.className = 'modifier-toggle';
                toggle.setAttribute('aria-pressed', String(active.has(modifier)));
                toggle.textContent = humanize(modifier);
                toggle.addEventListener('click', () => updateGlobalDraft((next) => {
                    const selected = new Set(next.plantModifiers.active);
                    if (selected.has(modifier)) selected.delete(modifier);
                    else {
                        if (group.mode === 'exclusive') group.modifiers.forEach((id) => selected.delete(id));
                        selected.add(modifier);
                    }
                    const ids = state.bootstrap.global_control_contract.plant_modifier_ids;
                    next.plantModifiers.active = ids.filter((id) => selected.has(id));
                    next.plantModifiers.strengths[modifier] ??= modifier === 'obstacle' ? 1 : .5;
                    Object.keys(next.plantModifiers.strengths).forEach((id) => {
                        if (!selected.has(id)) delete next.plantModifiers.strengths[id];
                    });
                }));
                const range = document.createElement('input');
                range.type = 'range';
                range.min = '0';
                range.max = '1';
                range.step = '.05';
                range.value = String(draft.plantModifiers.strengths[modifier] ?? (modifier === 'obstacle' ? 1 : .5));
                range.disabled = !active.has(modifier);
                range.setAttribute('aria-label', `${humanize(modifier)} strength`);
                const output = document.createElement('output');
                output.textContent = `${Math.round(safeNumber(range.value) * 100)}%`;
                range.addEventListener('input', () => {
                    output.textContent = `${Math.round(safeNumber(range.value) * 100)}%`;
                    updateGlobalDraft((next) => { next.plantModifiers.strengths[modifier] = safeNumber(range.value); }, {render: false});
                });
                range.addEventListener('change', () => renderGlobalSettings());
                row.append(toggle, range, output);
                section.appendChild(row);
            });
            host.appendChild(section);
        });
        $('plantModifierCount').textContent = `${draft.plantModifiers.active.length} active`;
    }

    function renderGlobalSettings() {
        const draft = state.globalSettings.draft;
        if (!draft) return;
        renderVibeOptions(draft);
        $('globalBrightness').value = String(draft.brightness);
        $('globalBrightnessValue').textContent = `${draft.brightness} / 255`;
        $('globalSpeed').value = String(draft.speedMultiplier);
        $('globalSpeedValue').textContent = `${draft.speedMultiplier.toFixed(2)}×`;
        $('globalTargetFps').value = String(draft.targetFps);
        $('globalTargetFpsValue').textContent = `${draft.targetFps} fps`;
        renderPlantModifiers(draft);
        const changes = globalChangeList();
        state.globalSettings.dirty = changes.length > 0;
        $('wallDraftStatus').textContent = state.globalSettings.loading
            ? 'Reading observed wall state…'
            : state.globalSettings.applying ? 'Applying commands in order…'
            : state.globalSettings.pendingObservation ? 'Commands accepted · waiting for observed wall state'
            : changes.length ? `${changes.length} unapplied wall change${changes.length === 1 ? '' : 's'} · preview is local`
            : 'Draft matches observed wall state.';
        $('resetWallDraftButton').disabled = !changes.length || state.globalSettings.applying;
        $('reviewWallButton').disabled = !changes.length || !state.serverOnline || state.globalSettings.applying || state.globalSettings.pendingObservation;
        $('reviewWallButton').title = !state.serverOnline ? 'Reconnect to review wall-wide changes.' : 'Review every wall-wide command before applying.';
        updateServerActionButtons();
    }

    function updateGlobalDraft(mutator, {render = true} = {}) {
        if (!state.globalSettings.draft) return;
        const next = clone(state.globalSettings.draft);
        mutator(next);
        next.plantModifiers = canonicalPlantModifiers(next.plantModifiers);
        state.globalSettings.draft = next;
        state.globalSettings.dirty = !globalSettingsEqual(next, state.globalSettings.observed);
        persistGlobalDraft();
        resetChecker({preserveDocumentRevision: true});
        requestRender();
        if (render) renderGlobalSettings();
    }

    async function refreshGlobalSettings({quiet = false, preserveDraft = true} = {}) {
        if (!state.bootstrap || state.globalSettings.loading) return;
        state.globalSettings.loading = true;
        renderGlobalSettings();
        try {
            const payload = await requestJson(globalActions().status_url || '/api/status');
            const observed = normalizedGlobalSettings(payload);
            const hadDirtyDraft = state.globalSettings.dirty;
            state.globalSettings.observed = observed;
            if (state.globalSettings.pendingObservation) {
                if (globalSettingsEqual(state.globalSettings.draft, observed)) {
                    state.globalSettings.pendingObservation = false;
                    state.globalSettings.pendingSince = 0;
                } else if (Date.now() - state.globalSettings.pendingSince > 60000) {
                    state.globalSettings.pendingObservation = false;
                    state.globalSettings.pendingSince = 0;
                }
            } else if (!preserveDraft || !hadDirtyDraft) state.globalSettings.draft = clone(observed);
            state.globalSettings.dirty = !globalSettingsEqual(state.globalSettings.draft, observed);
            persistGlobalDraft();
            if (!quiet) toast('Observed wall settings refreshed.');
        } catch (error) {
            if (!quiet) toast(error.message, 'error');
        } finally {
            state.globalSettings.loading = false;
            renderGlobalSettings();
            requestRender();
        }
    }

    function reviewGlobalChanges() {
        const changes = globalChangeList();
        if (!changes.length || !state.serverOnline) return;
        const summary = $('wallChangeSummary');
        summary.replaceChildren();
        changes.forEach((change) => {
            const row = document.createElement('div');
            const term = document.createElement('dt');
            const detail = document.createElement('dd');
            term.textContent = change.label;
            detail.textContent = `${change.before} → ${change.after}`;
            row.append(term, detail);
            summary.appendChild(row);
        });
        $('wallReviewDialog').showModal();
    }

    async function applyGlobalChanges() {
        if (!state.serverOnline || !state.globalSettings.dirty || state.globalSettings.applying) return;
        const draft = clone(state.globalSettings.draft);
        const changes = new Set(globalChangeList().map((change) => change.id));
        const actions = globalActions();
        state.globalSettings.applying = true;
        renderGlobalSettings();
        try {
            if (changes.has('vibe')) await requestJson(actions.vibe_url || '/api/v1/vibe', {method: 'POST', body: JSON.stringify({vibe: draft.vibeId})});
            if (changes.has('plant')) await requestJson(actions.plant_modifiers_url || '/api/config/plant-modifiers', {method: 'POST', body: JSON.stringify({plant_modifiers: draft.plantModifiers})});
            if (changes.has('brightness')) await requestJson(actions.brightness_url || '/api/config/brightness', {method: 'POST', body: JSON.stringify({brightness: draft.brightness})});
            if (changes.has('targetFps')) await requestJson(actions.target_fps_url || '/api/config/target-fps', {method: 'POST', body: JSON.stringify({target_fps: draft.targetFps})});
            if (changes.has('speed')) await requestJson(actions.operator_speed_url || '/api/config/animation-speed', {method: 'POST', body: JSON.stringify({multiplier: draft.speedMultiplier})});
            state.globalSettings.observed = clone(draft);
            state.globalSettings.dirty = false;
            state.globalSettings.pendingObservation = true;
            state.globalSettings.pendingSince = Date.now();
            persistGlobalDraft();
            toast('Wall-wide commands sent; waiting for observed state.', 'success');
        } catch (error) {
            if (error.code === 'offline') setServerOnline(false);
            toast(`Wall update stopped: ${error.message}`, 'error');
            state.globalSettings.pendingObservation = false;
            state.globalSettings.pendingSince = 0;
            await refreshGlobalSettings({quiet: true, preserveDraft: true});
        } finally {
            state.globalSettings.applying = false;
            renderGlobalSettings();
        }
    }

    function previewElapsed(component, elapsed) {
        if (component?.presentation?.timing_adapter === 'wall_clock') return elapsed;
        const profile = vibeProfile();
        return elapsed * safeNumber(profile.tempo_scale, 1) * safeNumber(state.globalSettings.draft?.speedMultiplier, 1);
    }

    function presentedFrame(frame) {
        if (!frame) return frame;
        const profile = vibeProfile();
        const brightness = safeNumber(state.globalSettings.draft?.brightness, 255) / 255;
        const multiplier = Math.max(0, Math.min(1, safeNumber(profile.luminance_scale, 1) * brightness));
        if (Math.abs(multiplier - 1) < .001) return frame;
        const pixels = new Uint8Array(frame.pixels.length);
        for (let offset = 0; offset < frame.pixels.length; offset += 1) {
            pixels[offset] = Math.round(frame.pixels[offset] * multiplier);
        }
        return {...frame, pixels};
    }

    function setServerOnline(online, {checking = false, quiet = false} = {}) {
        state.serverOnline = Boolean(online);
        state.serverChecking = checking;
        const pill = $('serverState');
        pill.dataset.state = checking ? 'checking' : online ? 'online' : 'offline';
        pill.querySelector('span').textContent = checking ? 'Checking server' : online ? 'Server online' : 'Local only';
        $('serverActionBadge').textContent = online ? 'Server online' : 'Local only';
        if ($('networkStatus')) {
            $('networkStatus').textContent = checking
                ? 'Checking the wall server…'
                : online ? 'Wall server online; library save and checked activation are available.' : 'Local only; server save and wall activation are disabled.';
            $('networkStatus').dataset.state = checking ? 'checking' : online ? 'online' : 'offline';
        }
        if (!quiet && !state.busyAction) {
            $('serverActionStatus').textContent = online
                ? 'Server actions are available. Local preview remains separate from the physical wall.'
                : 'Offline: local drafts, checks, uploads, and downloads still work. Save and Activate are unavailable.';
        }
        updateServerActionButtons();
        renderGlobalSettings();
        if (state.masks.loaded) updateMaskControls();
    }

    function componentCapability(component = state.component) {
        if (ComposerState.capability) return ComposerState.capability(component);
        return {
            previewable: Boolean(component?.browser_runtime?.supported),
            saveable: Boolean(component?.browser_runtime?.supported),
            activationReady: Boolean(component?.browser_runtime?.supported),
            reason: null,
            managedIdentity: null,
        };
    }

    function currentCheckBinding() {
        if (!state.component || !state.bootstrap) return null;
        return ComposerState.checkBinding
            ? ComposerState.checkBinding(
                state.draftGeneration,
                state.component,
                state.bootstrap.geometry,
                state.globalSettings.draft,
                state.bootstrap.installation_profile?.digest || null,
            )
            : {
                draftGeneration: state.draftGeneration,
                componentKey: state.component.key,
                wallSettings: clone(state.globalSettings.draft),
                installationProfileDigest: state.bootstrap.installation_profile?.digest || null,
            };
    }

    function currentCheckAllowsActivation() {
        const expected = currentCheckBinding();
        if (ComposerState.checkAllowsActivation) {
            return ComposerState.checkAllowsActivation(state.checkResult, expected);
        }
        return Boolean(
            state.checkResult
            && ['pass', 'warn'].includes(state.checkResult.status)
            && state.checkResult.binding?.draftGeneration === expected?.draftGeneration
        );
    }

    function activationBlockReason() {
        if (!state.component) return 'Choose a look before activation.';
        const capability = componentCapability();
        if (!capability.activationReady) return capability.reason || 'This look is not activation-ready.';
        if (state.serverChecking) return 'Waiting for the wall server.';
        if (!state.serverOnline) return 'Reconnect to the wall server before activation.';
        if (state.globalSettings.loading || state.globalSettings.applying) return 'Wait for wall-wide settings to finish updating.';
        if (state.globalSettings.dirty) return 'Apply or revert the Wall draft before activating this checked scene.';
        if (state.globalSettings.pendingObservation) return 'Wait until the wall reports the reviewed Wall settings before activation.';
        if (!state.checkResult) return 'Run Check for this exact draft before activation.';
        if (!currentCheckAllowsActivation()) return ['pass', 'warn'].includes(state.checkResult.status)
            ? 'The previous Check is stale. Run Check again for this draft.'
            : 'Activation requires a completed Check with no failures for this exact draft.';
        return null;
    }

    function updateServerActionButtons() {
        const capability = componentCapability();
        const saveEnabled = Boolean(state.component && capability.saveable && state.serverOnline && !state.serverChecking && !state.busyAction);
        ['saveLibraryButton', 'saveLibraryPanelButton'].forEach((id) => {
            $(id).disabled = !saveEnabled;
        });
        const blockReason = activationBlockReason();
        ['activateButton', 'activatePanelButton'].forEach((id) => {
            $(id).disabled = Boolean(blockReason || state.busyAction);
            $(id).title = blockReason || 'Review this checked draft before activating it on the wall.';
            $(id).setAttribute('aria-disabled', String(Boolean(blockReason || state.busyAction)));
        });
        const reason = $('activationReadiness');
        if (reason) {
            reason.textContent = blockReason || (state.checkResult?.status === 'warn'
                ? 'Activation-ready with cautions: review this exact checked draft before continuing.'
                : 'Activation-ready: this exact draft passed Check.');
            reason.dataset.state = blockReason ? 'blocked' : 'ready';
        }
    }

    function setActionBusy(action, busy) {
        state.busyAction = busy ? action : null;
        const ids = action === 'activate'
            ? ['activateButton', 'activatePanelButton']
            : ['saveLibraryButton', 'saveLibraryPanelButton'];
        ids.forEach((id) => $(id).dataset.busy = String(busy));
        $('layersPanel').setAttribute('aria-busy', String(busy));
        updateServerActionButtons();
    }

    async function checkConnectivity({quiet = false} = {}) {
        if (!state.bootstrap) return;
        try {
            const url = state.bootstrap.capabilities?.server_actions?.connectivity_url || '/api/v1/composer/connectivity';
            const payload = await requestJson(url);
            setServerOnline(payload.online === true, {quiet});
            if (payload.online === true) await Promise.all([
                refreshGlobalSettings({quiet: true, preserveDraft: true}),
                preloadMasks(),
            ]);
        } catch (_error) {
            setServerOnline(false, {quiet});
        }
    }

    function showOfflineReadiness(payload) {
        const status = $('offlineReadiness');
        if (!status) return;
        const ready = payload?.readyOffline === true;
        status.dataset.state = ready ? 'ready' : 'not-ready';
        status.textContent = ready ? 'Ready offline' : (payload?.reason || 'Offline assets are not ready yet.');
        const button = $('prepareOfflineButton');
        if (button) {
            button.textContent = ready ? 'Refresh offline assets' : 'Prepare for offline use';
            button.disabled = false;
        }
    }

    async function refreshOfflineReadiness() {
        if (typeof ComposerRuntime?.offlineStatus !== 'function') return;
        try {
            showOfflineReadiness(await ComposerRuntime.offlineStatus());
        } catch (error) {
            showOfflineReadiness({readyOffline: false, reason: error.message});
        }
    }

    async function prepareOffline() {
        const button = $('prepareOfflineButton');
        if (!button || button.disabled) return;
        button.disabled = true;
        button.textContent = 'Preparing local assets…';
        $('offlineReadiness').dataset.state = 'preparing';
        $('offlineReadiness').textContent = 'Caching and verifying the pinned browser runtime and animation assets…';
        let temporary = null;
        try {
            let runtime = runtimeKind(state.component) === 'python' ? state.runtimes.draft : null;
            if (!runtime?.ready) {
                const component = (state.bootstrap?.components || []).find((item) => (
                    item.provider === 'python' && item.role === 'background' && item.browser_runtime?.supported
                ));
                if (!component) throw new Error('No browser-ready Python animation is available for offline preparation.');
                temporary = new ComposerRuntime(component, state.bootstrap.geometry, {initTimeoutMs: 90000});
                await temporary.init(defaultParams(component));
                runtime = temporary;
            }
            showOfflineReadiness(await runtime.prepareOffline());
        } catch (error) {
            showOfflineReadiness({readyOffline: false, reason: `Offline preparation failed: ${error.message}`});
            toast('Offline preparation could not finish.', 'error');
        } finally {
            temporary?.dispose();
            button.disabled = false;
        }
    }

    function updateInstallStatus() {
        const standalone = window.matchMedia?.('(display-mode: standalone)').matches || navigator.standalone === true;
        if (!$('installStatus')) return;
        $('installStatus').dataset.state = standalone ? 'installed' : 'browser';
        $('installStatus').textContent = standalone
            ? 'Running as the installed wall composer.'
            : 'Running in a browser tab; installation is optional.';
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
        if (!/^[0-9a-f]{64}$/.test(payload.installation_profile?.digest || '')) {
            throw new Error('The composer catalog has no managed installation-profile identity.');
        }
        payload.components.forEach((component) => {
            const capabilities = component.browser_capabilities;
            if (!capabilities || ['previewable', 'saveable', 'activation_ready'].some((key) => typeof capabilities[key] !== 'boolean')) {
                throw new Error(`The composer catalog has no capability contract for ${component.key || component.plugin_id}.`);
            }
        });
        return payload;
    }

    async function loadBootstrap() {
        const response = await fetch('/api/v1/composer/bootstrap', {headers: {'Accept': 'application/json'}});
        if (!response.ok) throw new Error(`Catalog request failed (${response.status}).`);
        state.bootstrap = assertBootstrap(await response.json());
        initializeGlobalSettings();
        configureCanvas();
        renderCatalog();
        const lastKey = localStorage.getItem(`${STORAGE_PREFIX}.last-component`);
        const preferred = state.bootstrap.components.find((item) => item.key === lastKey && item.role === 'background' && componentCapability(item).previewable)
            || state.bootstrap.components.find((item) => item.role === 'background' && componentCapability(item).previewable);
        if (preferred) await selectComponent(preferred);
        else showCatalogUnavailable('No components currently declare a supported browser runtime.');
        await checkConnectivity();
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
        const presetMetadata = (component.presets || []).flatMap((preset) => [
            preset.name,
            preset.description,
            preset.category,
            preset.preset_id,
            ...(Array.isArray(preset.tags) ? preset.tags : []),
        ]);
        const haystack = [component.name, component.description, component.plugin_id, component.provider, component.role, ...presetMetadata]
            .filter(Boolean).join(' ').toLowerCase();
        return component.role === 'background' && filterMatches && haystack.includes(state.query.toLowerCase());
    }

    function enableRovingFocus(host, selector, {vertical = true} = {}) {
        const items = [...host.querySelectorAll(selector)].filter((item) => !item.disabled);
        if (!items.length) return;
        let active = Math.max(0, items.findIndex((item) => item.getAttribute('aria-selected') === 'true' || item.getAttribute('aria-current') === 'true'));
        items.forEach((item, index) => { item.tabIndex = index === active ? 0 : -1; });
        host.onkeydown = (event) => {
            const previousKeys = vertical ? ['ArrowUp', 'ArrowLeft'] : ['ArrowLeft', 'ArrowUp'];
            const nextKeys = vertical ? ['ArrowDown', 'ArrowRight'] : ['ArrowRight', 'ArrowDown'];
            const current = Math.max(0, items.indexOf(document.activeElement));
            let next = null;
            if (previousKeys.includes(event.key)) next = (current - 1 + items.length) % items.length;
            else if (nextKeys.includes(event.key)) next = (current + 1) % items.length;
            else if (event.key === 'Home') next = 0;
            else if (event.key === 'End') next = items.length - 1;
            if (next == null) return;
            event.preventDefault();
            items.forEach((item, index) => { item.tabIndex = index === next ? 0 : -1; });
            items[next].focus();
            if (items[next].getAttribute('role') === 'tab') items[next].click();
        };
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
            empty.textContent = state.component && !matchesCatalog(state.component)
                ? `Editing ${state.component.name || humanize(state.component.plugin_id)}, hidden by filters.`
                : 'No animations or starting points match that search.';
            host.appendChild(empty);
            const clear = document.createElement('button');
            clear.type = 'button';
            clear.className = 'text-button';
            clear.textContent = 'Clear filters';
            clear.addEventListener('click', () => {
                state.catalogFilter = 'all';
                state.query = '';
                $('componentSearch').value = '';
                document.querySelectorAll('[data-filter]').forEach((item) => item.setAttribute('aria-pressed', String(item.dataset.filter === 'all')));
                renderCatalog();
            });
            host.appendChild(clear);
            return;
        }
        visible.forEach((component) => {
            const runtime = component.browser_runtime || {};
            const capability = componentCapability(component);
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'component-card';
            button.setAttribute('role', 'option');
            button.setAttribute('aria-selected', String(component.key === state.component?.key));
            button.disabled = !capability.previewable;
            button.dataset.activationReady = String(capability.activationReady);
            if (!capability.previewable) button.title = capability.reason || runtime.reason || 'Browser rendering is unavailable.';
            else if (!capability.activationReady) button.title = capability.reason || 'Preview and save only; activation is unavailable.';

            const icon = document.createElement('span');
            icon.className = 'component-icon';
            icon.setAttribute('aria-hidden', 'true');
            icon.textContent = component.icon || (runtime.kind === 'native' ? '⚙' : '✦');
            const copy = document.createElement('span');
            copy.className = 'component-copy';
            const name = document.createElement('strong');
            name.textContent = component.name || humanize(component.plugin_id);
            const meta = document.createElement('small');
            meta.textContent = capability.previewable
                ? `${component.role ? humanize(component.role) + ' · ' : ''}${runtime.kind === 'native' ? 'C++ → Wasm' : 'Python → Pyodide'} · ${capability.activationReady ? 'Activation-ready' : 'Preview only'}`
                : (runtime.reason || 'Browser runtime unavailable');
            copy.append(name, meta);
            const chip = document.createElement('span');
            chip.className = `runtime-chip${capability.previewable ? '' : ' unsupported'}`;
            chip.textContent = capability.previewable ? (runtime.kind === 'native' ? 'Wasm' : 'Py') : 'Server';
            button.append(icon, copy, chip);
            button.addEventListener('click', () => selectComponent(component));
            host.appendChild(button);
        });
        enableRovingFocus(host, '.component-card');
    }

    function presetParams(preset) {
        return clone(preset?.params || preset?.parameters || preset?.parameter_overrides || {});
    }

    function presetIdentity(preset, index = 0) {
        return preset?.key || preset?.preset_id || preset?.id || `preset-${index}`;
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
            button.setAttribute('role', 'option');
            button.setAttribute('aria-selected', String(state.selectedPreset === presetIdentity(preset, index)));
            const name = document.createElement('strong');
            name.textContent = preset.name || humanize(presetIdentity(preset, index));
            const description = document.createElement('small');
            description.textContent = preset.description || preset.category || 'Curated starting point';
            button.append(name, description);
            button.addEventListener('click', () => applyPreset(preset, index));
            host.appendChild(button);
        });
        host.setAttribute('role', 'listbox');
        host.setAttribute('aria-label', 'Starting points for the current animation');
        enableRovingFocus(host, '.preset-button');
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
        return enforceInstallationParams(component, result);
    }

    function enforceInstallationParams(component, params) {
        const result = clone(params || {});
        const schema = component?.parameter_schema || {};
        const modifiers = state.globalSettings.draft?.plantModifiers
            || state.bootstrap?.installation_profile?.plant_modifiers;
        if (schema.plant_modifiers && modifiers) result.plant_modifiers = clone(modifiers);
        if (schema.plant_aware && modifiers) result.plant_aware = Boolean(modifiers.active?.length);
        return result;
    }

    function authoredParams(component, params) {
        const schema = component?.parameter_schema || {};
        return Object.fromEntries(Object.entries(clone(params || {})).filter(([key]) => (
            Object.prototype.hasOwnProperty.call(schema, key) && !isGlobalInstallationParameter(key)
        )));
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

    function clockComponent() {
        return state.bootstrap?.components.find((item) => item.provider === 'python' && item.plugin_id === 'clock_overlay' && item.role === 'overlay') || null;
    }

    function pythonFallbacks() {
        return (state.bootstrap?.components || []).filter((item) => (
            item.provider === 'python'
            && item.role === 'background'
            && componentCapability(item).activationReady
        ));
    }

    function normalizedLayers(component, savedLayers = null) {
        const existing = savedLayers && typeof savedLayers === 'object' ? savedLayers : {};
        const fallbackCandidates = pythonFallbacks();
        const selfFallback = component.provider === 'python' ? component.key : null;
        const requestedFallback = fallbackCandidates.find((item) => item.key === existing.fallbackKey)?.key;
        const fallbackKey = selfFallback || requestedFallback || fallbackCandidates[0]?.key || null;
        return {
            clockEnabled: Boolean(existing.clockEnabled && clockComponent()),
            clockOpacity: Math.max(0, Math.min(255, Math.round(safeNumber(existing.clockOpacity, 220)))),
            clockParams: enforceInstallationParams(clockComponent() || {}, {
                ...defaultParams(clockComponent() || {}),
                ...(existing.clockParams && typeof existing.clockParams === 'object' ? clone(existing.clockParams) : {}),
            }),
            clockPresetKey: typeof existing.clockPresetKey === 'string' ? existing.clockPresetKey : '',
            fallbackKey,
        };
    }

    function renderLayers() {
        const component = state.component;
        if (!component) return;
        if ($('installationProfileStatus')) {
            const digest = state.bootstrap.installation_profile?.digest || '';
            $('installationProfileStatus').textContent = `Plant geometry is authoritative host state · profile ${digest.slice(0, 12)}… · presets cannot override it.`;
        }
        $('backgroundLayerIcon').textContent = component.icon || '✦';
        $('backgroundLayerName').textContent = component.name || humanize(component.plugin_id);
        $('backgroundLayerMeta').textContent = `${component.provider === 'receiver_native' ? 'Receiver C++' : 'Host Python'} · fixed background`;
        const clock = clockComponent();
        $('clockEnabled').disabled = !clock;
        $('clockEnabled').checked = Boolean(state.layers.clockEnabled && clock);
        $('clockOptions').hidden = !state.layers.clockEnabled;
        $('clockLayerCard').classList.toggle('is-enabled', state.layers.clockEnabled);
        $('clockOpacity').value = String(state.layers.clockOpacity);
        $('clockOpacityValue').textContent = `${Math.round(state.layers.clockOpacity / 255 * 100)}%`;
        renderClockControls();

        const select = $('fallbackSelect');
        select.replaceChildren();
        pythonFallbacks().forEach((item) => {
            const option = document.createElement('option');
            option.value = item.key;
            option.textContent = item.name || humanize(item.plugin_id);
            option.selected = item.key === state.layers.fallbackKey;
            select.appendChild(option);
        });
        select.disabled = component.provider === 'python';
        $('fallbackHelp').textContent = component.provider === 'python'
            ? 'This Python background is also its required safety fallback.'
            : 'Receiver C++ backgrounds require a real Host Python fallback before activation.';
        updateServerActionButtons();
    }

    function renderClockControls() {
        const clock = clockComponent();
        const presetSelect = $('clockPresetSelect');
        const host = $('clockParameterList');
        presetSelect.replaceChildren();
        const custom = document.createElement('option');
        custom.value = '';
        custom.textContent = 'Custom';
        presetSelect.appendChild(custom);
        [...(clock?.presets || []), ...CLOCK_STARTING_POINTS].forEach((preset, index) => {
            const option = document.createElement('option');
            option.value = presetIdentity(preset, index);
            option.textContent = preset.name || humanize(option.value);
            presetSelect.appendChild(option);
        });
        presetSelect.value = state.layers.clockPresetKey || '';
        host.replaceChildren();
        const entries = Object.entries(clock?.parameter_schema || {}).filter(([key]) => !isGlobalInstallationParameter(key));
        entries.filter(([key, contract]) => !isAdvancedParameter(key, contract)).forEach(([key, contract]) => {
            host.appendChild(parameterControl(key, contract || {}, {
                params: state.layers.clockParams,
                prefix: 'clock-parameter',
                onUpdate: updateClockParam,
                onCommit: commitHistory,
            }));
        });
        const advancedHost = $('advancedParameterList');
        advancedHost?.querySelectorAll('.clock-advanced-control').forEach((item) => item.remove());
        entries.filter(([key, contract]) => isAdvancedParameter(key, contract)).forEach(([key, contract]) => {
            const control = parameterControl(key, contract || {}, {
                params: state.layers.clockParams,
                prefix: 'clock-advanced-parameter',
                onUpdate: updateClockParam,
                onCommit: commitHistory,
            });
            control.classList.add('clock-advanced-control');
            advancedHost?.appendChild(control);
        });
        if ($('advancedParameterEmpty')) $('advancedParameterEmpty').hidden = Boolean(advancedHost?.children.length);
    }

    function updateClockParam(key, value) {
        if (JSON.stringify(state.layers.clockParams[key]) === JSON.stringify(value)) return;
        state.layers.clockParams[key] = value;
        state.lastSavedPreset = null;
        state.layers.clockPresetKey = '';
        $('clockPresetSelect').value = '';
        resetChecker();
        scheduleAutosave();
        requestRender();
    }

    function applyClockPreset(presetId) {
        if (!presetId) return;
        const clock = clockComponent();
        const presets = [...(clock?.presets || []), ...CLOCK_STARTING_POINTS];
        const preset = presets.find((item, index) => presetIdentity(item, index) === presetId);
        if (!preset) return;
        state.layers.clockParams = enforceInstallationParams(clock, {...defaultParams(clock), ...presetParams(preset)});
        state.layers.clockPresetKey = presetId;
        renderClockControls();
        $('clockPresetSelect').value = presetId;
        state.lastSavedPreset = null;
        resetChecker();
        commitHistory();
        scheduleAutosave();
        requestRender();
        toast(`Clock starting point: ${preset.name || humanize(presetId)}.`);
    }

    async function selectComponent(component, options = {}) {
        if (!componentCapability(component).previewable) return;
        if (state.component?.key === component.key && !options.force) return;
        state.component = component;
        state.selectedPreset = null;
        localStorage.setItem(`${STORAGE_PREFIX}.last-component`, component.key);
        const saved = options.ignoreAutosave ? null : loadAutosave(component);
        const defaults = defaultParams(component);
        state.params = enforceInstallationParams(component, saved?.params || defaults);
        state.originalParams = enforceInstallationParams(component, saved?.original_params || defaults);
        state.layers = normalizedLayers(component, saved?.layers);
        state.documentRevision = Number.isInteger(saved?.document_revision)
            ? saved.document_revision
            : state.documentRevision;
        state.lastSavedPreset = null;
        $('presetName').value = saved?.name || `${component.name || humanize(component.plugin_id)} draft`;
        state.elapsed = 0;
        state.frameIndex = 0;
        state.frames = {draft: null, original: null, overlay: null, composed: null};
        resetChecker();
        updateComponentCopy();
        renderCatalog();
        renderPresets();
        renderParameterControls();
        renderLayers();
        setComposerEnabled(true);
        if (options.historyMode === 'preserve') updateHistoryButtons();
        else if (options.historyMode === 'commit' || (options.historyMode == null && state.history.length)) commitHistory();
        else resetHistory();
        if (!options.deferRuntime) await startRuntimes();
        scheduleAutosave();
        requestRender();
    }

    function applyPreset(preset, index) {
        state.selectedPreset = presetIdentity(preset, index);
        state.lastSavedPreset = null;
        const params = enforceInstallationParams(state.component, {...defaultParams(state.component), ...presetParams(preset)});
        state.params = clone(params);
        state.originalParams = clone(params);
        $('presetName').value = preset.name || humanize(state.selectedPreset);
        state.elapsed = 0;
        state.frameIndex = 0;
        state.frames = {draft: null, original: null, overlay: null, composed: null};
        renderPresets();
        renderParameterControls();
        resetChecker();
        commitHistory();
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
        updateServerActionButtons();
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
            state.runtimes = {draft, original: null, overlay: null};
            await draft.init(state.params);
            if (state.compare !== 'draft') await ensureOriginalRuntime();
            if (generation !== state.runtimeGeneration) return;
            const engine = draft.engine || state.component.browser_runtime.kind;
            setEngineState('ready', 'Local renderer', engine);
            $('playButton').disabled = false;
            $('timeline').disabled = false;
            $('runCheckerButton').disabled = false;
            state.needsRender = true;
            if (state.layers.clockEnabled) {
                ensureOverlayRuntime().then(requestRender).catch((error) => {
                    $('clockPreviewNote').textContent = `Clock is configured for activation; local layer preview is unavailable: ${error.message}`;
                    toast('Background preview is ready; the Clock layer could not start locally.', 'error');
                });
            }
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
        if (state.overlayMode === 'shared' && typeof state.runtimes.draft?.disposeInstance === 'function') {
            state.runtimes.draft.disposeInstance('clock_overlay');
        }
        state.runtimes.overlay?.dispose();
        state.runtimes.draft?.dispose();
        state.runtimes.original?.dispose();
        state.runtimes = {draft: null, original: null, overlay: null};
        state.originalRuntimePromise = null;
        state.overlayRuntimePromise = null;
        state.overlayMode = null;
    }

    function disposeOverlayRuntime() {
        if (state.overlayMode === 'shared' && typeof state.runtimes.draft?.disposeInstance === 'function') {
            state.runtimes.draft.disposeInstance('clock_overlay');
        }
        state.runtimes.overlay?.dispose();
        state.runtimes.overlay = null;
        state.overlayRuntimePromise = null;
        state.overlayMode = null;
        state.frames.overlay = null;
        state.frames.composed = null;
    }

    function ensureOverlayRuntime() {
        if (!state.layers.clockEnabled) return Promise.resolve(null);
        if (state.overlayMode === 'shared') return Promise.resolve(state.runtimes.draft);
        if (state.runtimes.overlay?.ready) return Promise.resolve(state.runtimes.overlay);
        if (state.overlayRuntimePromise) return state.overlayRuntimePromise;
        const clock = clockComponent();
        if (!clock) return Promise.reject(new Error('Clock overlay is not in the component catalog.'));
        const generation = state.runtimeGeneration;
        $('clockPreviewNote').textContent = 'Preparing the Clock layer locally…';

        if (runtimeKind(state.component) === 'python' && typeof state.runtimes.draft?.initInstance === 'function') {
            state.overlayRuntimePromise = state.runtimes.draft
                .initInstance(clock, clone(state.layers.clockParams), 'clock_overlay')
                .then(() => {
                    if (generation !== state.runtimeGeneration || !state.layers.clockEnabled) throw new Error('Clock layer was replaced.');
                    state.overlayMode = 'shared';
                    $('clockPreviewNote').textContent = 'Clock composed locally in the same Python worker to conserve memory.';
                    return state.runtimes.draft;
                });
        } else if (runtimeKind(state.component) === 'native') {
            const runtime = new ComposerRuntime(clock, state.bootstrap.geometry, {initTimeoutMs: 90000});
            state.runtimes.overlay = runtime;
            state.overlayRuntimePromise = runtime.init(clone(state.layers.clockParams)).then(() => {
                if (generation !== state.runtimeGeneration || !state.layers.clockEnabled) {
                    runtime.dispose();
                    throw new Error('Clock layer was replaced.');
                }
                state.overlayMode = 'separate';
                $('clockPreviewNote').textContent = 'Clock composed in a separate Python worker because the background is native C++.';
                return runtime;
            });
        } else {
            $('clockPreviewNote').textContent = 'Clock is configured for activation. This runtime does not yet expose local layer instances.';
            return Promise.reject(new Error('Same-worker layer rendering is not available yet.'));
        }
        state.overlayRuntimePromise = state.overlayRuntimePromise.finally(() => {
            state.overlayRuntimePromise = null;
        });
        return state.overlayRuntimePromise;
    }

    async function renderOverlayFrame(elapsed, frameIndex) {
        if (!state.layers.clockEnabled) return null;
        const runtime = await ensureOverlayRuntime();
        if (!runtime) return null;
        const wallTime = Date.now() / 1000;
        const frame = state.overlayMode === 'shared'
            ? await runtime.renderInstance('clock_overlay', elapsed, frameIndex, clone(state.layers.clockParams), wallTime)
            : await runtime.render(elapsed, frameIndex, clone(state.layers.clockParams));
        return validatedFrame(frame);
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
        const advancedHost = $('advancedParameterList');
        host.replaceChildren();
        advancedHost?.replaceChildren();
        const schema = state.component?.parameter_schema || {};
        const entries = Object.entries(schema);
        const authoredEntries = entries.filter(([key]) => !isGlobalInstallationParameter(key));
        const creative = authoredEntries.filter(([key, contract]) => !isAdvancedParameter(key, contract));
        const advanced = authoredEntries.filter(([key, contract]) => isAdvancedParameter(key, contract));
        $('parameterEmpty').hidden = creative.length > 0;
        if ($('advancedParameterEmpty')) $('advancedParameterEmpty').hidden = advanced.length > 0;
        creative.forEach(([key, contract]) => host.appendChild(parameterControl(key, contract || {})));
        advanced.forEach(([key, contract]) => advancedHost?.appendChild(parameterControl(key, contract || {})));
    }

    function isAdvancedParameter(key, contract = {}) {
        if (contract.advanced === true || contract.installation === true || contract.visibility === 'advanced') return true;
        return /(^|_)(plant|mask|path|modifier|diagnostic|runtime|geometry|calibration|strip_map|led_map)(_|$)/i.test(key);
    }

    function isGlobalInstallationParameter(key) {
        return ['plant_modifiers', 'plant_aware', 'installation_profile', 'installation_profile_digest'].includes(key);
    }

    function parameterControl(key, contract, context = {}) {
        const params = context.params || state.params;
        const prefix = context.prefix || 'parameter';
        const applyValue = context.onUpdate || updateParam;
        const commit = context.onCommit || commitHistory;
        const type = String(contract.type || typeof params[key]).toLowerCase();
        const wrapper = document.createElement('div');
        wrapper.className = 'parameter-control';
        const labelRow = document.createElement('div');
        labelRow.className = 'parameter-label-row';
        const label = document.createElement('label');
        label.htmlFor = `${prefix}-${key}`;
        label.textContent = humanize(key);
        const readout = document.createElement('output');
        readout.className = 'parameter-value';
        readout.htmlFor = `${prefix}-${key}`;
        labelRow.append(label, readout);
        wrapper.appendChild(labelRow);

        let input;
        if (type === 'bool' || type === 'boolean') {
            wrapper.classList.add('switch-control');
            input = document.createElement('input');
            input.type = 'checkbox';
            input.checked = Boolean(params[key]);
            input.setAttribute('role', 'switch');
            readout.textContent = input.checked ? 'On' : 'Off';
            input.addEventListener('change', () => {
                applyValue(key, input.checked);
                readout.textContent = input.checked ? 'On' : 'Off';
                commit();
            });
        } else if ((contract.options || contract.enum) && Array.isArray(contract.options || contract.enum)) {
            input = document.createElement('select');
            const options = contract.options || contract.enum;
            options.forEach((value) => {
                const option = document.createElement('option');
                option.value = String(value);
                option.textContent = humanize(value);
                option.selected = String(params[key]) === String(value);
                input.appendChild(option);
            });
            readout.textContent = humanize(params[key]);
            input.addEventListener('change', () => {
                const option = options.find((candidate) => String(candidate) === input.value);
                applyValue(key, option ?? input.value);
                readout.textContent = humanize(input.value);
                commit();
            });
        } else if (['float', 'number', 'int', 'integer'].includes(type) && contract.min != null && contract.max != null) {
            const range = document.createElement('div');
            range.className = 'range-wrap';
            input = document.createElement('input');
            input.type = 'range';
            input.min = String(contract.min);
            input.max = String(contract.max);
            input.step = String(contract.step ?? (type === 'int' || type === 'integer' ? 1 : sensibleStep(contract.min, contract.max)));
            input.value = String(params[key] ?? contract.default ?? contract.min);
            const number = document.createElement('input');
            number.type = 'number';
            number.min = input.min;
            number.max = input.max;
            number.step = input.step;
            number.value = input.value;
            number.setAttribute('aria-label', `${humanize(key)} precise value`);
            const applyNumeric = (raw) => {
                const value = numericValue(raw, type, {...contract, step: Number(input.step)});
                input.value = String(value);
                number.value = formatParameterValue(value, input.step);
                readout.textContent = formatParameterValue(value, input.step);
                applyValue(key, value);
                return value;
            };
            const commitNumeric = (source) => {
                applyNumeric(source.value);
                commit();
            };
            input.addEventListener('input', () => applyNumeric(input.value));
            input.addEventListener('change', () => commitNumeric(input));
            input.addEventListener('blur', () => commitNumeric(input));
            number.addEventListener('change', () => commitNumeric(number));
            number.addEventListener('blur', () => commitNumeric(number));
            number.addEventListener('keydown', (event) => {
                if (event.key !== 'Enter') return;
                event.preventDefault();
                commitNumeric(number);
                number.select();
            });
            readout.textContent = formatParameterValue(input.value, input.step);
            range.append(input, number);
            wrapper.appendChild(range);
        } else if (type === 'object' || typeof params[key] === 'object') {
            input = document.createElement('textarea');
            input.value = JSON.stringify(params[key] ?? contract.default ?? {}, null, 2);
            readout.textContent = 'JSON';
            input.addEventListener('change', () => {
                try {
                    const parsed = JSON.parse(input.value);
                    input.removeAttribute('aria-invalid');
                    applyValue(key, parsed);
                    commit();
                } catch (_error) {
                    input.setAttribute('aria-invalid', 'true');
                    toast(`${humanize(key)} must be valid JSON.`, 'error');
                }
            });
        } else {
            input = document.createElement('input');
            input.type = ['float', 'number', 'int', 'integer'].includes(type) ? 'number' : 'text';
            input.value = params[key] ?? contract.default ?? '';
            if (contract.min != null) input.min = contract.min;
            if (contract.max != null) input.max = contract.max;
            input.step = type === 'int' || type === 'integer' ? '1' : 'any';
            readout.textContent = String(input.value || '—');
            input.addEventListener('change', () => {
                const value = input.type === 'number' ? numericValue(input.value, type, contract) : input.value;
                applyValue(key, value);
                readout.textContent = String(value || '—');
                commit();
            });
        }
        input.id = `${prefix}-${key}`;
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
        if (ComposerState.normalizeNumber) return ComposerState.normalizeNumber(raw, type, contract);
        let value = type === 'int' || type === 'integer' ? Math.round(Number(raw)) : Number(raw);
        if (!Number.isFinite(value)) value = safeNumber(contract.default);
        if (contract.min != null) value = Math.max(Number(contract.min), value);
        if (contract.max != null) value = Math.min(Number(contract.max), value);
        return value;
    }

    function formatParameterValue(value, step) {
        if (ComposerState.formatNumber) return ComposerState.formatNumber(value, step);
        const numeric = Number(value);
        const precision = String(step).includes('.') ? Math.min(3, String(step).split('.')[1].length) : 0;
        return Number.isFinite(numeric) ? numeric.toFixed(precision) : String(value);
    }

    function updateParam(key, value) {
        if (JSON.stringify(state.params[key]) === JSON.stringify(value)) return;
        state.params[key] = value;
        state.selectedPreset = null;
        state.lastSavedPreset = null;
        renderPresets();
        resetChecker();
        scheduleAutosave();
        requestRender();
    }

    function snapshot() {
        return {
            componentKey: state.component?.key || null,
            params: clone(state.params),
            originalParams: clone(state.originalParams),
            selectedPreset: state.selectedPreset,
            name: $('presetName').value,
            layers: clone(state.layers),
            documentRevision: state.documentRevision,
        };
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

    async function restoreHistory(index) {
        const entry = state.history[index];
        if (!entry) return;
        const component = state.bootstrap?.components.find((item) => item.key === entry.componentKey);
        if (!component) {
            toast('That history entry uses a renderer that is no longer in the catalog.', 'error');
            return;
        }
        const componentChanged = component.key !== state.component?.key;
        state.historyIndex = index;
        state.component = component;
        state.params = clone(entry.params);
        state.originalParams = clone(entry.originalParams);
        const clockWasEnabled = state.layers.clockEnabled;
        state.layers = normalizedLayers(state.component, entry.layers);
        $('presetName').value = entry.name;
        state.selectedPreset = entry.selectedPreset;
        state.documentRevision = entry.documentRevision;
        state.lastSavedPreset = null;
        updateComponentCopy();
        renderCatalog();
        renderParameterControls();
        renderPresets();
        renderLayers();
        resetChecker({preserveDocumentRevision: true});
        updateHistoryButtons();
        scheduleAutosave();
        if (componentChanged) await startRuntimes();
        else {
            if (clockWasEnabled && !state.layers.clockEnabled) disposeOverlayRuntime();
            if (!clockWasEnabled && state.layers.clockEnabled) ensureOverlayRuntime().then(requestRender).catch(() => {});
        }
        requestRender();
    }

    function updateHistoryButtons() {
        $('undoButton').disabled = !state.component || state.historyIndex <= 0;
        $('redoButton').disabled = !state.component || state.historyIndex >= state.history.length - 1;
    }

    function scheduleAutosave() {
        if (!state.component) return;
        $('saveState').textContent = 'Autosaving draft locally…';
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
                layers: state.layers,
                document_revision: state.documentRevision,
                draft_generation: state.draftGeneration,
                saved_at: new Date().toISOString(),
            };
            try {
                localStorage.setItem(`${STORAGE_PREFIX}.draft.${state.component.key}`, JSON.stringify(payload));
                $('saveState').textContent = 'Draft autosaved locally';
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
                requests.push(state.runtimes.draft.render(previewElapsed(state.component, elapsed), frameIndex, enforceInstallationParams(state.component, state.params)).then((frame) => ['draft', frame]));
            }
            if (state.compare !== 'draft') {
                requests.push(state.runtimes.original.render(previewElapsed(state.component, elapsed), frameIndex, enforceInstallationParams(state.component, state.originalParams)).then((frame) => ['original', frame]));
            }
            const results = await Promise.all(requests);
            if (generation !== state.runtimeGeneration) return;
            results.forEach(([kind, frame]) => { state.frames[kind] = validatedFrame(frame); });
            state.frames.composed = null;
            if (state.frames.draft && state.layers.clockEnabled && state.compare !== 'original') {
                try {
                    state.frames.overlay = await renderOverlayFrame(elapsed, frameIndex);
                    state.frames.composed = composeDraftFrame(state.frames.draft, state.frames.overlay);
                } catch (overlayError) {
                    state.frames.overlay = null;
                    $('clockPreviewNote').textContent = `Clock remains configured for activation; local layer preview is unavailable: ${overlayError.message}`;
                }
            }
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
        const pixels = frame.pixels instanceof ArrayBuffer
            ? new Uint8Array(frame.pixels)
            : ArrayBuffer.isView(frame.pixels) ? new Uint8Array(frame.pixels.buffer, frame.pixels.byteOffset, frame.pixels.byteLength) : null;
        const frameFormat = frame.frameFormat || frame.format || 'rgb';
        const channels = frameFormat === 'premultiplied-rgba' ? 4 : frameFormat === 'rgb' ? 3 : 0;
        if (!pixels || !channels || !Number.isInteger(width) || !Number.isInteger(height) || pixels.length !== width * height * channels) {
            throw new Error(`Renderer returned an invalid ${frameFormat} frame.`);
        }
        return {...frame, width, height, pixels, frameFormat};
    }

    function composeDraftFrame(background, overlay) {
        const compositor = window.LEDGridComposerCompositor;
        if (!overlay || typeof compositor?.composeLayers !== 'function') return null;
        if (overlay.width !== background.width || overlay.height !== background.height) {
            throw new Error('Clock layer geometry does not match the background.');
        }
        const pixels = compositor.composeLayers({
            width: background.width,
            height: background.height,
            layers: [
                {pixels: background.pixels, format: 'rgb', blend: 'replace', enabled: true, opacity: 255},
                {
                    pixels: overlay.pixels,
                    format: overlay.frameFormat,
                    blend: 'source-over',
                    enabled: true,
                    opacity: state.layers.clockOpacity,
                },
            ],
        });
        if (!(pixels instanceof Uint8Array) || pixels.length !== background.width * background.height * 3) {
            throw new Error('Local compositor returned an invalid RGB frame.');
        }
        return {...background, pixels, frameFormat: 'rgb', engine: `${background.engine || 'browser'} + local compositor`};
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
        const draftFrame = presentedFrame(state.frames.composed || state.frames.draft);
        const originalFrame = presentedFrame(state.frames.original);
        if (state.compare === 'draft' && draftFrame) {
            context.putImageData(canonicalImageData(draftFrame), 0, 0);
        } else if (state.compare === 'original' && originalFrame) {
            context.putImageData(canonicalImageData(originalFrame), 0, 0);
        } else if (state.compare === 'split' && originalFrame && draftFrame) {
            const left = frameCanvas(originalFrame);
            const right = frameCanvas(draftFrame);
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

    function schemaCheck(component, params, {requireAll = true} = {}) {
        const problems = [];
        const schema = component.parameter_schema || {};
        Object.keys(params || {}).filter((key) => !Object.prototype.hasOwnProperty.call(schema, key)).forEach((key) => {
            problems.push(`${humanize(key)} is not a declared parameter`);
        });
        Object.entries(schema).forEach(([key, contract]) => {
            const value = params[key];
            if (value === undefined) {
                if (requireAll) problems.push(`${humanize(key)} is missing`);
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

    function resetChecker({preserveDocumentRevision = false} = {}) {
        state.draftGeneration += 1;
        if (!preserveDocumentRevision) state.documentRevision += 1;
        state.checkerGeneration += 1;
        state.checkResult = null;
        $('checkerDot').removeAttribute('data-state');
        $('checkSummary').dataset.grade = 'idle';
        $('checkGauge').textContent = '—';
        $('checkHeadline').textContent = 'Not checked yet';
        $('checkSummaryCopy').textContent = 'Run a local sample after tuning.';
        document.querySelectorAll('[data-metric]').forEach((row) => {
            delete row.dataset.state;
            row.querySelector('dd').textContent = 'Waiting';
            const detail = row.querySelector('small');
            if (!detail.dataset.defaultDescription) detail.dataset.defaultDescription = detail.textContent;
            detail.textContent = detail.dataset.defaultDescription;
        });
        $('checkerProgress').hidden = true;
        $('runCheckerButton').disabled = !state.component || !state.runtimes.draft?.ready;
        updateServerActionButtons();
    }

    async function runChecker() {
        if (!state.component || !ComposerRuntime) return;
        const generation = ++state.checkerGeneration;
        const binding = currentCheckBinding();
        state.checkResult = null;
        updateServerActionButtons();
        const button = $('runCheckerButton');
        button.disabled = true;
        $('checkerProgress').hidden = false;
        $('checkerProgressBar').value = 0;
        $('checkerProgressValue').textContent = '0%';
        $('checkerProgressLabel').textContent = 'Preparing isolated renderer…';
        const backgroundSchemaProblems = schemaCheck(state.component, state.params).map((problem) => `Background: ${problem}`);
        const clock = state.layers.clockEnabled ? clockComponent() : null;
        const clockSchemaProblems = clock
            ? schemaCheck(clock, state.layers.clockParams).map((problem) => `Clock: ${problem}`)
            : state.layers.clockEnabled ? ['Clock: component is unavailable'] : [];
        const schemaProblems = [...backgroundSchemaProblems, ...clockSchemaProblems];
        const runtime = new ComposerRuntime(state.component, state.bootstrap.geometry, {timeoutMs: 30000});
        let overlayRuntime = null;
        let overlayMode = null;
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
            try {
                await runtime.init(clone(state.params));
            } catch (error) {
                throw new Error(`Background renderer failed to initialize: ${error.message}`);
            }
            if (state.layers.clockEnabled) {
                if (!clock) throw new Error('Clock renderer failed to initialize: component is unavailable.');
                $('checkerProgressLabel').textContent = 'Preparing Clock layer…';
                if (runtimeKind(state.component) === 'python') {
                    if (typeof runtime.initInstance !== 'function') {
                        throw new Error('Clock renderer failed to initialize: same-worker layer rendering is unavailable.');
                    }
                    try {
                        await runtime.initInstance(clock, clone(state.layers.clockParams), 'clock_overlay');
                        overlayMode = 'shared';
                    } catch (error) {
                        throw new Error(`Clock renderer failed to initialize: ${error.message}`);
                    }
                } else {
                    overlayRuntime = new ComposerRuntime(clock, state.bootstrap.geometry, {timeoutMs: 30000, initTimeoutMs: 90000});
                    try {
                        await overlayRuntime.init(clone(state.layers.clockParams));
                        overlayMode = 'separate';
                    } catch (error) {
                        throw new Error(`Clock renderer failed to initialize: ${error.message}`);
                    }
                }
            }
            for (let index = 0; index < SAMPLE_FRAMES; index += 1) {
                if (generation !== state.checkerGeneration) throw new Error('Check replaced.');
                const sampleStarted = performance.now();
                let backgroundFrame;
                try {
                    backgroundFrame = validatedFrame(await runtime.render(
                        previewElapsed(state.component, index / 12),
                        index,
                        enforceInstallationParams(state.component, state.params),
                    ));
                } catch (error) {
                    throw new Error(`Background renderer failed at frame ${index + 1}: ${error.message}`);
                }
                let frame = backgroundFrame;
                if (state.layers.clockEnabled) {
                    let overlayFrame;
                    try {
                        const response = overlayMode === 'shared'
                            ? await runtime.renderInstance('clock_overlay', index / 12, index, clone(state.layers.clockParams), Date.now() / 1000)
                            : await overlayRuntime.render(index / 12, index, clone(state.layers.clockParams));
                        overlayFrame = validatedFrame(response);
                    } catch (error) {
                        throw new Error(`Clock renderer failed at frame ${index + 1}: ${error.message}`);
                    }
                    try {
                        frame = composeDraftFrame(backgroundFrame, overlayFrame);
                        if (!frame) throw new Error('local compositor is unavailable');
                    } catch (error) {
                        throw new Error(`Layer compositor failed at frame ${index + 1}: ${error.message}`);
                    }
                }
                frame = presentedFrame(frame);
                const pixels = frame.pixels;
                renderTimes.push(performance.now() - sampleStarted);
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
                $('checkerProgressLabel').textContent = `${state.layers.clockEnabled ? 'Sampling composed' : 'Sampling'} frame ${completed} of ${SAMPLE_FRAMES}`;
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

            const targetFps = Math.max(1, safeNumber(state.globalSettings.draft?.targetFps, 30));
            const frameBudgetMs = 1000 / targetFps;
            const renderStatus = p95 > frameBudgetMs ? 'fail' : p95 > frameBudgetMs * .5 ? 'warn' : 'pass';
            updateMetric('render', `${p95.toFixed(2)} ms`, renderStatus, `${state.layers.clockEnabled ? 'Background + Clock + compositor' : runtime.engine}; ${frameBudgetMs.toFixed(2)} ms budget at ${targetFps} fps, measured on this browser rather than receiver hardware.`);
            if (renderStatus === 'fail') failures.push('render time'); else if (renderStatus === 'warn') warnings.push('render time');

            const grade = failures.length ? 'fail' : warnings.length ? 'warn' : 'pass';
            const score = Math.max(0, 100 - failures.length * 24 - warnings.length * 8);
            $('checkSummary').dataset.grade = grade;
            $('checkGauge').textContent = String(score);
            $('checkerDot').dataset.state = grade;
            $('checkHeadline').textContent = grade === 'pass' ? 'Local sample looks healthy' : grade === 'warn' ? 'Review a few cautions' : 'Resolve local check failures';
            $('checkSummaryCopy').textContent = failures.length
                ? `Failed: ${failures.join(', ')}.`
                : warnings.length ? `Review: ${warnings.join(', ')}.` : `${SAMPLE_FRAMES} ${state.layers.clockEnabled ? 'composed ' : ''}frames passed the local heuristics.`;
            if (generation === state.checkerGeneration) {
                state.checkResult = {
                    status: grade,
                    binding,
                    warnings: warnings.slice(),
                    failures: failures.slice(),
                    completedAt: new Date().toISOString(),
                };
                updateServerActionButtons();
            }
            $('saveState').textContent = 'Draft autosaved locally';
        } catch (error) {
            if (generation === state.checkerGeneration && error.message !== 'Check replaced.') {
                $('checkSummary').dataset.grade = 'fail';
                $('checkGauge').textContent = '!';
                $('checkHeadline').textContent = 'Checker could not finish';
                $('checkSummaryCopy').textContent = error.message;
                $('checkerDot').dataset.state = 'fail';
                state.checkResult = {status: 'fail', binding, completedAt: new Date().toISOString(), error: error.message};
                updateServerActionButtons();
            }
        } finally {
            if (overlayMode === 'shared' && typeof runtime.disposeInstance === 'function') runtime.disposeInstance('clock_overlay');
            overlayRuntime?.dispose();
            runtime.dispose();
            if (generation === state.checkerGeneration) {
                $('checkerProgress').hidden = true;
                button.disabled = false;
            }
        }
    }

    function presetIdForName(name) {
        let presetId = String(name || '').toLowerCase().replace(/[^a-z0-9_-]+/g, '_').replace(/^_+|_+$/g, '') || 'browser_draft';
        if (!/^[a-z]/.test(presetId)) presetId = `preset_${presetId}`;
        return presetId.slice(0, 64);
    }

    function currentPresetRecord() {
        if (state.lastSavedPreset) return state.lastSavedPreset;
        return (state.component?.presets || []).find((preset, index) => presetIdentity(preset, index) === state.selectedPreset) || null;
    }

    function componentReference(component, params, preset = null) {
        const identity = component?.browser_capabilities?.managed_identity;
        if (!identity) throw new Error('This component has no catalog-managed browser identity.');
        const reference = {
            provider: identity.provider,
            component_id: identity.component_id,
            component_digest: identity.component_digest,
            runtime_digest: identity.runtime_digest,
            parameter_schema_version: identity.parameter_schema_version,
            parameters: authoredParams(component, params),
        };
        const presetId = preset?.preset_id;
        const fingerprint = preset?.preset_fingerprint;
        if (presetId && fingerprint) {
            reference.preset_id = presetId;
            reference.preset_fingerprint = fingerprint;
        }
        return reference;
    }

    function buildScene(backgroundPreset = currentPresetRecord()) {
        if (!state.component) throw new Error('Choose a background first.');
        const background = componentReference(state.component, state.params, backgroundPreset);
        const fallback = pythonFallbacks().find((item) => item.key === state.layers.fallbackKey);
        if (!fallback) throw new Error('Choose an available Host Python fallback.');
        const knownFallback = state.component.provider === 'python'
            ? clone(background)
            : componentReference(fallback, defaultParams(fallback));
        const layers = [];
        if (state.layers.clockEnabled) {
            const clock = clockComponent();
            if (!clock) throw new Error('Clock overlay is not available in the component catalog.');
            layers.push({
                role: 'clock',
                component: componentReference(clock, state.layers.clockParams),
                enabled: true,
                opacity: state.layers.clockOpacity,
                blend_mode: 'source_over',
            });
        }
        const profileDigest = state.bootstrap.installation_profile?.digest;
        if (!/^[0-9a-f]{64}$/.test(profileDigest || '')) {
            throw new Error('The host did not provide a managed installation-profile identity.');
        }
        return {
            schema: 'ledgrid.browser-scene',
            schema_version: 1,
            revision: state.documentRevision,
            background,
            layers,
            installation_profile: {digest: profileDigest},
            fallback: knownFallback,
        };
    }

    function scenePresetDocument() {
        const name = $('presetName').value.trim() || `${state.component.name} scene`;
        return {
            schema: 'ledgrid.scene-preset',
            schema_version: 1,
            preset_id: presetIdForName(name),
            name,
            description: 'Authored in the browser composer; local preview is not physical-wall observation.',
            scene: buildScene(),
        };
    }

    function exportedDocument() {
        return buildScene();
    }

    async function saveToLibrary({overwrite = false} = {}) {
        if (!state.component || state.busyAction) return;
        if (!state.serverOnline) {
            toast('Offline: the draft is saved on this device, but the library is unavailable.', 'error');
            return;
        }
        setActionBusy('save', true);
        $('serverActionStatus').textContent = 'Validating and saving the component preset…';
        try {
            const url = state.bootstrap.capabilities?.server_actions?.save_component_preset_url || '/api/v1/composer/presets';
            const result = await requestJson(url, {
                method: 'POST',
                body: JSON.stringify({
                    schema: 'ledgrid.browser-composer-save',
                    schema_version: 1,
                    component_key: state.component.key,
                    name: $('presetName').value.trim(),
                    description: 'Authored and locally previewed in the browser composer.',
                    params: authoredParams(state.component, state.params),
                    overwrite,
                }),
            });
            state.lastSavedPreset = {...clone(result.preset), preset_fingerprint: result.preset_fingerprint};
            const existingIndex = state.component.presets.findIndex((item) => item.preset_id === result.preset.preset_id);
            const record = {
                ...clone(result.preset),
                preset_fingerprint: result.preset_fingerprint,
                key: `${state.component.key}:${result.preset.preset_id}`,
                component_key: state.component.key,
            };
            if (existingIndex >= 0) state.component.presets.splice(existingIndex, 1, record);
            else state.component.presets.push(record);
            state.selectedPreset = record.key;
            renderPresets();

            $('serverActionStatus').textContent = 'Component saved. Saving the exact browser scene document…';
            try {
                const sceneUrl = state.bootstrap.capabilities?.server_actions?.save_scene_preset_url || '/api/v1/scene-presets';
                const scene = buildScene(state.lastSavedPreset);
                await requestJson(sceneUrl, {
                    method: 'POST',
                    body: JSON.stringify({
                        name: $('presetName').value.trim(),
                        description: 'Versioned browser scene authored and previewed locally; not physically observed.',
                        scene,
                    }),
                });
                $('serverActionStatus').textContent = 'Saved the component preset and exact scene revision to the server library. The physical wall was not changed.';
                toast('Look and scene saved to the library.', 'success');
            } catch (sceneError) {
                $('serverActionStatus').textContent = `Component preset saved; scene revision was not saved: ${sceneError.message}`;
                toast('The component saved, but the scene revision needs attention.', 'error');
            }
        } catch (error) {
            if (error.status === 409 && error.code === 'preset_exists' && !overwrite) {
                $('overwriteCopy').textContent = `“${$('presetName').value.trim()}” already exists in the server library. Replacing it will not change the physical wall.`;
                $('overwriteDialog').showModal();
                $('serverActionStatus').textContent = 'A preset with this name already exists. Choose whether to replace it.';
            } else {
                if (error.code === 'offline') setServerOnline(false);
                $('serverActionStatus').textContent = `Library save failed: ${error.message}`;
                toast(error.message, 'error');
            }
        } finally {
            setActionBusy('save', false);
        }
    }

    function reviewActivation() {
        const blockReason = activationBlockReason();
        if (blockReason) {
            toast(blockReason, 'error');
            $('serverActionStatus').textContent = `Activation is not ready: ${blockReason}`;
            return;
        }
        try {
            buildScene();
        } catch (error) {
            toast(error.message, 'error');
            $('serverActionStatus').textContent = `Activation is not ready: ${error.message}`;
            return;
        }
        const fallback = pythonFallbacks().find((item) => item.key === state.layers.fallbackKey);
        $('activateBackground').textContent = state.component.name || humanize(state.component.plugin_id);
        $('activateOverlay').textContent = state.layers.clockEnabled ? `Clock · ${Math.round(state.layers.clockOpacity / 255 * 100)}%` : 'Off';
        $('activateFallback').textContent = fallback?.name || 'Unavailable';
        if ($('activateProvider')) $('activateProvider').textContent = state.component.provider;
        if ($('activateRuntimeDigest')) $('activateRuntimeDigest').textContent = ComposerState.runtimeDigest?.(state.component) || 'Catalog identity';
        if ($('activateRevision')) $('activateRevision').textContent = String(state.documentRevision);
        if ($('activateCheck')) {
            const outcome = state.checkResult?.status === 'warn' ? 'Passed with cautions' : 'Passed';
            const cautions = state.checkResult?.warnings?.length
                ? ` · review ${state.checkResult.warnings.join(', ')}`
                : '';
            $('activateCheck').textContent = `${outcome}${cautions} · draft generation ${state.draftGeneration}`;
        }
        if ($('activateDestination')) $('activateDestination').textContent = 'Physical living wall';
        $('activateDialog').showModal();
    }

    async function activateScene() {
        if (state.busyAction) return;
        setActionBusy('activate', true);
        $('serverActionStatus').textContent = 'Validating the exact background, Clock slot, and Python fallback…';
        try {
            const blockReason = activationBlockReason();
            if (blockReason) throw new Error(blockReason);
            const scene = buildScene();
            const validateUrl = state.bootstrap.capabilities?.server_actions?.validate_scene_url || '/api/v1/scene/validate';
            await requestJson(validateUrl, {method: 'POST', body: JSON.stringify(scene)});
            const activateUrl = state.bootstrap.capabilities?.server_actions?.activate_scene_url || '/api/v1/scene';
            const result = await requestJson(activateUrl, {method: 'PUT', body: JSON.stringify(scene)});
            const receipt = result.receipt || {};
            const acceptance = receipt.command_accepted ? 'Queued by server' : 'Command not queued';
            const observation = receipt.observed_status === 'observed'
                ? 'server reports observed live state'
                : 'live state not observed';
            const telemetry = receipt.telemetry_complete ? 'telemetry complete' : 'telemetry incomplete';
            const camera = receipt.camera_observation ? 'camera evidence attached' : 'no camera observation';
            $('serverActionStatus').textContent = `${acceptance} · revision ${receipt.requested_revision ?? scene.revision} · ${observation} · ${telemetry} · ${camera}.`;
            toast(receipt.command_accepted
                ? 'Activation command queued; controller and physical observation remain separate.'
                : 'The wall server did not queue the activation command.', receipt.command_accepted ? 'success' : 'error');
        } catch (error) {
            if (error.code === 'offline') setServerOnline(false);
            $('serverActionStatus').textContent = `Activation was not accepted: ${error.message}`;
            toast(error.message, 'error');
        } finally {
            setActionBusy('activate', false);
        }
    }

    async function copyJson() {
        const text = JSON.stringify(exportedDocument(), null, 2);
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
        const preset = exportedDocument();
        const blob = new Blob([`${JSON.stringify(preset, null, 2)}\n`], {type: 'application/json'});
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = `${presetIdForName($('presetName').value)}.json`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(link.href);
        toast('Versioned browser scene downloaded.');
    }

    function locallyValidatedImport(payload) {
        assertSafeImport(payload);
        const browserScene = payload?.schema === 'ledgrid.browser-scene'
            ? payload
            : payload?.schema === 'ledgrid.scene-preset' && payload.scene?.schema === 'ledgrid.browser-scene'
                ? payload.scene
                : null;
        if (browserScene) {
            assertOnlyKeys(browserScene, ['schema', 'schema_version', 'revision', 'background', 'layers', 'installation_profile', 'fallback'], 'browser scene');
            if (browserScene.schema_version !== 1) throw new Error('The uploaded browser scene uses an unsupported version.');
            if (!Number.isInteger(browserScene.revision) || browserScene.revision < 0) throw new Error('The uploaded browser scene has an invalid revision.');
            const background = locallyValidatedBrowserReference(browserScene.background, 'background');
            if (!Array.isArray(browserScene.layers) || browserScene.layers.length > 1) {
                throw new Error('Version 1 browser scenes allow at most one Clock layer.');
            }
            const layer = browserScene.layers[0];
            if (layer) assertOnlyKeys(layer, ['role', 'component', 'enabled', 'opacity', 'blend_mode'], 'browser scene Clock layer');
            if (layer && (
                layer.role !== 'clock'
                || layer.blend_mode !== 'source_over'
                || typeof layer.enabled !== 'boolean'
                || !Number.isInteger(layer.opacity)
                || layer.opacity < 0
                || layer.opacity > 255
            )) throw new Error('The uploaded browser scene has an invalid fixed Clock layer.');
            if (layer) {
                const clock = locallyValidatedBrowserReference(layer.component, 'overlay');
                if (clock.plugin_id !== 'clock_overlay') throw new Error('The fixed browser scene layer must use Clock.');
            }
            const fallback = locallyValidatedBrowserReference(browserScene.fallback, 'background');
            if (fallback.provider !== 'python') throw new Error('The browser scene fallback must use Host Python.');
            if (background.provider === 'python' && (
                fallback.plugin_id !== background.plugin_id
                || ComposerState.stableJson(browserScene.fallback.parameters) !== ComposerState.stableJson(browserScene.background.parameters)
            )) throw new Error('A Python browser scene background must also be its exact fallback.');
            assertOnlyKeys(browserScene.installation_profile, ['digest'], 'browser scene installation profile');
            if (!/^[0-9a-f]{64}$/.test(browserScene.installation_profile?.digest || '')) {
                throw new Error('The browser scene has no managed installation-profile digest.');
            }
            return {
                kind: 'browser_scene',
                draft: {
                    component_key: background.key,
                    name: payload.name || 'Imported browser scene',
                    description: payload.description || '',
                    params: clone(browserScene.background.parameters),
                    browser_scene: clone(browserScene),
                },
            };
        }
        if (payload?.schema === 'ledgrid.scene-preset' && payload.schema_version === 1 && payload.scene?.schema === 'ledgrid.scene-state') {
            if (payload.scene.schema_version !== 1) throw new Error('The uploaded scene uses an unsupported version.');
            if (!Array.isArray(payload.scene.overlays) || payload.scene.overlays.length > 1) {
                throw new Error('Version 1 scenes allow only the fixed Clock overlay slot.');
            }
            if (payload.scene.overlays.some((layer) => layer?.slot_id !== 'clock_overlay')) {
                throw new Error('The uploaded scene contains an unsupported layer role.');
            }
            const background = payload.scene.background;
            const componentKey = `${background?.provider}:${background?.plugin_id}`;
            const component = state.bootstrap.components.find((item) => item.key === componentKey && item.role === 'background');
            if (!component) throw new Error('The uploaded scene background is not in this catalog.');
            const overlay = payload.scene.overlays[0];
            if (overlay && (
                overlay.component?.provider !== 'python'
                || overlay.component?.plugin_id !== 'clock_overlay'
                || !['source-over', undefined].includes(overlay.blend_mode)
            )) throw new Error('Version 1 scenes support only the fixed Python Clock source-over overlay.');
            const fallback = payload.scene.known_python_fallback;
            if (!fallback || fallback.provider !== 'python' || !state.bootstrap.components.some((item) => (
                item.provider === fallback.provider && item.plugin_id === fallback.plugin_id && item.role === 'background'
            ))) throw new Error('The uploaded scene requires a catalogued Host Python fallback.');
            return {
                kind: 'scene_preset',
                draft: {
                    component_key: componentKey,
                    name: payload.name || 'Imported scene',
                    description: payload.description || '',
                    params: {...(background?.resolved_parameters || {}), ...(background?.parameter_overrides || {})},
                    scene: payload.scene,
                },
            };
        }
        if (!payload || typeof payload !== 'object' || !payload.params || typeof payload.params !== 'object') {
            throw new Error('Upload a component preset or ledgrid.scene-preset JSON document.');
        }
        if (payload.version !== 2) throw new Error('The uploaded component preset uses an unsupported version.');
        const pluginId = payload.animation || payload.plugin_id;
        const provider = payload.provider;
        const matches = state.bootstrap.components.filter((item) => item.plugin_id === pluginId && (!provider || item.provider === provider));
        if (matches.length !== 1) throw new Error('The uploaded preset does not identify one provider-qualified component.');
        return {
            kind: 'component_preset',
            draft: {
                component_key: matches[0].key,
                name: payload.name || humanize(payload.preset_id || pluginId),
                description: payload.description || '',
                params: clone(payload.params),
            },
        };
    }

    function locallyValidatedBrowserReference(reference, role) {
        if (!reference || typeof reference !== 'object') throw new Error(`The uploaded scene ${role} is invalid.`);
        assertOnlyKeys(reference, ['provider', 'component_id', 'component_digest', 'runtime_digest', 'parameter_schema_version', 'parameters', 'preset_id', 'preset_fingerprint'], `browser scene ${role}`);
        if ((reference.preset_id == null) !== (reference.preset_fingerprint == null)) {
            throw new Error(`The uploaded scene ${role} preset ID and fingerprint must appear together.`);
        }
        if (reference.preset_id != null && (
            !/^[a-z][a-z0-9_-]{0,63}$/.test(reference.preset_id)
            || !/^[0-9a-f]{64}$/.test(reference.preset_fingerprint)
        )) throw new Error(`The uploaded scene ${role} preset identity is invalid.`);
        if (!reference.parameters || typeof reference.parameters !== 'object' || Array.isArray(reference.parameters)) {
            throw new Error(`The uploaded scene ${role} parameters must be an object.`);
        }
        const key = `${reference.provider}:${reference.component_id}`;
        const component = state.bootstrap.components.find((item) => item.key === key && item.role === role);
        if (!component) throw new Error(`The uploaded scene ${role} is not in this catalog.`);
        const capability = componentCapability(component);
        if (!capability.previewable) throw new Error(capability.reason || `The uploaded scene ${role} is not previewable.`);
        const identity = component.browser_capabilities?.managed_identity || {};
        for (const field of ['provider', 'component_id', 'component_digest', 'runtime_digest', 'parameter_schema_version']) {
            if (reference[field] !== identity[field]) throw new Error(`The uploaded scene ${role} ${field} does not match the catalog.`);
        }
        const problems = schemaCheck(component, reference.parameters, {requireAll: false});
        if (problems.length) throw new Error(`The uploaded scene ${role} is invalid: ${problems[0]}.`);
        return component;
    }

    function assertOnlyKeys(value, allowed, label) {
        if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`The uploaded ${label} must be an object.`);
        const unexpected = Object.keys(value).find((key) => !allowed.includes(key));
        if (unexpected) throw new Error(`The uploaded ${label} contains an unsupported field: ${unexpected}.`);
    }

    function assertSafeImport(value, depth = 0, budget = {nodes: 0}) {
        budget.nodes += 1;
        if (budget.nodes > 4096) throw new Error('The uploaded document contains too many values.');
        if (depth > 16) throw new Error('The uploaded document is nested too deeply.');
        if (typeof value === 'number' && !Number.isFinite(value)) throw new Error('The uploaded document contains a non-finite number.');
        if (typeof value === 'string' && new TextEncoder().encode(value).byteLength > 16384) throw new Error('The uploaded document contains an oversized text value.');
        if (Array.isArray(value)) {
            value.forEach((item) => assertSafeImport(item, depth + 1, budget));
            return;
        }
        if (!value || typeof value !== 'object') return;
        Object.entries(value).forEach(([key, item]) => {
            if (['__proto__', 'prototype', 'constructor'].includes(key)) {
                throw new Error(`The uploaded document contains a forbidden key: ${key}.`);
            }
            assertSafeImport(item, depth + 1, budget);
        });
    }

    async function applyImportedDraft(validated) {
        const draft = validated.draft || {};
        const component = state.bootstrap.components.find((item) => item.key === draft.component_key);
        if (!component) throw new Error('The uploaded background is not in this composer catalog.');
        if (component.role !== 'background') throw new Error('A scene background must use a background component.');
        if (!componentCapability(component).previewable) throw new Error(componentCapability(component).reason || 'That background cannot render in this browser.');
        await selectComponent(component, {
            force: true,
            ignoreAutosave: true,
            deferRuntime: true,
            historyMode: 'preserve',
        });
        state.params = enforceInstallationParams(component, {...defaultParams(component), ...clone(draft.params || {})});
        state.originalParams = clone(state.params);
        $('presetName').value = draft.name || `${component.name} draft`;

        const browserScene = draft.browser_scene;
        if (browserScene?.schema === 'ledgrid.browser-scene') {
            const clock = (browserScene.layers || []).find((item) => item.role === 'clock' && item.enabled);
            const fallback = browserScene.fallback;
            state.documentRevision = browserScene.revision;
            state.layers = normalizedLayers(component, {
                clockEnabled: Boolean(clock),
                clockOpacity: clock?.opacity ?? 220,
                clockParams: clock ? clone(clock.component?.parameters || {}) : {},
                fallbackKey: fallback ? `${fallback.provider}:${fallback.component_id}` : null,
            });
        } else if (validated.kind === 'scene_preset') {
            const scene = draft.scene;
            const clock = (scene.overlays || []).find((item) => item.slot_id === 'clock_overlay' && item.enabled);
            const fallback = scene.known_python_fallback;
            state.layers = normalizedLayers(component, {
                clockEnabled: Boolean(clock),
                clockOpacity: clock?.opacity ?? 220,
                clockParams: clock ? {
                    ...(clock.component?.resolved_parameters || {}),
                    ...(clock.component?.parameter_overrides || {}),
                } : {},
                fallbackKey: fallback ? `${fallback.provider}:${fallback.plugin_id}` : null,
            });
        }
        const problems = schemaCheck(component, state.params);
        if (problems.length) throw new Error(`Uploaded preset is invalid: ${problems[0]}.`);
        if (state.layers.clockEnabled) {
            const clockProblems = schemaCheck(clockComponent(), state.layers.clockParams);
            if (clockProblems.length) throw new Error(`Uploaded Clock layer is invalid: ${clockProblems[0]}.`);
        }
        renderParameterControls();
        renderLayers();
        resetChecker({preserveDocumentRevision: Boolean(browserScene)});
        commitHistory();
        await startRuntimes();
        scheduleAutosave();
    }

    async function importJson(file) {
        try {
            if (file.size > 256 * 1024) throw new Error('Upload a JSON document no larger than 256 KB.');
            const source = await file.text();
            const payload = JSON.parse(source);
            assertSafeImport(payload);
            let validated;
            let serverValidated = false;
            if (state.serverOnline) {
                const url = state.bootstrap.capabilities?.server_actions?.validate_import_url || '/api/v1/composer/presets/validate';
                try {
                    validated = await requestJson(url, {method: 'POST', body: JSON.stringify(payload)});
                    serverValidated = true;
                } catch (error) {
                    if (error.code !== 'offline') throw error;
                    setServerOnline(false);
                    validated = locallyValidatedImport(payload);
                }
            } else {
                validated = locallyValidatedImport(payload);
            }
            await applyImportedDraft(validated);
            $('serverActionStatus').textContent = serverValidated
                ? 'Upload validated by the server and opened as a local draft. The physical wall was not changed.'
                : 'Upload checked locally and opened as a draft. Server validation is pending until you reconnect.';
            toast(serverValidated ? 'Preset validated and opened locally.' : 'Preset opened locally; server validation is pending.');
        } catch (error) {
            if (error.code === 'offline') setServerOnline(false);
            toast(error.message, 'error');
        } finally {
            $('importFile').value = '';
        }
    }

    function maskCounts(cells = state.masks.cells) {
        const counts = {foliage: 0, planter_bowls: 0};
        if (!cells) return counts;
        for (const value of cells) {
            if (value === 1) counts.foliage += 1;
            else if (value === 2) counts.planter_bowls += 1;
        }
        return counts;
    }

    function masksEqual(left, right) {
        if (!left || !right || left.length !== right.length) return false;
        for (let index = 0; index < left.length; index += 1) {
            if (left[index] !== right[index]) return false;
        }
        return true;
    }

    function updateMaskControls(message = null, kind = '') {
        const masks = state.masks;
        const counts = maskCounts();
        if (masks.ledInfo) {
            $('maskGeometry').textContent = `${masks.ledInfo.strip_count} × ${masks.ledInfo.leds_per_strip}`;
        }
        $('foliageMaskCount').textContent = masks.loaded ? counts.foliage.toLocaleString() : '—';
        $('bowlMaskCount').textContent = masks.loaded ? counts.planter_bowls.toLocaleString() : '—';
        $('editorFoliageCount').textContent = counts.foliage.toLocaleString();
        $('editorBowlCount').textContent = counts.planter_bowls.toLocaleString();
        $('undoMaskButton').disabled = !masks.history.length;
        $('revertMasksButton').disabled = !masks.dirty;
        $('saveMasksButton').disabled = !masks.dirty || !state.serverOnline;
        document.querySelectorAll('[data-mask-tool]').forEach((button) => {
            button.setAttribute('aria-pressed', String(Number(button.dataset.maskTool) === masks.tool));
        });
        if (message) {
            $('maskEditorStatus').textContent = message;
            $('maskEditorStatus').dataset.state = kind;
        }
    }

    function loadMaskPayload(payload) {
        const info = payload?.led_info || {};
        const stripCount = Number(info.strip_count);
        const ledsPerStrip = Number(info.leds_per_strip);
        const totalLeds = Number(info.total_leds);
        if (!Number.isInteger(stripCount) || !Number.isInteger(ledsPerStrip) || totalLeds !== stripCount * ledsPerStrip) {
            throw new Error('Mask calibration contains invalid geometry.');
        }
        const cells = new Uint8Array(totalLeds);
        for (const index of payload.masks?.foliage || []) {
            if (Number.isInteger(index) && index >= 0 && index < totalLeds) cells[index] = 1;
        }
        for (const index of payload.masks?.planter_bowls || []) {
            if (!Number.isInteger(index) || index < 0 || index >= totalLeds) continue;
            if (cells[index] === 1) throw new Error(`Calibrated mask layers overlap at pixel ${index}.`);
            cells[index] = 2;
        }
        state.masks.ledInfo = {strip_count: stripCount, leds_per_strip: ledsPerStrip, total_leds: totalLeds};
        state.masks.cells = cells;
        state.masks.savedCells = cells.slice();
        state.masks.history = [];
        state.masks.dirty = false;
        state.masks.loaded = true;
    }

    async function preloadMasks() {
        if (state.masks.loaded) return;
        try {
            const payload = await requestJson(globalActions().masks_url || '/api/painter/masks');
            loadMaskPayload(payload);
            updateMaskControls();
        } catch (_error) {
            // Mask editing remains an explicit, retryable action when unavailable.
        }
    }

    async function openMaskEditor() {
        $('maskEditorDialog').showModal();
        if (state.masks.loaded) {
            renderMaskCanvas();
            updateMaskControls(state.masks.dirty ? 'Unsaved mask draft restored.' : 'Calibrated masks ready. The wall is unchanged.');
            return;
        }
        updateMaskControls('Loading calibrated masks…');
        try {
            const payload = await requestJson(globalActions().masks_url || '/api/painter/masks');
            loadMaskPayload(payload);
            renderMaskCanvas();
            updateMaskControls('Calibrated masks ready. The wall is unchanged.', 'success');
        } catch (error) {
            updateMaskControls(error.message, 'error');
        }
    }

    function renderMaskCanvas() {
        const {cells, ledInfo, zoom} = state.masks;
        if (!cells || !ledInfo) return;
        const canvas = $('maskCanvas');
        const width = ledInfo.strip_count;
        const height = ledInfo.leds_per_strip;
        canvas.width = width * zoom;
        canvas.height = height * zoom;
        const context = canvas.getContext('2d', {alpha: false});
        context.fillStyle = '#080808';
        context.fillRect(0, 0, canvas.width, canvas.height);
        for (let strip = 0; strip < width; strip += 1) {
            for (let led = 0; led < height; led += 1) {
                const value = cells[strip * height + led];
                if (!value) continue;
                context.fillStyle = value === 1 ? '#35c86f' : '#ff9f43';
                context.fillRect(strip * zoom, (height - 1 - led) * zoom, zoom, zoom);
            }
        }
        if (zoom >= 6) {
            context.strokeStyle = 'rgba(255,255,255,.07)';
            context.lineWidth = 1;
            context.beginPath();
            for (let x = 0; x <= width; x += 1) {
                context.moveTo(x * zoom + .5, 0);
                context.lineTo(x * zoom + .5, canvas.height);
            }
            for (let y = 0; y <= height; y += 1) {
                context.moveTo(0, y * zoom + .5);
                context.lineTo(canvas.width, y * zoom + .5);
            }
            context.stroke();
        }
    }

    function maskCellFromPointer(event) {
        const canvas = $('maskCanvas');
        const rect = canvas.getBoundingClientRect();
        const x = Math.floor((event.clientX - rect.left) * canvas.width / rect.width / state.masks.zoom);
        const y = Math.floor((event.clientY - rect.top) * canvas.height / rect.height / state.masks.zoom);
        if (!state.masks.ledInfo || x < 0 || y < 0 || x >= state.masks.ledInfo.strip_count || y >= state.masks.ledInfo.leds_per_strip) return null;
        return {strip: x, led: state.masks.ledInfo.leds_per_strip - 1 - y};
    }

    function paintMaskLine(from, to) {
        if (!from || !to || !state.masks.cells) return;
        let x0 = from.strip;
        let y0 = from.led;
        const x1 = to.strip;
        const y1 = to.led;
        const dx = Math.abs(x1 - x0);
        const sx = x0 < x1 ? 1 : -1;
        const dy = -Math.abs(y1 - y0);
        const sy = y0 < y1 ? 1 : -1;
        let error = dx + dy;
        while (true) {
            state.masks.cells[x0 * state.masks.ledInfo.leds_per_strip + y0] = state.masks.tool;
            if (x0 === x1 && y0 === y1) break;
            const doubled = 2 * error;
            if (doubled >= dy) { error += dy; x0 += sx; }
            if (doubled <= dx) { error += dx; y0 += sy; }
        }
        state.masks.dirty = !masksEqual(state.masks.cells, state.masks.savedCells);
        renderMaskCanvas();
        updateMaskControls(state.masks.dirty ? 'Unsaved mask calibration draft.' : 'Draft matches saved calibration.');
    }

    function beginMaskStroke(event) {
        if (!state.masks.cells || event.button !== 0) return;
        const cell = maskCellFromPointer(event);
        if (!cell) return;
        state.masks.history.push(state.masks.cells.slice());
        if (state.masks.history.length > 60) state.masks.history.shift();
        state.masks.painting = true;
        state.masks.lastCell = cell;
        event.currentTarget.setPointerCapture(event.pointerId);
        paintMaskLine(cell, cell);
    }

    function continueMaskStroke(event) {
        if (!state.masks.painting) return;
        const cell = maskCellFromPointer(event);
        if (!cell) return;
        paintMaskLine(state.masks.lastCell, cell);
        state.masks.lastCell = cell;
    }

    function endMaskStroke() {
        state.masks.painting = false;
        state.masks.lastCell = null;
    }

    function undoMaskStroke() {
        const previous = state.masks.history.pop();
        if (!previous) return;
        state.masks.cells = previous;
        state.masks.dirty = !masksEqual(previous, state.masks.savedCells);
        renderMaskCanvas();
        updateMaskControls('Last mask stroke undone.');
    }

    function revertMasks() {
        if (!state.masks.savedCells) return;
        state.masks.history.push(state.masks.cells.slice());
        state.masks.cells = state.masks.savedCells.slice();
        state.masks.dirty = false;
        renderMaskCanvas();
        updateMaskControls('Returned to saved calibration.');
    }

    function maskIndices(value) {
        const indices = [];
        state.masks.cells?.forEach((cell, index) => { if (cell === value) indices.push(index); });
        return indices;
    }

    async function saveMasks() {
        if (!state.masks.dirty || !state.serverOnline) return;
        const button = $('saveMasksButton');
        button.disabled = true;
        updateMaskControls('Saving both calibrated mask layers…');
        try {
            const payload = await requestJson(globalActions().masks_url || '/api/painter/masks', {
                method: 'POST',
                body: JSON.stringify({
                    led_info: state.masks.ledInfo,
                    masks: {foliage: maskIndices(1), planter_bowls: maskIndices(2)},
                }),
            });
            state.masks.savedCells = state.masks.cells.slice();
            state.masks.history = [];
            state.masks.dirty = false;
            updateMaskControls(`Saved ${payload.counts.foliage.toLocaleString()} foliage and ${payload.counts.planter_bowls.toLocaleString()} bowl pixels.`, 'success');
            toast('Plant mask calibration saved. The wall output was not changed.', 'success');
        } catch (error) {
            updateMaskControls(error.message, 'error');
            toast(error.message, 'error');
        } finally {
            updateMaskControls();
        }
    }

    function selectInspectorTab(name) {
        const panels = {controls: 'controlsPanel', layers: 'layersPanel', wall: 'wallPanel', checker: 'checkerPanel'};
        Object.entries(panels).forEach(([tabName, panelId]) => {
            $(`${tabName}Tab`).setAttribute('aria-selected', String(name === tabName));
            $(`${tabName}Tab`).tabIndex = name === tabName ? 0 : -1;
            $(panelId).hidden = name !== tabName;
        });
    }

    function selectMobileView(name) {
        const target = ['check', 'layers', 'wall'].includes(name) ? 'tune' : name;
        document.querySelectorAll('.mobile-view').forEach((view) => view.classList.toggle('is-active', view.dataset.mobileView === target));
        document.querySelectorAll('[data-mobile-target]').forEach((button) => {
            if (button.dataset.mobileTarget === name) button.setAttribute('aria-current', 'page');
            else button.removeAttribute('aria-current');
        });
        if (name === 'check') selectInspectorTab('checker');
        else if (name === 'layers') selectInspectorTab('layers');
        else if (name === 'wall') selectInspectorTab('wall');
        else if (name === 'tune') selectInspectorTab('controls');
        if (target === 'tune') document.querySelector('.inspector-pane').scrollTop = 0;
    }

    function bindEvents() {
        $('componentSearch').addEventListener('input', (event) => {
            state.query = event.target.value;
            if (state.query.trim()) $('animationCatalogDisclosure').open = true;
            renderCatalog();
        });
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
            if (JSON.stringify(state.params) === JSON.stringify(state.originalParams)) return;
            state.params = clone(state.originalParams);
            state.selectedPreset = null;
            renderParameterControls();
            renderPresets();
            resetChecker();
            commitHistory();
            scheduleAutosave();
            requestRender();
        });
        $('presetName').addEventListener('input', () => {
            resetChecker();
            scheduleAutosave();
        });
        $('presetName').addEventListener('change', commitHistory);
        $('importButton').addEventListener('click', () => $('importFile').click());
        $('importPanelButton').addEventListener('click', () => $('importFile').click());
        $('importFile').addEventListener('change', (event) => event.target.files[0] && importJson(event.target.files[0]));
        $('copyButton').addEventListener('click', copyJson);
        $('exportButton').addEventListener('click', exportJson);
        $('exportPanelButton').addEventListener('click', exportJson);
        ['saveLibraryButton', 'saveLibraryPanelButton'].forEach((id) => $(id).addEventListener('click', () => saveToLibrary()));
        ['activateButton', 'activatePanelButton'].forEach((id) => $(id).addEventListener('click', reviewActivation));
        $('controlsTab').addEventListener('click', () => selectInspectorTab('controls'));
        $('layersTab').addEventListener('click', () => selectInspectorTab('layers'));
        $('wallTab').addEventListener('click', () => selectInspectorTab('wall'));
        $('checkerTab').addEventListener('click', () => selectInspectorTab('checker'));
        enableRovingFocus(document.querySelector('.inspector-tabs'), '[role="tab"]', {vertical: false});
        $('clockEnabled').addEventListener('change', (event) => {
            state.layers.clockEnabled = event.target.checked;
            state.lastSavedPreset = null;
            if (!state.layers.clockEnabled) disposeOverlayRuntime();
            renderLayers();
            resetChecker();
            commitHistory();
            scheduleAutosave();
            if (state.layers.clockEnabled) ensureOverlayRuntime().then(requestRender).catch((error) => {
                $('clockPreviewNote').textContent = `Clock remains configured for activation; local layer preview is unavailable: ${error.message}`;
            });
            requestRender();
        });
        $('clockOpacity').addEventListener('input', (event) => {
            state.layers.clockOpacity = Math.max(0, Math.min(255, Math.round(safeNumber(event.target.value, 220))));
            $('clockOpacityValue').textContent = `${Math.round(state.layers.clockOpacity / 255 * 100)}%`;
            state.lastSavedPreset = null;
            resetChecker();
            scheduleAutosave();
            requestRender();
        });
        $('clockOpacity').addEventListener('change', commitHistory);
        $('clockPresetSelect').addEventListener('change', (event) => applyClockPreset(event.target.value));
        $('fallbackSelect').addEventListener('change', (event) => {
            if (state.layers.fallbackKey === event.target.value) return;
            state.layers.fallbackKey = event.target.value;
            resetChecker();
            commitHistory();
            scheduleAutosave();
        });
        $('confirmOverwriteButton').addEventListener('click', (event) => {
            event.preventDefault();
            $('overwriteDialog').close();
            saveToLibrary({overwrite: true});
        });
        $('confirmActivateButton').addEventListener('click', (event) => {
            event.preventDefault();
            $('activateDialog').close();
            activateScene();
        });
        $('runCheckerButton').addEventListener('click', runChecker);
        $('prepareOfflineButton')?.addEventListener('click', prepareOffline);
        $('refreshWallButton').addEventListener('click', () => refreshGlobalSettings({preserveDraft: true}));
        $('globalBrightness').addEventListener('input', (event) => updateGlobalDraft((next) => {
            next.brightness = Math.round(safeNumber(event.target.value, 128));
        }));
        $('globalSpeed').addEventListener('input', (event) => updateGlobalDraft((next) => {
            next.speedMultiplier = safeNumber(event.target.value, 1);
        }));
        $('globalTargetFps').addEventListener('input', (event) => updateGlobalDraft((next) => {
            next.targetFps = Math.round(safeNumber(event.target.value, 30));
        }));
        $('resetWallDraftButton').addEventListener('click', () => {
            state.globalSettings.draft = clone(state.globalSettings.observed);
            state.globalSettings.dirty = false;
            persistGlobalDraft();
            resetChecker({preserveDocumentRevision: true});
            renderGlobalSettings();
            requestRender();
        });
        $('reviewWallButton').addEventListener('click', reviewGlobalChanges);
        $('confirmWallChangesButton').addEventListener('click', (event) => {
            event.preventDefault();
            $('wallReviewDialog').close();
            applyGlobalChanges();
        });
        $('editMasksButton').addEventListener('click', openMaskEditor);
        $('closeMaskEditorButton').addEventListener('click', () => $('maskEditorDialog').close());
        $('doneMaskEditorButton').addEventListener('click', () => $('maskEditorDialog').close());
        $('undoMaskButton').addEventListener('click', undoMaskStroke);
        $('revertMasksButton').addEventListener('click', revertMasks);
        $('saveMasksButton').addEventListener('click', saveMasks);
        $('maskZoom').addEventListener('input', (event) => {
            state.masks.zoom = Math.round(safeNumber(event.target.value, 6));
            renderMaskCanvas();
        });
        document.querySelectorAll('[data-mask-tool]').forEach((button) => button.addEventListener('click', () => {
            state.masks.tool = Number(button.dataset.maskTool);
            updateMaskControls();
        }));
        $('maskCanvas').addEventListener('pointerdown', beginMaskStroke);
        $('maskCanvas').addEventListener('pointermove', continueMaskStroke);
        $('maskCanvas').addEventListener('pointerup', endMaskStroke);
        $('maskCanvas').addEventListener('pointercancel', endMaskStroke);
        document.querySelectorAll('[data-mobile-target]').forEach((button) => button.addEventListener('click', () => selectMobileView(button.dataset.mobileTarget)));
        document.addEventListener('keydown', (event) => {
            const key = event.key.toLowerCase();
            if ($('maskEditorDialog').open && !event.metaKey && !event.ctrlKey && !event.altKey) {
                if (key === '1') state.masks.tool = 1;
                else if (key === '2') state.masks.tool = 2;
                else if (key === 'e') state.masks.tool = 0;
                else if (key === 'z') undoMaskStroke();
                else return;
                event.preventDefault();
                updateMaskControls();
                return;
            }
            const target = event.target;
            const editing = target instanceof HTMLElement && (
                target.isContentEditable || ['INPUT', 'SELECT', 'TEXTAREA'].includes(target.tagName)
            );
            const modifier = event.metaKey || event.ctrlKey;

            if (!modifier && !event.altKey && !editing) {
                if (event.key === '/') {
                    event.preventDefault();
                    selectMobileView('library');
                    $('componentSearch').focus();
                } else if (event.code === 'Space' && state.component) {
                    event.preventDefault();
                    $('playButton').click();
                } else if (key === 't') {
                    event.preventDefault();
                    selectMobileView('tune');
                } else if (key === 'l') {
                    event.preventDefault();
                    selectMobileView('layers');
                } else if (key === 'w') {
                    event.preventDefault();
                    selectMobileView('wall');
                } else if (key === 'c') {
                    event.preventDefault();
                    selectMobileView('check');
                }
                return;
            }

            if (!modifier || event.altKey) return;
            if (editing && key === 'z') return;
            if (key === 'z') {
                event.preventDefault();
                restoreHistory(state.historyIndex + (event.shiftKey ? 1 : -1));
            } else if (key === 's') {
                event.preventDefault();
                if (state.component && state.serverOnline) saveToLibrary();
                else toast('Draft saved on this device. Reconnect to save to the server library.');
            } else if (key === 'o') {
                event.preventDefault();
                $('importFile').click();
            } else if (key === 'e') {
                event.preventDefault();
                if (state.component) exportJson();
            }
        });
        window.addEventListener('online', () => {
            checkConnectivity();
            refreshOfflineReadiness();
        });
        window.addEventListener('offline', () => setServerOnline(false));
        window.addEventListener('beforeunload', () => {
            window.clearInterval(state.connectivityTimer);
            disposeRuntimes();
        });
        window.addEventListener('beforeunload', (event) => {
            if (!state.masks.dirty) return;
            event.preventDefault();
            event.returnValue = '';
        });
    }

    async function registerServiceWorker() {
        if (!('serviceWorker' in navigator) || !window.isSecureContext) {
            showOfflineReadiness({readyOffline: false, reason: 'Offline preparation requires a secure browser context.'});
            return;
        }
        try {
            await navigator.serviceWorker.register('/composer-service-worker.js', {scope: '/'});
            await navigator.serviceWorker.ready;
            navigator.serviceWorker.addEventListener('controllerchange', refreshOfflineReadiness, {once: true});
            await refreshOfflineReadiness();
        } catch (error) {
            showOfflineReadiness({readyOffline: false, reason: `Offline worker unavailable: ${error.message}`});
            console.info('Composer offline shell is not available:', error.message);
        }
    }

    async function initialize() {
        bindEvents();
        syncPlayButton();
        updateInstallStatus();
        setServerOnline(false, {checking: true, quiet: true});
        window.requestAnimationFrame(animationLoop);
        registerServiceWorker();
        try {
            if (!ComposerRuntime) throw new Error('The browser runtime adapter did not load.');
            await loadBootstrap();
            state.connectivityTimer = window.setInterval(() => checkConnectivity({quiet: true}), 15000);
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
