#!/usr/bin/env node
// Isolated regression for playlist draft loss; never connects to an installed wall.
import assert from 'node:assert/strict';
import {chromium} from './node_modules/playwright/index.mjs';
const url = process.argv[2] || 'http://127.0.0.1:8771/composer';
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(url).hostname));
const browser = await chromium.launch({headless:true});
try {
  const page = await browser.newPage({viewport:{width:390,height:844},serviceWorkers:'block'});
  const scene = component_id => ({schema:'ledgrid.scene.v2',animation:{component_id,parameters:{}},background:{component_id:'solid_background',parameters:{}},widgets:[],plants:{effects:{version:1,active:[],strengths:{}}},look:{palette_id:'mist',pace:.7,presentation_brightness:.82}});
  const original = {id:'saved-a',name:'Evening',entries:[
    {entry_id:'entry-a',label:'Same scene title',duration_seconds:45,scene:scene('aurora_curtains')},
    {entry_id:'entry-b',label:'Same scene title',duration_seconds:60,scene:scene('fireworks')},
  ]};
  const presets = await (await page.request.get(new URL('/api/composer/components/aurora_curtains/presets',url).href)).json();
  original.entries[0].scene.animation.parameters = presets.presets[0].parameters;
  let releasePresets; const pendingPresets = new Promise(resolve=>releasePresets=resolve);
  await page.route('**/api/composer/components/aurora_curtains/presets',async route=>{await pendingPresets;await route.fulfill({json:presets});});
  let saved=structuredClone(original), failSave=false, holdSave=null, releaseSave=null, holdLoad=null, releaseLoad=null;
  let writes=0, runs=0;
  await page.route('**/api/composer/playlists',route=>route.fulfill({json:{playlists:[{id:saved.id,name:saved.name,total_duration_seconds:105}]}}));
  await page.route('**/api/composer/playlists/saved-a',async route=>{
    if(route.request().method()==='PUT') {
      writes++;
      if(holdSave) await holdSave;
      if(failSave) return route.fulfill({status:500,json:{error:'Save failed for this check.'}});
      saved={...route.request().postDataJSON(),id:'saved-a'};
    } else if(holdLoad) await holdLoad;
    await route.fulfill({json:{playlist:saved}});
  });
  await page.route('**/api/composer/playlists/run',route=>{runs++; return route.fulfill({status:500,json:{error:'Unexpected playback'}});});
  await page.goto(url);
  await page.waitForFunction(()=>document.querySelector('#playlistChoice').options.length===2);
  await page.locator('[data-workspace-view="playlists"]').click();
  const choice=page.locator('#playlistChoice'), durations=page.locator('.playlist-entry input');
  const dialog=page.locator('#playlistUnsavedDialog');
  const values=()=>durations.evaluateAll(inputs=>inputs.map(input=>input.value));
  const dirty=()=>page.locator('#playlistDraftStatus').textContent();
  const changeDuration=async value=>{await durations.first().fill(value);await durations.first().dispatchEvent('change');};
  const chooseNew=async()=>{await choice.selectOption('');await dialog.waitFor({state:'visible',timeout:5000});};
  await choice.selectOption('saved-a');await page.waitForFunction(()=>document.querySelectorAll('.playlist-entry').length===2);
  assert.deepEqual(await values(),['45','60']);
  await durations.first().fill('31');
  releasePresets();
  await page.waitForFunction(name=>document.querySelector('.playlist-entry-description small').textContent.includes(name),presets.presets[0].name);
  assert.equal(await durations.first().evaluate(input=>input===document.activeElement),true,'metadata arrival retains input focus');
  assert.equal(await durations.first().inputValue(),'31','metadata arrival retains typed value');
  await durations.first().dispatchEvent('change');
  await chooseNew();
  await dialog.getByRole('button',{name:'Keep editing'}).click();
  assert.deepEqual(await values(),['31','60']);assert.equal(await choice.inputValue(),'saved-a');
  await chooseNew();await page.keyboard.press('Escape');assert.deepEqual(await values(),['31','60']);
  failSave=true;await chooseNew();await dialog.getByRole('button',{name:'Save changes'}).click();
  await page.waitForFunction(()=>document.querySelector('#playlistDraftError').textContent.includes('Save failed'));
  assert.deepEqual(await values(),['31','60']);assert.equal(await dirty(),'Unsaved playlist changes');
  await page.waitForTimeout(1100);assert.match(await page.locator('#playlistDraftError').textContent(),/Save failed/);
  assert.deepEqual(saved,original,'failed save preserves existing record');
  failSave=false;
  await page.locator('#playlistName').fill('Evening edited');
  await page.locator('.playlist-entry').last().getByRole('button',{name:/Move up/}).click();
  holdSave=new Promise(resolve=>releaseSave=resolve);
  await chooseNew();await dialog.getByRole('button',{name:'Save changes'}).click();
  await page.waitForFunction(()=>document.querySelector('#playlistName').disabled);
  assert.equal(await choice.isDisabled(),true);assert.equal(await durations.first().isDisabled(),true);
  releaseSave();holdSave=null;
  await page.waitForFunction(()=>document.querySelectorAll('.playlist-entry').length===0);
  assert.equal(saved.name,'Evening edited');assert.deepEqual(saved.entries.map(e=>e.duration_seconds),[60,31]);
  await choice.selectOption('saved-a');await page.waitForFunction(()=>document.querySelectorAll('.playlist-entry').length===2);
  assert.deepEqual(await values(),['60','31']);assert.equal(await dirty(),'Playlist saved');
  const identities=await page.locator('.playlist-entry-description small').allTextContents();
  assert.match(identities[0],/Fireworks/);assert.match(identities[1],/Aurora/);assert.ok(identities[1].includes(presets.presets[0].name));
  if(process.argv[3]) await page.screenshot({path:process.argv[3]});
  assert.equal(await page.locator('.playlist-entry-description img, .playlist-entry-description [aria-label$="preview unavailable"]').count(),2);
  assert.deepEqual(saved.entries.map(e=>e.scene),[original.entries[1].scene,original.entries[0].scene]);
  // Name-only edits, removals, additions, discard, and retained workspace drafts.
  await page.locator('#playlistName').fill('Unsaved name');assert.equal(await dirty(),'Unsaved playlist changes');
  await chooseNew();await dialog.getByRole('button',{name:'Discard',exact:true}).click();
  await page.waitForFunction(()=>document.querySelectorAll('.playlist-entry').length===0);
  await choice.selectOption('saved-a');await page.waitForFunction(()=>document.querySelectorAll('.playlist-entry').length===2);
  await page.locator('.playlist-entry').first().getByRole('button',{name:/Remove/}).click();
  await chooseNew();await dialog.getByRole('button',{name:'Keep editing'}).click();assert.equal(await durations.count(),1);
  await page.locator('#playlistAdd').click();await page.waitForFunction(()=>document.querySelectorAll('.playlist-entry').length===2 && !document.querySelector('#playlistAdd').disabled);
  assert.equal(await dirty(),'Unsaved playlist changes');
  await page.locator('[data-workspace-view="browse"]').click();await page.locator('[data-workspace-view="playlists"]').click();
  assert.equal(await durations.count(),2);assert.equal(await dirty(),'Unsaved playlist changes');
  await chooseNew();await dialog.getByRole('button',{name:'Discard',exact:true}).click();
  await page.waitForFunction(()=>document.querySelectorAll('.playlist-entry').length===0);
  holdLoad=new Promise(resolve=>releaseLoad=resolve);
  await choice.selectOption('saved-a');await page.waitForFunction(()=>document.querySelector('#playlistChoice').disabled);
  assert.equal(await page.locator('#playlistAdd').isDisabled(),true);assert.equal(await page.locator('#playlistName').isDisabled(),true);
  releaseLoad();holdLoad=null;await page.waitForFunction(()=>document.querySelectorAll('.playlist-entry').length===2);
  await changeDuration('33');
  let unloadPrompt=false;
  page.once('dialog',async native=>{unloadPrompt=native.type()==='beforeunload';await native.dismiss();});
  await page.reload({timeout:3000}).catch(()=>{});assert.equal(unloadPrompt,true);assert.deepEqual(await values(),['33','31']);
  assert.equal(writes,2);assert.equal(runs,0);
  console.log(JSON.stringify({drafts:'keep, Escape, failed save, save-and-switch, discard, workspace navigation and reload protected',identity:identities,locked:'save/load in flight',preserved:'scene payloads',playbackCommands:runs}));
} finally {await browser.close();}
