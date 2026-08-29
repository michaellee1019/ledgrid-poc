(function attachComposerState(global) {
    'use strict';

    const CHECKER_VERSION = 'browser-checker-v3';

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

    global.LEDGridComposerState = Object.freeze({
        CHECKER_VERSION,
        capability,
        checkAllowsActivation,
        checkBinding,
        clone,
        componentPresetIdentity,
        formatNumber,
        localInstallationProfile,
        normalizeNumber,
        orderedMetricStats,
        runtimeDigest,
        sameCheckBinding,
        stableJson,
    });
})(typeof window === 'undefined' ? globalThis : window);
