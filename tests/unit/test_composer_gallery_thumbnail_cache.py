"""Exercise real Gallery drawing across asynchronous grid replacements."""
from pathlib import Path
import subprocess


def test_gallery_rebuild_preserves_attached_detail_and_pending_presets():
    source = Path('web/static/js/composer_slice.js').read_text()
    placement = source[source.index('function placeGalleryDetail'):source.index('function showGalleryDetail')]
    render = source[source.index('function renderGallery'):source.index('async function loadGallery')]
    runner = r'''
const assert = require('node:assert/strict');
const vm = require('node:vm');
class Element {
  constructor() { this.children = []; this.dataset = {}; this.parent = null; }
  append(...nodes) { for (const node of nodes) { node.parent?.remove(node); this.children.push(node); node.parent = this; } }
  remove(node) { this.children.splice(this.children.indexOf(node), 1); node.parent = null; }
  replaceChildren(...nodes) { for (const node of [...this.children]) this.remove(node); this.append(...nodes); }
  after(node) { const parent = this.parent; node.parent?.remove(node); parent.children.splice(parent.children.indexOf(this) + 1, 0, node); node.parent = parent; }
  querySelectorAll() { return this.children.filter(node => node.className === 'gallery-card'); }
  setAttribute() {}
  addEventListener() {}
}
const grid = new Element(), detail = new Element(), count = {}, empty = {};
const pendingPresetTarget = new Element(); detail.append(pendingPresetTarget); grid.append(detail);
let entries = [{key:'early',component_id:'early',available:true,parameters:{}},{key:'late',component_id:'late',available:true,parameters:{}}];
const layout = {matches:true, addEventListener(event, handler) { assert.equal(event,'change'); this.change=handler; }};
const context = {
  state:{gallery:{detail:'early',favorites:new Set()},scene:{}},
  $: selector => ({'#galleryGrid':grid,'#galleryDetail':detail.parent ? detail : null,'#galleryCount':count,'#galleryEmpty':empty})[selector],
  galleryEntries:()=>entries,
  window:{matchMedia:()=>layout}, document:{createElement:()=>new Element()},
  scheduleGalleryThumbnails(){},
};
vm.runInNewContext(process.argv[1] + ';this.render=renderGallery;installGalleryLayoutListener();',context);
context.render(); context.render();
assert.equal(grid.children.filter(node=>node===detail).length,1);
assert.equal(grid.children[1],detail,'detail stays beside the selected phone card');
assert.equal(detail.children[0],pendingPresetTarget,'pending preset target survives publication rerender');
layout.matches=false; layout.change();
assert.equal(grid.children[2],detail,'desktop change follows the two-card row without publication');
layout.matches=true; layout.change();
assert.equal(grid.children[1],detail,'phone change follows the selected one-card row without publication');
entries=[]; context.render(); assert.equal(detail.hidden,true); assert.equal(detail.parent,grid);
entries=[{key:'early',component_id:'early',available:true}]; context.render();
assert.equal(detail.hidden,false); assert.equal(detail.parent,grid);
'''
    result = subprocess.run(['node', '-e', runner, placement + render], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "window.addEventListener('online', refreshStatus);\n  installGalleryLayoutListener();" in source


def test_pending_thumbnail_repaints_every_replacement_canvas():
    source = Path('web/static/js/composer_slice.js').read_text()
    drawing = source[source.index('function stableGalleryJson'):source.index('function showGalleryDetail')]
    runner = r'''
const assert = require('node:assert/strict');
const vm = require('node:vm');
const state = {gallery: {digest:'catalog', thumbnails: new Map()}, background: {gain:.5}};
const pending = [];
let requests = 0;
const context = {state, GALLERY_FIXED_PREVIEW:{},
  galleryScene: entry => ({background: state.background, animation: entry}),
  preview: () => { requests++; return new Promise(resolve => pending.push(resolve)); },
  drawFrameIntoCanvas: (canvas, frame) => { canvas.frame = frame; },
};
const makeCanvas = () => ({setAttribute(key, value) { this[key] = value; }});
vm.runInNewContext(process.argv[1] + '; this.draw = drawGalleryThumbnail;', context);
(async () => {
  const entry = {key:'sparkle', name:'Sparkle', parameters:{seed:1}};
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
  const unchanged = context.draw(makeCanvas(), entry);
  assert.equal(requests, 3, 'matching Scene inputs reuse the resolved frame');
  const altered = context.draw(makeCanvas(), {...entry, parameters:{seed:2}});
  assert.equal(requests, 4, 'a changed animation default must use a new cache key');
  await unchanged;
  pending.shift()({frame}); await altered;
  state.background = {gain:.8};
  const changedBackground = context.draw(makeCanvas(), entry);
  assert.equal(requests, 5, 'a changed resolved Scene background must use a new cache key');
  pending.shift()({frame}); await changedBackground;
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    result = subprocess.run(['node', '-e', runner, drawing], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
