// Composer layout preferences are deliberately isolated from Scene state and
// publication. The studio stays in fixed Library / Preview / inspector order.
(() => {
  'use strict';
  const retiredKey = 'ledgrid.composer.desktop-palette-layout.v3';
  const preferenceKey = 'ledgrid.composer.constrained-docks.v1';
  const bounds = Object.freeze({library: [176, 360], inspector: [252, 480]});
  const defaults = Object.freeze({library: 220, inspector: 306, collapsed: {background: false, animation: false, widgets: false, plants: false, look: false}});
  const workspace = document.querySelector('.desktop-workspace');
  if (!workspace) return;

  try { window.localStorage.removeItem(retiredKey); } catch (_) { /* Optional preference cleanup. */ }
  workspace.setAttribute('data-layout', 'docked-studio');
  const desktop = window.matchMedia('(min-width: 761px)');
  const clamp = (dock, value) => Math.min(bounds[dock][1], Math.max(bounds[dock][0], Math.round(value)));
  const isStoredLayout = (value) => value && value.version === 1
    && ['library', 'inspector'].every((dock) => Number.isFinite(value[dock]) && value[dock] >= bounds[dock][0] && value[dock] <= bounds[dock][1])
    && value.collapsed && Object.keys(defaults.collapsed).every((key) => typeof value.collapsed[key] === 'boolean');
  const read = () => {
    try {
      const parsed = JSON.parse(window.localStorage.getItem(preferenceKey));
      return isStoredLayout(parsed) ? parsed : defaults;
    } catch (_) { return defaults; }
  };
  let layout = read();
  const write = () => {
    try { window.localStorage.setItem(preferenceKey, JSON.stringify({version: 1, ...layout})); } catch (_) { /* Local preference storage is optional. */ }
  };

  const inspectorNames = Object.freeze({background: 'Background', animation: 'Animation', widgets: 'Widgets', plants: 'Plants', look: 'Look'});
  document.querySelectorAll('.inspectors > .inspector').forEach((section) => {
    const eyebrow = section.querySelector(':scope > .pane-heading .eyebrow')?.textContent.trim();
    const key = Object.entries(inspectorNames).find(([, name]) => name === eyebrow)?.[0];
    if (!key) return;
    const heading = section.querySelector(':scope > .pane-heading');
    const content = document.createElement('div');
    content.className = 'inspector-content'; content.id = `${key}-inspector-content`;
    [...section.children].filter((child) => child !== heading).forEach((child) => content.append(child));
    const toggle = document.createElement('button');
    toggle.type = 'button'; toggle.className = 'inspector-toggle';
    toggle.setAttribute('aria-controls', content.id);
    heading.append(toggle); section.append(content);
    section.classList.add('collapsible-inspector'); section.dataset.inspectorKey = key;
    toggle.addEventListener('click', () => {
      layout = {...layout, collapsed: {...layout.collapsed, [key]: !layout.collapsed[key]}};
      apply(); write();
    });
  });

  const updateSeparators = () => document.querySelectorAll('[data-dock-resizer]').forEach((separator) => {
    const dock = separator.dataset.dockResizer;
    separator.setAttribute('aria-valuemin', String(bounds[dock][0]));
    separator.setAttribute('aria-valuemax', String(bounds[dock][1]));
    separator.setAttribute('aria-valuenow', String(layout[dock]));
    separator.setAttribute('aria-valuetext', `${dock === 'library' ? 'Library' : 'Scene controls'} width ${layout[dock]} pixels`);
  });
  const apply = () => {
    workspace.style.setProperty('--library-width', `${layout.library}px`);
    workspace.style.setProperty('--inspector-width', `${layout.inspector}px`);
    document.querySelectorAll('.collapsible-inspector[data-inspector-key]').forEach((section) => {
      const collapsed = layout.collapsed[section.dataset.inspectorKey];
      const toggle = section.querySelector('.inspector-toggle');
      section.classList.toggle('is-collapsed', collapsed);
      toggle.setAttribute('aria-expanded', String(!collapsed));
      toggle.setAttribute('aria-label', `${collapsed ? 'Expand' : 'Collapse'} ${inspectorNames[section.dataset.inspectorKey]} inspector`);
      toggle.textContent = collapsed ? 'Expand' : 'Collapse';
    });
    updateSeparators();
  };
  const setWidth = (dock, value, persist = true) => {
    layout = {...layout, [dock]: clamp(dock, value)};
    apply(); if (persist) write();
  };
  document.querySelector('#resetComposerLayout')?.addEventListener('click', () => {
    layout = {library: defaults.library, inspector: defaults.inspector, collapsed: {...defaults.collapsed}};
    apply();
    try { window.localStorage.removeItem(preferenceKey); } catch (_) { /* Local preference storage is optional. */ }
  });
  document.querySelectorAll('[data-dock-resizer]').forEach((separator) => {
    const dock = separator.dataset.dockResizer;
    const pointerWidth = (event) => {
      const rect = workspace.getBoundingClientRect();
      return dock === 'library' ? event.clientX - rect.left : rect.right - event.clientX;
    };
    separator.addEventListener('pointerdown', (event) => {
      if (!desktop.matches || event.button !== 0) return;
      event.preventDefault(); separator.setPointerCapture(event.pointerId);
      const move = (next) => setWidth(dock, pointerWidth(next), false);
      const finish = () => { separator.removeEventListener('pointermove', move); write(); };
      separator.addEventListener('pointermove', move);
      separator.addEventListener('pointerup', finish, {once: true});
      separator.addEventListener('pointercancel', finish, {once: true});
    });
    separator.addEventListener('keydown', (event) => {
      if (!desktop.matches) return;
      const step = event.shiftKey ? 24 : 8;
      const direction = dock === 'library' ? 1 : -1;
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        event.preventDefault(); setWidth(dock, layout[dock] + (event.key === 'ArrowRight' ? step : -step) * direction);
      } else if (event.key === 'Home') { event.preventDefault(); setWidth(dock, bounds[dock][0]); }
      else if (event.key === 'End') { event.preventDefault(); setWidth(dock, bounds[dock][1]); }
    });
  });
  apply();
})();
