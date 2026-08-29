(function composerApplication() {
    'use strict';

    const {ComposerRuntime} = window.LEDGridComposerRuntime || {};
    const ComposerState = window.LEDGridComposerState || {};
    const $ = (id) => document.getElementById(id);
    const STORAGE_PREFIX = 'ledgrid.browser-composer.v1';
    const BUNDLED_BOOTSTRAP_URL = '/static/generated/composer/bootstrap.v1.json';
    const SAMPLE_FRAMES = 48;
    const EMPTY_PROFILE_DIGEST = '0'.repeat(64);
    const GLOBE_REGION_ORDER = Object.freeze([
        'top_left',
        'top_right',
        'upper_middle',
        'middle_left',
        'middle_right',
        'lower_left',
        'lower_right',
    ]);
    const MASK_LAYERS = Object.freeze([
        Object.freeze({id: 'foliage', value: 1, label: 'Foliage', color: '#35c86f', key: '1'}),
        ...GLOBE_REGION_ORDER.map((id, index) => Object.freeze({
            id,
            value: index + 2,
            label: humanizeMaskLayer(id),
            color: ['#ff9f43', '#ff7043', '#f7c843', '#58b7ff', '#9c7cff', '#e66fd0', '#64d8cb'][index],
            key: String(index + 2),
        })),
    ]);
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
        reducedMotion: false,
        motionPreference: null,
        motionPreferenceListener: null,
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
        serverCheck: null,
        controllerObservation: null,
        activation: {
            activationId: null,
            idempotencyKey: null,
            statusUrl: null,
            pollTimer: null,
            pollStartedAt: 0,
            phase: null,
            lastStatus: null,
            resourceRequestUrl: null,
            resourceRequestId: null,
            resourceKind: null,
            resourcePollTimer: null,
            resourcePollStartedAt: 0,
        },
        autosaveTimer: null,
        connectivityTimer: null,
        composerReady: false,
        serverOnline: false,
        serverChecking: true,
        serverBootstrap: null,
        serverCatalogCompatible: false,
        serverActivationCompatible: false,
        serverCatalogReason: 'Wall capabilities have not been refreshed.',
        wallStateLoaded: false,
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
        installationProfile: {
            selectedDigest: null,
            selectedArtifactUrl: null,
            desiredDigest: null,
            desiredArtifactUrl: null,
            candidate: null,
        },
        masks: {
            loaded: false,
            digest: null,
            revision: null,
            ledInfo: null,
            cells: null,
            savedCells: null,
            history: [],
            tool: 'foliage',
            dirty: false,
            painting: false,
            lastCell: null,
            keyboardCell: {strip: 0, led: 0},
            zoom: 6,
            stale: false,
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

    function humanizeMaskLayer(value) {
        return String(value || '').replaceAll('_', ' ').replace(/\b\w/g, (match) => match.toUpperCase());
    }

    function humanize(value) {
        return String(value || '').replaceAll('_', ' ').replace(/\b\w/g, (match) => match.toUpperCase());
    }

    function safeNumber(value, fallback = 0) {
        const number = Number(value);
        return Number.isFinite(number) ? number : fallback;
    }

    const modalReturnFocus = new WeakMap();
    const MODAL_FOCUSABLE_SELECTOR = [
        'a[href]',
        'button:not([disabled])',
        'input:not([disabled]):not([type="hidden"])',
        'select:not([disabled])',
        'textarea:not([disabled])',
        '[tabindex]:not([tabindex="-1"])',
    ].join(',');

    function modalFocusableElements(dialog) {
        return [...dialog.querySelectorAll(MODAL_FOCUSABLE_SELECTOR)].filter((element) => (
            element instanceof HTMLElement
            && !element.hidden
            && !element.closest('[hidden]')
            && element.tabIndex >= 0
        ));
    }

    function trapModalFocus(event) {
        const dialog = event.currentTarget;
        if (!(dialog instanceof HTMLDialogElement) || !dialog.open || event.key !== 'Tab') return;
        const focusable = modalFocusableElements(dialog);
        if (!focusable.length) {
            event.preventDefault();
            return;
        }
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        const active = document.activeElement;
        if (event.shiftKey && (active === first || !dialog.contains(active))) {
            event.preventDefault();
            last.focus({preventScroll: true});
        } else if (!event.shiftKey && (active === last || !dialog.contains(active))) {
            event.preventDefault();
            first.focus({preventScroll: true});
        }
    }

    function showComposerModal(dialog, {initialFocus = null, returnFocus = document.activeElement} = {}) {
        if (!dialog || dialog.open) return;
        if (returnFocus instanceof HTMLElement && returnFocus.isConnected) {
            modalReturnFocus.set(dialog, returnFocus);
        }
        dialog.showModal();
        if (initialFocus instanceof HTMLElement && !initialFocus.disabled) {
            initialFocus.focus({preventScroll: true});
        }
    }

    function restoreModalFocus(event) {
        const dialog = event.currentTarget;
        const returnFocus = modalReturnFocus.get(dialog);
        modalReturnFocus.delete(dialog);
        if (!(returnFocus instanceof HTMLElement)) return;
        // Native Escape handling may complete its own focus step after the
        // dialog's close event. Restore on the next task so the control that
        // opened the dialog wins consistently in every browser engine.
        window.setTimeout(() => {
            if (
                document.querySelector('dialog[open]')
                || !returnFocus.isConnected
                || returnFocus.disabled
                || returnFocus.closest('dialog:not([open])')
            ) return;
            returnFocus.focus({preventScroll: true});
        }, 0);
    }

    function applyMotionPreference(reduced, {announce = false} = {}) {
        state.reducedMotion = Boolean(reduced);
        document.documentElement.dataset.motion = state.reducedMotion ? 'reduced' : 'full';
        if (state.reducedMotion) state.playing = false;
        syncPlayButton();
        if (announce && state.reducedMotion) toast('Reduced motion enabled; local preview paused.');
    }

    function initializeMotionPreference() {
        const preference = window.matchMedia?.('(prefers-reduced-motion: reduce)');
        state.motionPreference = preference || null;
        applyMotionPreference(Boolean(preference?.matches));
        if (!preference) return;
        state.motionPreferenceListener = (event) => applyMotionPreference(event.matches, {announce: true});
        if (typeof preference.addEventListener === 'function') {
            preference.addEventListener('change', state.motionPreferenceListener);
        } else if (typeof preference.addListener === 'function') {
            preference.addListener(state.motionPreferenceListener);
        }
    }

    function disposeMotionPreference() {
        const preference = state.motionPreference;
        const listener = state.motionPreferenceListener;
        if (!preference || !listener) return;
        if (typeof preference.removeEventListener === 'function') {
            preference.removeEventListener('change', listener);
        } else if (typeof preference.removeListener === 'function') {
            preference.removeListener(listener);
        }
        state.motionPreference = null;
        state.motionPreferenceListener = null;
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

    async function requestJsonResource(url, options = {}) {
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
            error.etag = response.headers.get('ETag');
            throw error;
        }
        return {payload, etag: response.headers.get('ETag')};
    }

    function globalActions() {
        return state.bootstrap?.capabilities?.server_actions || {};
    }

    function profileUrlForDigest(url, priorDigest, nextDigest) {
        if (typeof url !== 'string' || !url) return null;
        if (url.includes('{digest}')) return url.replace('{digest}', nextDigest);
        if (url.includes(':digest')) return url.replace(':digest', nextDigest);
        if (priorDigest && url.includes(priorDigest)) return url.replace(priorDigest, nextDigest);
        return url;
    }

    function managedProfileUrl(kind, digest) {
        return `/api/v1/installation-profiles/${digest}/${kind}`;
    }

    function initializeInstallationProfileState() {
        const localProfile = ComposerState.localInstallationProfile(state.bootstrap);
        state.installationProfile.selectedDigest = localProfile?.digest || null;
        state.installationProfile.selectedArtifactUrl = localProfile?.artifactUrl || null;
        state.installationProfile.desiredDigest = localProfile?.digest || null;
        state.installationProfile.desiredArtifactUrl = localProfile?.artifactUrl || null;
        state.installationProfile.candidate = null;
    }

    function desiredInstallationProfile() {
        const profile = state.installationProfile;
        if (
            !/^[0-9a-f]{64}$/.test(profile.desiredDigest || '')
            || profile.desiredDigest === EMPTY_PROFILE_DIGEST
            || typeof profile.desiredArtifactUrl !== 'string'
            || !profile.desiredArtifactUrl
        ) return null;
        return {
            digest: profile.desiredDigest,
            artifactUrl: profile.desiredArtifactUrl,
        };
    }

    function composerRuntimeOptions(overrides = {}) {
        return {...overrides, installationProfile: desiredInstallationProfile()};
    }

    function updateSelectedInstallationProfile(nextDigest) {
        const profile = state.installationProfile;
        if (!/^[0-9a-f]{64}$/.test(nextDigest || '') || nextDigest === EMPTY_PROFILE_DIGEST) return;
        const priorDigest = profile.selectedDigest;
        const priorAuthority = state.bootstrap?.installation_profile?.authority;
        if (nextDigest === priorDigest && priorAuthority === 'host') return;
        const actions = globalActions();
        for (const name of (
            ['installation_profile_draft_url', 'installation_profile_publish_url', 'installation_profile_artifact_url']
        )) {
            const kind = name.replace('installation_profile_', '').replace('_url', '');
            actions[name] = profileUrlForDigest(actions[name], priorDigest, nextDigest)
                || managedProfileUrl(kind, nextDigest);
        }
        const followsSelected = !profile.desiredDigest || profile.desiredDigest === priorDigest;
        profile.selectedDigest = nextDigest;
        profile.selectedArtifactUrl = actions.installation_profile_artifact_url || null;
        if (followsSelected) {
            profile.desiredDigest = nextDigest;
            profile.desiredArtifactUrl = profile.selectedArtifactUrl;
            restartRuntimesAtCurrentState();
        }
        if (state.bootstrap?.installation_profile) {
            state.bootstrap.installation_profile.digest = nextDigest;
            state.bootstrap.installation_profile.authority = 'host';
        }
        resetChecker({preserveDocumentRevision: true});
        renderLayers();
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

    function activationGlobalSettings() {
        const draft = state.globalSettings.draft;
        if (!draft) throw new Error('Wall settings have not been observed yet.');
        const profile = vibeProfile(draft.vibeId);
        const controller = state.controllerObservation || {};
        const revision = Number(controller.globalSettingsRevision ?? controller.stateRevision);
        if (!Number.isSafeInteger(revision) || revision < 0) {
            throw new Error('The controller did not publish a global-settings revision.');
        }
        return {
            schema: 'ledgrid.global-settings-state',
            schema_version: 1,
            revision,
            vibe: {
                vibe_id: profile.vibe_id,
                profile_version: profile.profile_version,
                resolved_profile_digest: profile.resolved_profile_digest,
            },
            plant_modifiers: canonicalPlantModifiers(draft.plantModifiers),
            output: {
                power: true,
                brightness: draft.brightness,
                animation_speed_scale: safeNumber(
                    state.bootstrap?.global_control_contract?.operator_speed_baseline,
                    .3,
                ) * draft.speedMultiplier,
                target_fps: draft.targetFps,
            },
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
            : !state.wallStateLoaded ? 'Composer draft only · current wall state has not been read.'
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
            const priorObserved = clone(state.globalSettings.observed);
            const priorProfileDigest = state.installationProfile.selectedDigest;
            const nextControllerObservation = {
                sessionId: payload.controller_session_id || null,
                stateRevision: payload.controller_state_revision,
                globalSettingsRevision: payload.global_settings?.revision
                    ?? payload.global_settings_revision
                    ?? payload.active_identity?.global_settings_identity?.revision
                    ?? payload.controller_state_revision,
                activeIdentity: clone(payload.active_identity || null),
                installationProfileDigest: payload.installation_profile_digest || null,
            };
            if (
                state.serverCheck
                && JSON.stringify(state.controllerObservation) !== JSON.stringify(nextControllerObservation)
            ) {
                state.serverCheck = null;
            }
            state.controllerObservation = nextControllerObservation;
            state.wallStateLoaded = true;
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
            const observedProfileDigest = payload.installation_profile_digest;
            updateSelectedInstallationProfile(observedProfileDigest);
            state.globalSettings.dirty = !globalSettingsEqual(state.globalSettings.draft, observed);
            if (
                state.checkResult
                && (
                    !globalSettingsEqual(priorObserved, observed)
                    || priorProfileDigest !== state.installationProfile.selectedDigest
                )
            ) resetChecker({preserveDocumentRevision: true});
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
        const returnFocus = document.activeElement;
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
        showComposerModal($('wallReviewDialog'), {
            initialFocus: $('wallReviewDialog').querySelector('[value="cancel"]'),
            returnFocus,
        });
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

    function setComposerReady(ready, detail = null) {
        state.composerReady = Boolean(ready);
        const pill = $('composerState');
        if (!pill) return;
        pill.dataset.state = ready ? 'ready' : 'loading';
        pill.querySelector('span').textContent = ready ? 'Composer ready' : 'Composer loading';
        pill.title = detail || (ready
            ? 'Local rendering and editing are ready on this device.'
            : 'Loading the bundled renderer catalog.');
    }

    function serverComponentCompatibility() {
        if (!state.component || !state.serverBootstrap) {
            return {compatible: false, activationReady: false, reason: 'Choose a renderer and refresh wall capabilities.'};
        }
        const serverComponent = state.serverBootstrap.components.find((item) => item.key === state.component.key);
        if (!serverComponent) {
            return {compatible: false, activationReady: false, reason: 'The connected wall does not advertise this renderer.'};
        }
        const localIdentity = state.component.browser_capabilities?.managed_identity || {};
        const serverIdentity = serverComponent.browser_capabilities?.managed_identity || {};
        for (const field of ['component_digest', 'runtime_digest', 'parameter_schema_version']) {
            if (localIdentity[field] !== serverIdentity[field]) {
                return {compatible: false, activationReady: false, reason: `The connected wall has a different ${field.replaceAll('_', ' ')}.`};
            }
        }
        if (serverComponent.browser_capabilities?.activation_ready !== true) {
            return {
                compatible: true,
                activationReady: false,
                reason: serverComponent.browser_capabilities?.reason || 'This renderer is not activation-ready on the connected wall.',
            };
        }
        return {compatible: true, activationReady: true, reason: null};
    }

    function updateServerComponentCompatibility() {
        const result = serverComponentCompatibility();
        state.serverCatalogCompatible = result.compatible;
        state.serverActivationCompatible = result.activationReady;
        state.serverCatalogReason = result.reason;
        updateServerActionButtons();
    }

    async function refreshServerBootstrap(url = null) {
        const bootstrapUrl = url || globalActions().bootstrap_url || '/api/v1/composer/bootstrap?catalog_only=1';
        const payload = assertBootstrap(await requestJson(bootstrapUrl));
        state.serverBootstrap = payload;
        const actions = clone(payload.capabilities?.server_actions || {});
        state.bootstrap.capabilities.server_actions = actions;
        updateServerComponentCompatibility();
        return payload;
    }

    function setServerOnline(online, {checking = false, quiet = false} = {}) {
        state.serverOnline = Boolean(online);
        state.serverChecking = checking;
        const activationAvailable = state.bootstrap?.capabilities?.server_actions?.activation_available === true;
        const activationMode = state.bootstrap?.capabilities?.server_actions?.activation_mode;
        const activationIsCanary = activationAvailable && activationMode === 'development_canary';
        const pill = $('serverState');
        pill.dataset.state = checking ? 'checking' : online ? 'online' : 'offline';
        pill.querySelector('span').textContent = checking ? 'Checking wall' : online ? 'Wall connected' : 'Wall offline';
        $('serverActionBadge').textContent = online ? 'Wall connected' : 'Wall offline';
        if ($('networkStatus')) {
            $('networkStatus').textContent = checking
                ? 'Checking the wall server…'
                : online
                    ? activationAvailable
                        ? activationIsCanary
                            ? 'Wall connected; guarded activation is available only as a development/canary capability, not a production GO.'
                            : 'Wall connected; activation capability labeling is invalid, so physical activation is unavailable.'
                        : 'Wall connected; library save is available, but physical activation is disabled.'
                    : 'Composer ready; shared save and wall operations are offline.';
            $('networkStatus').dataset.state = checking ? 'checking' : online ? 'online' : 'offline';
        }
        if (!quiet && !state.busyAction) {
            $('serverActionStatus').textContent = online
                ? activationAvailable
                    ? activationIsCanary
                        ? 'Development/canary activation is available; this server is not production-qualified.'
                        : 'Activation is fail-closed because this server is not labeled as a development/canary target.'
                    : 'Library save is available. Physical activation remains disabled for this release.'
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
                state.installationProfile.desiredDigest || null,
                currentPresetRecord(),
            )
            : {
                draftGeneration: state.draftGeneration,
                componentKey: state.component.key,
                wallSettings: clone(state.globalSettings.draft),
                installationProfileDigest: state.installationProfile.desiredDigest || null,
                presetIdentity: componentPresetIdentity(),
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
        if (state.bootstrap?.capabilities?.server_actions?.activation_available !== true) {
            return 'Physical-wall activation is disabled on this server.';
        }
        if (state.bootstrap?.capabilities?.server_actions?.activation_mode !== 'development_canary') {
            return 'Physical-wall activation is not labeled as an explicit development/canary capability.';
        }
        if (!state.serverCatalogCompatible || !state.serverActivationCompatible) return state.serverCatalogReason || 'The connected wall catalog does not match this renderer.';
        const capability = componentCapability();
        if (!capability.activationReady) return capability.reason || 'This look is not activation-ready.';
        if (state.serverChecking) return 'Waiting for the wall server.';
        if (!state.serverOnline) return 'Reconnect to the wall server before activation.';
        if (state.globalSettings.loading || state.globalSettings.applying) return 'Wait for wall-wide settings to finish updating.';
        if (state.globalSettings.dirty) return 'Apply or revert the Wall draft before activating this checked scene.';
        if (state.globalSettings.pendingObservation) return 'Wait until the wall reports the reviewed Wall settings before activation.';
        return null;
    }

    function updateServerActionButtons() {
        const capability = componentCapability();
        const saveEnabled = Boolean(state.component && capability.saveable && state.serverCatalogCompatible && state.serverOnline && !state.serverChecking && !state.busyAction);
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
            const hasCurrentLocalCheck = currentCheckAllowsActivation();
            reason.textContent = blockReason || (!hasCurrentLocalCheck
                ? 'Activation-ready: the server will issue the authoritative Check when you review this scene.'
                : state.checkResult?.status === 'warn'
                    ? 'Activation-ready with cautions: review this exact checked draft before continuing.'
                    : 'Activation-ready: this exact draft passed Check.');
            reason.dataset.state = blockReason ? 'blocked' : 'ready';
        }
        updateActivationResourceButtons();
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
            if (payload.online === true) {
                try {
                    await refreshServerBootstrap(payload.bootstrap_url);
                } catch (error) {
                    state.serverBootstrap = null;
                    state.serverCatalogCompatible = false;
                    state.serverActivationCompatible = false;
                    state.serverCatalogReason = `Wall capabilities are unavailable: ${error.message}`;
                }
            }
            setServerOnline(payload.online === true, {quiet});
        } catch (_error) {
            state.serverBootstrap = null;
            state.serverCatalogCompatible = false;
            state.serverActivationCompatible = false;
            state.serverCatalogReason = 'The wall is offline.';
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
                temporary = new ComposerRuntime(component, state.bootstrap.geometry, composerRuntimeOptions({initTimeoutMs: 90000}));
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

    function assertBootstrap(payload, {requireLocalProfile = false} = {}) {
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
        if (payload.artifact?.kind === 'bundled' && (
            payload.artifact.version !== 1
            || !/^[0-9a-f]{64}$/.test(payload.artifact.catalog_digest || '')
        )) throw new Error('The bundled composer catalog has no valid version identity.');
        if (!/^[0-9a-f]{64}$/.test(payload.installation_profile?.digest || '')) {
            throw new Error('The composer catalog has no installation-profile identity.');
        }
        if (requireLocalProfile && !ComposerState.localInstallationProfile(payload)) {
            throw new Error('The bundled composer catalog has no exact local installation-profile artifact.');
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
        const response = await fetch(BUNDLED_BOOTSTRAP_URL, {
            headers: {'Accept': 'application/json'},
            cache: 'no-cache',
        });
        if (!response.ok) throw new Error(`Bundled catalog request failed (${response.status}).`);
        state.bootstrap = assertBootstrap(await response.json(), {requireLocalProfile: true});
        initializeInstallationProfileState();
        initializeGlobalSettings();
        configureCanvas();
        renderCatalog();
        const lastKey = localStorage.getItem(`${STORAGE_PREFIX}.last-component`);
        const preferred = state.bootstrap.components.find((item) => item.key === lastKey && item.role === 'background' && componentCapability(item).previewable)
            || state.bootstrap.components.find((item) => item.role === 'background' && componentCapability(item).previewable);
        if (preferred) await selectComponent(preferred);
        else showCatalogUnavailable('No components currently declare a supported browser runtime.');
        setComposerReady(true, `Bundled catalog ${state.bootstrap.artifact?.catalog_digest?.slice(0, 12) || 'ready'}`);
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
            const selected = state.installationProfile.selectedDigest || '';
            const desired = state.installationProfile.desiredDigest || selected;
            const bundled = state.bootstrap?.installation_profile?.authority === 'bundled';
            $('installationProfileStatus').textContent = desired !== selected
                ? `Published profile ${desired.slice(0, 12)}… is staged for the next Check and reviewed activation · wall remains ${selected.slice(0, 12)}….`
                : bundled
                    ? `Bundled profile ${selected.slice(0, 12)}… drives local preview · use Refresh in Wall settings to read the connected wall.`
                    : `Plant geometry is authoritative host state · selected profile ${selected.slice(0, 12)}… · presets cannot override it.`;
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
        updateServerComponentCompatibility();
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
            const draft = new ComposerRuntime(state.component, geometry, composerRuntimeOptions());
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
            const runtime = new ComposerRuntime(clock, state.bootstrap.geometry, composerRuntimeOptions({initTimeoutMs: 90000}));
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
        const runtime = new ComposerRuntime(state.component, state.bootstrap.geometry, composerRuntimeOptions());
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
        const status = $('previewMotionStatus');
        if (!status) return;
        if (state.reducedMotion) {
            status.textContent = state.playing
                ? 'Reduced motion is enabled. The local preview is playing because you explicitly started it.'
                : 'Reduced motion is enabled. The local preview starts paused; use Play to animate it.';
        } else {
            status.textContent = state.playing
                ? 'The local preview is playing.'
                : 'The local preview is paused.';
        }
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
        state.serverCheck = null;
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
        const runtime = new ComposerRuntime(state.component, state.bootstrap.geometry, composerRuntimeOptions({timeoutMs: 30000}));
        let overlayRuntime = null;
        let overlayMode = null;
        const renderTimes = [];
        const backgroundRendererTimes = [];
        const clockRendererTimes = [];
        const bridgeAndCompositorTimes = [];
        let previous = null;
        let deltaTotal = 0;
        let deltaMax = 0;
        let changedPairs = 0;
        let luminanceTotal = 0;
        let luminancePeak = 0;
        let clippingChannels = 0;
        let channelCount = 0;
        let peakCurrent = 0;
        let currentTotal = 0;
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
                    overlayRuntime = new ComposerRuntime(clock, state.bootstrap.geometry, composerRuntimeOptions({timeoutMs: 30000, initTimeoutMs: 90000}));
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
                let overlayFrame = null;
                if (overlayMode === 'shared') {
                    try {
                        const responses = await runtime.renderInstances([
                            {
                                instanceId: 'primary',
                                elapsed: previewElapsed(state.component, index / 12),
                                frameIndex: index,
                                params: enforceInstallationParams(state.component, state.params),
                            },
                            {
                                instanceId: 'clock_overlay',
                                elapsed: index / 12,
                                frameIndex: index,
                                params: clone(state.layers.clockParams),
                                wallTime: Date.now() / 1000,
                            },
                        ]);
                        backgroundFrame = validatedFrame(responses[0]);
                        overlayFrame = validatedFrame(responses[1]);
                    } catch (error) {
                        throw new Error(`Composed Python render failed at frame ${index + 1}: ${error.message}`);
                    }
                } else {
                    try {
                        backgroundFrame = validatedFrame(await runtime.render(
                            previewElapsed(state.component, index / 12),
                            index,
                            enforceInstallationParams(state.component, state.params),
                        ));
                    } catch (error) {
                        throw new Error(`Background renderer failed at frame ${index + 1}: ${error.message}`);
                    }
                    if (state.layers.clockEnabled) {
                        try {
                            overlayFrame = validatedFrame(await overlayRuntime.render(
                                index / 12, index, clone(state.layers.clockParams),
                            ));
                        } catch (error) {
                            throw new Error(`Clock renderer failed at frame ${index + 1}: ${error.message}`);
                        }
                    }
                }
                let frame = backgroundFrame;
                if (state.layers.clockEnabled) {
                    try {
                        frame = composeDraftFrame(backgroundFrame, overlayFrame);
                        if (!frame) throw new Error('local compositor is unavailable');
                    } catch (error) {
                        throw new Error(`Layer compositor failed at frame ${index + 1}: ${error.message}`);
                    }
                }
                frame = presentedFrame(frame);
                const pixels = frame.pixels;
                const sampleDuration = performance.now() - sampleStarted;
                const backgroundRenderMs = Number(backgroundFrame.renderMs);
                const clockRenderMs = Number(overlayFrame?.renderMs);
                if (Number.isFinite(backgroundRenderMs)) {
                    backgroundRendererTimes.push(backgroundRenderMs);
                }
                if (Number.isFinite(clockRenderMs)) clockRendererTimes.push(clockRenderMs);
                bridgeAndCompositorTimes.push(Math.max(
                    0,
                    sampleDuration
                        - (Number.isFinite(backgroundRenderMs) ? backgroundRenderMs : 0)
                        - (Number.isFinite(clockRenderMs) ? clockRenderMs : 0),
                ));
                renderTimes.push(sampleDuration);
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
                currentTotal += frameCurrent;
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
            const percentile = (values, fraction) => {
                if (!values.length) return null;
                const sorted = values.slice().sort((a, b) => a - b);
                return sorted[Math.min(sorted.length - 1, Math.ceil(sorted.length * fraction) - 1)];
            };
            const renderStats = ComposerState.orderedMetricStats(renderTimes);
            const meanRenderMs = renderStats.mean;
            const p95 = renderStats.p95;
            const p99 = renderStats.p99;
            const maxRenderMs = renderStats.max;
            const backgroundRendererP95 = percentile(backgroundRendererTimes, .95);
            const clockRendererP95 = percentile(clockRendererTimes, .95);
            const bridgeAndCompositorP95 = percentile(bridgeAndCompositorTimes, .95);
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
            const renderStatus = ComposerState.advisoryRenderStatus(p95, frameBudgetMs);
            const rendererBreakdown = state.layers.clockEnabled && backgroundRendererP95 !== null
                ? ` Python p95: background ${backgroundRendererP95.toFixed(2)} ms${clockRendererP95 === null ? '' : ` + Clock ${clockRendererP95.toFixed(2)} ms`}; bridge/compositor ${bridgeAndCompositorP95.toFixed(2)} ms.`
                : '';
            updateMetric('render', `${p95.toFixed(2)} ms`, renderStatus, `${state.layers.clockEnabled ? 'Background + Clock + compositor' : runtime.engine}; ${frameBudgetMs.toFixed(2)} ms budget at ${targetFps} fps, measured on this browser rather than receiver hardware. Advisory only; this metric does not block activation.${rendererBreakdown}`);
            if (renderStatus === 'warn') warnings.push('render time');

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
                    source: 'browser',
                    binding,
                    warnings: warnings.slice(),
                    failures: failures.slice(),
                    capturedAt: Date.now(),
                    completedAt: new Date().toISOString(),
                    environment: {
                        userAgent: navigator.userAgent || null,
                        platform: navigator.platform || null,
                        hardwareConcurrency: navigator.hardwareConcurrency || null,
                    },
                    sampleCount: SAMPLE_FRAMES,
                    frameTimeMs: {
                        mean: meanRenderMs,
                        p95,
                        p99,
                        max: maxRenderMs,
                        renderer: {
                            backgroundP95: backgroundRendererP95,
                            clockP95: clockRendererP95,
                            bridgeAndCompositorP95,
                        },
                    },
                    cadence: {
                        observedFps: meanRenderMs > 0 ? 1000 / meanRenderMs : null,
                        targetFps,
                        missedFrameRatio: renderTimes.filter((value) => value > frameBudgetMs).length / SAMPLE_FRAMES,
                        changedFrameRatio: changedPairs / deltas,
                    },
                    electrical: {
                        kind: 'uncalibrated_estimate',
                        brightness: state.globalSettings.draft?.brightness,
                        peakCurrentAmps: peakCurrent,
                        // Floating-point accumulation can put the arithmetic
                        // mean a few ulps above an identical sampled maximum.
                        // Preserve the mathematical mean <= peak invariant
                        // required by the server evidence contract.
                        meanCurrentAmps: Math.min(peakCurrent, currentTotal / SAMPLE_FRAMES),
                        nominalVoltageVolts: 5,
                    },
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
                state.checkResult = {
                    status: 'fail',
                    source: 'browser',
                    binding,
                    capturedAt: Date.now(),
                    completedAt: new Date().toISOString(),
                    environment: {userAgent: navigator.userAgent || null},
                    sampleCount: renderTimes.length,
                    error: error.message,
                };
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

    function componentPresetIdentity(preset = currentPresetRecord()) {
        if (ComposerState.componentPresetIdentity) {
            return ComposerState.componentPresetIdentity(preset);
        }
        if (!preset || typeof preset !== 'object') return null;
        const presetId = preset.preset_id ?? preset.presetId;
        const presetFingerprint = preset.preset_fingerprint ?? preset.presetFingerprint;
        return presetId && presetFingerprint
            ? {presetId: String(presetId), presetFingerprint: String(presetFingerprint)}
            : null;
    }

    function sameComponentPresetIdentity(left, right) {
        const stable = ComposerState.stableJson || JSON.stringify;
        return stable(componentPresetIdentity(left)) === stable(componentPresetIdentity(right));
    }

    function invalidateCheckerForPresetIdentityChange(previousIdentity) {
        if (sameComponentPresetIdentity(previousIdentity, currentPresetRecord())) return false;
        resetChecker();
        return true;
    }

    function adoptImportedPresetIdentity(draft) {
        const reference = draft?.browser_scene?.background || draft?.scene?.background;
        const identity = componentPresetIdentity(reference);
        state.selectedPreset = null;
        state.lastSavedPreset = null;
        if (!identity) return;
        const matchingIndex = (state.component?.presets || []).findIndex((preset) => (
            sameComponentPresetIdentity(preset, identity)
        ));
        if (matchingIndex >= 0) {
            state.selectedPreset = presetIdentity(
                state.component.presets[matchingIndex], matchingIndex
            );
        }
        state.lastSavedPreset = {
            preset_id: identity.presetId,
            preset_fingerprint: identity.presetFingerprint,
        };
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
        const profileDigest = state.installationProfile.desiredDigest;
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
        const returnFocus = document.activeElement;
        if (!state.serverOnline) {
            toast('Offline: the draft is saved on this device, but the library is unavailable.', 'error');
            return;
        }
        setActionBusy('save', true);
        $('serverActionStatus').textContent = 'Validating and saving the component preset…';
        try {
            const previousPresetIdentity = componentPresetIdentity();
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
            if (!componentPresetIdentity(record)) {
                throw new Error('The server returned an incomplete component preset identity.');
            }
            if (existingIndex >= 0) state.component.presets.splice(existingIndex, 1, record);
            else state.component.presets.push(record);
            state.selectedPreset = record.key;
            invalidateCheckerForPresetIdentityChange(previousPresetIdentity);
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
                showComposerModal($('overwriteDialog'), {
                    initialFocus: $('overwriteDialog').querySelector('[value="cancel"]'),
                    returnFocus,
                });
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

    function newIdempotencyKey() {
        if (typeof crypto?.randomUUID === 'function') return crypto.randomUUID();
        const bytes = new Uint8Array(16);
        crypto.getRandomValues(bytes);
        return Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('');
    }

    async function createServerCheck() {
        const scene = buildScene();
        const checkUrl = state.bootstrap.capabilities?.server_actions?.check_scene_url
            || '/api/v1/scene/checks';
        const result = await requestJson(checkUrl, {
            method: 'POST',
            body: JSON.stringify({
                scene,
                global_settings: activationGlobalSettings(),
                browser_evidence: clone(state.checkResult),
            }),
        });
        if (!result.check_token || !result.basis_digest || !result.basis?.controller) {
            throw new Error('The wall server returned an incomplete Check authorization.');
        }
        state.serverCheck = {
            token: result.check_token,
            basis: clone(result.basis),
            basisDigest: result.basis_digest,
            expiresAt: result.expires_at,
            idempotencyKey: newIdempotencyKey(),
        };
        return state.serverCheck;
    }

    async function reviewActivation() {
        const returnFocus = document.activeElement;
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
        setActionBusy('activate', true);
        $('serverActionStatus').textContent = 'Requesting a short-lived server Check for this exact scene and wall state…';
        try {
            await createServerCheck();
        } catch (error) {
            if (error.code === 'offline') setServerOnline(false);
            state.serverCheck = null;
            $('serverActionStatus').textContent = `Server Check failed: ${error.message}`;
            toast(error.message, 'error');
            return;
        } finally {
            setActionBusy('activate', false);
        }
        const fallback = pythonFallbacks().find((item) => item.key === state.layers.fallbackKey);
        $('activateBackground').textContent = state.component.name || humanize(state.component.plugin_id);
        $('activateOverlay').textContent = state.layers.clockEnabled ? `Clock · ${Math.round(state.layers.clockOpacity / 255 * 100)}%` : 'Off';
        $('activateFallback').textContent = fallback?.name || 'Unavailable';
        if ($('activateProvider')) $('activateProvider').textContent = state.component.provider;
        if ($('activateRuntimeDigest')) $('activateRuntimeDigest').textContent = ComposerState.runtimeDigest?.(state.component) || 'Catalog identity';
        if ($('activateProfileDigest')) $('activateProfileDigest').textContent = state.installationProfile.desiredDigest || 'Unavailable';
        if ($('activateRevision')) $('activateRevision').textContent = String(state.documentRevision);
        if ($('activateCheck')) {
            const hasCurrentLocalCheck = currentCheckAllowsActivation();
            const outcome = !hasCurrentLocalCheck
                ? 'Server Check issued'
                : state.checkResult?.status === 'warn' ? 'Passed with cautions' : 'Passed';
            const cautions = hasCurrentLocalCheck && state.checkResult?.warnings?.length
                ? ` · review ${state.checkResult.warnings.join(', ')}`
                : '';
            const expires = state.serverCheck?.expiresAt
                ? new Date(state.serverCheck.expiresAt * 1000).toLocaleTimeString()
                : 'soon';
            $('activateCheck').textContent = `${outcome}${cautions} · server-authorized until ${expires}`;
        }
        if ($('activateDestination')) $('activateDestination').textContent = 'Physical living wall';
        showComposerModal($('activateDialog'), {
            initialFocus: $('activateDialog').querySelector('[value="cancel"]'),
            returnFocus,
        });
    }

    function clearActivationPolling() {
        if (state.activation.pollTimer) window.clearTimeout(state.activation.pollTimer);
        state.activation.pollTimer = null;
    }

    function clearActivationResourcePolling() {
        if (state.activation.resourcePollTimer) {
            window.clearTimeout(state.activation.resourcePollTimer);
        }
        state.activation.resourcePollTimer = null;
    }

    function updateActivationResourceButtons() {
        const status = state.activation.lastStatus;
        const cancel = $('cancelActivationButton');
        const rollback = $('rollbackActivationButton');
        const cancelAvailable = ['queued', 'preflighting'].includes(status?.phase);
        const rollbackAvailable = status?.phase === 'active'
            && status?.rollback?.available === true
            && Boolean(status?.rollback?.snapshot_id)
            && Number.isInteger(status?.controller?.state_revision_after);
        if (cancel) {
            cancel.hidden = !cancelAvailable;
            cancel.disabled = !cancelAvailable || !state.serverOnline
                || Boolean(state.busyAction) || Boolean(state.activation.resourceRequestUrl);
        }
        if (rollback) {
            rollback.hidden = !rollbackAvailable;
            rollback.disabled = !rollbackAvailable || !state.serverOnline
                || Boolean(state.busyAction) || Boolean(state.activation.resourceRequestUrl);
        }
    }

    function activationIdentitiesMatch(status) {
        if (!status?.requested_identity || !status?.observed_identity) return false;
        const stable = ComposerState.stableJson || JSON.stringify;
        return stable(status.requested_identity) === stable(status.observed_identity);
    }

    function renderActivationStatus(status) {
        const phase = status?.phase || 'queued';
        state.activation.phase = phase;
        state.activation.lastStatus = clone(status);
        updateActivationResourceButtons();
        const readable = humanize(phase);
        if (phase === 'active') {
            if (status.rollback?.available === false && status.rollback?.error) {
                $('serverActionStatus').textContent = `Previously active · no longer current · ${status.rollback.error}.`;
                return true;
            }
            if (!activationIdentitiesMatch(status)) {
                $('serverActionStatus').textContent = 'Activation observation is incomplete; the wall is not reported Active.';
                return false;
            }
            const telemetry = status.telemetry?.complete && status.telemetry?.fresh
                ? 'telemetry complete and fresh' : 'telemetry incomplete';
            $('serverActionStatus').textContent = `Active · exact scene, globals, runtime, and profile observed · ${telemetry}.`;
            toast('The controller observed the exact checked activation.', 'success');
            return true;
        }
        if (['rolled_back', 'failed', 'timed_out'].includes(phase)) {
            const rollback = status.rollback?.result || (status.rollback?.available ? 'rollback available' : 'rollback unavailable');
            $('serverActionStatus').textContent = `${readable} · ${status.error || status.rollback?.error || rollback}.`;
            toast(`Activation ${readable.toLowerCase()}.`, 'error');
            return true;
        }
        $('serverActionStatus').textContent = `${readable} · waiting for correlated controller observation; the wall is not yet reported Active.`;
        return false;
    }

    async function pollActivationStatus() {
        clearActivationPolling();
        if (!state.activation.statusUrl) return;
        try {
            const status = await requestJson(state.activation.statusUrl);
            if (status.activation_id !== state.activation.activationId) {
                throw new Error('The activation status correlation ID changed.');
            }
            if (renderActivationStatus(status)) return;
        } catch (error) {
            if (error.code === 'offline') setServerOnline(false);
            $('serverActionStatus').textContent = `Pending activation status could not be refreshed: ${error.message}`;
        }
        if (Date.now() - state.activation.pollStartedAt >= 120000) {
            $('serverActionStatus').textContent = 'Activation is still unconfirmed after two minutes; refresh status before treating the wall as Active.';
            return;
        }
        state.activation.pollTimer = window.setTimeout(pollActivationStatus, 1000);
    }

    async function cancelPendingActivation() {
        const status = state.activation.lastStatus;
        if (!state.activation.statusUrl || !['queued', 'preflighting'].includes(status?.phase)) return;
        setActionBusy('activate', true);
        try {
            clearActivationPolling();
            const accepted = await requestJson(state.activation.statusUrl, {method: 'DELETE'});
            state.activation.resourceRequestUrl = accepted.request_status_url;
            state.activation.resourceRequestId = accepted.request_id;
            state.activation.resourceKind = 'cancel';
            state.activation.resourcePollStartedAt = Date.now();
            $('serverActionStatus').textContent = 'Cancellation requested · waiting for the correlated controller status.';
            pollActivationResourceResult();
        } catch (error) {
            if (error.code === 'offline') setServerOnline(false);
            $('serverActionStatus').textContent = `Cancellation was not accepted: ${error.message}`;
            toast(error.message, 'error');
        } finally {
            setActionBusy('activate', false);
            updateActivationResourceButtons();
        }
    }

    async function rollbackActivation() {
        const status = state.activation.lastStatus;
        if (
            !state.activation.statusUrl
            || status?.phase !== 'active'
            || status?.rollback?.available !== true
            || !status?.rollback?.snapshot_id
            || !Number.isInteger(status?.controller?.state_revision_after)
        ) return;
        setActionBusy('activate', true);
        try {
            clearActivationPolling();
            const accepted = await requestJson(`${state.activation.statusUrl}/rollback`, {
                method: 'POST',
                body: JSON.stringify({
                    expected_controller_session_id: status.controller.session_id,
                    expected_controller_state_revision: status.controller.state_revision_after,
                }),
            });
            state.activation.resourceRequestUrl = accepted.request_status_url;
            state.activation.resourceRequestId = accepted.request_id;
            state.activation.resourceKind = 'rollback';
            state.activation.resourcePollStartedAt = Date.now();
            $('serverActionStatus').textContent = 'Rollback requested · waiting for exact restoration to be observed.';
            pollActivationResourceResult();
        } catch (error) {
            if (error.code === 'offline') setServerOnline(false);
            $('serverActionStatus').textContent = `Rollback was not accepted: ${error.message}`;
            toast(error.message, 'error');
        } finally {
            setActionBusy('activate', false);
            updateActivationResourceButtons();
        }
    }

    async function pollActivationResourceResult() {
        clearActivationResourcePolling();
        const url = state.activation.resourceRequestUrl;
        const requestId = state.activation.resourceRequestId;
        const kind = state.activation.resourceKind;
        if (!url || !requestId || !kind) return;
        try {
            const result = await requestJson(url);
            if (
                result.activation_id !== state.activation.activationId
                || result.request_id !== requestId
            ) {
                throw new Error('The activation request result correlation changed.');
            }
            if (result.outcome === 'pending') {
                $('serverActionStatus').textContent = `${humanize(kind)} pending · the controller has not completed the correlated request.`;
            } else {
                state.activation.resourceRequestUrl = null;
                state.activation.resourceRequestId = null;
                state.activation.resourceKind = null;
                updateActivationResourceButtons();
                if (result.outcome !== 'succeeded') {
                    const message = `${humanize(kind)} ${result.outcome}: ${result.error || 'controller rejected the request'}`;
                    $('serverActionStatus').textContent = message;
                    toast(message, 'error');
                    return;
                }
                const status = await requestJson(state.activation.statusUrl);
                if (status.activation_id !== state.activation.activationId) {
                    throw new Error('The activation status correlation ID changed.');
                }
                renderActivationStatus(status);
                return;
            }
        } catch (error) {
            if (error.code === 'offline') setServerOnline(false);
            $('serverActionStatus').textContent = `${humanize(kind)} result could not be refreshed: ${error.message}`;
        }
        if (Date.now() - state.activation.resourcePollStartedAt >= 120000) {
            $('serverActionStatus').textContent = `${humanize(kind)} remains unconfirmed after two minutes; do not infer success from the activation's prior state.`;
            return;
        }
        state.activation.resourcePollTimer = window.setTimeout(
            pollActivationResourceResult, 1000
        );
    }

    async function activateScene() {
        if (state.busyAction) return;
        setActionBusy('activate', true);
        $('serverActionStatus').textContent = 'Validating the exact background, Clock slot, and Python fallback…';
        try {
            const blockReason = activationBlockReason();
            if (blockReason) throw new Error(blockReason);
            const scene = buildScene();
            const serverCheck = state.serverCheck;
            if (!serverCheck?.token) throw new Error('Request a fresh server Check before activation.');
            if (serverCheck.expiresAt && Date.now() >= serverCheck.expiresAt * 1000) {
                state.serverCheck = null;
                throw new Error('The server Check expired. Review activation again.');
            }
            const activateUrl = state.bootstrap.capabilities?.server_actions?.activate_scene_url || '/api/v1/scene';
            const result = await requestJson(activateUrl, {
                method: 'PUT',
                headers: {'Idempotency-Key': serverCheck.idempotencyKey},
                body: JSON.stringify({
                    check_token: serverCheck.token,
                    expected_controller_session_id: serverCheck.basis.controller.session_id,
                    expected_controller_state_revision: serverCheck.basis.controller.state_revision,
                    scene,
                    global_settings: activationGlobalSettings(),
                }),
            });
            state.activation.activationId = result.activation_id;
            state.activation.idempotencyKey = serverCheck.idempotencyKey;
            state.activation.statusUrl = result.status_url
                || `/api/v1/scene/activations/${encodeURIComponent(result.activation_id)}`;
            state.activation.pollStartedAt = Date.now();
            state.activation.lastStatus = null;
            state.serverCheck = null;
            $('serverActionStatus').textContent = `Pending · activation ${result.activation_id} is queued; the wall is not yet reported Active.`;
            toast('Activation queued; waiting for correlated controller observation.');
            pollActivationStatus();
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
        adoptImportedPresetIdentity(draft);
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
        renderPresets();
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
            const validated = locallyValidatedImport(payload);
            await applyImportedDraft(validated);
            $('serverActionStatus').textContent = 'Upload checked locally and opened as a draft. The physical wall was not changed.';
            toast('Preset checked and opened locally.');
        } catch (error) {
            if (error.code === 'offline') setServerOnline(false);
            toast(error.message, 'error');
        } finally {
            $('importFile').value = '';
        }
    }

    function maskLayerById(id) {
        return MASK_LAYERS.find((layer) => layer.id === id) || null;
    }

    function maskLayerByValue(value) {
        return MASK_LAYERS.find((layer) => layer.value === value) || null;
    }

    function maskCounts(cells = state.masks.cells) {
        const counts = Object.fromEntries(MASK_LAYERS.map((layer) => [layer.id, 0]));
        if (!cells) return counts;
        for (const value of cells) {
            const layer = maskLayerByValue(value);
            if (layer) counts[layer.id] += 1;
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

    function maskDraftStorageKey(digest = state.masks.digest) {
        return digest ? `${STORAGE_PREFIX}.profile-draft.${digest}` : null;
    }

    function persistMaskDraft() {
        const key = maskDraftStorageKey();
        if (!key || !state.masks.cells || !state.masks.savedCells) return;
        try {
            localStorage.setItem(key, JSON.stringify({
                schema: 'ledgrid.browser-installation-profile-draft',
                schema_version: 1,
                digest: state.masks.digest,
                revision: state.masks.revision,
                led_info: state.masks.ledInfo,
                cells: Array.from(state.masks.cells),
                saved_cells: Array.from(state.masks.savedCells),
                dirty: state.masks.dirty,
                saved_at: new Date().toISOString(),
            }));
        } catch (_error) {
            // The in-memory draft remains authoritative for this session.
        }
    }

    function restoredMaskDraft(digest, totalLeds) {
        try {
            const stored = JSON.parse(localStorage.getItem(maskDraftStorageKey(digest)));
            if (
                stored?.schema !== 'ledgrid.browser-installation-profile-draft'
                || stored.schema_version !== 1
                || stored.digest !== digest
                || typeof stored.revision !== 'string'
                || !Array.isArray(stored.cells)
                || !Array.isArray(stored.saved_cells)
                || stored.cells.length !== totalLeds
                || stored.saved_cells.length !== totalLeds
            ) return null;
            const valid = (value) => Number.isInteger(value) && value >= 0 && value <= MASK_LAYERS.length;
            if (!stored.cells.every(valid) || !stored.saved_cells.every(valid)) return null;
            return {
                revision: stored.revision,
                cells: Uint8Array.from(stored.cells),
                savedCells: Uint8Array.from(stored.saved_cells),
                dirty: Boolean(stored.dirty),
            };
        } catch (_error) {
            return null;
        }
    }

    function updateMaskControls(message = null, kind = '') {
        const masks = state.masks;
        const counts = maskCounts();
        if (masks.ledInfo) {
            $('maskGeometry').textContent = `${masks.ledInfo.strip_count} × ${masks.ledInfo.leds_per_strip}`;
        }
        $('foliageMaskCount').textContent = masks.loaded ? counts.foliage.toLocaleString() : '—';
        const globeTotal = GLOBE_REGION_ORDER.reduce((total, name) => total + counts[name], 0);
        $('globeMaskCount').textContent = masks.loaded ? globeTotal.toLocaleString() : '—';
        $('editorFoliageCount').textContent = counts.foliage.toLocaleString();
        for (const name of GLOBE_REGION_ORDER) {
            const output = $(`editor-${name.replaceAll('_', '-')}-count`);
            if (output) output.textContent = counts[name].toLocaleString();
        }
        $('undoMaskButton').disabled = !masks.history.length;
        $('revertMasksButton').disabled = !masks.dirty;
        $('saveMasksButton').disabled = !masks.dirty || !state.serverOnline;
        $('publishProfileButton').disabled = masks.dirty || masks.stale || !state.serverOnline || !masks.loaded;
        const candidate = state.installationProfile.candidate;
        $('reviewProfileCandidateButton').hidden = !candidate;
        $('reviewProfileCandidateButton').disabled = !candidate || candidate.digest === state.installationProfile.desiredDigest;
        document.querySelectorAll('[data-mask-tool]').forEach((button) => {
            button.setAttribute('aria-pressed', String(button.dataset.maskTool === masks.tool));
        });
        if (message) {
            $('maskEditorStatus').textContent = message;
            $('maskEditorStatus').dataset.state = kind;
        }
    }

    function validatedMaskIndices(value, label, totalLeds) {
        if (!Array.isArray(value)) throw new Error(`${label} must be an array.`);
        let prior = -1;
        return value.map((index) => {
            if (!Number.isInteger(index) || index < 0 || index >= totalLeds || index <= prior) {
                throw new Error(`${label} must contain sorted, unique in-bounds indices.`);
            }
            prior = index;
            return index;
        });
    }

    function loadMaskPayload(payload, {etag = null, restoreLocal = true} = {}) {
        if (payload?.schema !== 'ledgrid.installation-profile-draft' || payload.schema_version !== 1) {
            throw new Error('Installation-profile draft uses an unsupported schema.');
        }
        if (!/^[0-9a-f]{64}$/.test(payload.digest || '') || payload.digest === EMPTY_PROFILE_DIGEST) {
            throw new Error('Installation-profile draft has no managed source digest.');
        }
        if (typeof payload.revision !== 'string' || !payload.revision) {
            throw new Error('Installation-profile draft has no optimistic-concurrency revision.');
        }
        const normalizedEtag = etag?.replace(/^W\//, '').replace(/^"|"$/g, '');
        if (normalizedEtag && normalizedEtag !== payload.revision) {
            throw new Error('Installation-profile draft ETag does not match its revision.');
        }
        const info = payload?.led_info || {};
        const stripCount = Number(info.strip_count);
        const ledsPerStrip = Number(info.leds_per_strip);
        const totalLeds = Number(info.total_leds);
        if ((stripCount !== 32) || (ledsPerStrip !== 138) || totalLeds !== 4416) {
            throw new Error('Installation-profile draft must use the calibrated 32 × 138 geometry.');
        }
        const globes = payload.masks?.globes;
        if (!globes || typeof globes !== 'object' || Array.isArray(globes)) {
            throw new Error('Installation-profile draft has no named globe regions.');
        }
        if (Object.keys(globes).join(',') !== GLOBE_REGION_ORDER.join(',')) {
            throw new Error('Installation-profile draft must preserve the stable seven globe-region order.');
        }
        const cells = new Uint8Array(totalLeds);
        for (const index of validatedMaskIndices(payload.masks?.foliage, 'masks.foliage', totalLeds)) {
            cells[index] = 1;
        }
        for (const [position, name] of GLOBE_REGION_ORDER.entries()) {
            for (const index of validatedMaskIndices(globes[name], `masks.globes.${name}`, totalLeds)) {
                if (cells[index] !== 0) throw new Error(`Installation-profile layers overlap at pixel ${index}.`);
                cells[index] = position + 2;
            }
        }
        const restored = restoreLocal ? restoredMaskDraft(payload.digest, totalLeds) : null;
        state.masks.digest = payload.digest;
        state.masks.revision = restored?.revision || payload.revision;
        state.masks.ledInfo = {strip_count: stripCount, leds_per_strip: ledsPerStrip, total_leds: totalLeds};
        state.masks.cells = restored?.cells || cells;
        state.masks.savedCells = restored?.savedCells || cells.slice();
        state.masks.history = [];
        state.masks.dirty = restored ? !masksEqual(restored.cells, restored.savedCells) : false;
        state.masks.stale = Boolean(restored && restored.revision !== payload.revision);
        state.masks.loaded = true;
        persistMaskDraft();
    }

    async function fetchProfileDraft({restoreLocal = true} = {}) {
        const url = globalActions().installation_profile_draft_url;
        if (!url) throw new Error('No managed installation-profile draft is available.');
        const result = await requestJsonResource(url);
        loadMaskPayload(result.payload, {etag: result.etag, restoreLocal});
        return result.payload;
    }

    async function preloadMasks() {
        if (state.masks.loaded) return;
        try {
            await fetchProfileDraft();
            updateMaskControls();
        } catch (_error) {
            // Mask editing remains an explicit, retryable action when unavailable.
        }
    }

    async function openMaskEditor() {
        const returnFocus = document.activeElement;
        showComposerModal($('maskEditorDialog'), {
            initialFocus: $('closeMaskEditorButton'),
            returnFocus,
        });
        if (state.masks.loaded) {
            renderMaskCanvas();
            updateMaskControls(state.masks.dirty ? 'Unsaved mask draft restored.' : 'Calibrated masks ready. The wall is unchanged.');
            return;
        }
        updateMaskControls('Loading calibrated masks…');
        try {
            await fetchProfileDraft();
            renderMaskCanvas();
            const suffix = state.masks.stale
                ? ' A locally restored draft is based on an older revision; it will be preserved on conflict.'
                : '';
            updateMaskControls(`Managed profile draft ready. The wall is unchanged.${suffix}`, 'success');
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
                context.fillStyle = maskLayerByValue(value)?.color || '#ffffff';
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
        const keyboardCell = state.masks.keyboardCell;
        if (
            document.activeElement === canvas
            && keyboardCell
            && keyboardCell.strip >= 0
            && keyboardCell.strip < width
            && keyboardCell.led >= 0
            && keyboardCell.led < height
        ) {
            context.strokeStyle = '#ffffff';
            context.lineWidth = 2;
            context.strokeRect(
                keyboardCell.strip * zoom + 1,
                (height - 1 - keyboardCell.led) * zoom + 1,
                Math.max(1, zoom - 2),
                Math.max(1, zoom - 2),
            );
        }
        updateMaskCanvasAccessibility();
    }

    function updateMaskCanvasAccessibility() {
        const canvas = $('maskCanvas');
        const {keyboardCell, ledInfo} = state.masks;
        if (!canvas || !keyboardCell || !ledInfo) return;
        canvas.setAttribute(
            'aria-label',
            `Editable plant mask grid. Keyboard cursor at strip ${keyboardCell.strip + 1} of ${ledInfo.strip_count}, LED ${keyboardCell.led + 1} of ${ledInfo.leds_per_strip}.`,
        );
    }

    function paintKeyboardMaskCell(value) {
        const {cells, ledInfo, keyboardCell} = state.masks;
        if (!cells || !ledInfo || !keyboardCell) return;
        state.masks.history.push(cells.slice());
        if (state.masks.history.length > 60) state.masks.history.shift();
        const resolvedValue = value ?? maskLayerById(state.masks.tool)?.value ?? 0;
        cells[keyboardCell.strip * ledInfo.leds_per_strip + keyboardCell.led] = resolvedValue;
        state.masks.dirty = !masksEqual(cells, state.masks.savedCells);
        persistMaskDraft();
        renderMaskCanvas();
        const layer = maskLayerByValue(resolvedValue)?.label || 'Erase';
        updateMaskControls(
            `${layer} at strip ${keyboardCell.strip + 1}, LED ${keyboardCell.led + 1}. ${state.masks.dirty ? 'Draft has unsaved changes.' : 'Draft matches saved calibration.'}`,
        );
    }

    function handleMaskCanvasKeydown(event) {
        const {keyboardCell, ledInfo} = state.masks;
        if (!keyboardCell || !ledInfo) return;
        const movement = {
            ArrowLeft: [-1, 0],
            ArrowRight: [1, 0],
            ArrowUp: [0, 1],
            ArrowDown: [0, -1],
            PageUp: [0, 10],
            PageDown: [0, -10],
        }[event.key];
        if (movement) {
            event.preventDefault();
            event.stopPropagation();
            keyboardCell.strip = Math.max(0, Math.min(ledInfo.strip_count - 1, keyboardCell.strip + movement[0]));
            keyboardCell.led = Math.max(0, Math.min(ledInfo.leds_per_strip - 1, keyboardCell.led + movement[1]));
            renderMaskCanvas();
            $('maskEditorStatus').textContent = `Keyboard cursor: strip ${keyboardCell.strip + 1}, LED ${keyboardCell.led + 1}. Press Space to paint ${maskLayerById(state.masks.tool)?.label || 'Erase'}.`;
            return;
        }
        if (event.key === 'Home' || event.key === 'End') {
            event.preventDefault();
            event.stopPropagation();
            keyboardCell.strip = event.key === 'Home' ? 0 : ledInfo.strip_count - 1;
            renderMaskCanvas();
            $('maskEditorStatus').textContent = `Keyboard cursor: strip ${keyboardCell.strip + 1}, LED ${keyboardCell.led + 1}.`;
            return;
        }
        if (event.key === ' ' || event.key === 'Enter') {
            event.preventDefault();
            event.stopPropagation();
            paintKeyboardMaskCell();
        } else if (event.key === 'Delete' || event.key === 'Backspace') {
            event.preventDefault();
            event.stopPropagation();
            paintKeyboardMaskCell(0);
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
            state.masks.cells[x0 * state.masks.ledInfo.leds_per_strip + y0] = maskLayerById(state.masks.tool)?.value || 0;
            if (x0 === x1 && y0 === y1) break;
            const doubled = 2 * error;
            if (doubled >= dy) { error += dy; x0 += sx; }
            if (doubled <= dx) { error += dx; y0 += sy; }
        }
        state.masks.dirty = !masksEqual(state.masks.cells, state.masks.savedCells);
        persistMaskDraft();
        resetChecker({preserveDocumentRevision: true});
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
        persistMaskDraft();
        resetChecker({preserveDocumentRevision: true});
        renderMaskCanvas();
        updateMaskControls('Last mask stroke undone.');
    }

    function revertMasks() {
        if (!state.masks.savedCells) return;
        state.masks.history.push(state.masks.cells.slice());
        state.masks.cells = state.masks.savedCells.slice();
        state.masks.dirty = false;
        persistMaskDraft();
        resetChecker({preserveDocumentRevision: true});
        renderMaskCanvas();
        updateMaskControls('Returned to saved calibration.');
    }

    function maskIndices(value) {
        const indices = [];
        state.masks.cells?.forEach((cell, index) => { if (cell === value) indices.push(index); });
        return indices;
    }

    function currentProfileDraftDocument() {
        const globes = {};
        GLOBE_REGION_ORDER.forEach((name, index) => { globes[name] = maskIndices(index + 2); });
        return {
            schema: 'ledgrid.installation-profile-draft',
            schema_version: 1,
            digest: state.masks.digest,
            revision: state.masks.revision,
            led_info: clone(state.masks.ledInfo),
            masks: {foliage: maskIndices(1), globes},
        };
    }

    async function saveMasks() {
        if (!state.masks.dirty || !state.serverOnline) return;
        const button = $('saveMasksButton');
        button.disabled = true;
        const submittedRevision = state.masks.revision;
        updateMaskControls('Saving the managed profile draft…');
        try {
            const result = await requestJsonResource(globalActions().installation_profile_draft_url, {
                method: 'PUT',
                headers: {'If-Match': `"${submittedRevision}"`},
                body: JSON.stringify(currentProfileDraftDocument()),
            });
            loadMaskPayload(result.payload, {etag: result.etag, restoreLocal: false});
            renderMaskCanvas();
            updateMaskControls('Managed profile draft saved. Publish remains separate; the wall was not changed.', 'success');
            toast('Profile draft saved. The selected wall profile is unchanged.', 'success');
        } catch (error) {
            if (error.status === 409) {
                state.masks.stale = true;
                persistMaskDraft();
                const currentRevision = error.payload?.current_revision || error.payload?.revision;
                const current = currentRevision ? ` Server revision is ${currentRevision}.` : '';
                updateMaskControls(`Stale draft rejected.${current} Your exact local draft is preserved.`, 'error');
                toast('Stale profile draft rejected; local edits were preserved.', 'error');
            } else {
                updateMaskControls(error.message, 'error');
                toast(error.message, 'error');
            }
        } finally {
            updateMaskControls();
        }
    }

    async function publishProfileDraft() {
        if (state.masks.dirty || state.masks.stale || !state.serverOnline || !state.masks.loaded) return;
        updateMaskControls('Publishing one immutable profile candidate…');
        try {
            const result = await requestJsonResource(globalActions().installation_profile_publish_url, {
                method: 'POST',
                headers: {'If-Match': `"${state.masks.revision}"`},
            });
            const payload = result.payload;
            if (
                !/^[0-9a-f]{64}$/.test(payload.published_digest || '')
                || typeof payload.artifact_url !== 'string'
                || payload.selected !== false
            ) throw new Error('Profile publish returned an incomplete immutable candidate.');
            state.installationProfile.candidate = {
                digest: payload.published_digest,
                artifactUrl: payload.artifact_url,
                revision: payload.revision,
                receipt: clone(payload.receipt || null),
            };
            updateMaskControls(`Published ${payload.published_digest.slice(0, 12)}… as a candidate. The selected wall profile is unchanged.`, 'success');
            toast('Immutable profile candidate published; wall selection unchanged.', 'success');
        } catch (error) {
            if (error.status === 409) {
                state.masks.stale = true;
                persistMaskDraft();
                const currentRevision = error.payload?.current_revision || error.payload?.revision;
                const current = currentRevision ? ` Server revision is ${currentRevision}.` : '';
                updateMaskControls(`Publish rejected because the draft revision is stale.${current} Your local draft is preserved.`, 'error');
            } else updateMaskControls(error.message, 'error');
            toast(error.message, 'error');
        } finally {
            updateMaskControls();
        }
    }

    function reviewProfileCandidate() {
        const candidate = state.installationProfile.candidate;
        if (!candidate) return;
        $('profileCandidateDigest').textContent = candidate.digest;
        $('profileCandidateSelectedDigest').textContent = state.installationProfile.selectedDigest || 'Unavailable';
        if ($('maskEditorDialog').open) $('maskEditorDialog').close();
        showComposerModal($('profileCandidateDialog'), {
            initialFocus: $('profileCandidateDialog').querySelector('[value="cancel"]'),
            returnFocus: $('editMasksButton'),
        });
    }

    function stageProfileCandidate() {
        const candidate = state.installationProfile.candidate;
        if (!candidate) return;
        state.installationProfile.desiredDigest = candidate.digest;
        state.installationProfile.desiredArtifactUrl = candidate.artifactUrl;
        resetChecker({preserveDocumentRevision: true});
        renderLayers();
        updateMaskControls('Candidate staged for local preview and the next Check. The wall remains unchanged.', 'success');
        restartRuntimesAtCurrentState();
        toast('Profile candidate staged. Run Check before reviewed activation.');
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
        document.querySelectorAll('dialog').forEach((dialog) => {
            dialog.addEventListener('close', restoreModalFocus);
            dialog.addEventListener('keydown', trapModalFocus);
        });
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
        $('cancelActivationButton')?.addEventListener('click', cancelPendingActivation);
        $('rollbackActivationButton')?.addEventListener('click', rollbackActivation);
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
        $('publishProfileButton').addEventListener('click', publishProfileDraft);
        $('reviewProfileCandidateButton').addEventListener('click', reviewProfileCandidate);
        $('confirmProfileCandidateButton').addEventListener('click', (event) => {
            event.preventDefault();
            $('profileCandidateDialog').close();
            stageProfileCandidate();
        });
        $('maskZoom').addEventListener('input', (event) => {
            state.masks.zoom = Math.round(safeNumber(event.target.value, 6));
            renderMaskCanvas();
        });
        document.querySelectorAll('[data-mask-tool]').forEach((button) => button.addEventListener('click', () => {
            state.masks.tool = button.dataset.maskTool;
            updateMaskControls();
        }));
        $('maskCanvas').addEventListener('pointerdown', beginMaskStroke);
        $('maskCanvas').addEventListener('pointermove', continueMaskStroke);
        $('maskCanvas').addEventListener('pointerup', endMaskStroke);
        $('maskCanvas').addEventListener('pointercancel', endMaskStroke);
        $('maskCanvas').addEventListener('keydown', handleMaskCanvasKeydown);
        $('maskCanvas').addEventListener('focus', renderMaskCanvas);
        $('maskCanvas').addEventListener('blur', renderMaskCanvas);
        document.querySelectorAll('[data-mobile-target]').forEach((button) => button.addEventListener('click', () => selectMobileView(button.dataset.mobileTarget)));
        document.addEventListener('keydown', (event) => {
            const key = event.key.toLowerCase();
            if ($('maskEditorDialog').open && !event.metaKey && !event.ctrlKey && !event.altKey) {
                const layer = MASK_LAYERS.find((item) => item.key === key);
                if (layer) state.masks.tool = layer.id;
                else if (key === 'e') state.masks.tool = 'erase';
                else if (key === 'z') undoMaskStroke();
                else return;
                event.preventDefault();
                updateMaskControls();
                return;
            }
            if (document.querySelector('dialog[open]')) return;
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
                    $('controlsTab').focus();
                } else if (key === 'l') {
                    event.preventDefault();
                    selectMobileView('layers');
                    $('layersTab').focus();
                } else if (key === 'w') {
                    event.preventDefault();
                    selectMobileView('wall');
                    $('wallTab').focus();
                } else if (key === 'c') {
                    event.preventDefault();
                    selectMobileView('check');
                    $('checkerTab').focus();
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
            clearActivationPolling();
            disposeMotionPreference();
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
        initializeMotionPreference();
        updateInstallStatus();
        setServerOnline(false, {checking: true, quiet: true});
        window.requestAnimationFrame(animationLoop);
        registerServiceWorker();
        try {
            if (!ComposerRuntime) throw new Error('The browser runtime adapter did not load.');
            await loadBootstrap();
            checkConnectivity();
            state.connectivityTimer = window.setInterval(() => checkConnectivity({quiet: true}), 15000);
        } catch (error) {
            setComposerReady(false, error.message);
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
