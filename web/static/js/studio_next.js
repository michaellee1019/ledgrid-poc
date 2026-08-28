(() => {
  'use strict';

  const POLL_MS = 2000;
  const OBSERVATION_TIMEOUT_MS = 15000;
  // The existing animation-speed endpoint accepts a multiplier of the current
  // backend baseline, while status exposes the resulting absolute scale.
  const OPERATOR_SPEED_BASELINE = 0.3;
  const REDUCED_MOTION = window.matchMedia('(prefers-reduced-motion: reduce)');
  const WORKSPACES = ['now', 'looks', 'scene', 'room', 'health'];
  const PROVIDER_LABELS = {
    python: 'Host Python',
    receiver_native: 'Receiver native'
  };
  const FIELD_MODIFIERS = new Set(['attractor', 'repulsor', 'slow_zone']);
  const SURFACE_MODIFIERS = new Set(['obstacle', 'portal', 'bumper', 'hazard', 'habitat']);
  const LIGHT_MODIFIERS = new Set(['illuminate', 'shadow', 'refract', 'hue_shift', 'liquid_glass']);
  const INTENT_WORDS = {
    settle: ['settle', 'quiet', 'calm', 'slow', 'soft', 'ambient', 'night', 'breathe', 'tidal'],
    welcome: ['welcome', 'warm', 'garden', 'glow', 'sun', 'gold', 'cozy', 'rainbow'],
    focus: ['focus', 'clock', 'time', 'info', 'steady', 'minimal', 'graph', 'status'],
    play: ['play', 'game', 'interactive', 'pong', 'snake', 'maze', 'party', 'spark', 'pixel']
  };

  const state = {
    bootstrap: null,
    localMode: null,
    live: null,
    lastStatusError: null,
    components: [],
    componentsByKey: new Map(),
    looks: [],
    looksByKey: new Map(),
    selectedLookKey: null,
    compareKeys: [],
    filters: {search: '', intent: 'all', provider: 'all', role: 'all', readiness: 'all'},
    workspace: 'now',
    receipts: [],
    review: null,
    previewPlaying: false,
    previewTick: 0,
    scene: {
      baseSceneRevision: 0,
      baseLiveFingerprint: null,
      draftRevision: 0,
      validatedRevision: null,
      validatedScene: null,
      dirty: false,
      drift: false,
      lastError: null
    },
    room: {
      observed: null,
      draft: null,
      touched: false
    },
    lastDialogOpener: null,
    pollHandle: null
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const text = (selector, value, root = document) => {
    const node = typeof selector === 'string' ? $(selector, root) : selector;
    if (node) node.textContent = value == null ? '' : String(value);
  };

  function element(tag, attributes = {}, children = []) {
    const node = document.createElement(tag);
    Object.entries(attributes).forEach(([key, value]) => {
      if (value == null || value === false) return;
      if (key === 'class') node.className = value;
      else if (key === 'text') node.textContent = String(value);
      else if (key === 'dataset') Object.assign(node.dataset, value);
      else if (key in node && key !== 'role') node[key] = value;
      else node.setAttribute(key, String(value));
    });
    const items = Array.isArray(children) ? children : [children];
    items.forEach((child) => {
      if (child == null) return;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    });
    return node;
  }

  function announce(message, assertive = false) {
    const node = assertive ? $('#assertiveStatus') : $('#politeStatus');
    if (!node) return;
    node.textContent = '';
    window.requestAnimationFrame(() => { node.textContent = message; });
  }

  function errorMessage(value) {
    if (value instanceof Error) return value.message;
    if (typeof value === 'string') return value;
    return 'The request could not be completed.';
  }

  async function requestJSON(url, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
    const response = await fetch(url, {...options, headers, cache: options.cache || 'no-store'});
    let payload = null;
    try {
      payload = await response.json();
    } catch (_error) {
      payload = null;
    }
    if (!response.ok) {
      const detail = payload?.error || payload?.message || `${response.status} ${response.statusText}`;
      const problem = new Error(detail);
      problem.status = response.status;
      problem.payload = payload;
      throw problem;
    }
    return payload ?? {};
  }

  function asNumber(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function sourceTime(value) {
    if (value == null) return null;
    if (typeof value === 'number' || /^\d+(\.\d+)?$/.test(String(value))) {
      const numeric = Number(value);
      if (!Number.isFinite(numeric)) return null;
      return numeric < 1e12 ? numeric * 1000 : numeric;
    }
    const parsed = Date.parse(String(value));
    return Number.isFinite(parsed) ? parsed : null;
  }

  function formatAge(milliseconds) {
    if (!Number.isFinite(milliseconds)) return 'Source time unavailable';
    const seconds = Math.max(0, milliseconds / 1000);
    if (seconds < 1) return 'Less than 1s ago';
    if (seconds < 60) return `${Math.round(seconds)}s ago`;
    return `${Math.round(seconds / 60)}m ago`;
  }

  function formatClock(milliseconds) {
    if (!Number.isFinite(milliseconds)) return 'time unavailable';
    return new Intl.DateTimeFormat(undefined, {
      hour: 'numeric', minute: '2-digit', second: '2-digit'
    }).format(new Date(milliseconds));
  }

  function displayName(value) {
    return String(value || '')
      .replace(/[_-]+/g, ' ')
      .replace(/\b\w/g, (letter) => letter.toUpperCase()) || 'Unnamed';
  }

  function providerLabel(provider) {
    return PROVIDER_LABELS[provider] || `Unknown provider (${provider || 'missing'})`;
  }

  function stable(value) {
    if (Array.isArray(value)) return value.map(stable);
    if (value && typeof value === 'object') {
      return Object.keys(value).sort().reduce((result, key) => {
        result[key] = stable(value[key]);
        return result;
      }, {});
    }
    return value;
  }

  function stableString(value) {
    return JSON.stringify(stable(value));
  }

  function nested(object, paths) {
    for (const path of paths) {
      let current = object;
      let found = true;
      for (const part of path.split('.')) {
        if (!current || typeof current !== 'object' || !(part in current)) {
          found = false;
          break;
        }
        current = current[part];
      }
      if (found && current != null) return current;
    }
    return null;
  }

  function normalizeVibe(raw) {
    const candidate = nested(raw, [
      'vibe.id', 'vibe.vibe_id', 'vibe.state.id', 'vibe.state.vibe_id',
      'vibe.profile.id', 'vibe_id', 'selected_vibe.id', 'selected_vibe'
    ]);
    return candidate == null ? null : String(candidate).toLowerCase();
  }

  function normalizePlant(raw) {
    const source = raw?.plant_modifiers && typeof raw.plant_modifiers === 'object'
      ? raw.plant_modifiers
      : {version: 1, active: [], strengths: {}};
    const active = Array.isArray(source.active)
      ? source.active.map(String).filter(Boolean)
      : Object.entries(source).filter(([, value]) => value === true).map(([key]) => key);
    const strengths = source.strengths && typeof source.strengths === 'object'
      ? Object.fromEntries(Object.entries(source.strengths).map(([key, value]) => [key, asNumber(value) ?? 1]))
      : {};
    return {version: 1, active: Array.from(new Set(active)).sort(), strengths};
  }

  function normalizeIdentity(raw, isRunning, mode) {
    const scene = raw?.scene_state && typeof raw.scene_state === 'object'
      ? raw.scene_state
      : raw?.scene?.schema === 'ledgrid.scene-state' ? raw.scene : null;
    if (mode === 'scene' || (scene && isRunning)) {
      const background = scene?.background || {};
      const preset = background.preset_id || background.preset?.preset_id || null;
      return {
        kind: 'scene',
        label: raw.scene_preset?.name || raw.scene_name || 'Scene',
        provider: background.provider || null,
        pluginId: background.plugin_id || null,
        presetId: preset,
        role: 'background',
        sceneRevision: asNumber(scene?.revision),
        scene
      };
    }
    const pluginId = nested(raw, [
      'current.plugin_id', 'current_animation', 'animation', 'current_animation_name'
    ]);
    const presetId = nested(raw, [
      'current.preset_id', 'current_preset.preset_id', 'current_preset.id', 'preset.preset_id',
      'preset_id', 'selected_preset.preset_id'
    ]);
    const provider = nested(raw, [
      'current.provider', 'current_provider', 'provider', 'current_preset.provider'
    ]) || (pluginId ? 'python' : null);
    const catalogLook = pluginId
      ? state.looks.find((look) => look.pluginId === String(pluginId)
        && (!presetId || look.presetId === String(presetId))
        && (!provider || look.provider === provider))
      : null;
    return {
      kind: 'look',
      label: catalogLook?.name || displayName(presetId || pluginId || (isRunning ? 'Unknown output' : 'Stopped')),
      provider: provider ? String(provider) : null,
      pluginId: pluginId ? String(pluginId) : null,
      presetId: presetId ? String(presetId) : null,
      role: catalogLook?.role || 'background',
      sceneRevision: null,
      scene: null
    };
  }

  function normalizeStatus(raw) {
    if (!raw || typeof raw !== 'object') throw new Error('Status response is not an object.');
    const isRunning = raw.is_running === true;
    const mode = String(raw.mode || (isRunning ? 'animation' : 'stopped'));
    const observedAt = sourceTime(nested(raw, [
      'updated_at', 'written_at', 'status.updated_at', 'status.written_at',
      'controller.updated_at', 'source_observed_at'
    ]));
    const identity = normalizeIdentity(raw, isRunning, mode);
    const brightness = asNumber(nested(raw, ['output_brightness', 'brightness', 'config.brightness']));
    const targetFps = asNumber(nested(raw, ['target_fps', 'config.target_fps']));
    const explicitOperatorSpeed = asNumber(nested(raw, [
      'operator_speed', 'animation_speed_multiplier', 'speed_multiplier'
    ]));
    const operatorSpeedScale = asNumber(raw.animation_speed_scale);
    const operatorSpeed = explicitOperatorSpeed ?? (operatorSpeedScale == null ? null : operatorSpeedScale / OPERATOR_SPEED_BASELINE);
    const vibeId = normalizeVibe(raw);
    const plantModifiers = normalizePlant(raw);
    const consequence = {
      power: typeof raw.power === 'boolean' ? raw.power : 'unknown',
      isRunning,
      mode,
      identity: {
        kind: identity.kind,
        provider: identity.provider,
        pluginId: identity.pluginId,
        presetId: identity.presetId,
        sceneRevision: identity.sceneRevision,
        scene: identity.scene
      },
      brightness,
      vibeId,
      plantModifiers,
      targetFps,
      operatorSpeed
    };
    return {
      raw,
      source: String(raw.source || raw.status_source || 'controller status'),
      sourceObservedAt: observedAt,
      receivedAt: Date.now(),
      lastCommandId: nested(raw, ['last_command_id', 'command_id']),
      isRunning,
      mode,
      power: typeof raw.power === 'boolean' ? raw.power : null,
      identity,
      scene: identity.scene,
      brightness,
      vibeId,
      plantModifiers,
      targetFps,
      operatorSpeed,
      operatorSpeedScale,
      fingerprint: stableString(consequence)
    };
  }

  function normalizeAction(source, component) {
    const action = source?.action || source?.execution || component?.action || component?.execution || {};
    const retiredDirectClaim = action.take_look_enabled === true || action.allowed === true;
    const code = retiredDirectClaim ? 'guarded_activation_required' : action.code || null;
    const composerEligible = action.composer_check_eligible === true
      || retiredDirectClaim || code === 'guarded_activation_required';
    const reason = composerEligible
      ? 'Physical activation requires Composer Check and guarded activation.'
      : action.reason || action.diagnostic || component?.diagnostic
        || 'Physical activation requires Composer Check; this catalog record exposes no direct live route.';
    return {allowed: false, composerEligible, code, reason: String(reason)};
  }

  function normalizePreview(source, component) {
    const preview = source?.preview || source?.preview_contract || component?.preview || component?.preview_contract || {};
    const posterUrl = preview.poster_url || preview.posterUrl || preview.static_url || null;
    const loopUrl = preview.loop_url || preview.loopUrl || preview.animated_url || null;
    return {
      posterUrl,
      loopUrl,
      available: Boolean(posterUrl || loopUrl),
      framebufferReadback: preview.framebuffer_readback === true,
      label: preview.label || preview.preview_label || null
    };
  }

  function normalizeCatalog(payload) {
    const catalog = payload?.catalog || {};
    const rawComponents = Array.isArray(catalog.components) ? catalog.components : [];
    state.components = rawComponents.map((component) => {
      const provider = String(component.provider || 'unknown');
      const pluginId = String(component.plugin_id || component.id || 'unknown');
      return {
        raw: component,
        key: String(component.key || `${provider}:${pluginId}`),
        provider,
        pluginId,
        name: String(component.display_name || component.name || displayName(pluginId)),
        description: String(component.description || ''),
        role: String(component.role || 'background'),
        category: String(component.category || ''),
        tags: Array.isArray(component.tags) ? component.tags.map(String) : [],
        providerCollision: component.provider_collision === true,
        action: normalizeAction(component),
        preview: normalizePreview(component),
        sceneCompatibility: component.scene_compatibility || component.compatibility || {}
      };
    });
    state.componentsByKey = new Map(state.components.map((item) => [item.key, item]));

    const rawPresets = Array.isArray(catalog.presets) ? catalog.presets : [];
    state.looks = rawPresets.map((preset) => {
      const provider = String(preset.provider || 'unknown');
      const pluginId = String(preset.plugin_id || preset.component_id || 'unknown');
      const componentKey = String(preset.component_key || `${provider}:${pluginId}`);
      const component = state.componentsByKey.get(componentKey);
      const presetId = String(preset.preset_id || preset.id || 'default');
      const action = normalizeAction(preset, component);
      const tags = [
        ...(Array.isArray(component?.tags) ? component.tags : []),
        ...(Array.isArray(preset.tags) ? preset.tags.map(String) : []),
        ...(Array.isArray(preset.intents) ? preset.intents.map(String) : [])
      ];
      return {
        raw: preset,
        key: String(preset.key || `${provider}:${pluginId}:${presetId}`),
        componentKey,
        provider,
        pluginId,
        presetId,
        name: String(preset.display_name || preset.name || displayName(presetId)),
        componentName: component?.name || displayName(pluginId),
        description: String(preset.description || component?.description || ''),
        role: String(preset.role || component?.role || 'background'),
        category: String(preset.category || component?.category || ''),
        tags,
        parameters: preset.params || preset.parameters || {},
        presetFingerprint: preset.fingerprint || preset.preset_fingerprint || null,
        action,
        preview: normalizePreview(preset, component),
        providerCollision: component?.providerCollision === true
      };
    });
    state.looksByKey = new Map(state.looks.map((item) => [item.key, item]));
  }

  function liveIdentityLine(snapshot) {
    if (!snapshot) return 'Waiting for first server observation';
    const identity = snapshot.identity;
    if (!snapshot.isRunning) {
      if (!identity.pluginId) return 'No last output identity was reported';
      return `${identity.label} · ${providerLabel(identity.provider)} · ${displayName(identity.role)}`;
    }
    if (identity.kind === 'scene') {
      const background = identity.pluginId ? displayName(identity.pluginId) : 'unknown background';
      return `${identity.label} · ${background} · ${providerLabel(identity.provider)}${identity.sceneRevision == null ? '' : ` · revision ${identity.sceneRevision}`}`;
    }
    return `${identity.label} · ${providerLabel(identity.provider)} · ${displayName(identity.role)}${identity.presetId ? ` · preset ${identity.presetId}` : ''}`;
  }

  function updateLiveSurface() {
    const snapshot = state.live;
    const stateNode = $('#liveState');
    const stop = $('#stopOutput');
    if (!snapshot) {
      text(stateNode, state.lastStatusError ? 'State unknown' : 'Connecting');
      stateNode.dataset.state = 'unknown';
      text('#liveIdentityLabel', 'Observed output');
      text('#liveIdentity', 'Waiting for the first server observation…');
      text('#liveQualifier', state.lastStatusError || 'Live actions are unavailable while state is unknown.');
      text('#liveBrightness', 'Unknown');
      text('#liveVibe', 'Unknown');
      text('#liveAge', 'Waiting');
      stop.disabled = true;
      text('#connectionState', state.lastStatusError ? 'Controller status unavailable' : 'Connecting to controller');
      renderNow();
      renderHealth();
      updateLiveActionAvailability();
      return;
    }
    const age = snapshot.sourceObservedAt == null ? null : Date.now() - snapshot.sourceObservedAt;
    const stale = age == null || age > 10000 || Boolean(state.lastStatusError);
    let headline = snapshot.isRunning ? 'Running' : 'Stopped';
    let headlineState = snapshot.isRunning ? 'running' : 'stopped';
    if (stale) {
      headline = 'State unknown';
      headlineState = 'stale';
    }
    text(stateNode, headline);
    stateNode.dataset.state = headlineState;
    text('#liveIdentityLabel', stale
      ? 'Last observed output'
      : snapshot.isRunning ? 'Live now' : 'Last output');
    text('#liveIdentity', liveIdentityLine(snapshot));
    const relationship = snapshot.identity.kind === 'scene'
      ? `Scene revision ${snapshot.identity.sceneRevision ?? 'unknown'} · saved/drift relation not reported`
      : 'Observed from controller; drafts do not replace this state';
    text('#liveQualifier', state.lastStatusError
      ? `Last observation retained; current status request failed: ${state.lastStatusError}`
      : stale
        ? `Last server observation was ${formatAge(age)}; its output identity may no longer be current.`
        : relationship);
    text('#liveBrightness', snapshot.brightness == null ? 'Unknown' : `${Math.round(snapshot.brightness / 255 * 100)}% · ${snapshot.brightness}/255`);
    text('#liveVibe', snapshot.vibeId ? displayName(snapshot.vibeId) : 'Unknown');
    text('#liveAge', formatAge(age));
    stop.disabled = Boolean(state.lastStatusError);
    text('#connectionState', state.lastStatusError
      ? 'Controller status unavailable'
      : stale ? 'Controller evidence is stale' : `Connected · ${state.localMode ? 'local mode' : 'wall controller'}`);
    updateRoomObserved(snapshot);
    updateSceneDrift(snapshot);
    renderNow();
    renderHealth();
    updateLiveActionAvailability();
  }

  async function fetchFreshStatus({render = true} = {}) {
    try {
      const raw = await requestJSON('/api/status');
      const snapshot = normalizeStatus(raw);
      state.live = snapshot;
      state.lastStatusError = null;
      if (render) updateLiveSurface();
      return snapshot;
    } catch (error) {
      state.lastStatusError = errorMessage(error);
      if (render) updateLiveSurface();
      throw error;
    }
  }

  function liveClaimIsKnown() {
    if (!state.live || state.lastStatusError) return false;
    if (state.live.sourceObservedAt == null) return false;
    return Date.now() - state.live.sourceObservedAt <= 10000;
  }

  function renderNow() {
    const snapshot = state.live;
    if (!snapshot) {
      text('#nowStatus', state.lastStatusError
        ? `Wall state is unknown: ${state.lastStatusError}`
        : 'Waiting for controller status. Live actions stay disabled.');
      setDefinition('#nowFacts', [
        ['Source', 'Not observed'], ['Mode', 'Unknown'], ['Provider', 'Unknown'], ['Saved relation', 'Unknown']
      ]);
      return;
    }
    const age = snapshot.sourceObservedAt == null ? null : Date.now() - snapshot.sourceObservedAt;
    text('#nowStatus', snapshot.isRunning
      ? `${liveIdentityLine(snapshot)} is the last server-observed physical output.`
      : `Output is stopped. ${liveIdentityLine(snapshot)} is shown only as the last reported output.`);
    setDefinition('#nowFacts', [
      ['Source', `${snapshot.source} · ${formatAge(age)}`],
      ['Mode', displayName(snapshot.mode)],
      ['Provider', snapshot.identity.provider ? providerLabel(snapshot.identity.provider) : 'Not reported'],
      ['Saved relation', snapshot.identity.kind === 'scene' ? 'Revision observed; saved/drift relation unknown' : 'Not reported for this look']
    ]);
  }

  function setDefinition(selector, rows) {
    const root = $(selector);
    if (!root) return;
    root.replaceChildren(...rows.map(([term, definition]) => element('div', {}, [
      element('dt', {text: term}), element('dd', {text: definition})
    ])));
  }

  function setWorkspace(name, {focus = false} = {}) {
    if (!WORKSPACES.includes(name)) return;
    state.workspace = name;
    $$('.sn-workspace').forEach((workspace) => {
      const active = workspace.dataset.workspace === name;
      workspace.hidden = !active;
      workspace.inert = !active;
      if (active) workspace.removeAttribute('inert');
      else workspace.setAttribute('inert', '');
    });
    $$('[data-nav]').forEach((button) => {
      if (button.dataset.nav === name) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    });
    if (name === 'looks') renderLooks();
    if (name === 'scene') renderScene();
    if (name === 'room') renderRoom();
    if (name === 'health') renderHealth();
    if (focus) {
      const heading = $(`#workspace-${name} h1`);
      heading?.setAttribute('tabindex', '-1');
      heading?.focus({preventScroll: false});
    }
    const route = name === 'now' ? '/studio-next' : `/studio-next#${name}`;
    history.replaceState(null, '', route);
  }

  function openDialog(dialog, opener = document.activeElement) {
    if (!(dialog instanceof HTMLDialogElement)) return;
    state.lastDialogOpener = opener instanceof HTMLElement ? opener : null;
    dialog.showModal();
    window.requestAnimationFrame(() => $('h2[tabindex="-1"]', dialog)?.focus());
  }

  function closeDialog(dialog) {
    if (!(dialog instanceof HTMLDialogElement) || !dialog.open) return;
    dialog.close();
    const opener = state.lastDialogOpener;
    state.lastDialogOpener = null;
    if (opener?.isConnected) opener.focus();
  }

  function setReviewSending(sending) {
    const dialog = $('#reviewDialog');
    dialog.dataset.sending = sending ? 'true' : 'false';
    $$('[data-close-dialog]', dialog).forEach((button) => { button.disabled = sending; });
  }

  function addReceipt(receipt) {
    const item = {
      id: receipt.id || `attempt-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`,
      kind: receipt.kind || 'command',
      label: receipt.label || 'Command request',
      status: receipt.status || 'sending',
      requestedAt: receipt.requestedAt || Date.now(),
      acceptedAt: receipt.acceptedAt || null,
      commandId: receipt.commandId || null,
      requestId: receipt.requestId || null,
      detail: receipt.detail || '',
      operations: receipt.operations || null,
      canRetryRoom: receipt.canRetryRoom === true,
      canRestoreRoom: receipt.canRestoreRoom === true
    };
    state.receipts.unshift(item);
    renderReceipts();
    return item;
  }

  function updateReceipt(id, patch) {
    const receipt = state.receipts.find((item) => item.id === id);
    if (!receipt) return null;
    Object.assign(receipt, patch);
    renderReceipts();
    return receipt;
  }

  function receiptStatusLabel(status) {
    const labels = {
      sending: 'Sending', accepted: 'Accepted; awaiting observation', observed: 'Observed',
      rejected: 'Rejected', failed: 'Failed', conflict: 'Observed conflict', timeout: 'Accepted; outcome not observed',
      saved: 'Saved; wall unchanged'
    };
    return labels[status] || displayName(status);
  }

  function renderReceipts() {
    text('#receiptCount', state.receipts.length);
    text('#mobileReceiptCount', state.receipts.length);
    const list = $('#receiptList');
    const recent = $('#recentReceipts');
    if (!list || !recent) return;
    if (!state.receipts.length) {
      list.replaceChildren(element('li', {text: 'No command attempts yet.'}));
      recent.replaceChildren(element('li', {text: 'No commands requested from Studio Next in this session.'}));
      return;
    }
    list.replaceChildren(...state.receipts.map((receipt) => {
      const children = [
        element('strong', {text: receipt.label}),
        element('span', {text: receiptStatusLabel(receipt.status)}),
        element('time', {dateTime: new Date(receipt.requestedAt).toISOString(), text: `Requested ${formatClock(receipt.requestedAt)}`})
      ];
      if (receipt.commandId) children.push(element('span', {text: `Command ID: ${receipt.commandId}`}));
      if (receipt.requestId) children.push(element('span', {text: `Request ID: ${receipt.requestId}`}));
      if (receipt.detail) children.push(element('span', {text: receipt.detail}));
      if (Array.isArray(receipt.operations)) {
        const operations = element('ol', {class: 'sn-receipt-operations'});
        receipt.operations.forEach((operation) => operations.append(element('li', {
          text: `${operation.label}: ${operation.status}${operation.detail ? ` — ${operation.detail}` : ''}`
        })));
        children.push(operations);
      }
      if (receipt.canRetryRoom || receipt.canRestoreRoom) {
        const actions = element('div', {class: 'sn-action-row'});
        if (receipt.canRetryRoom) actions.append(element('button', {
          class: 'sn-button sn-button--quiet', type: 'button', text: 'Retry remaining', dataset: {receiptAction: 'retry-room'}
        }));
        if (receipt.canRestoreRoom) actions.append(element('button', {
          class: 'sn-button sn-button--quiet', type: 'button', text: 'Restore observed starting values', dataset: {receiptAction: 'restore-room'}
        }));
        children.push(actions);
      }
      return element('li', {class: 'sn-receipt', dataset: {status: receipt.status}}, children);
    }));
    recent.replaceChildren(...state.receipts.slice(0, 3).map((receipt) => element('li', {
      text: `${receipt.label}: ${receiptStatusLabel(receipt.status)}`
    })));
  }

  function openReceipts() {
    const drawer = $('#receiptDrawer');
    drawer.hidden = false;
    $('#receiptToggle')?.setAttribute('aria-expanded', 'true');
    $('#mobileReceiptToggle')?.setAttribute('aria-expanded', 'true');
    $('#closeReceipts')?.focus();
  }

  function closeReceipts() {
    const drawer = $('#receiptDrawer');
    drawer.hidden = true;
    $('#receiptToggle')?.setAttribute('aria-expanded', 'false');
    $('#mobileReceiptToggle')?.setAttribute('aria-expanded', 'false');
    const returnTarget = window.matchMedia('(max-width: 820px)').matches
      ? $('#mobileReceiptToggle') : $('#receiptToggle');
    returnTarget?.focus();
  }

  async function observeAfter(preflight, matcher, timeout = OBSERVATION_TIMEOUT_MS) {
    const started = Date.now();
    const delays = [250, 500, 1000];
    let attempt = 0;
    while (Date.now() - started < timeout) {
      const delay = delays[attempt] ?? 2000;
      await new Promise((resolve) => window.setTimeout(resolve, delay));
      attempt += 1;
      try {
        const snapshot = await fetchFreshStatus();
        const newer = preflight.sourceObservedAt != null
          && snapshot.sourceObservedAt != null
          && snapshot.sourceObservedAt > preflight.sourceObservedAt;
        if (!newer) continue;
        const result = matcher(snapshot);
        if (result === true) return {status: 'observed', snapshot};
        if (result === 'conflict') return {status: 'conflict', snapshot};
      } catch (_error) {
        // Continue polling while the outcome remains unknown.
      }
    }
    return {status: 'timeout', snapshot: state.live};
  }

  async function stopOutput() {
    const button = $('#stopOutput');
    if (!state.live) return;
    button.disabled = true;
    const preflight = state.live;
    const receipt = addReceipt({kind: 'stop', label: 'Stop output', status: 'sending'});
    announce('Sending Stop output request.');
    try {
      const payload = await requestJSON('/api/stop', {method: 'POST'});
      updateReceipt(receipt.id, {
        status: 'accepted', acceptedAt: Date.now(), commandId: payload.command_id || null,
        detail: 'Stop request accepted. Waiting for a newer stopped observation.'
      });
      announce('Stop request accepted; awaiting observation.');
      const outcome = await observeAfter(preflight, (snapshot) => {
        if (!snapshot.isRunning && !['scene', 'animation', 'painter'].includes(snapshot.mode)) return true;
        if (snapshot.isRunning) return 'conflict';
        return false;
      });
      if (outcome.status === 'observed') {
        updateReceipt(receipt.id, {status: 'observed', detail: `Stopped observed at ${formatClock(outcome.snapshot.sourceObservedAt)}. Power state remains unknown.`});
        announce('Stopped output observed.');
      } else if (outcome.status === 'conflict') {
        updateReceipt(receipt.id, {status: 'conflict', detail: `A newer observation still reports ${liveIdentityLine(outcome.snapshot)}.`});
        announce('Stop request conflicts with newer running output.', true);
      } else {
        updateReceipt(receipt.id, {status: 'timeout', detail: 'Stop request was accepted but a stopped state was not observed within 15 seconds.'});
        announce('Stop not yet observed.', true);
      }
    } catch (error) {
      updateReceipt(receipt.id, {status: 'rejected', detail: errorMessage(error)});
      announce(`Stop output was rejected: ${errorMessage(error)}`, true);
    } finally {
      button.disabled = !state.live;
    }
  }

  function intentMatches(look, intent) {
    if (intent === 'all') return true;
    const explicit = look.tags.map((tag) => tag.toLowerCase());
    if (explicit.includes(intent)) return true;
    const haystack = [look.name, look.componentName, look.description, look.category, ...look.tags]
      .join(' ').toLowerCase();
    return (INTENT_WORDS[intent] || []).some((word) => haystack.includes(word));
  }

  function filteredLooks() {
    const search = state.filters.search.trim().toLowerCase();
    return state.looks.filter((look) => {
      const haystack = [
        look.name, look.componentName, look.description, look.provider, providerLabel(look.provider),
        look.role, look.key, look.pluginId, look.presetId, look.category, ...look.tags
      ].join(' ').toLowerCase();
      if (search && !haystack.includes(search)) return false;
      if (!intentMatches(look, state.filters.intent)) return false;
      if (state.filters.provider !== 'all' && look.provider !== state.filters.provider) return false;
      if (state.filters.role !== 'all' && look.role !== state.filters.role) return false;
      if (state.filters.readiness === 'ready' && !look.action.composerEligible) return false;
      if (state.filters.readiness === 'disabled' && look.action.composerEligible) return false;
      return true;
    });
  }

  function updateFilterInputs() {
    const search = $('#lookSearch');
    if (search && search.value !== state.filters.search) search.value = state.filters.search;
    const intent = $('#intentFilter');
    const provider = $('#providerFilter');
    const role = $('#roleFilter');
    const readiness = $('#readinessFilter');
    if (intent) intent.value = state.filters.intent;
    if (provider) provider.value = state.filters.provider;
    if (role) role.value = state.filters.role;
    if (readiness) readiness.value = state.filters.readiness;
  }

  function clearCatalogFilters() {
    state.filters = {search: '', intent: 'all', provider: 'all', role: 'all', readiness: 'all'};
    updateFilterInputs();
    renderLooks();
    announce(`Showing all ${state.looks.length} looks.`);
  }

  function chooseIntent(intent) {
    state.filters.intent = intent;
    state.filters.search = '';
    state.filters.provider = 'all';
    state.filters.role = 'all';
    state.filters.readiness = 'all';
    updateFilterInputs();
    setWorkspace('looks');
    renderLooks();
    const count = filteredLooks().length;
    announce(`${displayName(intent)} filter applied. ${count} of ${state.looks.length} looks match.`);
    $('#lookSearch')?.focus();
  }

  function renderLooks() {
    updateFilterInputs();
    const looks = filteredLooks();
    text('#lookCount', `${looks.length} matching / ${state.looks.length} total looks`);
    const list = $('#lookResults');
    const empty = $('#lookEmpty');
    if (!list || !empty) return;
    if (!looks.some((look) => look.key === state.selectedLookKey)) {
      state.previewPlaying = false;
      state.selectedLookKey = looks.find((look) => look.action.composerEligible)?.key || looks[0]?.key || null;
    }
    empty.hidden = looks.length !== 0;
    list.hidden = looks.length === 0;
    list.replaceChildren(...looks.map((look) => {
      const button = element('button', {
        type: 'button',
        dataset: {lookKey: look.key},
        'aria-current': look.key === state.selectedLookKey ? 'true' : 'false',
        'aria-label': `${look.name}, ${look.componentName}, ${providerLabel(look.provider)}, ${displayName(look.role)}. Activation: ${look.action.reason}`
      }, [
        element('span', {class: 'sn-result-name', text: look.name}),
        element('span', {class: 'sn-result-component', text: look.componentName}),
        element('span', {class: 'sn-result-meta', text: `${providerLabel(look.provider)} · ${displayName(look.role)} · ${look.action.composerEligible ? 'Composer Check eligible' : 'Check unavailable'}`})
      ]);
      if (!look.action.composerEligible) button.append(element('span', {class: 'sn-result-reason', text: look.action.reason}));
      return element('li', {}, button);
    }));
    renderLookDetail();
    renderCompareTray();
  }

  function selectLook(key, {focus = false} = {}) {
    if (!state.looksByKey.has(key)) return;
    if (state.selectedLookKey !== key) state.previewPlaying = false;
    state.selectedLookKey = key;
    renderLooks();
    if (focus) $('#lookDetailHeading')?.focus({preventScroll: false});
  }

  function previewProvenance(look) {
    if (!look) return 'Isolated preview · never changes the physical wall';
    if (look.provider === 'receiver_native') {
      if (!look.preview.available) return 'Receiver preview unavailable · generated placeholder, not receiver framebuffer readback · wall unchanged';
      return 'Host simulation preview · not receiver framebuffer readback · wall unchanged';
    }
    if (!look.preview.available) return 'Preview asset unavailable · generated placeholder · wall unchanged';
    return look.preview.label || 'Isolated host preview · never changes the physical wall';
  }

  function previewSummary(look) {
    if (!look) return 'No look selected.';
    return `${look.name}, preset ${look.presetId}, from ${look.componentName}. ${providerLabel(look.provider)} ${displayName(look.role)}. Activation: ${look.action.reason}`;
  }

  function hashNumber(value) {
    let hash = 2166136261;
    for (const character of String(value)) {
      hash ^= character.charCodeAt(0);
      hash = Math.imul(hash, 16777619);
    }
    return hash >>> 0;
  }

  function hsl(hue, saturation = 62, lightness = 52) {
    return `hsl(${((hue % 360) + 360) % 360} ${saturation}% ${lightness}%)`;
  }

  function drawPreviewCanvas(canvas, look, tick = 0, {scene = false} = {}) {
    if (!(canvas instanceof HTMLCanvasElement)) return;
    const context = canvas.getContext('2d', {alpha: false});
    if (!context) return;
    const width = canvas.width;
    const height = canvas.height;
    const seed = hashNumber(look?.key || look?.pluginId || 'studio-next');
    const baseHue = seed % 360;
    context.fillStyle = '#030504';
    context.fillRect(0, 0, width, height);
    for (let y = 0; y < height; y += 1) {
      for (let x = 0; x < width; x += 1) {
        const wave = Math.sin((y + tick) * (0.07 + (seed % 11) / 180) + x * 0.31);
        const curl = Math.cos((x - width / 2) * 0.28 - (y + tick * 0.55) * 0.045);
        const noise = ((x * 29 + y * 17 + seed) % 31) / 31;
        const intensity = (wave + curl + noise) / 3;
        if (intensity < -0.28) continue;
        const hue = baseHue + wave * 54 + y * 0.34;
        const lightness = 28 + (intensity + 1) * 25;
        context.fillStyle = hsl(hue, scene ? 56 : 68, lightness);
        context.fillRect(x, y, 1, 1);
      }
    }
    if (scene && $('#sceneClockEnabled')?.checked) {
      const opacity = Number($('#sceneOpacity')?.value || 255) / 255;
      context.fillStyle = `rgb(240 249 236 / ${opacity})`;
      const yOffset = Math.max(2, Math.min(height - 14, 62 + Number($('#sceneLedTranslation')?.value || 0)));
      context.fillRect(4, yOffset, width - 8, 2);
      context.fillRect(5, yOffset + 5, 4, 7);
      context.fillRect(12, yOffset + 5, 3, 7);
      context.fillRect(19, yOffset + 5, 4, 7);
    }
  }

  function selectedLook() {
    return state.looksByKey.get(state.selectedLookKey) || null;
  }

  function renderLookDetail() {
    const look = selectedLook();
    const image = $('#lookPreviewImage');
    const canvas = $('#lookPreviewCanvas');
    text('#lookPreviewPlaque', previewProvenance(look));
    if (!look) {
      image.hidden = true;
      canvas.hidden = false;
      drawPreviewCanvas(canvas, null);
      text('#lookComponentName', 'Choose a look');
      text('#lookDetailHeading', 'A large, exact-ratio preview will appear here.');
      text('#lookDescription', 'The catalog has no matching selection. Clear filters to browse every look.');
      setDefinition('#lookMetadata', [['Provider', '—'], ['Role', '—'], ['Readiness', '—'], ['Identity', '—']]);
      $('#lookDisabledReason').hidden = true;
      text('#lookPreviewSummary', 'Preview summary unavailable until a look is selected.');
      ['#playPreview', '#pinCompare'].forEach((id) => { $(id).disabled = true; });
      return;
    }
    text('#lookComponentName', look.componentName);
    text('#lookDetailHeading', look.name);
    text('#lookDescription', look.description || 'No catalog description is available. Full identity remains visible below.');
    setDefinition('#lookMetadata', [
      ['Provider', providerLabel(look.provider)], ['Role', displayName(look.role)],
      ['Activation', 'Composer Check required'],
      ['Identity', look.key]
    ]);
    const reason = $('#lookDisabledReason');
    reason.hidden = look.action.composerEligible;
    text(reason, look.action.composerEligible ? '' : look.action.reason);
    text('#lookPreviewSummary', previewSummary(look));
    const source = state.previewPlaying && look.preview.loopUrl
      ? look.preview.loopUrl
      : look.preview.posterUrl || look.preview.loopUrl;
    if (source) {
      image.src = source;
      image.alt = `Isolated preview of ${look.name}; ${previewProvenance(look)}`;
      image.hidden = false;
      canvas.hidden = true;
      image.onerror = () => {
        image.hidden = true;
        canvas.hidden = false;
        drawPreviewCanvas(canvas, look, state.previewTick);
      };
    } else {
      image.hidden = true;
      canvas.hidden = false;
      drawPreviewCanvas(canvas, look, state.previewTick);
    }
    $('#playPreview').disabled = !look.preview.loopUrl;
    text('#playPreview', !look.preview.loopUrl
      ? 'Animated preview unavailable'
      : state.previewPlaying ? 'Pause isolated preview' : 'Play isolated preview');
    const pinned = state.compareKeys.includes(look.key);
    $('#pinCompare').disabled = !pinned && state.compareKeys.length >= 3;
    text('#pinCompare', pinned ? 'Remove from compare' : (state.compareKeys.length >= 3 ? 'Compare set is full' : 'Add to compare'));
  }

  function togglePreview() {
    const look = selectedLook();
    if (!look?.preview.loopUrl) return;
    state.previewPlaying = !state.previewPlaying;
    renderLookDetail();
    if (state.previewPlaying && REDUCED_MOTION.matches) {
      announce('Animated preview started only after explicit activation because reduced motion is preferred.');
      return;
    }
    announce(state.previewPlaying ? 'Animated isolated preview started.' : 'Isolated preview paused.');
  }

  function toggleCompare(key) {
    const position = state.compareKeys.indexOf(key);
    if (position >= 0) state.compareKeys.splice(position, 1);
    else if (state.compareKeys.length < 3) state.compareKeys.push(key);
    else {
      announce('Compare set is full. Remove one before adding another.', true);
      return;
    }
    renderLookDetail();
    renderCompareTray();
    announce(`${state.compareKeys.length} of 3 compare positions filled.`);
  }

  function renderCompareTray() {
    text('#compareCount', state.compareKeys.length);
    $('#openCompare').disabled = state.compareKeys.length === 0;
    const list = $('#compareTrayList');
    if (!list) return;
    list.replaceChildren(...state.compareKeys.map((key) => {
      const look = state.looksByKey.get(key);
      const remove = element('button', {type: 'button', text: '×', 'aria-label': `Remove ${look?.name || key} from compare`, dataset: {removeCompare: key}});
      return element('li', {}, [element('span', {text: look?.name || key}), remove]);
    }));
  }

  function openCompareDialog() {
    if (!state.compareKeys.length) return;
    const grid = $('#compareGrid');
    grid.replaceChildren(...state.compareKeys.map((key) => {
      const look = state.looksByKey.get(key);
      if (!look) return element('article', {class: 'sn-compare-card'}, 'Catalog item unavailable.');
      const source = look.preview.posterUrl || look.preview.loopUrl;
      const media = source
        ? element('img', {src: source, width: 33, height: 138, alt: ''})
        : element('canvas', {width: 33, height: 138});
      const frame = element('div', {class: 'sn-wall-frame', 'aria-hidden': 'true'}, media);
      if (media instanceof HTMLCanvasElement) drawPreviewCanvas(media, look);
      const button = element('button', {
        class: 'sn-button sn-button--quiet', type: 'button', text: `Choose ${look.name}`,
        dataset: {chooseCompare: look.key}
      });
      return element('article', {class: 'sn-compare-card'}, [
        frame,
        element('h3', {text: look.name}),
        element('p', {text: look.componentName}),
        element('p', {text: `${providerLabel(look.provider)} · ${displayName(look.role)} · preset ${look.presetId}`}),
        element('p', {text: previewProvenance(look)}),
        element('p', {text: look.action.composerEligible ? 'Activation: Composer Check eligible' : `Activation unavailable: ${look.action.reason}`}),
        button
      ]);
    }));
    openDialog($('#compareDialog'), $('#openCompare'));
  }

  function updateLiveActionAvailability() {
    renderLookDetail();
    const sceneReady = state.scene.validatedRevision === state.scene.draftRevision && state.scene.validatedScene;
    $('#refreshEvidence').disabled = !state.live;
  }

  function reviewCurrentText(snapshot) {
    const observed = snapshot.sourceObservedAt == null
      ? 'Source observation time unavailable'
      : `${formatClock(snapshot.sourceObservedAt)} · ${formatAge(Date.now() - snapshot.sourceObservedAt)}`;
    return [
      `Observed: ${observed}`,
      `Output: ${snapshot.isRunning ? 'Running' : 'Stopped'}`,
      `Power: ${snapshot.power == null ? 'Unknown' : snapshot.power ? 'On' : 'Off'}`,
      `Identity: ${liveIdentityLine(snapshot)}`,
      `Brightness: ${snapshot.brightness == null ? 'Unknown' : `${snapshot.brightness}/255`}`,
      `Vibe: ${snapshot.vibeId ? displayName(snapshot.vibeId) : 'Unknown'}`
    ].join('\n');
  }

  function eligibleSceneBackgrounds() {
    return state.components.filter((component) => {
      if (component.provider !== 'python' || component.role !== 'background' || component.providerCollision) return false;
      if (component.action?.composerEligible !== true) return false;
      const compatibility = component.sceneCompatibility || {};
      if (compatibility.selectable === false || compatibility.composable === false) return false;
      const readiness = [component.raw.status, component.raw.availability, component.raw.readiness]
        .filter(Boolean).map((value) => String(value).toLowerCase().replaceAll('-', '_'));
      return !readiness.some((value) => ['build_only', 'unavailable', 'quarantined', 'disabled', 'error'].includes(value));
    });
  }

  function readyClockOverlay() {
    const clock = state.components.find((component) => component.provider === 'python'
      && component.pluginId === 'clock_overlay' && component.role === 'overlay');
    if (!clock || clock.raw?.gallery !== 'show' || clock.raw?.is_test === true) return null;
    const readiness = [clock.raw?.status, clock.raw?.availability, clock.raw?.readiness]
      .filter(Boolean).map((value) => String(value).toLowerCase().replaceAll('-', '_'));
    if (readiness.some((value) => ['build_only', 'unavailable', 'quarantined', 'disabled', 'error'].includes(value))) return null;
    if (clock.raw?.compatibility?.implementation_loaded !== true) return null;
    if (clock.sceneCompatibility?.selectable === false || clock.sceneCompatibility?.composable === false) return null;
    return clock;
  }

  function initializeScene(sceneEnvelope) {
    const scene = sceneEnvelope?.scene && typeof sceneEnvelope.scene === 'object' ? sceneEnvelope.scene : null;
    const select = $('#sceneBackground');
    const backgrounds = eligibleSceneBackgrounds();
    select.replaceChildren(element('option', {value: '', text: backgrounds.length ? 'Choose a background' : 'No Host Python background is ready'}));
    backgrounds.forEach((component) => select.append(element('option', {
      value: component.key,
      text: `${component.name} · ${providerLabel(component.provider)}`
    })));
    const currentKey = scene?.background
      ? `${scene.background.provider}:${scene.background.plugin_id}`
      : null;
    if (currentKey && backgrounds.some((component) => component.key === currentKey)) select.value = currentKey;
    else if (backgrounds.length) select.value = backgrounds[0].key;

    const overlay = Array.isArray(scene?.overlays) ? scene.overlays.find((item) => item.slot_id === 'clock_overlay') : null;
    const clockReady = Boolean(readyClockOverlay());
    $('#sceneClockEnabled').disabled = !clockReady;
    $('#sceneClockEnabled').checked = clockReady && Boolean(overlay?.enabled);
    text('#clockAvailability', clockReady
      ? 'Ready Host Python clock overlay.'
      : 'Clock overlay unavailable · readiness gate is closed.');
    $('#sceneOpacity').value = String(overlay?.opacity ?? 255);
    $('#sceneStripTranslation').value = String(overlay?.placement?.strip_translation ?? 0);
    $('#sceneLedTranslation').value = String(overlay?.placement?.led_translation ?? 0);
    $('#sceneClipPolicy').value = 'clip_to_wall';
    $('#sceneStalePolicy').value = overlay?.stale_policy?.policy === 'clear_after_lease' ? 'clear_after_lease' : 'hold';
    $('#sceneLeaseMs').value = String(overlay?.stale_policy?.lease_ms ?? 1000);
    $('#clockControls').hidden = !$('#sceneClockEnabled').checked;
    $('#leaseField').hidden = $('#sceneStalePolicy').value !== 'clear_after_lease';
    text('#sceneOpacityOutput', `${Math.round(Number($('#sceneOpacity').value) / 255 * 100)}%`);
    state.scene.baseSceneRevision = asNumber(scene?.revision) ?? 0;
    state.scene.baseLiveFingerprint = state.live?.fingerprint || null;
    state.scene.draftRevision = 0;
    state.scene.validatedRevision = null;
    state.scene.validatedScene = null;
    state.scene.dirty = false;
    state.scene.drift = false;
    state.scene.lastError = null;
    renderScene();
  }

  function sceneDraftChanged() {
    state.scene.draftRevision += 1;
    state.scene.validatedRevision = null;
    state.scene.validatedScene = null;
    state.scene.dirty = true;
    state.scene.lastError = null;
    $('#clockControls').hidden = !$('#sceneClockEnabled').checked;
    $('#leaseField').hidden = $('#sceneStalePolicy').value !== 'clear_after_lease';
    text('#sceneOpacityOutput', `${Math.round(Number($('#sceneOpacity').value) / 255 * 100)}%`);
    renderScene();
  }

  function componentRef(component, preset = null) {
    const reference = {
      plugin_id: component.pluginId,
      provider: component.provider,
      parameter_overrides: {},
      resolved_parameters: {}
    };
    if (preset?.presetId && preset?.presetFingerprint && /^[a-f0-9]{64}$/.test(preset.presetFingerprint)) {
      reference.preset_id = preset.presetId;
      reference.preset_fingerprint = preset.presetFingerprint;
    }
    return reference;
  }

  function buildSceneDraft() {
    const background = state.componentsByKey.get($('#sceneBackground').value);
    if (!background) throw new Error('Choose a compatible Host Python background.');
    if (background.provider !== 'python' || background.role !== 'background') {
      throw new Error('Initial Studio Next scene execution requires a Host Python background.');
    }
    if (background.action?.composerEligible !== true) {
      throw new Error(`This background is unavailable: ${background.action?.reason || 'server readiness gate is closed'}`);
    }
    const backgroundRef = componentRef(background);
    const overlays = [];
    if ($('#sceneClockEnabled').checked) {
      const clock = readyClockOverlay();
      if (!clock) throw new Error('The fixed Host Python clock_overlay component is unavailable or not ready.');
      const policy = $('#sceneStalePolicy').value;
      if (!['hold', 'clear_after_lease'].includes(policy)) throw new Error('Unsupported stale policy.');
      const stalePolicy = {policy};
      if (policy === 'clear_after_lease') {
        const lease = Number($('#sceneLeaseMs').value);
        if (!Number.isInteger(lease) || lease < 1 || lease > 4294967295) {
          throw new Error('Lease milliseconds must be an integer from 1 to 4,294,967,295.');
        }
        stalePolicy.lease_ms = lease;
      }
      const stripTranslation = Number($('#sceneStripTranslation').value);
      const ledTranslation = Number($('#sceneLedTranslation').value);
      const opacity = Number($('#sceneOpacity').value);
      if (![stripTranslation, ledTranslation, opacity].every(Number.isInteger)) throw new Error('Scene placement and opacity must be integers.');
      overlays.push({
        slot_id: 'clock_overlay',
        component: componentRef(clock),
        enabled: true,
        opacity,
        placement: {
          strip_translation: stripTranslation,
          led_translation: ledTranslation,
          clip_policy: 'clip_to_wall'
        },
        stale_policy: stalePolicy
      });
    }
    return {
      schema: 'ledgrid.scene-state',
      schema_version: 1,
      revision: Math.max(0, state.scene.baseSceneRevision + state.scene.draftRevision),
      background: backgroundRef,
      overlays,
      known_python_fallback: {...backgroundRef}
    };
  }

  function drawScenePreview() {
    let background = null;
    try {
      text('#scenePreviewPlaque', 'Draft preview placeholder · wall unchanged');
      const component = state.componentsByKey.get($('#sceneBackground').value);
      background = component || {key: 'scene-unselected', pluginId: 'scene-unselected'};
      const canvas = $('#scenePreviewCanvas');
      canvas.width = 33;
      canvas.height = 138;
      drawPreviewCanvas(canvas, background, state.previewTick, {scene: true});
      const overlay = $('#sceneClockEnabled').checked
        ? `Clock overlay at ${$('#sceneOpacityOutput').textContent}, strip ${$('#sceneStripTranslation').value}, LED ${$('#sceneLedTranslation').value}, ${$('#sceneStalePolicy').value}.`
        : 'No clock overlay.';
      text('#scenePreviewSummary', `${background.name || displayName(background.pluginId)} Host Python background. ${overlay} Draft placeholder only; refresh for a backend-rendered isolated frame. The physical wall is unchanged.`);
    } catch (error) {
      text('#scenePreviewSummary', `Preview unavailable: ${errorMessage(error)}`);
    }
  }

  function drawFrameData(canvas, payload) {
    const colors = payload?.frame_data;
    if (!(canvas instanceof HTMLCanvasElement) || !Array.isArray(colors)) return false;
    const stripCount = Number(payload?.led_info?.strip_count) || 33;
    const ledsPerStrip = Number(payload?.led_info?.leds_per_strip) || 138;
    if (!Number.isInteger(stripCount) || !Number.isInteger(ledsPerStrip)
      || stripCount < 1 || ledsPerStrip < 1 || colors.length < stripCount * ledsPerStrip) return false;
    canvas.width = stripCount;
    canvas.height = ledsPerStrip;
    const context = canvas.getContext('2d', {alpha: false});
    if (!context) return false;
    context.fillStyle = '#000';
    context.fillRect(0, 0, stripCount, ledsPerStrip);
    for (let strip = 0; strip < stripCount; strip += 1) {
      for (let led = 0; led < ledsPerStrip; led += 1) {
        const color = colors[strip * ledsPerStrip + led];
        if (!Array.isArray(color) || color.length < 3) continue;
        const channels = color.slice(0, 3).map((channel) => Math.max(0, Math.min(255, Number(channel) || 0)));
        context.fillStyle = `rgb(${channels[0]} ${channels[1]} ${channels[2]})`;
        context.fillRect(strip, led, 1, 1);
      }
    }
    return true;
  }

  function sceneConsequence(scene) {
    if (!scene || typeof scene !== 'object') return null;
    return {
      schema: scene.schema,
      schema_version: scene.schema_version,
      revision: scene.revision,
      background: scene.background,
      overlays: scene.overlays,
      known_python_fallback: scene.known_python_fallback
    };
  }

  function renderScene() {
    const hasBackground = Boolean(state.componentsByKey.get($('#sceneBackground')?.value));
    const dirty = $('#sceneDirtyBadge');
    const validated = $('#sceneValidationBadge');
    const drift = $('#sceneDriftBadge');
    text(dirty, state.scene.dirty ? 'Dirty draft' : 'Clean');
    dirty.className = state.scene.dirty ? 'is-warning' : '';
    const valid = state.scene.validatedRevision === state.scene.draftRevision && state.scene.validatedScene;
    text(validated, valid ? `Validated revision ${state.scene.draftRevision}` : `Not validated · draft ${state.scene.draftRevision}`);
    validated.className = valid ? 'is-good' : 'is-warning';
    text(drift, state.scene.drift ? 'Wall changed elsewhere' : 'No drift observed');
    drift.className = state.scene.drift ? 'is-warning' : '';
    if (state.scene.lastError) {
      text('#sceneValidation', state.scene.lastError);
      $('#sceneValidation').className = 'sn-validation is-invalid';
    } else if (valid) {
      text('#sceneValidation', `Validated revision ${state.scene.draftRevision}. Physical activation requires Composer's server Check and controller preconditions.`);
      $('#sceneValidation').className = 'sn-validation is-valid';
    } else {
      text('#sceneValidation', `Validation has not run for draft revision ${state.scene.draftRevision}. Editing clears prior validation.`);
      $('#sceneValidation').className = 'sn-validation';
    }
    $('#validateScene').disabled = !hasBackground;
    $('#previewScene').disabled = !hasBackground;
    $('#saveScene').disabled = !valid;
    drawScenePreview();
  }

  function updateSceneDrift(snapshot) {
    if (!state.scene.baseLiveFingerprint) {
      state.scene.baseLiveFingerprint = snapshot.fingerprint;
      return;
    }
    if (state.scene.dirty && snapshot.fingerprint !== state.scene.baseLiveFingerprint) {
      state.scene.drift = true;
      renderScene();
    }
  }

  async function validateScene() {
    const button = $('#validateScene');
    button.disabled = true;
    text(button, 'Validating…');
    const revision = state.scene.draftRevision;
    try {
      const scene = buildSceneDraft();
      const payload = await requestJSON('/api/v1/scene/validate', {
        method: 'POST', body: JSON.stringify({scene})
      });
      if (revision !== state.scene.draftRevision) {
        state.scene.lastError = 'The draft changed while validation was running. Validate the current revision again.';
      } else {
        state.scene.validatedRevision = revision;
        state.scene.validatedScene = payload.scene || scene;
        state.scene.lastError = null;
        announce(`Scene draft revision ${revision} validated.`);
      }
    } catch (error) {
      state.scene.validatedRevision = null;
      state.scene.validatedScene = null;
      state.scene.lastError = `Validation failed: ${errorMessage(error)}`;
      announce(state.scene.lastError, true);
    } finally {
      text(button, 'Validate draft');
      renderScene();
    }
  }

  async function previewScene() {
    const button = $('#previewScene');
    const original = button.textContent;
    button.disabled = true;
    text(button, 'Preparing isolated preview…');
    try {
      const scene = buildSceneDraft();
      const payload = await requestJSON('/api/v1/scene/preview', {
        method: 'POST',
        body: JSON.stringify({scene, elapsed: state.previewTick / 10})
      });
      if (payload.live_state_mutated === true) throw new Error('Preview contract reported a live-state mutation.');
      if (!drawFrameData($('#scenePreviewCanvas'), payload)) throw new Error('Preview response did not contain a complete renderable frame.');
      text('#scenePreviewPlaque', 'Backend-rendered isolated scene preview · wall unchanged');
      const provenance = payload.preview_label || (payload.background_provider === 'receiver_native'
        ? 'Host simulation preview — not receiver framebuffer readback'
        : 'Isolated scene preview');
      text('#scenePreviewSummary', `Backend-rendered isolated frame. Provenance: ${provenance}. The physical wall is unchanged; live_state_mutated=${String(payload.live_state_mutated === true)}.`);
      announce('Isolated scene preview refreshed. Physical wall unchanged.');
    } catch (error) {
      text('#scenePreviewSummary', `Isolated preview failed: ${errorMessage(error)}. No live endpoint was called.`);
      announce(`Scene preview failed: ${errorMessage(error)}`, true);
    } finally {
      button.disabled = false;
      text(button, original);
    }
  }

  function openSaveSceneDialog() {
    if (state.scene.validatedRevision !== state.scene.draftRevision || !state.scene.validatedScene) return;
    $('#sceneSaveName').value = '';
    $('#sceneSaveDescription').value = '';
    openDialog($('#saveSceneDialog'), $('#saveScene'));
    window.requestAnimationFrame(() => $('#sceneSaveName')?.focus());
  }

  async function saveSceneLayout(event) {
    event.preventDefault();
    const button = $('#confirmSaveScene');
    const name = $('#sceneSaveName').value.trim();
    if (!name) {
      $('#sceneSaveName').focus();
      announce('A layout name is required.', true);
      return;
    }
    if (state.scene.validatedRevision !== state.scene.draftRevision || !state.scene.validatedScene) {
      announce('The current draft is not validated. Nothing was saved.', true);
      return;
    }
    button.disabled = true;
    text(button, 'Saving layout…');
    try {
      const payload = {
        name,
        description: $('#sceneSaveDescription').value.trim(),
        scene: state.scene.validatedScene
      };
      const response = await requestJSON('/api/v1/scene-presets', {method: 'POST', body: JSON.stringify(payload)});
      addReceipt({
        kind: 'scene-save', label: `Save layout ${name}`, status: 'saved',
        detail: `Layout ${response.preset?.preset_id || name} saved. Wall, Vibe, plant behavior, brightness, FPS, speed, and power were unchanged.`
      });
      state.scene.dirty = false;
      closeDialog($('#saveSceneDialog'));
      announce('Layout saved; wall unchanged.');
      renderScene();
    } catch (error) {
      announce(`Layout was not saved: ${errorMessage(error)}`, true);
      button.disabled = false;
      text(button, 'Save layout; wall unchanged');
    }
  }

  function cloneRoom(value) {
    return value == null ? null : JSON.parse(JSON.stringify(value));
  }

  function roomObservedFromSnapshot(snapshot) {
    const brightness = snapshot.brightness != null && snapshot.brightness >= 0 && snapshot.brightness <= 255
      ? snapshot.brightness : null;
    const fps = snapshot.targetFps != null && snapshot.targetFps >= 1 && snapshot.targetFps <= 200
      ? snapshot.targetFps : null;
    const speed = snapshot.operatorSpeed != null && snapshot.operatorSpeed > 0
      ? snapshot.operatorSpeed : null;
    return {
      vibe: snapshot.vibeId,
      plant: normalizePlant(snapshot.raw),
      brightness,
      fps,
      speed,
      speedScale: snapshot.operatorSpeedScale
    };
  }

  function updateRoomObserved(snapshot) {
    const observed = roomObservedFromSnapshot(snapshot);
    state.room.observed = observed;
    if (!state.room.touched || !state.room.draft) state.room.draft = cloneRoom(observed);
    renderRoom();
  }

  function vibeProfiles() {
    const provided = Array.isArray(state.bootstrap?.vibe_profiles) ? state.bootstrap.vibe_profiles : [];
    const normalized = provided.map((profile) => {
      const id = profile.id || profile.vibe_id || profile.state?.id;
      if (!id) return null;
      return {id: String(id).toLowerCase(), name: String(profile.name || displayName(id))};
    }).filter(Boolean);
    if (normalized.length) return normalized;
    return ['neutral', 'quiet', 'cozy', 'vivid', 'celebration'].map((id) => ({id, name: displayName(id)}));
  }

  function populateVibeSelect() {
    const select = $('#roomVibe');
    const currentValue = select.value;
    select.replaceChildren(...vibeProfiles().map((profile) => element('option', {value: profile.id, text: profile.name})));
    if (currentValue && Array.from(select.options).some((option) => option.value === currentValue)) select.value = currentValue;
  }

  function setPlantControls(plant) {
    const active = new Set(plant?.active || []);
    $$('input[name="lightModifier"]').forEach((checkbox) => { checkbox.checked = active.has(checkbox.value); });
    $('#plantField').value = Array.from(active).find((item) => FIELD_MODIFIERS.has(item)) || '';
    $('#plantSurface').value = Array.from(active).find((item) => SURFACE_MODIFIERS.has(item)) || '';
    const firstActive = Array.from(active)[0];
    const strength = firstActive ? asNumber(plant?.strengths?.[firstActive]) : null;
    $('#plantStrength').value = String(strength ?? 0.7);
  }

  function roomDraftFromControls() {
    const observed = state.room.observed || {};
    const light = $$('input[name="lightModifier"]:checked').map((checkbox) => checkbox.value);
    const field = $('#plantField').value;
    const surface = $('#plantSurface').value;
    const active = [...light, ...(field ? [field] : []), ...(surface ? [surface] : [])];
    const strength = Number($('#plantStrength').value);
    const strengths = Object.fromEntries(active.map((modifier) => [modifier, strength]));
    return {
      vibe: $('#roomVibe').disabled ? observed.vibe : ($('#roomVibe').value || null),
      plant: {version: 1, active: active.sort(), strengths},
      brightness: $('#roomBrightness').disabled ? observed.brightness : Number($('#roomBrightness').value),
      fps: $('#roomFps').disabled ? observed.fps : Number($('#roomFps').value),
      speed: $('#roomSpeed').disabled ? observed.speed : Number($('#roomSpeed').value),
      speedScale: null
    };
  }

  function roomChanged() {
    state.room.draft = roomDraftFromControls();
    state.room.touched = true;
    renderRoom({preserveControls: true});
  }

  function roomPlan() {
    const observed = state.room.observed;
    const draft = state.room.draft;
    if (!observed || !draft) return [];
    const plan = [];
    if (observed.vibe != null && draft.vibe != null && observed.vibe !== draft.vibe) {
      plan.push({key: 'vibe', label: 'Vibe', oldValue: observed.vibe, newValue: draft.vibe});
    }
    if (stableString(observed.plant) !== stableString(draft.plant)) {
      plan.push({key: 'plant', label: 'Plant behavior', oldValue: observed.plant, newValue: draft.plant});
    }
    if (observed.brightness != null && Number(observed.brightness) !== Number(draft.brightness)) {
      plan.push({key: 'brightness', label: 'Brightness', oldValue: Number(observed.brightness), newValue: Number(draft.brightness)});
    }
    if (observed.fps != null && Number(observed.fps) !== Number(draft.fps)) {
      plan.push({key: 'fps', label: 'Target FPS', oldValue: Number(observed.fps), newValue: Number(draft.fps)});
    }
    if (observed.speed != null && Number(observed.speed).toFixed(4) !== Number(draft.speed).toFixed(4)) {
      plan.push({key: 'speed', label: 'Operator speed', oldValue: Number(observed.speed), newValue: Number(draft.speed)});
    }
    return plan;
  }

  function roomValueLabel(key, value) {
    if (key === 'vibe') return displayName(value);
    if (key === 'plant') {
      const active = value?.active || [];
      return active.length ? active.map(displayName).join(', ') : 'No active modifiers';
    }
    if (key === 'brightness') return `${value}/255 · ${Math.round(value / 255 * 100)}%`;
    if (key === 'fps') return `${value} FPS`;
    if (key === 'speed') return `${Number(value).toFixed(2)}× operator multiplier`;
    return String(value);
  }

  function renderRoom({preserveControls = false} = {}) {
    const observed = state.room.observed;
    const draft = state.room.draft;
    populateVibeSelect();
    const controls = [
      '#roomVibe', '#roomBrightness', '#roomFps', '#roomSpeed', '#plantField', '#plantSurface', '#plantStrength'
    ];
    const hasObserved = Boolean(observed && draft);
    controls.forEach((selector) => { $(selector).disabled = !hasObserved; });
    $$('input[name="lightModifier"]').forEach((input) => { input.disabled = !hasObserved; });
    if (!hasObserved) {
      text('#observedVibe', 'Unknown');
      text('#observedBrightness', 'Unknown');
      text('#observedFps', 'Unknown');
      text('#observedSpeed', 'Unknown');
      text('#observedPlants', 'Unknown');
      text('#roomDraftState', 'Waiting for observed room state');
      $('#resetRoomDraft').disabled = true;
      $('#reviewRoom').disabled = true;
      return;
    }
    $('#roomVibe').disabled = observed.vibe == null;
    $('#roomBrightness').disabled = observed.brightness == null;
    $('#roomFps').disabled = observed.fps == null;
    $('#roomSpeed').disabled = observed.speed == null;
    if (!preserveControls) {
      if (draft.vibe && Array.from($('#roomVibe').options).some((option) => option.value === draft.vibe)) $('#roomVibe').value = draft.vibe;
      $('#roomBrightness').value = String(draft.brightness ?? 128);
      $('#roomFps').value = String(draft.fps ?? 30);
      $('#roomSpeed').value = String(draft.speed ?? 1);
      setPlantControls(draft.plant);
    }
    text('#roomBrightnessOutput', `${Math.round(Number($('#roomBrightness').value) / 255 * 100)}% · ${$('#roomBrightness').value}/255 draft`);
    text('#roomFpsOutput', `${$('#roomFps').value} draft`);
    text('#plantStrengthOutput', `${Math.round(Number($('#plantStrength').value) * 100)}%`);
    text('#observedVibe', observed.vibe ? displayName(observed.vibe) : 'Unknown');
    text('#observedBrightness', observed.brightness == null ? 'Unknown' : roomValueLabel('brightness', observed.brightness));
    text('#observedFps', observed.fps == null ? 'Unknown' : roomValueLabel('fps', observed.fps));
    text('#observedSpeed', observed.speed == null ? 'Unknown' : roomValueLabel('speed', observed.speed));
    text('#observedPlants', roomValueLabel('plant', observed.plant));
    const plan = roomPlan();
    text('#roomDraftState', plan.length ? `${plan.length} draft ${plan.length === 1 ? 'change' : 'changes'} · not applied` : 'No draft changes');
    $('#resetRoomDraft').disabled = !plan.length;
    $('#reviewRoom').disabled = !plan.length || !liveClaimIsKnown();
  }

  function resetRoomDraft() {
    state.room.draft = cloneRoom(state.room.observed);
    state.room.touched = false;
    renderRoom();
    announce('Room drafts reset to the latest observed values. No commands were sent.');
  }

  function restoreRoomStartingValues() {
    if (!state.room.restoreTarget) return;
    state.room.draft = cloneRoom(state.room.restoreTarget);
    state.room.touched = true;
    setWorkspace('room');
    renderRoom();
    announce('Starting values are now a draft. Review them before any best-effort restoration requests are sent.');
    openRoomReview();
  }

  async function openRoomReview() {
    let plan = roomPlan();
    if (!plan.length) return;
    const dialog = $('#reviewDialog');
    text('#reviewCurrent', 'Fetching a fresh server observation…');
    text('#reviewProposed', plan.map((item) => `${item.label}: ${roomValueLabel(item.key, item.oldValue)} → ${roomValueLabel(item.key, item.newValue)}`).join('\n'));
    text('#reviewUnchanged', 'Looks, current scene, saved scene layouts, and power will not be saved or replaced. Any room properties not listed above remain unchanged.');
    text('#reviewSafety', 'Operations run serially: Vibe → plant behavior → brightness → target FPS → operator speed. Each must be observed before the next is sent. This plan is not atomic.');
    $('#reviewWarning').hidden = true;
    const confirm = $('#confirmReview');
    confirm.disabled = true;
    text(confirm, `Apply ${plan.length} room ${plan.length === 1 ? 'change' : 'changes'} serially`);
    openDialog(dialog, $('#reviewRoom'));
    try {
      const snapshot = await fetchFreshStatus();
      if (!liveClaimIsKnown()) throw new Error('The wall observation is stale or lacks a source time.');
      plan = roomPlan();
      state.review = {
        kind: 'room', target: {plan, startingObserved: cloneRoom(state.room.observed)},
        openedFingerprint: snapshot.fingerprint, snapshot
      };
      text('#reviewCurrent', reviewCurrentText(snapshot));
      text('#reviewProposed', plan.map((item) => `${item.label}: ${roomValueLabel(item.key, item.oldValue)} → ${roomValueLabel(item.key, item.newValue)}`).join('\n'));
      confirm.disabled = plan.length === 0;
    } catch (error) {
      state.review = null;
      text('#reviewCurrent', `State unavailable: ${errorMessage(error)}`);
      text('#reviewWarning', 'Room application is blocked until a fresh server observation is available.');
      $('#reviewWarning').hidden = false;
    }
  }

  function roomOperationRequest(operation) {
    if (operation.key === 'vibe') return {
      url: '/api/v1/vibe', body: {vibe: operation.newValue},
      expected: (payload) => ({vibe: payload.requested_vibe?.id || payload.requested_vibe?.vibe_id || operation.newValue})
    };
    if (operation.key === 'plant') return {
      url: '/api/config/plant-modifiers', body: {plant_modifiers: operation.newValue},
      expected: () => ({plant: operation.newValue})
    };
    if (operation.key === 'brightness') return {
      url: '/api/config/brightness', body: {brightness: operation.newValue},
      expected: () => ({brightness: operation.newValue})
    };
    if (operation.key === 'fps') return {
      url: '/api/config/target-fps', body: {target_fps: operation.newValue},
      expected: () => ({fps: operation.newValue})
    };
    if (operation.key === 'speed') return {
      url: '/api/config/animation-speed', body: {multiplier: operation.newValue},
      expected: (payload) => ({speed: operation.newValue, speedScale: asNumber(payload.animation_speed_scale)})
    };
    throw new Error(`Unsupported room operation ${operation.key}.`);
  }

  function roomObservationMatches(operation, expected, snapshot) {
    const observed = roomObservedFromSnapshot(snapshot);
    if (operation.key === 'vibe') return observed.vibe === expected.vibe;
    if (operation.key === 'plant') return stableString(observed.plant) === stableString(expected.plant);
    if (operation.key === 'brightness') return Number(observed.brightness) === Number(expected.brightness);
    if (operation.key === 'fps') return Number(observed.fps) === Number(expected.fps);
    if (operation.key === 'speed') {
      if (expected.speedScale != null && snapshot.operatorSpeedScale != null) {
        return Math.abs(snapshot.operatorSpeedScale - expected.speedScale) < 0.0001;
      }
      return Math.abs(observed.speed - expected.speed) < 0.0001;
    }
    return false;
  }

  async function confirmRoomReview(review) {
    const confirm = $('#confirmReview');
    confirm.disabled = true;
    text(confirm, 'Checking current wall…');
    let preflight;
    try {
      preflight = await fetchFreshStatus();
      if (!liveClaimIsKnown()) throw new Error('The wall observation is stale or lacks a controller source time.');
      if (preflight.fingerprint !== review.openedFingerprint) {
        review.openedFingerprint = preflight.fingerprint;
        review.snapshot = preflight;
        review.target.plan = roomPlan();
        text('#reviewCurrent', reviewCurrentText(preflight));
        text('#reviewProposed', review.target.plan.map((item) => `${item.label}: ${roomValueLabel(item.key, item.oldValue)} → ${roomValueLabel(item.key, item.newValue)}`).join('\n'));
        text('#reviewWarning', 'Wall or global state changed since this review opened. No room operation was sent. Review the updated old → new plan, then confirm again.');
        $('#reviewWarning').hidden = false;
        text(confirm, 'Confirm updated serial room plan');
        confirm.disabled = review.target.plan.length === 0;
        announce('Room state changed since review opened. No command was sent.', true);
        return;
      }
    } catch (error) {
      text('#reviewWarning', `Fresh status check failed. No command was sent: ${errorMessage(error)}`);
      $('#reviewWarning').hidden = false;
      confirm.disabled = false;
      text(confirm, 'Retry serial room plan');
      return;
    }

    const plan = review.target.plan;
    state.room.restoreTarget = cloneRoom(review.target.startingObserved);
    const operationReceipts = plan.map((operation) => ({...operation, status: 'Not attempted', detail: ''}));
    const receipt = addReceipt({
      kind: 'room', label: `Room plan · ${plan.length} operations`, status: 'sending',
      operations: operationReceipts, canRestoreRoom: true
    });
    closeDialog($('#reviewDialog'));
    openReceipts();
    announce('Serial room plan started. This is not an atomic operation.');
    let before = preflight;
    let stopped = false;
    for (let index = 0; index < plan.length; index += 1) {
      const operation = plan[index];
      const row = operationReceipts[index];
      row.status = 'Sending';
      updateReceipt(receipt.id, {operations: operationReceipts});
      try {
        const spec = roomOperationRequest(operation);
        const payload = await requestJSON(spec.url, {method: 'POST', body: JSON.stringify(spec.body)});
        row.status = 'Accepted; awaiting observation';
        row.detail = payload.command_id ? `command ${payload.command_id}` : 'command correlation unavailable';
        updateReceipt(receipt.id, {status: 'accepted', operations: operationReceipts, commandId: payload.command_id || receipt.commandId});
        const expected = spec.expected(payload);
        const outcome = await observeAfter(before, (snapshot) => roomObservationMatches(operation, expected, snapshot));
        if (outcome.status === 'observed') {
          row.status = 'Observed';
          row.detail = `newer observation at ${formatClock(outcome.snapshot.sourceObservedAt)}`;
          before = outcome.snapshot;
          updateReceipt(receipt.id, {operations: operationReceipts});
          continue;
        }
        row.status = 'Accepted; awaiting observation';
        row.detail = 'observation timeout; remaining operations stopped';
        stopped = true;
        updateReceipt(receipt.id, {
          status: 'timeout', operations: operationReceipts, canRetryRoom: true,
          detail: `${operation.label} was accepted but not observed. Remaining operations were not attempted. This plan was not atomic.`
        });
        announce(`${operation.label} was accepted but not observed. Serial room plan stopped.`, true);
        break;
      } catch (error) {
        row.status = 'Failed';
        row.detail = errorMessage(error);
        stopped = true;
        updateReceipt(receipt.id, {
          status: 'failed', operations: operationReceipts, canRetryRoom: true,
          detail: `${operation.label} failed. Remaining operations were not attempted. Earlier observed operations were not rolled back.`
        });
        announce(`${operation.label} failed. Serial room plan stopped.`, true);
        break;
      }
    }
    if (!stopped) {
      updateReceipt(receipt.id, {
        status: 'observed', operations: operationReceipts,
        detail: 'Every requested property was observed in order. The calls were serial and were not atomic.',
        canRetryRoom: false, canRestoreRoom: true
      });
      state.room.touched = false;
      state.room.draft = cloneRoom(state.room.observed);
      announce('Every room property in the serial plan was observed.');
    }
    renderRoom();
  }

  function receiverEvidence(snapshot) {
    const raw = snapshot?.raw || {};
    const receiver = raw.scene?.receiver || raw.receiver_hybrid || raw.receiver || raw.driver_stats?.receiver_hybrid || {};
    const publisher = receiver.publisher || {};
    const readable = Array.isArray(receiver.readable_devices) ? receiver.readable_devices.map(Number) : [];
    const unverified = Array.isArray(receiver.unverified_devices) ? receiver.unverified_devices.map(Number) : [];
    const devices = Array.isArray(raw.driver_stats?.devices) ? raw.driver_stats.devices : [];
    const sourceSceneRevision = receiver.source_scene_revision ?? receiver.scene_revision ?? null;
    const currentSceneRevision = snapshot?.isRunning && snapshot.mode === 'scene'
      ? snapshot.scene?.revision ?? null : null;
    const sceneRevisionMatches = sourceSceneRevision != null && currentSceneRevision != null
      && Number(sourceSceneRevision) === Number(currentSceneRevision);
    return {
      raw: receiver,
      exists: Object.keys(receiver).length > 0 || devices.length > 0,
      operational: receiver.operational === true || receiver.healthy === true || raw.driver_stats?.aggregate?.errors === 0,
      healthy: receiver.healthy === true,
      degraded: receiver.degraded === true,
      telemetryComplete: receiver.telemetry_complete === true,
      releaseAcceptance: receiver.release_acceptance === true,
      transportPolicy: String(receiver.transport_policy || 'not reported'),
      readable,
      unverified,
      sourceSceneRevision,
      currentSceneRevision,
      sceneRevisionMatches,
      current: Boolean(snapshot?.isRunning && snapshot.mode === 'scene' && sceneRevisionMatches),
      contextDigest: receiver.context_digest || null,
      error: receiver.error || publisher.last_error || (asNumber(raw.driver_stats?.aggregate?.errors) > 0 ? `${raw.driver_stats.aggregate.errors} driver errors` : null),
      devices
    };
  }

  function healthConclusion(snapshot, evidence) {
    if (!snapshot || snapshot.sourceObservedAt == null) return 'Wall state is unknown; controller source time is unavailable.';
    const age = Date.now() - snapshot.sourceObservedAt;
    if (age > 10000) return 'Wall state is unknown; evidence is stale.';
    if (snapshot.power === false) return 'Wall is powered off. Receiver playback evidence below is historical.';
    if (!snapshot.isRunning) return 'Output is stopped. Receiver evidence may describe the last run, not an active presentation.';
    if (evidence.exists && !evidence.current) {
      const relation = evidence.sourceSceneRevision == null
        ? 'receiver source scene revision is not reported'
        : `receiver scene ${evidence.sourceSceneRevision} does not match current scene ${evidence.currentSceneRevision ?? 'unknown'}`;
      return `Playing; receiver evidence is historical or unrelated because ${relation}.`;
    }
    if (evidence.error || (evidence.exists && evidence.operational === false)) {
      return `Playback needs attention${evidence.error ? `: ${evidence.error}` : '.'}`;
    }
    const expectedDegraded = evidence.degraded
      || evidence.transportPolicy.toLowerCase().includes('degraded')
      || evidence.unverified.length > 0;
    if (evidence.operational && expectedDegraded && (!evidence.telemetryComplete || !evidence.releaseAcceptance)) {
      return `Playing; verification incomplete as expected. ${evidence.unverified.length ? `Sections ${evidence.unverified.map((index) => index + 1).join(', ')} require a visual check.` : 'Configured transport policy does not provide complete evidence.'}`;
    }
    if (evidence.current && evidence.operational && evidence.telemetryComplete && evidence.releaseAcceptance) {
      return 'Playback evidence agrees across all reported sections. Transport evidence does not prove visible LEDs, wiring, power delivery, foliage occlusion, or color accuracy.';
    }
    return 'Playback evidence is incomplete. A visual check and receiver details are required; Studio Next will not summarize this as Healthy.';
  }

  function sectionEvidence(evidence, index) {
    const device = evidence.devices[index] || {};
    const readable = evidence.readable.includes(index)
      || device.receiver_status_seen === true
      || (device.receiver_status_version != null && Number(device.receiver_status_version) > 0);
    const unverified = evidence.unverified.includes(index);
    const transportError = device.error || device.last_error || null;
    let stateLabel = 'Evidence unavailable';
    if (!evidence.current) stateLabel = 'Historical or unrelated evidence';
    else if (transportError) stateLabel = 'Transport needs attention';
    else if (unverified) stateLabel = 'Verification incomplete as expected';
    else if (readable && evidence.telemetryComplete) stateLabel = 'Receiver evidence reported';
    else if (readable) stateLabel = 'Readable; evidence incomplete';
    else if (evidence.operational && evidence.transportPolicy.includes('degraded')) stateLabel = 'One-way transport expected';
    return {
      stateLabel,
      transport: transportError ? `Error: ${transportError}` : evidence.operational ? 'Operational evidence reported' : 'Not established',
      playback: device.receiver_frames_displayed != null
        ? `${device.receiver_frames_displayed} receiver frames reported`
        : 'Frame agreement not reported',
      telemetry: unverified ? 'Incomplete by policy' : evidence.telemetryComplete ? 'Complete' : 'Incomplete / unknown',
      release: evidence.releaseAcceptance
        ? evidence.current ? 'Accepted for current scene revision' : 'Historical/unrelated acceptance only'
        : 'Not established',
      revision: device.receiver_active_scene_revision ?? evidence.sourceSceneRevision ?? 'Not reported'
    };
  }

  function renderHealth() {
    const snapshot = state.live;
    const evidence = receiverEvidence(snapshot);
    text('#healthHeadline', state.lastStatusError
      ? `Wall state is unknown; the latest status request failed (${state.lastStatusError}). Prior evidence below may be stale.`
      : healthConclusion(snapshot, evidence));
    text('#healthSource', snapshot ? snapshot.source : 'Not observed');
    text('#healthAge', snapshot?.sourceObservedAt == null ? 'Unknown' : formatAge(Date.now() - snapshot.sourceObservedAt));
    const root = $('#healthSections');
    if (root) {
      root.replaceChildren(...[0, 1, 2, 3].map((index) => {
        const section = sectionEvidence(evidence, index);
        return element('article', {class: 'sn-health-section'}, [
          element('h3', {text: `Section ${index + 1}`}),
          element('span', {class: 'sn-health-section__state', text: snapshot?.isRunning ? section.stateLabel : 'Historical evidence only'}),
          element('dl', {}, [
            element('dt', {text: 'Transport: '}), element('dd', {text: section.transport}),
            element('dt', {text: 'Playback: '}), element('dd', {text: section.playback}),
            element('dt', {text: 'Telemetry: '}), element('dd', {text: section.telemetry}),
            element('dt', {text: 'Release: '}), element('dd', {text: section.release}),
            element('dt', {text: 'Scene revision: '}), element('dd', {text: section.revision})
          ])
        ]);
      }));
    }
    setDefinition('#healthEvidence', [
      ['Output', !snapshot ? 'Unknown' : snapshot.isRunning ? `Running · ${liveIdentityLine(snapshot)}` : 'Stopped · receiver data is historical'],
      ['Transport', evidence.error ? `Needs attention: ${evidence.error}` : evidence.operational ? `${evidence.current ? 'Current-scene' : 'Historical/unrelated'} operational evidence reported` : 'Not established'],
      ['Playback agreement', !evidence.current
        ? `Not established for current scene · receiver ${evidence.sourceSceneRevision ?? 'revision unknown'}, current ${evidence.currentSceneRevision ?? 'revision unknown'}`
        : evidence.healthy ? 'Receiver reports agreement for the current scene revision'
          : evidence.degraded ? 'Current-scene degraded / partial agreement' : 'Not established'],
      ['Telemetry completeness', evidence.telemetryComplete ? 'Complete' : evidence.unverified.length ? `Incomplete by policy · sections ${evidence.unverified.map((index) => index + 1).join(', ')}` : 'Incomplete or not reported'],
      ['Release acceptance', evidence.releaseAcceptance
        ? evidence.current ? 'Accepted for the current scene revision' : 'Reported only for historical/unrelated evidence'
        : 'Not established'],
      ['Transport policy', evidence.transportPolicy],
      ['Receiver context', evidence.contextDigest
        ? 'Digest reported, but no independent expected digest is exposed for comparison'
        : 'Context digest not reported'],
      ['Visible-pixel verification', 'Visual check required · transport is not proof of visible output'],
      ['Camera-visible acceptance', 'TODO · calibrated camera evidence is not integrated']
    ]);
    $('#refreshEvidence').disabled = !snapshot;
  }

  async function refreshEvidence() {
    if (!state.live) return;
    const button = $('#refreshEvidence');
    const before = state.live;
    button.disabled = true;
    text(button, 'Requesting evidence…');
    const receipt = addReceipt({kind: 'evidence', label: 'Request fresh receiver evidence', status: 'sending'});
    try {
      const payload = await requestJSON('/api/v1/receivers/status/refresh', {method: 'POST', body: JSON.stringify({})});
      updateReceipt(receipt.id, {
        status: 'accepted', acceptedAt: Date.now(), commandId: payload.command_id || null,
        requestId: payload.request_id || null,
        detail: 'Refresh request accepted. No completion is claimed until a newer source observation arrives.'
      });
      text('#refreshStatus', `Accepted${payload.request_id ? ` · request ${payload.request_id}` : ''}. Waiting for a newer controller source observation.`);
      announce('Evidence refresh request accepted; awaiting a newer observation.');
      const outcome = await observeAfter(before, () => true);
      if (outcome.status === 'observed') {
        updateReceipt(receipt.id, {
          status: 'observed',
          detail: `Newer evidence observed at ${formatClock(outcome.snapshot.sourceObservedAt)}; request correlation unavailable.`
        });
        text('#refreshStatus', `Newer evidence observed at ${formatClock(outcome.snapshot.sourceObservedAt)}; request correlation unavailable. This is not a correlated “refresh complete” claim.`);
        announce('Newer receiver evidence observed; request correlation unavailable.');
      } else {
        updateReceipt(receipt.id, {status: 'timeout', detail: 'Refresh was accepted, but no newer source observation arrived within 15 seconds.'});
        text('#refreshStatus', 'Refresh accepted; newer source evidence was not observed within 15 seconds.');
        announce('Evidence refresh accepted; outcome not observed.', true);
      }
    } catch (error) {
      updateReceipt(receipt.id, {status: 'rejected', detail: errorMessage(error)});
      text('#refreshStatus', `Refresh request rejected: ${errorMessage(error)}`);
      announce(`Evidence refresh rejected: ${errorMessage(error)}`, true);
    } finally {
      button.disabled = !state.live;
      text(button, 'Request fresh evidence');
    }
  }

  async function confirmReview() {
    const review = state.review;
    if (!review) return;
    if (review.kind === 'room') await confirmRoomReview(review);
  }

  function bindEvents() {
    $$('[data-nav]').forEach((button) => button.addEventListener('click', () => setWorkspace(button.dataset.nav, {focus: true})));
    $$('.sn-intents [data-intent]').forEach((button) => button.addEventListener('click', () => chooseIntent(button.dataset.intent)));
    $('#browseAll').addEventListener('click', () => { clearCatalogFilters(); setWorkspace('looks', {focus: true}); });
    $('#roomShortcut').addEventListener('click', () => setWorkspace('room', {focus: true}));
    $('#stopOutput').addEventListener('click', stopOutput);

    $('#lookSearch').addEventListener('input', (event) => { state.filters.search = event.target.value; renderLooks(); });
    $('#clearSearch').addEventListener('click', () => { state.filters.search = ''; updateFilterInputs(); renderLooks(); $('#lookSearch').focus(); });
    $('#intentFilter').addEventListener('change', (event) => { state.filters.intent = event.target.value; renderLooks(); });
    $('#providerFilter').addEventListener('change', (event) => { state.filters.provider = event.target.value; renderLooks(); });
    $('#roleFilter').addEventListener('change', (event) => { state.filters.role = event.target.value; renderLooks(); });
    $('#readinessFilter').addEventListener('change', (event) => { state.filters.readiness = event.target.value; renderLooks(); });
    $('#clearFilters').addEventListener('click', clearCatalogFilters);
    $$('[data-clear-catalog]').forEach((button) => button.addEventListener('click', clearCatalogFilters));
    $('#lookResults').addEventListener('click', (event) => {
      const button = event.target.closest('[data-look-key]');
      if (button) selectLook(button.dataset.lookKey, {focus: window.matchMedia('(max-width: 820px)').matches});
    });
    $('#playPreview').addEventListener('click', togglePreview);
    $('#pinCompare').addEventListener('click', () => { const look = selectedLook(); if (look) toggleCompare(look.key); });
    $('#openCompare').addEventListener('click', openCompareDialog);
    $('#compareTrayList').addEventListener('click', (event) => {
      const button = event.target.closest('[data-remove-compare]');
      if (button) toggleCompare(button.dataset.removeCompare);
    });
    $('#compareGrid').addEventListener('click', (event) => {
      const button = event.target.closest('[data-choose-compare]');
      if (!button) return;
      closeDialog($('#compareDialog'));
      selectLook(button.dataset.chooseCompare, {focus: true});
    });

    $('#sceneForm').addEventListener('input', sceneDraftChanged);
    $('#validateScene').addEventListener('click', validateScene);
    $('#previewScene').addEventListener('click', previewScene);
    $('#saveScene').addEventListener('click', openSaveSceneDialog);
    $('#saveSceneForm').addEventListener('submit', saveSceneLayout);

    $('#roomForm').addEventListener('input', roomChanged);
    $('#resetRoomDraft').addEventListener('click', resetRoomDraft);
    $('#reviewRoom').addEventListener('click', openRoomReview);
    $('#refreshEvidence').addEventListener('click', refreshEvidence);

    $('#confirmReview').addEventListener('click', confirmReview);
    $$('[data-close-dialog]').forEach((button) => button.addEventListener('click', () => closeDialog(button.closest('dialog'))));
    $$('dialog').forEach((dialog) => dialog.addEventListener('click', (event) => {
      if (event.target === dialog && dialog.dataset.sending !== 'true') closeDialog(dialog);
    }));
    $('#reviewDialog').addEventListener('cancel', (event) => {
      if (event.currentTarget.dataset.sending === 'true') event.preventDefault();
    });

    $('#receiptToggle').addEventListener('click', () => $('#receiptDrawer').hidden ? openReceipts() : closeReceipts());
    $('#mobileReceiptToggle').addEventListener('click', () => $('#receiptDrawer').hidden ? openReceipts() : closeReceipts());
    $('#closeReceipts').addEventListener('click', closeReceipts);
    $('#receiptList').addEventListener('click', (event) => {
      const action = event.target.closest('[data-receipt-action]')?.dataset.receiptAction;
      if (action === 'retry-room') { closeReceipts(); setWorkspace('room'); openRoomReview(); }
      if (action === 'restore-room') { closeReceipts(); restoreRoomStartingValues(); }
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !$$('dialog[open]').length && !$('#receiptDrawer').hidden) closeReceipts();
    });
  }

  async function bootstrap() {
    bindEvents();
    renderReceipts();
    renderLookDetail();
    renderHealth();
    updateLiveSurface();
    try {
      const payload = await requestJSON('/api/v1/studio-next/bootstrap');
      if (payload.schema !== 'ledgrid.studio-next-bootstrap' || Number(payload.schema_version) !== 1) {
        throw new Error('Unsupported Studio Next bootstrap schema.');
      }
      state.bootstrap = payload;
      state.localMode = payload.local_mode === true;
      normalizeCatalog(payload);
      if (payload.status && typeof payload.status === 'object') {
        state.live = normalizeStatus(payload.status);
        state.lastStatusError = null;
      }
      populateVibeSelect();
      updateLiveSurface();
      initializeScene(payload.scene);
      renderLooks();
      const totalComponents = payload.catalog?.totals?.components ?? state.components.length;
      const totalPresets = payload.catalog?.totals?.presets ?? state.looks.length;
      announce(`Studio Next catalog loaded: ${totalComponents} components and ${totalPresets} looks.`);
      try {
        await fetchFreshStatus();
      } catch (_error) {
        // The bootstrap observation remains visible but live actions fail closed on the status error.
      }
    } catch (error) {
      state.lastStatusError = `Studio Next bootstrap failed: ${errorMessage(error)}`;
      updateLiveSurface();
      text('#lookCount', 'Catalog unavailable');
      text('#lookDescription', state.lastStatusError);
      announce(state.lastStatusError, true);
    }
    const initialRoute = location.hash.replace('#', '');
    setWorkspace(WORKSPACES.includes(initialRoute) ? initialRoute : 'now');
    state.pollHandle = window.setInterval(() => {
      fetchFreshStatus().catch(() => {});
    }, POLL_MS);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bootstrap, {once: true});
  else bootstrap();
})();
