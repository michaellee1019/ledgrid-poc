(() => {
  'use strict';
  const root = document.querySelector('.composer');
  const api = root.dataset.apiRoot;
  const list = document.querySelector('#overlayList');
  const state = { overlays: [], checked: null, activationKey: null, presentationMode: 'vibe', previewGeneration: 0, looks: [], library: { items: [], favorites: [], recents: [] }, libraryFilter: 'all', libraryQuery: '', deleteLookId: null, starterId: null, background: { seed: 4201, source_fps: 30 }, reference: null, recovery: null, committedPreview: null, components: [] };
  // Filled exclusively by the qualified local component endpoint.
  const defaults = {};
  const componentLabels = { conway_life: 'Conway Life', clock_overlay: 'Clock Overlay' };
  const $ = (selector) => document.querySelector(selector);
  const identity = (item) => item ? `r${item.revision} · ${item.digest}` : 'No acknowledgement';
  function draft() {
    const presentation = state.presentationMode === 'vibe'
      ? { vibe: $('#vibe').value }
      : { custom: { palette_id: $('#previewPalette').value, wall_pace: Number($('#wallPace').value), presentation_luminance: Number($('#sceneLuminance').value) } };
    return { origin: 'composer', scene: {
      schema: 'ledgrid.scene.v1', ...presentation, master_brightness: 1,
      background: { slot_id: 'background', component_id: 'aurora_curtains', version: 1, provider: 'python', role: 'background', parameters: {
        curtain_density: Number($('#curtainDensity').value), fold_depth: Number($('#foldDepth').value), glow_intensity: Number($('#glowIntensity').value), source_fps: state.background.source_fps, seed: state.background.seed,
      } },
      overlays: state.overlays.map((overlay) => ({
        slot_id: overlay.slot_id, component: { component_id: overlay.component_id, version: 1, provider: 'python', role: 'overlay', parameters: overlay.parameters },
        enabled: overlay.enabled, opacity: Number(overlay.opacity), placement: { strip_translation: Number(overlay.strip), led_translation: Number(overlay.led), clip_policy: 'clip_to_wall' },
        stale_policy: overlay.stale === 'hold' ? { policy: 'hold' } : { policy: 'clear_after_lease', lease_ms: 1200 },
      })),
    } };
  }
  function markDraftLocal() { state.checked = null; state.activationKey = null; $('#desiredIdentity').textContent = 'Not checked'; $('#reconcileState').textContent = 'Pending'; $('#reconcileState').dataset.state = 'pending'; $('#liveMessage').textContent = 'Draft is local.'; }
  function edit() { markDraftLocal(); queuePreview(); }
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
  function drawPreview(frame) {
    if (!frame || frame.encoding !== 'rgb_u8_base64' || frame.width !== 33 || frame.height !== 138 || frame.orientation !== 'strip_major_led_zero_bottom') throw new Error('Preview returned an unsupported frame.');
    const bytes = Uint8Array.from(atob(frame.pixels), (character) => character.charCodeAt(0));
    if (bytes.length !== frame.width * frame.height * 3) throw new Error('Preview frame size is invalid.');
    const canvas = $('#scenePreview'); const context = canvas.getContext('2d'); const image = context.createImageData(frame.width, frame.height);
    for (let strip = 0; strip < frame.width; strip += 1) for (let led = 0; led < frame.height; led += 1) { const source = (strip * frame.height + led) * 3; const target = ((frame.height - 1 - led) * frame.width + strip) * 4; image.data[target] = bytes[source]; image.data[target + 1] = bytes[source + 1]; image.data[target + 2] = bytes[source + 2]; image.data[target + 3] = 255; }
    context.putImageData(image, 0, 0);
  }
  async function validatePreview(candidate) { const response = await fetch(`${api}/preview`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(candidate) }); const body = await response.json(); if (!response.ok) throw new Error(body.error || 'Preview could not render.'); return body; }
  function sameBasis(left, right) { return Boolean(left && right && left.revision === right.revision && left.digest === right.digest); }
  function setUnsaved(unsaved) { $('#draftState').textContent = unsaved ? 'Unsaved local draft.' : (state.reference ? 'Saved basis.' : 'Choose a starter or saved look to establish a basis.'); }
  function reference(kind, id, basis, baseline = null) { const value={ kind, id, basis }; if (kind === 'starter') value.baseline=baseline; return value; }
  function setReference(value) { state.reference = value; state.starterId = value && value.kind === 'starter' ? value.id : null; }
  function commitPreview(body) { drawPreview(body.frame); state.committedPreview = body; $('#previewIdentity').textContent = identity(body.basis); $('#previewStatus').textContent = 'Current local runtime frame · no wall change.'; }
  async function draftRequest(path, options) { const response = await fetch(`${api}/draft${path}`, options); const body = await response.json(); if (!response.ok) throw new Error(body.error || 'Working draft could not be updated.'); return body; }
  async function autosave(body, candidate) {
    if (!state.reference) return;
    if (sameBasis(body.basis, state.reference.basis)) { await draftRequest('', {method:'DELETE'}); setUnsaved(false); return; }
    const saved=await draftRequest('', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({draft:candidate, reference:state.reference})}); if (saved.draft) setReference(saved.draft.reference);
    setUnsaved(true);
  }
  async function queuePreview(candidate = draft(), options = {}) {
    const generation = ++state.previewGeneration; $('#previewStatus').textContent = 'Rendering this local draft…';
    try { const body = await validatePreview(candidate); if (generation !== state.previewGeneration) return null; commitPreview(body); if (options.autosave !== false) await autosave(body, candidate); return body; }
    catch (error) { if (generation === state.previewGeneration) { $('#previewIdentity').textContent = 'Preview unavailable'; $('#previewStatus').textContent = error.message || 'Preview could not render.'; } }
  }
  function render() {
    list.replaceChildren(); $('#overlayEmpty').hidden = state.overlays.length > 0;
    const available = state.components.filter((choice) => !state.overlays.some((overlay) => overlay.component_id === choice.component_id));
    const picker = $('#overlayChoice'); picker.replaceChildren();
    available.forEach((choice) => { const option = document.createElement('option'); option.value = choice.slot_id; option.textContent = choice.label; picker.append(option); });
    picker.disabled = available.length === 0; $('#addOverlay').disabled = available.length === 0;
    state.overlays.forEach((overlay, index) => {
      const row = document.createElement('li'); row.className = 'overlay';
      const choice = componentChoice(overlay.component_id); const title = choice ? choice.label : (componentLabels[overlay.component_id] || overlay.component_id);
      row.innerHTML = `<div class="overlay-top"><div><span class="overlay-title">${title}</span><span class="overlay-slot"> · slot: ${overlay.slot_id}</span></div><div><button class="button text" data-action="up" ${index === 0 ? 'disabled' : ''}>Up</button><button class="button text" data-action="down" ${index === state.overlays.length - 1 ? 'disabled' : ''}>Down</button><button class="button text" data-action="remove">Remove</button></div></div><div class="overlay-controls"><label class="enabled"><input data-field="enabled" type="checkbox" ${overlay.enabled ? 'checked' : ''}> Enabled</label><label>Opacity <input data-field="opacity" type="number" min="0" max="255" value="${overlay.opacity}"></label><label>Across <input data-field="strip" type="number" value="${overlay.strip}"></label><label>Down <input data-field="led" type="number" value="${overlay.led}"></label><label>Stale frames <select data-field="stale"><option value="hold" ${overlay.stale === 'hold' ? 'selected' : ''}>Hold</option><option value="clear" ${overlay.stale === 'clear' ? 'selected' : ''}>Clear after lease</option></select></label>${componentControls(overlay)}</div>`;
      const update = (event) => { const field = event.target.dataset.field; const parameter = event.target.dataset.param; if (field) overlay[field] = field === 'enabled' ? event.target.checked : event.target.value; if (parameter) { const control = choice.controls.find((item) => item.id === parameter); overlay.parameters[parameter] = readParameter(control, event.target); } if (field || parameter) edit(); };
      row.addEventListener('input', update);
      row.addEventListener('change', update);
      row.addEventListener('click', (event) => { const action = event.target.dataset.action; if (!action) return; if (action === 'remove') state.overlays.splice(index, 1); if (action === 'up') [state.overlays[index - 1], state.overlays[index]] = [state.overlays[index], state.overlays[index - 1]]; if (action === 'down') [state.overlays[index + 1], state.overlays[index]] = [state.overlays[index], state.overlays[index + 1]]; edit(); render(); });
      list.append(row);
    });
  }
  function authoredLook(look) { const scene=look.scene; const authored={schema:'ledgrid.scene.v1',background:scene.background,overlays:scene.overlays,master_brightness:scene.master_brightness}; if(scene.vibe_source==='custom') authored.custom={palette_id:scene.palette_id,wall_pace:scene.wall_pace,presentation_luminance:scene.presentation_luminance}; else authored.vibe=scene.vibe_source; return {origin:'composer',scene:authored}; }
  function recoveryDraft(value) { return value.scene.vibe_source ? authoredLook({scene:value.scene}) : value; }
  function starterDraft(value, preservePresentation = false) { const candidate=preservePresentation ? draft() : {origin:'composer',scene:{schema:'ledgrid.scene.v1',vibe:'quiet',master_brightness:1}}; candidate.scene.background=value.background; candidate.scene.overlays=value.overlays; return candidate; }
  function assignDraft(value) { const scene=value.scene; const p=scene.background.parameters; $('#curtainDensity').value=p.curtain_density; $('#foldDepth').value=p.fold_depth; $('#glowIntensity').value=p.glow_intensity; state.background={seed:p.seed,source_fps:p.source_fps}; state.presentationMode=scene.vibe ? 'vibe':'custom'; if(scene.vibe){$('#vibe').value=scene.vibe;setVibeDefaults();} if(scene.custom){$('#previewPalette').value=scene.custom.palette_id;$('#wallPace').value=scene.custom.wall_pace;$('#sceneLuminance').value=scene.custom.presentation_luminance;} state.overlays=scene.overlays.map((o)=>({slot_id:o.slot_id,component_id:o.component.component_id,enabled:o.enabled,opacity:o.opacity,strip:o.placement.strip_translation,led:o.placement.led_translation,stale:o.stale_policy.policy==='hold'?'hold':'clear',parameters:{...o.component.parameters}})); render(); }
  async function applyDraft(value, options = {}) { assignDraft(value); const preview=await queuePreview(value, options); if(!preview) throw new Error('Draft could not restore.'); return preview; }
  function status(payload) { const data = payload.status || payload; $('#desiredIdentity').textContent = data.desired ? identity(data.desired) : 'Not checked'; $('#observedIdentity').textContent = identity(data.observed); $('#reconcileState').textContent = data.state[0].toUpperCase() + data.state.slice(1); $('#reconcileState').dataset.state = data.state; if (data.rejection) $('#liveMessage').textContent = data.rejection; }
  async function stopScene() { const button=$('#stopScene'); button.disabled=true; $('#liveMessage').textContent='Stopping the observed scene…'; try { const statusBody=await (await fetch(`${api}/status`)).json(); if (!statusBody.observed) throw new Error('Nothing is live to stop.'); const response=await fetch(`${api}/stop`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({basis:statusBody.observed})}); const body=await response.json(); status(body); if (!response.ok) throw new Error(body.error); state.checked=null; state.activationKey=null; $('#liveMessage').textContent='Stopped safely in the local adapter.'; } catch(error) { $('#liveMessage').textContent=`${error.message || 'Stop was not acknowledged.'} Retry once.`; } finally { button.disabled=false; } }
  async function looks(path = '', options = {}) { const response = await fetch(`${api}/looks${path}`, options); const body = await response.json(); if (!response.ok) throw new Error(body.error); return body; }
  async function starters(path = '', options = {}) { const response = await fetch(`${api}/starters${path}`, options); const body = await response.json(); if (!response.ok) throw new Error(body.error); return body; }
  async function library(path = '', options = {}) { const response = await fetch(`${api}/library${path}`, options); const body = await response.json(); if (!response.ok) throw new Error(body.error || 'Library is unavailable.'); return body; }
  function setLibrary(value) { state.library = value; renderLibrary(); }
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
    if (item.kind === 'starter') {
      const value=(await starters(`/${item.id}`)).starter; const candidate=starterDraft(value, true); const preview=await validatePreview(candidate);
      assignDraft(candidate); setReference(reference('starter', value.id, preview.basis, candidate)); markDraftLocal(); commitPreview(preview); await autosave(preview, candidate); await recordRecent(item); $('#starterMessage').textContent=`Previewing ${value.name}.`;
      return;
    }
    const result=await looks(`/${item.id}`); const candidate=authoredLook(result.look); setReference(reference('look', result.look.id, result.look.basis)); assignDraft(candidate); markDraftLocal(); render(); const preview=await queuePreview();
    if(!preview || !sameBasis(preview.basis, result.look.basis)) throw new Error('Saved look preview no longer matches; recreate it.');
    await recordRecent(item);
  }
  function renderLibrary() {
    const target=$('#libraryList'); if (!target) return; target.replaceChildren(); const items=filteredLibrary(); $('#libraryEmpty').hidden=items.length > 0;
    items.forEach((item) => { const row=document.createElement('li'); row.className='overlay library-item'; const title=document.createElement('strong'); title.textContent=item.name; const kind=document.createElement('span'); kind.className='overlay-slot'; kind.textContent=item.kind === 'starter' ? ' · Starting point' : ' · My look'; const open=document.createElement('button'); open.className='button secondary'; open.type='button'; open.textContent=item.kind === 'starter' ? 'Preview' : 'Open'; const favorite=document.createElement('button'); favorite.className='button text'; favorite.type='button'; favorite.textContent=item.favorite ? 'Remove favorite' : 'Favorite'; const actions=document.createElement('div'); actions.append(open, favorite); const line=document.createElement('div'); line.className='overlay-top'; const label=document.createElement('div'); label.append(title, kind); line.append(label, actions); row.append(line);
      favorite.addEventListener('click', async () => { try { await toggleFavorite(item); } catch(error) { $('#starterMessage').textContent=error.message || 'Favorite could not be updated.'; } });
      open.addEventListener('click', async () => { try { await openLibraryItem(item); } catch(error) { $('#starterMessage').textContent=error.message || 'This item could not be opened.'; } }); target.append(row); });
  }
  function renderStarters() { renderLibrary(); }
  function copyName(name) { let candidate = `${name} copy`; let suffix = 2; const names = new Set(state.looks.map((look) => look.name.toLocaleLowerCase())); while (names.has(candidate.toLocaleLowerCase())) candidate = `${name} copy ${suffix++}`; return candidate; }
  function renderLooks() { const target = $('#lookList'); target.replaceChildren(); $('#lookEmpty').hidden = state.looks.length > 0; state.looks.forEach((look) => { const row = document.createElement('li'); row.className = 'overlay'; row.innerHTML = `<div class="overlay-top"><input class="look-row-name" maxlength="80"><div><button class="button text" data-look="open">Open</button><button class="button text" data-look="duplicate">Duplicate</button><button class="button text" data-look="rename">Rename</button><button class="button text" data-look="delete">${state.deleteLookId === look.id ? 'Confirm delete' : 'Delete'}</button></div></div>`; row.querySelector('input').value = look.name; row.addEventListener('click', async (event) => { const action = event.target.dataset.look; if (!action) return; if (action !== 'delete') state.deleteLookId = null; try { if (action === 'duplicate') await looks(`/${look.id}/duplicate`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:copyName(look.name)})}); if (action === 'rename') await looks(`/${look.id}`, {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:row.querySelector('input').value})}); if (action === 'delete') { if (state.deleteLookId !== look.id) { state.deleteLookId = look.id; renderLooks(); return; } await looks(`/${look.id}`, {method:'DELETE'}); state.deleteLookId = null; } if (action === 'open') await openLibraryItem({kind:'look', id:look.id, name:look.name}); state.looks=(await looks()).looks; renderLooks(); await refreshLibrary(); } catch(error) { $('#lookMessage').textContent=error.message||'Saved look could not be changed.'; } }); target.append(row); }); }
  async function goLive() {
    const button = $('#goLive'); button.disabled = true; $('#liveMessage').textContent = 'Checking this exact local draft…';
    try {
      if (state.reference) await preflightLibrary(state.reference);
      if (!state.checked) {
        const checked = await fetch(`${api}/check`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(draft()) });
        const checkData = await checked.json();
        if (!checked.ok) { status(checkData); throw new Error(checkData.error); }
        state.checked = checkData; status(checkData);
      }
      $('#liveMessage').textContent = state.activationKey ? 'Retrying the exact checked scene…' : 'Sending checked scene to the local adapter…';
      const activated = await fetch(`${api}/activate`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ token: state.checked.token, basis: state.checked.basis, idempotency_key: state.activationKey || (state.activationKey = crypto.randomUUID()) }) });
      const activationData = await activated.json();
      if (!activated.ok) { status(activationData); throw new Error(activationData.error); }
      status(activationData); if (state.reference) await recordRecent(state.reference); $('#liveMessage').textContent = activationData.exact_retry ? 'Exact retry confirmed by the local adapter.' : 'Local adapter observed the exact checked scene.';
    } catch (error) { $('#liveMessage').textContent = error.message || 'The draft was not accepted.'; }
    finally { button.disabled = false; }
  }
  function renderStarterChoices(items) { const target=$('#starterList'); target.replaceChildren(); items.forEach((starter)=>{ const button=document.createElement('button'); button.className='button secondary'; button.textContent=starter.name; button.addEventListener('click',async()=>{ const generation=++state.previewGeneration; try { const value=(await starters(`/${starter.id}`)).starter; const candidate=starterDraft(value, true); const preview=await validatePreview(candidate); if (generation !== state.previewGeneration) return; assignDraft(candidate); setReference(reference('starter', value.id, preview.basis, candidate)); markDraftLocal(); commitPreview(preview); await autosave(preview, candidate); $('#starterMessage').textContent=`Previewing ${value.name}.`; } catch(error) { if (generation === state.previewGeneration) $('#starterMessage').textContent=error.message; } }); target.append(button); }); }
  $('#addOverlay').addEventListener('click', () => { const slot = $('#overlayChoice').value || Object.keys(defaults).find((candidate) => !state.overlays.some((item) => item.slot_id === candidate)); if (!slot || !defaults[slot]) return; state.overlays.push({...defaults[slot], parameters: structuredClone(defaults[slot].parameters)}); edit(); render(); });
  async function makeSavedReference(result, candidate) { setReference(reference('look', result.look.id, result.look.basis)); if (!state.committedPreview || !sameBasis(state.committedPreview.basis, result.look.basis)) await queuePreview(candidate); else await autosave(state.committedPreview, candidate); }
  $('#saveLook').addEventListener('click', async () => { try { const candidate=draft(); const result=await looks('', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:$('#lookName').value,draft:candidate})}); await makeSavedReference(result, candidate); $('#lookName').value=''; state.looks=(await looks()).looks; renderLooks(); await refreshLibrary(); $('#lookMessage').textContent=`Saved ${result.look.name}.`; } catch (error) { $('#lookMessage').textContent=error.message || 'Saved look could not be created.'; } });
  $('#remixStarter').addEventListener('click', async () => { if (!state.starterId || !$('#remixName').value) return; try { const candidate=draft(); const result=await starters(`/${state.starterId}/remix`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:$('#remixName').value,draft:candidate})}); await makeSavedReference(result, candidate); $('#remixName').value=''; state.looks=(await looks()).looks; renderLooks(); await refreshLibrary(); $('#starterMessage').textContent='Saved your remix.'; } catch(error) { $('#starterMessage').textContent=error.message; } });
  document.querySelectorAll('.composer input, .composer select').forEach((input) => { if (!['vibe', 'previewPalette', 'wallPace', 'sceneLuminance', 'librarySearch'].includes(input.id)) input.addEventListener('change', edit); });
  $('#vibe').addEventListener('change', () => { state.presentationMode = 'vibe'; setVibeDefaults(); edit(); });
  ['#previewPalette', '#wallPace', '#sceneLuminance'].forEach((selector) => $(selector).addEventListener('change', () => { state.presentationMode = 'custom'; edit(); }));
  async function auroraFallback() { const result=await starters('/aurora'); const candidate=starterDraft(result.starter); const preview=await validatePreview(candidate); return {candidate, reference:reference('starter', result.starter.id, preview.basis, candidate), preview}; }
  async function discardTarget(record) { const ref=record.reference; if (ref.kind === 'look') { try { const result=await looks(`/${ref.id}`); return {candidate:authoredLook(result.look), reference:reference('look', result.look.id, result.look.basis)}; } catch (_) {} } if (ref.kind === 'starter' && ref.baseline) { const candidate=recoveryDraft(ref.baseline); const preview=await validatePreview(candidate); return {candidate, reference:ref, preview}; } return auroraFallback(); }
  $('#restoreDraft').addEventListener('click', async () => { try { if (!state.recovery) throw new Error('This recovery can only be discarded.'); setReference(state.recovery.reference); await applyDraft(recoveryDraft(state.recovery.draft), {autosave:false}); setUnsaved(true); $('#recoveryCard').hidden=true; } catch(error) { $('#recoveryMessage').textContent=error.message; } });
  $('#discardDraft').addEventListener('click', async () => { try { const next=state.recovery ? await discardTarget(state.recovery) : await auroraFallback(); const preview=next.preview || await validatePreview(next.candidate); if (!sameBasis(preview.basis, next.reference.basis)) throw new Error('Referenced look no longer matches; use Aurora only.'); assignDraft(next.candidate); commitPreview(preview); setReference(next.reference); setUnsaved(false); await draftRequest('', {method:'DELETE'}); state.recovery=null; $('#recoveryCard').hidden=true; } catch(error) { $('#recoveryMessage').textContent=error.message; } });
  $('#librarySearch').addEventListener('input', (event) => { state.libraryQuery=event.target.value; renderLibrary(); }); document.querySelectorAll('[data-library-filter]').forEach((button) => button.addEventListener('click', () => { state.libraryFilter=button.dataset.libraryFilter; document.querySelectorAll('[data-library-filter]').forEach((item) => item.classList.toggle('active', item === button)); renderLibrary(); }));
  $('#goLive').addEventListener('click', goLive); $('#stopScene').addEventListener('click', stopScene); render(); queuePreview(); fetch(`${api}/status`).then((response)=>response.json()).then(status); fetch(`${api}/draft`).then(async (response)=>{ const body=await response.json(); if(body.draft){state.recovery=body.draft; $('#restoreDraft').disabled=false; $('#recoveryCard').hidden=false;} else if(!response.ok){$('#restoreDraft').disabled=true;$('#recoveryCard').hidden=false;$('#recoveryMessage').textContent=body.error || 'Working draft can only be discarded.';} }); looks().then((result) => { state.looks=result.looks; renderLooks(); }).catch((error) => { $('#lookMessage').textContent=error.message; }); refreshLibrary().catch((error)=>{ $('#starterMessage').textContent=error.message; }); fetch(`${api}/components`).then(async (response) => { const body = await response.json(); if (!response.ok) throw new Error(body.error || 'Overlay choices are unavailable.'); return body; }).then((body) => { state.components = body.choices; body.choices.forEach((choice) => { defaults[choice.slot_id] = { slot_id: choice.slot_id, component_id: choice.component_id, enabled: true, opacity: choice.component_id === 'conway_life' ? 190 : 208, strip: 0, led: choice.component_id === 'clock_overlay' ? -8 : 0, stale: 'hold', parameters: structuredClone(choice.parameters) }; }); render(); }).catch((error) => { $('#previewStatus').textContent = error.message; });
})();
