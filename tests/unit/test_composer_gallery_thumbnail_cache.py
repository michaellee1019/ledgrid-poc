"""Gallery images remain visible across grid rebuilds without render requests."""
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
  galleryThumbnail:()=>new Element(),
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


def test_catalog_images_survive_scene_changes_and_report_missing_assets():
    source = Path('web/static/js/composer_slice.js').read_text()
    drawing = source[source.index('function galleryThumbnail'):source.index('function placeGalleryDetail')]
    runner = r'''
const assert = require('node:assert/strict');
const vm = require('node:vm');
class Element {
  setAttribute(key, value) { this[key] = value; }
  addEventListener(event, callback) { this[event] = callback; }
  replaceWith(node) { this.replacement = node; }
}
const context = {
  window:{ComposerGalleryPreviews:{sparkle:'/static/generated/gallery/sparkle-hash.png'}, ComposerPresetPreviews:{sparkle:{quiet:'/static/generated/gallery/sparkle-quiet.png',dense:'/static/generated/gallery/sparkle-dense.png'}}},
  document:{createElement:()=>new Element()},
  state:{scene:{look:{presentation_brightness:0, pace:0}}},
  preview:()=>{ throw new Error('Gallery must not queue simulation renders'); },
};
vm.runInNewContext(process.argv[1] + ';this.thumbnail=galleryThumbnail;', context);
const entry={component_id:'sparkle',name:'Sparkle'};
const first=context.thumbnail(entry);
assert.equal(first.src,context.window.ComposerGalleryPreviews.sparkle);
assert.equal(first.width,33); assert.equal(first.height,138);
assert.equal(first.loading,'lazy'); assert.match(first.alt,/representative catalog/);
context.state.scene.look={presentation_brightness:1.7,pace:2};
const replacement=context.thumbnail(entry);
assert.equal(replacement.src,first.src,'same cacheable image survives Scene edits and grid rebuilds');
first.error();
assert.equal(first.replacement.textContent,'Preview unavailable');
assert.equal(first.replacement['aria-label'],'Sparkle preview unavailable');
const missing=context.thumbnail({component_id:'missing',name:'Missing'});
assert.equal(missing.textContent,'Preview unavailable');
const quiet=context.thumbnail(entry,{preset_id:'quiet',name:'Quiet'});
const dense=context.thumbnail(entry,{preset_id:'dense',name:'Dense'});
assert.notEqual(quiet.src,dense.src);
assert.notEqual(quiet.src,first.src);
assert.equal(quiet.loading,'lazy');
assert.equal(context.thumbnail(entry,{preset_id:'absent',name:'Absent'}).textContent,'Preview unavailable');
quiet.error(); assert.equal(quiet.replacement['aria-label'],'Quiet preview unavailable');
'''
    result = subprocess.run(['node', '-e', runner, drawing], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
