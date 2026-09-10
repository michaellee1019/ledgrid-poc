// The Composer used to turn its shell into a draggable palette board.  Layout
// is now a fixed docked studio, so this tiny bootstrap only retires that local
// preference; it deliberately owns no Scene or publication state.
(() => {
  'use strict';
  const retiredKey = 'ledgrid.composer.desktop-palette-layout.v3';
  try { window.localStorage.removeItem(retiredKey); } catch (_) { /* Optional preference cleanup. */ }
  document.querySelector('.desktop-workspace')?.setAttribute('data-layout', 'docked-studio');
})();
