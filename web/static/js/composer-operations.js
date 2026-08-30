(function attachComposerOperations(global) {
    'use strict';

    // This is deliberately a presentation model, not a second command path.
    // Every value carries the controller provider/revision that observed it so
    // a reconnect or an old status payload cannot acknowledge a power change.
    function outputPowerState({desired, observed, pending = false, outcome = null, provider = null, revision = null} = {}) {
        const desiredPower = typeof desired === 'boolean' ? desired : null;
        const observedPower = typeof observed === 'boolean' ? observed : null;
        const revisionQualified = typeof provider === 'string'
            && provider.trim()
            && Number.isSafeInteger(revision)
            && revision >= 0;
        let state = 'unavailable';
        let message = 'Output state is unavailable until Composer receives a revision-qualified controller observation.';
        if (!revisionQualified) {
            state = 'stale';
            message = 'Output state is stale; refresh the wall before changing power.';
        } else if (outcome?.state === 'failed' || outcome?.state === 'timed_out') {
            state = 'failed';
            message = outcome.message || 'The controller did not apply the requested output power.';
        } else if (outcome?.state === 'stale' || outcome?.state === 'reconnected') {
            state = 'stale';
            message = outcome.message || 'The controller observation is no longer current.';
        } else if (pending) {
            state = 'pending';
            message = outcome?.message || 'Waiting for a fresh controller acknowledgement of output power.';
        } else if (observedPower !== null) {
            state = observedPower ? 'on' : 'off';
            message = observedPower
                ? 'Output is on and observed by the controller.'
                : 'Output is off; the selected scene remains preserved for a checked restore.';
        }
        return Object.freeze({
            state,
            desired: desiredPower,
            observed: observedPower,
            provider: revisionQualified ? provider.trim() : null,
            revision: revisionQualified ? revision : null,
            message,
        });
    }

    function shortIdentity(identity) {
        const digest = identity?.scene_identity?.digest || identity?.digest || identity?.identity_digest;
        const revision = identity?.scene_identity?.revision ?? identity?.revision;
        if (typeof digest === 'string' && /^[0-9a-f]{16,}$/i.test(digest)) {
            return `scene ${digest.slice(0, 12)}…${Number.isSafeInteger(revision) ? ` r${revision}` : ''}`;
        }
        return identity ? 'identified output' : 'none reported';
    }

    // Keep the operational vocabulary in one small, pure adapter.  The page
    // owns polling and command authority; this only turns the approved read
    // model into concise, user-facing state labels.
    function statusPresentation(status = {}, {desiredPower = null, stop = null} = {}) {
        const observation = status?.observation || {};
        const revision = observation.revision || {};
        const reconciliation = status?.reconciliation || {};
        const power = status?.output_power || {};
        const receivers = status?.health?.receivers || {};
        const performance = status?.health?.performance || {};
        const flags = [];
        const add = (state, message) => flags.push({state, message});

        if (stop?.pending) add('pending', 'Stop is waiting for the exact safe-idle observation.');
        if (stop?.failed) add('failed', stop.message || 'Stop was not acknowledged by the controller.');
        if (observation.freshness !== 'fresh') add('stale', 'Controller observation is stale or unavailable.');
        if (reconciliation.state === 'diverged') add('divergent', reconciliation.reason || 'Observed output differs from the selected activation.');
        if (reconciliation.state === 'reconnected') add('reconnected', reconciliation.reason || 'Controller session changed; refresh before issuing a command.');
        if (receivers.state === 'degraded' || (Array.isArray(receivers.missing) && receivers.missing.length)) {
            add('partial-receiver', `Receiver coverage is partial${receivers.missing?.length ? ` (${receivers.missing.length} missing)` : ''}.`);
        }
        if (power.state === 'failed') add('failed', power.reason || 'The latest output-power request failed.');
        if (power.state === 'pending' && !stop?.pending) add('pending', power.reason || 'Output-power change is awaiting observation.');

        const uniqueFlags = flags.filter((flag, index) => (
            flags.findIndex((candidate) => candidate.state === flag.state) === index
        ));
        const session = typeof revision.session_id === 'string' ? revision.session_id : null;
        const stateRevision = Number.isSafeInteger(revision.state_revision) ? revision.state_revision : null;
        const provider = session ? `controller ${session.slice(0, 8)}…` : 'controller session unavailable';
        const observedPower = typeof power.observed === 'boolean' ? power.observed : null;
        return Object.freeze({
            state: uniqueFlags[0]?.state || observation.state || 'unavailable',
            flags: uniqueFlags,
            selectedIdentity: shortIdentity(reconciliation.desired_identity),
            activeIdentity: shortIdentity(observation.identity),
            controller: `${provider}${stateRevision == null ? '' : ` · revision ${stateRevision}`}`,
            freshness: observation.freshness || 'unknown',
            desiredPower: typeof desiredPower === 'boolean' ? desiredPower : null,
            observedPower,
            powerState: power.state || 'unknown',
            receiver: receivers.state || 'unknown',
            receiverDetail: `${Array.isArray(receivers.connected) ? receivers.connected.length : 0} connected · ${Array.isArray(receivers.missing) ? receivers.missing.length : 0} missing`,
            performance: performance.state || 'unknown',
            performanceDetail: Number.isFinite(performance.actual_fps)
                ? `${performance.actual_fps} fps${Number.isFinite(performance.target_fps) ? ` / ${performance.target_fps}` : ''}`
                : 'No current frame-rate observation',
            rawEvidenceUrl: typeof status?.raw_evidence?.url === 'string' ? status.raw_evidence.url : null,
        });
    }

    global.LEDGridComposerOperations = Object.freeze({outputPowerState, statusPresentation});
})(typeof window === 'undefined' ? globalThis : window);
