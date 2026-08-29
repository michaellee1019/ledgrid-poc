#!/usr/bin/env node
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

function argumentsFrom(argv) {
  const values = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    if (!key?.startsWith('--') || argv[index + 1] == null) throw new Error(`Invalid argument ${key || ''}`);
    values[key.slice(2)] = argv[index + 1];
  }
  return values;
}

const args = argumentsFrom(process.argv.slice(2));
const requiredArguments = ['engine', 'base-url', 'manifest', 'playwright-module', 'output'];
for (const name of requiredArguments) {
  if (!args[name]) throw new Error(`Missing --${name}`);
}
const timeoutMs = Number(args['timeout-ms'] || 180000);
const startedAt = new Date().toISOString();
const result = {
  requested_engine: args.engine,
  reported_engine: null,
  browser_version: null,
  playwright_version: null,
  executed: false,
  started_at: startedAt,
  completed_at: startedAt,
  outcome: 'FAIL',
  journeys: [],
  fixture_status: null,
};

function mutationRequest(request) {
  const method = request.method().toUpperCase();
  if (!['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) return false;
  const pathname = new URL(request.url()).pathname;
  const authoringOnly = new Set([
    '/api/v1/composer/presets',
    '/api/v1/scene-presets',
    '/api/v1/scene/checks',
  ]);
  if (authoringOnly.has(pathname)) return false;
  if (/^\/api\/v1\/installation-profiles\/[0-9a-f]{64}\/(draft|publish)$/.test(pathname)) return false;
  return pathname.startsWith('/api/');
}

function observation(assertionId, passed, detail) {
  return {assertion_id: assertionId, passed: passed === true, detail: String(detail)};
}

async function selectBackground(
  page,
  preferredChip = 'Wasm',
  {activationReady = null, preferredName = null} = {},
) {
  await page.locator('#animationCatalogDisclosure').evaluate((element) => { element.open = true; });
  const catalog = page.locator('#componentList');
  await catalog.waitFor({state: 'visible'});
  const readinessSelector = activationReady == null
    ? ''
    : `[data-activation-ready="${String(activationReady)}"]`;
  const cards = catalog.locator(`.component-card:not([disabled])${readinessSelector}`);
  const runtimeCards = cards.filter({has: page.locator(`.runtime-chip:text-is("${preferredChip}")`)});
  const preferred = preferredName == null
    ? runtimeCards.first()
    : runtimeCards.filter({has: page.locator(`strong:text-is("${preferredName}")`)});
  await preferred.waitFor({state: 'visible', timeout: timeoutMs});
  if (await preferred.count() !== 1) {
    throw new Error(`No unique enabled ${preferredChip} background${preferredName ? ` named ${preferredName}` : ''} is available`);
  }
  // Chromium can leave Playwright waiting in its scroll-into-view action for
  // this independently scrollable catalog pane. The keyboard journey covers
  // focus and user keyboard selection; here dispatch the component's normal
  // click event directly after the enabled, unique-card assertion.
  await preferred.evaluate((element) => element.click());
  await page.waitForFunction(() => {
    const button = document.querySelector('#runCheckerButton');
    return button instanceof HTMLButtonElement && !button.disabled;
  }, null, {timeout: timeoutMs});
}

async function completeLocalCheck(page) {
  const checkerTab = page.locator('#checkerTab');
  if (await checkerTab.isVisible()) await checkerTab.click();
  else await page.locator('[data-mobile-target="check"]').click();
  const button = page.locator('#runCheckerButton');
  await button.waitFor({state: 'visible'});
  try {
    const checkAvailability = await page.waitForFunction(() => {
      if (!document.querySelector('#runCheckerButton')?.disabled) return 'ready';
      if (document.querySelector('#engineBadge')?.getAttribute('data-state') === 'error') return 'renderer_error';
      return null;
    }, null, {timeout: timeoutMs});
    if (await checkAvailability.jsonValue() !== 'ready') throw new Error('Renderer entered an error state');
  } catch (error) {
    const headline = (await page.locator('#checkHeadline').textContent()) || '';
    const summary = (await page.locator('#checkSummaryCopy').textContent()) || '';
    const badge = (await page.locator('#engineBadge').textContent()) || '';
    const metrics = ((await page.locator('#metricList').textContent()) || '').replace(/\s+/g, ' ').trim();
    const toast = ((await page.locator('#toastRegion').textContent()) || '').replace(/\s+/g, ' ').trim();
    const component = ((await page.locator('#componentDescription').textContent()) || '').replace(/\s+/g, ' ').trim();
    const placeholder = ((await page.locator('#previewPlaceholder').textContent()) || '').replace(/\s+/g, ' ').trim();
    throw new Error(`Local Check never became available: ${headline}; ${summary}; renderer=${badge.trim()}; component=${component || 'none'}; placeholder=${placeholder || 'none'}; notifications=${toast || 'none'}; metrics=${metrics}`, {cause: error});
  }
  await button.click();
  await page.waitForFunction(() => {
    const headline = document.querySelector('#checkHeadline')?.textContent || '';
    return headline !== 'Not checked yet' && headline !== '';
  }, null, {timeout: timeoutMs});
  const grade = await page.locator('#checkSummary').getAttribute('data-grade');
  const headline = (await page.locator('#checkHeadline').textContent()) || '';
  const summary = (await page.locator('#checkSummaryCopy').textContent()) || '';
  const metrics = ((await page.locator('#metricList').textContent()) || '').replace(/\s+/g, ' ').trim();
  if (!['pass', 'warn'].includes(grade || '')) {
    throw new Error(`Local Check did not pass: ${grade || 'unknown'} ${headline}; ${summary}; metrics=${metrics}`);
  }
  return `${grade}: ${headline}; ${summary}; metrics=${metrics}`;
}

async function alterFirstParameter(page) {
  const control = page.locator('#parameterList input[type="range"], #parameterList input[type="number"]').first();
  await control.waitFor({state: 'visible'});
  return control.evaluate((element) => {
    const input = /** @type {HTMLInputElement} */ (element);
    const prior = input.value;
    const minimum = Number(input.min || 0);
    const maximum = Number(input.max || 100);
    const step = Number(input.step || 1);
    const current = Number(input.value);
    const next = current + step <= maximum ? current + step : Math.max(minimum, current - step);
    if (!Number.isFinite(next) || String(next) === prior) throw new Error('No tunable parameter value is available');
    input.value = String(next);
    input.dispatchEvent(new Event('input', {bubbles: true}));
    input.dispatchEvent(new Event('change', {bubbles: true}));
    return {prior, tuned: input.value};
  });
}

async function waitForCacheUpgrade(page, {previous_cache: previousCache, current_cache: currentCache}) {
  const deadline = Date.now() + timeoutMs;
  let names = [];
  while (Date.now() < deadline) {
    names = await page.evaluate(() => caches.keys());
    if (names.includes(currentCache) && !names.includes(previousCache)) return names;
    await page.waitForTimeout(100);
  }
  throw new Error(
    `Service-worker cache upgrade did not replace ${previousCache} with ${currentCache}: ${names.join(', ') || 'no caches'}`,
  );
}

async function cachedPythonRuntimeInventory(page, currentCache) {
  return page.evaluate(async ({shellCacheName}) => {
    const hex = (bytes) => Array.from(new Uint8Array(bytes), (value) => value.toString(16).padStart(2, '0')).join('');
    const runtimeCache = await caches.open(`${shellCacheName}-python-runtime`);
    const shellCache = await caches.open(shellCacheName);
    const metadataResponse = await shellCache.match('/.ledgrid-composer/offline-metadata');
    const metadata = metadataResponse ? await metadataResponse.json() : null;
    const entries = [];
    for (const request of await runtimeCache.keys()) {
      const response = await runtimeCache.match(request);
      if (!response) throw new Error(`Python runtime cache entry disappeared: ${request.url}`);
      const payload = await response.arrayBuffer();
      const recorded = metadata?.pythonAssets?.[request.url] || null;
      const sha256 = hex(await crypto.subtle.digest('SHA-256', payload));
      entries.push({
        url: request.url,
        bytes: payload.byteLength,
        sha256,
        recordedBytes: recorded?.bytes ?? null,
        recordedSha256: recorded?.sha256 ?? null,
        verified: recorded?.bytes === payload.byteLength && recorded?.sha256 === sha256,
      });
    }
    return entries.sort((left, right) => left.url.localeCompare(right.url));
  }, {shellCacheName: currentCache});
}

async function refreshObservedWallState(page) {
  await page.locator('#wallTab').click();
  await page.locator('#refreshWallButton').click();
  await page.waitForFunction(() => {
    const status = document.querySelector('#wallDraftStatus')?.textContent || '';
    return !/has not been read|Reading observed wall state/i.test(status);
  }, null, {timeout: timeoutMs});
}

async function runCore(browser, contract, composerUrl) {
  const context = await browser.newContext({viewport: contract.viewport, acceptDownloads: true, serviceWorkers: 'allow'});
  const page = await context.newPage();
  page.setDefaultTimeout(timeoutMs);
  const assertions = [];
  const consoleErrors = [];
  const forbidden = [];
  page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()); });
  page.on('pageerror', (error) => consoleErrors.push(error.message));
  page.on('request', (request) => { if (mutationRequest(request)) forbidden.push(`${request.method()} ${new URL(request.url()).pathname}`); });
  try {
    const response = await page.goto(composerUrl, {waitUntil: 'domcontentloaded'});
    if (!response?.ok()) throw new Error(`Composer navigation returned ${response?.status() || 'no response'}`);
    await page.locator('#composerWorkspace').waitFor({state: 'visible'});
    assertions.push(observation('composer_loaded', true, `${response.status()} ${composerUrl}`));

    await selectBackground(page, 'Py', {
      activationReady: true,
      preferredName: contract.background_name,
    });
    assertions.push(observation('background_selected', true, (await page.locator('#stageHeading').textContent()) || 'selected'));

    const values = await alterFirstParameter(page);
    assertions.push(observation('parameter_tuned', values.prior !== values.tuned, `${values.prior} -> ${values.tuned}`));
    await page.locator('#undoButton').click();
    await page.waitForFunction((prior) => document.querySelector('#parameterList input[type="range"], #parameterList input[type="number"]')?.value === prior, values.prior);
    assertions.push(observation('undo_restored_prior_value', true, values.prior));
    await page.locator('#redoButton').click();
    await page.waitForFunction((tuned) => document.querySelector('#parameterList input[type="range"], #parameterList input[type="number"]')?.value === tuned, values.tuned);
    assertions.push(observation('redo_restored_tuned_value', true, values.tuned));

    await page.locator('#layersTab').click();
    await page.locator('#clockEnabled').check();
    if (!(await page.locator('#clockEnabled').isChecked())) throw new Error('Clock did not remain enabled');
    assertions.push(observation('clock_enabled', true, 'Clock overlay toggle retained; disabled before the provider-agnostic core Check so composed Clock coverage remains in its dedicated journey'));
    await page.locator('#clockEnabled').uncheck();

    const name = `REL-01 ${args.engine} ${Date.now()}`;
    await page.locator('#presetName').fill(name);
    const checkDetail = await completeLocalCheck(page);
    assertions.push(observation('local_check_completed', true, checkDetail));

    const save = page.locator('#saveLibraryButton');
    try {
      await page.waitForFunction(() => !document.querySelector('#saveLibraryButton')?.disabled, null, {timeout: timeoutMs});
    } catch (error) {
      const serverState = ((await page.locator('#serverState').textContent()) || '').replace(/\s+/g, ' ').trim();
      const actionStatus = ((await page.locator('#serverActionStatus').textContent()) || '').replace(/\s+/g, ' ').trim();
      throw new Error(`Library Save remained unavailable: server=${serverState}; status=${actionStatus}`, {cause: error});
    }
    await save.click();
    await page.waitForFunction(() => (
      /physical wall was not changed/i.test(document.querySelector('#serverActionStatus')?.textContent || '')
      && document.querySelector('#saveLibraryButton')?.getAttribute('data-busy') !== 'true'
    ), null, {timeout: timeoutMs});
    const saveStatus = (await page.locator('#serverActionStatus').textContent()) || '';
    assertions.push(observation('library_save_confirmed_without_wall_change', /saved/i.test(saveStatus), saveStatus));

    await page.waitForTimeout(400);
    await page.reload({waitUntil: 'domcontentloaded'});
    await page.locator('#composerWorkspace').waitFor({state: 'visible'});
    const savedPreset = page.locator('.preset-button').filter({has: page.locator(`strong:text-is("${name}")`)});
    await savedPreset.waitFor({state: 'visible', timeout: timeoutMs});
    await savedPreset.click();
    const restoredName = await page.locator('#presetName').inputValue();
    const restoredSelected = await savedPreset.getAttribute('aria-selected');
    assertions.push(observation(
      'saved_preset_reappears_after_reload',
      restoredName === name && restoredSelected === 'true',
      `${name} -> ${restoredName}; selected=${restoredSelected}`,
    ));
    const presetLabels = await page.locator('.preset-button strong').allTextContents();
    assertions.push(observation(
      'deployment_snapshot_hidden_from_starting_points',
      !presetLabels.some((label) => label.trim().toLowerCase() === 'before-deploy'),
      presetLabels.join(', ') || 'no starting points',
    ));

    const activate = page.locator('#activateButton');
    const reviewReadyAfterSave = !(await activate.isDisabled());
    assertions.push(observation(
      'saved_identity_reviewable_after_save',
      reviewReadyAfterSave,
      reviewReadyAfterSave
        ? 'Saving preserved the exact preset identity; Review will issue the authoritative server Check.'
        : 'Review became unavailable after saving a preset identity.',
    ));
    await refreshObservedWallState(page);
    const savedCheckDetail = await completeLocalCheck(page);
    assertions.push(observation('saved_identity_check_completed', true, savedCheckDetail));
    try {
      await page.waitForFunction(() => !document.querySelector('#activateButton')?.disabled, null, {timeout: timeoutMs});
    } catch (error) {
      const reason = await activate.getAttribute('title');
      throw new Error(`Activation review remained unavailable after the saved-identity Check: ${reason || 'no reason exposed'}`, {cause: error});
    }
    await activate.click();
    await page.locator('#activateDialog[open]').waitFor({state: 'visible', timeout: timeoutMs});
    assertions.push(observation('activation_review_opened', true, 'Guarded review dialog opened after server Check'));
    await page.locator('#activateDialog button[value="cancel"]').click();
    await page.waitForFunction(() => !document.querySelector('#activateDialog')?.hasAttribute('open'));
    assertions.push(observation('activation_review_cancelled', true, 'Review closed without confirmation'));
  } catch (error) {
    assertions.push(observation('journey_error', false, error?.stack || error));
  } finally {
    assertions.push(observation('browser_console_clean', consoleErrors.length === 0, consoleErrors.join('\n') || 'No console errors'));
    assertions.push(observation('forbidden_wall_requests_zero', forbidden.length === 0, forbidden.join(', ') || 'No wall mutation request observed'));
    await context.close();
  }
  return finishJourney('core_no_mutation', contract, assertions);
}

