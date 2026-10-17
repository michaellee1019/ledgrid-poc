#!/usr/bin/env node
import assert from 'node:assert/strict';
import {chromium} from './node_modules/playwright/index.mjs';

const url = process.argv[2] || 'http://127.0.0.1:8773/composer';
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(url).hostname));
const browser = await chromium.launch({headless: true});
try {
  for (const width of [1440, 390]) {
    const page = await browser.newPage({viewport: {width, height: 844}});
    await page.goto(url);
    await page.waitForSelector('.semantic-number', {state: 'attached'});
    // Inspect all pairs, including controls installed dynamically and hidden scenes.
    const pairs = await page.locator('.semantic-number').evaluateAll((wrappers) => wrappers.map((wrap) => {
      const range = wrap.querySelector('input[type=range]');
      const exact = wrap.querySelector('input[type=number]');
      return {id: exact.id, label: [...wrap.closest('label').childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent.trim()).filter(Boolean).join(' '),
        rangeName: range.getAttribute('aria-label'), exactName: exact.getAttribute('aria-label'),
        rangeDescription: range.getAttribute('aria-description'), exactDescription: exact.getAttribute('aria-description'), min: exact.min, max: exact.max};
    }));
    const names = [];
    for (const pair of pairs) {
      assert.ok(pair.exactName?.includes(pair.label), `${pair.id}: exact input must name its visible setting`);
      assert.ok(pair.rangeName?.includes(pair.label), `${pair.id}: slider must name its visible setting`);
      assert.match(pair.exactName, /exact value$/);
      assert.match(pair.rangeName, /adjustment$/);
      assert.equal(pair.rangeDescription, `Range ${pair.min} to ${pair.max}.`);
      assert.equal(pair.exactDescription, pair.rangeDescription);
      names.push(pair.rangeName, pair.exactName);
    }
    assert.equal(new Set(names).size, names.length, 'numeric names must be unique');
    assert.equal(await page.evaluate(() => { const ids = [...document.querySelectorAll('[id]')].map((e) => e.id); return new Set(ids).size === ids.length; }), true, 'no duplicate IDs');
    await page.locator('[data-workspace-view="edit"]').click();
    // Use the actual browser accessibility tree, not only the authored attributes.
    const cdp = await page.context().newCDPSession(page);
    async function checkPair(id) {
      const exact = page.locator(`#${id}`);
      const slider = page.locator(`.semantic-number:has(#${id}) .semantic-range`);
      const pair = pairs.find((item) => item.id === id);
      await slider.focus();
      const before = Number(await exact.inputValue());
      await page.keyboard.press('ArrowRight');
      assert.ok(Number(await exact.inputValue()) > before, `${id}: slider arrow changes exact value`);
      await page.keyboard.press('Tab');
      assert.equal(await exact.evaluate((e) => e === document.activeElement), true, `${id}: slider tabs to exact value`);
      await exact.fill('0.5');
      await page.keyboard.press('ArrowUp');
      assert.equal(await exact.inputValue(), '0.51', `${id}: exact arrow editing works`);
      await exact.press('Tab');
      assert.equal(await slider.inputValue(), '0.51', `${id}: numeric editing syncs slider`);
      const {nodes} = await cdp.send('Accessibility.getFullAXTree');
      assert.ok(nodes.some((n) => n.role?.value === 'slider' && n.name?.value === pair.rangeName), `${id}: named slider in AX tree`);
      assert.ok(nodes.some((n) => n.role?.value === 'spinbutton' && n.name?.value === pair.exactName && n.description?.value === pair.exactDescription), `${id}: named exact input and range in AX tree`);
    }
    await checkPair('sceneLuminance');
    await checkPair('curtainDensity');
    await page.locator('#animationChoice').selectOption('tetris');
    const dynamic = page.locator('#tetrisRisk');
    assert.equal(await dynamic.getAttribute('aria-label'), pairs.find((p) => p.id === 'tetrisRisk').exactName);
    await page.locator('#animationChoice').selectOption('aurora_curtains');
    await checkPair('foldDepth');
    const currentNames = await page.locator('.semantic-number input').evaluateAll((controls) => controls.map((c) => c.getAttribute('aria-label')));
    assert.deepEqual([...currentNames].sort(), [...names].sort(), 'names survive scene changes');
    console.log(JSON.stringify({width, pairs: pairs.length, uniqueExplicitNames: names.length, keyboard: 'slider → exact; arrows and numeric edits', accessibilityTree: 'slider + spinbutton names and range descriptions', dynamic: 'Tetris controls and Aurora return', screenReaderSpeech: 'untested'}));
    await page.close();
  }
} finally { await browser.close(); }
