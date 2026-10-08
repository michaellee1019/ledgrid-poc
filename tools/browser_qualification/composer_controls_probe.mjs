#!/usr/bin/env node
import assert from 'node:assert/strict';
import {chromium} from './node_modules/playwright/index.mjs';
const url = process.argv[2] || 'http://127.0.0.1:8767/composer';
assert.ok(['127.0.0.1','localhost'].includes(new URL(url).hostname));
const browser = await chromium.launch({headless:true});
try {
  for (const width of [1440,390]) {
    const context = await browser.newContext({viewport:{width,height:844}});
    const page = await context.newPage();
    await page.goto(url);
    await page.locator('#gallerySearch').fill('Fireworks');
    await page.locator('.gallery-select').first().click();
    await page.locator('[data-workspace-view="edit"]').click();
    await page.waitForSelector('.control-disclosure[data-component-id="fireworks"]');
    assert.equal(await page.locator('.primary-control-grid > label').count(),4);
    const advanced = page.locator('.control-disclosure > details');
    await advanced.locator('summary').click();
    const seed = page.locator('#fireworksSeed');
    await seed.fill('2468');
    await seed.dispatchEvent('change');
    assert.equal(await advanced.getAttribute('open'),'');
    assert.equal(await seed.evaluate(e=>e===document.activeElement),true);
    assert.equal(await seed.inputValue(),'2468');
    assert.equal(await page.locator('#lifeSeed').isVisible(),false);
    await page.locator('[data-panel-id="widgets"] .panel-toggle').click();
    assert.equal(await page.locator('[data-panel-id="widgets"] .semantic-switch').first().isVisible(),true);
    await page.locator('[data-panel-id="plants"] .panel-toggle').click();
    assert.equal(await page.locator('.final-optic-row .semantic-switch').first().isVisible(),true);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth > innerWidth),false);
    await page.locator('.inspector-dock').evaluate(e=>{e.scrollTop=0;});
    await page.screenshot({path:`/tmp/composer-controls-${width}.png`});
    console.log(JSON.stringify({width,primary:4,advanced:'preserves focus and state',widgetsAndPlants:'reachable'}));
    await context.close();
  }
} finally {await browser.close();}