async function runOffline(browser, contract, composerUrl, baseUrl) {
  const offlineStrategy = manifestContract.offline_strategies[args.engine];
  const context = await browser.newContext({viewport: contract.viewport, acceptDownloads: true, serviceWorkers: 'allow'});
  const page = await context.newPage();
  page.setDefaultTimeout(timeoutMs);
  const assertions = [];
  const consoleErrors = [];
  const expectedOfflineConsole = [];
  const failedRequests = [];
  const forbidden = [];
  const wallReads = [];
  let offlineMode = false;
  page.on('console', (message) => {
    if (message.type() !== 'error') return;
    if (offlineMode && (
      /(?:ERR_INTERNET_DISCONNECTED|internet connection appears to be offline)/i.test(message.text())
      || (offlineStrategy === 'fixture_origin_outage' && /server responded with a status of 503 \(SERVICE UNAVAILABLE\)/i.test(message.text()))
    )) {
      expectedOfflineConsole.push(message.text());
    } else {
      consoleErrors.push(message.text());
    }
  });
  page.on('pageerror', (error) => consoleErrors.push(error.message));
  page.on('requestfailed', (request) => failedRequests.push({
    method: request.method(),
    url: request.url(),
    error: request.failure()?.errorText || 'unknown request failure',
  }));
  page.on('request', (request) => { if (mutationRequest(request)) forbidden.push(`${request.method()} ${new URL(request.url()).pathname}`); });
  page.on('request', (request) => {
    const pathname = new URL(request.url()).pathname;
    if (request.method() === 'GET' && (
      pathname === '/api/status'
      || pathname === '/api/painter/masks'
      || /^\/api\/v1\/installation-profiles\/[0-9a-f]{64}\/draft$/.test(pathname)
    )) wallReads.push(pathname);
  });
  try {
    await page.route('**/api/v1/composer/bootstrap?catalog_only=1', (route) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({schema: 'unavailable'}),
    }));
    const markerUrl = new URL('/static/icons/composer.svg', baseUrl).toString();
    const markerResponse = await page.goto(markerUrl, {waitUntil: 'domcontentloaded'});
    if (!markerResponse?.ok()) throw new Error(`Could not establish the fixture origin: ${markerResponse?.status() || 'no response'}`);
    await page.evaluate(async ({previousCache}) => {
      const cache = await caches.open(previousCache);
      await cache.put('/__rel01_previous_generation__', new Response('v16-marker', {
        headers: {'Content-Type': 'text/plain'},
      }));
    }, {previousCache: manifestContract.service_worker_upgrade.previous_cache});

    const response = await page.goto(composerUrl, {waitUntil: 'domcontentloaded'});
    if (!response?.ok()) throw new Error(`Composer navigation returned ${response?.status() || 'no response'}`);
    assertions.push(observation('composer_loaded', true, `${response.status()} ${composerUrl}`));
    await page.waitForFunction(() => document.querySelector('#composerState')?.getAttribute('data-state') === 'ready', null, {timeout: timeoutMs});
    assertions.push(observation(
      'opening_wall_state_reads_zero',
      wallReads.length === 0,
      wallReads.join(', ') || 'Opening Composer read no status, masks, or selected wall profile',
    ));
    const cacheNames = await waitForCacheUpgrade(page, manifestContract.service_worker_upgrade);
    assertions.push(observation(
      'previous_cache_generation_upgraded',
      cacheNames.includes(manifestContract.service_worker_upgrade.current_cache)
        && !cacheNames.includes(manifestContract.service_worker_upgrade.previous_cache),
      cacheNames.join(', '),
    ));

    await page.waitForFunction(() => navigator.serviceWorker?.controller != null, null, {timeout: timeoutMs});
    assertions.push(observation(
      'service_worker_controls_composer',
      true,
      await page.evaluate(() => navigator.serviceWorker.controller?.scriptURL || 'controlled'),
    ));
    const cachedBootstrap = await page.evaluate(async ({currentCache}) => {
      const url = '/static/generated/composer/bootstrap.v1.json';
      const response = await fetch(url, {cache: 'reload'});
      const cached = await (await caches.open(currentCache)).match(url);
      return {status: response.status, cached: Boolean(cached)};
    }, {currentCache: manifestContract.service_worker_upgrade.current_cache});
    assertions.push(observation(
      'renderer_catalog_cached',
      cachedBootstrap.status === 200 && cachedBootstrap.cached,
      JSON.stringify(cachedBootstrap),
    ));

    await selectBackground(page, 'Py', {
      activationReady: true,
      preferredName: contract.background_name,
    });
    assertions.push(observation(
      'mobile_renderer_selection_opens_tune',
      await page.locator('#composerWorkspace').evaluate((element) => element.classList.contains('mobile-dual-pane'))
        && await page.locator('[data-mobile-target="tune"]').getAttribute('aria-current') === 'page',
      'Selecting an animation on a phone opens the paired preview and controls workspace',
    ));
    assertions.push(observation(
      'static_preview_play_enabled',
      !(await page.locator('#playButton').isDisabled()),
      'Play remained local while dynamic server bootstrap was unavailable',
    ));
    await page.locator('[data-mobile-target="layers"]').click();
    await page.locator('#installationAdvanced').evaluate((element) => { element.open = true; });
    await page.locator('#prepareOfflineButton').click();
    try {
      await page.waitForFunction(() => document.querySelector('#offlineReadiness')?.getAttribute('data-state') === 'ready', null, {timeout: timeoutMs});
    } catch (error) {
      const readiness = ((await page.locator('#offlineReadiness').textContent()) || '').replace(/\s+/g, ' ').trim();
      const toast = ((await page.locator('#toastRegion').textContent()) || '').replace(/\s+/g, ' ').trim();
      throw new Error(`Offline preparation did not become ready: ${readiness}; notifications=${toast || 'none'}`, {cause: error});
    }
    assertions.push(observation('offline_assets_prepared', true, (await page.locator('#offlineReadiness').textContent()) || 'ready'));
    assertions.push(observation(
      'offline_strategy_declared',
      ['native_network_offline', 'fixture_origin_outage'].includes(offlineStrategy),
      offlineStrategy,
    ));
    const runtimeInventoryBefore = await cachedPythonRuntimeInventory(
      page,
      manifestContract.service_worker_upgrade.current_cache,
    );
    if (!runtimeInventoryBefore.length || runtimeInventoryBefore.some((item) => !item.verified)) {
      throw new Error(`Prepared Python runtime cache is incomplete or unverified: ${JSON.stringify(runtimeInventoryBefore)}`);
    }

    const retainedName = `REL-01 offline ${args.engine} ${Date.now()}`;
    await page.locator('#presetName').fill(retainedName);
    await page.locator('#presetName').blur();
    await page.waitForTimeout(700);
    const priorDocumentToken = `rel01-${Date.now()}-${Math.random()}`;
    await page.evaluate((token) => { window.__rel01OfflineDocumentToken = token; }, priorDocumentToken);
    offlineMode = true;
    const fixtureOriginOutage = offlineStrategy === 'fixture_origin_outage';
    if (fixtureOriginOutage) {
      await context.addCookies([{
        name: 'ledgrid_rel01_origin_offline', value: '1', url: baseUrl,
      }]);
    } else {
      await context.setOffline(true);
      assertions.push(observation(
        'offline_network_isolation_enforced',
        true,
        'Playwright browser context network disabled',
      ));
    }
    const offlineNavigation = await page.goto(composerUrl, {waitUntil: 'domcontentloaded'});
    await page.locator('#composerWorkspace').waitFor({state: 'visible'});
    const replacedDocument = await page.evaluate(
      (token) => window.__rel01OfflineDocumentToken !== token,
      priorDocumentToken,
    );
    if (!replacedDocument) throw new Error('Offline navigation did not replace the Composer document');
    assertions.push(observation(
      'offline_reload_succeeded',
      true,
      `Composer shell navigated again with network disabled${offlineNavigation ? ` (HTTP ${offlineNavigation.status()})` : ''}`,
    ));
    assertions.push(observation(
      'cached_composer_navigation',
      offlineNavigation?.status() === 200 && replacedDocument,
      `Controlled cached Composer returned ${offlineNavigation?.status() || 'no response'} in a replaced document`,
    ));
    await page.waitForFunction((expected) => document.querySelector('#presetName')?.value === expected, retainedName, {timeout: timeoutMs});
    const offlineName = `${retainedName} edited`;
    await page.locator('#presetName').fill(offlineName);
    await page.locator('#presetName').blur();
    assertions.push(observation('offline_draft_edit_persisted', (await page.locator('#presetName').inputValue()) === offlineName, offlineName));
    const staleBlocked = await page.locator('#activateButton').isDisabled();
    assertions.push(observation('stale_activation_rejected_before_queue', staleBlocked, staleBlocked ? 'Prior Check invalidated; activation review disabled offline' : 'Activation remained available after offline draft change'));

    const checkDetail = await completeLocalCheck(page);
    assertions.push(observation('offline_check_completed', true, checkDetail));
    const runtimeInventoryAfter = await cachedPythonRuntimeInventory(
      page,
      manifestContract.service_worker_upgrade.current_cache,
    );
    assertions.push(observation(
      'python_runtime_cache_stable',
      JSON.stringify(runtimeInventoryAfter) === JSON.stringify(runtimeInventoryBefore),
      JSON.stringify({before: runtimeInventoryBefore, after: runtimeInventoryAfter}),
    ));
    await page.locator('[data-mobile-target="layers"]').click();
    const downloadPromise = page.waitForEvent('download');
    await page.locator('#exportPanelButton').click();
    const download = await downloadPromise;
    assertions.push(observation('offline_export_completed', Boolean(download.suggestedFilename()), download.suggestedFilename()));

    if (fixtureOriginOutage) {
      await context.clearCookies({name: 'ledgrid_rel01_origin_offline'});
      const fixtureStatus = await (await fetch(new URL('/__qualification__/status', baseUrl))).json();
      assertions.push(observation(
        'offline_network_isolation_enforced',
        Number.isSafeInteger(fixtureStatus.network_outage_blocks)
          && fixtureStatus.network_outage_blocks > 0
          && fixtureStatus.network_outage_paths?.includes('/composer'),
        `Fixture rejected ${fixtureStatus.network_outage_blocks} network request(s): ${(fixtureStatus.network_outage_paths || []).join(', ')}`,
      ));
    } else {
      await context.setOffline(false);
    }
    offlineMode = false;
    await page.unroute('**/api/v1/composer/bootstrap?catalog_only=1');
    await page.evaluate(() => window.dispatchEvent(new Event('online')));
    await page.waitForFunction(() => /Wall connected/.test(document.querySelector('#serverState')?.textContent || ''), null, {timeout: timeoutMs});
    const reconnectedName = await page.locator('#presetName').inputValue();
    assertions.push(observation('reconnect_succeeded', true, (await page.locator('#serverState').textContent()) || 'Wall connected'));
    assertions.push(observation(
      'reconnect_preserved_local_draft',
      reconnectedName === offlineName,
      `${offlineName} -> ${reconnectedName}`,
    ));
    const connectedSaveAvailable = !(await page.locator('#saveLibraryButton').isDisabled());
    assertions.push(observation(
      'wall_capabilities_refreshed',
      connectedSaveAvailable,
      connectedSaveAvailable ? 'Digest-compatible server save capability attached' : 'Server save capability did not attach',
    ));
  } catch (error) {
    assertions.push(observation(
      'journey_error',
      false,
      `${error?.stack || error}\nfailedRequests=${JSON.stringify(failedRequests)}`,
    ));
  } finally {
    assertions.push(observation(
      'browser_console_clean',
      consoleErrors.length === 0,
      consoleErrors.join('\n') || (expectedOfflineConsole.length
        ? `No unexpected console errors; ${expectedOfflineConsole.length} expected offline network failure(s) were isolated`
        : 'No console errors'),
    ));
    assertions.push(observation('forbidden_wall_requests_zero', forbidden.length === 0, forbidden.join(', ') || 'No wall mutation request observed'));
    await context.close();
  }
  return finishJourney('offline_reconnect', contract, assertions);
}

