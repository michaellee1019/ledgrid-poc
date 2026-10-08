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
    ['playlists', document.querySelector('.playlist-pane')],
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

  const pinned = new Set(['scenes', 'gallery', 'preview', 'playlists', 'global-scene', 'look', 'animation', 'operations']);
  const collapsedDefaults = () => Object.fromEntries(panels.map(([id]) => [id, pinned.has(id)]));
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
    const isExpanded = pinned.has(id) || expanded[id] === true;
    const toggle = panel.querySelector(':scope > .pane-heading .panel-toggle');
    panel.classList.toggle('is-collapsed', !isExpanded);
    panel.dataset.panelId = id;
    toggle.setAttribute('aria-expanded', String(isExpanded));
    toggle.setAttribute('aria-label', `${isExpanded ? 'Collapse' : 'Expand'} ${panelLabel(panel)} panel`);
    toggle.textContent = isExpanded ? 'Hide' : 'Show';
    toggle.hidden = pinned.has(id);
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

// Navigation only moves existing controls: their live event handlers stay intact.
(() => {
  const root = document.querySelector('.composer');
  if (!root) return;
  const dock = document.querySelector('.inspector-dock');
  const operations = document.querySelector('.operations-pane');
  const controls = document.querySelector('.control-workspace');
  const look = document.querySelector('[aria-labelledby="look-title"]');
  controls.prepend(look);
  const motion = document.querySelector('.global-scene-controls');
  look.after(motion);
  const performance = document.createElement('details');
  performance.className = 'advanced-controls';
  performance.innerHTML = '<summary>Output performance</summary>';
  performance.append(document.querySelector('.target-fps-control'), document.querySelector('.actual-fps'));
  motion.querySelector('.panel-content').append(performance);
  const save = document.querySelector('.scene-save-controls');
  save.prepend(document.querySelector('#saveState'));
  dock.prepend(save);
  root.append(operations);
  const undoRow = document.querySelector('#undoScene').parentElement;
  operations.querySelector('.operations-summary').prepend(undoRow);
  const playlistSummary = document.createElement('p');
  playlistSummary.className = 'playback-playlist-summary';
  operations.append(playlistSummary);
  const source = document.querySelector('#playlistStatus');
  const syncPlaylist = () => {
    playlistSummary.textContent = source.textContent;
    playlistSummary.hidden = !source.dataset.state || source.dataset.state === 'saved';
  };
  new MutationObserver(syncPlaylist).observe(source, {childList:true, subtree:true, characterData:true, attributes:true});
  syncPlaylist();
  const navigationKey = 'ledgrid.composer.workspace.v1';
  let collection = 'animations';
  const remember = () => {
    try { window.localStorage.setItem(navigationKey, JSON.stringify({view: root.dataset.view, collection})); }
    catch (_) { /* Navigation preferences are optional. */ }
  };
  const setView = (view) => {
    if (!['browse', 'edit', 'playlists'].includes(view)) view = 'browse';
    root.dataset.view = view;
    document.querySelectorAll('[data-workspace-view]').forEach(button => {
      const active = button.dataset.workspaceView === view;
      button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active));
    });
    remember();
  };
  const browse = (nextCollection) => {
    collection = ['animations', 'favorites', 'saved'].includes(nextCollection) ? nextCollection : 'animations';
    document.querySelector('.browse-workspace').dataset.collection = collection;
    document.querySelector('.gallery').hidden = collection === 'saved';
    document.querySelector('.library-pane').hidden = collection !== 'saved';
    document.querySelectorAll('[data-browse-view]').forEach(button => {
      const active = button.dataset.browseView === collection;
      button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active));
    });
    if (collection === 'saved') document.querySelector('[data-library-filter="all"]').click();
    else document.querySelector(`[data-gallery-filter="${collection === 'favorites' ? 'favorites' : 'all'}"]`).click();
    remember();
  };
  document.querySelectorAll('[data-workspace-view]').forEach(button => button.addEventListener('click', () => setView(button.dataset.workspaceView)));
  document.querySelectorAll('[data-browse-view]').forEach(button => button.addEventListener('click', () => browse(button.dataset.browseView)));
  document.querySelector('#openScene').addEventListener('click', () => { setView('browse'); browse('saved'); document.querySelector('#librarySearch').focus(); });
  document.querySelector('#browsePlaylistAdd').addEventListener('click', () => { document.querySelector('#playlistAdd').click(); setView('playlists'); });
  document.querySelector('#playlistBrowse').addEventListener('click', () => { setView('browse'); browse('animations'); document.querySelector('#gallerySearch').focus(); });
  let preference = {};
  try { preference = JSON.parse(window.localStorage.getItem(navigationKey)) || {}; }
  catch (_) { /* Default navigation when storage is unavailable. */ }
  browse(preference.collection);
  setView(preference.view);
})();
