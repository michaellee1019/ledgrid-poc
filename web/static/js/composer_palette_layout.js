/* Desktop palette layout is a disposable UI preference, never Scene state. */
(() => {
  'use strict';
  const desktop = window.matchMedia('(min-width: 761px)');
  const board = document.querySelector('.desktop-workspace');
  const controlsWorkspace = document.querySelector('.control-workspace');
  if (!board || !controlsWorkspace) return;

  const paletteDefinitions = [
    ['library', 'Library', document.querySelector('.library-pane')],
    ['installed-final', 'Installed Final', document.querySelector('.preview-pane')],
    ...[...controlsWorkspace.querySelectorAll('.inspector')].map((node) => {
      const headingId = node.getAttribute('aria-labelledby') || '';
      const label = node.querySelector('.eyebrow')?.textContent?.trim()
        || document.getElementById(headingId)?.textContent?.trim() || headingId;
      return [headingId.replace(/-title$/, '') || label.toLowerCase().replace(/[^a-z0-9]+/g, '-'), label, node];
    }),
    ['operations', 'Operations', document.querySelector('.operations-pane')],
  ].filter(([, , node]) => node);
  const ids = new Set(paletteDefinitions.map(([id]) => id));
  const names = new Map(paletteDefinitions.map(([id, name]) => [id, name]));
  const nodes = new Map(paletteDefinitions.map(([id, , node]) => [id, node]));
  if (ids.size !== paletteDefinitions.length) return;

  const originalBoardChildren = [...board.children];
  const original = paletteDefinitions.map(([id, , node]) => ({id, node, parent: node.parentElement, next: node.nextSibling}));
  // v3 intentionally drops the old hidden palettes and Arrange-menu state.
  const storageKey = 'ledgrid.composer.desktop-palette-layout.v3';
  const defaultLayout = () => ({
    groups: [['library'], ['installed-final'], ['global-scene-controls', 'background', 'look'], ['animation', 'widgets', 'plants', 'snake', 'canopy', 'reef', 'arcade-trio'], ['operations']],
    collapsed: {}, heights: {}, widths: [1, .82, 1.1, 1.35, 1],
  });
  let layout = defaultLayout();
  let draggedId = null;
  let resizeSession;
  let announce;

  function clamp(value, min, max) { return Math.min(max, Math.max(min, value)); }
  function sanitize(raw) {
    const next = defaultLayout();
    if (!raw || typeof raw !== 'object') return next;
    const seen = new Set();
    const groups = Array.isArray(raw.groups) ? raw.groups.map((group) => {
      if (!Array.isArray(group)) return [];
      return group.filter((id) => {
        if (!ids.has(id) || seen.has(id)) return false;
        seen.add(id); return true;
      });
    }).filter((group) => group.length) : [];
    paletteDefinitions.forEach(([id]) => { if (!seen.has(id)) groups.push([id]); });
    next.groups = groups;
    next.collapsed = Object.fromEntries(paletteDefinitions.map(([id]) => [id, Boolean(raw.collapsed?.[id])]));
    next.heights = Object.fromEntries(paletteDefinitions.map(([id]) => [id, clamp(Number(raw.heights?.[id]) || 0, 180, 900)]).filter(([, height]) => height));
    next.widths = groups.map((_, index) => clamp(Number(raw.widths?.[index]) || 1, .65, 2.2));
    return next;
  }
  function load() { try { layout = sanitize(JSON.parse(localStorage.getItem(storageKey))); } catch (_) { layout = defaultLayout(); } }
  function save() { try { localStorage.setItem(storageKey, JSON.stringify(layout)); } catch (_) { /* Disposable preference. */ } }
  function say(message) { if (announce) announce.textContent = message; }
  function title(id) { return names.get(id) || id; }
  function groupFor(id) { return layout.groups.findIndex((group) => group.includes(id)); }
  function remove(id) {
    const group = groupFor(id);
    if (group < 0) return -1;
    layout.groups[group] = layout.groups[group].filter((value) => value !== id);
    if (!layout.groups[group].length) { layout.groups.splice(group, 1); layout.widths.splice(group, 1); }
    return group;
  }
  function makeButton(label, text, action, id, className) {
    const button = document.createElement('button');
    button.type = 'button'; button.className = `button text ${className}`;
    button.dataset.paletteAction = action; button.dataset.paletteId = id;
    button.title = label; button.setAttribute('aria-label', label); button.textContent = text;
    return button;
  }
  function ensurePaletteFrame(id) {
    const node = nodes.get(id);
    node.classList.add('palette-shell');
    const heading = node.querySelector('.pane-heading');
    if (!heading || heading.querySelector('.palette-header-controls')) return;
    const controls = document.createElement('div'); controls.className = 'palette-header-controls';
    const grab = makeButton(`Drag ${title(id)} palette`, '⠿', 'drag', id, 'palette-grab');
    grab.draggable = true;
    const collapse = makeButton(`Collapse ${title(id)} palette`, '⌄', 'collapse', id, 'palette-collapse');
    collapse.setAttribute('aria-expanded', 'true');
    controls.append(grab, collapse); heading.append(controls);
    const resize = document.createElement('div'); resize.className = 'palette-resize-handle';
    resize.tabIndex = 0; resize.role = 'separator'; resize.setAttribute('aria-orientation', 'horizontal');
    resize.dataset.paletteResize = id; resize.setAttribute('aria-label', `Resize ${title(id)} palette height`);
    node.append(resize);
  }
  function toggleCollapsed(id) {
    layout.collapsed[id] = !layout.collapsed[id]; save(); render();
    requestAnimationFrame(() => document.querySelector(`[data-palette-id="${id}"][data-palette-action="collapse"]`)?.focus());
    say(`${title(id)} palette ${layout.collapsed[id] ? 'collapsed' : 'expanded'}.`);
  }
  function moveIntoStack(id, targetGroup, targetIndex) {
    const sourceGroup = groupFor(id);
    if (sourceGroup === targetGroup) {
      const sourceIndex = layout.groups[sourceGroup].indexOf(id);
      layout.groups[sourceGroup].splice(sourceIndex, 1);
      layout.groups[sourceGroup].splice(targetIndex > sourceIndex ? targetIndex - 1 : targetIndex, 0, id);
    } else {
      const anchor = layout.groups[targetGroup].find((value) => value !== id);
      remove(id);
      const insertionGroup = layout.groups[groupFor(anchor)];
      insertionGroup.splice(targetIndex, 0, id);
    }
    save(); render(); say(`${title(id)} moved.`);
  }
  function moveToOwnColumn(id, insertionIndex) {
    const sourceGroup = groupFor(id);
    const sourceWasSingle = layout.groups[sourceGroup]?.length === 1;
    remove(id);
    if (sourceWasSingle && sourceGroup < insertionIndex) insertionIndex -= 1;
    insertionIndex = clamp(insertionIndex, 0, layout.groups.length);
    layout.groups.splice(insertionIndex, 0, [id]); layout.widths.splice(insertionIndex, 0, 1);
    save(); render(); say(`${title(id)} moved to its own column.`);
  }
  function makeStack(group, groupIndex) {
    const stack = document.createElement('section'); stack.className = 'palette-stack';
    stack.dataset.groupIndex = groupIndex; stack.style.setProperty('--stack-width', `${layout.widths[groupIndex] || 1}fr`);
    stack.setAttribute('aria-label', `Palette column ${groupIndex + 1}`);
    group.forEach((id) => {
      const node = nodes.get(id); ensurePaletteFrame(id);
      const collapsed = Boolean(layout.collapsed[id]); node.classList.toggle('is-collapsed', collapsed);
      node.style.setProperty('--palette-height', `${layout.heights[id] || 900}px`);
      const collapse = node.querySelector('[data-palette-action="collapse"]');
      collapse.textContent = collapsed ? '›' : '⌄';
      collapse.setAttribute('aria-label', `${collapsed ? 'Expand' : 'Collapse'} ${title(id)} palette`);
      collapse.setAttribute('aria-expanded', String(!collapsed));
      stack.append(node);
    });
    return stack;
  }
  function gridColumns() { return layout.groups.map((_, index) => `minmax(170px, ${layout.widths[index] || 1}fr)`).join(' minmax(8px, 8px) '); }
  function updateColumnWidth(index, delta) {
    const total = board.getBoundingClientRect().width || 1; const shift = delta / total * layout.groups.length;
    layout.widths[index] = clamp((layout.widths[index] || 1) + shift, .65, 2.2);
    layout.widths[index + 1] = clamp((layout.widths[index + 1] || 1) - shift, .65, 2.2);
    board.style.gridTemplateColumns = gridColumns();
  }
  function updateHeight(id, delta) {
    layout.heights[id] = clamp((layout.heights[id] || 300) + delta, 180, 900);
    nodes.get(id).style.setProperty('--palette-height', `${layout.heights[id]}px`);
  }
  function render() {
    if (!desktop.matches) { teardownDesktop(); return; }
    board.classList.add('palette-board'); board.replaceChildren(controlsWorkspace); board.style.gridTemplateColumns = gridColumns();
    layout.groups.forEach((group, index) => {
      board.append(makeStack(group, index));
      if (index >= layout.groups.length - 1) return;
      const resize = document.createElement('button'); resize.type = 'button'; resize.className = 'palette-column-resizer';
      resize.dataset.paletteColumnResize = index; resize.dataset.paletteColumnDrop = index + 1;
      resize.setAttribute('aria-label', `Resize columns ${index + 1} and ${index + 2}`);
      resize.title = 'Drag to resize columns; drop a palette here to make a new column.';
      resize.setAttribute('aria-orientation', 'vertical'); board.append(resize);
    });
    announce = document.createElement('p'); announce.className = 'palette-screen-reader'; announce.setAttribute('aria-live', 'polite'); board.append(announce);
  }
  function teardownDesktop() {
    resizeSession = undefined; draggedId = null;
    original.slice().reverse().forEach(({node, parent, next}) => {
      node.classList.remove('palette-shell', 'is-collapsed', 'is-dragging'); node.style.removeProperty('--palette-height');
      node.querySelector('.palette-header-controls')?.remove(); node.querySelector('.palette-resize-handle')?.remove();
      parent.insertBefore(node, next?.parentNode === parent ? next : null);
    });
    board.classList.remove('palette-board'); board.style.removeProperty('grid-template-columns');
    announce = undefined; board.replaceChildren(...originalBoardChildren);
  }

  board.addEventListener('click', (event) => {
    const button = event.target.closest('[data-palette-action="collapse"]');
    if (button) toggleCollapsed(button.dataset.paletteId);
  });
  board.addEventListener('dragstart', (event) => {
    const grab = event.target.closest('.palette-grab'); if (!grab) return;
    draggedId = grab.dataset.paletteId; nodes.get(draggedId).classList.add('is-dragging');
    event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', draggedId);
  });
  board.addEventListener('dragend', () => {
    draggedId = null;
    document.querySelectorAll('.palette-shell').forEach((node) => node.classList.remove('is-dragging'));
    document.querySelectorAll('.palette-stack, .palette-column-resizer').forEach((node) => node.classList.remove('is-drop-target'));
  });
  board.addEventListener('dragover', (event) => {
    if (!draggedId) return;
    const boundary = event.target.closest('[data-palette-column-drop]'); const stack = event.target.closest('.palette-stack');
    if (!boundary && !stack) return;
    if (stack) {
      const group = layout.groups[Number(stack.dataset.groupIndex)];
      if (group.includes(draggedId) && group.length === 1) return;
    }
    event.preventDefault(); event.dataTransfer.dropEffect = 'move'; (boundary || stack).classList.add('is-drop-target');
  });
  board.addEventListener('dragleave', (event) => event.target.closest('.palette-stack, .palette-column-resizer')?.classList.remove('is-drop-target'));
  board.addEventListener('drop', (event) => {
    if (!draggedId) return;
    const boundary = event.target.closest('[data-palette-column-drop]');
    if (boundary) { event.preventDefault(); moveToOwnColumn(draggedId, Number(boundary.dataset.paletteColumnDrop)); return; }
    const stack = event.target.closest('.palette-stack'); if (!stack) return;
    event.preventDefault(); const groupIndex = Number(stack.dataset.groupIndex); const target = event.target.closest('.palette-shell');
    const index = target ? [...stack.querySelectorAll('.palette-shell')].indexOf(target)
      + (event.clientY > target.getBoundingClientRect().top + target.offsetHeight / 2 ? 1 : 0)
      : layout.groups[groupIndex].length;
    moveIntoStack(draggedId, groupIndex, index);
  });
  board.addEventListener('pointerdown', (event) => {
    const column = event.target.closest('[data-palette-column-resize]'); const height = event.target.closest('[data-palette-resize]');
    if (!column && !height) return;
    const handle = column || height; handle.setPointerCapture(event.pointerId);
    resizeSession = column
      ? {kind: 'column', index: Number(column.dataset.paletteColumnResize), last: event.clientX, pointerId: event.pointerId}
      : {kind: 'height', id: height.dataset.paletteResize, last: event.clientY, pointerId: event.pointerId};
  });
  board.addEventListener('pointermove', (event) => {
    if (!resizeSession || resizeSession.pointerId !== event.pointerId) return;
    const next = resizeSession.kind === 'column' ? event.clientX : event.clientY; const delta = next - resizeSession.last;
    if (!delta) return;
    if (resizeSession.kind === 'column') updateColumnWidth(resizeSession.index, delta); else updateHeight(resizeSession.id, delta);
    resizeSession.last = next;
  });
  function finishResize(event) {
    if (!resizeSession || resizeSession.pointerId !== event.pointerId) return;
    const finished = resizeSession; resizeSession = undefined; save();
    say(finished.kind === 'column' ? 'Palette column widths updated.' : `${title(finished.id)} palette height updated.`);
  }
  board.addEventListener('pointerup', finishResize); board.addEventListener('pointercancel', finishResize);
  board.addEventListener('keydown', (event) => {
    const column = event.target.closest('[data-palette-column-resize]'); const height = event.target.closest('[data-palette-resize]');
    if (column && ['ArrowLeft', 'ArrowRight'].includes(event.key)) {
      event.preventDefault(); updateColumnWidth(Number(column.dataset.paletteColumnResize), event.key === 'ArrowLeft' ? -20 : 20); save(); column.focus();
    }
    if (height && ['ArrowUp', 'ArrowDown'].includes(event.key)) {
      event.preventDefault(); updateHeight(height.dataset.paletteResize, event.key === 'ArrowDown' ? 20 : -20); save(); height.focus();
    }
  });
  load(); render(); desktop.addEventListener('change', () => { if (desktop.matches) load(); render(); });
})();