async function draftSnapshot(page) {
  return page.evaluate(() => ({
    name: document.querySelector('#presetName')?.value || '',
    clock: {
      enabled: document.querySelector('#clockEnabled')?.checked === true,
      opacity: document.querySelector('#clockOpacity')?.value || '',
    },
    parameters: [...document.querySelectorAll('#parameterList input, #parameterList select')].map((element) => ({
      id: element.id || null,
      name: element.getAttribute('name'),
      type: element.getAttribute('type') || element.tagName.toLowerCase(),
      value: element.value,
      checked: element instanceof HTMLInputElement ? element.checked : null,
    })),
  }));
}

async function runWorkerRecovery(browser, contract, composerUrl) {
  const context = await browser.newContext({viewport: contract.viewport, serviceWorkers: 'allow'});
  await context.addInitScript(() => {
    const NativeWorker = window.Worker;
    const harness = {workers: [], creations: 0, injectedFaults: 0};
    class QualificationWorker extends NativeWorker {
      constructor(...workerArguments) {
        super(...workerArguments);
        harness.workers.push(this);
        harness.creations += 1;
      }
    }
    Object.defineProperty(window, '__rel01WorkerHarness', {value: harness});
    window.Worker = QualificationWorker;
  });
  const page = await context.newPage();
  page.setDefaultTimeout(timeoutMs);
  const assertions = [];
  const consoleErrors = [];
  const forbidden = [];
  page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()); });
  page.on('pageerror', (error) => consoleErrors.push(error.message));
  page.on('request', (request) => { if (mutationRequest(request)) forbidden.push(`${request.method()} ${new URL(request.url()).pathname}`); });
  try {
    const response = await page.goto(composerUrl, {waitUntil: 'domcontentloaded'});
    if (!response?.ok()) throw new Error(`Composer navigation returned ${response?.status() || 'no response'}`);
    await selectBackground(page, 'Py', {
      activationReady: true,
      preferredName: contract.background_name,
    });
    assertions.push(observation('composer_loaded', true, `${response.status()} ${composerUrl}`));

    await alterFirstParameter(page);
    await page.locator('#presetName').fill(`REL-01 recovery ${args.engine} ${Date.now()}`);
    await page.locator('#layersTab').click();
    await page.locator('#clockOpacity').evaluate((element) => {
      element.value = '173';
      element.dispatchEvent(new Event('input', {bubbles: true}));
      element.dispatchEvent(new Event('change', {bubbles: true}));
    });
    await page.locator('#presetName').blur();
    await page.waitForTimeout(700);
    const captured = await draftSnapshot(page);
    const initialWorkers = await page.evaluate(() => window.__rel01WorkerHarness?.creations || 0);
    if (initialWorkers < 1) throw new Error('No browser renderer Worker was observed');
    assertions.push(observation(
      'nested_draft_captured',
      captured.parameters.length > 0 && captured.name.startsWith('REL-01 recovery') && captured.clock.opacity === '173',
      JSON.stringify(captured),
    ));

    const fault = await page.evaluate(() => {
      const harness = window.__rel01WorkerHarness;
      const worker = harness?.workers.at(-1);
      if (!worker) return {injected: false, detail: 'No worker available'};
      harness.injectedFaults += 1;
      const event = new ErrorEvent('error', {message: 'REL-01 synthetic worker fault before termination'});
      worker.dispatchEvent(event);
      worker.terminate();
      return {injected: true, detail: event.message};
    });
    assertions.push(observation('worker_fault_injected', fault.injected, `${fault.detail}; synthetic error dispatched, then Worker.terminate()`));
    await page.waitForFunction((prior) => window.__rel01WorkerHarness?.creations === prior + 1, initialWorkers, {timeout: 30000});
    const restartedWorkers = await page.evaluate(() => window.__rel01WorkerHarness.creations);
    assertions.push(observation('bounded_worker_restart', restartedWorkers === initialWorkers + 1, `${initialWorkers} -> ${restartedWorkers} workers`));

    const restored = await draftSnapshot(page);
    const restoredExactly = JSON.stringify(restored) === JSON.stringify(captured);
    assertions.push(observation('exact_draft_restored', restoredExactly, restoredExactly ? JSON.stringify(restored) : `before=${JSON.stringify(captured)} after=${JSON.stringify(restored)}`));
    const checkDetail = await completeLocalCheck(page);
    assertions.push(observation('recovered_check_completed', true, checkDetail));
    await page.waitForTimeout(1000);
    const settled = await draftSnapshot(page);
    const noStaleOverwrite = JSON.stringify(settled) === JSON.stringify(captured);
    assertions.push(observation('stale_worker_overwrite_absent', noStaleOverwrite, noStaleOverwrite ? 'Draft remained exact after recovered Check settled' : `expected=${JSON.stringify(captured)} settled=${JSON.stringify(settled)}`));
  } catch (error) {
    assertions.push(observation('journey_error', false, error?.stack || error));
  } finally {
    assertions.push(observation('browser_console_clean', consoleErrors.length === 0, consoleErrors.join('\n') || 'No console errors'));
    assertions.push(observation('forbidden_wall_requests_zero', forbidden.length === 0, forbidden.join(', ') || 'No wall mutation request observed'));
    await context.close();
  }
  return finishJourney('worker_recovery', contract, assertions);
}

