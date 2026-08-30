(function attachComposerMaintenance(global) {
    'use strict';

    const registry = global.LEDGridComposerModules;
    if (!registry) throw new Error('Composer maintenance requires the Composer module registry.');

    const receiverDiagnostics = new Set(['receiver_band', 'sparse_boundary']);
    const stripDiagnostics = new Set(['strip_ramp', 'direction_sentinel']);
    const terminalPhases = new Set(['restored', 'safe_idle', 'rejected', 'failed']);

    function resolveCapability(state) {
        const value = state?.serverBootstrap?.capabilities?.maintenance;
        return state?.serverOnline === true
            && state?.serverChecking !== true
            && value?.available === true
            && value.execution === 'controller_file_channel'
            ? value : null;
    }

    global.LEDGridComposerMaintenance = Object.freeze({resolveCapability});

    registry.register('guarded-maintenance', ({state, dom, events, runtime}) => {
        const $ = dom.byId;
        const panel = $('maintenancePanel');
        const diagnostic = $('maintenanceDiagnostic');
        const target = $('maintenanceTarget');
        const targetField = $('maintenanceTargetField');
        const lane = $('maintenanceLane');
        const laneField = $('maintenanceLaneField');
        const intensity = $('maintenanceIntensity');
        const duration = $('maintenanceDuration');
        const run = $('maintenanceRunButton');
        const result = $('maintenanceResult');
        let timer = null;
        let activeRequestId = null;

        function capability() {
            return resolveCapability(state);
        }

        function number(input, fallback) {
            const value = Number(input.value);
            return Number.isFinite(value) ? value : fallback;
        }

        function clamp(value, lower, upper) { return Math.max(lower, Math.min(upper, value)); }

        function newRequestId() {
            const crypto = runtime.window.crypto;
            if (typeof crypto?.randomUUID === 'function') return crypto.randomUUID();
            if (typeof crypto?.getRandomValues === 'function') {
                const bytes = crypto.getRandomValues(new Uint8Array(16));
                bytes[6] = (bytes[6] & 0x0f) | 0x40;
                bytes[8] = (bytes[8] & 0x3f) | 0x80;
                const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
                return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
            }
            throw new Error('This browser cannot create an immutable maintenance request ID.');
        }

        function requestBody() {
            const available = capability();
            if (!available) throw new Error('Guarded maintenance is unavailable on this controller.');
            const kind = diagnostic.value;
            const chosen = Math.round(number(target, 0));
            let selectedTarget;
            if (receiverDiagnostics.has(kind)) selectedTarget = {receiver_id: clamp(chosen, 0, 4)};
            else if (stripDiagnostics.has(kind)) selectedTarget = {strip: clamp(chosen, 0, 32)};
            else if (kind === 'tail_lane_probe') selectedTarget = {
                receiver_id: 4,
                lane: clamp(Math.round(number(lane, 0)), 0, 7),
            };
            else throw new Error('Choose one of the reviewed maintenance diagnostics.');
            return {
                diagnostic: kind,
                target: selectedTarget,
                intensity: clamp(Math.round(number(intensity, 32)), 1, available.max_intensity),
                duration_seconds: clamp(number(duration, 1), 0.1, available.max_duration_seconds),
            };
        }

        function formatTerminal(payload) {
            const status = payload.status || {};
            const resultEnvelope = payload.result || status.result || {};
            const resultPayload = resultEnvelope.result || resultEnvelope;
            const receipt = resultPayload.receipt || resultPayload.acknowledgement || {};
            const acknowledged = receipt.acknowledged_receivers;
            const identity = payload.request?.expected_identity || {};
            const authority = status.authority_digest || resultPayload.authority_digest || 'unavailable';
            const restore = resultPayload.restore_receipt || resultPayload.restoration || {};
            const receiptIds = Array.isArray(acknowledged)
                ? acknowledged.map((item) => Number(item?.logical_device)).sort((a, b) => a - b) : [];
            const restoreIds = Array.isArray(restore.acknowledged_receivers)
                ? restore.acknowledged_receivers.map((item) => Number(item?.logical_device)).sort((a, b) => a - b) : [];
            const complete = JSON.stringify(receiptIds) === '[0,1,2,3,4]';
            const restored = JSON.stringify(restoreIds) === '[0,1,2,3,4]';
            const evidence = `session ${identity.controller_session_id || 'unavailable'} · revision ${identity.controller_state_revision ?? 'unavailable'} · authority ${String(authority).slice(0, 12)} · receipt ${receiptIds.join(',') || 'unavailable'} · restore ${restoreIds.join(',') || 'unavailable'}`;
            if (payload.phase === 'restored' && complete && restored) {
                return `Restored · ${evidence}`;
            }
            return `${payload.phase} · ${status.error || 'The controller did not report successful restoration.'} · ${evidence}`;
        }

        function render() {
            const available = capability();
            panel.hidden = !available;
            if (!available) return;
            const kind = diagnostic.value;
            const receiver = receiverDiagnostics.has(kind);
            const strip = stripDiagnostics.has(kind);
            laneField.hidden = kind !== 'tail_lane_probe';
            if (receiver) {
                targetField.firstChild.textContent = 'Receiver'; target.min = '0'; target.max = '4'; target.disabled = false;
            } else if (strip) {
                targetField.firstChild.textContent = 'Strip'; target.min = '0'; target.max = '32'; target.disabled = false;
            } else {
                targetField.firstChild.textContent = 'Tail receiver (fixed)'; target.value = '4'; target.min = '4'; target.max = '4'; target.disabled = true;
            }
            intensity.max = String(available.max_intensity);
            duration.max = String(available.max_duration_seconds);
            run.disabled = Boolean(activeRequestId) || !state.serverOnline || state.serverChecking;
        }

        function clearPoll() { if (timer) runtime.window.clearTimeout(timer); timer = null; }

        async function poll() {
            if (!activeRequestId) return;
            try {
                const response = await runtime.window.fetch(`/api/v1/composer/maintenance/${encodeURIComponent(activeRequestId)}`, {headers: {Accept: 'application/json'}});
                const payload = await response.json();
                if (!response.ok) throw new Error(payload?.error || 'Maintenance status could not be read.');
                result.textContent = terminalPhases.has(payload.phase)
                    ? formatTerminal(payload)
                    : `${payload.phase} · request ${activeRequestId} is controller-owned and has not succeeded yet.`;
                if (terminalPhases.has(payload.phase)) {
                    clearPoll(); activeRequestId = null; run.dataset.busy = 'false'; render(); return;
                }
                timer = runtime.window.setTimeout(poll, 500);
            } catch (error) {
                result.textContent = `Maintenance status unavailable · ${error.message || 'unknown failure'}. It is not successful.`;
                timer = runtime.window.setTimeout(poll, 1000);
            }
        }

        async function execute() {
            if (activeRequestId) return;
            try {
                const requestId = newRequestId();
                activeRequestId = requestId; render(); run.dataset.busy = 'true';
                result.textContent = `Queued · immutable request ${requestId} is awaiting the controller.`;
                const available = capability();
                if (!available) throw new Error('Guarded maintenance is unavailable on this controller.');
                const response = await runtime.window.fetch(available.url, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json', Accept: 'application/json', 'Idempotency-Key': requestId},
                    body: JSON.stringify(requestBody()),
                });
                const payload = await response.json();
                if (!response.ok) throw new Error(payload?.error || 'Maintenance request was rejected.');
                result.textContent = `${payload.phase} · immutable request ${payload.request_id} is awaiting the controller.`;
                void poll();
            } catch (error) {
                activeRequestId = null; run.dataset.busy = 'false'; render();
                result.textContent = `Rejected · ${error.message || 'Maintenance was not accepted.'}`;
            }
        }

        function requestRun(event) {
            event.preventDefault();
            void execute();
        }

        events.on(diagnostic, 'change', render);
        events.on(run, 'click', requestRun);
        events.on(dom.document, 'composer:bootstrap', render);
        events.on(dom.document, 'composer:capability-change', render);
        events.on(runtime.window, 'beforeunload', clearPoll);
        render();
    });
})(window);
