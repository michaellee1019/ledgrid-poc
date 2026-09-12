"""Exercise real Gallery drawing across asynchronous grid replacements."""
from pathlib import Path
import subprocess


def test_pending_thumbnail_repaints_every_replacement_canvas():
    source = Path('web/static/js/composer_slice.js').read_text()
    drawing = source[source.index('function galleryThumbnailKey'):source.index('function showGalleryDetail')]
    runner = r'''
const assert = require('node:assert/strict');
const vm = require('node:vm');
const state = {gallery: {digest:'catalog', thumbnails: new Map()}};
const pending = [];
let requests = 0;
const context = {state, GALLERY_FIXED_PREVIEW:{},
  galleryScene: entry => entry,
  preview: () => { requests++; return new Promise(resolve => pending.push(resolve)); },
  drawFrameIntoCanvas: (canvas, frame) => { canvas.frame = frame; },
};
const makeCanvas = () => ({setAttribute(key, value) { this[key] = value; }});
vm.runInNewContext(process.argv[1] + '; this.draw = drawGalleryThumbnail;', context);
(async () => {
  const entry = {key:'sparkle', name:'Sparkle'};
  const detached = makeCanvas(), attached = makeCanvas(), replacedAgain = makeCanvas();
  const work = [context.draw(detached, entry), context.draw(attached, entry), context.draw(replacedAgain, entry)];
  assert.equal(requests, 1);
  const frame = {pixels:'real-frame'};
  pending.shift()({frame});
  await Promise.all(work);
  for (const canvas of [detached, attached, replacedAgain]) {
    assert.equal(canvas.frame, frame, 'every canvas awaiting the same frame must paint');
    assert.match(canvas['aria-label'], /representative/);
  }
  const cached = makeCanvas(); await context.draw(cached, entry);
  assert.equal(cached.frame, frame); assert.equal(requests, 1);
  // A completion from an explicitly invalidated cache cannot paint stale data.
  state.gallery.thumbnails.clear();
  const stale = makeCanvas(); const old = context.draw(stale, entry);
  state.gallery.thumbnails.clear();
  const fresh = makeCanvas(); const current = context.draw(fresh, entry);
  pending.shift()({frame:{pixels:'stale'}}); await old;
  assert.equal(stale.frame, undefined);
  pending.shift()({frame}); await current; assert.equal(fresh.frame, frame);
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    result = subprocess.run(['node', '-e', runner, drawing], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