async function runResponsiveLayouts(browser, contract, composerUrl) {
  const observations = [];
  const consoleErrors = [];
  const forbidden = [];
  for (const viewport of contract.viewports) {
    const context = await browser.newContext({viewport: {width: viewport.width, height: viewport.height}, serviceWorkers: 'block'});
    const page = await context.newPage();
    page.setDefaultTimeout(timeoutMs);
    page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(`${viewport.name}: ${message.text()}`); });
    page.on('pageerror', (error) => consoleErrors.push(`${viewport.name}: ${error.message}`));
    page.on('request', (request) => { if (mutationRequest(request)) forbidden.push(`${viewport.name}: ${request.method()} ${new URL(request.url()).pathname}`); });
    let measurements = null;
    let viewportError = null;
    try {
      const response = await page.goto(composerUrl, {waitUntil: 'domcontentloaded'});
      if (!response?.ok()) throw new Error(`Composer navigation returned ${response?.status() || 'no response'}`);
      await page.locator('#composerWorkspace').waitFor({state: 'visible'});
      if (viewport.width <= 760) await page.locator('[data-mobile-target="layers"]').click();
      else await page.locator('#layersTab').click();
      measurements = await page.evaluate(() => {
        const visible = (element) => {
          if (!(element instanceof HTMLElement)) return false;
          const style = getComputedStyle(element);
          const box = element.getBoundingClientRect();
          return style.display !== 'none' && style.visibility !== 'hidden' && box.width > 0 && box.height > 0;
        };
        const mobile = window.innerWidth <= 760;
        const navigationSelector = mobile
          ? '[data-mobile-target]'
          : '#controlsTab, #layersTab, #wallTab, #checkerTab';
        const actionSelector = '#exportPanelButton, #saveLibraryPanelButton, #activatePanelButton';
        const targetSelector = mobile
          ? '.mobile-tabs button, #activatePanelButton'
          : '#activatePanelButton';
        const navigation = [...document.querySelectorAll(navigationSelector)];
        const actions = [...document.querySelectorAll(actionSelector)];
        const targetBoxes = [...document.querySelectorAll(targetSelector)].filter(visible).map((element) => {
          const box = element.getBoundingClientRect();
          return {id: element.id || element.getAttribute('data-mobile-target'), width: box.width, height: box.height};
        });
        return {
          inner_width: window.innerWidth,
          document_scroll_width: document.documentElement.scrollWidth,
          body_scroll_width: document.body.scrollWidth,
          navigation_count: navigation.length,
          visible_navigation_count: navigation.filter(visible).length,
          action_count: actions.length,
          visible_action_count: actions.filter(visible).length,
          target_boxes: targetBoxes,
        };
      });
    } catch (error) {
      viewportError = error?.message || String(error);
    } finally {
      await context.close();
    }
    const noOverflow = measurements != null
      && measurements.document_scroll_width <= measurements.inner_width + 1
      && measurements.body_scroll_width <= measurements.inner_width + 1;
    const navigationReachable = measurements != null
      && measurements.navigation_count > 0
      && measurements.visible_navigation_count === measurements.navigation_count
      && measurements.action_count > 0
      && measurements.visible_action_count === measurements.action_count;
    const targetSize = measurements != null
      && measurements.target_boxes.length > 0
      && measurements.target_boxes.every((box) => box.width >= 44 && box.height >= 44);
    const viewportAssertions = [
      observation('no_horizontal_overflow', noOverflow, viewportError || JSON.stringify(measurements)),
      observation('navigation_reachable', navigationReachable, viewportError || JSON.stringify(measurements)),
      observation('primary_targets_44px', targetSize, viewportError || JSON.stringify(measurements?.target_boxes || [])),
    ];
    observations.push({
      name: viewport.name,
      width: viewport.width,
      height: viewport.height,
      outcome: viewportAssertions.every((item) => item.passed) ? 'PASS' : 'FAIL',
      assertions: viewportAssertions,
      measurements,
    });
  }
  const assertions = [
    observation('all_viewports_observed', observations.length === contract.viewports.length && observations.every((item) => item.outcome === 'PASS'), `${observations.filter((item) => item.outcome === 'PASS').length}/${contract.viewports.length} passed`),
    observation('browser_console_clean', consoleErrors.length === 0, consoleErrors.join('\n') || 'No console errors'),
    observation('forbidden_wall_requests_zero', forbidden.length === 0, forbidden.join(', ') || 'No wall mutation request observed'),
  ];
  const journey = finishJourney('responsive_layouts', contract, assertions);
  journey.viewports = contract.viewports;
  journey.viewport_observations = observations;
  return journey;
}

