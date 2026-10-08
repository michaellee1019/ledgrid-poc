#!/usr/bin/env node
// Isolated UI flow; managed metadata, saved definitions and playback are simulated.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {chromium} from './node_modules/playwright/index.mjs';
const url = process.argv[2] || 'http://127.0.0.1:8767/composer';
assert.ok(['127.0.0.1','localhost'].includes(new URL(url).hostname));
const browser = await chromium.launch({headless:true});
try {
  const page = await browser.newPage({viewport:{width:390,height:844},serviceWorkers:'block'});
  await page.addInitScript(() => { window.playlistMessages=[]; document.addEventListener('DOMContentLoaded',()=>new MutationObserver(()=>window.playlistMessages.push(document.querySelector('#playlistStatus').textContent)).observe(document.querySelector('#playlistStatus'),{childList:true,subtree:true,characterData:true})); });
  // Use shipped managed identities for the isolated no-wall fixture.
  const shipped=JSON.parse(readFileSync(new URL('../../web/static/generated/composer/bootstrap.v1.json',import.meta.url)));
  await page.route('**/api/v1/composer/bootstrap*',async route=>{
    const response=await route.fetch(); const body=await response.json();
    for(const component of body.components || []) {
      const known=shipped.components.find(c=>c.plugin_id===component.plugin_id && c.provider===component.provider && c.role===component.role);
      if(known?.browser_capabilities?.managed_identity) component.browser_capabilities=known.browser_capabilities;
    }
    await route.fulfill({response,json:body});
  });
  let saved=null;
  await page.route('**/api/composer/playlists',async route=>{
    if(route.request().method()==='POST') { saved={...route.request().postDataJSON(),id:'browser-fixture'}; await route.fulfill({json:{playlist:saved}}); }
    else await route.fulfill({json:{playlists:saved?[saved]:[]}});
  });
  await page.route('**/api/composer/playlists/browser-fixture',route=>route.fulfill({json:{playlist:saved}}));
  let phase='idle';
  const run='11111111-1111-4111-8111-111111111111';
  await page.route('**/api/composer/playlists/run', async route => {
    phase='running';
    await route.fulfill({json:{accepted:{run_id:run,request_id:run}}});
  });
  await page.route('**/api/composer/playlists/status*', route=>route.fulfill({json:{current:{phase,run_id:run,current_index:0,entry_count:2,current_entry:{label:'First'},next_entry:{label:'Second'},remaining_seconds:3},request:null}}));
  await page.goto(url);
  await page.waitForSelector('.gallery-select');
  let added = 0;
  for (const animation of ['Aurora','Fireworks']) {
    await page.locator('[data-workspace-view="browse"]').click();
    await page.locator('#gallerySearch').fill(animation);
    const publication=page.waitForResponse(response=>response.request().method()==='POST' && new URL(response.url()).pathname==='/api/composer/scene');
    await page.locator('.gallery-select').first().click(); await publication;
    await page.locator('#browsePlaylistAdd').click();
    added++;
    try { await page.waitForFunction(count=>document.querySelectorAll('.playlist-entry').length===count,added,{timeout:5000}); }
    catch(error) { throw new Error(`Add failed: ${JSON.stringify(await page.evaluate(()=>window.playlistMessages))}`); }
  }
  assert.equal(await page.locator('.playlist-entry').count(),2);
  await page.locator('.playlist-entry input').first().fill('3');
  await page.locator('.playlist-entry input').first().dispatchEvent('change');
  await page.locator('.playlist-entry input').last().fill('4');
  await page.locator('.playlist-entry input').last().dispatchEvent('change');
  await page.locator('.playlist-entry').last().getByRole('button',{name:/Move up/}).click();
  await page.locator('#playlistName').fill('Focused browser playlist');
  await page.locator('#playlistSave').click();
  try { await page.waitForFunction(()=>document.querySelector('#playlistStatus').dataset.state==='saved',{},{timeout:5000}); } catch(error) { throw new Error(JSON.stringify(await page.evaluate(()=>window.playlistMessages))); }
  const id=await page.locator('#playlistChoice').inputValue();
  await page.locator('#playlistChoice').selectOption('');
  await page.locator('#playlistChoice').selectOption(id);
  await page.waitForSelector('.playlist-entry');
  assert.deepEqual(await page.locator('.playlist-entry input').evaluateAll(inputs=>inputs.map(i=>i.value)),['4','3']);
  await page.locator('#playlistStart').click();
  await page.waitForFunction(()=>document.querySelector('.playback-playlist-summary').textContent.includes('Next: Second'));
  phase='overridden';
  await page.locator('[data-workspace-view="browse"]').click();
  await page.locator('#gallerySearch').fill('Aurora');
  const takeover=page.waitForRequest(request=>request.method()==='POST' && new URL(request.url()).pathname==='/api/composer/scene');
  await page.locator('.gallery-select').first().click(); await takeover;
  await page.waitForFunction(()=>document.querySelector('.playback-playlist-summary').textContent.includes('Live editing took over'));
  console.log(JSON.stringify({savedDefinition:'order and duration round trip',add:'direct from Browse',playback:'simulated current/next and takeover passed'}));
} finally {await browser.close();}
