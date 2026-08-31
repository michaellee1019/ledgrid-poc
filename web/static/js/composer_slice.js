(() => {
  'use strict';
  const root = document.querySelector('.composer');
  const api = root.dataset.apiRoot;
  const list = document.querySelector('#overlayList');
  const state = { overlays: [], checked: null, activationKey: null, presentationMode: 'vibe', previewGeneration: 0 };
  const defaults = {
    conway_lower: { slot_id: 'conway_lower', component_id: 'conway_life', enabled: true, opacity: 190, strip: 0, led: 0, stale: 'hold', parameters: { seed: 1971, rule: 'B3/S23', initial_density: 0.14, generations_per_second: 5.0 } },
    clock_upper: { slot_id: 'clock_upper', component_id: 'clock_overlay', enabled: true, opacity: 208, strip: 0, led: -8, stale: 'hold', parameters: { show_seconds: true, color: [255, 224, 128] } },
  };
  const $ = (selector) => document.querySelector(selector);
  const identity = (item) => item ? `r${item.revision} · ${item.digest}` : 'No acknowledgement';
  function draft() {
    const presentation = state.presentationMode === 'vibe'
      ? { vibe: $('#vibe').value }
      : { custom: { palette_id: $('#previewPalette').value, wall_pace: Number($('#wallPace').value), presentation_luminance: Number($('#sceneLuminance').value) } };
    return { origin: 'composer', scene: {
      schema: 'ledgrid.scene.v1', ...presentation, master_brightness: 1,
      background: { slot_id: 'background', component_id: 'aurora_curtains', version: 1, provider: 'python', role: 'background', parameters: {
        curtain_density: Number($('#curtainDensity').value), fold_depth: Number($('#foldDepth').value), glow_intensity: Number($('#glowIntensity').value), source_fps: 30, seed: 4201,
      } },
      overlays: state.overlays.map((overlay) => ({
        slot_id: overlay.slot_id, component: { component_id: overlay.component_id, version: 1, provider: 'python', role: 'overlay', parameters: overlay.parameters },
        enabled: overlay.enabled, opacity: Number(overlay.opacity), placement: { strip_translation: Number(overlay.strip), led_translation: Number(overlay.led), clip_policy: 'clip_to_wall' },
        stale_policy: overlay.stale === 'hold' ? { policy: 'hold' } : { policy: 'clear_after_lease', lease_ms: 1200 },
      })),
    } };
  }
  function edit() { state.checked = null; state.activationKey = null; $('#desiredIdentity').textContent = 'Not checked'; $('#reconcileState').textContent = 'Pending'; $('#reconcileState').dataset.state = 'pending'; $('#liveMessage').textContent = 'Draft is local.'; queuePreview(); }
  function setVibeDefaults() { const values = { quiet: ['mist', .70, .82], neutral: ['neutral', 1, 1], vivid: ['spectrum', 1.25, 1.15] }[$('#vibe').value]; $('#previewPalette').value = values[0]; $('#wallPace').value = values[1]; $('#sceneLuminance').value = values[2]; }
  function drawPreview(frame) {
    if (!frame || frame.encoding !== 'rgb_u8_base64' || frame.width !== 33 || frame.height !== 138 || frame.orientation !== 'strip_major_led_zero_bottom') throw new Error('Preview returned an unsupported frame.');
    const bytes = Uint8Array.from(atob(frame.pixels), (character) => character.charCodeAt(0));
    if (bytes.length !== frame.width * frame.height * 3) throw new Error('Preview frame size is invalid.');
    const canvas = $('#scenePreview'); const context = canvas.getContext('2d'); const image = context.createImageData(frame.width, frame.height);
    for (let strip = 0; strip < frame.width; strip += 1) for (let led = 0; led < frame.height; led += 1) { const source = (strip * frame.height + led) * 3; const target = ((frame.height - 1 - led) * frame.width + strip) * 4; image.data[target] = bytes[source]; image.data[target + 1] = bytes[source + 1]; image.data[target + 2] = bytes[source + 2]; image.data[target + 3] = 255; }
    context.putImageData(image, 0, 0);
  }
  async function queuePreview() {
    const generation = ++state.previewGeneration; $('#previewStatus').textContent = 'Rendering this local draft…';
    try { const response = await fetch(`${api}/preview`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(draft()) }); const body = await response.json(); if (generation !== state.previewGeneration) return; if (!response.ok) throw new Error(body.error || 'Preview could not render.'); drawPreview(body.frame); $('#previewIdentity').textContent = identity(body.basis); $('#previewStatus').textContent = 'Current local runtime frame · no wall change.'; }
    catch (error) { if (generation === state.previewGeneration) { $('#previewIdentity').textContent = 'Preview unavailable'; $('#previewStatus').textContent = error.message || 'Preview could not render.'; } }
  }
  function render() {
    list.replaceChildren(); $('#overlayEmpty').hidden = state.overlays.length > 0; $('#addOverlay').disabled = state.overlays.length >= 2;
    state.overlays.forEach((overlay, index) => {
      const row = document.createElement('li'); row.className = 'overlay';
      const title = overlay.component_id === 'conway_life' ? 'Conway Life' : 'Clock Overlay';
      const lifeControls = overlay.component_id === 'conway_life'
        ? `<label>Seed <input data-param="seed" type="number" min="0" max="999999" value="${overlay.parameters.seed}"></label><label>Rule <select data-param="rule"><option value="B3/S23" ${overlay.parameters.rule === 'B3/S23' ? 'selected' : ''}>B3/S23</option><option value="B36/S23" ${overlay.parameters.rule === 'B36/S23' ? 'selected' : ''}>B36/S23</option></select></label>`
        : '';
      row.innerHTML = `<div class="overlay-top"><div><span class="overlay-title">${title}</span><span class="overlay-slot"> · slot: ${overlay.slot_id}</span></div><div><button class="button text" data-action="up" ${index === 0 ? 'disabled' : ''}>Up</button><button class="button text" data-action="down" ${index === state.overlays.length - 1 ? 'disabled' : ''}>Down</button><button class="button text" data-action="remove">Remove</button></div></div><div class="overlay-controls"><label class="enabled"><input data-field="enabled" type="checkbox" ${overlay.enabled ? 'checked' : ''}> Enabled</label><label>Opacity <input data-field="opacity" type="number" min="0" max="255" value="${overlay.opacity}"></label><label>Across <input data-field="strip" type="number" value="${overlay.strip}"></label><label>Down <input data-field="led" type="number" value="${overlay.led}"></label><label>Stale frames <select data-field="stale"><option value="hold" ${overlay.stale === 'hold' ? 'selected' : ''}>Hold</option><option value="clear" ${overlay.stale === 'clear' ? 'selected' : ''}>Clear after lease</option></select></label>${lifeControls}</div>`;
      row.addEventListener('input', (event) => { const field = event.target.dataset.field; const parameter = event.target.dataset.param; if (field) overlay[field] = field === 'enabled' ? event.target.checked : event.target.value; if (parameter) overlay.parameters[parameter] = parameter === 'seed' ? Number(event.target.value) : event.target.value; if (!field && !parameter) return; edit(); });
      row.addEventListener('change', (event) => { const field = event.target.dataset.field; const parameter = event.target.dataset.param; if (field) overlay[field] = field === 'enabled' ? event.target.checked : event.target.value; if (parameter) overlay.parameters[parameter] = parameter === 'seed' ? Number(event.target.value) : event.target.value; if (field || parameter) edit(); });
      row.addEventListener('click', (event) => { const action = event.target.dataset.action; if (!action) return; if (action === 'remove') state.overlays.splice(index, 1); if (action === 'up') [state.overlays[index - 1], state.overlays[index]] = [state.overlays[index], state.overlays[index - 1]]; if (action === 'down') [state.overlays[index + 1], state.overlays[index]] = [state.overlays[index], state.overlays[index + 1]]; edit(); render(); });
      list.append(row);
    });
  }
  function status(payload) { const data = payload.status || payload; $('#desiredIdentity').textContent = data.desired ? identity(data.desired) : 'Not checked'; $('#observedIdentity').textContent = identity(data.observed); $('#reconcileState').textContent = data.state[0].toUpperCase() + data.state.slice(1); $('#reconcileState').dataset.state = data.state; if (data.rejection) $('#liveMessage').textContent = data.rejection; }
  async function goLive() {
    const button = $('#goLive'); button.disabled = true; $('#liveMessage').textContent = 'Checking this exact local draft…';
    try {
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
      status(activationData); $('#liveMessage').textContent = activationData.exact_retry ? 'Exact retry confirmed by the local adapter.' : 'Local adapter observed the exact checked scene.';
    } catch (error) { $('#liveMessage').textContent = error.message || 'The draft was not accepted.'; }
    finally { button.disabled = false; }
  }
  $('#addOverlay').addEventListener('click', () => { const slot = Object.keys(defaults).find((candidate) => !state.overlays.some((item) => item.slot_id === candidate)); if (!slot) return; state.overlays.push({...defaults[slot], parameters: {...defaults[slot].parameters}}); edit(); render(); });
  document.querySelectorAll('.composer input, .composer select').forEach((input) => { if (!['vibe', 'previewPalette', 'wallPace', 'sceneLuminance'].includes(input.id)) input.addEventListener('change', edit); });
  $('#vibe').addEventListener('change', () => { state.presentationMode = 'vibe'; setVibeDefaults(); edit(); });
  ['#previewPalette', '#wallPace', '#sceneLuminance'].forEach((selector) => $(selector).addEventListener('change', () => { state.presentationMode = 'custom'; edit(); }));
  $('#goLive').addEventListener('click', goLive); render(); queuePreview();
})();