async function focusWithKeyboard(page, selector, {reverse = false, maxSteps = 400} = {}) {
  for (let step = 0; step <= maxSteps; step += 1) {
    const focused = await page.evaluate((target) => document.activeElement?.matches(target) === true, selector);
    if (focused) {
      return page.evaluate(() => {
        const element = document.activeElement;
        return {
          id: element?.id || null,
          tag: element?.tagName || null,
          text: element?.textContent?.replace(/\s+/g, ' ').trim().slice(0, 120) || null,
        };
      });
    }
    const keys = [];
    if (args.engine === 'webkit') keys.push('Alt');
    if (reverse) keys.push('Shift');
    keys.push('Tab');
    await page.keyboard.press(keys.join('+'));
  }
  throw new Error(`Keyboard focus did not reach ${selector} within ${maxSteps} Tab steps`);
}

async function focusNamedComponentWithKeyboard(page, name, {maxSteps = 400} = {}) {
  await page.waitForFunction((expectedName) => (
    [...document.querySelectorAll('#componentList .component-card:not([disabled])')]
      .filter((card) => card.querySelector('strong')?.textContent?.trim() === expectedName)
      .length === 1
  ), name, {timeout: timeoutMs});
  for (let step = 0; step <= maxSteps; step += 1) {
    const focused = await page.evaluate((expectedName) => {
      const active = document.activeElement;
      return active instanceof HTMLButtonElement
        && active.matches('#componentList .component-card:not([disabled])')
        && active.querySelector('strong')?.textContent?.trim() === expectedName;
    }, name);
    if (focused) return;
    const keys = [];
    if (args.engine === 'webkit') keys.push('Alt');
    keys.push('Tab');
    await page.keyboard.press(keys.join('+'));
  }
  throw new Error(`Keyboard focus did not reach the ${name} component within ${maxSteps} Tab steps`);
}

async function completeLocalCheckWithKeyboard(page) {
  const button = page.locator('#runCheckerButton');
  await page.waitForFunction(() => {
    const target = document.querySelector('#runCheckerButton');
    const badge = document.querySelector('#engineBadge');
    if (target instanceof HTMLButtonElement && !target.disabled) return 'ready';
    if (badge?.getAttribute('data-state') === 'error') return 'renderer_error';
    return null;
  }, null, {timeout: timeoutMs});
  await focusWithKeyboard(page, '#runCheckerButton');
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => {
    const headline = document.querySelector('#checkHeadline')?.textContent || '';
    return headline !== 'Not checked yet' && headline !== '';
  }, null, {timeout: timeoutMs});
  const grade = await page.locator('#checkSummary').getAttribute('data-grade');
  const headline = (await page.locator('#checkHeadline').textContent()) || '';
  const summary = (await page.locator('#checkSummaryCopy').textContent()) || '';
  if (!['pass', 'warn'].includes(grade || '')) {
    throw new Error(`Keyboard Check did not pass: ${grade || 'unknown'} ${headline}; ${summary}`);
  }
  return `${grade}: ${headline}; ${summary}`;
}

