(function attachComposerState(global) {
    'use strict';

    const CHECKER_VERSION = 'browser-checker-v4';

    function clone(value) {
        return JSON.parse(JSON.stringify(value ?? null));
    }

    function createLatestStateQueue() {
        let revision = 0;
        let queued = null;
        let inFlight = null;
        let outcome = null;
        const snapshot = (intent) => Object.freeze({
            revision,
            intent: Object.freeze(clone(intent)),
        });
        return Object.freeze({
            enqueue(intent) {
                revision += 1;
                queued = snapshot(intent);
                outcome = null;
                return queued;
            },
            begin() {
                if (inFlight || !queued) return null;
                inFlight = queued;
                queued = null;
                return inFlight;
            },
            finish(entry, nextOutcome = null) {
                if (!inFlight || entry?.revision !== inFlight.revision) return false;
                inFlight = null;
                const current = entry.revision === revision;
                if (current && nextOutcome) outcome = Object.freeze(clone(nextOutcome));
                return current;
            },
            invalidate(nextOutcome = null) {
                revision += 1;
                queued = null;
                if (nextOutcome) outcome = Object.freeze(clone(nextOutcome));
                return revision;
            },
            currentRevision() { return revision; },
            hasQueued() { return Boolean(queued); },
            hasInFlight() { return Boolean(inFlight); },
            outcome() { return outcome; },
        });
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

    const LIBRARY_RECENTS_LIMIT = 12;
    const LIBRARY_FAVORITES_LIMIT = 128;
    const LIBRARY_DISCOVERY_BATCH_SIZE = 24;
    const LIBRARY_DISCOVERY_SCHEMA_VERSION = 1;
    const LIBRARY_DISCOVERY_MATERIALS = new Set(['show', 'test', 'calibration']);

    /**
     * A library entry is deliberately only a catalog identity.  It never
     * contains authored parameters or wall state, so saving or replaying one
     * is a local discovery action rather than a live-wall command.
     */
    function normalizeLibrarySelection(value) {
        if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
        const provider = typeof value.provider === 'string' ? value.provider.trim() : '';
        const component = typeof (value.component ?? value.plugin_id) === 'string'
            ? String(value.component ?? value.plugin_id).trim()
            : '';
        const presetValue = value.preset;
        const preset = presetValue == null || presetValue === '' ? null
            : (typeof presetValue === 'string' ? presetValue.trim() : '');
        if (!provider || !component || (presetValue != null && presetValue !== '' && !preset)) return null;
        if (provider.length > 120 || component.length > 240 || (preset && preset.length > 240)) return null;
        return Object.freeze({provider, component, preset: preset || null});
    }

    function librarySelectionKey(value) {
        const selection = normalizeLibrarySelection(value);
        return selection ? JSON.stringify([selection.provider, selection.component, selection.preset || '']) : null;
    }

    // Do not use localeCompare for persisted/library ordering. Its result can
    // differ with the browser's configured locale, while these identities are
    // replayed from local storage and shared URLs.
    function compareLibraryText(left, right) {
        const first = String(left || '');
        const second = String(right || '');
        return first === second ? 0 : (first < second ? -1 : 1);
    }

    function uniqueLibrarySelections(values, {limit = LIBRARY_RECENTS_LIMIT, sort = false} = {}) {
        if (!Array.isArray(values) || !Number.isSafeInteger(limit) || limit < 1) return [];
        const seen = new Set();
        const entries = [];
        values.forEach((value) => {
            const selection = normalizeLibrarySelection(value);
            const key = selection && librarySelectionKey(selection);
            if (!key || seen.has(key) || entries.length >= limit) return;
            seen.add(key);
            entries.push(selection);
        });
        if (sort) entries.sort((left, right) => compareLibraryText(librarySelectionKey(left), librarySelectionKey(right)));
        return entries;
    }

    function toggleLibraryFavorite(favorites, selection) {
        const normalized = normalizeLibrarySelection(selection);
        if (!normalized) return uniqueLibrarySelections(favorites, {limit: LIBRARY_FAVORITES_LIMIT, sort: true});
        const key = librarySelectionKey(normalized);
        const current = uniqueLibrarySelections(favorites, {limit: LIBRARY_FAVORITES_LIMIT, sort: false});
        const exists = current.some((item) => librarySelectionKey(item) === key);
        return uniqueLibrarySelections(
            exists ? current.filter((item) => librarySelectionKey(item) !== key) : [...current, normalized],
            {limit: LIBRARY_FAVORITES_LIMIT, sort: true},
        );
    }

    function recordLibraryRecent(recents, selection) {
        const normalized = normalizeLibrarySelection(selection);
        if (!normalized) return uniqueLibrarySelections(recents, {limit: LIBRARY_RECENTS_LIMIT});
        const key = librarySelectionKey(normalized);
        return uniqueLibrarySelections(
            [normalized, ...uniqueLibrarySelections(recents, {limit: LIBRARY_RECENTS_LIMIT}).filter((item) => librarySelectionKey(item) !== key)],
            {limit: LIBRARY_RECENTS_LIMIT},
        );
    }

    function resolveLibrarySelection(selection, components) {
        const normalized = normalizeLibrarySelection(selection);
        if (!normalized || !Array.isArray(components)) return null;
        const component = components.find((item) => (
            item?.provider === normalized.provider
            && item?.plugin_id === normalized.component
            && item?.role === 'background'
        ));
        if (!component) return null;
        const presetIndex = normalized.preset == null ? null : (component.presets || []).findIndex((item, index) => (
            String(item?.key || item?.preset_id || item?.id || `preset-${index}`) === normalized.preset
        ));
        return Object.freeze({component, presetIndex: presetIndex == null || presetIndex < 0 ? null : presetIndex});
    }

    function normalizedSearchText(value) {
        return String(value || '').trim().toLowerCase();
    }

    function libraryDiscoveryClassification(component, preset = null) {
        const materials = [preset, component].filter((item) => item && typeof item === 'object');
        const labels = materials.flatMap((item) => [
            item.library_classification,
            item.discovery_classification,
            item.classification,
            item.gallery,
            item.category,
            ...(Array.isArray(item.tags) ? item.tags : []),
        ]).map(normalizedSearchText);
        const identifiers = materials.flatMap((item) => [
            item.plugin_id, item.key, item.name, item.description,
        ]).map(normalizedSearchText);
        if (
            materials.some((item) => item.is_calibration === true)
            || labels.includes('calibration')
            || identifiers.some((value) => /(^|[_:\-\s])calibration($|[_:\-\s])/.test(value))
        ) return 'calibration';
        if (
            materials.some((item) => item.is_test === true)
            || labels.includes('test')
            || identifiers.some((value) => /(^|[_:\-\s])test($|[_:\-\s])/.test(value))
        ) return 'test';
        // Unknown or legacy material remains a show item. Legacy bootstrap
        // entries predate explicit taxonomy, so only whole-word stable IDs and
        // labels supply the compatibility classification.
        return 'show';
    }

    function libraryDiscoveryEntries(components) {
        if (!Array.isArray(components)) return [];
        const entries = [];
        const keys = new Set();
        components.forEach((component) => {
            if (!component || component.role !== 'background') return;
            const selection = normalizeLibrarySelection({
                provider: component.provider,
                component: component.plugin_id,
            });
            if (!selection) return;
            const capability = component?.browser_capabilities || {};
            const previewable = capability.previewable ?? Boolean(component?.browser_runtime?.supported);
            const componentTags = Array.isArray(component.tags) ? component.tags : [];
            const base = {
                component,
                selection,
                provider: selection.provider,
                runtimeKind: component?.browser_runtime?.kind === 'native' ? 'native' : 'python',
                previewable: Boolean(previewable),
            };
            const createEntry = ({preset = null, presetIndex = null} = {}) => {
                const identity = preset == null ? selection : normalizeLibrarySelection({
                    ...selection,
                    preset: String(preset?.key || preset?.preset_id || preset?.id || `preset-${presetIndex}`),
                });
                if (!identity) return null;
                const classification = libraryDiscoveryClassification(component, preset);
                const key = librarySelectionKey(identity);
                if (!key || keys.has(key)) return null;
                keys.add(key);
                const category = String(preset?.category || component.category || '').trim();
                const tags = [...new Set([...componentTags, ...(Array.isArray(preset?.tags) ? preset.tags : [])]
                    .map((tag) => String(tag).trim()).filter(Boolean))];
                const name = String(preset?.name || component.name || component.plugin_id || '').trim();
                const description = String(preset?.description || component.description || '').trim();
                const searchText = normalizedSearchText([
                    name, description, component.name, component.description,
                    component.provider, component.plugin_id, component.key, component.role,
                    preset?.key, preset?.preset_id, preset?.id, category, ...tags,
                ].filter(Boolean).join(' '));
                return Object.freeze({
                    ...base,
                    schemaVersion: LIBRARY_DISCOVERY_SCHEMA_VERSION,
                    kind: preset == null ? 'renderer' : 'preset',
                    preset,
                    presetIndex,
                    selection: identity,
                    key,
                    name,
                    description,
                    category,
                    tags: Object.freeze(tags),
                    searchText,
                    classification,
                    // Discovery is deliberately read-only. Test and
                    // calibration entries remain classified in generated
                    // metadata, but never enter the default user library.
                    eligible: Boolean(previewable) && classification === 'show',
                });
            };
            const renderer = createEntry();
            if (renderer) entries.push(renderer);
            (component.presets || []).forEach((preset, presetIndex) => {
                const entry = createEntry({preset, presetIndex});
                if (entry) entries.push(entry);
            });
        });
        return entries.sort((left, right) => (
            compareLibraryText(left.name, right.name)
            || compareLibraryText(left.kind, right.kind)
            || compareLibraryText(left.provider, right.provider)
            || compareLibraryText(left.key, right.key)
        ));
    }

    function libraryDiscoveryCategories(entries) {
        if (!Array.isArray(entries)) return [];
        return [...new Set(entries.map((entry) => String(entry?.category || '').trim()).filter(Boolean))]
            .sort(compareLibraryText);
    }

    function filterLibraryDiscoveryEntries(entries, filters = {}) {
        if (!Array.isArray(entries)) return [];
        const query = normalizedSearchText(filters.query);
        const runtime = filters.runtime || 'all';
        const kind = filters.kind || 'all';
        const category = filters.category || 'all';
        const saved = filters.saved || 'all';
        const classification = LIBRARY_DISCOVERY_MATERIALS.has(filters.classification)
            ? filters.classification
            : 'all';
        const includeIneligible = filters.includeIneligible === true;
        const favoriteKeys = new Set(uniqueLibrarySelections(filters.favorites, {limit: LIBRARY_FAVORITES_LIMIT, sort: false})
            .map(librarySelectionKey).filter(Boolean));
        const recentKeys = new Set(uniqueLibrarySelections(filters.recents, {limit: LIBRARY_RECENTS_LIMIT, sort: false})
            .map(librarySelectionKey).filter(Boolean));
        const visible = entries.filter((entry) => (
            entry
            && (includeIneligible || entry.eligible === true)
            && (!query || entry.searchText.includes(query))
            && (runtime === 'all' || entry.runtimeKind === runtime)
            && (kind === 'all' || entry.kind === kind)
            && (category === 'all' || entry.category === category)
            && (classification === 'all' || entry.classification === classification)
            && (saved === 'all'
                || (saved === 'favorites' && favoriteKeys.has(entry.key))
                || (saved === 'recent' && recentKeys.has(entry.key)))
        ));
        if (saved === 'recent') {
            return visible.sort((left, right) => recentKeys.has(left.key) && recentKeys.has(right.key)
                ? uniqueLibrarySelections(filters.recents, {limit: LIBRARY_RECENTS_LIMIT})
                    .map(librarySelectionKey).indexOf(left.key)
                    - uniqueLibrarySelections(filters.recents, {limit: LIBRARY_RECENTS_LIMIT})
                        .map(librarySelectionKey).indexOf(right.key)
                : 0);
        }
        return visible;
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
        createLatestStateQueue,
        createWallReconciliation,
        formatNumber,
        localInstallationProfile,
        LIBRARY_FAVORITES_LIMIT,
        LIBRARY_DISCOVERY_BATCH_SIZE,
        LIBRARY_RECENTS_LIMIT,
        filterLibraryDiscoveryEntries,
        libraryDiscoveryCategories,
        libraryDiscoveryEntries,
        libraryDiscoveryClassification,
        librarySelectionKey,
        LIBRARY_DISCOVERY_SCHEMA_VERSION,
        normalizeNumber,
        normalizeLibrarySelection,
        orderedMetricStats,
        runtimeDigest,
        recordLibraryRecent,
        reconcileWallObservation,
        resolveLibrarySelection,
        sameCheckBinding,
        stableJson,
        toggleLibraryFavorite,
        uniqueLibrarySelections,
    });
})(typeof window === 'undefined' ? globalThis : window);
