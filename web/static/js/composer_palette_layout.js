// Panel preferences are local presentation state. This module deliberately
// owns no Scene data and performs no publication or wall-control requests.
(() => {
  'use strict';

  const preferenceKey = 'ledgrid.composer.compact-panels.v2';
  const retiredKeys = Object.freeze([
    'ledgrid.composer.desktop-palette-layout.v3',
    'ledgrid.composer.constrained-docks.v1',
  ]);
  const workspace = document.querySelector('.desktop-workspace');
  if (!workspace) return;

  const panels = [
    ['scenes', document.querySelector('.library-pane')],
    ['gallery', document.querySelector('.gallery')],
    ['preview', document.querySelector('.preview-pane')],
    ['global-scene', document.querySelector('.global-scene-controls')],
    ['background', document.querySelector('[aria-labelledby="background-title"]')],
    ['animation', document.querySelector('[aria-labelledby="animation-title"]')],
    ['widgets', document.querySelector('[aria-labelledby="widgets-title"]')],
    ['plants', document.querySelector('[aria-labelledby="plants-title"]')],
    ['look', document.querySelector('[aria-labelledby="look-title"]')],
    ['snake', document.querySelector('[aria-labelledby="snake-title"]')],
    ['canopy', document.querySelector('[aria-labelledby="canopy-title"]')],
    ['reef', document.querySelector('[aria-labelledby="reef-title"]')],
    ['arcade-trio', document.querySelector('[aria-labelledby="arcade-trio-title"]')],
    ['operations', document.querySelector('.operations-pane')],
  ].filter(([, panel]) => panel);
  const panelIds = new Set(panels.map(([id]) => id));
  if (panelIds.size !== panels.length) return;

  const collapsedDefaults = () => Object.fromEntries(panels.map(([id]) => [id, false]));
  const sanitize = (value) => {
    if (!value || value.version !== 2 || !value.expanded || typeof value.expanded !== 'object') {
      return collapsedDefaults();
    }
    return Object.fromEntries(panels.map(([id]) => [id, value.expanded[id] === true]));
  };
  const read = () => {
    try { return sanitize(JSON.parse(window.localStorage.getItem(preferenceKey))); }
    catch (_) { return collapsedDefaults(); }
  };
  let expanded = read();

  const write = () => {
    try { window.localStorage.setItem(preferenceKey, JSON.stringify({version: 2, expanded})); }
    catch (_) { /* Local preference storage is optional. */ }
  };
  const panelLabel = (panel) => panel.querySelector(':scope > .pane-heading .eyebrow')?.textContent.trim()
    || panel.querySelector(':scope > .pane-heading h1, :scope > .pane-heading h2')?.textContent.trim()
    || 'panel';

  const apply = () => panels.forEach(([id, panel]) => {
    const isExpanded = expanded[id] === true;
    const toggle = panel.querySelector(':scope > .pane-heading .panel-toggle');
    panel.classList.toggle('is-collapsed', !isExpanded);
    panel.dataset.panelId = id;
    toggle.setAttribute('aria-expanded', String(isExpanded));
    toggle.setAttribute('aria-label', `${isExpanded ? 'Collapse' : 'Expand'} ${panelLabel(panel)} panel`);
    toggle.textContent = isExpanded ? 'Hide' : 'Show';
  });

  panels.forEach(([id, panel]) => {
    const heading = panel.querySelector(':scope > .pane-heading');
    if (!heading) return;
    const content = document.createElement('div');
    content.className = 'panel-content';
    content.id = `composer-panel-${id}`;
    [...panel.children].filter((child) => child !== heading).forEach((child) => content.append(child));
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'panel-toggle';
    toggle.setAttribute('aria-controls', content.id);
    toggle.addEventListener('click', () => {
      expanded = {...expanded, [id]: !expanded[id]};
      apply();
      write();
    });
    heading.append(toggle);
    panel.append(content);
    panel.classList.add('collapsible-panel', 'panels-ready');
  });

  document.querySelector('#resetComposerLayout')?.addEventListener('click', () => {
    expanded = collapsedDefaults();
    apply();
    try { window.localStorage.removeItem(preferenceKey); }
    catch (_) { /* Local preference storage is optional. */ }
  });
  retiredKeys.forEach((key) => {
    try { window.localStorage.removeItem(key); }
    catch (_) { /* Optional legacy preference cleanup. */ }
  });
  workspace.setAttribute('data-layout', 'responsive-panels');
  apply();
  window.ComposerPanelLayout = Object.freeze({
    expand(id) {
      if (!panelIds.has(id)) return false;
      if (expanded[id] !== true) {
        expanded = {...expanded, [id]: true};
        apply();
        write();
      }
      return true;
    },
  });
})();