async function runKeyboardOnlyDesktop(browser, contract, composerUrl) {
  const context = await browser.newContext({viewport: contract.viewport, serviceWorkers: 'allow'});
  const page = await context.newPage();
  page.setDefaultTimeout(timeoutMs);
  const assertions = [];
  const consoleErrors = [];
  const forbidden = [];
  attachObservability(page, 'keyboard_only_desktop', consoleErrors, forbidden);
  try {
    const response = await page.goto(composerUrl, {waitUntil: 'domcontentloaded'});
    if (!response?.ok()) throw new Error(`Composer navigation returned ${response?.status() || 'no response'}`);

    await page.keyboard.press(args.engine === 'webkit' ? 'Alt+Tab' : 'Tab');
    const skipFocused = await page.evaluate(() => document.activeElement?.matches('.skip-link') === true);
    await page.keyboard.press('Enter');
    const workspaceFocused = await page.evaluate(() => document.activeElement?.id === 'composerWorkspace');
    assertions.push(observation(
      'skip_link_reached_composer',
      skipFocused && workspaceFocused,
      `skip_focused=${skipFocused} workspace_focused=${workspaceFocused}`,
    ));

    await focusWithKeyboard(page, '#componentSearch');
    await page.keyboard.type(contract.background_name);
    await focusNamedComponentWithKeyboard(page, contract.background_name);
    await page.keyboard.press('Enter');
    await page.waitForFunction((name) => (
      document.querySelector('#stageHeading')?.textContent?.trim() === name
      && document.querySelector('#runCheckerButton') instanceof HTMLButtonElement
      && !document.querySelector('#runCheckerButton').disabled
    ), contract.background_name, {timeout: timeoutMs});
    assertions.push(observation(
      'renderer_selected_by_keyboard',
      true,
      (await page.locator('#stageHeading').textContent()) || contract.background_name,
    ));

    await focusWithKeyboard(page, '#parameterList input[type="range"], #parameterList input[type="number"]');
    const priorParameter = await page.evaluate(() => document.activeElement?.value || null);
    const parameterKey = await page.evaluate(() => {
      const input = /** @type {HTMLInputElement} */ (document.activeElement);
      const current = Number(input.value);
      const maximum = Number(input.max || Number.POSITIVE_INFINITY);
      if (input.type === 'range') return current < maximum ? 'ArrowRight' : 'ArrowLeft';
      return current < maximum ? 'ArrowUp' : 'ArrowDown';
    });
    await page.keyboard.press(parameterKey);
    await page.waitForFunction((prior) => document.activeElement?.value !== prior, priorParameter);
    const tunedParameter = await page.evaluate(() => document.activeElement?.value || null);
    await focusWithKeyboard(page, '#controlsTab', {reverse: true});
    await page.keyboard.press('ControlOrMeta+Z');
    await page.waitForFunction((prior) => document.querySelector('#parameterList input[type="range"], #parameterList input[type="number"]')?.value === prior, priorParameter);
    await page.keyboard.press('ControlOrMeta+Shift+Z');
    await page.waitForFunction((tuned) => document.querySelector('#parameterList input[type="range"], #parameterList input[type="number"]')?.value === tuned, tunedParameter);
    assertions.push(observation(
      'parameter_keyboard_edit_undo_redo',
      priorParameter !== tunedParameter,
      `${priorParameter} -> ${tunedParameter} -> ${priorParameter} -> ${tunedParameter}`,
    ));

    await page.keyboard.press('End');
    const endState = await page.evaluate(() => ({
      active: document.activeElement?.id,
      selected: document.querySelector('#checkerTab')?.getAttribute('aria-selected'),
      panelHidden: document.querySelector('#checkerPanel')?.hidden,
    }));
    await page.keyboard.press('Home');
    const homeState = await page.evaluate(() => ({
      active: document.activeElement?.id,
      selected: document.querySelector('#controlsTab')?.getAttribute('aria-selected'),
      panelHidden: document.querySelector('#controlsPanel')?.hidden,
    }));
    await page.keyboard.press('ArrowRight');
    const arrowState = await page.evaluate(() => ({
      active: document.activeElement?.id,
      selected: document.querySelector('#layersTab')?.getAttribute('aria-selected'),
      panelHidden: document.querySelector('#layersPanel')?.hidden,
    }));
    const tablistOperable = endState.active === 'checkerTab'
      && endState.selected === 'true'
      && endState.panelHidden === false
      && homeState.active === 'controlsTab'
      && homeState.selected === 'true'
      && homeState.panelHidden === false
      && arrowState.active === 'layersTab'
      && arrowState.selected === 'true'
      && arrowState.panelHidden === false;
    assertions.push(observation(
      'tablist_arrow_home_end_navigation',
      tablistOperable,
      JSON.stringify({endState, homeState, arrowState}),
    ));

    await focusWithKeyboard(page, '#clockEnabled');
    await page.keyboard.press('Space');
    const clockEnabled = await page.locator('#clockEnabled').isChecked();
    assertions.push(observation(
      'clock_toggle_keyboard_operable',
      clockEnabled,
      `clock_enabled=${clockEnabled}`,
    ));

    const name = `REL-01 keyboard ${args.engine} ${Date.now()}`;
    await focusWithKeyboard(page, '#presetName', {reverse: true});
    await page.keyboard.press('ControlOrMeta+A');
    await page.keyboard.type(name);
    await focusWithKeyboard(page, '#importButton');
    await page.keyboard.press('c');
    const firstCheck = await completeLocalCheckWithKeyboard(page);
    assertions.push(observation('local_check_keyboard_completed', true, firstCheck));

    await page.waitForFunction(() => (
      document.querySelector('#networkStatus')?.getAttribute('data-state') === 'online'
      && !document.querySelector('#saveLibraryButton')?.disabled
    ), null, {timeout: timeoutMs});
    await page.keyboard.press('ControlOrMeta+S');
    await page.waitForFunction(() => (
      /physical wall was not changed/i.test(document.querySelector('#serverActionStatus')?.textContent || '')
      && document.querySelector('#saveLibraryButton')?.getAttribute('data-busy') !== 'true'
    ), null, {timeout: timeoutMs});
    const saveStatus = (await page.locator('#serverActionStatus').textContent()) || '';
    assertions.push(observation(
      'library_save_keyboard_completed_without_wall_change',
      /saved/i.test(saveStatus) && await page.locator('#activateButton').isDisabled(),
      saveStatus,
    ));

    await page.keyboard.press('w');
    await page.waitForFunction(() => document.querySelector('#wallTab')?.getAttribute('aria-selected') === 'true');
    await focusWithKeyboard(page, '#refreshWallButton');
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => {
      const status = document.querySelector('#wallDraftStatus')?.textContent || '';
      return !/has not been read|Reading observed wall state/i.test(status);
    }, null, {timeout: timeoutMs});
    await focusWithKeyboard(page, '#importButton', {reverse: true});
    await page.keyboard.press('c');
    await completeLocalCheckWithKeyboard(page);
    await page.waitForFunction(() => !document.querySelector('#activateButton')?.disabled, null, {timeout: timeoutMs});
    await focusWithKeyboard(page, '#activateButton', {reverse: true});
    await page.keyboard.press('Enter');
    const activationReviewOutcome = await page.waitForFunction(() => {
      if (document.querySelector('#activateDialog')?.hasAttribute('open')) return 'dialog_open';
      const status = document.querySelector('#serverActionStatus')?.textContent || '';
      if (/Server Check failed:/i.test(status)) return status;
      return null;
    }, null, {timeout: timeoutMs});
    const activationReviewStatus = await activationReviewOutcome.jsonValue();
    if (activationReviewStatus !== 'dialog_open') {
      throw new Error(`Activation review did not open: ${activationReviewStatus}`);
    }
    const dialogFocus = [];
    const forwardTab = args.engine === 'webkit' ? 'Alt+Tab' : 'Tab';
    const backwardTab = args.engine === 'webkit' ? 'Alt+Shift+Tab' : 'Shift+Tab';
    for (const key of [backwardTab, forwardTab, forwardTab]) {
      dialogFocus.push(await page.evaluate(() => ({
        active: document.activeElement?.id || document.activeElement?.textContent?.trim() || null,
        dialog: document.activeElement?.closest('dialog')?.id || null,
      })));
      await page.keyboard.press(key);
    }
    dialogFocus.push(await page.evaluate(() => ({
      active: document.activeElement?.id || document.activeElement?.textContent?.trim() || null,
      dialog: document.activeElement?.closest('dialog')?.id || null,
    })));
    const focusContained = dialogFocus.every((item) => item.dialog === 'activateDialog');
    assertions.push(observation(
      'activation_dialog_keyboard_focus_contained',
      focusContained,
      JSON.stringify(dialogFocus),
    ));
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('#activateDialog')?.hasAttribute('open'));
    await page.waitForFunction(() => document.activeElement?.id === 'activateButton');
    const returnFocus = await page.evaluate(() => document.activeElement?.id || null);
    assertions.push(observation(
      'activation_review_cancelled_by_keyboard',
      returnFocus === 'activateButton',
      `dialog_closed=true return_focus=${returnFocus || 'none'}`,
    ));
  } catch (error) {
    assertions.push(observation('journey_error', false, error?.stack || error));
  } finally {
    assertions.push(observation('browser_console_clean', consoleErrors.length === 0, consoleErrors.join('\n') || 'No console errors'));
    assertions.push(observation('forbidden_wall_requests_zero', forbidden.length === 0, forbidden.join(', ') || 'No wall mutation request observed'));
    await context.close();
  }
  return finishJourney('keyboard_only_desktop', contract, assertions);
}

function attachObservability(page, label, consoleErrors, forbidden) {
  page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(`${label}: ${message.text()}`); });
  page.on('pageerror', (error) => consoleErrors.push(`${label}: ${error.message}`));
  page.on('request', (request) => { if (mutationRequest(request)) forbidden.push(`${label}: ${request.method()} ${new URL(request.url()).pathname}`); });
}

