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

    global.LEDGridComposerOperations = Object.freeze({outputPowerState});
})(typeof window === 'undefined' ? globalThis : window);
