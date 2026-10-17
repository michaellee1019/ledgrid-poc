#!/usr/bin/env node
// Loopback fixture only: exercise real saved-scene writes without installed output.
import assert from 'node:assert/strict';
import {chromium} from './node_modules/playwright/index.mjs';
const url = process.argv[2] || 'http://127.0.0.1:8775/composer';
assert.ok(['127.0.0.1','localhost'].includes(new URL(url).hostname));
const browser = await chromium.launch({headless:true});
try {
  for (const width of [1440,390]) {
    const page = await browser.newPage({viewport:{width,height:844},serviceWorkers:'block'});
    page.setDefaultTimeout(7000);
    const name = `Save probe ${width} ${Date.now()}`;
    const endpoint = path => new URL(`/api/composer/${path}`,url).href;
    const getLook = async id => (await (await page.request.get(endpoint(`looks/${id}`))).json()).look;
    const library = async () => (await (await page.request.get(endpoint('library'))).json()).items;
    await page.goto(url);
    await page.waitForFunction(()=>document.querySelector('#galleryCount').textContent.includes('animations'));
    await page.locator('#gallerySearch').fill('Aurora');
    await page.locator('.gallery-select').first().click();
    await page.locator('[data-workspace-view="edit"]').click();
    await page.locator('#sceneName').fill(name);
    // Start with a copy so repeated runs cannot overwrite a prior fixture selection.
    await page.locator('#saveAsScene').click();
    await page.locator('#sceneCopyDialog').waitFor({state:'visible'});
    assert.equal(await page.locator('#sceneCopyName').inputValue(), `${name} copy`);
    await page.locator('#sceneCopyName').fill(name);
    await page.locator('#sceneCopySubmit').click();
    await page.waitForFunction(()=>document.querySelector('#saveFeedback').dataset.state==='success');
    const originalItem = (await library()).find(item=>item.name===name);
    const original = await getLook(originalItem.id);
    assert.match(await page.locator('#saveFeedback').textContent(), /current revision/);
    await page.locator('#curtainDensity').fill('0.37');
    await page.locator('#curtainDensity').dispatchEvent('input');
    assert.equal(await page.locator('#saveFeedback').isVisible(), false, 'parameter edit clears success');
    await page.locator('[data-panel-id="plants"] .panel-toggle').click();
    await page.locator('#plantIlluminateEnabled').evaluate(e=>{e.checked=true;e.dispatchEvent(new Event('change',{bubbles:true}));});
    await page.locator('#plantIlluminateStrength').evaluate(e=>{e.value='.4';e.dispatchEvent(new Event('input',{bubbles:true}));});
    await page.locator('[data-panel-id="widgets"] .panel-toggle').click();
    await page.locator('#clockEnabled').evaluate(e=>{e.checked=true;e.dispatchEvent(new Event('change',{bubbles:true}));});
    await page.locator('#clockAcross').evaluate(e=>{e.value='3';e.dispatchEvent(new Event('input',{bubbles:true}));});
    await page.locator('[data-workspace-view="browse"]').click();
    await page.locator('[data-workspace-view="edit"]').click();
    assert.equal(await page.locator('#saveFeedback').isVisible(), false);
    await page.locator('#saveAsScene').click();
    assert.equal(await page.locator('#sceneCopyName').inputValue(), `${name} copy`);
    await page.locator('#sceneCopyName').fill(name);
    await page.locator('#sceneCopySubmit').click();
    await page.waitForFunction(()=>document.querySelector('#sceneCopyError').textContent.includes('Choose a different name'));
    assert.equal(await page.locator('#sceneCopyDialog').isVisible(),true);
    assert.equal(await page.locator('#curtainDensity').inputValue(),'0.37');
    assert.deepEqual(await getLook(original.id),original,'collision leaves original intact');
    let fail=true;
    await page.route('**/api/composer/looks',async route=>{
      if(route.request().method()==='POST' && fail) return route.fulfill({status:500,json:{error:'Save failed for probe.'}});
      return route.continue();
    });
    const copyName = `${name} edited`;
    await page.locator('#sceneCopyName').fill(copyName);
    await page.locator('#sceneCopySubmit').click();
    await page.waitForFunction(()=>document.querySelector('#sceneCopyError').textContent.includes('Save failed'));
    assert.equal(await page.locator('#curtainDensity').inputValue(),'0.37');
    fail=false;
    await page.locator('#sceneCopySubmit').click();
    await page.waitForFunction(()=>!document.querySelector('#sceneCopyDialog').open);
    const copyItem=(await library()).find(item=>item.name===copyName);
    const copy=await getLook(copyItem.id);
    assert.notEqual(copy.id,original.id);
    assert.equal(copy.scene.animation.parameters.curtain_density,.37);
    assert.equal(copy.scene.plants.effects.strengths.illuminate,.4);
    assert.equal(copy.scene.widgets.find(w=>w.component.component_id==='clock_overlay').placement.strip_translation,3);
    assert.deepEqual(await getLook(original.id),original,'copy leaves original ID, name, content intact');
    // A delayed successful save must not mark a newer edit clean.
    let release; const gate=new Promise(resolve=>release=resolve); let started;
    const arrived=new Promise(resolve=>started=resolve);
    await page.route(`**/api/composer/looks/${copy.id}`,async route=>{
      if(route.request().method()==='PUT'){started();await gate;}
      await route.continue();
    });
    await page.locator('#saveScene').click(); await arrived;
    await page.locator('#curtainDensity').fill('0.39');
    await page.locator('#curtainDensity').dispatchEvent('input');
    release();
    await page.waitForFunction(()=>document.querySelector('#saveFeedback').dataset.state==='dirty');
    assert.match(await page.locator('#saveFeedback').textContent(), /earlier revision.*unsaved/);
    await page.waitForFunction(async () => { const response=await fetch('/api/composer/recovery'); const body=await response.json(); return body.recovery?.scene.animation.parameters.curtain_density===.39; });
    await page.reload();
    await page.waitForFunction(expected=>document.querySelector('#sceneName').value===expected,copyName);
    await page.locator('[data-workspace-view="edit"]').click();
    assert.equal(await page.locator('#curtainDensity').inputValue(),'0.39','late save cannot erase newer recovery');
    await page.waitForFunction(()=>document.querySelector('#saveState').textContent==='Unsaved changes');
    await page.locator('#saveScene').click();
    await page.waitForFunction(()=>document.querySelector('#saveFeedback').dataset.state==='success');
    await page.locator('[data-workspace-view="browse"]').click();
    await page.locator('[data-browse-view="saved"]').click();
    const kinds=await page.locator('#libraryList .library-kind').allTextContents();
    assert.ok(kinds.length>=2);assert.ok(kinds.every(kind=>kind==='Saved'));
    assert.equal(await page.locator('.library-filters').count(),0,'one collection navigation');
    await page.locator('#libraryList button').filter({has:page.locator('span').filter({hasText:new RegExp(`^${name}$`)})}).click();
    await page.waitForFunction(expected=>document.querySelector('#sceneName').value===expected,name);
    assert.equal(await page.locator('#saveFeedback').isVisible(),false,'saved selection clears old feedback');
    assert.deepEqual(await getLook(original.id),original);
    if(process.argv[3]) await page.screenshot({path:`${process.argv[3]}-${width}.png`});
    console.log(JSON.stringify({width,copy:'unique suggestion/edit/collision/failure/retry',original:'ID/name/content preserved',edits:'animation + plant + clock retained',feedback:'current/earlier revision + invalidation',reload:'saved title restored',saved:'user scenes only; one navigation'}));
    await page.close();
  }
} finally {await browser.close();}