async function runGlobalControls(browser, contract, composerUrl) {
  const context = await browser.newContext({viewport: contract.viewport, serviceWorkers: 'allow'});
  const page = await context.newPage();
  page.setDefaultTimeout(timeoutMs);
  const assertions = [];
  const consoleErrors = [];
  const forbidden = [];
  attachObservability(page, 'global_controls', consoleErrors, forbidden);
  try {
    const response = await page.goto(composerUrl, {waitUntil: 'domcontentloaded'});
    if (!response?.ok()) throw new Error(`Composer navigation returned ${response?.status() || 'no response'}`);
    await refreshObservedWallState(page);
    await page.locator('#vibeOptions button').first().waitFor({state: 'visible'});
    assertions.push(observation('composer_loaded', true, `${response.status()} ${composerUrl}`));

    const vibeButtons = page.locator('#vibeOptions button');
    const vibeCount = await vibeButtons.count();
    const vibes = [];
    for (let index = 0; index < vibeCount; index += 1) {
      const button = vibeButtons.nth(index);
      vibes.push((await button.textContent())?.trim() || `vibe-${index}`);
      await button.click();
    }
    assertions.push(observation('all_vibes_exercised', vibeCount === 5, vibes.join(', ')));

    for (const [selector, value, assertionId] of [
      ['#globalBrightness', '137', 'brightness_tuned'],
      ['#globalSpeed', '1.35', 'speed_tuned'],
      ['#globalTargetFps', '47', 'target_fps_tuned'],
    ]) {
      await page.locator(selector).evaluate((element, next) => {
        element.value = next;
        element.dispatchEvent(new Event('input', {bubbles: true}));
        element.dispatchEvent(new Event('change', {bubbles: true}));
      }, value);
      assertions.push(observation(assertionId, (await page.locator(selector).inputValue()) === value, `${selector}=${await page.locator(selector).inputValue()}`));
    }

    const modifierNames = [];
    const modifierCount = await page.locator('#plantModifierGroups .modifier-toggle').count();
    for (let index = 0; index < modifierCount; index += 1) {
      const toggle = page.locator('#plantModifierGroups .modifier-toggle').nth(index);
      const name = (await toggle.textContent())?.trim() || `modifier-${index}`;
      modifierNames.push(name);
      await toggle.click();
      const currentToggle = page.locator('#plantModifierGroups .modifier-toggle').filter({hasText: name}).first();
      const row = currentToggle.locator('xpath=..');
      const strength = row.locator('input[type="range"]');
      if (await strength.isEnabled()) {
        await strength.evaluate((element) => {
          element.value = element.value === '0.65' ? '0.7' : '0.65';
          element.dispatchEvent(new Event('input', {bubbles: true}));
          element.dispatchEvent(new Event('change', {bubbles: true}));
        });
      }
    }
    assertions.push(observation('all_plant_modifier_classes_exercised', modifierCount === 14, modifierNames.join(', ')));
    const review = page.locator('#reviewWallButton');
    await page.waitForFunction(() => !document.querySelector('#reviewWallButton')?.disabled, null, {timeout: timeoutMs});
    await review.click();
    await page.locator('#wallReviewDialog[open]').waitFor({state: 'visible'});
    await page.locator('#wallReviewDialog button[value="cancel"]').click();
    await page.waitForFunction(() => !document.querySelector('#wallReviewDialog')?.hasAttribute('open'));
    assertions.push(observation('wall_review_cancelled', true, 'All draft changes reviewed, then cancelled before apply'));
  } catch (error) {
    assertions.push(observation('journey_error', false, error?.stack || error));
  } finally {
    assertions.push(observation('browser_console_clean', consoleErrors.length === 0, consoleErrors.join('\n') || 'No console errors'));
    assertions.push(observation('forbidden_wall_requests_zero', forbidden.length === 0, forbidden.join(', ') || 'No wall mutation request observed'));
    await context.close();
  }
  return finishJourney('global_controls', contract, assertions);
}

async function runProfileMasks(browser, contract, composerUrl) {
  const context = await browser.newContext({viewport: contract.viewport, serviceWorkers: 'allow'});
  const page = await context.newPage();
  page.setDefaultTimeout(timeoutMs);
  const assertions = [];
  const consoleErrors = [];
  const forbidden = [];
  attachObservability(page, 'profile_masks', consoleErrors, forbidden);
  const layers = ['foliage', 'top_left', 'top_right', 'upper_middle', 'middle_left', 'middle_right', 'lower_left', 'lower_right'];
  const ledOffset = contract.engine_led_offsets?.[args.engine];
  if (!Number.isInteger(ledOffset) || ledOffset < 0 || ledOffset >= 138) {
    throw new Error(`No deterministic mask-coordinate offset is declared for ${args.engine}`);
  }
  const expectedCells = Object.fromEntries(layers.map((layer, index) => [layer, index * 138 + ledOffset]));
  let savedCells = null;
  try {
    const response = await page.goto(composerUrl, {waitUntil: 'domcontentloaded'});
    if (!response?.ok()) throw new Error(`Composer navigation returned ${response?.status() || 'no response'}`);
    assertions.push(observation('composer_loaded', true, `${response.status()} ${composerUrl}`));
    const bootstrapPreflight = await page.evaluate(async () => {
      const response = await fetch('/api/v1/composer/bootstrap', {cache: 'no-store'});
      const payload = await response.json();
      return {
        status: response.status,
        digest: payload.installation_profile?.digest || null,
        draft_url: payload.capabilities?.server_actions?.installation_profile_draft_url || null,
        publish_url: payload.capabilities?.server_actions?.installation_profile_publish_url || null,
      };
    });
    if (bootstrapPreflight.status !== 200 || !bootstrapPreflight.draft_url || !bootstrapPreflight.publish_url) {
      throw new Error(`Fixture bootstrap has no managed profile authoring contract: ${JSON.stringify(bootstrapPreflight)}`);
    }
    await page.waitForFunction(() => (
      document.querySelector('#componentList')?.getAttribute('aria-busy') === 'false'
      && document.querySelector('#networkStatus')?.getAttribute('data-state') === 'online'
    ), null, {timeout: timeoutMs});
    await refreshObservedWallState(page);
    const draftPreflight = await page.evaluate(async (url) => {
      const response = await fetch(url, {cache: 'no-store'});
      const payload = await response.json();
      return {status: response.status, schema: payload.schema, revision: payload.revision || null};
    }, bootstrapPreflight.draft_url);
    if (draftPreflight.status !== 200 || draftPreflight.schema !== 'ledgrid.installation-profile-draft') {
      throw new Error(`Managed profile draft preflight failed: ${JSON.stringify(draftPreflight)}`);
    }
    await page.locator('#wallTab').click();
    await page.locator('#editMasksButton').click();
    await page.locator('#maskEditorDialog[open]').waitFor({state: 'visible'});
    await page.waitForFunction(() => !/loading/i.test(document.querySelector('#maskEditorStatus')?.textContent || ''), null, {timeout: timeoutMs});
    await page.locator('#maskCanvas').waitFor({state: 'visible'});
    for (let offset = 0; offset < ledOffset; offset += 1) {
      await page.locator('#maskCanvas').dispatchEvent('keydown', {key: 'ArrowUp', code: 'ArrowUp'});
    }
    for (const layer of layers) {
      await page.locator(`[data-mask-tool="${layer}"]`).click();
      await page.locator('#maskCanvas').dispatchEvent('keydown', {key: ' ', code: 'Space'});
      await page.locator('#maskCanvas').dispatchEvent('keydown', {key: 'ArrowRight', code: 'ArrowRight'});
    }
    const dirty = !(await page.locator('#saveMasksButton').isDisabled());
    if (!dirty) {
      throw new Error(
        `Eight semantic paint operations did not dirty the managed draft: ${(await page.locator('#maskEditorStatus').textContent()) || 'no status'}; bootstrap=${JSON.stringify(bootstrapPreflight)}; draft=${JSON.stringify(draftPreflight)}`,
      );
    }
    await page.locator('#saveMasksButton').click();
    await page.waitForFunction(() => /managed profile draft saved/i.test(document.querySelector('#maskEditorStatus')?.textContent || ''), null, {timeout: timeoutMs});
    savedCells = await page.evaluate(async () => {
      const bootstrap = await (await fetch('/api/v1/composer/bootstrap')).json();
      const draft = await (await fetch(bootstrap.installation_profile.draft_url)).json();
      const result = {foliage: draft.masks.foliage};
      for (const [name, indices] of Object.entries(draft.masks.globes)) result[name] = indices;
      return result;
    });
    const exactPaintSaved = layers.every((layer) => savedCells[layer]?.includes(expectedCells[layer]));
    assertions.push(observation('foliage_and_seven_globes_edited', exactPaintSaved, `expected=${JSON.stringify(expectedCells)} saved=${JSON.stringify(savedCells)}`));
    assertions.push(observation('profile_draft_saved', true, (await page.locator('#maskEditorStatus').textContent()) || 'saved'));
    await page.locator('#publishProfileButton').click();
    await page.waitForFunction(() => /published .* as a candidate/i.test(document.querySelector('#maskEditorStatus')?.textContent || ''), null, {timeout: timeoutMs});
    assertions.push(observation('immutable_candidate_published', true, (await page.locator('#maskEditorStatus').textContent()) || 'published'));
    await page.locator('#reviewProfileCandidateButton').click();
    await page.locator('#profileCandidateDialog[open]').waitFor({state: 'visible'});
    await page.locator('#profileCandidateDialog button[value="cancel"]').click();
    await page.waitForFunction(() => !document.querySelector('#profileCandidateDialog')?.hasAttribute('open'));
    assertions.push(observation('profile_selection_review_cancelled', true, 'Candidate review closed without staging or wall selection'));

    await page.reload({waitUntil: 'domcontentloaded'});
    await page.locator('#wallTab').click();
    await page.locator('#editMasksButton').click();
    await page.locator('#maskEditorDialog[open]').waitFor({state: 'visible'});
    await page.waitForFunction(() => !/loading/i.test(document.querySelector('#maskEditorStatus')?.textContent || ''), null, {timeout: timeoutMs});
    const reloadedCells = await page.evaluate(async () => {
      const bootstrap = await (await fetch('/api/v1/composer/bootstrap')).json();
      const draft = await (await fetch(bootstrap.installation_profile.draft_url)).json();
      const result = {foliage: draft.masks.foliage};
      for (const [name, indices] of Object.entries(draft.masks.globes)) result[name] = indices;
      return result;
    });
    const exactReload = JSON.stringify(reloadedCells) === JSON.stringify(savedCells)
      && layers.every((layer) => reloadedCells[layer]?.includes(expectedCells[layer]));
    assertions.push(observation('semantic_regions_survived_reload', exactReload, `expected_cells=${JSON.stringify(expectedCells)} saved=${JSON.stringify(savedCells)} reloaded=${JSON.stringify(reloadedCells)}`));
  } catch (error) {
    assertions.push(observation('journey_error', false, error?.stack || error));
  } finally {
    assertions.push(observation('browser_console_clean', consoleErrors.length === 0, consoleErrors.join('\n') || 'No console errors'));
    assertions.push(observation('forbidden_wall_requests_zero', forbidden.length === 0, forbidden.join(', ') || 'No wall mutation request observed'));
    await context.close();
  }
  return finishJourney('profile_masks', contract, assertions);
}

