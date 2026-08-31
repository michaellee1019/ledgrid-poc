(() => {
  'use strict';
  const $ = (selector) => document.querySelector(selector);
  const statement = $('#stateExplanationStatement');
  const action = $('#stateExplanationAction');
  const details = {
    draft: $('#stateExplanationDraft'), preview: $('#stateExplanationPreview'),
    desired: $('#stateExplanationDesired'), observed: $('#stateExplanationObserved'),
    message: $('#stateExplanationMessage'),
  };
  let generation = 0;

  function identity(value) { return value && Number.isInteger(value.revision) && typeof value.digest === 'string' ? `r${value.revision} · ${value.digest}` : 'Unavailable'; }
  function reference(value) { return value && value.kind && value.id ? `${value.kind} · ${value.id} · ${identity(value.basis)}` : 'Unavailable'; }
  function explanation(snapshot) {
    const reconciliation = snapshot.reconciliation || {};
    if (snapshot.recovery) return {statement:'A recoverable local draft is available.', action:['Restore local draft', '#restoreDraft']};
    const states = {
      converged:['The local adapter observed this exact checked scene.', ['Stop the local scene', '#stopScene']],
      stopped:['The local adapter has stopped the observed scene.', null],
      rejected:['The local adapter rejected this local draft.', ['Review the local build', '#build']],
      stale:['The checked local draft is stale.', ['Check it again with Go Live', '#goLive']],
      diverged:['The local adapter needs its current observed scene before it can stop.', ['Review local handoff', '#live']],
      retry:['The local adapter confirmed an exact retry.', ['Stop the local scene', '#stopScene']],
    };
    if (states[reconciliation.state]) return {statement:states[reconciliation.state][0], action:states[reconciliation.state][1]};
    if (reconciliation.state === 'pending' && reconciliation.desired) return {statement:'This checked scene is pending local-adapter observation.', action:['Continue with Go Live', '#goLive']};
    if (snapshot.previewUnavailable) return {statement:'The local preview is unavailable.', action:['Review the local build', '#build']};
    if (snapshot.localMessage && snapshot.localMessage.startsWith('Checking')) return {statement:'This local draft is being checked.', action:null};
    if (snapshot.localMessage && (snapshot.localMessage.startsWith('Sending') || snapshot.localMessage.startsWith('Retrying'))) return {statement:'This checked scene is being sent to the local adapter.', action:null};
    if (snapshot.previewMessage.startsWith('Rendering')) return {statement:'This local draft is rendering a preview.', action:null};
    if (snapshot.draftMessage === 'Unsaved local draft.') return {statement:'This local draft is previewed but not saved as a look.', action:['Save this local look', '#saveLook']};
    if (snapshot.reference && snapshot.preview) return {statement:'This saved local scene is previewed here and has not changed the wall.', action:['Check it with Go Live', '#goLive']};
    return {statement:'This scene is local to Composer.', action:['Review the local build', '#build']};
  }
  function render(snapshot) {
    const value = explanation(snapshot); statement.textContent = value.statement;
    details.draft.textContent = reference(snapshot.reference || (snapshot.recovery && snapshot.recovery.reference));
    details.preview.textContent = identity(snapshot.preview);
    details.desired.textContent = identity(snapshot.reconciliation && snapshot.reconciliation.desired);
    details.observed.textContent = identity(snapshot.reconciliation && snapshot.reconciliation.observed);
    details.message.textContent = snapshot.localMessage || snapshot.previewMessage || 'Unavailable';
    if (!value.action) { action.hidden=true; action.removeAttribute('data-focus'); return; }
    action.hidden=false; action.textContent=value.action[0]; action.dataset.focus=value.action[1];
  }
  function receive(snapshot) { const current=++generation; requestAnimationFrame(() => { if (current === generation) render(snapshot); }); }
  action.addEventListener('click', () => { const target=action.dataset.focus && $(action.dataset.focus); if (target) target.focus({preventScroll:false}); });
  window.addEventListener('composer-state-change', (event) => receive(event.detail));
  receive(window.__composerStateProjection || {previewMessage:'Checking the local Composer state…', draftMessage:'', localMessage:''});
})();
