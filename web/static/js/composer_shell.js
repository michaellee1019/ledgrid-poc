(() => {
  'use strict';
  const shell = document.querySelector('#composerShell');
  const message = document.querySelector('#composerShellMessage');
  const retry = document.querySelector('#composerShellRetry');
  const update = document.querySelector('#composerShellUpdate');
  const root = document.querySelector('.composer');
  const controls = () => [...document.querySelectorAll('.composer button, .composer input, .composer select')];
  const priorDisabled = new Map();
  let offline = false;

  function setMutationDisabled(value) {
    if (value) controls().forEach((control) => { if (!priorDisabled.has(control)) priorDisabled.set(control, control.disabled); control.disabled = true; });
    else { priorDisabled.forEach((disabled, control) => { control.disabled = disabled; }); priorDisabled.clear(); }
    const preview = document.querySelector('#scenePreview'); preview.setAttribute('aria-disabled', String(value)); root.classList.toggle('offline', value);
  }
  function show(text, action = null) { shell.hidden = false; message.textContent = text; retry.hidden = action !== 'retry'; update.hidden = action !== 'update'; }
  function unavailableState() { [['#saveState', 'Unavailable offline'], ['#desiredIdentity', 'Unavailable offline'], ['#observedIdentity', 'Unavailable offline'], ['#connectionState', 'Unavailable'], ['#previewIdentity', 'Unavailable offline'], ['#previewStatus', 'Local Composer server unavailable.'], ['#operationMessage', 'Local Composer server unavailable.']].forEach(([selector, text]) => { const node=document.querySelector(selector); if (node) node.textContent=text; }); }
  function setOffline() { offline = true; window.__composerShellUnavailable=true; setMutationDisabled(true); unavailableState(); show('Local Composer server unavailable. This shell cannot show current scene or live state.', 'retry'); }
  function reconnect() { show('Reload Composer to reconnect and fetch current local state.', 'retry'); }
  function guard(event) {
    if (!offline) return;
    const target = event.target;
    if (target.closest('.composer')) { event.preventDefault(); event.stopImmediatePropagation(); }
  }
  function reloadFresh() { window.location.reload(); }
  async function retireOfflineWorkers() {
    if ('serviceWorker' in navigator) {
      const registrations = await navigator.serviceWorker.getRegistrations();
      await Promise.all(registrations.filter((registration) => [registration.active, registration.waiting, registration.installing].some((worker) => worker && ['/composer-sw.js', '/composer-service-worker.js'].includes(new URL(worker.scriptURL).pathname))).map((registration) => registration.unregister()));
    }
    if ('caches' in window) {
      const names = await caches.keys();
      await Promise.all(names.filter((name) => name.startsWith('composer-shell-') || name.startsWith('ledgrid-composer-')).map((name) => caches.delete(name)));
    }
  }
  ['click', 'pointerdown', 'keydown', 'input', 'change'].forEach((type) => document.addEventListener(type, guard, true));
  window.addEventListener('online', reconnect); window.addEventListener('composer-server-unavailable', setOffline);
  retry.addEventListener('click', reloadFresh);
  new MutationObserver(() => { if (offline) setMutationDisabled(true); }).observe(root, {childList:true, subtree:true});
  retireOfflineWorkers().catch((error) => console.info("Composer cache retirement:", error.message));
})();