async function runPythonNativeClock(browser, contract, composerUrl) {
  const context = await browser.newContext({viewport: contract.viewport, serviceWorkers: 'allow'});
  const page = await context.newPage();
  page.setDefaultTimeout(timeoutMs);
  const assertions = [];
  const consoleErrors = [];
  const forbidden = [];
  attachObservability(page, 'python_native_clock', consoleErrors, forbidden);
  try {
    const response = await page.goto(composerUrl, {waitUntil: 'domcontentloaded'});
    if (!response?.ok()) throw new Error(`Composer navigation returned ${response?.status() || 'no response'}`);
    await page.waitForFunction(() => document.querySelector('#componentList')?.getAttribute('aria-busy') === 'false');
    assertions.push(observation('composer_loaded', true, `${response.status()} ${composerUrl}`));
    for (const [chip, assertionId] of [
      ['Py', 'python_background_with_clock_checked'],
      ['Wasm', 'managed_native_background_with_clock_checked'],
    ]) {
      await selectBackground(page, chip, {
        activationReady: chip === 'Py',
        preferredName: chip === 'Py'
          ? contract.python_background_name
          : contract.managed_native_background_name,
      });
      const name = (await page.locator('#stageHeading').textContent()) || chip;
      await page.locator('#layersTab').click();
      if (!(await page.locator('#clockEnabled').isChecked())) await page.locator('#clockEnabled').check();
      const detail = await completeLocalCheck(page);
      assertions.push(observation(assertionId, true, `${name}: ${detail}`));
      if (chip === 'Wasm') {
        const reason = await page.locator('#activateButton').getAttribute('title');
        assertions.push(observation(
          'managed_native_host_ineligibility_declared',
          reason === contract.managed_native_ineligibility_reason
            || reason === contract.managed_native_digest_mismatch_reason,
          `declared=${reason || 'none'} expected=${contract.managed_native_ineligibility_reason} or ${contract.managed_native_digest_mismatch_reason}`,
        ));
      }
    }
  } catch (error) {
    assertions.push(observation('journey_error', false, error?.stack || error));
  } finally {
    assertions.push(observation('browser_console_clean', consoleErrors.length === 0, consoleErrors.join('\n') || 'No console errors'));
    assertions.push(observation('forbidden_wall_requests_zero', forbidden.length === 0, forbidden.join(', ') || 'No wall mutation request observed'));
    await context.close();
  }
  return finishJourney('python_native_clock', contract, assertions);
}

function finishJourney(journeyId, contract, assertions) {
  const byId = new Map(assertions.map((item) => [item.assertion_id, item]));
  for (const assertionId of contract.required_assertions) {
    if (!byId.has(assertionId)) {
      const item = observation(assertionId, false, 'Not executed after an earlier failure');
      assertions.push(item);
      byId.set(assertionId, item);
    }
  }
  const outcome = contract.required_assertions.every((assertionId) => byId.get(assertionId)?.passed === true) ? 'PASS' : 'FAIL';
  return {journey_id: journeyId, viewport: contract.viewport, outcome, assertions};
}

let browser;
let manifestContract;
try {
  const manifest = JSON.parse(await fs.readFile(args.manifest, 'utf8'));
  manifestContract = manifest;
  if (!manifest.required_engines?.includes(args.engine)) throw new Error(`Engine ${args.engine} is not required by the manifest`);
  const moduleEntry = path.join(path.resolve(args['playwright-module']), 'index.js');
  const packageMetadata = JSON.parse(await fs.readFile(path.join(path.resolve(args['playwright-module']), 'package.json'), 'utf8'));
  result.playwright_version = packageMetadata.version || null;
  const imported = await import(pathToFileURL(moduleEntry).href);
  const playwright = imported.default || imported;
  const browserType = playwright[args.engine];
  if (!browserType) throw new Error(`Playwright module does not expose ${args.engine}`);
  browser = await browserType.launch({headless: true});
  if (args['artifacts-dir']) {
    const artifactsDir = path.resolve(args['artifacts-dir']);
    await fs.mkdir(artifactsDir, {recursive: true});
    const newContext = browser.newContext.bind(browser);
    let traceNumber = 0;
    browser.newContext = async (options = {}) => {
      const context = await newContext({...options, recordVideo: {dir: path.join(artifactsDir, 'videos')}});
      const tracePath = path.join(artifactsDir, `trace-${traceNumber++}.zip`);
      await context.tracing.start({screenshots: true, snapshots: true, sources: true});
      const close = context.close.bind(context);
      context.close = async (...closeArgs) => {
        try {
          await context.tracing.stop({path: tracePath});
        } finally {
          return close(...closeArgs);
        }
      };
      return context;
    };
  }
  result.executed = true;
  result.reported_engine = browser.browserType().name();
  result.browser_version = browser.version();
  result.offline_strategy = manifest.offline_strategies?.[args.engine] || null;
  const baseUrl = new URL(args['base-url']).toString();
  const composerUrl = new URL('/composer', baseUrl).toString();
  const journeyPlan = [
    ['core_no_mutation', () => runCore(browser, manifest.journeys.core_no_mutation, composerUrl)],
    ['offline_reconnect', () => runOffline(browser, manifest.journeys.offline_reconnect, composerUrl, baseUrl)],
    ['worker_recovery', () => runWorkerRecovery(browser, manifest.journeys.worker_recovery, composerUrl)],
    ['responsive_layouts', () => runResponsiveLayouts(browser, manifest.journeys.responsive_layouts, composerUrl)],
    ['keyboard_only_desktop', () => runKeyboardOnlyDesktop(browser, manifest.journeys.keyboard_only_desktop, composerUrl)],
    ['global_controls', () => runGlobalControls(browser, manifest.journeys.global_controls, composerUrl)],
    ['profile_masks', () => runProfileMasks(browser, manifest.journeys.profile_masks, composerUrl)],
    ['python_native_clock', () => runPythonNativeClock(browser, manifest.journeys.python_native_clock, composerUrl)],
  ];
  const selectedJourney = args.journey || null;
  if (selectedJourney && !journeyPlan.some(([journeyId]) => journeyId === selectedJourney)) {
    throw new Error(`Unknown focused journey ${selectedJourney}`);
  }
  for (const [journeyId, run] of journeyPlan) {
    if (!selectedJourney || selectedJourney === journeyId) result.journeys.push(await run());
  }
  const fixtureResponse = await fetch(new URL('/__qualification__/status', baseUrl));
  result.fixture_status = await fixtureResponse.json();
  const fixtureSafe = fixtureResponse.ok
    && result.fixture_status?.schema === 'ledgrid.browser-qualification-fixture-status'
    && result.fixture_status?.wall_consumer_attached === false
    && result.fixture_status?.wall_mutation_attempts === 0;
  result.outcome = result.journeys.every((journey) => journey.outcome === 'PASS') && fixtureSafe ? 'PASS' : 'FAIL';
} catch (error) {
  result.error = {name: error?.name || 'Error', message: error?.message || String(error), stack: error?.stack || null};
} finally {
  if (browser) await browser.close();
  result.completed_at = new Date().toISOString();
  await fs.mkdir(path.dirname(path.resolve(args.output)), {recursive: true});
  await fs.writeFile(args.output, `${JSON.stringify(result, null, 2)}\n`, 'utf8');
}
process.exitCode = result.outcome === 'PASS' ? 0 : 1;
