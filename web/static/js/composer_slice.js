(() => {
  'use strict';
  const root = document.querySelector('.composer');
  const api = root.dataset.apiRoot;
  const $ = (selector) => document.querySelector(selector);
  const DEFAULT_SCENE_PACE = .7;
  function newUuid() {
    const browserCrypto = typeof globalThis.crypto === 'object' ? globalThis.crypto : null;
    if (typeof browserCrypto?.randomUUID === 'function') return browserCrypto.randomUUID();
    if (typeof browserCrypto?.getRandomValues === 'function') {
      const bytes = browserCrypto.getRandomValues(new Uint8Array(16));
      bytes[6] = (bytes[6] & 0x0f) | 0x40;
      bytes[8] = (bytes[8] & 0x3f) | 0x80;
      const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
      return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    }
    return `composer-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  }
  // A page lifetime client id makes a reload a new mutation stream.  Reusing
  // an id while resetting its sequence would make the next authored edit stale.
  const clientId = newUuid();
  const state = { status: {connected: false, running: false, armed: false, current: null, desired: null, observed: null, revision: 0}, library: {items: [], favorites: []}, filter: 'look', query: '', selection: null,
    scene: null, history: [], redo: [], sequence: 0, intent: 0, submitting: false, previewGeneration: 0, refreshInFlight: false, dirty: false, components: new Map(), componentPresets: Object.create(null), authoredValidationError: null,
    gallery: {entries: [], digest: null, query: '', filter: 'all', favorites: new Set(), detail: null, detailToken: 0},
    publication: {queued: null, afterStop: null, inFlight: null, scheduled: false},
    wall: {
      bootstrap: null, observation: null, scene: null, activating: false, dirty: false,
      adoptedLook: null, adoptedVibeId: null, activationError: null, retryBlocked: false, requestId: null,
    },
    playlist: {id: null, entries: [], saved: [], requestId: null, runId: null,
      saving: false, switching: false, adding: false, baseline: null, actionError: null, retryStart: null, dismissedStatusError: null, startIntent: null, stopPromise: null, statusIntent: 0, statusPoll: 0}
  };
  window.ComposerEditorContext = Object.freeze({
    snapshot: () => structuredClone({scene: state.scene, selection: state.selection, dirty: state.dirty, intent: state.intent}),
    componentName: (ref) => componentDescriptor(ref)?.name || ref?.component_id,
    parameterLabel: (ref, name) => componentDescriptor(ref)?.parameter_schema?.[name]?.label || parameterLabel(name),
  });
  const identity = (value) => value ? `r${value.revision} · ${value.digest}` : 'None';
  const beginIntent = () => ++state.intent;
  const intentIsCurrent = (intent) => intent === state.intent;
  const sleep = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));
  async function requestJson(url, options = {}) {
    let response;
    try { response = await fetch(url, {cache: 'no-store', ...options}); }
    catch (_) { const error = new Error('Wall server unavailable.'); error.code = 'offline'; throw error; }
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(body.error || `Wall request failed (${response.status}).`);
      error.code = body.code; error.status = response.status; error.blockers = body.blockers;
      throw error;
    }
    return body;
  }
  const number = (id) => Number($(id).value);
  function syncSceneSpeed(value, animation = state.scene?.animation) {
    const pace = Number(value);
    const range = $('#sceneSpeed');
    const exact = $('#sceneSpeedExact');
    const suggested = componentDescriptor(animation)?.suggested_scene_pace ?? 2;
    range.max = Math.min(Number(exact.max), Math.max(pace, suggested));
    range.value = String(pace);
    exact.value = String(pace);
    exact.dataset.authored = String(pace);
    exact.removeAttribute('aria-invalid');
    $('#sceneSpeedValue').textContent = pace === 0 ? 'Frozen' : `${pace.toFixed(2)}×`;
  }
  function scenePace() {
    return Number($('#sceneSpeedExact').dataset.authored ?? DEFAULT_SCENE_PACE);
  }
  const DEFAULT_TARGET_FPS = 200;
  function boundedTargetFps(value) { return Math.max(1, Math.min(200, Math.round(Number(value) || DEFAULT_TARGET_FPS))); }
  function syncTargetFps(value) {
    const target = boundedTargetFps(value);
    $('#targetFps').value = String(target);
    $('#targetFpsValue').textContent = `${target} FPS`;
    return target;
  }
  function renderActualFps(observation = state.wall.observation) {
    const measured = Number(observation?.actual_fps);
    const output = $('#actualFps');
    const outputRunning = Boolean(observation?.is_running || state.status?.running);
    const text = Number.isFinite(measured) && measured > 0 ? `${measured.toFixed(1)} FPS` : (outputRunning ? 'Unavailable' : 'Not running');
    if (output.textContent !== text) output.textContent = text;
  }
  function syncFrameRateObservation(observation = state.wall.observation) {
    renderActualFps(observation);
    // Never let a polling observation replace a value the operator just
    // authored.  The queued entry carries that exact target to activation.
    if (state.wall.dirty || state.publication.queued || state.publication.afterStop || state.publication.inFlight) return;
    const observedTarget = Number(observation?.target_fps);
    if (Number.isFinite(observedTarget) && observedTarget > 0) authoredTargetFps = syncTargetFps(observedTarget);
  }
  const plantOptics = Object.freeze([
    {id: 'illuminate', label: 'Illuminate', enabled: '#plantIlluminateEnabled', strength: '#plantIlluminateStrength', value: '#plantIlluminateValue'},
    {id: 'shadow', label: 'Shadow', enabled: '#plantShadowEnabled', strength: '#plantShadowStrength', value: '#plantShadowValue'},
    {id: 'hue_shift', label: 'Hue shift', enabled: '#plantHueShiftEnabled', strength: '#plantHueShiftStrength', value: '#plantHueShiftValue'},
  ]);
  const plantOpticIds = new Set(plantOptics.map(({id}) => id));
  function sameLocalParameters(left, right) {
    if (left === right) return true;
    if (!left || !right || typeof left !== 'object' || typeof right !== 'object') return false;
    const leftKeys = Object.keys(left).sort(); const rightKeys = Object.keys(right).sort();
    return leftKeys.length === rightKeys.length && leftKeys.every((key, index) => key === rightKeys[index] && sameLocalParameters(left[key], right[key]));
  }
  const semanticControls = new Map();
  let controlEditSessions = new WeakMap();
  let historyGeneration = 0;
  let authoredTargetFps = null;
  const PALETTE_SWATCHES = Object.freeze({
    mist: ['#172034', '#7193c7', '#e2efff'], neutral: ['#151515', '#777', '#eee'],
    spectrum: ['#d6275c', '#36c5f0', '#f6d743'], ember: ['#2a0c07', '#e0522d', '#ffca70'],
    rainbow: ['#2a0048', '#ff285a', '#28ff8c'],
  });
  function isSemanticControl(control) { return control.matches('input:not([type="hidden"]):not([type="range"]), select, textarea') && !control.dataset.semanticInstrument; }
  function dispatchSemanticEdit(control, previous = null) { void edit({target: control}, previous); }
  function beginControlEdit(control) {
    if (!controlEditSessions.has(control)) controlEditSessions.set(control, {previous: historySnapshot(), remembered: false});
  }
  function finishControlEdit(control) { controlEditSessions.delete(control); }
  function finishControlEditAfterEvent(control) {
    const session = controlEditSessions.get(control);
    setTimeout(() => { if (controlEditSessions.get(control) === session) finishControlEdit(control); }, 0);
  }
  function finiteControlValue(control) { return String(control.value).trim() !== '' && Number.isFinite(Number(control.value)); }
  function normalControlValue(control, value) {
    let next = Number(value); const min = Number(control.min); const max = Number(control.max); const step = Number(control.step);
    if (!Number.isFinite(next)) return null;
    if (Number.isFinite(min)) next = Math.max(min, next);
    if (Number.isFinite(max)) next = Math.min(max, next);
    if (Number.isFinite(step) && step > 0) { const origin = Number.isFinite(min) ? min : 0; next = origin + Math.round((next - origin) / step) * step; }
    return control.step === '1' ? Math.round(next) : Number(next.toFixed(6));
  }
  function controlDefault(control) { return control.dataset.semanticDefault; }
  function controlWords(control) { return String(control.closest('[data-parameter]')?.dataset.parameter || control.id || '').replace(/([a-z0-9])([A-Z])/g, '$1 $2').split(/[^a-z0-9]+/i).filter(Boolean).map((word) => word.toLowerCase()); }
  function isSeedControl(control) { return controlWords(control).includes('seed'); }
  function controlLabelText(label) {
    return [...label.childNodes]
      .filter((node) => node.nodeType === Node.TEXT_NODE)
      .map((node) => node.textContent.trim())
      .filter(Boolean)
      .join(' ');
  }
  function addControlMeta(label, control) {
    const defaults = controlDefault(control); if (defaults == null) return;
    const reset = document.createElement('button'); reset.type = 'button'; reset.className = 'semantic-reset'; reset.textContent = 'Reset'; reset.setAttribute('aria-label', `Reset ${controlLabelText(label)} to default`);
    reset.addEventListener('click', () => {
      control.value = defaults; control.checked = defaults === 'true'; syncSemanticControls();
      dispatchSemanticEdit(control);
    });
    const meta = document.createElement('small'); meta.className = 'semantic-default'; meta.textContent = `Default ${defaults}`; reset.title = `Reset to ${defaults}`;
    label.append(meta, reset);
  }
  function syncNumericRange(record) {
    const {control, range} = record;
    range.min = control.dataset.uiMin || control.min;
    range.max = control.dataset.uiMax || control.max;
    const exactStep = control.step && control.step !== 'any' ? control.step : '.01';
    const coarseStep = Number(control.dataset.coarseStep);
    const span = Number(range.max) - Number(range.min);
    const steps = span / coarseStep;
    range.step = coarseStep > 0 && Math.abs(steps - Math.round(steps)) < 1e-6 ? String(coarseStep) : exactStep;
    const describe = (element) => element.setAttribute('aria-description', `Range ${element.min} to ${element.max}.`);
    describe(range);
    describe(control);
    if (finiteControlValue(control)) range.value = control.value;
  }
  function decorateNumeric(label, control) {
    const section = label.closest('section')?.getAttribute('aria-label') || label.closest('.inspector')?.querySelector('h2')?.textContent.trim();
    const setting = [section, controlLabelText(label)].filter(Boolean).join(': ');
    const range = document.createElement('input'); range.type = 'range'; range.className = 'semantic-range';
    range.setAttribute('aria-label', `${setting} adjustment`);
    control.setAttribute('aria-label', `${setting} exact value`);
    const wrap = document.createElement('span');
    wrap.className = 'semantic-number'; control.classList.add('semantic-exact');
    control.before(wrap); wrap.append(range, control);
    const record = {kind: 'number', control, range, lastValue: control.value};
    semanticControls.set(control, record);
    syncNumericRange(record);
    range.addEventListener('pointerdown', () => beginControlEdit(control));
    range.addEventListener('keydown', () => beginControlEdit(control));
    range.addEventListener('pointerup', () => finishControlEditAfterEvent(control));
    range.addEventListener('pointercancel', () => finishControlEditAfterEvent(control));
    range.addEventListener('keyup', () => finishControlEditAfterEvent(control));
    range.addEventListener('change', () => finishControlEditAfterEvent(control));
    range.addEventListener('input', () => {
      const value = normalControlValue(range, range.value); if (value == null) return; const changed = String(value) !== control.value; control.value = String(value); record.lastValue = control.value;
      if (changed) dispatchSemanticEdit(control);
    });
    wireExactNumber(record);
    if (isSeedControl(control)) {
      const randomize = document.createElement('button'); randomize.type = 'button'; randomize.className = 'semantic-randomize'; randomize.textContent = 'Randomize';
      randomize.addEventListener('click', () => { const min = Math.ceil(Number(control.min)); const max = Math.floor(Number(control.max)); const bytes = new Uint32Array(1); crypto.getRandomValues(bytes); control.value = String(min + (bytes[0] % Math.max(1, max - min + 1))); range.value = control.value; record.lastValue = control.value; dispatchSemanticEdit(control); });
      wrap.after(randomize);
    }
    addControlMeta(label, control);
  }
  function decorateChoices(label, control) {
    const options = [...control.options]; const palette = control.id === 'previewPalette';
    if (!palette && options.length > 5) { addControlMeta(label, control); return; }
    const choices = document.createElement('span'); choices.className = palette ? 'semantic-palettes' : 'semantic-choices'; choices.setAttribute('role', 'radiogroup'); choices.setAttribute('aria-label', controlLabelText(label));
    const buttons = options.map((option, index) => { const button = document.createElement('button'); button.type = 'button'; button.className = palette ? 'semantic-palette' : 'semantic-choice'; button.dataset.value = option.value; button.setAttribute('role', 'radio');
      if (palette) { const swatch = document.createElement('span'); swatch.className = 'semantic-swatch'; (PALETTE_SWATCHES[option.value] || ['#1b2d35', '#4f8290', '#b9e9dc']).forEach((color) => { const stripe = document.createElement('i'); stripe.style.background = color; swatch.append(stripe); }); button.append(swatch, document.createTextNode(option.textContent)); } else button.textContent = option.textContent;
      button.addEventListener('click', () => { control.value = option.value; syncSemanticControls(); dispatchSemanticEdit(control); });
      button.addEventListener('keydown', (event) => { if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return; event.preventDefault(); const direction = event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 1; const next = buttons[(index + direction + buttons.length) % buttons.length]; next.focus(); next.click(); }); choices.append(button); return button; });
    control.classList.add('semantic-source'); control.setAttribute('aria-hidden', 'true'); control.tabIndex = -1; control.after(choices); semanticControls.set(control, {kind: 'choices', control, buttons}); addControlMeta(label, control);
  }
  function decorateSwitch(label, control) {
    const compactPlantOptic = Boolean(control.closest('.final-optic-row'));
    const button = document.createElement('button'); button.type = 'button'; button.className = 'semantic-switch'; button.setAttribute('role', 'switch'); button.setAttribute('aria-label', controlLabelText(label)); button.addEventListener('click', () => {
      control.checked = !control.checked; syncSemanticControls();
      if (compactPlantOptic) {
        const optic = plantOptics.find((candidate) => $(candidate.enabled) === control);
        if (optic) syncPlantOpticControl(optic);
      }
      dispatchSemanticEdit(control);
    }); control.classList.add('semantic-source'); control.setAttribute('aria-hidden', 'true'); control.tabIndex = -1; control.after(button); semanticControls.set(control, {kind: 'switch', control, button});
    if (!compactPlantOptic) addControlMeta(label, control);
  }
  function wireExactNumber(record) {
    const {control, range} = record;
    const protect = (event) => {
      if (finiteControlValue(control)) {
        control.removeAttribute('aria-invalid');
        if (range) range.value = control.value;
        return;
      }
      control.setAttribute('aria-invalid', 'true'); event.stopImmediatePropagation();
    };
    control.addEventListener('input', protect);
    control.addEventListener('input', () => dispatchSemanticEdit(control));
    control.addEventListener('change', protect);
    control.addEventListener('blur', () => { if (!finiteControlValue(control)) control.value = record.lastValue; });
  }
  function decorateExactNumber(label, control) {
    const record = {kind: 'exact', control, lastValue: control.value};
    semanticControls.set(control, record);
    wireExactNumber(record);
    addControlMeta(label, control);
  }
  function installSemanticControls(scope) {
    scope.querySelectorAll('input,select,textarea').forEach((control) => {
      if (!isSemanticControl(control)) return;
      const label = control.closest('label');
      if (!label || ['animationChoice', 'backgroundChoice', 'widgetChoice'].includes(control.id)) return;
      control.dataset.semanticInstrument = 'true';
      if (!Object.hasOwn(control.dataset, 'semanticDefault') && !parameterEdits.has(control)) {
        control.dataset.semanticDefault = control.type === 'checkbox' ? String(control.checked) : control.value;
      }
      label.classList.add('semantic-control');
      if (control.type === 'checkbox') decorateSwitch(label, control);
      else if (control.tagName === 'SELECT') decorateChoices(label, control);
      else if (control.type === 'number' && control.min !== '' && control.max !== '') decorateNumeric(label, control);
      else if (control.type === 'number') decorateExactNumber(label, control);
      else { control.classList.add('semantic-text'); addControlMeta(label, control); }
    });
  }
  function syncSemanticControls() {
    for (const [control, record] of semanticControls) {
      if (!control.isConnected) { semanticControls.delete(control); continue; }
      if (record.kind === 'number') syncNumericRange(record);
      if ((record.kind === 'number' || record.kind === 'exact') && finiteControlValue(control)
          && control.getAttribute('aria-invalid') !== 'true') record.lastValue = control.value;
      if (record.kind === 'choices') record.buttons.forEach((button) => {
        const selected = button.dataset.value === control.value;
        button.setAttribute('aria-checked', String(selected)); button.tabIndex = selected ? 0 : -1;
      });
      if (record.kind === 'switch') record.button.setAttribute('aria-checked', String(control.checked));
    }
  }
  const componentEditors = new Map();
  const widgetEditors = new Map();
  const parameterEdits = new WeakMap();
  const presetRequests = new Map();
  const componentRoles = ['animation', 'background', 'widget'];
  const sceneComponentRoles = ['animation', 'background'];
  const manualPlacement = (strip = 0, led = 0) => ({
    mode: 'manual', strip_translation: strip, led_translation: led,
  });

  function componentKey(ref) {
    return ref && `${ref.provider}:${ref.component_id}:v${ref.version}`;
  }
  function componentDescriptor(ref) {
    const entry = state.components.get(componentKey(ref));
    return entry?.role === ref?.role && entry?.available !== false ? entry : null;
  }
  function componentRef(entry, parameters = entry.parameters || {}) {
    const {component_id, version, provider, role} = entry;
    return {component_id, version, provider, role, parameters: structuredClone(parameters)};
  }
  function defaultComponent(role, component_id) {
    const ref = {component_id, version: 1, provider: 'python', role};
    return componentRef(ref, componentDescriptor(ref)?.parameters);
  }
  function defaultScene() {
    return {schema: 'ledgrid.scene.v2',
      background: defaultComponent('background', 'solid_background'),
      animation: defaultComponent('animation', 'aurora_curtains'),
      widgets: [], plants: {effects: {version: 1, active: [], strengths: {}}},
      look: {palette_id: $('#previewPalette').value, pace: scenePace(), presentation_brightness: number('#sceneLuminance')},
    };
  }
  function componentOption(entry) {
    const option = new Option(entry.name, entry.component_id);
    option.dataset.componentKey = componentKey(entry);
    option.disabled = entry.available === false;
    return option;
  }
  function selectedComponent(select) {
    return state.components.get(select.selectedOptions[0]?.dataset.componentKey);
  }
  function installComponentChoices() {
    for (const role of componentRoles) {
      const select = $(`#${role}Choice`);
      const entries = [...state.components.values()].filter((entry) => entry.role === role);
      select.replaceChildren(...entries.map(componentOption));
      select.disabled = !entries.some((entry) => entry.available !== false);
    }
    $('#addWidget').disabled = $('#widgetChoice').disabled;
  }
  function selectComponentRef(select, ref) {
    const key = componentKey(ref);
    let option = [...select.options].find((item) => item.dataset.componentKey === key);
    if (!option && ref) {
      select.querySelector('[data-preserved]')?.remove();
      option = new Option(`${ref.component_id} (preserved)`, ref.component_id);
      option.dataset.componentKey = key;
      option.dataset.preserved = 'true';
      option.disabled = true;
      select.append(option);
    }
    if (option && !option.dataset.preserved) select.querySelector('[data-preserved]')?.remove();
    if (option) option.selected = true;
  }
  function parameterLabel(name) {
    const words = name.replaceAll('_', ' ');
    return words.charAt(0).toUpperCase() + words.slice(1);
  }
  function supportedParameter(spec) {
    return spec.control !== 'fixed' && ['int', 'float', 'bool', 'str', 'color', 'cells'].includes(spec.type);
  }
  function parameterBound(spec, bound, parameters) {
    const supplied = parameters[spec[`${bound}_parameter`]];
    if (!Number.isFinite(supplied)) return spec[bound];
    if (!Number.isFinite(spec[bound])) return supplied;
    return bound === 'min' ? Math.max(spec[bound], supplied) : Math.min(spec[bound], supplied);
  }
  function setNumberBounds(control, spec, parameters) {
    const min = parameterBound(spec, 'min', parameters);
    const max = parameterBound(spec, 'max', parameters);
    control.min = Number.isFinite(min) ? String(min) : '';
    control.max = Number.isFinite(max) ? String(max) : '';
    control.dataset.uiMin = Number.isFinite(spec.ui_min) ? String(Math.max(spec.ui_min, min ?? -Infinity)) : control.min;
    control.dataset.uiMax = Number.isFinite(spec.ui_max) ? String(Math.min(spec.ui_max, max ?? Infinity)) : control.max;
    control.dataset.coarseStep = spec.coarse_step ?? '';
  }
  function parameterInputValue(control, spec, options) {
    if (options.length) return options.find((option) => String(option) === control.value);
    if (control.type === 'checkbox') return control.checked;
    if (spec.type === 'int' || spec.type === 'float') {
      const value = control.valueAsNumber;
      if (!Number.isFinite(value) || control.validity.rangeUnderflow || control.validity.rangeOverflow) return undefined;
      if (spec.type === 'int' && !Number.isInteger(value)) return undefined;
      return value;
    }
    if (spec.type === 'color') return control.value.slice(1).match(/../g).map((channel) => parseInt(channel, 16));
    if (spec.type === 'cells') {
      let cells;
      try { cells = JSON.parse(control.value); } catch (_) { return undefined; }
      const inBounds = (value, min, max) => Number.isInteger(value) && value >= (min ?? -Infinity) && value <= (max ?? Infinity);
      if (!Array.isArray(cells) || cells.length > (spec.max_items ?? Infinity)
          || cells.some((cell) => !Array.isArray(cell) || cell.length !== 2
            || !inBounds(cell[0], spec.strip_min, spec.strip_max) || !inBounds(cell[1], spec.led_min, spec.led_max))) return undefined;
      return cells;
    }
    if (Number.isInteger(spec.max_length) && [...control.value].length > spec.max_length) return undefined;
    return control.value;
  }
  function parameterControlValue(value, spec) {
    if (spec.type === 'color' && Array.isArray(value)) return `#${value.map((channel) => channel.toString(16).padStart(2, '0')).join('')}`;
    if (spec.type === 'cells') return JSON.stringify(value ?? []);
    return String(value ?? '');
  }
  function createParameterControl({prefix, name, spec, defaultValue, parameters, update}) {
    const label = document.createElement('label');
    label.dataset.parameter = name;
    label.textContent = spec.label || parameterLabel(name);
    const options = Array.isArray(spec.options) ? spec.options : [];
    if (spec.autoplay_only) label.title = 'Applies to autoplay.';
    const control = document.createElement(options.length ? 'select' : spec.type === 'cells' ? 'textarea' : 'input');
    control.id = `${prefix}-${name}`;
    if (options.length) {
      for (const option of options) control.append(new Option(spec.option_labels?.[option] || String(option), String(option)));
    } else if (spec.type === 'int' || spec.type === 'float') {
      control.type = 'number';
      control.step = spec.step ?? (spec.type === 'int' ? 1 : .01);
      setNumberBounds(control, spec, parameters);
    } else if (spec.type === 'bool') control.type = 'checkbox';
    else if (spec.type === 'color') control.type = 'color';
    else if (spec.type === 'cells') { control.rows = 3; control.spellcheck = false; }
    else if (spec.control === 'emoji_text') { control.inputMode = 'text'; control.autocapitalize = 'off'; }
    if (spec.description) control.title = spec.description;
    if (spec.unit) label.textContent += ` (${spec.unit})`;
    if (defaultValue !== undefined) control.dataset.semanticDefault = parameterControlValue(defaultValue, spec);
    label.append(control);
    let lastValue;
    function sync(nextParameters, force = false) {
      const value = nextParameters[name] ?? defaultValue;
      if (control.type === 'number') setNumberBounds(control, spec, nextParameters);
      if (force || !sameLocalParameters(lastValue, value)) {
        if (control.type === 'checkbox') control.checked = Boolean(value);
        else if (control.value !== parameterControlValue(value, spec)) control.value = parameterControlValue(value, spec);
        control.removeAttribute('aria-invalid');
      }
      lastValue = value;
    }
    sync(parameters, true);
    parameterEdits.set(control, (scene) => {
      const value = parameterInputValue(control, spec, options);
      if (value === undefined) { control.setAttribute('aria-invalid', 'true'); return false; }
      control.removeAttribute('aria-invalid');
      return update(scene, name, value);
    });
    const isLiveText = !options.length && spec.type === 'str' && spec.mutation !== 'commit';
    control.addEventListener('change', edit);
    if (isLiveText || spec.type === 'color') control.addEventListener('input', edit);
    if (isLiveText || control.type === 'number' || spec.type === 'color') {
      control.addEventListener('focus', () => beginControlEdit(control));
      control.addEventListener('beforeinput', () => beginControlEdit(control));
      control.addEventListener('change', () => finishControlEditAfterEvent(control));
      control.addEventListener('blur', () => finishControlEditAfterEvent(control));
    }
    control.addEventListener('blur', () => { if (control.getAttribute('aria-invalid') === 'true') sync({[name]: lastValue}, true); });
    return {label, sync};
  }
  function positionedAction(entry) {
    const actions = entry?.input_capability?.actions || {};
    return Object.entries(actions).find(([, action]) => action.position);
  }
  function createComponentEditor(host, ref, resolveRef, prefix) {
    const entry = componentDescriptor(ref);
    host.replaceChildren();
    if (!entry) {
      const message = document.createElement('p'); message.className = 'status-line';
      message.textContent = state.components.size
        ? `Unsupported ${ref.role} component ${ref.component_id}, version ${ref.version}.`
        : 'Loading component controls…';
      host.append(message);
      return {entry, sync() {}};
    }
    const cards = document.createElement('div'); cards.className = 'operation-row component-presets';
    cards.setAttribute('aria-label', `${entry.name} presets`); cards.hidden = true;
    const presetState = document.createElement('p'); presetState.className = 'status-line component-preset-state';
    presetState.setAttribute('aria-live', 'polite'); presetState.hidden = true;
    host.append(cards, presetState);
    const schema = entry.parameter_schema || {};
    const names = Object.keys(schema).filter((name) => supportedParameter(schema[name]));
    const preferred = entry.preferred_parameters?.length ? entry.preferred_parameters.filter((name) => names.includes(name)) : names;
    const primaryNames = preferred.slice(0, 4);
    const fields = names.map((name) => [name, createParameterControl({
      prefix, name, spec: schema[name], defaultValue: entry.parameters?.[name], parameters: ref.parameters,
      update: (scene, key, value) => {
        const target = resolveRef(scene);
        if (!target || componentKey(target) !== componentKey(ref)) return false;
        target.parameters[key] = value;
      },
    })]);
    if (fields.length) {
      const disclosure = document.createElement('section');
      disclosure.className = 'control-disclosure';
      disclosure.dataset.componentId = entry.component_id;
      disclosure.setAttribute('aria-label', `${entry.name} controls`);
      const primary = document.createElement('div'); primary.className = 'primary-control-grid'; primary.setAttribute('aria-label', 'Primary controls');
      const fieldsByName = new Map(fields);
      for (const name of primaryNames) primary.append(fieldsByName.get(name).label);
      disclosure.append(primary);
      const advanced = fields.filter(([name]) => !primaryNames.includes(name));
      if (advanced.length) {
        const details = document.createElement('details'); details.className = 'advanced-controls';
        const summary = document.createElement('summary'); summary.textContent = 'Advanced tuning';
        const grid = document.createElement('div'); grid.className = 'advanced-control-grid';
        grid.setAttribute('aria-label', 'Advanced controls');
        advanced.forEach(([, field]) => grid.append(field.label));
        details.append(summary, grid); disclosure.append(details);
      }
      host.append(disclosure);
    }
    const pointAction = positionedAction(entry)?.[1];
    if (pointAction) {
      const gesture = document.createElement('p');
      gesture.className = 'status-line'; gesture.textContent = `Tap the live preview to ${pointAction.label.toLocaleLowerCase()}.`;
      host.append(gesture);
    }
    if (entry.input_capability?.mode === 'player' && entry.component_id === 'tetris') {
      // Tetris has a dedicated controller-guarded player route.
      const play = document.createElement('a'); play.className = 'button secondary tetris-player-link'; play.href = '/play/tetris'; play.textContent = 'Play Tetris on wall';
      host.append(play);
    }
    installSemanticControls(host);
    let activeRef = ref;
    let choices = [];
    function syncPresetState() {
      const match = choices.find((preset) => sameLocalParameters(preset.parameters, activeRef.parameters));
      [...cards.children].forEach((button, index) => {
        const active = match === choices[index];
        button.setAttribute('aria-pressed', String(active)); button.classList.toggle('active', active);
      });
      presetState.hidden = !choices.length;
      const text = match ? `Preset: ${match.name}` : 'Custom remix';
      if (choices.length) updatePlacementText(presetState, text);
    }
    if (entry.preset_count > 0 || (ref.role !== 'animation' && entry.preset_count == null)) {
      loadComponentPresets(entry).then((presets) => {
        if (!host.contains(cards)) return;
        choices = presets;
        for (const preset of presets) {
          const button = document.createElement('button');
          button.type = 'button'; button.className = 'button secondary'; button.textContent = preset.name; button.title = preset.description || preset.name;
          button.addEventListener('click', () => {
            void edit(null, null, (next) => {
              const target = resolveRef(next);
              if (!target || componentKey(target) !== componentKey(ref)) return false;
              target.parameters = presetParameters(entry, preset, target.parameters);
              applyAuthoredPresetPresentation(next, preset);
            });
          });
          cards.append(button);
        }
        cards.hidden = !presets.length;
        syncPresetState();
      }).catch((error) => {
        if (host.contains(cards)) { presetState.hidden = false; presetState.textContent = error.message; }
      });
    }
    return {entry, sync(nextRef, force) {
      activeRef = nextRef;
      const parameters = {...entry.parameters, ...nextRef.parameters};
      fields.forEach(([, field]) => field.sync(parameters, force));
      syncPresetState();
    }};
  }
  function presetParameters(entry, preset, previous = {}) {
    const next = structuredClone(preset.parameters);
    for (const [name, spec] of Object.entries(entry.parameter_schema || {})) {
      if (spec.preserve_on_preset && Object.hasOwn(previous, name)) next[name] = structuredClone(previous[name]);
    }
    return next;
  }
  function loadComponentPresets(entry) {
    if (Object.hasOwn(state.componentPresets, entry.component_id)) return Promise.resolve(state.componentPresets[entry.component_id]);
    const pending = presetRequests.get(entry.component_id);
    if (pending) return pending;
    const request = requestJson(`${api}/components/${encodeURIComponent(entry.component_id)}/presets`).then((body) => {
      const presets = Array.isArray(body.presets) ? body.presets : [];
      state.componentPresets[entry.component_id] = presets;
      refreshPlaylistIdentities();
      return presets;
    }).finally(() => presetRequests.delete(entry.component_id));
    presetRequests.set(entry.component_id, request);
    return request;
  }
  function createWidgetEditor(widget, entry) {
    const element = document.createElement('section');
    element.className = 'widget-settings';
    element.dataset.widgetId = widget.id;
    const title = document.createElement('h3'); title.textContent = entry?.name || parameterLabel(widget.component.component_id);
    element.setAttribute('aria-label', title.textContent);
    const actions = document.createElement('div'); actions.className = 'operation-row';
    const visible = document.createElement('input'); visible.type = 'checkbox';
    const visibility = document.createElement('label'); visibility.textContent = `Show ${title.textContent}`; visibility.append(visible);
    const findWidget = (scene) => scene.widgets.find((item) => item.id === widget.id);
    visible.dataset.semanticDefault = 'true';
    parameterEdits.set(visible, (scene) => { const target = findWidget(scene); if (!target) return false; target.visible = visible.checked; });
    visible.addEventListener('change', edit);
    const remove = document.createElement('button');
    remove.type = 'button'; remove.className = 'button text'; remove.textContent = `Remove ${title.textContent}`;
    remove.addEventListener('click', () => { void edit(null, null, (scene) => { scene.widgets = scene.widgets.filter((item) => item.id !== widget.id); }); });
    actions.append(visibility, remove);
    const settings = document.createElement('div'); settings.className = 'widget-settings';
    const controls = document.createElement('div'); settings.append(controls);
    const placement = document.createElement('details'); placement.className = 'advanced-controls';
    const summary = document.createElement('summary'); summary.textContent = 'Placement';
    const grid = document.createElement('div'); grid.className = 'advanced-control-grid';
    const modeLabel = document.createElement('label'); modeLabel.textContent = 'Position';
    const mode = document.createElement('select');
    mode.append(new Option('Manual', 'manual'), new Option('Automatic', 'auto'));
    modeLabel.append(mode); grid.append(modeLabel);
    const placementStatus = document.createElement('p'); placementStatus.className = 'status-line'; placementStatus.setAttribute('role', 'status');
    const prefix = `widget-${widget.id}`;
    const bounds = {strip_translation: {label: 'Across', min: -32, max: 32}, led_translation: {label: 'Down', min: -137, max: 137}};
    const defaultPlacement = {...manualPlacement(), ...entry?.default_placement};
    let manual = widget.placement.mode === 'manual' ? widget.placement : defaultPlacement;
    const offsets = Object.entries(bounds).map(([name, bounds]) => createParameterControl({
      prefix, name, spec: {type: 'int', ...bounds}, defaultValue: defaultPlacement[name], parameters: manual,
      update: (scene, key, value) => {
        const target = findWidget(scene);
        if (target?.placement.mode !== 'manual') return false;
        target.placement[key] = value;
      },
    }));
    offsets.forEach(({label}) => grid.append(label));
    parameterEdits.set(mode, (scene) => {
      const target = findWidget(scene);
      if (!target) return false;
      target.placement = mode.value === 'auto' ? {mode: 'auto'} : manualPlacement(manual.strip_translation, manual.led_translation);
    });
    mode.addEventListener('change', edit);
    grid.append(placementStatus);
    placement.append(summary, grid);
    settings.append(placement);
    element.append(title, actions, settings);
    const editor = createComponentEditor(controls, widget.component, (scene) => findWidget(scene)?.component, prefix);
    installSemanticControls(element);
    return {entry, element, placementStatus, sync(nextWidget, force) {
      visible.checked = nextWidget.visible;
      settings.hidden = !nextWidget.visible;
      const current = nextWidget.placement;
      mode.value = current.mode;
      if (current.mode === 'manual') manual = current;
      offsets.forEach((field) => { field.label.hidden = current.mode !== 'manual'; field.sync(manual, force); });
      editor.sync(nextWidget.component, force);
    }};
  }
  function syncComponentEditors(force = false) {
    if (!state.scene) return;
    for (const role of sceneComponentRoles) {
      const ref = state.scene[role];
      selectComponentRef($(`#${role}Choice`), ref);
      let editor = componentEditors.get(role);
      if (editor?.key !== componentKey(ref) || editor?.entry !== componentDescriptor(ref)) {
        editor = createComponentEditor($(`#${role}Controls`), ref, (scene) => scene[role], `${role}-${ref.component_id}`);
        editor.key = componentKey(ref);
        componentEditors.set(role, editor);
      }
      editor.sync(ref, force);
    }
    const widgets = state.scene.widgets;
    const widgetIds = new Set(widgets.map(({id}) => id));
    for (const [id, editor] of widgetEditors) {
      if (!widgetIds.has(id)) { editor.element.remove(); widgetEditors.delete(id); }
    }
    const host = $('#widgetEditors');
    widgets.forEach((widget, index) => {
      let editor = widgetEditors.get(widget.id);
      const entry = componentDescriptor(widget.component);
      if (editor?.key !== componentKey(widget.component) || editor?.entry !== entry) {
        editor?.element.remove();
        editor = createWidgetEditor(widget, entry);
        editor.key = componentKey(widget.component);
        widgetEditors.set(widget.id, editor);
      }
      if (host.children[index] !== editor.element) host.insertBefore(editor.element, host.children[index] || null);
      editor.sync(widget, force);
    });
    syncSemanticControls();
  }
  function sceneFromControls() {
    const next = structuredClone(state.scene || defaultScene());
    applyPlantOptics(next);
    next.look = {
      palette_id: $('#previewPalette').value,
      pace: scenePace(),
      presentation_brightness: number('#sceneLuminance'),
    };
    return next;
  }
  function syncScenePresentation(scene) {
    $('#previewPalette').value = scene.look?.palette_id ?? 'mist';
    syncSceneSpeed(scene.look?.pace ?? DEFAULT_SCENE_PACE, scene.animation);
    $('#sceneLuminance').value = scene.look?.presentation_brightness ?? .82;
    const plantEffects = scene.plants?.effects || {};
    const activeOptics = new Set(plantEffects.active || []);
    const strengths = plantEffects.strengths || {};
    plantOptics.forEach((optic) => {
      $(optic.enabled).checked = activeOptics.has(optic.id);
      $(optic.strength).value = strengths[optic.id] ?? .5;
      syncPlantOpticControl(optic);
    });
  }
  function applyScene(scene) {
    clearSaveFeedback();
    state.scene = structuredClone(scene);
    syncScenePresentation(scene);
    syncComponentEditors(true);
    placementWarning();
  }
  function widgetPlacementName(widgetId) {
    const ref = state.scene?.widgets.find((widget) => widget.id === widgetId)?.component;
    return componentDescriptor(ref)?.name || ref?.component_id || `Widget ${widgetId}`;
  }

  const GALLERY_FAVORITES_KEY = 'ledgrid-composer-gallery-favorites-v1';
  function readGalleryFavorites() {
    try { return new Set(JSON.parse(window.localStorage.getItem(GALLERY_FAVORITES_KEY) || '[]').filter((key) => typeof key === 'string')); }
    catch (_) { return new Set(); }
  }
  function writeGalleryFavorites() {
    try { window.localStorage.setItem(GALLERY_FAVORITES_KEY, JSON.stringify([...state.gallery.favorites].sort())); } catch (_) { /* Private browsing can withhold storage. */ }
  }
  function galleryEntries() {
    const query = state.gallery.query.trim().toLocaleLowerCase();
    return state.gallery.entries.filter((entry) => {
      if (state.gallery.filter === 'favorites' && !state.gallery.favorites.has(entry.key)) return false;
      return !query || `${entry.name} ${entry.description} ${entry.component_id}`.toLocaleLowerCase().includes(query);
    });
  }
  function galleryScene(entry, parameters = entry.parameters) {
    const next = structuredClone(state.scene || defaultScene());
    next.animation = componentRef(entry, parameters);
    return next;
  }
  function stableGalleryJson(value) {
    if (Array.isArray(value)) return `[${value.map(stableGalleryJson).join(',')}]`;
    if (value && typeof value === 'object') return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableGalleryJson(value[key])}`).join(',')}}`;
    return JSON.stringify(value);
  }
  function galleryThumbnail(entry, preset = null) {
    // Catalog illustrations stay legible independently of the current Look,
    // widgets, or stopped wall. Browser caching also survives grid rebuilds.
    const image = document.createElement('img');
    image.className = 'gallery-thumb'; image.width = 33; image.height = 138;
    const name = preset ? preset.name : entry.name;
    image.alt = `${name} representative catalog preview`;
    image.title = 'Catalog example. The main Preview shows your current Scene settings.';
    image.loading = 'lazy'; image.decoding = 'async';
    const unavailable = () => {
      const label = document.createElement('span'); label.className = 'gallery-thumb-unavailable';
      label.textContent = 'Preview unavailable'; label.setAttribute('aria-label', `${name} preview unavailable`);
      return label;
    };
    image.addEventListener('error', () => image.replaceWith(unavailable()), {once: true});
    const url = preset ? window.ComposerPresetPreviews?.[entry.component_id]?.[preset.preset_id] : window.ComposerGalleryPreviews?.[entry.component_id];
    if (url) image.src = url;
    else return unavailable();
    return image;
  }
  function placeGalleryDetail(entry) {
    const detail = $('#galleryDetail');
    const cards = [...$('#galleryGrid').querySelectorAll('.gallery-card')];
    const selectedIndex = cards.findIndex((card) => card.dataset.galleryKey === entry.key);
    if (selectedIndex < 0) { detail.hidden = true; return false; }
    // The Gallery becomes one column on a phone.  Keep this in sync with the
    // narrow-screen Gallery rule so the detail follows the selected card row.
    const columns = window.matchMedia?.('(max-width: 760px)').matches ? 1 : 2;
    const rowEnd = Math.min(cards.length - 1, Math.floor(selectedIndex / columns) * columns + columns - 1);
    cards[rowEnd].after(detail);
    detail.hidden = false;
    return true;
  }
  function installGalleryLayoutListener() {
    window.matchMedia?.('(max-width: 760px)')?.addEventListener?.('change', () => {
      const entry = galleryEntries().find((entry) => entry.key === state.gallery.detail);
      if (entry) placeGalleryDetail(entry);
    });
  }
  function showGalleryDetail(entry) {
    const detail = $('#galleryDetail'); detail.replaceChildren(); detail.hidden = false;
    const heading = document.createElement('h3'); heading.textContent = entry.name;
    const description = document.createElement('p'); description.textContent = entry.description;
    const status = document.createElement('p'); status.className = 'gallery-card-meta'; status.textContent = entry.available ? `${entry.preset_count} preset${entry.preset_count === 1 ? '' : 's'} · live selection` : 'Unavailable on this Composer';
    const presets = document.createElement('div'); presets.className = 'gallery-preset-list'; presets.setAttribute('aria-label', `${entry.name} presets`);
    const illustration = galleryThumbnail(entry); illustration.classList.add('gallery-detail-thumb');
    detail.append(illustration, heading, description, status, presets);
    state.gallery.detail = entry.key;
    const detailToken = ++state.gallery.detailToken;
    placeGalleryDetail(entry);
    if (!entry.available) return;
    const loading = document.createElement('span'); loading.className = 'muted'; loading.textContent = 'Loading presets…'; presets.append(loading);
    loadComponentPresets(entry).then((choices) => {
      if (state.gallery.detail !== entry.key || state.gallery.detailToken !== detailToken) return;
      presets.replaceChildren();
      choices.forEach((preset) => {
        const button = document.createElement('button'); button.type = 'button'; button.className = 'button secondary gallery-preset'; button.title = preset.description || preset.name;
        button.setAttribute('aria-label', preset.name);
        const label = document.createElement('span'); label.textContent = preset.name;
        button.append(galleryThumbnail(entry, preset), label);
        button.addEventListener('click', () => selectGalleryEntry(entry, preset)); presets.append(button);
      });
      if (!presets.childElementCount) presets.textContent = 'No authored presets.';
    }).catch((error) => { if (state.gallery.detail === entry.key && state.gallery.detailToken === detailToken) presets.textContent = error.message; });
  }
  async function selectGalleryEntry(entry, preset = null) {
    if (!entry.available) return;
    const intentToken = beginIntent();
    const previous = structuredClone(state.scene || defaultScene());
    const parameters = preset
      ? presetParameters(entry, preset, componentKey(previous.animation) === componentKey(entry) ? previous.animation.parameters : {})
      : entry.parameters;
    const next = galleryScene(entry, parameters);
    if (preset) applyAuthoredPresetPresentation(next, preset);
    state.dirty = true; applyScene(next);
    try {
      const published = await submit(next, {intentToken, rememberEdit: true, previous});
      if (!intentIsCurrent(intentToken) || published.coalesced) return;
      refreshGallerySelection();
      // Publication can accept the authored Scene while the wall rejects its
      // acknowledgement.  renderStatus already exposes that retryable failure.
      if (state.wall.activationError) return;
      $('#operationMessage').textContent = preset ? `${entry.name} · ${preset.name} is live.` : `${entry.name} is live.`;
    } catch (error) {
      if (!intentIsCurrent(intentToken)) return;
      if (!state.publication.queued && !state.publication.inFlight) { state.scene = previous; applyScene(previous); refreshGallerySelection(); }
      $('#operationMessage').textContent = error.message || 'That animation is unavailable; the previous output is still live.';
    }
  }
  function refreshGallerySelection() { renderGallery(); }
  function renderGallery() {
    const grid = $('#galleryGrid'); const entries = galleryEntries();
    const detail = $('#galleryDetail');
    grid.replaceChildren();
    entries.forEach((entry) => {
      const card = document.createElement('div'); card.className = 'gallery-card'; card.dataset.galleryKey = entry.key; card.setAttribute('role', 'listitem');
      const select = document.createElement('button'); select.type = 'button'; select.className = 'gallery-select'; select.setAttribute('aria-current', String(componentKey(state.scene?.animation) === componentKey(entry))); select.disabled = !entry.available; select.setAttribute('aria-label', `Play & edit ${entry.name}`);
      const thumbnail = galleryThumbnail(entry);
      const copy = document.createElement('span'); copy.className = 'gallery-card-copy';
      const title = document.createElement('span'); title.className = 'gallery-card-title'; title.textContent = entry.name;
      const description = document.createElement('span'); description.className = 'gallery-card-description'; description.textContent = entry.description;
      const meta = document.createElement('span'); meta.className = 'gallery-card-meta'; meta.textContent = entry.available ? `${entry.preset_count} presets · Play & edit` : 'Unavailable';
      copy.append(title, description, meta); select.append(thumbnail, copy);
      select.addEventListener('click', () => { showGalleryDetail(entry); selectGalleryEntry(entry); });
      const favorite = document.createElement('button'); favorite.type = 'button'; favorite.className = 'gallery-favorite'; favorite.setAttribute('aria-label', `Favorite ${entry.name}`); favorite.setAttribute('aria-pressed', String(state.gallery.favorites.has(entry.key))); favorite.textContent = state.gallery.favorites.has(entry.key) ? '★' : '☆';
      favorite.addEventListener('click', (event) => { event.stopPropagation(); if (state.gallery.favorites.has(entry.key)) state.gallery.favorites.delete(entry.key); else state.gallery.favorites.add(entry.key); writeGalleryFavorites(); renderGallery(); });
      card.append(select, favorite); grid.append(card);
    });
    // The detail lives in this grid; retain its node across card rebuilds so
    // current preset requests and event handlers keep their attached target.
    grid.append(detail);
    const detailEntry = entries.find((entry) => entry.key === state.gallery.detail);
    if (detailEntry) placeGalleryDetail(detailEntry);
    else $('#galleryDetail').hidden = true;
    $('#galleryCount').textContent = `${entries.length} animation${entries.length === 1 ? '' : 's'}`;
    $('#galleryEmpty').hidden = entries.length > 0;
  }
  async function loadGallery() {
    try {
      const response = await fetch(`${api}/gallery`, {cache: 'no-store'}); const body = await response.json(); if (!response.ok) throw new Error(body.error || 'Gallery is unavailable.');
      state.gallery.entries = Array.isArray(body.entries) ? body.entries : [];
      state.gallery.digest = body.catalog_digest;
      state.gallery.favorites = readGalleryFavorites();
      const entries = new Map(state.gallery.entries.map((entry) => [componentKey(entry), entry]));
      if (Array.isArray(body.components)) body.components.forEach((entry) => {
        const key = componentKey(entry);
        entries.set(key, {...entries.get(key), ...entry});
      });
      state.components = entries;
      syncSceneSpeed(state.scene?.look.pace ?? DEFAULT_SCENE_PACE);
      installComponentChoices();
      syncComponentEditors();
      renderGallery();
      refreshPlaylistIdentities();
    } catch (error) { $('#galleryCount').textContent = 'Unavailable'; $('#galleryEmpty').hidden = false; $('#galleryEmpty').textContent = error.message || 'Gallery is unavailable.'; }
  }
  function syncPlantOpticControl(optic) {
    const enabled = $(optic.enabled).checked;
    const strength = $(optic.strength);
    strength.disabled = !enabled;
    $(optic.value).textContent = `${Math.round(Number(strength.value) * 100)}%`;
  }
  function renderPlantOpticsStatus() { /* Compact rows carry their own current state. */ }
  function wirePlantOptic(optic) {
    const strength = $(optic.strength);
    $(optic.enabled).addEventListener('change', (event) => { syncPlantOpticControl(optic); edit(event); });
    strength.addEventListener('pointerdown', () => beginControlEdit(strength));
    strength.addEventListener('keydown', () => beginControlEdit(strength));
    strength.addEventListener('pointerup', () => finishControlEditAfterEvent(strength));
    strength.addEventListener('pointercancel', () => finishControlEditAfterEvent(strength));
    strength.addEventListener('keyup', () => finishControlEditAfterEvent(strength));
    strength.addEventListener('change', (event) => { syncPlantOpticControl(optic); edit(event); finishControlEditAfterEvent(strength); });
    strength.addEventListener('input', (event) => {
      syncPlantOpticControl(optic);
      edit(event);
    });
  }
  function applyPlantOptics(next) {
    const effects = next.plants?.effects || {};
    const active = Array.isArray(effects.active) ? effects.active : [];
    const strengths = effects.strengths && typeof effects.strengths === 'object' ? effects.strengths : {};
    const preservedActive = active.filter((id) => !plantOpticIds.has(id));
    const preservedStrengths = Object.fromEntries(Object.entries(strengths).filter(([id]) => !plantOpticIds.has(id)));
    plantOptics.forEach((optic) => {
      if (!$(optic.enabled).checked) return;
      preservedActive.push(optic.id);
      preservedStrengths[optic.id] = number(optic.strength);
    });
    next.plants = {effects: {version: 1, active: preservedActive, strengths: preservedStrengths}};
  }
  function updatePlacementText(node, text) {
    if (node && node.textContent !== text) node.textContent = text;
  }
  function updatePlacementWarning(node, text) {
    const hidden = !text;
    if (hidden && !node.hidden) node.hidden = true;
    updatePlacementText(node, text);
    if (!hidden && node.hidden) node.hidden = false;
  }
  function placementWarning(placements = null) {
    const warning = $('#widgetWarning');
    const entries = Object.entries(placements || {});
    for (const [id, editor] of widgetEditors) {
      const resolved = placements?.[id];
      const message = resolved ? `Across ${resolved.strip_translation}, Down ${resolved.led_translation}${resolved.clamped ? ' (kept on the wall)' : ''}.` : '';
      updatePlacementText(editor.placementStatus, message);
    }
    const messages = entries.filter(([, placement]) => placement?.warning).map(([widgetId, placement]) => {
      const details = [];
      if (placement.clamped) details.push('the requested position was kept inside the wall');
      if (placement.plant_overlap_pixels) details.push(`${placement.plant_overlap_pixels} clearance pixel${placement.plant_overlap_pixels === 1 ? '' : 's'} overlap`);
      if (placement.widget_overlap_pixels) details.push(`${placement.widget_overlap_pixels} pixel${placement.widget_overlap_pixels === 1 ? '' : 's'} overlap an earlier widget`);
      if (placement.used_fallback) details.push('this is the least-overlapping automatic position');
      if (!details.length) details.push(placement.warning);
      return `${widgetPlacementName(widgetId)} at Across ${placement.strip_translation}, Down ${placement.led_translation}: ${details.join('; ')}. Advisory only—the position is still live.`;
    });
    updatePlacementWarning(warning, messages.join(' '));
  }
  function drawFrameIntoCanvas(canvas, frame) {
    if (!frame || frame.encoding !== 'rgb_u8_base64') throw new Error('Preview returned an unsupported frame.');
    const bytes = Uint8Array.from(atob(frame.pixels), (character) => character.charCodeAt(0));
    const context = canvas.getContext('2d'); const image = context.createImageData(frame.width, frame.height);
    for (let strip = 0; strip < frame.width; strip += 1) for (let led = 0; led < frame.height; led += 1) { const source = (strip * frame.height + led) * 3; const target = ((frame.height - 1 - led) * frame.width + strip) * 4; image.data[target] = bytes[source]; image.data[target + 1] = bytes[source + 1]; image.data[target + 2] = bytes[source + 2]; image.data[target + 3] = 255; }
    context.putImageData(image, 0, 0);
  }
  function drawFrame(frame) { drawFrameIntoCanvas($('#scenePreview'), frame); }
  async function preview(scene, previewTime = null) { let response; try { response = await fetch(`${api}/preview`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({origin: 'composer', scene, ...(previewTime ? {preview: previewTime} : {})})}); } catch (_) { const error = new Error('Local Composer server unavailable.'); error.previewUnavailable = true; throw error; } const body = await response.json(); if (!response.ok) { const error = new Error(body.error || 'Preview could not render.'); error.previewUnavailable = response.status >= 500; throw error; } return body; }
  const previewScheduler = new window.ComposerPreviewScheduler({
    request: preview,
    isVisible: () => !document.hidden,
    onFrame: (body) => { drawFrame(body.frame); $('#previewIdentity').textContent = identity(body.basis); $('#previewStatus').textContent = 'Current Scene preview.'; placementWarning(body.widget_placements || {}); },
    onError: (error) => { $('#previewStatus').textContent = error.message || 'Preview could not render.'; if (error.previewUnavailable) window.dispatchEvent(new Event('composer-server-unavailable')); },
  });
  const paletteForVibe = Object.freeze({quiet: 'mist', neutral: 'neutral', vivid: 'spectrum', celebration: 'spectrum', cozy: 'ember'});
  function observedWallIdentity(_scene = null, observation = state.wall.observation) {
    if (observation?.active_identity?.scene_identity) return observation.active_identity.scene_identity;
    if (!observation?.scene) return null;
    return {revision: Number(observation.controller_state_revision || 0), digest: observation.scene_digest || 'controller scene · digest unavailable'};
  }
  function wallStatus(lastError = null) {
    const observation = state.wall.observation;
    const selected = observedWallIdentity();
    const connected = Boolean(observation?.controller_session_id && observation.freshness === 'fresh');
    const running = Boolean(connected && observation?.is_running);
    return {connected, running, armed: running,
      current: selected, desired: state.wall.requested ? {revision: state.sequence, digest: state.wall.requested.animation?.component_id || 'requested scene'} : null,
      observed: running ? selected : null, revision: Number(observation?.controller_state_revision || 0),
      receiver_connectivity: observation?.receiver_connectivity || {}, last_error: lastError || observation?.last_error || null};
  }
  function composerSceneFromWall(scene, observation) {
    if (scene?.schema === 'ledgrid.scene.v2') return structuredClone(scene);
    const selected = scene?.background?.provider === 'python' ? scene.background : scene?.known_python_fallback;
    if (!selected?.plugin_id) throw new Error('The selected wall scene has no editable Python animation.');
    const parameters = {...structuredClone(selected.resolved_parameters || {}), ...structuredClone(selected.parameter_overrides || {})};
    const clock = (scene.overlays || []).find((overlay) => overlay.slot_id === 'clock_overlay' && overlay.enabled);
    const speedBaseline = Number(state.wall.bootstrap?.global_control_contract?.operator_speed_baseline || .3);
    const vibeId = observation?.vibe?.state?.vibe_id || observation?.vibe?.vibe_id || 'neutral';
    const plantModifiers = observation?.plant_modifiers || {version: 1, active: [], strengths: {}};
    return {
      schema: 'ledgrid.scene.v2',
      background: {
        component_id: 'solid_background', version: 1, provider: 'python', role: 'background',
        parameters: {gain: 0, seed: Math.trunc(Number(parameters.seed || 0))},
      },
      animation: {component_id: selected.plugin_id, version: 1, provider: 'python', role: 'animation', parameters},
      widgets: clock ? [{
        id: 'clock', visible: true,
        component: {
          component_id: 'clock_overlay', version: 1, provider: 'python', role: 'widget',
          parameters: {...structuredClone(clock.component?.resolved_parameters || {}), ...structuredClone(clock.component?.parameter_overrides || {})},
        },
        placement: {
          mode: 'manual', strip_translation: Math.trunc(Number(clock.placement?.strip_translation || 0)),
          led_translation: Math.trunc(Number(clock.placement?.led_translation ?? -8)),
        },
      }] : [],
      plants: {effects: {
        version: 1, active: structuredClone(plantModifiers.active || []),
        strengths: structuredClone(plantModifiers.strengths || {}),
      }},
      look: {
        palette_id: paletteForVibe[vibeId] || 'neutral',
        pace: Math.max(0, Math.min(Number($('#sceneSpeedExact').max), Number(observation?.animation_speed_scale ?? speedBaseline) / speedBaseline)),
        presentation_brightness: Math.max(0, Math.min(2, Number(observation?.brightness ?? 128) / 255)),
      },
    };
  }
  function browserSceneForWall(scene) { return structuredClone(scene); }
  function globalSettingsForWall(scene, power, targetFps) {
    const observation = state.wall.observation || {};
    const bootstrap = state.wall.bootstrap || {};
    const observedVibe = observation.vibe?.state || observation.vibe || {};
    const vibeId = observedVibe.vibe_id || observedVibe.id || 'neutral';
    const profile = bootstrap.vibe_profiles?.find((item) => item.vibe_id === vibeId)
      || bootstrap.vibe_profiles?.find((item) => item.vibe_id === 'neutral');
    if (!profile?.resolved_profile_digest) throw new Error('The selected wall vibe is unavailable.');
    const allowedModifiers = new Set(bootstrap.global_control_contract?.plant_modifier_ids || []);
    const active = (observation.plant_modifiers?.active || []).filter((id) => allowedModifiers.has(id));
    const strengths = Object.fromEntries(active.map((id) => [id, Number(observation.plant_modifiers.strengths?.[id] ?? .5)]));
    const revision = Number(observation.active_identity?.global_settings_identity?.revision ?? observation.controller_state_revision);
    if (!Number.isSafeInteger(revision) || revision < 0) throw new Error('The wall settings observation has no usable revision.');
    const baseline = Number(bootstrap.global_control_contract?.operator_speed_baseline || .3);
    // Scene owns its authored palette, pace, effects and presentation brightness.
    // Preserve the separately observed installation output controls.
    const brightness = Number(observation.brightness ?? 128);
    const animationSpeed = Number(observation.animation_speed_scale ?? baseline);
    return {
      schema: 'ledgrid.global-settings-state', schema_version: 1, revision,
      vibe: {vibe_id: profile.vibe_id, profile_version: profile.profile_version, resolved_profile_digest: profile.resolved_profile_digest},
      plant_modifiers: {version: 1, active, strengths},
      output: {
        power: Boolean(power), brightness: Math.max(0, Math.min(255, Math.round(brightness))),
        animation_speed_scale: Math.max(0, animationSpeed),
        // The target is authored by the Motion control.  Actual FPS is
        // telemetry only, while Scene pace and renderer source rates stay in
        // their respective Scene/component contracts.
        target_fps: targetFps,
      },
    };
  }
  async function refreshWallStatus({adopt = false, preserveAuthored = false} = {}) {
    if (!state.wall.bootstrap) state.wall.bootstrap = await requestJson('/api/v1/composer/bootstrap');
    const priorRevision = state.wall.observation?.active_identity?.scene_identity?.digest || state.wall.scene?.revision;
    const [scenePayload, observation] = await Promise.all([
      requestJson('/api/v1/scene'), requestJson(`/api/v1/composer/settings/observed${state.wall.requestId ? `?request_id=${encodeURIComponent(state.wall.requestId)}` : ''}`),
    ]);
    state.wall.scene = scenePayload.scene || null;
    state.wall.observation = observation;
    if (observation.scene) state.wall.scene = structuredClone(observation.scene);
    if (observation.command_result?.state === 'failed' && observation.command_result.request_id === state.wall.requestId) { state.wall.activationError = observation.command_result.error || 'Controller playback failed; output may have been interrupted.'; state.wall.retryBlocked = true; }
    syncFrameRateObservation(observation);
    const shouldAdopt = Boolean(!preserveAuthored && scenePayload.scene && (adopt || (
      !state.wall.dirty && priorRevision != null && priorRevision !== (observation.active_identity?.scene_identity?.digest || scenePayload.scene.revision)
    )));
    if (shouldAdopt) {
      const current = composerSceneFromWall(scenePayload.scene, observation);
      state.scene = current; state.selection = null; clearHistory(); state.dirty = false; state.wall.dirty = false;
      state.wall.adoptedLook = structuredClone(current.look);
      state.wall.adoptedVibeId = observation?.vibe?.state?.vibe_id || observation?.vibe?.vibe_id || null;
      $('#sceneName').value = `Currently playing · ${current.animation.component_id.replaceAll('_', ' ')}`;
      applyScene(current); schedulePreview();
    }
    renderStatus(wallStatus());
    return scenePayload;
  }
  function renderStatus(payload) {
    const observation = state.wall.observation;
    const known = Boolean(!state.wall.statusUnavailable && observation?.controller_session_id && observation.freshness === 'fresh' && typeof observation.is_running === 'boolean');
    const status = {...(payload.status || payload), ...wallStatus()};
    status.connected = known; status.running = known && observation.is_running; status.armed = status.running;
    state.status = status;
    state.revision = Math.max(state.revision || 0, status.revision || 0);
    const nameOf = scene => {
      const id = scene?.animation?.component_id || scene?.background?.component_id || scene?.background?.plugin_id;
      return state.gallery?.entries?.find(item => item.component_id === id)?.name || (id ? id.replaceAll('_', ' ') : 'Scene unavailable');
    };
    const activeComponent = observation?.active_identity?.component_identities?.find(item => item.slot_id === 'animation');
    const observedName = observation?.scene ? nameOf(observation.scene) : activeComponent ? nameOf({animation:activeComponent}) : 'Scene unavailable';
    const pending = Boolean(state.wall.activating || state.publication.queued || state.publication.afterStop || state.publication.inFlight);
    const stopping = state.publication.queued?.kind === 'stop' || state.publication.inFlight?.kind === 'stop';
    $('#observedIdentity').textContent = known ? identity(status.observed) : 'Unknown';
    $('#desiredIdentity').textContent = identity(status.desired); $('#sceneRevision').textContent = String(status.revision ?? 0);
    $('#sceneIdentity').textContent = identity(status.current); $('#saveState').textContent = state.dirty ? 'Unsaved changes' : (state.selection?.kind === 'look' ? 'Saved scene' : 'Not saved');
    $('#workingSceneContext').textContent = `Editing: ${nameOf(state.scene)} · local preview`;
    const queued = state.publication.afterStop || state.publication.queued || state.publication.inFlight;
    $('#requestedSceneSummary').textContent = queued?.kind === 'publish' ? `Requesting: ${nameOf(queued.scene)}` : state.wall.requested ? `Last scene requested: ${nameOf(state.wall.requested)}` : 'No scene requested from this tab.';
    $('#liveAction').textContent = stopping ? 'Stopping output…' : 'Stop output';
    $('#liveAction').disabled = !known || !status.running || state.wall.activating || stopping;
    $('#resumeScene').hidden = !known || status.running;
    $('#resumeScene').disabled = pending || !state.scene;
    $('#operations-title').textContent = !known ? 'Playback unknown' : status.running ? `Playing · ${observedName}` : 'Output stopped';
    $('#playbackHint').textContent = !known ? 'Controller unavailable or stale. Reconnecting automatically.'
      : state.wall.activationError ? 'Action failed. Check the error below.'
      : state.authoredValidationError ? 'Edit needs attention. Previous controller playback is unchanged.'
      : pending ? 'Request pending. Controller playback is observed separately.'
      : status.running ? 'Controller playback reported; physical output is not verified.'
      : 'Resume edited scene, or choose or adjust a scene.';
    $('#connectionState').textContent = known ? 'Controller connected' : 'Controller unavailable';
    const connectivity = status.receiver_connectivity;
    const connectedReceivers = Array.isArray(connectivity) && connectivity.length ? connectivity.filter(item => item.receiver_connected === true && Number.isFinite(item.receiver_status_observed_at) && Date.now() / 1000 - item.receiver_status_observed_at >= 0 && Date.now() / 1000 - item.receiver_status_observed_at <= 10) : Array.isArray(connectivity?.connected) ? connectivity.connected : null;
    $('#receiverSummary').textContent = known && connectedReceivers ? `Receivers: ${connectedReceivers.length}/5 connected` : 'Receivers: status unavailable';
    $('#receiverConnectivity').textContent = known ? JSON.stringify(connectivity || {}) : 'Unknown';
    $('#wallActivationFailure').hidden = !state.wall.activationError;
    $('#wallActionMessage').textContent = state.wall.activationError || '';
    $('#retryWallAction').textContent = state.wall.actionKind === 'stop' ? 'Retry stop' : 'Retry once';
    $('#retryWallAction').disabled = pending;
    $('#operationMessage').textContent = state.authoredValidationError || state.wall.activationError || status.last_error || (!known ? 'Playback is unknown.' : status.running ? 'Valid edits apply automatically.' : 'Output stopped. Resume sends the edited scene.');
  }
  function updateHistoryActions() {
    $('#undoScene').disabled = state.history.length === 0;
    $('#redoScene').disabled = state.redo.length === 0;
  }
  function clearHistory() {
    state.history = []; state.redo = []; controlEditSessions = new WeakMap(); historyGeneration += 1; updateHistoryActions();
  }
  async function acknowledgeUndo(revision) {
    clearHistory();
    const result = await requestJson(`${api}/undo-ack`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({client_id: clientId, revision})});
    renderStatus(result.status || result);
  }
  function schedulePreview() { const generation = ++state.previewGeneration; const candidate = state.scene || defaultScene(); previewScheduler.submitAuthored(candidate, {generation}).catch((error) => { if (generation === state.previewGeneration) $('#previewStatus').textContent = error.message; }); }
  function historySnapshot(scene = state.scene || defaultScene(), targetFps = authoredTargetFps ?? boundedTargetFps($('#targetFps').value)) {
    return {scene: structuredClone(scene), targetFps: boundedTargetFps(targetFps)};
  }
  function normalizeHistoryEntry(entry) {
    return entry?.scene && Number.isFinite(entry.targetFps) ? historySnapshot(entry.scene, entry.targetFps) : historySnapshot(entry);
  }
  function remember(previous) { state.history.push(normalizeHistoryEntry(previous)); if (state.history.length > 40) state.history.shift(); state.redo = []; updateHistoryActions(); }
  let sceneSaveBarrier = null;
  async function flushPublication() {
    state.publication.scheduled = false;
    // Save also writes recovery. Later authored edits must reach the server
    // after that snapshot, so a slow save cannot erase their recoverable draft.
    if (sceneSaveBarrier && state.publication.queued?.kind !== 'stop') await sceneSaveBarrier;
    if (state.publication.inFlight || !state.publication.queued) return;
    const entry = state.publication.queued;
    state.publication.queued = null;
    state.publication.inFlight = entry;
    let finishAuthored;
    entry.authoredSettled = new Promise((resolve) => { finishAuthored = resolve; });
    state.submitting = true;
    try {
      if (entry.kind === 'stop') {
        await stopOutputNow(entry.scene);
        entry.resolve({stopped: true});
        return;
      }
      const response = await fetch(`${api}${entry.endpoint}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(entry.body)});
      const result = await response.json();
      state.authoredValidationError = response.ok ? null : (result.error || 'Current scene could not be accepted.');
      if (!response.ok) throw Object.assign(new Error(state.authoredValidationError), {result, status: response.status});
      finishAuthored();
      state.composerInteractionBasis = (result.status || result).current?.digest || null;
      // A result always carries the server's authoritative desired/observed
      // snapshot.  Do not infer output state from an old local draft.
      renderStatus(result.status || result);
      state.wall.activating = true; $('#liveAction').disabled = true;
      try { await guardedWallActivation(entry.scene, true, entry.targetFps); }
      catch (wallError) {
        // The authored scene remains current and recoverable. A later edit
        // retries from a fresh controller revision without rolling the UI back.
        if (intentIsCurrent(entry.intent)) {
          state.wall.activationError = wallError.message;
          // A transport outage may recover without another edit. Terminal
          // acknowledgement failures require a new edit or an explicit retry.
          state.wall.retryBlocked = wallError.code !== 'offline';
          renderStatus(wallStatus());
        }
      } finally {
        state.wall.activating = false;
        renderStatus(state.status || wallStatus());
      }
      entry.resolve(result);
    } catch (error) {
      // Validation failures leave the last valid output untouched. The next
      // valid edit is always eligible to publish.
      entry.reject(error);
    } finally {
      state.publication.inFlight = null;
      state.submitting = false;
      if (!state.publication.queued && state.publication.afterStop) {
        state.publication.queued = state.publication.afterStop;
        state.publication.afterStop = null;
      }
      if (state.publication.queued && !state.publication.scheduled) {
        state.publication.scheduled = true;
        queueMicrotask(flushPublication);
      }
      finishAuthored();
      // Release the visible retry immediately after the publication settles.
      // Newer queued work still keeps it disabled through renderStatus.
      renderStatus(state.status || wallStatus());
    }
  }
  function submit(scene, {builtin = false, endpoint = null, requestBody = null, intentToken = null, rememberEdit = false, previous = null, automatic = false} = {}) {
    if (intentToken != null && !intentIsCurrent(intentToken)) return Promise.resolve({coalesced: true});
    const publicationIntent = intentToken == null ? beginIntent() : intentToken;
    if (!automatic) state.wall.retryBlocked = false;
    if (rememberEdit) remember(previous || structuredClone(state.scene || defaultScene()));
    clearSaveFeedback();
    state.scene = scene; state.wall.dirty = true; syncComponentEditors(); schedulePreview(); state.submitting = true;
    const sceneSnapshot = structuredClone(scene);
    const targetFps = boundedTargetFps($('#targetFps').value);
    authoredTargetFps = targetFps;
    const requestEndpoint = endpoint || (builtin ? '/built-ins/open' : '/scene');
    const body = requestBody || (builtin ? {scene: sceneSnapshot, client_id: clientId, mutation_id: newUuid(), client_sequence: ++state.sequence} : {origin: 'composer', scene: sceneSnapshot, client_id: clientId, mutation_id: newUuid(), client_sequence: ++state.sequence});
    return new Promise((resolve, reject) => {
      const entry = {kind: 'publish', endpoint: requestEndpoint, body, scene: sceneSnapshot, targetFps, intent: publicationIntent, resolve, reject};
      const stopping = state.publication.queued?.kind === 'stop' || state.publication.inFlight?.kind === 'stop';
      const replacement = stopping ? state.publication.afterStop : state.publication.queued;
      if (stopping) state.publication.afterStop = entry;
      else state.publication.queued = entry;
      // A drag may produce many intermediate states before the first network
      // turn. Superseded drafts were never sent, so they are successful local
      // coalesces rather than errors that could roll the UI back.
      replacement?.resolve({coalesced: true});
      if (!state.publication.inFlight && !state.publication.scheduled) {
        state.publication.scheduled = true;
        queueMicrotask(flushPublication);
      }
    });
  }
  function retryWallAction() {
    return state.wall.actionKind === 'stop' ? stopOutput() : retryWallActivation();
  }
  function retryWallActivation() {
    if (!state.scene || state.wall.activating || state.publication.queued || state.publication.afterStop || state.publication.inFlight) return Promise.resolve({coalesced: true});
    return submit(structuredClone(state.scene)).catch((error) => { $('#operationMessage').textContent = error.message; });
  }
  async function edit(event, priorScene = null, changeScene = null) {
    const control = event?.target || null;
    const previousDraft = historySnapshot();
    const next = sceneFromControls();
    const update = changeScene || parameterEdits.get(control);
    if (update && update(next) === false) return;
    if (changeScene) syncScenePresentation(next);
    const targetFps = boundedTargetFps($('#targetFps').value);
    if (sameLocalParameters(next, state.scene) && targetFps === authoredTargetFps) return;
    const session = control && controlEditSessions.get(control);
    const rememberEdit = !session || !session.remembered;
    const previous = session?.previous || (priorScene ? historySnapshot(priorScene, previousDraft.targetFps) : previousDraft);
    if (session) session.remembered = true;
    const historyBefore = state.history.slice(); const redoBefore = state.redo.slice(); const editHistoryGeneration = historyGeneration;
    const intentToken = beginIntent();
    state.dirty = true;
    try { await submit(next, {intentToken, rememberEdit, previous}); }
    catch (error) {
      if (intentIsCurrent(intentToken) && !state.publication.queued && !state.publication.inFlight) {
        if (historyGeneration === editHistoryGeneration) { state.history = historyBefore; state.redo = redoBefore; }
        if (session && rememberEdit) session.remembered = false;
        state.scene = previousDraft.scene; authoredTargetFps = previousDraft.targetFps; syncTargetFps(previousDraft.targetFps); applyScene(previousDraft.scene); updateHistoryActions();
      }
      $('#operationMessage').textContent = error.message;
    }
  }
  function applyAuthoredPresetPresentation(next, preset) {
    if (preset.installation_effects) next.plants = {...next.plants, effects: preset.installation_effects};
    if (preset.presentation?.look) next.look = {...next.look, ...preset.presentation.look};
    if (Number.isFinite(preset.presentation?.background_gain)) next.background = {...next.background, parameters: {...next.background.parameters, gain: preset.presentation.background_gain}};
  }
  function filteredItems() { const query = state.query.toLocaleLowerCase(); let items = state.library.items.filter((item) => item.name.toLocaleLowerCase().includes(query)); if (state.filter === 'favorites') items = items.filter((item) => state.library.favorites.some((favorite) => favorite.kind === item.kind && favorite.id === item.id)); else if (state.filter !== 'all') items = items.filter((item) => item.kind === state.filter); return items; }
  async function triggerInstrumentAtPointer(event) {
    const entry = componentDescriptor(state.scene?.animation);
    const positioned = positionedAction(entry);
    if (event.button !== 0 || event.isPrimary === false || !positioned) return;
    const [kind, action] = positioned;
    const status = state.status;
    if (!status?.running || !status?.armed || !status?.current || state.submitting) return;
    const activeRef = state.wall.observation?.scene?.animation
      || state.wall.observation?.active_identity?.component_identities?.find((component) => component.slot_id === 'animation');
    if (activeRef && activeRef.component_id !== entry.component_id) return;
    const canvas = $('#scenePreview');
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const x = Math.min(canvas.width - .001, Math.max(0, (event.clientX - rect.left) * canvas.width / rect.width));
    const y = Math.min(canvas.height - .001, Math.max(0, (1 - (event.clientY - rect.top) / rect.height) * canvas.height));
    const basis = state.composerInteractionBasis;
    const scene_digest = activeRef?.component_id === entry.component_id && /^[a-f0-9]{64}$/.test(basis) ? basis : null;
    try {
      const body = await requestJson('/api/interaction', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({kind, x, y, strength: 1, component_id: entry.component_id, ...(scene_digest ? {scene_digest} : {})}),
      });
      if (body.accepted !== true) throw new Error(action.rejected || `${entry.name} did not accept the interaction.`);
      $('#previewStatus').textContent = action.accepted || `${entry.name} accepted the interaction.`;
    } catch (error) { $('#operationMessage').textContent = error.message; }
  }
  function renderLibrary() { const target = $('#libraryList'); target.replaceChildren(); const items = filteredItems(); $('#libraryEmpty').hidden = items.length > 0; items.forEach((item) => { const button = document.createElement('button'); button.type = 'button'; button.className = 'button'; button.setAttribute('aria-current', String(state.selection?.kind === item.kind && state.selection?.id === item.id)); button.innerHTML = `<span>${item.name}</span><span class="library-kind">${item.kind === 'starter' ? 'Built-in' : 'Saved'}</span>`; button.addEventListener('click', () => openItem(item)); const row = document.createElement('li'); row.append(button); target.append(row); }); document.querySelectorAll('[data-library-filter]').forEach((button) => button.classList.toggle('active', button.dataset.libraryFilter === state.filter)); }
  async function openItem(item) {
    const intentToken = beginIntent();
    clearSaveFeedback();
    try {
      // Allocate ordering when the user chooses a Library item, before its
      // lookup completes. A slow earlier lookup can then never beat a newer
      // choice or control edit at the server.
      const selectionMutation = newUuid(); const selectionSequence = ++state.sequence;
      let publication;
      if (item.kind === 'look') {
        const response = await fetch(`${api}/looks/${item.id}`); const body = await response.json(); if (!response.ok) throw new Error(body.error);
        if (!intentIsCurrent(intentToken)) return;
        publication = await submit(body.look.scene, {
          endpoint: `/looks/${item.id}/open`,
          requestBody: {client_id: clientId, mutation_id: selectionMutation, client_sequence: selectionSequence}, intentToken,
        });
        if (!intentIsCurrent(intentToken) || publication.coalesced) return;
        state.scene = publication.look.scene; renderStatus(publication.status); $('#sceneName').value = publication.look.name;
      }
      else { const starterResponse = await fetch(`${api}/starters/${item.id}`); const starter = (await starterResponse.json()).starter; if (!intentIsCurrent(intentToken)) return; applyScene(starter.scene); publication = await submit(starter.scene, {builtin: true, requestBody: {scene: starter.scene, client_id: clientId, mutation_id: selectionMutation, client_sequence: selectionSequence}, intentToken}); if (!intentIsCurrent(intentToken) || publication.coalesced) return; $('#sceneName').value = starter.name; }
      if (!intentIsCurrent(intentToken)) return;
      state.selection = item; clearHistory(); state.dirty = false; applyScene(state.scene); schedulePreview(); renderLibrary(); renderStatus(state.status);
    } catch (error) { if (intentIsCurrent(intentToken) && error.status !== 409) $('#operationMessage').textContent = error.message || 'Scene could not be opened.'; }
  }
  function focusable(dialog) { return [...dialog.querySelectorAll('button,[href],input,select,textarea,[tabindex]:not([tabindex="-1"])')].filter((node) => !node.disabled); }
  function openDialog(dialog) { const prior = document.activeElement; dialog.showModal(); focusable(dialog)[0]?.focus(); const trap = (event) => { if (event.key === 'Escape') { event.preventDefault(); dialog.close(); } if (event.key !== 'Tab') return; const nodes = focusable(dialog); const first = nodes[0]; const last = nodes.at(-1); if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); } }; dialog.addEventListener('keydown', trap); dialog.addEventListener('close', () => { dialog.removeEventListener('keydown', trap); prior?.focus(); }, {once: true}); }
  function blockers(result) { const list = $('#readinessList'); list.replaceChildren(); (result.blockers || [{message: result.error || 'The live update is not ready.', recovery: 'Review the connection and current scene.'}]).forEach((blocker) => { const item = document.createElement('li'); item.textContent = `${blocker.message} ${blocker.recovery || ''}`; list.append(item); }); openDialog($('#readinessDialog')); }


  async function guardedWallActivation(scene, power, targetFps = boundedTargetFps($('#targetFps').value)) {
    state.wall.actionKind = 'scene';
    await refreshWallStatus({preserveAuthored: true});
    if (state.wall.bootstrap?.capabilities?.server_actions?.activation_available !== true) {
      state.wall.dirty = false;
      return {local_only: true};
    }
    const accepted = await requestJson('/api/v1/scene', {
      method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({scene: structuredClone(scene), global_settings: globalSettingsForWall(scene, power, targetFps)}),
    });
    state.wall.requestId = accepted.request_id || null;
    state.wall.requested = structuredClone(scene);
    state.wall.activationError = null;
    state.wall.dirty = Boolean(state.publication.queued || state.publication.afterStop);
    await refreshWallStatus({preserveAuthored: true});
    return accepted;
  }
  async function waitForStopCommand(requestId, {signal, deadline}) {
    if (!requestId) throw new Error('The controller did not identify the Stop request.');
    while (Date.now() < deadline && !signal.aborted) {
      const observation = await requestJson(`/api/v1/composer/settings/observed?request_id=${encodeURIComponent(requestId)}`, {signal});
      if (signal.aborted) throw new Error('Stop timed out; refresh to check output.');
      state.wall.observation = observation;
      const result = observation.command_result;
      if (result?.request_id === requestId && result.state === 'failed') {
        throw new Error(result.error || 'The controller could not stop output.');
      }
      if (result?.request_id === requestId && result.state === 'completed'
          && observation.freshness === 'fresh' && observation.is_running === false) return;
      await sleep(Math.min(200, Math.max(0, deadline - Date.now())));
    }
    throw new Error('Stop timed out; refresh to check output.');
  }
  async function stopOutputNow(scene) {
    const controller = new AbortController();
    const deadline = Date.now() + 10000;
    const timeout = window.setTimeout(() => controller.abort(), 10000);
    const signal = controller.signal;
    state.wall.actionKind = 'stop';
    state.wall.activating = true; $('#liveAction').disabled = true; $('#liveAction').textContent = 'Stopping output…';
    $('#operationMessage').textContent = 'Stopping output. The next valid edit resumes automatically.';
    try {
      if (!state.wall.bootstrap) state.wall.bootstrap = await requestJson('/api/v1/composer/bootstrap', {signal});
      if (state.wall.bootstrap?.capabilities?.server_actions?.activation_available === true) {
        const accepted = await requestJson('/api/v1/scene', {method: 'DELETE', signal});
        if (signal.aborted) throw new Error('Stop timed out; refresh to check output.');
        state.wall.requestId = accepted.request_id;
        await waitForStopCommand(accepted.request_id, {signal, deadline});
      }
      const result = await requestJson(`${api}/stop`, {
        method: 'POST', signal, headers: {'Content-Type': 'application/json'}, body: JSON.stringify({client_id: clientId}),
      });
      if (signal.aborted) throw new Error('Stop timed out; refresh to check output.');
      state.wall.activationError = null;
      renderStatus(result.status || result);
    } catch (error) {
      const message = signal.aborted ? 'Stop timed out; refresh to check output.' : error.message;
      state.wall.activationError = message;
      renderStatus(wallStatus(message)); blockers({error: message, blockers: error.blockers});
    }
    finally { window.clearTimeout(timeout); state.wall.activating = false; renderStatus(state.status || wallStatus()); }
  }
  function stopOutput() {
    beginIntent();
    if (!state.scene) return Promise.resolve();
    const superseded = state.publication.afterStop;
    state.publication.afterStop = null;
    superseded?.resolve({coalesced: true});
    if (state.publication.queued?.kind === 'stop' || state.publication.inFlight?.kind === 'stop') return Promise.resolve();
    return new Promise((resolve, reject) => {
      const replacement = state.publication.queued;
      state.publication.queued = {kind: 'stop', scene: structuredClone(state.scene), resolve, reject};
      replacement?.resolve({coalesced: true});
      if (!state.publication.inFlight && !state.publication.scheduled) {
        state.publication.scheduled = true;
        queueMicrotask(flushPublication);
      }
    });
  }
  async function check() { try { const response = await fetch(`${api}/check`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({origin: 'composer', scene: sceneFromControls()})}); const result = await response.json(); $('#checkMessage').textContent = response.ok ? 'This is advisory; it does not change output.' : (result.error || 'Check could not complete.'); const details = $('#checkDetails'); details.replaceChildren(); [['Scene identity', identity(result.basis)], ['Connection', result.status?.connected ? 'Connected' : 'Disconnected'], ['Publication', result.status?.connected ? 'Every valid edit applies automatically' : 'Reconnect and make a new edit to request playback']].forEach(([term, description]) => { const entry = document.createElement('div'); entry.innerHTML = `<dt>${term}</dt><dd>${description}</dd>`; details.append(entry); }); if (result.status) renderStatus(result); openDialog($('#checkDialog')); } catch (error) { $('#operationMessage').textContent = error.message; } }
  function saveFeedback(message, stateName) {
    const feedback = $('#saveFeedback');
    feedback.textContent = message; feedback.dataset.state = stateName; feedback.hidden = false;
  }
  function focusSceneName() {
    const secondary = $('#secondaryOperations'); if (secondary) secondary.open = true;
    $('#sceneName').focus({preventScroll: true});
  }
  function clearSaveFeedback() {
    const feedback = $('#saveFeedback');
    if (feedback) { feedback.hidden = true; feedback.textContent = ''; delete feedback.dataset.state; }
  }
  let sceneSaving = false;
  function uniqueCopyName() {
    const source = $('#sceneName').value.trim() || state.selection?.name || 'Untitled scene';
    const names = new Set(state.library.items.filter((item) => item.kind === 'look').map((item) => item.name.toLocaleLowerCase()));
    for (let index = 1; ; index++) {
      const suffix = index === 1 ? ' copy' : ` copy ${index}`;
      const name = `${source.slice(0, 80 - suffix.length)}${suffix}`;
      if (!names.has(name.toLocaleLowerCase())) return name;
    }
  }
  function showCopyDialog() {
    if (sceneSaving || $('#sceneCopyDialog').open) return;
    $('#sceneCopyName').value = uniqueCopyName(); $('#sceneCopyError').textContent = '';
    openDialog($('#sceneCopyDialog')); $('#sceneCopyName').select();
  }
  async function save(as, copyName = null) {
    if (sceneSaving) return false;
    if (as && copyName == null) { showCopyDialog(); return false; }
    const name = (copyName ?? $('#sceneName').value).trim();
    const creatingLook = as || state.selection?.kind !== 'look';
    const fail = (message) => {
      if (as) $('#sceneCopyError').textContent = message;
      else saveFeedback(message, 'error');
    };
    if (creatingLook && !name) {
      fail('Enter a scene name before saving.');
      if (!as) focusSceneName();
      return false;
    }
    const scene = sceneFromControls();
    const authoredName = $('#sceneName').value;
    const intent = state.intent;
    const selection = state.selection;
    sceneSaving = true;
    $('#saveScene').disabled = true; $('#saveAsScene').disabled = true;
    $('#sceneCopySubmit').disabled = true;
    let releaseSave;
    sceneSaveBarrier = new Promise((resolve) => { releaseSave = resolve; });
    try {
      await state.publication.inFlight?.authoredSettled;
      const result = await requestJson(creatingLook ? `${api}/looks` : `${api}/looks/${selection.id}`, {
        method: creatingLook ? 'POST' : 'PUT', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(creatingLook ? {name, scene} : {scene}),
      });
      sceneSaveBarrier = null; releaseSave();
      // A save persists its captured revision. A later edit/selection must never
      // inherit that revision's clean badge or replace its current scene/name.
      const current = state.intent === intent && state.selection === selection && $('#sceneName').value === authoredName && sameLocalParameters(scene, sceneFromControls());
      if (current) {
        state.selection = {kind: 'look', id: result.look.id, name: result.look.name};
        $('#sceneName').value = result.look.name; state.dirty = false;
        $('#saveState').textContent = 'Saved scene';
        saveFeedback(`Saved ${result.look.name} · current revision.`, 'success');
      } else if (state.selection === selection) {
        state.dirty = true;
        saveFeedback(`Saved ${result.look.name} · earlier revision. Current edits are unsaved.`, 'dirty');
      }
      if (as) $('#sceneCopyDialog').close();
      // A failed refresh does not undo a successful save or invite duplicate copies.
      try { await loadLibrary(); } catch (_) { if (current && state.intent === intent) saveFeedback(`Saved ${result.look.name}. Scene list could not refresh; reopen Saved to retry.`, 'success'); }
      return true;
    } catch (error) {
      const message = /already has that name/i.test(error.message) ? 'A scene already has that name. Choose a different name.' : (error.message || 'This scene could not be saved. Try again.');
      if (state.intent === intent && state.selection === selection) fail(message);
      return false;
    } finally {
      sceneSaveBarrier = null; releaseSave();
      sceneSaving = false; $('#saveScene').disabled = false; $('#saveAsScene').disabled = false; $('#sceneCopySubmit').disabled = false;
    }
  }
  async function restoreSavedSelection() {
    if (state.selection?.kind !== 'look') return;
    const selection = state.selection; const intent = state.intent;
    try {
      const {look} = await requestJson(`${api}/looks/${selection.id}`);
      if (selection !== state.selection || intent !== state.intent) return;
      state.selection = {...selection, name: look.name}; $('#sceneName').value = look.name;
      state.dirty = !sameLocalParameters(state.scene, look.scene);
    } catch (error) { if (selection === state.selection && intent === state.intent) $('#operationMessage').textContent = error.message; }
  }
  async function rewind(direction) {
    const source = direction === 'undo' ? state.history : state.redo;
    const entry = source.pop();
    if (!entry) { updateHistoryActions(); return; }
    const next = normalizeHistoryEntry(entry);
    const opposite = direction === 'undo' ? state.redo : state.history;
    opposite.push(historySnapshot()); updateHistoryActions(); state.dirty = true;
    authoredTargetFps = next.targetFps; syncTargetFps(next.targetFps); applyScene(next.scene);
    try { await submit(next.scene); } catch (error) { $('#operationMessage').textContent = error.message; }
  }
  function isNativeTextEditingTarget(target) { return Boolean(target?.closest?.('input, textarea, [contenteditable]:not([contenteditable="false"])')); }
  function handleSceneHistoryShortcut(event) {
    if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== 'z' || isNativeTextEditingTarget(event.target)) return;
    event.preventDefault(); rewind(event.shiftKey ? 'redo' : 'undo');
  }
  async function loadLibrary() { state.library = await requestJson(`${api}/library`); renderLibrary(); }
  function browseScenes() {
    window.ComposerPanelLayout.expand('scenes');
    const search = $('#librarySearch');
    search.scrollIntoView({behavior: 'smooth', block: 'center'});
    search.focus({preventScroll: true});
  }
  function formatPlaylistDuration(seconds) {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    const minutes = Math.floor(total / 60);
    return `${minutes}:${String(total % 60).padStart(2, '0')}`;
  }
  function playlistDefinition() {
    return {name: $('#playlistName').value.trim(), entries: state.playlist.entries.map((entry) => structuredClone(entry))};
  }
  function playlistDirty() {
    return JSON.stringify(playlistDefinition()) !== (state.playlist.baseline || JSON.stringify({name: '', entries: []}));
  }
  function renderPlaylistDraftStatus() {
    $('#playlistDraftStatus').textContent = playlistDirty() ? 'Unsaved playlist changes' : state.playlist.id ? 'Playlist saved' : 'New playlist';
  }
  function playlistEntryIdentity(entry) {
    const animation = entry.scene?.animation || entry.scene?.background;
    const componentId = animation?.component_id;
    const catalog = state.gallery.entries.find((item) => item.component_id === componentId);
    if (catalog?.preset_count > 0 && !Object.hasOwn(state.componentPresets, componentId)) void loadComponentPresets(catalog).catch(() => {});
    const preset = (state.componentPresets[componentId] || []).find((item) => sameLocalParameters(item.parameters, animation?.parameters));
    const name = catalog?.name || componentId || 'Unknown animation';
    return {catalog, preset, text: `${name} · ${preset ? preset.name : state.componentPresets[componentId] ? 'Custom settings' : 'Preset details unavailable'}`};
  }
  function refreshPlaylistEntryIdentity(item, entry, index) {
    const details = playlistEntryIdentity(entry);
    const description = item.querySelector('.playlist-entry-description');
    description.replaceChildren();
    const title = document.createElement('strong'); title.textContent = entry.label;
    const identity = document.createElement('small'); identity.textContent = details.text;
    if (details.catalog) description.append(galleryThumbnail(details.catalog, details.preset));
    else { const fallback = document.createElement('span'); fallback.className = 'gallery-thumb-unavailable'; fallback.textContent = 'Preview unavailable'; fallback.setAttribute('aria-label', `${details.text} preview unavailable`); description.append(fallback); }
    const text = document.createElement('div'); text.append(title, identity); description.append(text);
    const accessibleLabel = `${index + 1}. ${entry.label} · ${details.text}`;
    item.querySelector('input').setAttribute('aria-label', `${accessibleLabel} duration in seconds`);
    item.querySelectorAll('[data-playlist-action]').forEach(button => button.setAttribute('aria-label', `${button.dataset.playlistAction} ${accessibleLabel}`));
  }
  function refreshPlaylistIdentities() {
    // Metadata may arrive while a duration field is being edited. Update only
    // descriptions and accessible names, retaining controls and their focus.
    document.querySelectorAll('#playlistEntries > .playlist-entry').forEach((item, index) => refreshPlaylistEntryIdentity(item, state.playlist.entries[index], index));
  }
  function renderPlaylist() {
    const busy = state.playlist.saving || state.playlist.switching || state.playlist.adding || Boolean(state.playlist.retryStart);
    const list = $('#playlistEntries'); list.replaceChildren();
    const total = state.playlist.entries.reduce((sum, entry) => sum + Number(entry.duration_seconds || 0), 0);
    $('#playlistTotal').textContent = formatPlaylistDuration(total);
    $('#playlistEmpty').hidden = state.playlist.entries.length > 0;
    state.playlist.entries.forEach((entry, index) => {
      const item = document.createElement('li'); item.className = 'playlist-entry';
      const description = document.createElement('div'); description.className = 'playlist-entry-description';
      const durationLabel = document.createElement('label'); durationLabel.append('Seconds ');
      const duration = document.createElement('input'); duration.type = 'number'; duration.min = '1'; duration.max = '86400'; duration.step = '1'; duration.value = String(entry.duration_seconds); duration.disabled = busy;
      duration.addEventListener('input', () => { entry.duration_seconds = duration.value === '' ? '' : Number(duration.value); renderPlaylistDraftStatus(); });
      duration.addEventListener('change', () => { entry.duration_seconds = Math.max(1, Math.min(86400, Math.round(Number(duration.value) || 60))); renderPlaylist(); });
      durationLabel.append(duration);
      const actions = document.createElement('div'); actions.className = 'playlist-entry-actions';
      [['↑', -1, 'Move up'], ['↓', 1, 'Move down']].forEach(([text, offset, label]) => {
        const button = document.createElement('button'); button.type = 'button'; button.className = 'button text'; button.textContent = text; button.title = label; button.dataset.playlistAction = label; button.disabled = busy || index + offset < 0 || index + offset >= state.playlist.entries.length;
        button.addEventListener('click', () => { const [moved] = state.playlist.entries.splice(index, 1); state.playlist.entries.splice(index + offset, 0, moved); renderPlaylist(); }); actions.append(button);
      });
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'button text'; remove.textContent = 'Remove'; remove.disabled = busy; remove.dataset.playlistAction = 'Remove'; remove.addEventListener('click', () => { state.playlist.entries.splice(index, 1); renderPlaylist(); }); actions.append(remove);
      item.append(description, durationLabel, actions); list.append(item); refreshPlaylistEntryIdentity(item, entry, index);
    });
    $('#playlistName').disabled = busy;
    $('#playlistChoice').disabled = busy;
    $('#playlistAdd').disabled = busy;
    $('#playlistBrowse').disabled = busy;
    $('#playlistSave').disabled = busy;
    $('#playlistStart').disabled = busy || Boolean(state.playlist.startIntent) || state.playlist.entries.length === 0;
    renderPlaylistDraftStatus(); renderPlaylistActionFailure();
  }
  async function loadPlaylists() {
    const body = await requestJson(`${api}/playlists`); state.playlist.saved = body.playlists || [];
    const choice = $('#playlistChoice'); const selected = state.playlist.id || '';
    choice.replaceChildren(new Option('New playlist', ''));
    state.playlist.saved.forEach((item) => choice.append(new Option(`${item.name} · ${formatPlaylistDuration(item.total_duration_seconds)}`, item.id)));
    choice.value = state.playlist.saved.some((item) => item.id === selected) ? selected : '';
  }
  function confirmPlaylistChange() {
    const dialog = $('#playlistUnsavedDialog');
    dialog.returnValue = 'keep';
    return new Promise((resolve) => {
      dialog.addEventListener('close', () => resolve(dialog.returnValue), {once: true});
      dialog.showModal();
    });
  }
  async function openPlaylist(playlistId) {
    $('#playlistChoice').value = state.playlist.id || '';
    if (state.playlist.saving || state.playlist.switching || state.playlist.adding || state.playlist.retryStart) return;
    state.playlist.switching = true; renderPlaylist();
    try {
      if (playlistDirty()) {
        const action = await confirmPlaylistChange();
        if (action !== 'discard' && action !== 'save') return;
        if (action === 'save') await savePlaylist();
      }
      // Fetch before replacing the draft; failed loads preserve all edits.
      const record = playlistId ? (await requestJson(`${api}/playlists/${encodeURIComponent(playlistId)}`)).playlist : {id: null, name: '', entries: []};
      state.playlist.id = record.id; state.playlist.entries = structuredClone(record.entries); $('#playlistName').value = record.name;
      state.playlist.baseline = JSON.stringify(playlistDefinition());
      $('#playlistChoice').value = record.id || ''; $('#playlistDraftError').textContent = '';
    } finally { state.playlist.switching = false; renderPlaylist(); }
  }
  async function savePlaylist() {
    if (state.playlist.saving) throw new Error('Playlist save is already in progress.');
    state.playlist.saving = true; renderPlaylist();
    try {
      const definition = playlistDefinition();
      const body = await requestJson(state.playlist.id ? `${api}/playlists/${encodeURIComponent(state.playlist.id)}` : `${api}/playlists`, {
        method: state.playlist.id ? 'PUT' : 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(definition),
      });
      state.playlist.id = body.playlist.id; state.playlist.entries = structuredClone(body.playlist.entries); $('#playlistName').value = body.playlist.name;
      state.playlist.baseline = JSON.stringify(playlistDefinition()); $('#playlistDraftError').textContent = '';
      await loadPlaylists(); $('#playlistStatus').textContent = `Saved ${body.playlist.name}.`; $('#playlistStatus').dataset.state = 'saved'; return body.playlist;
    } finally {
      state.playlist.saving = false; renderPlaylist();
    }
  }
  let playlistPicker = null;
  function renderPlaylistPicker() {
    const picker = playlistPicker; if (!picker) return;
    const query = $('#playlistPickerSearch').value.trim().toLocaleLowerCase();
    const list = $('#playlistPickerList'); list.replaceChildren();
    picker.items.filter(item => item.name.toLocaleLowerCase().includes(query)).forEach(item => {
      const row = document.createElement('label'); row.className = 'playlist-picker-choice';
      const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.checked = picker.selected.has(item.key); checkbox.disabled = picker.adding;
      checkbox.addEventListener('change', () => { if (checkbox.checked) picker.selected.add(item.key); else picker.selected.delete(item.key); updatePlaylistPickerCount(); });
      const text = document.createElement('span'); const title = document.createElement('strong'); title.textContent = item.name;
      const kind = document.createElement('small'); kind.textContent = item.kind === 'look' ? 'Saved scene' : item.kind === 'starter' ? 'Built-in scene' : 'Built-in animation · factory settings';
      text.append(title, kind); row.append(checkbox, text); list.append(row);
    });
    $('#playlistPickerEmpty').hidden = list.childElementCount > 0;
    updatePlaylistPickerCount();
  }
  function updatePlaylistPickerCount() {
    const picker = playlistPicker; if (!picker) return;
    $('#playlistPickerAdd').textContent = picker.adding ? 'Adding…' : `Add selected (${picker.selected.size})`;
    $('#playlistPickerAdd').disabled = picker.adding || !picker.selected.size;
  }
  async function openPlaylistPicker() {
    if (playlistPicker || state.playlist.saving || state.playlist.switching || state.playlist.adding || state.playlist.retryStart) return;
    const pane = $('.playlist-pane');
    const picker = {items: [], selected: new Set(), adding: false, scrollTop: pane.scrollTop}; playlistPicker = picker;
    const dialog = $('#playlistPickerDialog');
    $('#playlistPickerSearch').value = ''; $('#playlistPickerMessage').textContent = 'Loading scenes…';
    renderPlaylistPicker();
    dialog.addEventListener('close', () => { if (playlistPicker === picker) playlistPicker = null; pane.scrollTop = picker.scrollTop; }, {once: true});
    openDialog(dialog);
    try {
      const library = await requestJson(`${api}/library`, {signal: AbortSignal.timeout(10000)});
      if (playlistPicker !== picker) return;
      picker.items = [
        ...(library.items || []).filter(item => ['starter', 'look'].includes(item.kind)).map(item => ({...item, key: `${item.kind}:${item.id}`})),
        ...state.gallery.entries.filter(entry => entry.available).map(entry => ({kind: 'animation', id: entry.component_id, name: entry.name, key: `animation:${entry.key}`, entry})),
      ];
      $('#playlistPickerMessage').textContent = 'Selections are added to this draft. Playback stays unchanged.'; renderPlaylistPicker();
    } catch (error) { if (playlistPicker === picker) $('#playlistPickerMessage').textContent = `${error.message} Close and reopen to retry.`; }
  }
  async function addPickedPlaylistScenes() {
    const picker = playlistPicker; if (!picker || picker.adding || !picker.selected.size) return;
    picker.adding = true; renderPlaylistPicker();
    try {
      const entries = [];
      for (const item of picker.items.filter(item => picker.selected.has(item.key))) {
        let scene;
        if (item.kind === 'animation') {
          scene = defaultScene();
          scene.background.parameters.gain = .62;
          scene.look = {palette_id: 'mist', pace: .7, presentation_brightness: .82};
          scene.animation = {component_id: item.entry.component_id, version: item.entry.version, provider: item.entry.provider, role: 'animation', parameters: structuredClone(item.entry.parameters)};
        } else {
          const body = await requestJson(`${api}/${item.kind === 'look' ? 'looks' : 'starters'}/${encodeURIComponent(item.id)}`, {signal: AbortSignal.timeout(10000)});
          scene = (item.kind === 'look' ? body.look : body.starter)?.scene;
          if (!scene) throw new Error(`${item.name} is unavailable. No entries were added.`);
        }
        if (playlistPicker !== picker || !$('#playlistPickerDialog').open) return;
        entries.push({entry_id: newUuid(), label: item.name, duration_seconds: 60, scene: browserSceneForWall(scene)});
      }
      // Publish only to the playlist draft after every read succeeds. Cancellation
      // or a failed lookup leaves both the existing draft and live scene untouched.
      state.playlist.entries.push(...entries); renderPlaylist();
      $('#playlistDraftError').textContent = '';
      $('#playlistStatus').textContent = `Added ${entries.length}: ${entries.map(entry => entry.label).join(', ')}. Playback unchanged.`;
      delete $('#playlistStatus').dataset.state;
      $('#playlistPickerDialog').close();
    } catch (error) { if (playlistPicker === picker) $('#playlistPickerMessage').textContent = `${error.message} No entries were added; retry or cancel.`; }
    finally { if (playlistPicker === picker) { picker.adding = false; renderPlaylistPicker(); } }
  }
  function currentPlaylistEntryLabel() {
    const selected = state.library.items?.find((item) => item.kind === state.selection?.kind && item.id === state.selection?.id);
    if (selected?.name) return selected.name;
    const named = $('#sceneName').value.trim(); if (named) return named;
    return $('#animationChoice').selectedOptions[0]?.textContent || 'Current Scene';
  }
  async function addCurrentSceneToPlaylist() {
    const button = $('#playlistAdd');
    if (state.playlist.saving || state.playlist.switching || state.playlist.adding || state.playlist.retryStart) { $('#playlistStatus').textContent = 'Wait for the playlist save or operation to finish before editing.'; return; }
    try {
      state.playlist.adding = true; renderPlaylist();
      const selectedScene = structuredClone(sceneFromControls());
      const selectedLabel = currentPlaylistEntryLabel();
      button.disabled = true; $('#playlistStatus').textContent = 'Adding the selected Scene…'; $('#playlistStatus').dataset.state = 'running';
      if (!state.wall.bootstrap || !state.wall.observation) await refreshWallStatus({preserveAuthored: true});
      state.playlist.entries.push({entry_id: newUuid(), label: selectedLabel, duration_seconds: 60, scene: browserSceneForWall(selectedScene)});
      renderPlaylist(); $('#playlistStatus').textContent = 'Added the current Scene.'; delete $('#playlistStatus').dataset.state;
    } catch (error) { $('#playlistStatus').textContent = error.message; $('#playlistStatus').dataset.state = 'error'; }
    finally { state.playlist.adding = false; renderPlaylist(); }
  }
  function renderPlaylistActionFailure() {
    const error = state.playlist.actionError;
    $('#playlistActionFailure').hidden = !error;
    $('#playlistActionMessage').textContent = error ? `${error.message}${state.playlist.retryStart ? state.playlist.retryStart.cancelled ? ' Start and stop remain unconfirmed. Refresh may locate the run. Unlocking editing abandons automatic recovery; the original request may still run.' : ' Start is unconfirmed. Retry uses the same request.' : error.kind === 'abandoned' ? '' : ' Refresh status or try the action again.'}` : '';
    const retry = $('#playlistRetryAction');
    retry.hidden = Boolean(state.playlist.retryStart?.cancelled) || (!state.playlist.retryStart && error?.kind !== 'stop');
    retry.textContent = state.playlist.retryStart ? 'Retry start' : 'Retry stop';
    const pending = Boolean(state.playlist.startIntent || state.playlist.stopPromise);
    retry.disabled = pending;
    $('#playlistStop').disabled = Boolean(state.playlist.stopPromise);
    $('#playlistRefreshStatus').disabled = pending;
    $('#playlistUnlockEditing').hidden = !state.playlist.retryStart?.cancelled;
    $('#playlistUnlockEditing').disabled = pending;
    $('#playlistDismissError').disabled = pending;
    $('#playlistDismissError').hidden = Boolean(state.playlist.retryStart);
  }
  function abandonPlaylistStart() {
    if (!state.playlist.retryStart?.cancelled || state.playlist.startIntent || state.playlist.stopPromise) return;
    state.playlist.retryStart = null;
    state.playlist.actionError = {kind: 'abandoned', message: 'Automatic start recovery abandoned. The original request may still run; check controller playback and use Stop output if needed.'};
    renderPlaylist();
  }
  function playlistActionFailed(error, kind) {
    state.playlist.actionError = {message: error.message || String(error), kind};
    renderPlaylistActionFailure();
  }
  function newPlaylistStartIntent() {
    let settle;
    const done = new Promise((resolve) => { settle = resolve; });
    return {cancelled: false, dispatched: false, accepted: null, stopPromise: null, done, settle};
  }
  function sendPlaylistStop(runId) {
    if (state.playlist.stopPromise) return state.playlist.stopPromise;
    const pending = (async () => {
      const body = await requestJson(`${api}/playlists/stop`, {method: 'POST', signal: AbortSignal.timeout(10000), headers: {'Content-Type': 'application/json'}, body: JSON.stringify({run_id: runId})});
      state.playlist.requestId = body.accepted.request_id; state.playlist.runId = body.accepted.run_id; state.playlist.statusIntent += 1;
      state.playlist.actionError = null; renderPlaylistActionFailure();
      await refreshPlaylistStatus();
    })();
    state.playlist.stopPromise = pending; renderPlaylistActionFailure();
    return pending.finally(() => {
      if (state.playlist.stopPromise === pending) state.playlist.stopPromise = null;
      renderPlaylistActionFailure();
    });
  }
  function stopAcceptedPlaylist(intent) {
    if (!intent.stopPromise) intent.stopPromise = sendPlaylistStop(intent.accepted.run_id);
    return intent.stopPromise;
  }
  async function startPlaylist(retry = null) {
    if (state.playlist.startIntent) { $('#playlistStatus').textContent = 'Playlist start is already in progress.'; $('#playlistStatus').dataset.state = 'error'; return; }
    if (state.playlist.retryStart && (retry !== state.playlist.retryStart || retry.cancelled)) return;
    const intent = newPlaylistStartIntent();
    state.playlist.startIntent = intent; state.playlist.statusIntent += 1; renderPlaylist();
    try {
      const playlist = retry ? {id: retry.playlistId} : await savePlaylist();
      if (intent.cancelled) { $('#playlistStatus').textContent = 'Playlist start cancelled.'; $('#playlistStatus').dataset.state = 'stopped'; return; }
      const requestId = retry?.requestId || newUuid(); intent.dispatched = true;
      state.playlist.requestId = requestId; state.playlist.statusIntent += 1;
      state.playlist.retryStart = {requestId, playlistId: playlist.id}; renderPlaylist();
      const body = await requestJson(`${api}/playlists/run`, {method: 'POST', signal: AbortSignal.timeout(10000), headers: {'Content-Type': 'application/json', 'Idempotency-Key': requestId}, body: JSON.stringify({playlist_id: playlist.id})});
      state.playlist.retryStart = null; state.playlist.actionError = null;
      intent.accepted = body.accepted; state.playlist.requestId = body.accepted.request_id; state.playlist.runId = body.accepted.run_id; state.playlist.statusIntent += 1;
      if (intent.cancelled) {
        $('#playlistStatus').textContent = 'Stopping playlist…'; $('#playlistStatus').dataset.state = 'running';
        try { await stopAcceptedPlaylist(intent); }
        catch (error) { playlistActionFailed(error, 'stop'); }
        return;
      }
      $('#playlistStatus').textContent = 'Starting playlist…'; $('#playlistStatus').dataset.state = 'running';
      await refreshPlaylistStatus();
    } catch (error) {
      if (!intent.dispatched || intent.accepted || (error.status && error.status < 500)) state.playlist.retryStart = null;
      else if (state.playlist.retryStart) state.playlist.retryStart.cancelled = intent.cancelled;
      playlistActionFailed(error, 'start');
    }
    finally {
      if (state.playlist.startIntent === intent) state.playlist.startIntent = null;
      intent.settle(); renderPlaylist();
    }
  }
  async function stopPlaylist() {
    try {
      const intent = state.playlist.startIntent;
      if (intent) {
        intent.cancelled = true; state.playlist.statusIntent += 1;
        if (!intent.dispatched) { $('#playlistStatus').textContent = 'Playlist start cancelled.'; $('#playlistStatus').dataset.state = 'stopped'; return; }
        if (!intent.accepted) { $('#playlistStatus').textContent = 'Stopping when playlist starts…'; $('#playlistStatus').dataset.state = 'running'; await intent.done; return; }
        await stopAcceptedPlaylist(intent); return;
      }
      if (state.playlist.retryStart) { state.playlist.retryStart.cancelled = true; playlistActionFailed(new Error('Stop is waiting for the original start result.'), 'stop'); return; }
      if (!state.playlist.runId) throw new Error('No pending or running playlist to stop.');
      await sendPlaylistStop(state.playlist.runId);
    } catch (error) { playlistActionFailed(error, 'stop'); }
  }
  async function refreshPlaylistStatus() {
    const requestId = state.playlist.requestId;
    const runId = state.playlist.runId;
    const statusIntent = state.playlist.statusIntent;
    const statusPoll = ++state.playlist.statusPoll;
    try {
      const query = requestId ? `?request_id=${encodeURIComponent(requestId)}` : '';
      const body = await requestJson(`${api}/playlists/status${query}`);
      if (statusPoll !== state.playlist.statusPoll || statusIntent !== state.playlist.statusIntent || requestId !== state.playlist.requestId || runId !== state.playlist.runId) return;
      const requestStatus = body.request; const current = body.current;
      if (state.playlist.retryStart && !state.playlist.startIntent && requestStatus?.request_id === requestId) {
        const cancelled = state.playlist.retryStart.cancelled;
        state.playlist.retryStart = null;
        if (!['rejected','failed'].includes(requestStatus.phase)) state.playlist.actionError = null;
        state.playlist.runId = requestStatus.run_id || state.playlist.runId; renderPlaylist();
        if (cancelled && requestStatus.run_id && ['queued','running'].includes(requestStatus.phase)) {
          try { await sendPlaylistStop(requestStatus.run_id); } catch (error) { playlistActionFailed(error, 'stop'); }
          return;
        }
      }
      if (requestId && !requestStatus && runId && current?.run_id !== runId) {
        $('#playlistStatus').textContent = 'Playlist request pending…'; $('#playlistStatus').dataset.state = 'running'; return;
      }
      const status = requestStatus && ['rejected', 'failed'].includes(requestStatus.phase) ? requestStatus : current;
      if (!status || status.phase === 'idle') { $('#playlistStatus').textContent = 'No playlist running.'; delete $('#playlistStatus').dataset.state; return; }
      if (status.phase === 'running') {
        const remaining = Math.max(0, Math.ceil(Number(status.remaining_seconds) || 0));
        $('#playlistStatus').textContent = `Item ${Number(status.current_index) + 1}/${status.entry_count} · ${status.current_entry?.label || 'Scene'} · ${remaining}s remaining · ${status.next_entry ? `Next: ${status.next_entry.label}` : status.next_entry === null ? 'Final item; output stops afterward' : 'Next entry unavailable'}`;
        $('#playlistStatus').dataset.state = 'running'; state.playlist.runId = status.run_id; return;
      }
      const messages = {completed: 'Playlist complete. Output stopped.', stopped: 'Playlist stopped.', overridden: 'Live editing took over. Playlist ended.', rejected: status.error || 'Playlist start was rejected.', failed: status.error || 'Playlist failed.'};
      if (['rejected','failed'].includes(status.phase)) {
        const key = `${status.request_id || status.run_id}:${status.phase}`;
        if (key !== state.playlist.dismissedStatusError) { state.playlist.statusErrorKey = key; playlistActionFailed(new Error(messages[status.phase]), 'start'); }
        if (status.request_id === requestId) state.playlist.retryStart = null; renderPlaylist();
      }
      $('#playlistStatus').textContent = messages[status.phase] || `Playlist ${status.phase}.`; $('#playlistStatus').dataset.state = ['rejected','failed'].includes(status.phase) ? 'error' : status.phase;
      if (['completed','stopped','overridden','rejected','failed'].includes(status.phase) && status.run_id === state.playlist.runId) state.playlist.runId = null;
    } catch (error) {
      if (statusPoll === state.playlist.statusPoll && statusIntent === state.playlist.statusIntent && requestId === state.playlist.requestId && runId === state.playlist.runId) {
        $('#playlistStatus').textContent = error.message; $('#playlistStatus').dataset.state = 'error';
      }
    }
  }

  function wireLiveRange(control, publish) {
    control.addEventListener('pointerdown', () => beginControlEdit(control));
    control.addEventListener('keydown', () => beginControlEdit(control));
    control.addEventListener('pointerup', () => finishControlEditAfterEvent(control));
    control.addEventListener('pointercancel', () => finishControlEditAfterEvent(control));
    control.addEventListener('keyup', () => finishControlEditAfterEvent(control));
    control.addEventListener('input', publish);
    control.addEventListener('change', (event) => { publish(event); finishControlEditAfterEvent(control); });
  }
  function wire() {
    $('#sceneCopyForm').addEventListener('submit', (event) => { event.preventDefault(); void save(true, $('#sceneCopyName').value); });
    $('#sceneCopyCancel').addEventListener('click', () => $('#sceneCopyDialog').close());
    ['#previewPalette', '#sceneLuminance'].forEach((selector) => $(selector).addEventListener('change', edit));
    sceneComponentRoles.forEach((role) => $(`#${role}Choice`).addEventListener('change', (event) => {
      const entry = selectedComponent(event.target);
      if (entry?.role === role && entry.available !== false) void edit(event, null, (scene) => { scene[role] = componentRef(entry); });
    }));
    $('#addWidgetForm').addEventListener('submit', (event) => {
      event.preventDefault();
      const component = selectedComponent($('#widgetChoice'));
      if (component?.role !== 'widget' || component.available === false) return;
      void edit(null, null, (scene) => {
        scene.widgets.push({
          id: `widget-${newUuid()}`, component: componentRef(component), visible: true,
          placement: structuredClone(component.default_placement?.mode ? component.default_placement : manualPlacement()),
        });
      });
    });
    wireLiveRange($('#sceneSpeed'), (event) => { syncSceneSpeed(number('#sceneSpeed')); edit(event); });
    $('#sceneSpeedExact').addEventListener('change', (event) => {
      const input = event.target;
      if (input.value === '' || !input.validity.valid) {
        input.setAttribute('aria-invalid', 'true');
        return;
      }
      syncSceneSpeed(input.valueAsNumber);
      edit(event);
    });
    $('#sceneSpeedExact').addEventListener('keydown', (event) => {
      if (!['ArrowUp', 'ArrowDown'].includes(event.key)) return;
      event.preventDefault();
      const input = event.target;
      const direction = event.key === 'ArrowUp' ? 1 : -1;
      const step = Number($('#sceneSpeed').step) * direction;
      const typed = input.valueAsNumber;
      const current = input.validity.valid && Number.isFinite(typed) ? typed : scenePace();
      const pace = Number((current + step).toFixed(6));
      syncSceneSpeed(Math.max(Number(input.min), Math.min(Number(input.max), pace)));
      edit(event);
    });
    $('#sceneSpeedExact').addEventListener('blur', (event) => {
      if (event.target.getAttribute('aria-invalid') === 'true') syncSceneSpeed(state.scene?.look.pace ?? DEFAULT_SCENE_PACE);
    });
    $('#freezeSceneSpeed').addEventListener('click', () => { syncSceneSpeed(0); edit(); });
    wireLiveRange($('#targetFps'), (event) => { syncTargetFps($('#targetFps').value); edit(event); });
    $('#resetSceneSpeed').addEventListener('click', () => { syncSceneSpeed(DEFAULT_SCENE_PACE); edit({target: $('#sceneSpeed')}); });
    plantOptics.forEach(wirePlantOptic);
    $('#scenePreview').addEventListener('pointerdown', triggerInstrumentAtPointer);
    $('#librarySearch').addEventListener('input', (event) => { state.query = event.target.value; renderLibrary(); }); document.querySelectorAll('[data-library-filter]').forEach((button) => button.addEventListener('click', () => { state.filter = button.dataset.libraryFilter; renderLibrary(); }));
    $('#gallerySearch').addEventListener('input', (event) => { state.gallery.query = event.target.value; renderGallery(); });
    document.querySelectorAll('[data-gallery-filter]').forEach((button) => button.addEventListener('click', () => { state.gallery.filter = button.dataset.galleryFilter; document.querySelectorAll('[data-gallery-filter]').forEach((candidate) => candidate.classList.toggle('active', candidate.dataset.galleryFilter === state.gallery.filter)); renderGallery(); }));
    $('#openScene').addEventListener('click', browseScenes); $('#saveScene').addEventListener('click', () => save(false)); $('#saveAsScene').addEventListener('click', () => save(true)); $('#undoScene').addEventListener('click', () => rewind('undo')); $('#redoScene').addEventListener('click', () => rewind('redo')); $('#liveAction').addEventListener('click', stopOutput); $('#resumeScene').addEventListener('click', retryWallActivation); $('#retryWallAction').addEventListener('click', retryWallAction); $('#checkScene').addEventListener('click', check); document.querySelectorAll('[data-dialog-close]').forEach((button) => button.addEventListener('click', () => button.closest('dialog').close()));
    $('#playlistChoice').addEventListener('change', (event) => { void openPlaylist(event.target.value).catch((error) => { $('#playlistDraftError').textContent = `${error.message} Your playlist is still open.`; }); });
    $('#playlistName').addEventListener('input', renderPlaylistDraftStatus);
    window.addEventListener('beforeunload', (event) => { if (playlistDirty()) { event.preventDefault(); event.returnValue = ''; } });
    $('#playlistAdd').addEventListener('click', addCurrentSceneToPlaylist);
    $('#playlistSave').addEventListener('click', () => { $('#playlistDraftError').textContent = ''; void savePlaylist().catch((error) => { $('#playlistDraftError').textContent = `${error.message} Your edits are kept. Try Save again.`; }); });
    $('#playlistStart').addEventListener('click', () => { void startPlaylist(); });
    $('#playlistBrowse').addEventListener('click', () => { void openPlaylistPicker(); });
    $('#playlistPickerSearch').addEventListener('input', renderPlaylistPicker);
    $('#playlistPickerAdd').addEventListener('click', () => { void addPickedPlaylistScenes(); });
    $('#playlistPickerCancel').addEventListener('click', () => $('#playlistPickerDialog').close());
    $('#playlistStop').addEventListener('click', () => { void stopPlaylist(); });
    $('#playlistRetryAction').addEventListener('click', () => { if (state.playlist.retryStart) void startPlaylist(state.playlist.retryStart); else void stopPlaylist(); });
    $('#playlistUnlockEditing').addEventListener('click', abandonPlaylistStart);
    $('#playlistRefreshStatus').addEventListener('click', () => { void refreshPlaylistStatus(); });
    $('#playlistDismissError').addEventListener('click', () => { state.playlist.actionError = null; state.playlist.dismissedStatusError = state.playlist.statusErrorKey; renderPlaylistActionFailure(); });
    document.addEventListener('keydown', handleSceneHistoryShortcut);
  }
  installSemanticControls($('#look-title').closest('.inspector'));
  installSemanticControls($('#plants-title').closest('.inspector'));
  wire();
  updateHistoryActions();
  applyScene(defaultScene());
  authoredTargetFps = boundedTargetFps($('#targetFps').value);

  let nimbusLooksRevision = null;
  let nimbusSceneRevision = null;
  async function reconcileNimbus(observation, intent) {
    const nimbus = observation.nimbus;
    if (!nimbus || observation.freshness !== 'fresh') return;
    const saved = nimbus.saved_revision && `${observation.controller_session_id}/${nimbus.saved_revision}`;
    if (saved && saved !== nimbusLooksRevision) {
      try {
        await loadLibrary();
        nimbusLooksRevision = saved;
      } catch (_) { /* The next status poll retries the library read. */ }
    }
    const revision = nimbus.scene_revision && `${observation.controller_session_id}/${nimbus.scene_revision}`;
    if (!revision || revision === nimbusSceneRevision || !observation.scene
        || nimbus.scene_revision !== observation.controller_state_revision
        || !intentIsCurrent(intent) || state.submitting || state.wall.activating || state.publication.queued
        || state.publication.afterStop || state.publication.inFlight) return;
    nimbusSceneRevision = revision;
    state.scene = structuredClone(observation.scene);
    state.selection = null;
    state.dirty = false;
    state.wall.dirty = false;
    state.wall.activationError = null;
    state.wall.retryBlocked = false;
    state.wall.requestId = null;
    clearHistory();
    applyScene(state.scene);
    schedulePreview();
    renderLibrary();
    $('#sceneName').value = `Currently playing · ${state.scene.animation.component_id.replaceAll('_', ' ')}`;
  }
  async function refreshStatus() {
    if (state.refreshInFlight || state.wall.activating) return;
    state.refreshInFlight = true;
    const nimbusIntent = state.intent;
    try {
      const [status, observation] = await Promise.all([
        requestJson(`${api}/status?client_id=${encodeURIComponent(clientId)}`),
        requestJson(`/api/v1/composer/settings/observed${state.wall.requestId ? `?request_id=${encodeURIComponent(state.wall.requestId)}` : ''}`),
      ]);
      state.wall.observation = observation;
    if (observation.scene) state.wall.scene = structuredClone(observation.scene);
      if (observation.command_result?.state === 'failed' && observation.command_result.request_id === state.wall.requestId) { state.wall.activationError = observation.command_result.error || 'Controller playback failed; output may have been interrupted.'; state.wall.retryBlocked = true; }
      state.wall.statusUnavailable = false;
      state.composerInteractionBasis = status.current?.digest || null;
      syncFrameRateObservation(observation);
      if (!status.undo_invalidated) await reconcileNimbus(observation, nimbusIntent);
      renderStatus(status);
      if (status.undo_invalidated && !state.submitting && !state.publication.inFlight && !state.publication.queued) {
        const remoteIntent = state.intent;
        const recovered = await requestJson(`${api}/recovery?client_id=${encodeURIComponent(clientId)}`);
        if (remoteIntent === state.intent && recovered.recovery?.authoritative
            && recovered.status.revision === status.revision) {
          const current = recovered.recovery.scene;
          state.scene = current;
          state.selection = recovered.recovery.opened_look_id ? {kind: 'look', id: recovered.recovery.opened_look_id} : null;
          state.dirty = false; state.wall.dirty = false; state.wall.requestId = null;
          state.wall.activationError = null; state.wall.retryBlocked = false;
          state.wall.adoptedLook = structuredClone(current.look);
          clearHistory(); applyScene(current); schedulePreview(); renderLibrary();
          $('#sceneName').value = state.selection ? '' : `Currently playing · ${current.animation.component_id.replaceAll('_', ' ')}`;
          await restoreSavedSelection();
          await acknowledgeUndo(status.undo_invalidation_revision);
        }
      }

    } catch (error) { state.wall.statusUnavailable = true; renderStatus({...state.status, last_error: error.message}); }
    finally { state.refreshInFlight = false; }
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshStatus(); });
  // A transport return is not a second workflow. Refreshing status lets the
  // server-side coordinator converge the newest valid scene safely.
  window.addEventListener('online', refreshStatus);
  installGalleryLayoutListener();
  function recoverFromInvalidRecovery(body) { state.revision = body.status?.revision || 0; applyScene(defaultScene()); if (body.status) renderStatus(body.status); $('#operationMessage').textContent = `${body.error || 'Saved current scene needs recovery.'} Select a built-in scene to replace it immediately.`; }
  async function hydrateCurrentScene(catalogReady) {
    const hydrationIntent = state.intent;
    await refreshStatus();
    if (!intentIsCurrent(hydrationIntent)) return;
    let response;
    try { response = await fetch(`${api}/recovery?client_id=${encodeURIComponent(clientId)}`); }
    catch (_) { if (!intentIsCurrent(hydrationIntent)) return; const error = new Error('Local Composer server unavailable.'); error.serverUnavailable = true; throw error; }
    const body = await response.json();
    // Recovery describes the state before any edit made while loading.
    if (!intentIsCurrent(hydrationIntent)) return;
    if (!response.ok) { const error = new Error(body.error || 'Current scene recovery is unavailable.'); if (response.status >= 500) { error.serverUnavailable = true; throw error; } recoverFromInvalidRecovery(body); return; }
    if (nimbusSceneRevision) return;
    if (body.recovery) {
      state.scene = body.recovery.scene; state.selection = body.recovery.opened_look_id ? {kind:'look', id:body.recovery.opened_look_id} : null;
      state.dirty = false; applyScene(state.scene); await restoreSavedSelection();
    } else {
      await catalogReady;
      if (!intentIsCurrent(hydrationIntent)) return;
      applyScene(defaultScene());
      state.dirty = false;
    }
    // The legacy Composer may be empty after restart while the canonical wall
    // still has a selected Scene. Hydration must not republish that Scene or
    // restart stopped output. The operator chooses when to resume output.

  }
  renderPlaylist();
  const catalogReady = loadGallery();
  Promise.all([
    catalogReady,
    hydrateCurrentScene(catalogReady).then(() => Promise.all([loadLibrary(), loadPlaylists(), refreshPlaylistStatus()])),
  ]).then(() => { previewScheduler.start(); schedulePreview(); return refreshStatus(); })
    .then(() => { setInterval(() => { if (!document.hidden) { refreshStatus(); refreshPlaylistStatus(); } }, 1000); })
    .catch((error) => { $('#operationMessage').textContent = error.message || 'Local Composer server unavailable.'; if (error.serverUnavailable) window.dispatchEvent(new Event('composer-server-unavailable')); });
})();
