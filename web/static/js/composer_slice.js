(() => {
  'use strict';
  const root = document.querySelector('.composer');
  const api = root.dataset.apiRoot;
  const list = document.querySelector('#overlayList');
  const state = { overlays: [], checked: null, activationKey: null, presentationMode: 'vibe', previewGeneration: 0, previewScheduled: false, pendingPreview: null, autosaveChain: Promise.resolve(), selectedOverlaySlot: null, drag: null, looks: [], library: { items: [], favorites: [], recents: [] }, libraryReady: false, libraryFilter: 'all', libraryQuery: '', librarySelection: null, libraryPreviewGeneration: 0, libraryCardObserver: null, libraryCardQueue: [], libraryCardInFlight: 0, libraryPreviewCache: new Map(), libraryCardByReference: new Map(), deleteLookId: null, starterId: null, background: { seed: 4201, source_fps: 30 }, reference: null, recovery: null, reconciliation: null, committedPreview: null, components: [] };
  // Filled exclusively by the qualified local component endpoint.
  const defaults = {};
  const componentLabels = { conway_life: 'Conway Life', clock_overlay: 'Clock Overlay' };
  const TRANSLATION_MIN = -(2 ** 31);
  const TRANSLATION_MAX = (2 ** 31) - 1;
  const NATIVE_AURORA_DIGEST = 'd0b8c0f9c7d55a8f58b6156e20c59afe6e4c5a7e2821cb6b3a29d9af81c296bf';
  const $ = (selector) => document.querySelector(selector);
  const identity = (item) => item ? `r${item.revision} · ${item.digest}` : 'No acknowledgement';
  function draft() {
    const conway = state.overlays.find((overlay) => overlay.component_id === 'conway_life' && overlay.enabled);
    const clock = state.overlays.find((overlay) => overlay.component_id === 'clock_overlay');
    return { origin: 'composer', scene: {
      schema: 'ledgrid.scene.v2',
      background: { component_id: 'native_aurora', version: 1, provider: 'receiver_native', role: 'background', bundle_digest: NATIVE_AURORA_DIGEST, parameters: { gain: Number($('#glowIntensity').value), source_fps: state.background.source_fps, seed: state.background.seed } },
      animation: conway
        ? { component_id: 'conway_life', version: 1, provider: 'python', role: 'animation', parameters: conway.parameters }
        : { component_id: 'aurora_curtains', version: 1, provider: 'python', role: 'animation', parameters: { curtain_density: Number($('#curtainDensity').value), fold_depth: Number($('#foldDepth').value), glow_intensity: Number($('#glowIntensity').value), source_fps: state.background.source_fps, seed: state.background.seed } },
      widgets: clock ? [{ id: 'clock', component: { component_id: 'clock_overlay', version: 1, provider: 'python', role: 'widget', parameters: clock.parameters }, visible: Boolean(clock.enabled), placement: { mode: 'manual', strip_translation: canonicalTranslation(clock.strip), led_translation: canonicalTranslation(clock.led) } }] : [],
      plants: { effects: { version: 1, active: [], strengths: {} } },
      look: { palette_id: $('#previewPalette').value, pace: Number($('#wallPace').value), presentation_brightness: Number($('#sceneLuminance').value) },
    } };
  }
  function canonicalTranslation(value) { const number = Number(value); if (!Number.isFinite(number)) return 0; return Math.min(TRANSLATION_MAX, Math.max(TRANSLATION_MIN, Math.trunc(number))); }
  function explanationSnapshot() { return { reference:state.reference && {kind:state.reference.kind, id:state.reference.id, basis:state.reference.basis}, recovery:state.recovery && {reference:state.recovery.reference, basis:state.recovery.basis}, preview:state.committedPreview && state.committedPreview.basis, previewUnavailable:$('#previewIdentity').textContent === 'Preview unavailable', reconciliation:state.reconciliation, previewMessage:$('#previewStatus').textContent, draftMessage:$('#draftState').textContent, localMessage:$('#liveMessage').textContent }; }
  function publishComposerExplanation() { const snapshot=explanationSnapshot(); window.__composerStateProjection=snapshot; window.dispatchEvent(new CustomEvent('composer-state-change', {detail:snapshot})); }
  function markDraftLocal() { state.checked = null; state.activationKey = null; state.reconciliation={...(state.reconciliation || {}), desired:null, state:'pending'}; $('#desiredIdentity').textContent = 'Not checked'; $('#reconcileState').textContent = 'Pending'; $('#reconcileState').dataset.state = 'pending'; $('#liveMessage').textContent = 'Draft is local.'; publishComposerExplanation(); }
  function edit() { markDraftLocal(); schedulePreview(); }
  function componentChoice(componentId) { return state.components.find((choice) => choice.component_id === componentId); }
  function colorToHex(value) { return `#${value.map((channel) => Number(channel).toString(16).padStart(2, '0')).join('')}`; }
  function hexToColor(value) { return [1, 3, 5].map((offset) => Number.parseInt(value.slice(offset, offset + 2), 16)); }
  function componentControls(overlay) {
    const choice = componentChoice(overlay.component_id);
    if (!choice) return '';
    return choice.controls.map((control) => {
      const value = overlay.parameters[control.id];
      if (control.type === 'checkbox') return `<label class="enabled"><input data-param="${control.id}" type="checkbox" ${value ? 'checked' : ''}> ${control.label}</label>`;
      if (control.type === 'color') return `<label>${control.label} <input data-param="${control.id}" type="color" value="${colorToHex(value)}"></label>`;
      if (control.type === 'select') return `<label>${control.label} <select data-param="${control.id}">${control.options.map((option) => `<option value="${option}" ${value === option ? 'selected' : ''}>${option}</option>`).join('')}</select></label>`;
      return `<label>${control.label} <input data-param="${control.id}" type="number" min="${control.min}" max="${control.max}" step="${control.step}" value="${value}"></label>`;
    }).join('');
  }
  function readParameter(control, input) {
    if (control.type === 'checkbox') return input.checked;
    if (control.type === 'color') return hexToColor(input.value);
    return control.type === 'number' ? Number(input.value) : input.value;
  }
  function setVibeDefaults() { const values = { quiet: ['mist', .70, .82], neutral: ['neutral', 1, 1], vivid: ['spectrum', 1.25, 1.15] }[$('#vibe').value]; $('#previewPalette').value = values[0]; $('#wallPace').value = values[1]; $('#sceneLuminance').value = values[2]; }
  function drawFrame(canvas, frame) {
    if (!frame || frame.encoding !== 'rgb_u8_base64' || frame.width !== 33 || frame.height !== 138 || frame.orientation !== 'strip_major_led_zero_bottom') throw new Error('Preview returned an unsupported frame.');
    const bytes = Uint8Array.from(atob(frame.pixels), (character) => character.charCodeAt(0));
    if (bytes.length !== frame.width * frame.height * 3) throw new Error('Preview frame size is invalid.');
    const context = canvas.getContext('2d'); const image = context.createImageData(frame.width, frame.height);
    for (let strip = 0; strip < frame.width; strip += 1) for (let led = 0; led < frame.height; led += 1) { const source = (strip * frame.height + led) * 3; const target = ((frame.height - 1 - led) * frame.width + strip) * 4; image.data[target] = bytes[source]; image.data[target + 1] = bytes[source + 1]; image.data[target + 2] = bytes[source + 2]; image.data[target + 3] = 255; }
    context.putImageData(image, 0, 0);
  }
  function drawPreview(frame) { drawFrame($('#scenePreview'), frame); }
  async function validatePreview(candidate) { let response; try { response = await fetch(`${api}/preview`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(candidate) }); } catch (cause) { const error = new Error('Local Composer server unavailable.'); error.previewUnavailable = true; throw error; } const body = await response.json(); if (!response.ok) { const error = new Error(body.error || 'Preview could not render.'); error.previewUnavailable = response.status >= 500; throw error; } return body; }
  function sameBasis(left, right) { return Boolean(left && right && left.revision === right.revision && left.digest === right.digest); }
  function setUnsaved(unsaved) { $('#draftState').textContent = unsaved ? 'Unsaved local draft.' : (state.reference ? 'Saved basis.' : 'Choose a starter or saved look to establish a basis.'); publishComposerExplanation(); }
  function reference(kind, id, basis, baseline = null) { const value={ kind, id, basis }; if (kind === 'starter') value.baseline=baseline; return value; }
  function setReference(value) { state.reference = value; state.starterId = value && value.kind === 'starter' ? value.id : null; publishComposerExplanation(); }
  function commitPreview(body) { drawPreview(body.frame); state.committedPreview = body; $('#previewIdentity').textContent = identity(body.basis); $('#previewStatus').textContent = 'Installed final runtime frame · output unchanged.'; publishComposerExplanation(); }
  async function draftRequest(path, options) { const response = await fetch(`${api}/draft${path}`, options); const body = await response.json(); if (!response.ok) throw new Error(body.error || 'Working draft could not be updated.'); return body; }
  async function autosave(body, candidate, generation) {
    // Serialize writes so an older local preview cannot finish after a newer drag edit.
    state.autosaveChain = state.autosaveChain.then(async () => {
      if (generation !== state.previewGeneration || !state.reference) return;
      if (sameBasis(body.basis, state.reference.basis)) { await draftRequest('', {method:'DELETE'}); if (generation === state.previewGeneration) setUnsaved(false); return; }
      const saved=await draftRequest('', {method:'POST', headers:{'Content-Type':'application/json'},body:JSON.stringify({draft:candidate,reference:state.reference})});
      if (generation !== state.previewGeneration) return;
      if (saved.draft) setReference(saved.draft.reference);
      setUnsaved(true);
    }).catch((error) => { if (generation === state.previewGeneration) $('#previewStatus').textContent = error.message || 'Working draft could not be updated.'; });
    return state.autosaveChain;
  }
  const previewScheduler = new window.ComposerPreviewScheduler({
    request: validatePreview,
    isVisible: () => !document.hidden,
    onFrame: (body, task) => {
      if (task.kind === 'authored' && task.generation !== state.previewGeneration) return;
      commitPreview(body);
      if (task.kind === 'authored' && task.autosave) void autosave(body, task.candidate, task.generation);
    },
    onError: (error, task) => {
      if (task.kind === 'authored' && task.generation !== state.previewGeneration) return;
      $('#previewIdentity').textContent = 'Preview unavailable';
      $('#previewStatus').textContent = error.message || 'Preview could not render.';
      publishComposerExplanation();
      if (error.previewUnavailable) serverUnavailable();
    },
  });
  async function queuePreview(candidate = draft(), options = {}) {
    const generation = options.generation || ++state.previewGeneration;
    $('#previewStatus').textContent = 'Rendering installed final composition…'; publishComposerExplanation();
    try { return await previewScheduler.submitAuthored(candidate, {generation, autosave: options.autosave !== false}); }
    catch (_) { return null; }
  }
  function startFinalPreview() { previewScheduler.start(() => draft()); }
  const previewVisibilityChange = () => { if (!document.hidden) previewScheduler.poll(); };
  window.addEventListener('visibilitychange', previewVisibilityChange);
  window.addEventListener('pagehide', () => window.removeEventListener('visibilitychange', previewVisibilityChange), {once:true});
  function schedulePreview() {
    const generation = ++state.previewGeneration;
    state.pendingPreview = {candidate:draft(), generation};
    if (state.previewScheduled) return;
    state.previewScheduled = true;
    requestAnimationFrame(() => { const pending=state.pendingPreview; state.pendingPreview=null; state.previewScheduled=false; if (pending) queuePreview(pending.candidate, {generation:pending.generation}); });
  }
  function selectedOverlay() { return state.overlays.find((overlay) => overlay.slot_id === state.selectedOverlaySlot) || null; }
  function clearDrag(overlay = null) {
    if (!state.drag || (overlay && state.drag.overlay !== overlay)) return;
    const canvas = $('#scenePreview');
    if (canvas.hasPointerCapture && canvas.hasPointerCapture(state.drag.pointerId)) canvas.releasePointerCapture(state.drag.pointerId);
    state.drag = null; canvas.classList.remove('is-dragging');
  }
  function selectOverlay(slotId) {
    state.selectedOverlaySlot = state.overlays.some((overlay) => overlay.slot_id === slotId) ? slotId : null;
    render();
  }
  function setOverlayPlacement(overlay, strip, led) {
    const nextStrip = canonicalTranslation(strip); const nextLed = canonicalTranslation(led);
    if (overlay.strip === nextStrip && overlay.led === nextLed) return false;
    overlay.strip = nextStrip; overlay.led = nextLed;
    render(); edit();
    return true;
  }
  function moveSelectedOverlay(stripDelta, ledDelta) {
    const overlay = selectedOverlay();
    if (!overlay || !overlay.enabled) return false;
    return setOverlayPlacement(overlay, canonicalTranslation(overlay.strip) + stripDelta, canonicalTranslation(overlay.led) + ledDelta);
  }
  function beginPreviewDrag(event) {
    if (event.button !== 0 && event.pointerType !== 'touch') return;
    const overlay = selectedOverlay();
    if (!overlay || !overlay.enabled) return;
    const canvas = $('#scenePreview');
    state.drag = {pointerId:event.pointerId, overlay, startX:event.clientX, startY:event.clientY, strip:canonicalTranslation(overlay.strip), led:canonicalTranslation(overlay.led)};
    canvas.setPointerCapture(event.pointerId); canvas.classList.add('is-dragging'); event.preventDefault();
  }
  function continuePreviewDrag(event) {
    const drag = state.drag; if (!drag || drag.pointerId !== event.pointerId) return;
    // Object identity prevents a cancelled/removed/re-added slot from receiving a stale gesture.
    if (!state.overlays.includes(drag.overlay) || selectedOverlay() !== drag.overlay || !drag.overlay.enabled) { clearDrag(); return; }
    const rect = $('#scenePreview').getBoundingClientRect(); if (!rect.width || !rect.height) return;
    const strips = Math.round((event.clientX - drag.startX) / (rect.width / 33));
    const leds = -Math.round((event.clientY - drag.startY) / (rect.height / 138));
    if (setOverlayPlacement(drag.overlay, drag.strip + strips, drag.led + leds)) event.preventDefault();
  }
  function endPreviewDrag(event) { if (state.drag && state.drag.pointerId === event.pointerId) clearDrag(); }
  function nudgeSelectedOverlay(event) {
    const step = event.shiftKey ? 5 : 1;
    const deltas = {ArrowLeft:[-step, 0], ArrowRight:[step, 0], ArrowUp:[0, step], ArrowDown:[0, -step]};
    const delta = deltas[event.key]; if (!delta || !moveSelectedOverlay(...delta)) return;
    event.preventDefault();
  }
  function render() {
    list.replaceChildren(); $('#overlayEmpty').hidden = state.overlays.length > 0;
    const available = state.components.filter((choice) => !state.overlays.some((overlay) => overlay.component_id === choice.component_id));
    const picker = $('#overlayChoice'); picker.replaceChildren();
    available.forEach((choice) => { const option = document.createElement('option'); option.value = choice.slot_id; option.textContent = choice.label; picker.append(option); });
    picker.disabled = available.length === 0; $('#addOverlay').disabled = available.length === 0;
    state.overlays.forEach((overlay, index) => {
      const row = document.createElement('li'); row.className = `overlay${state.selectedOverlaySlot === overlay.slot_id ? ' is-selected' : ''}`; row.dataset.overlaySlot = overlay.slot_id; row.tabIndex = 0; row.setAttribute('aria-selected', String(state.selectedOverlaySlot === overlay.slot_id));
      const choice = componentChoice(overlay.component_id); const title = choice ? choice.label : (componentLabels[overlay.component_id] || overlay.component_id);
      row.innerHTML = `<div class="overlay-top"><div><span class="overlay-title">${title}</span><span class="overlay-slot"> · slot: ${overlay.slot_id}</span></div><div><button class="button text" data-action="up" ${index === 0 ? 'disabled' : ''}>Up</button><button class="button text" data-action="down" ${index === state.overlays.length - 1 ? 'disabled' : ''}>Down</button><button class="button text" data-action="remove">Remove</button></div></div><div class="overlay-controls"><label class="enabled"><input data-field="enabled" type="checkbox" ${overlay.enabled ? 'checked' : ''}> Enabled</label><label>Opacity <input data-field="opacity" type="number" min="0" max="255" value="${overlay.opacity}"></label><label>Across <input data-field="strip" type="number" min="${TRANSLATION_MIN}" max="${TRANSLATION_MAX}" step="1" value="${canonicalTranslation(overlay.strip)}"></label><label>Down <input data-field="led" type="number" min="${TRANSLATION_MIN}" max="${TRANSLATION_MAX}" step="1" value="${canonicalTranslation(overlay.led)}"></label><label>Stale frames <select data-field="stale"><option value="hold" ${overlay.stale === 'hold' ? 'selected' : ''}>Hold</option><option value="clear" ${overlay.stale === 'clear' ? 'selected' : ''}>Clear after lease</option></select></label>${componentControls(overlay)}</div>`;
      const update = (event) => { const field = event.target.dataset.field; const parameter = event.target.dataset.param; if (field) { overlay[field] = field === 'enabled' ? event.target.checked : (field === 'strip' || field === 'led' ? canonicalTranslation(event.target.value) : event.target.value); if (field === 'strip' || field === 'led') event.target.value = overlay[field]; } if (parameter) { const control = choice.controls.find((item) => item.id === parameter); overlay.parameters[parameter] = readParameter(control, event.target); } if (field || parameter) edit(); };
      row.addEventListener('input', update);
      row.addEventListener('change', update);
      row.addEventListener('click', (event) => { const action = event.target.dataset.action; if (!action) { if (!event.target.closest('input, select, button')) selectOverlay(overlay.slot_id); return; } if (action === 'remove') { clearDrag(overlay); if (state.selectedOverlaySlot === overlay.slot_id) state.selectedOverlaySlot = null; state.overlays.splice(index, 1); } if (action === 'up') [state.overlays[index - 1], state.overlays[index]] = [state.overlays[index], state.overlays[index - 1]]; if (action === 'down') [state.overlays[index + 1], state.overlays[index]] = [state.overlays[index], state.overlays[index + 1]]; edit(); render(); });
      row.addEventListener('keydown', (event) => { if ((event.key === 'Enter' || event.key === ' ') && !event.target.closest('input, select, button')) { selectOverlay(overlay.slot_id); event.preventDefault(); } });
      list.append(row);
    });
  }
  function authoredLook(look) { const scene=look.scene; const authored={schema:'ledgrid.scene.v1',background:scene.background,overlays:scene.overlays,master_brightness:scene.master_brightness}; if(scene.vibe_source==='custom') authored.custom={palette_id:scene.palette_id,wall_pace:scene.wall_pace,presentation_luminance:scene.presentation_luminance}; else authored.vibe=scene.vibe_source; return {origin:'composer',scene:authored}; }
  function recoveryDraft(value) { return value.scene.vibe_source ? authoredLook({scene:value.scene}) : value; }
  function starterDraft(value, preservePresentation = false) { const candidate=preservePresentation ? draft() : {origin:'composer',scene:{schema:'ledgrid.scene.v1',vibe:'quiet',master_brightness:1}}; candidate.scene.background=value.background; candidate.scene.overlays=value.overlays; return candidate; }
  function assignDraft(value) { const scene=value.scene; const p=scene.background.parameters; clearDrag(); state.selectedOverlaySlot=null; $('#curtainDensity').value=p.curtain_density; $('#foldDepth').value=p.fold_depth; $('#glowIntensity').value=p.glow_intensity; state.background={seed:p.seed,source_fps:p.source_fps}; state.presentationMode=scene.vibe ? 'vibe':'custom'; if(scene.vibe){$('#vibe').value=scene.vibe;setVibeDefaults();} if(scene.custom){$('#previewPalette').value=scene.custom.palette_id;$('#wallPace').value=scene.custom.wall_pace;$('#sceneLuminance').value=scene.custom.presentation_luminance;} state.overlays=scene.overlays.map((o)=>({slot_id:o.slot_id,component_id:o.component.component_id,enabled:o.enabled,opacity:o.opacity,strip:canonicalTranslation(o.placement.strip_translation),led:canonicalTranslation(o.placement.led_translation),stale:o.stale_policy.policy==='hold'?'hold':'clear',parameters:{...o.component.parameters}})); render(); }
  async function applyDraft(value, options = {}) { assignDraft(value); const preview=await queuePreview(value, options); if(!preview) throw new Error('Draft could not restore.'); return preview; }
  function status(payload) { const data = payload.status || payload; state.reconciliation=data; $('#desiredIdentity').textContent = data.desired ? identity(data.desired) : 'Not checked'; $('#observedIdentity').textContent = identity(data.observed); $('#reconcileState').textContent = data.state[0].toUpperCase() + data.state.slice(1); $('#reconcileState').dataset.state = data.state; if (data.rejection) $('#liveMessage').textContent = data.rejection; publishComposerExplanation(); }
  async function stopScene() { const button=$('#stopScene'); button.disabled=true; $('#liveMessage').textContent='Stopping the observed scene…'; publishComposerExplanation(); try { const statusBody=await (await fetch(`${api}/status`)).json(); if (!statusBody.observed) throw new Error('Nothing is live to stop.'); const response=await fetch(`${api}/stop`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({basis:statusBody.observed})}); const body=await response.json(); status(body); if (!response.ok) throw new Error(body.error); state.checked=null; state.activationKey=null; $('#liveMessage').textContent='Stopped safely in the local adapter.'; publishComposerExplanation(); } catch(error) { $('#liveMessage').textContent=`${error.message || 'Stop was not acknowledged.'} Retry once.`; publishComposerExplanation(); } finally { button.disabled=false; } }
  async function looks(path = '', options = {}) { const response = await fetch(`${api}/looks${path}`, options); const body = await response.json(); if (!response.ok) throw new Error(body.error); return body; }
  async function starters(path = '', options = {}) { const response = await fetch(`${api}/starters${path}`, options); const body = await response.json(); if (!response.ok) throw new Error(body.error); return body; }
  async function library(path = '', options = {}) { const response = await fetch(`${api}/library${path}`, options); const body = await response.json(); if (!response.ok) throw new Error(body.error || 'Library is unavailable.'); return body; }
  function libraryReferenceKey(item) { return `${item.kind}:${item.id}`; }
  function libraryBasisKey(basis) { return `${basis.revision}:${basis.digest}`; }
  function dropLibraryCardReference(referenceKey) { const basisKey=state.libraryCardByReference.get(referenceKey); state.libraryCardByReference.delete(referenceKey); if (basisKey && ![...state.libraryCardByReference.values()].includes(basisKey)) state.libraryPreviewCache.delete(basisKey); }
  function invalidateLibraryCard(item) { dropLibraryCardReference(libraryReferenceKey(item)); }
  function setLibrary(value) {
    state.library = value;
    state.libraryReady = true;
    const current = new Set(value.items.map(libraryReferenceKey));
    [...state.libraryCardByReference.keys()].filter((key) => !current.has(key)).forEach(dropLibraryCardReference);
    renderLibrary();
    publishLibraryNavigation();
  }
  function libraryNavigationSnapshot() { return {ready:state.libraryReady, items:state.library.items.map((item) => ({kind:item.kind, id:item.id, name:item.name, favorite:item.favorite, recent:item.recent})), filter:state.libraryFilter, query:state.libraryQuery, selection:state.librarySelection}; }
  function publishLibraryNavigation() { const snapshot=libraryNavigationSnapshot(); window.__composerLibraryNavigationProjection=snapshot; window.dispatchEvent(new CustomEvent('composer-library-navigation-change', {detail:snapshot})); }
  function applyLibraryNavigation(value) { state.libraryFilter=value.filter; state.libraryQuery=value.query; state.librarySelection=value.selection; $('#librarySearch').value=value.query; document.querySelectorAll('[data-library-filter]').forEach((item) => item.classList.toggle('active', item.dataset.libraryFilter === value.filter)); renderLibrary(); publishLibraryNavigation(); }
  window.__composerLibraryNavigation = {apply:applyLibraryNavigation, snapshot:libraryNavigationSnapshot};
  async function refreshLibrary() { setLibrary(await library()); }
  function libraryReference(item) { return {kind:item.kind, id:item.id}; }
  async function preflightLibrary(item) { const response=await fetch(`${api}/library/preflight`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reference:libraryReference(item)})}); const body=await response.json(); if (!response.ok) throw new Error(body.error || 'Library is unavailable.'); return body.reference; }
  function filteredLibrary() {
    const query=state.libraryQuery.trim().toLocaleLowerCase();
    const category = state.libraryFilter;
    const ordered = category === 'recent'
      ? state.library.recents.map((reference) => state.library.items.find((item) => item.kind === reference.kind && item.id === reference.id)).filter(Boolean)
      : state.library.items.filter((item) => category === 'all' || item.kind === category || (category === 'favorites' && item.favorite));
    return ordered.filter((item) => item.name.toLocaleLowerCase().includes(query));
  }
  async function recordRecent(item) { setLibrary(await library('/recents', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reference:libraryReference(item)})})); }
  async function toggleFavorite(item) { const path='/favorites'; const options={method:item.favorite ? 'DELETE':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reference:libraryReference(item)})}; setLibrary(await library(path, options)); }
  async function openLibraryItem(item) {
    await preflightLibrary(item);
    invalidateLibraryCard(item);
    if (item.kind === 'starter') {
      const value=(await starters(`/${item.id}`)).starter; const candidate=starterDraft(value, true); const preview=await validatePreview(candidate);
      assignDraft(candidate); setReference(reference('starter', value.id, preview.basis, candidate)); markDraftLocal(); commitPreview(preview); await autosave(preview, candidate); await recordRecent(item); $('#starterMessage').textContent=`Previewing ${value.name}.`;
      return;
    }
    const result=await looks(`/${item.id}`); const candidate=authoredLook(result.look); setReference(reference('look', result.look.id, result.look.basis)); assignDraft(candidate); markDraftLocal(); render(); const preview=await queuePreview();
    if(!preview || !sameBasis(preview.basis, result.look.basis)) throw new Error('Saved look preview no longer matches; recreate it.');
    await recordRecent(item);
  }
  async function fetchLibraryCard(item) {
    const response=await fetch(`${api}/library/cards`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reference:libraryReference(item)})});
    const body=await response.json(); if (!response.ok) throw new Error(body.error || 'Card preview could not render.'); return body;
  }
  function paintLibraryCard(canvas, status, card) { drawFrame(canvas, card.frame); status.textContent=''; canvas.dataset.basis=libraryBasisKey(card.basis); }
  function pumpLibraryCards() {
    while (state.libraryCardInFlight < 2 && state.libraryCardQueue.length) {
      const task=state.libraryCardQueue.shift(); state.libraryCardInFlight += 1;
      fetchLibraryCard(task.item).then((card) => {
        if (task.generation !== state.libraryPreviewGeneration || !task.canvas.isConnected) return;
        const basisKey=libraryBasisKey(card.basis); state.libraryPreviewCache.set(basisKey, card); state.libraryCardByReference.set(task.referenceKey, basisKey);
        paintLibraryCard(task.canvas, task.status, card);
      }).catch((error) => { if (task.generation === state.libraryPreviewGeneration && task.canvas.isConnected) task.status.textContent=error.message || 'Preview unavailable.'; }).finally(() => { state.libraryCardInFlight -= 1; pumpLibraryCards(); });
    }
  }
  function queueLibraryCard(item, canvas, status, generation) {
    const referenceKey=libraryReferenceKey(item); const basisKey=state.libraryCardByReference.get(referenceKey); const cached=basisKey && state.libraryPreviewCache.get(basisKey);
    if (cached) { paintLibraryCard(canvas, status, cached); return; }
    status.textContent='Loading preview…'; state.libraryCardQueue.push({item, canvas, status, generation, referenceKey}); pumpLibraryCards();
  }
  function observeLibraryCard(item, canvas, status, generation) {
    if (!('IntersectionObserver' in window)) { queueLibraryCard(item, canvas, status, generation); return; }
    canvas._libraryCard = {item, status, generation}; state.libraryCardObserver.observe(canvas);
  }
  function renderLibrary() {
    const target=$('#libraryList'); if (!target) return;
    if (state.libraryCardObserver) state.libraryCardObserver.disconnect();
    const generation=++state.libraryPreviewGeneration; state.libraryCardQueue=[];
    state.libraryCardObserver = 'IntersectionObserver' in window ? new IntersectionObserver((entries, observer) => entries.forEach((entry) => { if (!entry.isIntersecting) return; observer.unobserve(entry.target); const task=entry.target._libraryCard; if (task) queueLibraryCard(task.item, entry.target, task.status, task.generation); }), {rootMargin:'160px 0px'}) : null;
    target.replaceChildren(); const items=filteredLibrary(); $('#libraryEmpty').hidden=items.length > 0;
    items.forEach((item) => { const selected=state.librarySelection && state.librarySelection.kind === item.kind && state.librarySelection.id === item.id; const row=document.createElement('li'); row.className=`overlay library-item${selected ? ' is-selected' : ''}`; row.dataset.libraryKind=item.kind; row.dataset.libraryId=item.id; row.tabIndex=0; if (selected) row.setAttribute('aria-current', 'true'); const card=document.createElement('canvas'); card.className='library-card-preview'; card.width=33; card.height=138; card.setAttribute('aria-label', `${item.name} local preview`); const cardStatus=document.createElement('span'); cardStatus.className='library-card-status'; cardStatus.setAttribute('role', 'status'); const title=document.createElement('strong'); title.textContent=item.name; const kind=document.createElement('span'); kind.className='overlay-slot'; kind.textContent=item.kind === 'starter' ? ' · Starting point' : ' · My look'; const open=document.createElement('button'); open.className='button secondary'; open.type='button'; open.textContent=item.kind === 'starter' ? 'Preview' : 'Open'; const favorite=document.createElement('button'); favorite.className='button text'; favorite.type='button'; favorite.textContent=item.favorite ? 'Remove favorite' : 'Favorite'; const actions=document.createElement('div'); actions.append(open, favorite); const line=document.createElement('div'); line.className='overlay-top'; const label=document.createElement('div'); label.append(title, kind); line.append(label, actions); const visual=document.createElement('div'); visual.className='library-card-visual'; visual.append(card, cardStatus); row.append(visual, line);
      row.addEventListener('click', (event) => { if (!event.target.closest('button')) window.dispatchEvent(new CustomEvent('composer-library-card-select', {detail:{reference:libraryReference(item)}})); });
      row.addEventListener('keydown', (event) => { if ((event.key === 'Enter' || event.key === ' ') && !event.target.closest('button')) { window.dispatchEvent(new CustomEvent('composer-library-card-select', {detail:{reference:libraryReference(item)}})); event.preventDefault(); } });
      favorite.addEventListener('click', async () => { try { await toggleFavorite(item); } catch(error) { $('#starterMessage').textContent=error.message || 'Favorite could not be updated.'; } });
      open.addEventListener('click', async () => { try { await openLibraryItem(item); } catch(error) { $('#starterMessage').textContent=error.message || 'This item could not be opened.'; } }); target.append(row); observeLibraryCard(item, card, cardStatus, generation); });
  }
  function renderStarters() { renderLibrary(); }
  function copyName(name) { let candidate = `${name} copy`; let suffix = 2; const names = new Set(state.looks.map((look) => look.name.toLocaleLowerCase())); while (names.has(candidate.toLocaleLowerCase())) candidate = `${name} copy ${suffix++}`; return candidate; }
  function renderLooks() { const target = $('#lookList'); target.replaceChildren(); $('#lookEmpty').hidden = state.looks.length > 0; state.looks.forEach((look) => { const row = document.createElement('li'); row.className = 'overlay'; row.innerHTML = `<div class="overlay-top"><input class="look-row-name" maxlength="80"><div><button class="button text" data-look="open">Open</button><button class="button text" data-look="duplicate">Duplicate</button><button class="button text" data-look="rename">Rename</button><button class="button text" data-look="delete">${state.deleteLookId === look.id ? 'Confirm delete' : 'Delete'}</button></div></div>`; row.querySelector('input').value = look.name; row.addEventListener('click', async (event) => { const action = event.target.dataset.look; if (!action) return; if (action !== 'delete') state.deleteLookId = null; try { if (action === 'duplicate') { const duplicate=await looks(`/${look.id}/duplicate`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:copyName(look.name)})}); invalidateLibraryCard({kind:'look', id:duplicate.look.id}); } if (action === 'rename') { await looks(`/${look.id}`, {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:row.querySelector('input').value})}); invalidateLibraryCard({kind:'look', id:look.id}); } if (action === 'delete') { if (state.deleteLookId !== look.id) { state.deleteLookId = look.id; renderLooks(); return; } await looks(`/${look.id}`, {method:'DELETE'}); invalidateLibraryCard({kind:'look', id:look.id}); state.deleteLookId = null; } if (action === 'open') await openLibraryItem({kind:'look', id:look.id, name:look.name}); state.looks=(await looks()).looks; renderLooks(); await refreshLibrary(); } catch(error) { $('#lookMessage').textContent=error.message||'Saved look could not be changed.'; } }); target.append(row); }); }
  async function goLive() {
    const button = $('#goLive'); button.disabled = true; $('#liveMessage').textContent = 'Checking this exact local draft…'; publishComposerExplanation();
    try {
      if (state.reference) await preflightLibrary(state.reference);
      if (!state.checked) {
        const checked = await fetch(`${api}/check`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(draft()) });
        const checkData = await checked.json();
        if (!checked.ok) { status(checkData); throw new Error(checkData.error); }
        state.checked = checkData; status(checkData);
      }
      $('#liveMessage').textContent = state.activationKey ? 'Retrying the exact checked scene…' : 'Sending checked scene to the local adapter…'; publishComposerExplanation();
      const activated = await fetch(`${api}/activate`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ token: state.checked.token, basis: state.checked.basis, idempotency_key: state.activationKey || (state.activationKey = crypto.randomUUID()) }) });
      const activationData = await activated.json();
      if (!activated.ok) { status(activationData); throw new Error(activationData.error); }
      status(activationData); if (state.reference) await recordRecent(state.reference); $('#liveMessage').textContent = activationData.exact_retry ? 'Exact retry confirmed by the local adapter.' : 'Local adapter observed the exact checked scene.'; publishComposerExplanation();
    } catch (error) { $('#liveMessage').textContent = error.message || 'The draft was not accepted.'; publishComposerExplanation(); }
    finally { button.disabled = false; }
  }
  function renderStarterChoices(items) { const target=$('#starterList'); target.replaceChildren(); items.forEach((starter)=>{ const button=document.createElement('button'); button.className='button secondary'; button.textContent=starter.name; button.addEventListener('click',async()=>{ const generation=++state.previewGeneration; try { const value=(await starters(`/${starter.id}`)).starter; const candidate=starterDraft(value, true); const preview=await validatePreview(candidate); if (generation !== state.previewGeneration) return; assignDraft(candidate); setReference(reference('starter', value.id, preview.basis, candidate)); markDraftLocal(); commitPreview(preview); await autosave(preview, candidate); $('#starterMessage').textContent=`Previewing ${value.name}.`; } catch(error) { if (generation === state.previewGeneration) $('#starterMessage').textContent=error.message; } }); target.append(button); }); }
  $('#addOverlay').addEventListener('click', () => { const slot = $('#overlayChoice').value || Object.keys(defaults).find((candidate) => !state.overlays.some((item) => item.slot_id === candidate)); if (!slot || !defaults[slot]) return; state.overlays.push({...defaults[slot], parameters: structuredClone(defaults[slot].parameters)}); edit(); render(); });
  async function makeSavedReference(result, candidate, generation) { if (generation !== state.previewGeneration) return; setReference(reference('look', result.look.id, result.look.basis)); if (!state.committedPreview || !sameBasis(state.committedPreview.basis, result.look.basis)) await queuePreview(candidate, {generation}); else await autosave(state.committedPreview, candidate, generation); }
  $('#saveLook').addEventListener('click', async () => { try { const candidate=draft(); const generation=state.previewGeneration; const result=await looks('', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:$('#lookName').value,draft:candidate})}); invalidateLibraryCard({kind:'look', id:result.look.id}); await makeSavedReference(result, candidate, generation); $('#lookName').value=''; state.looks=(await looks()).looks; renderLooks(); await refreshLibrary(); $('#lookMessage').textContent=`Saved ${result.look.name}.`; } catch (error) { $('#lookMessage').textContent=error.message || 'Saved look could not be created.'; } });
  $('#remixStarter').addEventListener('click', async () => { if (!state.starterId || !$('#remixName').value) return; try { const candidate=draft(); const generation=state.previewGeneration; const result=await starters(`/${state.starterId}/remix`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:$('#remixName').value,draft:candidate})}); invalidateLibraryCard({kind:'look', id:result.look.id}); await makeSavedReference(result, candidate, generation); $('#remixName').value=''; state.looks=(await looks()).looks; renderLooks(); await refreshLibrary(); $('#starterMessage').textContent='Saved your remix.'; } catch(error) { $('#starterMessage').textContent=error.message; } });
  document.querySelectorAll('.composer input, .composer select').forEach((input) => { if (!['vibe', 'previewPalette', 'wallPace', 'sceneLuminance', 'librarySearch'].includes(input.id)) input.addEventListener('change', edit); });
  $('#vibe').addEventListener('change', () => { state.presentationMode = 'vibe'; setVibeDefaults(); edit(); });
  ['#previewPalette', '#wallPace', '#sceneLuminance'].forEach((selector) => $(selector).addEventListener('change', () => { state.presentationMode = 'custom'; edit(); }));
  async function auroraFallback() { const result=await starters('/aurora'); const candidate=starterDraft(result.starter); const preview=await validatePreview(candidate); return {candidate, reference:reference('starter', result.starter.id, preview.basis, candidate), preview}; }
  async function discardTarget(record) { const ref=record.reference; if (ref.kind === 'look') { try { const result=await looks(`/${ref.id}`); return {candidate:authoredLook(result.look), reference:reference('look', result.look.id, result.look.basis)}; } catch (_) {} } if (ref.kind === 'starter' && ref.baseline) { const candidate=recoveryDraft(ref.baseline); const preview=await validatePreview(candidate); return {candidate, reference:ref, preview}; } return auroraFallback(); }
  $('#restoreDraft').addEventListener('click', async () => { try { if (!state.recovery) throw new Error('This recovery can only be discarded.'); setReference(state.recovery.reference); await applyDraft(recoveryDraft(state.recovery.draft), {autosave:false}); setUnsaved(true); state.recovery=null; $('#recoveryCard').hidden=true; publishComposerExplanation(); } catch(error) { $('#recoveryMessage').textContent=error.message; } });
  $('#discardDraft').addEventListener('click', async () => { try { const next=state.recovery ? await discardTarget(state.recovery) : await auroraFallback(); const preview=next.preview || await validatePreview(next.candidate); if (!sameBasis(preview.basis, next.reference.basis)) throw new Error('Referenced look no longer matches; use Aurora only.'); assignDraft(next.candidate); commitPreview(preview); setReference(next.reference); setUnsaved(false); await draftRequest('', {method:'DELETE'}); state.recovery=null; $('#recoveryCard').hidden=true; publishComposerExplanation(); } catch(error) { $('#recoveryMessage').textContent=error.message; } });
  $('#librarySearch').addEventListener('input', (event) => applyLibraryNavigation({filter:state.libraryFilter, query:event.target.value, selection:null})); document.querySelectorAll('[data-library-filter]').forEach((button) => button.addEventListener('click', () => applyLibraryNavigation({filter:button.dataset.libraryFilter, query:state.libraryQuery, selection:null})));
  const previewCanvas = $('#scenePreview');
  previewCanvas.addEventListener('pointerdown', beginPreviewDrag);
  previewCanvas.addEventListener('pointermove', continuePreviewDrag);
  previewCanvas.addEventListener('pointerup', endPreviewDrag);
  previewCanvas.addEventListener('pointercancel', endPreviewDrag);
  previewCanvas.addEventListener('lostpointercapture', () => clearDrag());
  previewCanvas.addEventListener('keydown', nudgeSelectedOverlay);
  function serverUnavailable() { window.dispatchEvent(new Event('composer-server-unavailable')); }
  $('#goLive').addEventListener('click', goLive); $('#stopScene').addEventListener('click', stopScene); render(); if (!window.__composerShellUnavailable) { queuePreview(); startFinalPreview(); fetch(`${api}/status`).then(async (response)=>{ if (!response.ok) throw new Error('Local Composer server unavailable.'); return response.json(); }).then(status).catch(serverUnavailable); fetch(`${api}/draft`).then(async (response)=>{ const body=await response.json(); if(body.draft){state.recovery=body.draft; $('#restoreDraft').disabled=false; $('#recoveryCard').hidden=false; publishComposerExplanation();} else if(!response.ok){$('#restoreDraft').disabled=true;$('#recoveryCard').hidden=false;$('#recoveryMessage').textContent=body.error || 'Working draft can only be discarded.'; publishComposerExplanation();} }).catch(serverUnavailable); looks().then((result) => { state.looks=result.looks; renderLooks(); }).catch((error) => { $('#lookMessage').textContent=error.message; serverUnavailable(); }); refreshLibrary().catch((error)=>{ $('#starterMessage').textContent=error.message; serverUnavailable(); }); fetch(`${api}/components`).then(async (response) => { const body = await response.json(); if (!response.ok) throw new Error(body.error || 'Overlay choices are unavailable.'); return body; }).then((body) => { state.components = body.choices; body.choices.forEach((choice) => { defaults[choice.slot_id] = { slot_id: choice.slot_id, component_id: choice.component_id, enabled: true, opacity: choice.component_id === 'conway_life' ? 190 : 208, strip: 0, led: choice.component_id === 'clock_overlay' ? -8 : 0, stale: 'hold', parameters: structuredClone(choice.parameters) }; }); render(); }).catch((error) => { $('#previewStatus').textContent = error.message; publishComposerExplanation(); serverUnavailable(); }); }
})();
