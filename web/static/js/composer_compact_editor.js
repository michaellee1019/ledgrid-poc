// Presentation only: retain the existing controls, event handlers and editor scroll.
(() => {
  'use strict';
  const $ = (selector) => document.querySelector(selector);
  const dock = $('.inspector-dock'); const save = $('.scene-save-controls');
  if (!dock || !save || !window.ComposerEditorContext) return;
  const snapshot = () => window.ComposerEditorContext.snapshot();
  const button = (name) => { const node = document.createElement('button'); node.type = 'button'; node.className = 'button secondary'; node.textContent = name; return node; };
  const show = (dialog, focus = null) => {
    const prior = document.activeElement;
    dialog.showModal(); (focus || dialog.querySelector('button,input,summary'))?.focus({preventScroll: true});
    dialog.addEventListener('close', () => prior?.focus({preventScroll: true}), {once: true});
  };
  const details = document.createElement('dialog'); details.id = 'editorSceneDetails'; details.setAttribute('aria-labelledby', 'editor-details-title');
  const heading = document.createElement('h2'); heading.id = 'editor-details-title'; heading.textContent = 'Scene details & comparison';
  const close = button('Back to editing'); close.addEventListener('click', () => details.close());
  details.append(heading, close);
  const originalSave = $('#saveScene'); const stateChip = $('#saveState'); const working = $('#workingSceneContext');
  const feedbackNode = $('#saveFeedback');
  [...save.children].filter((node) => ![stateChip, working, feedbackNode].includes(node)).forEach((node) => details.append(node));
  const nameField = details.querySelector('#sceneName');
  const detailsSave = button('Save scene'); detailsSave.addEventListener('click', () => originalSave.click());
  const modalFeedback = document.createElement('p'); modalFeedback.id = 'editorSaveFeedback'; modalFeedback.className = 'save-feedback status-line'; modalFeedback.hidden = true; modalFeedback.setAttribute('role', 'status'); modalFeedback.setAttribute('aria-live', 'polite'); modalFeedback.setAttribute('aria-atomic', 'true');
  nameField.setAttribute('aria-describedby', modalFeedback.id);
  details.append(detailsSave, modalFeedback);
  const compareTitle = document.createElement('h3'); compareTitle.textContent = 'Current versus saved';
  const compareStatus = document.createElement('p'); compareStatus.className = 'status-line';
  const differences = document.createElement('ul'); differences.id = 'editorSceneChanges';
  const retry = button('Retry saved comparison'); retry.hidden = true;
  details.prepend(heading, close, modalFeedback, compareTitle, compareStatus, retry, differences); document.body.append(details);
  const context = document.createElement('div'); context.className = 'editor-scene-heading';
  const identity = working || document.createElement('p'); if (!working) identity.id = 'editorSceneTitle';
  const titleAndState = document.createElement('div');
  const savedTitle = document.createElement('strong'); savedTitle.id = 'editorSavedTitle';
  titleAndState.append(savedTitle, identity, stateChip);
  context.append(titleAndState, originalSave);
  const navigation = document.createElement('div'); navigation.className = 'editor-group-navigation';
  const jump = document.createElement('select'); jump.id = 'editorControlGroup'; jump.setAttribute('aria-label', 'Jump to controls');
  [['look', 'Basics'], ['animation', 'Animation'], ['plants', 'Plants'], ['widgets', 'Clock & message'], ['background', 'Background']].forEach(([id, name]) => jump.append(new Option(name, id)));
  const inspect = button('Scene details'); inspect.setAttribute('aria-haspopup', 'dialog'); inspect.setAttribute('aria-controls', details.id);
  navigation.append(jump, inspect); save.replaceChildren(context, navigation, feedbackNode);
  save.classList.add('compact-editor-context');
  inspect.addEventListener('click', () => { show(details); const current = snapshot(); void loadBaseline(current.selection?.kind === 'look' ? current.selection.id : null); });
  originalSave.addEventListener('click', (event) => {
    if (!nameField.value.trim() && snapshot().selection?.kind !== 'look' && !details.open) { event.stopImmediatePropagation(); show(details, nameField); }
  }, {capture: true});
  // Native modal focus containment requires closing details before another
  // existing dialog (Save a copy) or the Saved workspace is opened.
  ['#saveAsScene', '#openScene'].forEach((selector) => $(selector).addEventListener('click', () => {
    details.close();
    if (selector === '#saveAsScene') $('#sceneCopyDialog').addEventListener('close', () => inspect.focus({preventScroll: true}), {once: true});
  }, {capture: true}));
  jump.addEventListener('change', () => {
    const id = jump.value; window.ComposerPanelLayout.expand(id);
    const target = $(`[data-panel-id="${id}"]`); if (!target) return;
    const heading = target.querySelector(':scope > .pane-heading');
    dock.scrollTop += target.getBoundingClientRect().top - dock.getBoundingClientRect().top - save.getBoundingClientRect().height - 8;
    if (heading) { heading.tabIndex = -1; heading.focus({preventScroll: true}); }
  });
  // Brightness and speed are the first two adjustments; palette remains nearby.
  const basics = $('[data-panel-id="look"] .field-grid');
  const brightness = $('#sceneLuminance').closest('label');
  basics.prepend(brightness); brightness.after($('.scene-speed-control'));
  $('#undoScene').title = 'Undo the last scene edit across all groups. Reset controls use factory defaults.';

  let saved = null; let savedId = null; let baselineState = ''; let fetchGeneration = 0; let lastSave = '';
  const words = (value) => String(value).replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
  const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);
  function changedRows(current, baseline, path = []) {
    if (same(current, baseline)) return [];
    if (current && baseline && typeof current === 'object' && typeof baseline === 'object' && !Array.isArray(current) && !Array.isArray(baseline)) {
      return [...new Set([...Object.keys(current), ...Object.keys(baseline)])].flatMap((key) => changedRows(current[key], baseline[key], [...path, key]));
    }
    if (path[0] === 'widgets' && Array.isArray(current) && Array.isArray(baseline)) {
      return [{label: 'Clock & message', current: widgetSummary(current), baseline: widgetSummary(baseline)}];
    }
    const key = path.join('.');
    const labels = {'look.presentation_brightness': 'Scene brightness', 'look.pace': 'Scene speed', 'look.palette_id': 'Palette', 'plants.effects.active': 'Plant lighting', 'plants.effects.strengths.illuminate': 'Illuminate strength', 'plants.effects.strengths.shadow': 'Shadow strength', 'plants.effects.strengths.hue_shift': 'Hue shift strength', 'animation.component_id': 'Animation'};
    if (key === 'plants.effects.active') return [{label: 'Plant lighting', current: current?.length ? current.map(words).join(', ') : 'Off', baseline: baseline?.length ? baseline.map(words).join(', ') : 'Off'}];
    const label = labels[key] || path.filter((part) => !['parameters', 'effects', 'strengths', 'look'].includes(part)).map(words).join(' · ');
    return [{label, current, baseline}];
  }
  function widgetSummary(widgets) {
    if (!widgets.length) return 'None';
    return widgets.map((widget) => {
      const clock = widget.component?.component_id === 'clock_overlay';
      return `${clock ? 'Clock' : 'Message'} ${widget.visible === false ? 'hidden' : 'shown'}, across ${widget.placement?.strip_translation ?? 0}, down ${widget.placement?.led_translation ?? 0}${Object.entries(widget.component?.parameters || {}).map(([key, value]) => `, ${({format_24h: '24-hour time', show_seconds: 'Seconds', clock_offset_minutes: 'Time offset (minutes)'})[key] || words(key)} ${valueText(value)}`).join('')}`;
    }).join('; ');
  }
  const valueText = (value) => value == null ? 'None' : typeof value === 'boolean' ? (value ? 'On' : 'Off') : typeof value === 'object' ? JSON.stringify(value) : String(value);
  function renderComparison() {
    if (!details.open) return;
    differences.replaceChildren(); retry.hidden = baselineState !== 'unavailable';
    const current = snapshot();
    if (!current.selection || current.selection.kind !== 'look') { compareStatus.textContent = 'No saved scene selected. Save a scene to establish a comparison.'; return; }
    if (!saved || savedId !== current.selection.id) { compareStatus.textContent = baselineState === 'unavailable' ? 'Saved comparison unavailable. Current controls remain editable.' : 'Loading saved comparison…'; return; }
    const rows = changedRows(current.scene, saved.scene);
    compareStatus.textContent = `Compared with saved scene ${saved.name}. ${rows.length ? 'Only changed settings are listed.' : 'Current values match the saved scene.'} Undo affects the last scene edit across all groups; Reset uses factory defaults.`;
    rows.forEach((row) => { const item = document.createElement('li'); item.textContent = `${row.label}: saved ${valueText(row.baseline)} → current ${valueText(row.current)}`; differences.append(item); });
  }
  async function loadBaseline(id) {
    const generation = ++fetchGeneration; saved = null; savedId = id; baselineState = 'loading'; renderComparison();
    if (!id) { baselineState = ''; renderComparison(); return; }
    try {
      const response = await fetch(`/api/composer/looks/${encodeURIComponent(id)}`, {cache: 'no-store', signal: AbortSignal.timeout(5000)});
      const body = await response.json();
      if (!response.ok || !body.look || body.look.id !== id) throw new Error('Saved comparison unavailable');
      if (generation !== fetchGeneration || snapshot().selection?.id !== id) return;
      saved = body.look; baselineState = 'ready';
    } catch (_) { if (generation !== fetchGeneration) return; baselineState = 'unavailable'; }
    renderComparison();
  }
  retry.addEventListener('click', () => { void loadBaseline(snapshot().selection?.id); });
  function sync() {
    const current = snapshot();
    detailsSave.disabled = originalSave.disabled;
    if (modalFeedback.textContent !== feedbackNode.textContent) modalFeedback.textContent = feedbackNode.textContent;
    modalFeedback.hidden = feedbackNode.hidden; modalFeedback.dataset.state = feedbackNode.dataset.state || '';
    const sceneTitle = current.selection?.name || nameField.value.trim() || 'Untitled scene';
    if (savedTitle.textContent !== sceneTitle) { savedTitle.textContent = sceneTitle; savedTitle.title = sceneTitle; }
    if (!working) {
      const text = `Editing ${current.selection?.name || nameField.value.trim() || $('#animationChoice').selectedOptions[0]?.textContent || 'Untitled scene'}`;
      if (identity.textContent !== text) identity.textContent = text;
    }
    const id = current.selection?.kind === 'look' ? current.selection.id : null;
    const feedback = $('#saveFeedback'); const success = feedback.dataset.state === 'success' && !feedback.hidden ? feedback.textContent : '';
    if (id !== savedId || (success && success !== lastSave)) { lastSave = success; void loadBaseline(id); }
    else { if (!success) lastSave = ''; renderComparison(); }
  }
  let scheduled = false;
  const schedule = () => { if (!scheduled) { scheduled = true; queueMicrotask(() => { scheduled = false; sync(); }); } };
  new MutationObserver(schedule).observe(originalSave, {attributes: true, attributeFilter: ['disabled']});
  new MutationObserver(schedule).observe(stateChip, {childList: true, characterData: true, subtree: true});
  new MutationObserver(schedule).observe($('#saveFeedback'), {childList: true, attributes: true, subtree: true});
  document.addEventListener('input', schedule); document.addEventListener('change', schedule);
  details.addEventListener('toggle', schedule); details.addEventListener('close', schedule);
  // Enlarge the same canvas: its renderer and playback state remain untouched.
  const previewDialog = document.createElement('dialog'); previewDialog.id = 'editorLargePreview'; previewDialog.setAttribute('aria-label', 'Larger scene preview');
  const previewClose = button('Back to controls'); previewClose.addEventListener('click', () => previewDialog.close());
  previewDialog.append(previewClose); document.body.append(previewDialog);
  const enlarge = button('↗'); enlarge.classList.add('editor-enlarge-preview'); enlarge.setAttribute('aria-label', 'Enlarge preview');
  $('.preview-pane > .pane-heading').append(enlarge);
  enlarge.addEventListener('click', () => {
    const canvas = $('#scenePreview'); const parent = canvas.parentElement; const next = canvas.nextSibling;
    const status = $('#previewStatus'); const statusParent = status.parentElement; const statusNext = status.nextSibling;
    previewDialog.append(canvas, status); show(previewDialog, previewClose);
    previewDialog.addEventListener('close', () => { parent.insertBefore(canvas, next); statusParent.insertBefore(status, statusNext); }, {once: true});
  });
  sync();
})();
