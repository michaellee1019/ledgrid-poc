#!/usr/bin/env node
// Loopback-only: verify preparing playlist drafts never publishes a live scene.
import assert from 'node:assert/strict';
import {chromium} from './node_modules/playwright/index.mjs';
const url=process.argv[2]||'http://127.0.0.1:8777/composer';
assert.ok(['localhost','127.0.0.1'].includes(new URL(url).hostname));
const browser=await chromium.launch({headless:true});
try {
 for(const width of [1440,390]) {
  const page=await browser.newPage({viewport:{width,height:width===390?844:900},serviceWorkers:'block'});
  const liveWrites=[];let runCalls=0,saveCalls=0,saved=null,holdRead=null,failRead=false;
  const scene={schema:'ledgrid.scene.v2',background:{component_id:'solid_background',version:1,provider:'python',role:'background',parameters:{gain:.62,seed:4201}},animation:{component_id:'aurora_curtains',version:1,provider:'python',role:'animation',parameters:{curtain_density:.56,fold_depth:.58,glow_intensity:.62,source_fps:30,seed:4201}},widgets:[],plants:{effects:{version:1,active:[],strengths:{}}},look:{palette_id:'mist',pace:.7,presentation_brightness:.82}};
  await page.route('**/api/composer/library',route=>route.fulfill({json:{items:[{kind:'starter',id:'built-in',name:'Built-in Aurora'},{kind:'look',id:'saved-look',name:'Saved Evening'}],favorites:[]}}));
  await page.route('**/api/composer/starters/built-in',route=>route.fulfill({json:{starter:{name:'Built-in Aurora',scene}}}));
  await page.route('**/api/composer/looks/saved-look',async route=>{if(holdRead)await holdRead;if(failRead)return route.fulfill({status:500,json:{error:'Saved scene read failed'}});return route.fulfill({json:{look:{id:'saved-look',name:'Saved Evening',scene:{...scene,look:{...scene.look,presentation_brightness:.37}}}}});});
  await page.route('**/api/composer/playlists',route=>{if(route.request().method()==='POST'){saveCalls++;saved={...route.request().postDataJSON(),id:'picker-list'};return route.fulfill({json:{playlist:saved}});}return route.fulfill({json:{playlists:saved?[saved]:[]}});});
  await page.route('**/api/composer/playlists/run',route=>{runCalls++;return route.fulfill({json:{accepted:{request_id:'fixture-request',run_id:'fixture-run'}}});});
  page.on('request',request=>{const path=new URL(request.url()).pathname;if(request.method()!=='GET'&&(path==='/api/composer/scene'||path==='/api/v1/scene'||path.endsWith('/open')))liveWrites.push(path);});
  await page.goto(url);await page.waitForSelector('.gallery-select');
  await page.locator('[data-workspace-view="playlists"]').click();await page.locator('#playlistName').fill('Prepared without playback');
  const edited=await page.locator('#animationChoice').inputValue();
  const open=async()=>{await page.locator('#playlistBrowse').click();await page.waitForSelector('.playlist-picker-choice');};
  const choose=async(name)=>{await page.locator('#playlistPickerSearch').fill(name);await page.locator('.playlist-picker-choice input').first().check();};
  await open();await choose('Saved Evening');await page.locator('#playlistPickerCancel').click();
  assert.equal(await page.locator('.playlist-entry').count(),0);assert.equal(await page.locator('#playlistName').inputValue(),'Prepared without playback');
  await open();await choose('Built-in Aurora');await choose('Saved Evening');await choose('Fireworks');
  await page.locator('#playlistPickerAdd').evaluate(button=>{button.click();button.click();});await page.waitForFunction(()=>!document.querySelector('#playlistPickerDialog').open);
  assert.equal(await page.locator('.playlist-entry').count(),3);assert.match(await page.locator('#playlistDraftStatus').textContent(),/Unsaved/);
  assert.match(await page.locator('.playlist-entry').last().textContent(),/Fireworks/);assert.equal(await page.locator('#animationChoice').inputValue(),edited);
  assert.equal(await page.locator('.composer').getAttribute('data-view'),'playlists');assert.equal(liveWrites.length,0);assert.equal(runCalls,0);assert.equal(saveCalls,0);
  // Read failure is atomic and retains selection for retry; Cancel wins delayed reads.
  await open();await choose('Built-in Aurora');await choose('Saved Evening');failRead=true;await page.locator('#playlistPickerAdd').click();
  await page.waitForFunction(()=>document.querySelector('#playlistPickerMessage').textContent.includes('No entries were added'));assert.equal(await page.locator('.playlist-entry').count(),3);
  failRead=false;let release;holdRead=new Promise(resolve=>release=resolve);await page.locator('#playlistPickerAdd').click();await page.locator('#playlistPickerCancel').click();release();holdRead=null;
  await page.waitForTimeout(100);assert.equal(await page.locator('.playlist-entry').count(),3);assert.equal(liveWrites.length,0);
  assert.match(await page.locator('#playlistStatus').textContent(),/Playback unchanged/);
  await page.screenshot({path:`/private/tmp/composer-playlist-picker-${width}.png`});
  await page.locator('#playlistStart').click();await page.waitForFunction(()=>document.querySelector('#playlistStatus').textContent!=='Saving…');
  await page.waitForTimeout(200);assert.equal(runCalls,1);assert.equal(saveCalls,1);assert.equal(saved.entries.length,3);assert.equal(saved.entries[1].scene.look.presentation_brightness,.37);
  await page.locator('[data-workspace-view="browse"]').click();await page.locator('#gallerySearch').fill('Aurora');
  const live=page.waitForRequest(request=>request.method()==='POST'&&new URL(request.url()).pathname==='/api/composer/scene');
  await page.getByRole('button',{name:/Play & edit Aurora/}).click();await live;assert.ok(liveWrites.length>0,'ordinary Browse retains live publication');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  console.log(JSON.stringify({width,preparationWrites:0,added:3,cancel:'unchanged including delayed read',failure:'atomic',play:'explicit only',normalBrowse:'live retained'}));await page.close();
 }
} finally {await browser.close();}
