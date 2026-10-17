#!/usr/bin/env node
// All controller traffic is simulated against a loopback Composer fixture.
import assert from 'node:assert/strict';
import {chromium} from './node_modules/playwright/index.mjs';
const url=process.argv[2]||'http://127.0.0.1:8774/composer';
assert.ok(['localhost','127.0.0.1'].includes(new URL(url).hostname));
const browser=await chromium.launch({headless:true});
try {
 const page=await browser.newPage({viewport:{width:390,height:844},serviceWorkers:'block'});
 const scene={schema:'ledgrid.scene.v2',background:{component_id:'solid_background',version:1,provider:'python',role:'background',parameters:{gain:.62,seed:4201}},animation:{component_id:'aurora_curtains',version:1,provider:'python',role:'animation',parameters:{curtain_density:.56,fold_depth:.58,glow_intensity:.62,source_fps:30,seed:4201}},widgets:[],plants:{effects:{version:1,active:[],strengths:{}}},look:{palette_id:'mist',pace:.7,presentation_brightness:.82}};
 let offline=false,stopFails=true,putCount=0,deleteCount=0;
 const observation={controller_session_id:'fixture',controller_state_revision:1,freshness:'stale',is_running:true,scene,scene_digest:'observed-aurora',brightness:0,animation_speed_scale:.3,target_fps:150,receiver_connectivity:Array.from({length:5},(_,device_index)=>({device_index,receiver_connected:true,receiver_status_observed_at:Date.now()/1000}))};
 await page.route('**/api/v1/composer/bootstrap*',async route=>{const response=await route.fetch();const body=await response.json();body.capabilities.server_actions.activation_available=true;await route.fulfill({response,json:body});});
 await page.route('**/api/v1/composer/settings/observed*',route=>offline?route.abort():route.fulfill({json:observation}));
 await page.route('**/api/v1/scene',async route=>{
  if(route.request().method()==='PUT') {putCount++;const body=route.request().postDataJSON();assert.equal(body.global_settings.output.brightness,0);observation.is_running=true;await route.fulfill({json:{request_id:`scene-${putCount}`}});}
  else if(route.request().method()==='DELETE') {deleteCount++;if(stopFails)return route.fulfill({status:500,json:{error:'Simulated Stop failure'}});observation.is_running=false;observation.command_result={request_id:'stop-success',state:'completed'};await route.fulfill({json:{request_id:'stop-success'}});}
  else await route.fulfill({json:{scene}});
 });
 let saved={id:'saved-a',name:'Status check',entries:[{entry_id:'entry-a',label:'Aurora',duration_seconds:45,scene}]};
 let forceStartFailure=false,stopCalls=0,releaseStop=null;
 let writes=0,startCalls=[],playlistCurrent={phase:'stopped',run_id:'old-run'},playlistRequest=null;
 await page.route('**/api/composer/playlists',route=>route.fulfill({json:{playlists:[{...saved,total_duration_seconds:45}]}}));
 await page.route('**/api/composer/playlists/saved-a',route=>{if(route.request().method()==='PUT'){writes++;saved={...route.request().postDataJSON(),id:'saved-a'};}return route.fulfill({json:{playlist:saved}});});
 await page.route('**/api/composer/playlists/status*',route=>route.fulfill({json:{current:playlistCurrent,request:playlistRequest}}));
 await page.route('**/api/composer/playlists/run',route=>{
  const key=route.request().headers()['idempotency-key'];startCalls.push(key);
  if(forceStartFailure||startCalls.length===1)return route.fulfill({status:500,json:{error:'Start reply lost'}});
  playlistRequest={phase:'running',request_id:key,run_id:'one-run'};playlistCurrent={...playlistRequest,current_index:0,entry_count:1,current_entry:{label:'Aurora'},remaining_seconds:40,next_entry:null};
  return route.fulfill({json:{accepted:playlistRequest}});
 });
 await page.route('**/api/composer/playlists/stop',async route=>{
  stopCalls++;
  if(stopCalls===1)return route.fulfill({status:500,json:{error:'Stop reply lost'}});
  await new Promise(resolve=>releaseStop=resolve);
  playlistRequest={phase:'stopped',request_id:'stop-request',run_id:'one-run'};playlistCurrent=playlistRequest;
  return route.fulfill({json:{accepted:playlistRequest}});
 });
 await page.goto(url);await page.waitForSelector('.gallery-select');
 const title=page.locator('#operations-title'),stop=page.locator('#liveAction');
 await page.waitForFunction(()=>document.querySelector('#operations-title').textContent==='Playback unknown');
 assert.equal(await stop.textContent(),'Stop output');assert.equal(await stop.isDisabled(),true);assert.equal(await page.locator('#resumeScene').isVisible(),false);
 observation.freshness='fresh';observation.is_running=false;
 await page.waitForFunction(()=>document.querySelector('#operations-title').textContent==='Output stopped');
 await page.locator('#resumeScene').evaluate(button=>{button.click();button.click();});
 await page.waitForFunction(()=>document.querySelector('#operations-title').textContent.includes('Playing'));
 assert.equal(putCount,1,'rapid Resume clicks coalesce');
 await page.locator('#gallerySearch').fill('Fireworks');await page.locator('.gallery-select').first().click();
 await page.waitForFunction(()=>document.querySelector('#requestedSceneSummary').textContent.includes('Fireworks')&&!document.querySelector('#liveAction').disabled);
 assert.match(await title.textContent(),/Aurora/);await page.locator('[data-workspace-view="edit"]').click();
 assert.match(await page.locator('#workingSceneContext').textContent(),/Fireworks/);
 assert.equal(putCount,2);
 offline=true;await page.waitForFunction(()=>document.querySelector('#operations-title').textContent==='Playback unknown');
 assert.equal(await stop.textContent(),'Stop output');assert.equal(await page.locator('#resumeScene').isVisible(),false);
 offline=false;await page.waitForFunction(()=>document.querySelector('#operations-title').textContent.includes('Playing'));
 await stop.click();await page.locator('#readinessDialog').waitFor({state:'visible'});await page.locator('#readinessDialog [data-dialog-close]').click();
 assert.equal(await page.locator('#retryWallAction').textContent(),'Retry stop');stopFails=false;await page.locator('#retryWallAction').click();
 await page.waitForFunction(()=>document.querySelector('#operations-title').textContent==='Output stopped');assert.equal(deleteCount,2);assert.equal(putCount,2,'retry Stop cannot activate scene');
 await page.locator('[data-workspace-view="playlists"]').click();await page.locator('#playlistChoice').selectOption('saved-a');await page.waitForSelector('.playlist-entry');
 await page.locator('#playlistStart').click();await page.locator('#playlistActionFailure').waitFor({state:'visible'});
 const failure=await page.locator('#playlistActionMessage').textContent();await page.waitForTimeout(1200);
 assert.equal(await page.locator('#playlistActionMessage').textContent(),failure);assert.equal(await page.locator('#playlistStart').isDisabled(),true);
 await page.locator('#playlistRefreshStatus').click();assert.equal(startCalls.length,1);
 await page.locator('#playlistRetryAction').evaluate(button=>{button.click();button.click();});
 await page.waitForFunction(()=>document.querySelector('#playlistActionFailure').hidden);
 assert.equal(startCalls.length,2);assert.equal(startCalls[0],startCalls[1]);assert.equal(writes,1);
 await page.locator('#playlistStop').click();await page.waitForFunction(()=>document.querySelector('#playlistActionMessage').textContent.includes('Stop reply lost'));
 await page.locator('#playlistRetryAction').evaluate(button=>{button.click();button.click();});
 await page.waitForFunction(()=>document.querySelector('#playlistRetryAction').disabled);
 assert.equal(stopCalls,2,'rapid Stop retries issue one pending request');assert.equal(await page.locator('#playlistRefreshStatus').isDisabled(),true);
 releaseStop();await page.waitForFunction(()=>document.querySelector('#playlistActionFailure').hidden);
 forceStartFailure=true;playlistRequest=null;
 await page.locator('#playlistStart').click();await page.waitForFunction(()=>document.querySelector('#playlistActionMessage').textContent.includes('Start reply lost'));
 await page.locator('#playlistStop').click();await page.locator('#playlistUnlockEditing').waitFor({state:'visible'});
 assert.match(await page.locator('#playlistActionMessage').textContent(),/may still run/);
 const startsBeforeUnlock=startCalls.length;
 await page.locator('#playlistUnlockEditing').click();assert.equal(await page.locator('#playlistName').isDisabled(),false);
 assert.equal(startCalls.length,startsBeforeUnlock,'manual recovery never replays cancelled start');
 assert.match(await page.locator('#playlistActionMessage').textContent(),/may still run/);
 await page.screenshot({path:'/private/tmp/composer-playback-status-phone.png'});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 console.log(JSON.stringify({unknown:'stale and offline',scenes:'edited/requested/observed distinct',resume:'one command, zero brightness preserved',stop:'retry stops rather than activates',playlist:'failure survives polls; retry same request with no resave',simulated:true}));
} finally {await browser.close();}
