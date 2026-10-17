#!/usr/bin/env node
// Deliberate scripted navigation, not participant effort or a minimum route.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {chromium} from './node_modules/playwright/index.mjs';
const url=process.argv[2]||'http://127.0.0.1:8776/composer';
const mode=process.argv[3]||'candidate'; const evidence=process.argv[4]||'/tmp/composer-compact-editor';
assert.ok(['127.0.0.1','localhost'].includes(new URL(url).hostname));
assert.ok(['baseline','candidate'].includes(mode));await fs.mkdir(evidence,{recursive:true});
const browser=await chromium.launch({headless:true});const results=[];
try {
  const seed=await browser.newPage();await seed.goto(url);
  // Obtain the fixture's existing canonical scene, then normalize every run.
  let baseline=(await(await seed.request.get(new URL('/api/composer/recovery',url).href)).json()).recovery?.scene;
  if(!baseline){await seed.locator('#gallerySearch').fill('Aurora');await seed.locator('.gallery-select').first().click();await seed.waitForFunction(async()=>Boolean((await(await fetch('/api/composer/recovery')).json()).recovery?.scene));baseline=(await(await seed.request.get(new URL('/api/composer/recovery',url).href)).json()).recovery.scene;}
  baseline.animation={...baseline.animation,component_id:'aurora_curtains',parameters:{curtain_density:.56,fold_depth:.58,glow_intensity:.62,source_fps:30,seed:4201}};baseline.widgets=[];baseline.plants.effects={version:1,active:[],strengths:{}};baseline.look={palette_id:'mist',pace:.7,presentation_brightness:.82};await seed.close();
  for(const [width,height]of[[1440,900],[390,844],[320,568]]){
    const page=await browser.newPage({viewport:{width,height},serviceWorkers:'block'});page.setDefaultTimeout(8000);const errors=[];page.on('pageerror',error=>errors.push(error.stack));
    if(mode==='baseline'){
      await page.route('**/static/js/composer_compact_editor.js*',route=>route.fulfill({contentType:'application/javascript',body:''}));
      await page.route('**/static/css/composer_slice.css*',route=>route.fulfill({contentType:'text/css',path:`${evidence}/baseline.css`}));
    }
    await page.request.post(new URL('/api/composer/built-ins/open',url).href,{data:{scene:baseline,client_id:crypto.randomUUID()}});
    await page.goto(url);await page.waitForSelector('.semantic-number',{state:'attached'});await page.locator('[data-workspace-view="edit"]').click();
    await page.waitForFunction(()=>document.querySelector('#curtainDensity').value==='0.56');
    const dock=page.locator('.inspector-dock');const trace=[];let wheels=0,wheelPixels=0,automaticPixels=0,jumps=0,jumpPixels=0;
    const offset=()=>dock.evaluate(e=>e.scrollTop);
    async function checkpoint(label){const data=await page.evaluate(()=>{const d=document.querySelector('.inspector-dock'),r=d.getBoundingClientRect();const rect=s=>{const e=document.querySelector(s),v=e.getBoundingClientRect();return{top:v.top,bottom:v.bottom,height:v.height,width:v.width,scroll:e.scrollHeight>e.clientHeight+1};};const visible=s=>{const v=rect(s);return v.top>=r.top&&v.bottom<=r.bottom&&v.width>0;};return{offset:d.scrollTop,editorHeight:d.clientHeight,contextVisible:visible('.scene-save-controls'),brightnessVisible:visible('#sceneLuminance'),speedVisible:visible('#sceneSpeed'),preview:rect('#scenePreview'),previewPane:rect('.preview-pane'),output:rect('.operations-pane'),stop:rect('#liveAction'),name:document.querySelector('#workingSceneContext').textContent,dirty:document.querySelector('#saveState').textContent,overflow:document.documentElement.scrollWidth>innerWidth};});trace.push({label,...data});return data;}
    async function reach(selector){
      if(!await page.locator(selector).evaluate(e=>Boolean(e.closest('.inspector-dock'))))return;
      for(let i=0;i<60;i++){
        const g=await page.evaluate(s=>{const d=document.querySelector('.inspector-dock'),r=d.getBoundingClientRect(),v=document.querySelector(s).getBoundingClientRect(),c=document.querySelector('.scene-save-controls');return{top:r.top,bottom:r.bottom,left:r.left,right:r.right,y:v.top,end:v.bottom,sticky:getComputedStyle(c).position==='sticky'?c.getBoundingClientRect().height:0,offset:d.scrollTop};},selector);
        if(g.y>=g.top+g.sticky&&g.end<=g.bottom)return;
        const need=g.y<g.top+g.sticky?g.y-g.top-g.sticky-4:g.end-g.bottom+4;
        const dy=Math.sign(need)*Math.min(Math.abs(need),Math.max(60,(g.bottom-g.top)*.55));
        await page.mouse.move((g.left+g.right)/2,(g.top+g.bottom)/2);await page.mouse.wheel(0,dy);await page.waitForTimeout(100);wheels++;wheelPixels+=Math.abs(await offset()-g.offset);
      }throw new Error(`Could not deliberately reach ${selector} at${width}`);
    }
    async function act(selector,fn){await reach(selector);const before=await offset();await fn(page.locator(selector));await page.waitForTimeout(30);automaticPixels+=Math.abs(await offset()-before);}
    const click=selector=>act(selector,e=>e.click());
    async function number(selector,value){await act(selector,async e=>{const box=await e.boundingBox();await page.mouse.click(box.x+box.width/2,box.y+box.height/2);await page.keyboard.press(process.platform==='darwin'?'Meta+A':'Control+A');await page.keyboard.type(value);await page.keyboard.press('Tab');});}
    async function group(id){if(mode==='candidate'){const before=await offset();await page.locator('#editorControlGroup').selectOption(id);jumps++;jumpPixels+=Math.abs(await offset()-before);}else {await reach(`[data-panel-id="${id}"] > .pane-heading`);const selector=`[data-panel-id="${id}"] > .pane-heading > .panel-toggle`;if(await page.locator(selector).isVisible()&&await page.locator(selector).getAttribute('aria-expanded')==='false')await click(selector);}}
    const stats=()=>({wheelActions:wheels,wheelPixels,automaticFocusOrLocatorPixels:automaticPixels,groupJumps:jumps,jumpPixels});
    await checkpoint('entry');await number('#sceneLuminance','.35');await act('#sceneSpeed',async e=>{await e.press('Home');await e.press('ArrowRight');await e.press('ArrowRight');});await checkpoint('dim-slow');await click('.semantic-palette[data-value="ember"]');await click('#undoScene');await checkpoint('palette-undo');
    const title=`FLOW ${mode} ${width} ${Date.now()}`;
    if(mode==='candidate'){await page.locator('[aria-controls="editorSceneDetails"]').click();await page.locator('#sceneName').fill(title);await page.locator('#editorSceneDetails').getByRole('button',{name:'Save scene',exact:true}).click();}else {await number('#sceneName',title);await click('#saveScene');}
    await page.waitForFunction(()=>document.querySelector('#saveFeedback').dataset.state==='success');
    if(mode==='candidate')await page.locator('#editorSceneDetails').getByRole('button',{name:'Back to editing'}).click();
    const f1=stats();await checkpoint('saved');wheels=wheelPixels=automaticPixels=jumps=jumpPixels=0;
    await group('animation');await number('#curtainDensity','.57');await group('plants');await click('[data-panel-id="plants"] .semantic-switch[aria-label="Illuminate"]');await act('#plantIlluminateStrength',async e=>{await e.press('Home');for(let i=0;i<11;i++)await e.press('ArrowRight');});
    await group('widgets');await click('[data-panel-id="widgets"] .semantic-switch[aria-label="Show time"]');await act('#clockOffset',e=>e.press('ArrowUp'));await group('animation');await number('#curtainDensity','.58');await checkpoint('cross-group-return');
    const beforeResume=await offset();const values=await page.evaluate(()=>({density:document.querySelector('#curtainDensity').value,plant:document.querySelector('#plantIlluminateStrength').value,clock:document.querySelector('#clockOffset').value}));
    for(const view of['browse','playlists','edit'])await page.locator(`[data-workspace-view="${view}"]`).click();
    const resumed=await checkpoint('resumed');assert.equal(resumed.offset,beforeResume);assert.equal(await page.locator('[data-panel-id="widgets"] > .pane-heading > .panel-toggle').getAttribute('aria-expanded'),'true');assert.equal(await page.locator('[data-panel-id="plants"] > .pane-heading > .panel-toggle').getAttribute('aria-expanded'),'true');assert.deepEqual(await page.evaluate(()=>({density:document.querySelector('#curtainDensity').value,plant:document.querySelector('#plantIlluminateStrength').value,clock:document.querySelector('#clockOffset').value})),values);
    const f2=stats();let comparison='baseline has no contextual comparison',largePreview=null;
    if(mode==='candidate'){
      assert.equal(resumed.contextVisible,true);assert.match(resumed.dirty,/Unsaved/);assert.ok(resumed.stop.top>=resumed.output.top&&resumed.stop.bottom<=height,'Stop visible without footer scroll');
      const detailsButton=page.locator('[aria-controls="editorSceneDetails"]');await detailsButton.focus();await page.keyboard.press('Enter');
      await page.waitForFunction(()=>document.querySelector('#editorSceneChanges').textContent.includes('current 0.58'));
      await page.screenshot({path:`${evidence}/comparison-${width}.png`});
      const changed=await page.locator('#editorSceneChanges').textContent();assert.match(changed,/Density.*saved 0.56.*current 0.58/);assert.match(changed,/Illuminate/);assert.match(changed,/Clock/);comparison=changed;
      await page.keyboard.press('Escape');await page.waitForFunction(()=>!document.querySelector('#editorSceneDetails').open);assert.equal(await detailsButton.evaluate(e=>e===document.activeElement),true);assert.equal(await offset(),beforeResume);
      const small=await page.locator('#scenePreview').boundingBox();await page.locator('[aria-label="Enlarge preview"]').click();const big=await page.locator('#scenePreview').boundingBox();assert.ok(big.height>small.height);assert.equal(await page.locator('#previewStatus').evaluate(e=>e.parentElement.id==='editorLargePreview'),true,'full preview status is available in enlarged view');assert.ok(Math.abs(big.width/big.height-33/138)<.02);largePreview={smallHeight:small.height,largeHeight:big.height};await page.keyboard.press('Escape');await page.waitForFunction(()=>document.querySelector('#scenePreview').parentElement.classList.contains('preview-stage'));assert.equal(await page.locator('#scenePreview').evaluate(e=>e.parentElement.classList.contains('preview-stage')),true);
      for(const selector of['#saveScene','#editorControlGroup','[aria-controls="editorSceneDetails"]','#curtainDensity'])if(width<761)assert.ok((await page.locator(selector).boundingBox()).height>=44,`${selector}:44px target`);
      const selectedId=await page.evaluate(()=>window.ComposerEditorContext.snapshot().selection.id);
      let unavailable=true;
      await page.route(`**/api/composer/looks/${selectedId}`,route=>route.request().method()==='GET'&&unavailable?route.fulfill({status:503,json:{error:'Comparison unavailable for probe'}}):route.continue());
      await detailsButton.click();await page.waitForFunction(()=>document.querySelector('#editorSceneDetails').textContent.includes('Saved comparison unavailable.'));
      assert.equal(await page.getByRole('button',{name:'Retry saved comparison'}).isVisible(),true);
      assert.equal(await page.locator('#curtainDensity').inputValue(),values.density,'comparison failure preserves draft');
      unavailable=false;await page.getByRole('button',{name:'Retry saved comparison'}).click();
      await page.waitForFunction(()=>document.querySelector('#editorSceneChanges').textContent.includes('current 0.58'));
      await page.keyboard.press('Escape');await page.waitForFunction(()=>!document.querySelector('#editorSceneDetails').open);
      assert.equal(await page.evaluate(()=>{const s=window.ComposerEditorContext.snapshot();s.scene.look.pace=999;return window.ComposerEditorContext.snapshot().scene.look.pace!==999;}),true,'UI snapshot cannot mutate authored scene');
    }
    await page.screenshot({path:`${evidence}/${mode}-${width}.png`});
    if(mode==='candidate'){
      const draft=await page.evaluate(()=>window.ComposerEditorContext.snapshot().scene);
      const id=await page.evaluate(()=>window.ComposerEditorContext.snapshot().selection.id);
      await page.route(`**/api/composer/looks/${id}`,route=>route.request().method()==='PUT'?route.fulfill({status:500,json:{error:'Save failed visibly for probe.'}}):route.continue());
      assert.equal(await page.locator('#editorSceneDetails').evaluate(e=>e.open),false);
      await page.locator('#saveScene').click();
      await page.waitForFunction(()=>document.querySelector('#saveFeedback').textContent.includes('Save failed visibly'));
      assert.equal(await page.locator('#saveFeedback').isVisible(),true,'closed-details Save failure stays visible');
      assert.deepEqual(await page.evaluate(()=>window.ComposerEditorContext.snapshot().scene),draft,'failed Save preserves draft');
      await page.locator('[aria-controls="editorSceneDetails"]').click();
      assert.equal(await page.locator('#editorSaveFeedback').isVisible(),true,'modal exposes the same save feedback');
      await page.keyboard.press('Escape');await page.waitForFunction(()=>!document.querySelector('#editorSceneDetails').open);
      await page.screenshot({path:`${evidence}/save-failure-${width}.png`});
    }
    assert.equal(resumed.overflow,false);assert.deepEqual(errors,[]);
    const row={mode,width,height,f1,f2,sectionSwitches:3,resume:{before:beforeResume,after:resumed.offset,values},comparison,largePreview,trace,limits:'Scripted expert route; no participants, physical wall, touch keyboard or screen-reader speech'};results.push(row);await fs.writeFile(`${evidence}/${mode}.json`,JSON.stringify(results,null,2));console.log(JSON.stringify({mode,width,f1,f2,resume:resumed.offset,contextVisible:resumed.contextVisible,previewHeight:resumed.preview.height,stopVisible:resumed.stop.bottom<=height}));await page.close();
  }
}finally{await browser.close();}
