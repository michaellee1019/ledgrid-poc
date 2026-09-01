(() => {
  'use strict';
  const root = document.querySelector('.composer');
  const api = root.dataset.apiRoot;
  const $ = (selector) => document.querySelector(selector);
  const nativeDigest = 'd0b8c0f9c7d55a8f58b6156e20c59afe6e4c5a7e2821cb6b3a29d9af81c296bf';
  // A page lifetime client id makes a reload a new mutation stream.  Reusing
  // an id while resetting its sequence would make the next authored edit stale.
  const clientId = crypto.randomUUID();
  const state = { status: null, library: {items: [], favorites: []}, filter: 'all', query: '', selection: null,
    scene: null, history: [], redo: [], sequence: 0, submitting: false, previewGeneration: 0, refreshInFlight: false, dirty: false };
  const identity = (value) => value ? `r${value.revision} · ${value.digest}` : 'None';
  const number = (id) => Number($(id).value);
  const recoveryMatchesStatus = (body) => Boolean(body.recovery?.authoritative && body.status?.current && body.recovery?.basis?.digest === body.status.current.digest && body.recovery?.basis?.revision === body.status.current.revision);
  const emojiParameters = () => ({text: $('#emojiText').value, x_offset: Math.trunc(number('#emojiXOffset')), y_offset: Math.trunc(number('#emojiYOffset')), char_spacing: Math.trunc(number('#emojiCharSpacing')), line_spacing: Math.trunc(number('#emojiLineSpacing')), scroll_speed: number('#emojiScrollSpeed'), pulse_speed: number('#emojiPulseSpeed')});
  const fireflyParameters = () => ({seed: 7319, population: Math.trunc(number('#fireflyPopulation')), coupling_radius: 8, synchrony: number('#fireflySynchrony'), wandering: number('#fireflyWandering'), pulse_softness: number('#fireflyPulseSoftness'), meadow_glow: number('#fireflyMeadowGlow')});
  const emojiWidget = () => ({id: 'composer-emoji-message', component: {component_id: 'emoji_arranger', version: 1, provider: 'python', role: 'widget', parameters: emojiParameters()}, visible: true, placement: {mode: 'manual', strip_translation: 0, led_translation: 0}});

  function defaultScene() {
    return { schema: 'ledgrid.scene.v2',
      background: {component_id: 'native_aurora', version: 1, provider: 'receiver_native', role: 'background', bundle_digest: nativeDigest, parameters: {gain: number('#backgroundGain'), source_fps: 30, seed: 4201}},
      animation: {component_id: 'aurora_curtains', version: 1, provider: 'python', role: 'animation', parameters: {curtain_density: number('#curtainDensity'), fold_depth: number('#foldDepth'), glow_intensity: number('#glowIntensity'), source_fps: 30, seed: 4201}},
      widgets: [], plants: {effects: {version: 1, active: [], strengths: {}}},
      look: {palette_id: $('#previewPalette').value, pace: number('#wallPace'), presentation_brightness: number('#sceneLuminance')} };
  }
  function sceneFromControls() {
    const next = structuredClone(state.scene || defaultScene());
    next.background.parameters = {...next.background.parameters, gain: number('#backgroundGain')};
    const choice = $('#animationChoice').value;
    if (choice !== next.animation.component_id) next.animation = choice === 'conway_life'
      ? {component_id: 'conway_life', version: 1, provider: 'python', role: 'animation', parameters: {seed: number('#lifeSeed'), rule: 'B3/S23', initial_density: .14, generations_per_second: number('#lifeRate'), seed_cells: []}}
      : choice === 'tetris'
        ? {component_id: 'tetris', version: 1, provider: 'python', role: 'animation', parameters: {}}
        : choice === 'firefly_synchrony'
          ? {component_id: 'firefly_synchrony', version: 1, provider: 'python', role: 'animation', parameters: fireflyParameters()}
        : {component_id: 'aurora_curtains', version: 1, provider: 'python', role: 'animation', parameters: {curtain_density: number('#curtainDensity'), fold_depth: number('#foldDepth'), glow_intensity: number('#glowIntensity'), source_fps: 30, seed: 4201}};
    else if (choice === 'conway_life') next.animation.parameters = {...next.animation.parameters, seed: number('#lifeSeed'), generations_per_second: number('#lifeRate')};
    else if (choice === 'aurora_curtains') next.animation.parameters = {...next.animation.parameters, curtain_density: number('#curtainDensity'), fold_depth: number('#foldDepth'), glow_intensity: number('#glowIntensity')};
    else if (choice === 'firefly_synchrony') next.animation.parameters = fireflyParameters();
    const clockIndexes = next.widgets.reduce((indexes, widget, index) => widget.component?.component_id === 'clock_overlay' ? [...indexes, index] : indexes, []);
    if (clockIndexes.length === 1) { const clock = next.widgets[clockIndexes[0]]; if (state.lastControl === 'clockEnabled') clock.visible = $('#clockEnabled').checked; if (state.lastControl === 'clockOffset') clock.placement = {...clock.placement, mode: 'manual', strip_translation: clock.placement.strip_translation ?? 0, led_translation: Math.trunc(number('#clockOffset'))}; }
    else if (clockIndexes.length === 0 && state.lastControl === 'clockEnabled' && $('#clockEnabled').checked) next.widgets.push({id: 'composer-clock', component: {component_id: 'clock_overlay', version: 1, provider: 'python', role: 'widget', parameters: {format_24h: false, show_seconds: true, clock_offset_minutes: 0, color: [255, 224, 128]}}, visible: true, placement: {mode: 'manual', strip_translation: 0, led_translation: -8}});
    const emojiIndexes = next.widgets.reduce((indexes, widget, index) => widget.component?.component_id === 'emoji_arranger' ? [...indexes, index] : indexes, []);
    if (emojiIndexes.length === 1) { const emoji = next.widgets[emojiIndexes[0]]; if (state.lastControl === 'emojiEnabled') emoji.visible = $('#emojiEnabled').checked; else if (state.lastControl?.startsWith('emoji')) emoji.component.parameters = emojiParameters(); }
    else if (emojiIndexes.length === 0 && state.lastControl === 'emojiEnabled' && $('#emojiEnabled').checked) next.widgets.push(emojiWidget());
    next.look = {palette_id: $('#previewPalette').value, pace: number('#wallPace'), presentation_brightness: number('#sceneLuminance')};
    return next;
  }
  function applyScene(scene) {
    state.scene = structuredClone(scene);
    const animation = scene.animation || {};
    const parameters = animation.parameters || {};
    if (![...$('#animationChoice').options].some((option) => option.value === animation.component_id)) $('#animationChoice').append(new Option(`${animation.component_id} (preserved)`, animation.component_id));
    $('#animationChoice').value = animation.component_id;
    $('#lifeSeed').value = parameters.seed ?? 4201; $('#lifeRate').value = parameters.generations_per_second ?? 5;
    const aurora = animation.component_id === 'aurora_curtains' ? parameters : {};
    $('#curtainDensity').value = aurora.curtain_density ?? .56; $('#foldDepth').value = aurora.fold_depth ?? .58;
    $('#glowIntensity').value = aurora.glow_intensity ?? .62; $('#backgroundGain').value = scene.background?.parameters?.gain ?? .62;
    const firefly = animation.component_id === 'firefly_synchrony' ? parameters : {};
    $('#fireflyPopulation').value = firefly.population ?? 100; $('#fireflySynchrony').value = firefly.synchrony ?? .85;
    $('#fireflyWandering').value = firefly.wandering ?? .55; $('#fireflyPulseSoftness').value = firefly.pulse_softness ?? .5; $('#fireflyMeadowGlow').value = firefly.meadow_glow ?? .12;
    const clocks = (scene.widgets || []).filter((widget) => widget.component?.component_id === 'clock_overlay');
    const clock = clocks.length === 1 ? clocks[0] : null;
    $('#clockEnabled').checked = Boolean(clock?.visible); $('#clockOffset').value = clock?.placement?.led_translation ?? -8;
    const emojis = (scene.widgets || []).filter((widget) => widget.component?.component_id === 'emoji_arranger');
    const emoji = emojis.length === 1 ? emojis[0] : null; const message = emoji?.component?.parameters || {};
    $('#emojiEnabled').checked = Boolean(emoji?.visible); $('#emojiText').value = message.text ?? 'HI🔥'; $('#emojiXOffset').value = message.x_offset ?? 8; $('#emojiYOffset').value = message.y_offset ?? 3;
    $('#emojiCharSpacing').value = message.char_spacing ?? 1; $('#emojiLineSpacing').value = message.line_spacing ?? 1; $('#emojiScrollSpeed').value = message.scroll_speed ?? 0; $('#emojiPulseSpeed').value = message.pulse_speed ?? .5;
    if (clocks.length > 1) placementWarning({warning: 'Multiple Clock widgets are preserved; this inspector edits only a scene with one Clock widget.'});
    $('#previewPalette').value = scene.look?.palette_id ?? 'mist'; $('#wallPace').value = scene.look?.pace ?? .7; $('#sceneLuminance').value = scene.look?.presentation_brightness ?? .82;
    $('#plantsStatus').textContent = scene.plants?.effects?.active?.length ? `${scene.plants.effects.active.length} plant effects active.` : 'No active plant effects.';
    if (clocks.length <= 1) placementWarning();
  }
  function placementWarning(placement = null) { const warning = $('#widgetWarning'); warning.hidden = !placement?.warning; warning.textContent = placement?.warning || ''; }
  function drawFrame(frame) {
    if (!frame || frame.encoding !== 'rgb_u8_base64') throw new Error('Preview returned an unsupported frame.');
    const bytes = Uint8Array.from(atob(frame.pixels), (character) => character.charCodeAt(0));
    const canvas = $('#scenePreview'); const context = canvas.getContext('2d'); const image = context.createImageData(frame.width, frame.height);
    for (let strip = 0; strip < frame.width; strip += 1) for (let led = 0; led < frame.height; led += 1) { const source = (strip * frame.height + led) * 3; const target = ((frame.height - 1 - led) * frame.width + strip) * 4; image.data[target] = bytes[source]; image.data[target + 1] = bytes[source + 1]; image.data[target + 2] = bytes[source + 2]; image.data[target + 3] = 255; }
    context.putImageData(image, 0, 0);
  }
  async function preview(scene) { let response; try { response = await fetch(`${api}/preview`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({origin: 'composer', scene})}); } catch (_) { const error = new Error('Local Composer server unavailable.'); error.previewUnavailable = true; throw error; } const body = await response.json(); if (!response.ok) { const error = new Error(body.error || 'Preview could not render.'); error.previewUnavailable = response.status >= 500; throw error; } return body; }
  const previewScheduler = new window.ComposerPreviewScheduler({
    request: preview,
    isVisible: () => !document.hidden,
    onFrame: (body) => { drawFrame(body.frame); $('#previewIdentity').textContent = identity(body.basis); $('#previewStatus').textContent = 'Installed final runtime frame.'; placementWarning(Object.values(body.widget_placements || {}).find((placement) => placement.warning)); },
    onError: (error) => { $('#previewStatus').textContent = error.message || 'Preview could not render.'; if (error.previewUnavailable) window.dispatchEvent(new Event('composer-server-unavailable')); },
  });
  function renderStatus(payload) {
    const status = payload.status || payload; state.status = status;
    state.revision = Math.max(state.revision || 0, status.revision || 0);
    $('#connectionState').textContent = status.connected ? (status.running ? 'Connected · output running' : 'Connected · output stopped') : 'Disconnected';
    $('#observedIdentity').textContent = identity(status.observed); $('#diagnosticObserved').textContent = identity(status.observed); $('#desiredIdentity').textContent = identity(status.desired); $('#sceneRevision').textContent = String(status.revision ?? 0);
    $('#sceneIdentity').textContent = identity(status.current); $('#saveState').textContent = state.dirty ? 'Unsaved changes' : (state.selection?.kind === 'look' ? 'Saved look' : 'Current scene');
    $('#liveAction').textContent = status.running && status.armed && status.current ? 'Stop' : 'Go Live';
    $('#operationMessage').textContent = status.last_error || (status.armed ? 'Changes publish immediately.' : 'Use Go Live to arm output.');
  }
  async function acknowledgeUndo(revision) { state.history = []; state.redo = []; await fetch(`${api}/undo-ack`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({client_id: clientId, revision})}); }
  function schedulePreview() { const generation = ++state.previewGeneration; const candidate = sceneFromControls(); state.scene = candidate; previewScheduler.submitAuthored(candidate, {generation}).catch((error) => { if (generation === state.previewGeneration) $('#previewStatus').textContent = error.message; }); }
  function remember(previous) { state.history.push(previous); if (state.history.length > 40) state.history.shift(); state.redo = []; }
  async function submit(scene, {builtin = false, rememberEdit = false} = {}) {
    if (rememberEdit) remember(structuredClone(state.scene || defaultScene()));
    state.scene = scene; schedulePreview(); state.submitting = true;
    const endpoint = builtin ? '/built-ins/open' : '/scene';
    const body = builtin ? {scene, client_id: clientId, mutation_id: crypto.randomUUID(), client_sequence: ++state.sequence} : {origin: 'composer', scene, client_id: clientId, mutation_id: crypto.randomUUID(), client_sequence: ++state.sequence};
    try { const response = await fetch(`${api}${endpoint}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}); const result = await response.json(); renderStatus(result); if (!response.ok) throw Object.assign(new Error(result.error || 'Current scene could not be accepted.'), {result}); return result; }
    finally { state.submitting = false; }
  }
  async function edit(event) { state.lastControl = event?.target?.id || null; const previous = structuredClone(state.scene || defaultScene()); const next = sceneFromControls(); state.dirty = true; try { await submit(next, {rememberEdit: true}); } catch (error) { state.scene = previous; applyScene(previous); $('#operationMessage').textContent = error.message; } }
  function filteredItems() { const query = state.query.toLocaleLowerCase(); let items = state.library.items.filter((item) => item.name.toLocaleLowerCase().includes(query)); if (state.filter === 'favorites') items = items.filter((item) => state.library.favorites.some((favorite) => favorite.kind === item.kind && favorite.id === item.id)); else if (state.filter !== 'all') items = items.filter((item) => item.kind === state.filter); return items; }
  function renderLibrary() { const target = $('#libraryList'); target.replaceChildren(); const items = filteredItems(); $('#libraryEmpty').hidden = items.length > 0; items.forEach((item) => { const button = document.createElement('button'); button.type = 'button'; button.className = 'button'; button.setAttribute('aria-current', String(state.selection?.kind === item.kind && state.selection?.id === item.id)); button.innerHTML = `<span>${item.name}</span><span class="library-kind">${item.kind === 'starter' ? 'Built-in' : 'Saved'}</span>`; button.addEventListener('click', () => openItem(item)); const row = document.createElement('li'); row.append(button); target.append(row); }); document.querySelectorAll('[data-library-filter]').forEach((button) => button.classList.toggle('active', button.dataset.libraryFilter === state.filter)); }
  async function openItem(item) {
    try {
      if (item.kind === 'look') { const response = await fetch(`${api}/looks/${item.id}/open`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({client_id: clientId, mutation_id: crypto.randomUUID(), client_sequence: ++state.sequence})}); const result = await response.json(); if (!response.ok) throw new Error(result.error); state.scene = result.look.scene; $('#sceneName').value = result.look.name; renderStatus(result.status); }
      else { const starterResponse = await fetch(`${api}/starters/${item.id}`); const starter = (await starterResponse.json()).starter; applyScene(starter.scene); await submit(starter.scene, {builtin: true}); $('#sceneName').value = starter.name; }
      state.selection = item; state.history = []; state.redo = []; state.dirty = false; applyScene(state.scene); schedulePreview(); renderLibrary(); renderStatus(state.status);
    } catch (error) { $('#operationMessage').textContent = error.message || 'Scene could not be opened.'; }
  }
  function focusable(dialog) { return [...dialog.querySelectorAll('button,[href],input,select,textarea,[tabindex]:not([tabindex="-1"])')].filter((node) => !node.disabled); }
  function openDialog(dialog) { const prior = document.activeElement; dialog.showModal(); focusable(dialog)[0]?.focus(); const trap = (event) => { if (event.key === 'Escape') { event.preventDefault(); dialog.close(); } if (event.key !== 'Tab') return; const nodes = focusable(dialog); const first = nodes[0]; const last = nodes.at(-1); if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); } }; dialog.addEventListener('keydown', trap); dialog.addEventListener('close', () => { dialog.removeEventListener('keydown', trap); prior?.focus(); }, {once: true}); }
  function blockers(result) { const list = $('#readinessList'); list.replaceChildren(); (result.blockers || [{message: result.error || 'Go Live is not ready.', recovery: 'Review connection and current scene.'}]).forEach((blocker) => { const item = document.createElement('li'); item.textContent = `${blocker.message} ${blocker.recovery || ''}`; list.append(item); }); openDialog($('#readinessDialog')); }
  async function liveAction() { try { if (state.status?.running && state.status?.armed && state.status?.current) { const response = await fetch(`${api}/stop`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({client_id: clientId})}); const result = await response.json(); renderStatus(result); if (!response.ok) throw new Error(result.error); } else { if (!state.status?.current && state.scene) await submit(structuredClone(state.scene)); const response = await fetch(`${api}/go-live`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({client_id: clientId})}); const result = await response.json(); renderStatus(result); if (!response.ok) { blockers(result); return; } } } catch (error) { $('#operationMessage').textContent = error.message || 'Operation was not acknowledged.'; } }
  async function check() { try { const response = await fetch(`${api}/check`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({origin: 'composer', scene: sceneFromControls()})}); const result = await response.json(); $('#checkMessage').textContent = response.ok ? 'This is advisory; it does not change output.' : (result.error || 'Check could not complete.'); const details = $('#checkDetails'); details.replaceChildren(); [['Scene identity', identity(result.basis)], ['Connection', result.status?.connected ? 'Connected' : 'Disconnected'], ['Publication', result.status?.armed ? 'Immediate when edited' : 'Use Go Live to arm output']].forEach(([term, description]) => { const entry = document.createElement('div'); entry.innerHTML = `<dt>${term}</dt><dd>${description}</dd>`; details.append(entry); }); if (result.status) renderStatus(result); openDialog($('#checkDialog')); } catch (error) { $('#operationMessage').textContent = error.message; } }
  async function save(as) { try { const scene = sceneFromControls(); const name = $('#sceneName').value.trim(); if (as || state.selection?.kind !== 'look') { if (!name) throw new Error('Name this scene before Save As.'); const response = await fetch(`${api}/looks`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name, scene})}); const result = await response.json(); if (!response.ok) throw new Error(result.error); state.selection = {kind: 'look', id: result.look.id, name: result.look.name}; }
      else { const response = await fetch(`${api}/looks/save`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({scene})}); const result = await response.json(); if (!response.ok) throw new Error(result.error); }
      await loadLibrary(); state.dirty = false; $('#saveState').textContent = 'Saved';
    } catch (error) { $('#operationMessage').textContent = error.message; } }
  async function rewind(direction) { const source = direction === 'undo' ? state.history : state.redo; const next = source.pop(); if (!next) return; const opposite = direction === 'undo' ? state.redo : state.history; opposite.push(structuredClone(state.scene)); state.dirty = true; applyScene(next); try { await submit(next); } catch (error) { $('#operationMessage').textContent = error.message; } }
  async function loadLibrary() { const response = await fetch(`${api}/library`); state.library = await response.json(); renderLibrary(); }
  function wire() {
    ['#backgroundGain','#curtainDensity','#foldDepth','#glowIntensity','#animationChoice','#lifeSeed','#lifeRate','#fireflyPopulation','#fireflySynchrony','#fireflyWandering','#fireflyPulseSoftness','#fireflyMeadowGlow','#clockEnabled','#clockOffset','#emojiEnabled','#emojiText','#emojiXOffset','#emojiYOffset','#emojiCharSpacing','#emojiLineSpacing','#emojiScrollSpeed','#emojiPulseSpeed','#previewPalette','#wallPace','#sceneLuminance'].forEach((selector) => $(selector).addEventListener('change', edit));
    $('#removeEmoji').addEventListener('click', async () => { const next = structuredClone(state.scene || defaultScene()); next.widgets = next.widgets.filter((widget) => widget.component?.component_id !== 'emoji_arranger'); state.lastControl = 'removeEmoji'; try { await submit(next, {rememberEdit: true}); applyScene(next); } catch (error) { $('#operationMessage').textContent = error.message; } });
    $('#librarySearch').addEventListener('input', (event) => { state.query = event.target.value; renderLibrary(); }); document.querySelectorAll('[data-library-filter]').forEach((button) => button.addEventListener('click', () => { state.filter = button.dataset.libraryFilter; renderLibrary(); }));
    $('#openScene').addEventListener('click', () => $('#librarySearch').focus()); $('#saveScene').addEventListener('click', () => save(false)); $('#saveAsScene').addEventListener('click', () => save(true)); $('#undoScene').addEventListener('click', () => rewind('undo')); $('#redoScene').addEventListener('click', () => rewind('redo')); $('#liveAction').addEventListener('click', liveAction); $('#checkScene').addEventListener('click', check); document.querySelectorAll('[data-dialog-close]').forEach((button) => button.addEventListener('click', () => button.closest('dialog').close()));
    document.addEventListener('keydown', (event) => { if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== 'z') return; event.preventDefault(); rewind(event.shiftKey ? 'redo' : 'undo'); });
  }
  function syncSecondaryOperations() { $('#secondaryOperations').open = !window.matchMedia('(max-width: 760px)').matches; }
  const phoneLayout = window.matchMedia('(max-width: 760px)');
  phoneLayout.addEventListener('change', syncSecondaryOperations);
  syncSecondaryOperations(); wire(); applyScene(defaultScene());
  async function refreshStatus() { if (state.refreshInFlight) return; state.refreshInFlight = true; try { const response = await fetch(`${api}/recovery?client_id=${encodeURIComponent(clientId)}`); const body = await response.json(); if (!response.ok) throw new Error(body.error || 'Local Composer server unavailable.'); const status = body.status; const newerRemoteRevision = Boolean(recoveryMatchesStatus(body) && status.revision > (state.revision || 0)); if (newerRemoteRevision) { state.scene = body.recovery.scene; state.selection = body.recovery.opened_look_id ? {kind:'look', id:body.recovery.opened_look_id} : null; state.history = []; state.redo = []; state.dirty = false; applyScene(state.scene); schedulePreview(); } renderStatus(status); if (newerRemoteRevision && status.undo_invalidated) acknowledgeUndo(status.undo_invalidation_revision); } catch (error) { $('#operationMessage').textContent = error.message; } finally { state.refreshInFlight = false; } }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshStatus(); });
  function recoverFromInvalidRecovery(body) { state.revision = body.status?.revision || 0; applyScene(defaultScene()); if (body.status) renderStatus(body.status); $('#operationMessage').textContent = `${body.error || 'Saved current scene needs recovery.'} Select a built-in scene or use Go Live to replace it.`; }
  async function hydrateCurrentScene() { let response; try { response = await fetch(`${api}/recovery?client_id=${encodeURIComponent(clientId)}`); } catch (_) { const error = new Error('Local Composer server unavailable.'); error.serverUnavailable = true; throw error; } const body = await response.json(); if (!response.ok) { const error = new Error(body.error || 'Current scene recovery is unavailable.'); if (response.status >= 500) { error.serverUnavailable = true; throw error; } recoverFromInvalidRecovery(body); return; } if (body.recovery && (recoveryMatchesStatus(body) || !body.status.current)) { state.scene = body.recovery.scene; state.selection = body.recovery.opened_look_id ? {kind:'look', id:body.recovery.opened_look_id} : null; state.dirty = false; state.revision = body.status.revision || 0; applyScene(state.scene); renderStatus(body.status); } else { state.revision = body.status.revision || 0; applyScene(defaultScene()); renderStatus(body.status); } }
  hydrateCurrentScene().then(loadLibrary).then(() => { previewScheduler.start(() => sceneFromControls()); schedulePreview(); return refreshStatus(); }).then(() => { setInterval(() => { if (!document.hidden) refreshStatus(); }, 2500); }).catch((error) => { $('#operationMessage').textContent = error.message || 'Local Composer server unavailable.'; if (error.serverUnavailable) window.dispatchEvent(new Event('composer-server-unavailable')); });
})();
