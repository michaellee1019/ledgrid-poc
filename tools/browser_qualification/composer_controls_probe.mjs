#!/usr/bin/env node
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';

const url = process.argv[2] || 'http://127.0.0.1:8767/composer';
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(url).hostname));
const playwright = process.argv[3] ? pathToFileURL(process.argv[3]) : new URL('./node_modules/playwright/index.mjs', import.meta.url);
const {chromium} = await import(playwright.href);
const browser = await chromium.launch({headless: true});

const readScene = (page) => page.evaluate(() => window.ComposerEditorContext.snapshot().scene);
const acceptedEdit = (page, predicate) => page.waitForResponse((response) =>
  new URL(response.url()).pathname === '/api/composer/scene'
  && response.ok() && predicate(response.request().postDataJSON().scene));

try {
  for (const width of [1440, 390]) {
    const page = await browser.newPage({viewport: {width, height: 844}});
    const scriptErrors = [];
    page.on('pageerror', (error) => scriptErrors.push(error.message));
    await page.goto(url);
    await page.locator('[data-workspace-view="browse"]').click();
    const gallery = await page.request.get(new URL('/api/composer/gallery', url).href);
    assert.ok(gallery.ok());
    const catalog = await gallery.json();
    const fireworks = catalog.entries.find(({component_id}) => component_id === 'fireworks');
    await page.locator('#gallerySearch').fill(fireworks.name);
    const selected = acceptedEdit(page, (scene) => scene.animation.component_id === 'fireworks');
    await page.locator('.gallery-select').first().click();
    await selected;
    await page.locator('[data-workspace-view="edit"]').click();
    const controls = page.locator('#animationControls .control-disclosure[data-component-id="fireworks"]');
    await controls.waitFor();
    const primary = await controls.locator('.primary-control-grid > label').evaluateAll((labels) => labels.map(({dataset}) => dataset.parameter));
    assert.deepEqual(primary, fireworks.preferred_parameters.slice(0, 4));
    const advanced = controls.locator('details');
    await advanced.locator('summary').click();
    const seed = advanced.locator('[data-parameter="seed"] input[type="number"]');
    const seeded = acceptedEdit(page, (scene) => scene.animation.component_id === 'fireworks' && scene.animation.parameters.seed === 2468);
    await seed.fill('2468');
    await seeded;
    assert.equal(await advanced.getAttribute('open'), '');
    assert.equal(await seed.evaluate((e) => e === document.activeElement), true);
    assert.equal(await seed.inputValue(), '2468');
    const authored = await page.request.get(new URL('/api/composer/components/fireworks/presets', url).href);
    const [preset] = (await authored.json()).presets;
    const selectedPreset = acceptedEdit(page, (scene) => scene.animation.parameters.seed === preset.parameters.seed);
    await page.locator('#animationControls').getByRole('button', {name: preset.name, exact: true}).click();
    await selectedPreset;
    assert.equal(Number(await seed.inputValue()), preset.parameters.seed);
    if (preset.installation_effects) assert.deepEqual((await readScene(page)).plants.effects, preset.installation_effects);

    const widgets = page.locator('[data-panel-id="widgets"]');
    await widgets.locator('.panel-toggle').click();
    const beforeWidgets = (await readScene(page)).widgets;
    await widgets.locator('#widgetChoice').selectOption('clock_overlay');
    const added = acceptedEdit(page, (scene) => scene.widgets.length === beforeWidgets.length + 1);
    await widgets.locator('#addWidget').click();
    const authoredClock = (await added).request().postDataJSON().scene.widgets.at(-1);
    assert.deepEqual(authoredClock.placement, {mode: 'manual', strip_translation: 0, led_translation: -8});
    const clock = widgets.locator(`[data-widget-id="${authoredClock.id}"]`);
    await clock.getByRole('radio', {name: '24-hour', exact: true}).click();
    assert.equal((await readScene(page)).widgets.at(-1).component.parameters.format_24h, true);
    await clock.getByRole('switch', {name: 'Show Clock'}).click();
    assert.equal((await readScene(page)).widgets.at(-1).visible, false);
    await clock.getByRole('button', {name: 'Remove Clock'}).click();
    assert.deepEqual((await readScene(page)).widgets, beforeWidgets);

    const plants = page.locator('[data-panel-id="plants"]');
    await plants.locator('.panel-toggle').click();
    assert.ok(await plants.getByRole('switch', {name: 'Illuminate', exact: true}).isVisible());
    await page.locator('#animationChoice').selectOption('tetris');
    assert.equal(await page.getByRole('link', {name: 'Play Tetris on wall'}).getAttribute('href'), '/play/tetris');
    assert.equal(await controls.count(), 0);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.deepEqual(scriptErrors, []);
    console.log(JSON.stringify({width, primary, advanced: 'preserved focus', preset: preset.name, widgets: 'added, edited, hidden and removed', tetris: '/play/tetris'}));
    await page.close();
  }
} finally {
  await browser.close();
}
