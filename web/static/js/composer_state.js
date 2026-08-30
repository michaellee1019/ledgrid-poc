(function attachComposerState(global) {
    'use strict';

    const CHECKER_VERSION = 'browser-checker-v4';

    function clone(value) {
        return JSON.parse(JSON.stringify(value ?? null));
    }

    function stableJson(value) {
        if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`;
        if (value && typeof value === 'object') {
            return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(',')}}`;
        }
        return JSON.stringify(value);
    }

    function decimalPlaces(value) {
        const text = String(value ?? '');
        if (/e-/i.test(text)) {
            const [coefficient, exponent] = text.toLowerCase().split('e-');
            return Number(exponent) + (coefficient.split('.')[1]?.length || 0);
        }
        return text.split('.')[1]?.length || 0;
    }

    function normalizeNumber(raw, type, contract = {}) {
        const integer = type === 'int' || type === 'integer';
        let value = Number(raw);
        if (!Number.isFinite(value)) value = Number(contract.default);
        if (!Number.isFinite(value)) value = Number(contract.min);
        if (!Number.isFinite(value)) value = 0;
        if (integer) value = Math.round(value);

        const minimum = Number(contract.min);
        const maximum = Number(contract.max);
        if (Number.isFinite(minimum)) value = Math.max(minimum, value);
        if (Number.isFinite(maximum)) value = Math.min(maximum, value);

        const step = Number(contract.step);
        if (Number.isFinite(step) && step > 0) {
            const origin = Number.isFinite(minimum) ? minimum : 0;
            value = origin + Math.round((value - origin) / step) * step;
            const precision = Math.min(12, Math.max(decimalPlaces(step), decimalPlaces(origin)));
            value = Number(value.toFixed(precision));
            if (Number.isFinite(minimum)) value = Math.max(minimum, value);
            if (Number.isFinite(maximum)) value = Math.min(maximum, value);
        }
        if (integer) value = Math.round(value);
        return Object.is(value, -0) ? 0 : value;
    }

    function formatNumber(value, step = null) {
        const numeric = Number(value);
        if (!Number.isFinite(numeric)) return String(value);
        const precision = Math.min(6, decimalPlaces(step));
        return precision ? numeric.toFixed(precision) : String(Math.round(numeric));
    }

    function orderedMetricStats(values) {
        if (!Array.isArray(values) || !values.length || values.some((value) => !Number.isFinite(value))) {
            throw new TypeError('Metric samples must be a non-empty array of finite numbers.');
        }
        const sorted = values.slice().sort((left, right) => left - right);
        const nearestRank = (fraction) => sorted[
            Math.min(sorted.length - 1, Math.ceil(sorted.length * fraction) - 1)
        ];
        const mean = values.reduce((total, value) => total + value, 0) / values.length;
        // A sufficiently heavy tail can make the arithmetic mean exceed a
        // nearest-rank percentile. Qualification records require monotonic
        // summary fields, so preserve the mean and order later bounds upward.
        const p95 = Math.max(mean, nearestRank(.95));
        const p99 = Math.max(p95, nearestRank(.99));
        const maximum = Math.max(p99, sorted[sorted.length - 1]);
        return {mean, p95, p99, max: maximum};
    }

    function advisoryRenderStatus(p95, frameBudgetMs) {
        if (!Number.isFinite(p95) || !Number.isFinite(frameBudgetMs) || frameBudgetMs <= 0) {
            throw new TypeError('Render timing and frame budget must be finite positive numbers.');
        }
        // Browser timing includes the preview bridge and local compositor. Keep
        // it visible as a caution, but do not let host-specific scheduling noise
        // block a reviewed development/canary activation.
        return p95 > frameBudgetMs * .5 ? 'warn' : 'pass';
    }

    function runtimeDigest(component) {
        const runtime = component?.browser_runtime || {};
        const build = component?.build || {};
        return runtime.digest
            || runtime.asset_digest
            || build.bundle_digest
            || build.contract_digest
            || build.expected_payload_digest
            || null;
    }

    function componentPresetIdentity(preset) {
        if (!preset || typeof preset !== 'object') return null;
        const presetId = preset.preset_id ?? preset.presetId;
        const presetFingerprint = preset.preset_fingerprint ?? preset.presetFingerprint;
        if (!presetId || !presetFingerprint) return null;
        return {
            presetId: String(presetId),
            presetFingerprint: String(presetFingerprint),
        };
    }

    function localInstallationProfile(bootstrap) {
        const profile = bootstrap?.installation_profile;
        const digest = String(profile?.digest || '').toLowerCase();
        if (!/^[0-9a-f]{64}$/.test(digest) || /^0+$/.test(digest)) return null;
        if (typeof profile?.artifact_url !== 'string' || !profile.artifact_url) return null;
        return {
            digest,
            artifactUrl: profile.artifact_url,
        };
    }

    function capability(component) {
        const declared = component?.browser_capabilities || component?.activation_capability || component?.capability || {};
        const previewable = declared.previewable ?? Boolean(component?.browser_runtime?.supported);
        const saveable = declared.saveable ?? previewable;
        const activationReady = declared.activation_ready ?? Boolean(
            previewable
            && component?.scene_compatibility?.selectable !== false
            && component?.availability?.state !== 'unavailable'
        );
        return {
            previewable: Boolean(previewable),
            saveable: Boolean(saveable),
            activationReady: Boolean(activationReady),
            reason: activationReady ? null : (declared.reason || component?.browser_runtime?.reason || 'This renderer is not activation-ready.'),
            managedIdentity: declared.managed_identity || null,
        };
    }

    function checkBinding(
        draftGeneration,
        component,
        geometry,
        wallSettings = null,
        installationProfileDigest = null,
        preset = null,
    ) {
        return {
            checkerVersion: CHECKER_VERSION,
            draftGeneration,
            componentKey: component?.key || null,
            runtimeDigest: runtimeDigest(component),
            geometry: {
                stripCount: Number(geometry?.strip_count),
                ledsPerStrip: Number(geometry?.leds_per_strip),
                totalLeds: Number(geometry?.total_leds),
            },
            wallSettings: clone(wallSettings),
            installationProfileDigest,
            presetIdentity: componentPresetIdentity(preset),
        };
    }

    function sameCheckBinding(left, right) {
        return Boolean(left && right && stableJson(left) === stableJson(right));
    }

    function checkAllowsActivation(result, expectedBinding) {
        return Boolean(
            result
            && ['pass', 'warn'].includes(result.status)
            && sameCheckBinding(result.binding, expectedBinding)
        );
    }

    /**
     * Keep a browser request and the controller's later observation in one
     * small, portable contract.  A successful HTTP command is only an
     * acceptance; it cannot itself prove that the controller applied the
     * desired state.  The provider (controller session) and revision make a
     * pre-reconnect or pre-command snapshot unable to acknowledge a change.
     */
    function createWallReconciliation({provider, revision, desired, issuedAt = Date.now()} = {}) {
        if (typeof provider !== 'string' || !provider.trim()) {
            throw new TypeError('A controller provider identity is required for wall reconciliation.');
        }
        if (!Number.isSafeInteger(revision) || revision < 0) {
            throw new TypeError('A non-negative controller revision is required for wall reconciliation.');
        }
        if (!desired || typeof desired !== 'object') {
            throw new TypeError('A desired wall state is required for reconciliation.');
        }
        if (!Number.isFinite(issuedAt) || issuedAt < 0) {
            throw new TypeError('A finite reconciliation issue time is required.');
        }
        return Object.freeze({
            schema: 'ledgrid.composer-wall-reconciliation',
            schemaVersion: 1,
            provider: provider.trim(),
            revision,
            desired: clone(desired),
            issuedAt,
        });
    }

    function reconcileWallObservation(pending, {
        provider,
        revision,
        observed,
        observedAt = null,
        fresh = null,
    } = {}, now = Date.now()) {
        if (!pending) return Object.freeze({
            state: 'idle', acknowledged: false, retryable: false,
            message: 'No wall change is awaiting acknowledgement.',
        });
        if (typeof provider !== 'string' || !provider.trim()) return Object.freeze({
            state: 'stale', acknowledged: false, retryable: false,
            message: 'Wall identity is unavailable; refresh Wall settings before retrying.',
        });
        if (!Number.isSafeInteger(revision) || revision < 0) return Object.freeze({
            state: 'stale', acknowledged: false, retryable: false,
            message: 'Wall revision is unavailable; refresh Wall settings before retrying.',
        });
        if (!observed || typeof observed !== 'object') return Object.freeze({
            state: 'stale', acknowledged: false, retryable: false,
            message: 'Wall settings are incomplete; refresh before retrying.',
        });
        if (provider !== pending.provider) return Object.freeze({
            state: 'reconnected', acknowledged: false, retryable: true,
            message: 'The controller reconnected. Review the wall change again to retry against its new session.',
        });
        const timestampIsFresh = Number.isFinite(observedAt)
            && observedAt <= now + 5000
            && now - observedAt <= 15000;
        if (fresh !== true && !(fresh == null && timestampIsFresh)) return Object.freeze({
            state: 'stale', acknowledged: false, retryable: false,
            message: 'Waiting for a fresh wall observation. Refresh Wall settings to retry now.',
        });
        if (revision <= pending.revision) return Object.freeze({
            state: 'waiting', acknowledged: false, retryable: false,
            message: 'Commands were accepted; waiting for the controller revision that acknowledges them.',
        });
        if (!sameCheckBinding(pending.desired, observed)) return Object.freeze({
            state: 'mismatch', acknowledged: false, retryable: true,
            message: 'The fresh wall state differs from the reviewed change. Refresh or review again to retry.',
        });
        return Object.freeze({
            state: 'acknowledged', acknowledged: true, retryable: false,
            message: 'The controller acknowledged the reviewed Wall settings.',
        });
    }

    global.LEDGridComposerState = Object.freeze({
        CHECKER_VERSION,
        advisoryRenderStatus,
        capability,
        checkAllowsActivation,
        checkBinding,
        clone,
        componentPresetIdentity,
        createWallReconciliation,
        formatNumber,
        localInstallationProfile,
        normalizeNumber,
        orderedMetricStats,
        runtimeDigest,
        reconcileWallObservation,
        sameCheckBinding,
        stableJson,
    });
})(typeof window === 'undefined' ? globalThis : window);
