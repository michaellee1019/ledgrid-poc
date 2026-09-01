/* Disposable desktop palette arrangement.  This never reads or writes Composer scene state. */
(() => {
  'use strict';
  const desktop = window.matchMedia('(min-width: 761px)');
  const board = document.querySelector('.inspectors');
  if (!board) return;

  const palettes = Object.freeze([
    ['background', 'Background'], ['animation', 'Animation'], ['widgets', 'Widgets'], ['plants', 'Plants'], ['look', 'Look'],
  ]);
  const ids = new Set(palettes.map(([id]) => id));
  const nodes = new Map(palettes.map(([id]) => [id, document.querySelector(`#${id}-title`)?.closest('.inspector')]));
  if ([...nodes.values()].some((node) => !node)) return;
  const original = palettes.map(([id]) => nodes.get(id));
  const storageKey = 'ledgrid.composer.desktop-palette-layout.v1';
  // Two generous opening columns keep showpiece controls legible on ordinary
  // laptop screens; every palette can still be unstacked on demand.
  const defaultLayout = () => ({groups: [['background', 'look'], ['animation', 'widgets', 'plants']], hidden: [], collapsed: {}, heights: {}, widths: [1, 1.2]});
  let layout = defaultLayout();
  let draggedId = null;
  let boardControls;
  let announce;
  let resizeSession;

  function clamp(value, min, max) { return Math.min(max, Math.max(min, value)); }
  function clone(value) { return JSON.parse(JSON.stringify(value)); }
  function sanitize(raw) {
    const next = defaultLayout();
    if (!raw || typeof raw !== 'object') return next;
    const hidden = Array.isArray(raw.hidden) ? raw.hidden.filter((id, index, all) => ids.has(id) && all.indexOf(id) === index) : [];
    const seen = new Set(hidden);
    const groups = Array.isArray(raw.groups) ? raw.groups.map((group) => {
      if (!Array.isArray(group)) return [];
      return group.filter((id) => {
        if (!ids.has(id) || seen.has(id)) return false;
        seen.add(id);
        return true;
      });
    }).filter((group) => group.length) : [];
    palettes.forEach(([id]) => { if (!seen.has(id)) groups.push([id]); });
    next.groups = groups;
    next.hidden = hidden;
    next.collapsed = Object.fromEntries(palettes.map(([id]) => [id, Boolean(raw.collapsed?.[id])]));
    next.heights = Object.fromEntries(palettes.map(([id]) => [id, clamp(Number(raw.heights?.[id]) || 0, 180, 900)]).filter(([, height]) => height));
    next.widths = groups.map((_, index) => clamp(Number(raw.widths?.[index]) || 1, .65, 2.2));
    return next;
  }
  function load() { try { layout = sanitize(JSON.parse(localStorage.getItem(storageKey))); } catch (_) { layout = defaultLayout(); } }
  function save() { try { localStorage.setItem(storageKey, JSON.stringify(layout)); } catch (_) { /* Layout is intentionally disposable. */ } }
  function say(message) { if (announce) announce.textContent = message; }
  function title(id) { return palettes.find(([key]) => key === id)?.[1] || id; }
  function groupFor(id) { return layout.groups.findIndex((group) => group.includes(id)); }
  function remove(id) {
    const group = groupFor(id);
    if (group < 0) return;
    layout.groups[group] = layout.groups[group].filter((value) => value !== id);
    if (!layout.groups[group].length) { layout.groups.splice(group, 1); layout.widths.splice(group, 1); }
  }
  function focusAction(id, action) { requestAnimationFrame(() => document.querySelector(`[data-palette-id="${id}"][data-palette-action="${action}"]`)?.focus()); }

  function makeButton(label, glyph, action, id, extra = '') {
    const button = document.createElement('button');
    button.type = 'button'; button.className = `button text ${extra}`; button.dataset.paletteAction = action; button.dataset.paletteId = id;
    button.title = label; button.setAttribute('aria-label', label); button.textContent = glyph;
    return button;
  }
  function ensurePaletteFrame(id) {
    const node = nodes.get(id);
    node.classList.add('palette-shell');
    const heading = node.querySelector('.pane-heading');
    if (!heading.querySelector('.palette-actions')) {
      const actions = document.createElement('div'); actions.className = 'palette-actions'; actions.setAttribute('aria-label', `${title(id)} palette controls`);
      actions.append(
        makeButton('Drag to stack or move this palette', '⠿', 'drag', id, 'palette-grab'),
        makeButton('Move palette left', '←', 'left', id), makeButton('Move palette right', '→', 'right', id),
        makeButton('Stack this palette with the palette to its left', '⇲', 'stack', id), makeButton('Unstack into its own column', '⇱', 'unstack', id),
        makeButton('Make palette shorter', '−', 'smaller', id), makeButton('Make palette taller', '+', 'larger', id),
        makeButton('Collapse palette', '⌃', 'collapse', id), makeButton('Hide palette', '×', 'hide', id),
      );
      heading.append(actions);
      const resize = document.createElement('div'); resize.className = 'palette-resize-handle'; resize.tabIndex = 0; resize.role = 'separator'; resize.setAttribute('aria-orientation', 'horizontal'); resize.dataset.paletteResize = id; resize.setAttribute('aria-label', `Resize ${title(id)} palette height`); node.append(resize);
    }
    const grab = node.querySelector('[data-palette-action="drag"]');
    grab.draggable = true;
  }
  function action(id, actionName) {
    const current = groupFor(id);
    if (actionName === 'hide') { remove(id); layout.hidden.push(id); save(); render(); document.querySelector('#paletteChooser summary')?.focus(); say(`${title(id)} hidden. Use Palette drawer to restore it.`); return; }
    if (actionName === 'collapse') { layout.collapsed[id] = !layout.collapsed[id]; }
    if (actionName === 'smaller' || actionName === 'larger') { layout.heights[id] = clamp((layout.heights[id] || 300) + (actionName === 'larger' ? 80 : -80), 180, 900); }
    if (actionName === 'left' && current > 0) { const [group] = layout.groups.splice(current, 1); const [width] = layout.widths.splice(current, 1); layout.groups.splice(current - 1, 0, group); layout.widths.splice(current - 1, 0, width); }
    if (actionName === 'right' && current >= 0 && current < layout.groups.length - 1) { const [group] = layout.groups.splice(current, 1); const [width] = layout.widths.splice(current, 1); layout.groups.splice(current + 1, 0, group); layout.widths.splice(current + 1, 0, width); }
    if (actionName === 'stack' && current > 0) { remove(id); layout.groups[current - 1].push(id); }
    if (actionName === 'unstack' && current >= 0 && layout.groups[current].length > 1) { const position = layout.groups[current].indexOf(id); layout.groups[current].splice(position, 1); layout.groups.splice(current + 1, 0, [id]); layout.widths.splice(current + 1, 0, 1); }
    save(); render(); focusAction(id, actionName); say(`${title(id)} palette updated.`);
  }
  function restore(id) { layout.hidden = layout.hidden.filter((value) => value !== id); layout.groups.push([id]); layout.widths.push(1); save(); render(); focusAction(id, 'drag'); say(`${title(id)} restored.`); }
  function reset() { layout = defaultLayout(); try { localStorage.removeItem(storageKey); } catch (_) {} render(); focusAction('background', 'drag'); say('Palette layout reset to the showpiece arrangement.'); }
  function moveDrop(id, targetGroup, targetIndex) {
    const sourceGroup = groupFor(id);
    if (sourceGroup === targetGroup) {
      const sourceIndex = layout.groups[sourceGroup].indexOf(id);
      layout.groups[sourceGroup].splice(sourceIndex, 1);
      layout.groups[sourceGroup].splice(targetIndex > sourceIndex ? targetIndex - 1 : targetIndex, 0, id);
    } else {
      // The source can be an entire one-palette column. Resolve the target by
      // its remaining palette rather than its stale numeric index after removal.
      const targetAnchor = layout.groups[targetGroup].find((value) => value !== id);
      remove(id);
      const insertionGroup = layout.groups[groupFor(targetAnchor)];
      insertionGroup.splice(targetIndex, 0, id);
    }
    save(); render(); focusAction(id, 'drag'); say(`${title(id)} moved into a vertical palette stack.`);
  }
  function makeStack(group, groupIndex) {
    const stack = document.createElement('section'); stack.className = 'palette-stack'; stack.dataset.groupIndex = groupIndex; stack.style.setProperty('--stack-width', `${layout.widths[groupIndex] || 1}fr`); stack.setAttribute('aria-label', `Palette column ${groupIndex + 1}`);
    group.forEach((id) => { const node = nodes.get(id); ensurePaletteFrame(id); node.classList.toggle('is-collapsed', Boolean(layout.collapsed[id])); node.style.setProperty('--palette-height', `${layout.heights[id] || 900}px`); node.querySelector('[data-palette-action="collapse"]').textContent = layout.collapsed[id] ? '⌄' : '⌃'; node.querySelector('[data-palette-action="collapse"]').setAttribute('aria-label', `${layout.collapsed[id] ? 'Expand' : 'Collapse'} ${title(id)} palette`); stack.append(node); });
    return stack;
  }
  function gridColumns() { return layout.groups.map((_, index) => `minmax(170px, ${layout.widths[index] || 1}fr)`).join(' minmax(8px, 8px) '); }
  function updateColumnWidth(index, delta) {
    const total = board.getBoundingClientRect().width || 1;
    const shift = delta / total * layout.groups.length;
    layout.widths[index] = clamp((layout.widths[index] || 1) + shift, .65, 2.2);
    layout.widths[index + 1] = clamp((layout.widths[index + 1] || 1) - shift, .65, 2.2);
    board.style.gridTemplateColumns = gridColumns();
  }
  function updateHeight(id, delta) {
    layout.heights[id] = clamp((layout.heights[id] || 300) + delta, 180, 900);
    nodes.get(id).style.setProperty('--palette-height', `${layout.heights[id]}px`);
  }
  function makeControls() {
    boardControls = document.createElement('div'); boardControls.className = 'palette-arrangebar';
    const copy = document.createElement('p'); copy.textContent = 'Arrange your remix controls — drag to stack, or use the palette buttons.';
    const actions = document.createElement('div');
    const resetButton = document.createElement('button'); resetButton.type = 'button'; resetButton.className = 'button text'; resetButton.textContent = 'Reset layout'; resetButton.addEventListener('click', reset);
    const chooser = document.createElement('details'); chooser.id = 'paletteChooser'; chooser.className = 'palette-chooser'; const summary = document.createElement('summary'); summary.textContent = 'Palettes'; const list = document.createElement('div'); list.className = 'palette-chooser-list'; list.id = 'paletteChooserList'; chooser.append(summary, list); actions.append(resetButton, chooser); boardControls.append(copy, actions);
    announce = document.createElement('p'); announce.className = 'palette-screen-reader'; announce.setAttribute('aria-live', 'polite'); boardControls.append(announce);
  }
  function render() {
    if (!desktop.matches) { teardownDesktop(); return; }
    board.classList.add('palette-board'); if (!boardControls) makeControls(); board.replaceChildren(boardControls); board.style.setProperty('--palette-columns', layout.groups.length); board.style.gridTemplateColumns = gridColumns();
    layout.groups.forEach((group, index) => { board.append(makeStack(group, index)); if (index < layout.groups.length - 1) { const resize = document.createElement('button'); resize.type = 'button'; resize.className = 'palette-column-resizer'; resize.dataset.paletteColumnResize = index; resize.setAttribute('aria-label', `Resize columns ${index + 1} and ${index + 2}`); resize.title = 'Drag to resize columns; use left and right arrow keys too.'; resize.setAttribute('aria-orientation', 'vertical'); board.append(resize); } });
    const list = boardControls.querySelector('#paletteChooserList'); list.replaceChildren(); layout.hidden.forEach((id) => { const button = document.createElement('button'); button.type = 'button'; button.className = 'button secondary'; button.textContent = `Restore ${title(id)}`; button.addEventListener('click', () => restore(id)); list.append(button); }); list.hidden = !layout.hidden.length;
  }
  function teardownDesktop() {
    resizeSession = undefined; draggedId = null;
    board.classList.remove('palette-board'); board.style.removeProperty('--palette-columns'); board.style.removeProperty('grid-template-columns');
    original.forEach((node) => {
      node.classList.remove('palette-shell', 'is-collapsed', 'is-dragging'); node.style.removeProperty('--palette-height');
      node.querySelector('.palette-actions')?.remove(); node.querySelector('.palette-resize-handle')?.remove();
    });
    boardControls = undefined; announce = undefined; board.replaceChildren(...original);
  }
  board.addEventListener('click', (event) => { const button = event.target.closest('[data-palette-action]'); if (!button || button.dataset.paletteAction === 'drag') return; action(button.dataset.paletteId, button.dataset.paletteAction); });
  board.addEventListener('dragstart', (event) => {
    const grab = event.target.closest('.palette-grab');
    if (!grab) return;
    draggedId = grab.dataset.paletteId; const node = nodes.get(draggedId); node.classList.add('is-dragging'); event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', draggedId);
  });
  board.addEventListener('dragend', () => { draggedId = null; document.querySelectorAll('.palette-shell').forEach((node) => node.classList.remove('is-dragging')); document.querySelectorAll('.palette-stack').forEach((stack) => stack.classList.remove('is-drop-target')); });
  board.addEventListener('dragover', (event) => {
    const stack = event.target.closest('.palette-stack');
    if (!stack || !draggedId) return;
    const group = layout.groups[Number(stack.dataset.groupIndex)];
    if (group.includes(draggedId) && group.length === 1) return;
    event.preventDefault(); event.dataTransfer.dropEffect = 'move'; stack.classList.add('is-drop-target');
  });
  board.addEventListener('dragleave', (event) => event.target.closest('.palette-stack')?.classList.remove('is-drop-target'));
  board.addEventListener('drop', (event) => {
    const stack = event.target.closest('.palette-stack');
    if (!stack || !draggedId) return;
    event.preventDefault(); stack.classList.remove('is-drop-target'); const groupIndex = Number(stack.dataset.groupIndex); const target = event.target.closest('.palette-shell'); const index = target ? [...stack.querySelectorAll('.palette-shell')].indexOf(target) + (event.clientY > target.getBoundingClientRect().top + target.offsetHeight / 2 ? 1 : 0) : layout.groups[groupIndex].length; moveDrop(draggedId, groupIndex, index);
  });
  board.addEventListener('pointerdown', (event) => {
    const column = event.target.closest('[data-palette-column-resize]'); const height = event.target.closest('[data-palette-resize]');
    if (!column && !height) return;
    const handle = column || height; handle.setPointerCapture(event.pointerId);
    resizeSession = column ? {kind: 'column', index: Number(column.dataset.paletteColumnResize), last: event.clientX, pointerId: event.pointerId} : {kind: 'height', id: height.dataset.paletteResize, last: event.clientY, pointerId: event.pointerId};
  });
  board.addEventListener('pointermove', (event) => {
    if (!resizeSession || resizeSession.pointerId !== event.pointerId) return;
    const next = resizeSession.kind === 'column' ? event.clientX : event.clientY; const delta = next - resizeSession.last; if (!delta) return;
    if (resizeSession.kind === 'column') updateColumnWidth(resizeSession.index, delta); else updateHeight(resizeSession.id, delta);
    resizeSession.last = next;
  });
  function finishResize(event) {
    if (!resizeSession || resizeSession.pointerId !== event.pointerId) return;
    const finished = resizeSession; resizeSession = undefined; save(); say(finished.kind === 'column' ? 'Palette column widths updated.' : `${title(finished.id)} palette height updated.`);
  }
  board.addEventListener('pointerup', finishResize); board.addEventListener('pointercancel', finishResize);
  board.addEventListener('keydown', (event) => {
    const column = event.target.closest('[data-palette-column-resize]'); const height = event.target.closest('[data-palette-resize]');
    if (column && ['ArrowLeft', 'ArrowRight'].includes(event.key)) { event.preventDefault(); updateColumnWidth(Number(column.dataset.paletteColumnResize), event.key === 'ArrowLeft' ? -20 : 20); save(); column.focus(); }
    if (height && ['ArrowUp', 'ArrowDown'].includes(event.key)) { event.preventDefault(); updateHeight(height.dataset.paletteResize, event.key === 'ArrowDown' ? 20 : -20); save(); height.focus(); }
  });
  load(); render(); desktop.addEventListener('change', () => { if (desktop.matches) { load(); render(); } else render(); });
})();
