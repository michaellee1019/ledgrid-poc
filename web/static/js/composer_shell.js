(() => {
  'use strict';
  const shell = document.querySelector('#composerShell');
  const message = document.querySelector('#composerShellMessage');
  const retry = document.querySelector('#composerShellRetry');
  const update = document.querySelector('#composerShellUpdate');
  const root = document.querySelector('.composer');
  const controls = () => [...document.querySelectorAll('.inspectors input, .inspectors select, #saveScene, #saveAsScene, #openScene, #liveAction, #checkScene, #undoScene, #redoScene, #libraryList button')];
  const priorDisabled = new Map();
  let offline = false;
  let waiting = null;
  let activatingUpdate = false;

  function hasProtectedScene() { return document.querySelector('#saveState').textContent !== 'Saved'; }
  function setMutationDisabled(value) {
    if (value) controls().forEach((control) => { if (!priorDisabled.has(control)) priorDisabled.set(control, control.disabled); control.disabled = true; });
    else { priorDisabled.forEach((disabled, control) => { control.disabled = disabled; }); priorDisabled.clear(); }
    const preview = document.querySelector('#scenePreview'); preview.setAttribute('aria-disabled', String(value)); root.classList.toggle('offline', value);
  }
  function show(text, action = null) { shell.hidden = false; message.textContent = text; retry.hidden = action !== 'retry'; update.hidden = action !== 'update'; }
  function unavailableState() { [['#saveState', 'Unavailable offline'], ['#desiredIdentity', 'Unavailable offline'], ['#observedIdentity', 'Unavailable offline'], ['#diagnosticObserved', 'Unavailable offline'], ['#connectionState', 'Unavailable'], ['#previewIdentity', 'Unavailable offline'], ['#previewStatus', 'Local Composer server unavailable.'], ['#operationMessage', 'Local Composer server unavailable.']].forEach(([selector, text]) => { const node=document.querySelector(selector); if (node) node.textContent=text; }); }
  function setOffline() { offline = true; window.__composerShellUnavailable=true; setMutationDisabled(true); unavailableState(); show('Local Composer server unavailable. This shell cannot show current scene or live state.', 'retry'); }
  function reconnect() { show('Reload Composer to reconnect and fetch current local state.', 'retry'); }
  function guard(event) {
    if (!offline) return;
    const target = event.target;
    if (target.closest('.inspectors, #liveAction, #checkScene, #scenePreview')) { event.preventDefault(); event.stopImmediatePropagation(); }
  }
  function reloadFresh() { window.location.reload(); }
  function updateReady(worker) { waiting = worker; show('A Composer shell update is ready. Reload when this scene is safe.', 'update'); }
  function applyUpdate() { if (!waiting) return; if (hasProtectedScene()) return show('Save the current scene before reloading this shell update.', 'update'); activatingUpdate=true; waiting.postMessage({type:'composer-shell-activate'}); }
  function register() {
    if (!('serviceWorker' in navigator)) return;
    navigator.serviceWorker.register('/composer-sw.js?v=composer-shell-v6', {scope:'/'}).then((registration) => {
      if (registration.waiting) updateReady(registration.waiting);
      registration.addEventListener('updatefound', () => { const worker=registration.installing; if (!worker) return; worker.addEventListener('statechange', () => { if (worker.state === 'installed' && navigator.serviceWorker.controller) updateReady(worker); }); });
    });
    navigator.serviceWorker.addEventListener('controllerchange', () => { if (activatingUpdate) window.location.reload(); });
    navigator.serviceWorker.addEventListener('message', (event) => { if (event.data && event.data.type === 'composer-server-unavailable') setOffline(); });
  }
  ['click', 'pointerdown', 'keydown', 'input', 'change'].forEach((type) => document.addEventListener(type, guard, true));
  window.addEventListener('online', reconnect); window.addEventListener('composer-server-unavailable', setOffline);
  retry.addEventListener('click', reloadFresh); update.addEventListener('click', applyUpdate);
  new MutationObserver(() => { if (offline) setMutationDisabled(true); }).observe(root, {childList:true, subtree:true});
  register();
})();
