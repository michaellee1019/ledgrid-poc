#!/usr/bin/env node
// Isolated legacy publication fixture: asserts browser intent, not physical output.
import assert from 'node:assert/strict';
import {chromium} from './node_modules/playwright/index.mjs';
const url=process.argv[2] || 'http://127.0.0.1:8767/composer';
assert.ok(['127.0.0.1','localhost'].includes(new URL(url).hostname));
const browser=await chromium.launch({headless:true});
try {
 for(const width of [1440,390]) {
  const page=await browser.newPage({viewport:{width,height:844},serviceWorkers:'block'});
  const changes=[];
  page.on('request',r=>{if(r.method()==='POST' && new URL(r.url()).pathname==='/api/composer/scene') changes.push(r.postDataJSON().scene.look.pace);});
  await page.goto(url);
  await page.waitForFunction(()=>document.querySelector('#sceneIdentity').textContent.startsWith('r'));
  await page.locator('[data-workspace-view="edit"]').click();
  const speed=page.locator('#sceneSpeed'); const before=Number(await speed.inputValue());
  const next=before===1.4?1.5:1.4;
  await speed.dispatchEvent('pointerdown');
  for(const value of [1.2,next]) {
   const response=page.waitForResponse(r=>r.request().method()==='POST' && new URL(r.url()).pathname==='/api/composer/scene');
   await speed.evaluate((e,v)=>{e.value=v;e.dispatchEvent(new Event('input',{bubbles:true}));},String(value)); await response;
  }
  await speed.dispatchEvent('change'); await speed.dispatchEvent('pointerup');
  const undo=page.waitForResponse(r=>r.request().method()==='POST' && new URL(r.url()).pathname==='/api/composer/scene');
  await page.locator('#undoScene').click(); await undo;
  assert.equal(Number(await speed.inputValue()),before);
  assert.equal(changes.at(-1),before);
  await page.locator('#redoScene').click(); await page.waitForFunction(v=>Number(document.querySelector('#sceneSpeed').value)===v,next);
  assert.equal(await page.locator('.scene-save-controls #saveState').count(),1);
  await page.locator('#sceneName').fill(`Browser Undo ${width}`);
  await page.locator('#saveAsScene').click();
  await page.waitForFunction(()=>document.querySelector('#saveFeedback').dataset.state==='success');
  assert.match(await page.locator('#saveState').textContent(),/Saved/);
  console.log(JSON.stringify({width,undo:'gesture restores prior speed and publishes',save:'separate successful copy'}));
  await page.close();
 }
} finally {await browser.close();}
