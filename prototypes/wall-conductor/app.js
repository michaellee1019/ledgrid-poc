(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const escapeHTML = (value) => String(value).replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
  })[char]);

  const baseComponents = [
    ["nocturne-ferns", "Nocturne Ferns", "Ambient art", "Slow currents of colored light gather around calibrated foliage and pass through the seven glass globes."],
    ["tidepool-orbits", "Tidepool Orbits", "Ambient art", "Small luminous bodies find currents, form temporary schools, and scatter around semantic plant regions."],
    ["after-rain-clock", "After-Rain Clock", "Clocks & information", "A restrained typographic clock suspended in the clear space between leaf silhouettes."],
    ["glasshouse-weather", "Glasshouse Weather: Long-Form Forecast Ribbon", "Clocks & information", "A patient vertical weather narrative with current conditions and a slow forecast procession."],
    ["moth-signal", "Moth Signal", "Ambient art", "Warm agents drift toward globe emitters and avoid dense foliage obstacles."],
    ["seedfall", "Seedfall Through Seven Refractive Globes", "Ambient art", "Seeds descend, refract through calibrated vessels, and settle into quiet eddies."],
    ["column-tetris", "Column Tetris", "Games", "An autonomous or operator-controlled falling-block game composed for the unusually tall wall."],
    ["serpent-garden", "Serpent Garden", "Games", "A responsive growing line navigates foliage, portals, and edible emitters."],
    ["prism-rain", "Receiver Prism Rain", "Ambient art", "Receiver-native color rain with a host-built preview simulation and hardware-timed motion."],
    ["orbital-emoji", "Orbital Emoji Arrangement", "Pixel art & GIFs", "Place expressive glyphs along a tall orbit without losing their readable silhouette."],
    ["plant-calibration-sweep", "Plant Calibration Sweep — Foliage and Globe Registration", "Diagnostics", "A deliberate scan for checking geometric alignment against the physical plant wall."],
    ["receiver-lane-map", "Receiver Lane and SPI Device Map", "Diagnostics", "Identifies receiver boundaries, buses, and logical-to-physical mapping."],
    ["aurora-ledger", "Aurora Ledger", "Ambient art", "Layered ribbons preserve a calm center while registering around plant material."],
    ["firefly-habitat", "Firefly Habitat", "Ambient art", "Autonomous fireflies inhabit selected globes and avoid foliage at variable strength."],
    ["long-now", "The Long Now — Date, Time, Moon, and Quiet Status", "Clocks & information", "A vertically paced information composition designed to be understood from across a room."],
    ["rivulet", "Rivulet", "Ambient art", "A narrow stream wanders down the installation and splits around surface modifiers."],
    ["pixel-postcard", "Pixel Postcard Theatre", "Pixel art & GIFs", "Small authored scenes stage themselves in the center of the towering canvas."],
    ["portal-pong", "Seven-Portal Pong", "Games", "A playful autonomous rally where globe regions can become portals or bumpers."],
    ["chromatic-lattice", "Chromatic Lattice", "Ambient art", "A structured field of color relaxes and tightens with the selected global vibe."],
    ["thermal-proof", "Thermal and Throughput Proof Pattern", "Diagnostics", "A high-load test surface for installation commissioning and developer inspection."]
  ];

  const variantNames = [
    "Cozy Canopy",
    "Deep Canopy Drift Through Seven Glass Globes",
    "Quiet Window Before Dawn",
    "Long Evening With Soft Foliage Illumination",
    "Vivid Room-Scale Study",
    "Celebration With Restrained Highlights"
  ];

  const componentCount = 52;
  const components = Array.from({ length: componentCount }, (_, index) => {
    const source = baseComponents[index % baseComponents.length];
    const cycle = Math.floor(index / baseComponents.length);
    const id = cycle ? `${source[0]}-${cycle + 1}` : source[0];
    const name = cycle ? `${source[1]} · Study ${cycle + 1}` : source[1];
    const provider = index % 7 === 0 || index % 9 === 0 ? "receiver_native" : "host_python";
    const role = index % 13 === 2 ? "overlay" : index % 17 === 0 ? "compatibility_full_scene" : "background";
    const status = index % 19 === 10 ? "build_only" : index % 23 === 11 ? "quarantined" : index % 29 === 12 ? "unsupported" : "ready";
    return {
      id,
      key: `${provider}:${id}`,
      name,
      category: source[2],
      description: source[3],
      provider,
      role,
      status,
      index,
      capabilities: ["parameters", ...(index % 3 === 0 ? ["primary interaction"] : []), ...(index % 4 !== 1 ? ["plant material"] : []), ...(index % 5 === 0 ? ["vibe"] : [])]
    };
  });

  const presets = components.flatMap((component, componentIndex) => {
    const count = componentIndex < 32 ? 6 : 5; // 32×6 + 20×5 = 292
    return Array.from({ length: count }, (_, variantIndex) => ({
      id: `${component.key}/${variantIndex + 1}`,
      presetId: `${component.id}-${variantIndex + 1}`,
      componentKey: component.key,
      componentId: component.id,
      name: variantNames[variantIndex],
      fullName: `${component.name} — ${variantNames[variantIndex]}`,
      description: `${component.description} This authored variation favors ${["balanced motion and warm shadow", "legible depth around every globe region", "low luminance and unhurried transitions", "extended room-scale viewing", "strong chromatic separation", "festive motion without visual noise"][variantIndex]}.`,
      provider: component.provider,
      role: component.role,
      category: component.category,
      status: component.status,
      component,
      variantIndex,
      seed: componentIndex * 7 + variantIndex * 13 + 4,
      modified: componentIndex === 3 && variantIndex === 1,
      stale: componentIndex === 5 && variantIndex === 2
    }));
  });

  const initialDraft = presets.find((item) => item.componentId === "seedfall" && item.variantIndex === 1) || presets[0];
  const state = {
    view: "now",
    power: true,
    brightness: 176,
    vibe: "Cozy",
    live: presets[0],
    liveDirty: false,
    liveStopped: false,
    draft: presets[1],
    query: "",
    filter: "show",
    indexMode: "presets",
    compare: [],
    compareCollapsed: true,
    favoriteKeys: new Set(),
    params: { drift: 0.42, density: 64, palette: "Moss and rose", plant: true },
    scene: {
      background: initialDraft,
      overlay: components.find((item) => item.id === "after-rain-clock"),
      overlayEnabled: true,
      opacity: 82,
      translation: 8,
      clip: "Clip to wall",
      drift: true,
      saved: false
    },
    roomDirty: false,
    activeTool: null,
    pointer: { x: 16, y: 72 }
  };

  const workspace = $("#workspace");
  const dialog = $("#takeLiveDialog");
  const comparisonDialog = $("#comparisonDialog");
  let pendingLive = null;
  let toastTimer = 0;

  function componentLabel(item) {
    return item.provider === "receiver_native" ? "Receiver native" : "Host Python";
  }

  function statusLabel(item) {
    return ({ ready: "Show-ready", build_only: "Catalog / build only", quarantined: "Quarantined", unsupported: "Unsupported here" })[item.status];
  }

  function canvasMarkup(kind, seed, label, extra = "") {
    return `<div class="wall-frame ${kind === "live" ? "live-frame" : "preview-frame"} ${extra}">
      <span class="frame-tag ${kind === "live" ? "live" : ""}">${kind === "live" ? "Physical live" : "Isolated preview"}</span>
      <canvas class="wall-canvas" width="64" height="276" data-seed="${seed}" data-kind="${kind}" aria-label="${escapeHTML(label)}"></canvas>
      <span class="wall-grid-overlay" aria-hidden="true"></span>
    </div>`;
  }

  function showToast(message) {
    const toast = $("#toast");
    toast.textContent = message;
    toast.classList.add("is-visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove("is-visible"), 2600);
  }

  function syncLiveStrip() {
    $("#liveTitle").textContent = state.liveStopped ? "Wall stopped" : state.live.component.name;
    $("#liveSubtitle").textContent = state.liveStopped ? "Output is black · ready for a new start" : `${state.live.name}${state.liveDirty ? " · modified live" : " · saved preset"}`;
    $("#livePower").textContent = state.power ? "On" : "Off";
    $("#liveBrightness").textContent = state.brightness;
    $("#liveVibe").textContent = state.vibe;
  }

  function setView(view, pushHash = true) {
    const allowed = ["now", "find", "compose", "touch", "health", "more"];
    state.view = allowed.includes(view) ? view : "now";
    if (pushHash && location.hash !== `#${state.view}`) history.pushState(null, "", `#${state.view}`);
    $$("[data-nav]").forEach((button) => {
      const active = button.dataset.nav === state.view;
      button.classList.toggle("is-active", active);
      if (button.classList.contains("place-link")) active ? button.setAttribute("aria-current", "page") : button.removeAttribute("aria-current");
    });
    $(".app-shell").dataset.view = state.view;
    render();
    window.scrollTo({ top: 0, behavior: "instant" });
  }

  function render() {
    ({ now: renderNow, find: renderFind, compose: renderCompose, touch: renderTouch, health: renderHealth, more: renderMore })[state.view]();
    syncLiveStrip();
    requestAnimationFrame(paintAllCanvases);
  }

  function renderNow() {
    const item = state.live;
    workspace.innerHTML = `<section class="view-frame" aria-labelledby="nowHeading">
      <div class="view-heading">
        <div><span class="eyebrow live-color">The physical room</span><h1 id="nowHeading">Know what’s happening. Change it calmly.</h1></div>
        <p>The wall in front of you is always described here. Auditions elsewhere remain isolated until you deliberately take them live.</p>
      </div>
      <div class="now-stage">
        <article class="wall-hero">
          ${canvasMarkup("live", item.seed + 31, state.liveStopped ? "Stopped physical wall" : `Live output: ${item.fullName}`, state.liveStopped ? "is-stopped" : "")}
          <div class="hero-readout">
            <span class="eyebrow live-color">${state.liveStopped ? "Stopped safely" : "Running now"}</span>
            <h2>${state.liveStopped ? "The wall is black." : escapeHTML(item.component.name)}</h2>
            <p>${state.liveStopped ? "Power remains on. Pick a recent look or find something new; nothing starts until you confirm it." : escapeHTML(item.description)}</p>
            <div class="meta-line">
              <span class="state-flag ${state.liveDirty ? "state-warning" : "state-saved"}">${state.liveDirty ? "Modified" : "Matches saved"}</span>
              <span class="meta-pill">${escapeHTML(item.name)}</span>
              <span class="meta-pill">${componentLabel(item)}</span>
              <span class="meta-pill">${escapeHTML(item.category)}</span>
            </div>
            <div class="stat-ribbon"><div><b>58.7</b><span>actual FPS</span></div><div><b>60</b><span>target FPS</span></div><div><b>${state.brightness}</b><span>hardware brightness</span></div><div><b>1.0×</b><span>operator speed</span></div></div>
            <div class="action-row"><button class="quiet-button" type="button" data-nav="touch">Interact</button><button class="quiet-button" type="button" data-live-adjust>Adjust live parameters</button><button class="stop-button" type="button" data-stop>${state.liveStopped ? "Already stopped" : "Stop output"}</button></div>
          </div>
        </article>
        <div class="now-choices">
          <article class="prompt-panel">
            <span class="eyebrow">Choose by intent</span><h2>What should the room become?</h2><p>Intent opens a ranked library index; it never starts output on its own.</p>
            <div class="prompt-options">
              <button class="prompt-option" type="button" data-intent="quiet"><span class="prompt-number">01</span><span><strong>Settle the room</strong><small>Quiet motion, low luminance, no game content</small></span><span class="prompt-arrow">→</span></button>
              <button class="prompt-option" type="button" data-intent="clock"><span class="prompt-number">02</span><span><strong>Make time visible</strong><small>Clocks and useful information with generous previews</small></span><span class="prompt-arrow">→</span></button>
              <button class="prompt-option" type="button" data-intent="play"><span class="prompt-number">03</span><span><strong>Play together</strong><small>Interactive and autonomous games, ready for touch mode</small></span><span class="prompt-arrow">→</span></button>
              <button class="prompt-option" type="button" data-nav="find"><span class="prompt-number">04</span><span><strong>Explore all 292 looks</strong><small>Search names, descriptions, providers, roles, and readiness</small></span><span class="prompt-arrow">→</span></button>
            </div>
          </article>
          <div><span class="eyebrow">Recent in this prototype</span><div class="history-strip">${[presets[8], presets[33], presets[67]].map((recent) => `<button class="history-item" type="button" data-audition="${escapeHTML(recent.id)}"><strong>${escapeHTML(recent.fullName)}</strong><span>${componentLabel(recent)} · ${escapeHTML(recent.category)}</span></button>`).join("")}</div></div>
        </div>
      </div>
    </section>`;
  }

  function filteredItems() {
    const pool = state.indexMode === "presets" ? presets : components;
    const query = state.query.trim().toLowerCase();
    return pool.filter((item) => {
      const haystack = `${item.fullName || item.name} ${item.description} ${item.category} ${item.provider} ${item.role}`.toLowerCase();
      const queryMatch = !query || query.split(/\s+/).every((word) => haystack.includes(word));
      const filterMatch = state.filter === "all" ||
        (state.filter === "show" && item.status === "ready" && item.category !== "Diagnostics") ||
        (state.filter === "plants" && (item.component || item).capabilities.includes("plant material")) ||
        (state.filter === "games" && item.category === "Games") ||
        (state.filter === "native" && item.provider === "receiver_native") ||
        (state.filter === "lab" && (item.category === "Diagnostics" || item.status !== "ready"));
      return queryMatch && filterMatch;
    });
  }

  function normalizeSelected(item) {
    if (!item) return state.indexMode === "presets" ? presets[0] : components[0];
    if (item.component) return item;
    const preset = presets.find((candidate) => candidate.componentKey === item.key);
    return preset || presets[0];
  }

  function renderFind() {
    const results = filteredItems();
    const draftInResults = state.indexMode === "presets" ? results.find((item) => item.id === state.draft.id) : results.find((item) => item.key === state.draft.componentKey);
    const selected = normalizeSelected(draftInResults || results[0]);
    if (selected && selected.id !== state.draft.id) state.draft = selected;
    const ready = selected.status === "ready";
    const provenance = selected.provider === "receiver_native" ? "Host-built simulation · not receiver framebuffer readback or exact live output" : "Isolated host preview · generated without changing live output";
    workspace.innerHTML = `<section class="find-view" aria-labelledby="findHeading">
      <header class="find-header">
        <div><span class="eyebrow">Audition library</span><h1 id="findHeading">Find a look</h1></div>
        <label class="search-wrap"><span class="sr-only">Search library</span><input class="search-input" id="librarySearch" type="search" value="${escapeHTML(state.query)}" placeholder="Try “quiet foliage”, “clock”, or “receiver native”" autocomplete="off"><span class="search-hint">/ to focus</span></label>
        <div class="result-count"><strong>${results.length}</strong> ${state.indexMode}<br>of ${state.indexMode === "presets" ? 292 : 52}</div>
      </header>
      <div class="find-toolbar" role="toolbar" aria-label="Library filters">
        ${[["show", "Show-ready"], ["plants", "Plant-aware"], ["games", "Games"], ["native", "Receiver-native"], ["lab", "Lab & diagnostics"], ["all", "Everything"]].map(([key, label]) => `<button class="filter-chip ${state.filter === key ? "is-active" : ""}" type="button" data-filter="${key}" aria-pressed="${state.filter === key}">${label}</button>`).join("")}
        <span class="toolbar-spacer"></span><span class="meta-pill">↑ ↓ browse</span><span class="meta-pill">C compare</span>
      </div>
      <div class="find-workbench">
        <section class="library-index" aria-label="Library index">
          <div class="index-mode" role="group" aria-label="Browse mode"><button class="${state.indexMode === "presets" ? "is-selected" : ""}" type="button" data-index-mode="presets">292 Presets</button><button class="${state.indexMode === "components" ? "is-selected" : ""}" type="button" data-index-mode="components">52 Components</button></div>
          <div class="index-list" id="indexList" role="listbox" aria-label="Search results" tabindex="0">${results.length ? results.map((item, index) => {
            const itemId = item.id || item.key;
            const activeId = state.indexMode === "presets" ? state.draft.id : state.draft.componentKey;
            return `<button class="index-row ${itemId === activeId ? "is-selected" : ""} ${item.status !== "ready" ? "is-unavailable" : ""}" type="button" role="option" aria-selected="${itemId === activeId}" data-select="${escapeHTML(itemId)}"><span class="row-index">${String(index + 1).padStart(3, "0")}</span><span><strong>${escapeHTML(item.fullName || item.name)}</strong><small>${escapeHTML(item.description)}</small><em>${componentLabel(item)} · ${statusLabel(item)} · ${escapeHTML(item.role.replaceAll("_", " "))}</em></span></button>`;
          }).join("") : `<div class="empty-state">No matches. Try a broader phrase or choose “Everything.”</div>`}</div>
        </section>
        <article class="audition-stage" aria-label="Isolated audition">
          <div class="audition-preview">${canvasMarkup("preview", selected.seed, `Preview of ${selected.fullName}`)}<p class="preview-provenance">${provenance}</p></div>
          <div class="detail-copy">
            <span class="eyebrow">Selected ${state.indexMode === "presets" ? "preset" : "component"}</span><h2>${escapeHTML(selected.fullName)}</h2>
            <p class="detail-description">${escapeHTML(selected.description)}</p>
            <div class="meta-line"><span class="state-flag ${ready ? "state-preview" : "state-warning"}">${statusLabel(selected)}</span><span class="meta-pill">${escapeHTML(selected.category)}</span><span class="meta-pill">${escapeHTML(selected.role.replaceAll("_", " "))}</span>${selected.stale ? `<span class="state-flag state-warning">Saved source drifted</span>` : ""}</div>
            <div class="identity-stack"><strong>${escapeHTML(selected.provider)} : ${escapeHTML(selected.componentId)}</strong>Provider-qualified identity · preset ${escapeHTML(selected.presetId)}</div>
            <div class="detail-actions"><button class="quiet-button" type="button" data-favorite>${state.favoriteKeys.has(selected.id) ? "★ Favorited" : "☆ Favorite"}</button><button class="quiet-button" type="button" data-compare>${state.compare.some((item) => item.id === selected.id) ? "In compare set" : "Add to compare"}</button><button class="live-button" type="button" data-take-live ${ready ? "" : "disabled"}>${ready ? "Take this preset live" : "Visible, but not executable here"}</button></div>
            <details class="parameter-panel" open><summary>Shape this preview <span class="state-flag state-preview">isolated</span></summary>
              <div class="param-field"><label for="driftRange">Authored drift speed <span id="driftValue">${state.params.drift.toFixed(2)}</span></label><input id="driftRange" data-param="drift" type="range" min="0.05" max="1" step="0.01" value="${state.params.drift}"></div>
              <div class="param-field"><label for="densityRange">Agent density <span id="densityValue">${state.params.density}</span></label><input id="densityRange" data-param="density" type="range" min="12" max="120" step="1" value="${state.params.density}"></div>
              <div class="param-field"><label for="paletteSelect">Authored palette <span>preset parameter</span></label><select id="paletteSelect" data-param="palette"><option ${state.params.palette === "Moss and rose" ? "selected" : ""}>Moss and rose</option><option ${state.params.palette === "Moonlit cyan" ? "selected" : ""}>Moonlit cyan</option><option ${state.params.palette === "Amber glass" ? "selected" : ""}>Amber glass</option></select></div>
              <label class="switch-label"><input type="checkbox" data-param="plant" ${state.params.plant ? "checked" : ""}><span></span> Respond to compatible global plant layers</label>
              <p class="preview-provenance">Authored drift speed is part of this preset. Global vibe tempo and the operator speed multiplier are separate controls.</p>
            </details>
          </div>
        </article>
        ${renderCompareSet()}
      </div>
    </section>`;
    const chosenRow = $(".index-row.is-selected");
    if (chosenRow) chosenRow.scrollIntoView({ block: "nearest" });
  }

  function renderCompareSet() {
    return `<aside class="compare-set ${state.compareCollapsed ? "is-collapsed" : ""}" aria-label="Comparison set"><button class="compare-head" type="button" data-compare-toggle aria-expanded="${!state.compareCollapsed}"><h3>Compare set <span>${state.compare.length}/3 ${state.compareCollapsed ? "▴" : "▾"}</span></h3><p>Hold up to three full identities before choosing.</p></button><div class="compare-items">${state.compare.length ? state.compare.map((item) => `<article class="compare-item"><span class="mini-wall" style="filter:hue-rotate(${item.seed * 13}deg)" aria-hidden="true"></span><div><strong>${escapeHTML(item.fullName)}</strong><small>${componentLabel(item)}<br>${escapeHTML(item.name)}</small></div><button class="remove-compare" type="button" data-remove-compare="${escapeHTML(item.id)}" aria-label="Remove ${escapeHTML(item.fullName)} from compare">×</button></article>`).join("") : `<div class="compare-empty">Select a look, then add it here. Names stay complete.</div>`}</div><button class="quiet-button compare-launch" type="button" data-review-compare ${state.compare.length > 1 ? "" : "disabled"}>Review ${state.compare.length || ""} side by side</button></aside>`;
  }

  function renderCompose() {
    const scene = state.scene;
    workspace.innerHTML = `<section class="view-frame" aria-labelledby="composeHeading">
      <div class="view-heading"><div><span class="eyebrow">A two-track score</span><h1 id="composeHeading">Compose before you perform.</h1></div><p>Background and clock are explicit tracks. The whole score validates and previews in isolation before it can replace live output.</p></div>
      <div class="compose-layout">
        <section class="score" aria-label="Scene score">
          <header class="score-head"><div><h2>Evening room score</h2><p>${scene.saved ? "Saved scene layout · global vibe and plants excluded by design" : "Unsaved draft · live output untouched"}</p></div><span class="state-flag ${scene.saved ? "state-saved" : "state-preview"}">${scene.saved ? "Saved" : "Draft"}</span></header>
          <div class="score-track"><span class="track-label">Track 1<br>Background</span><div class="track-content"><strong>${escapeHTML(scene.background.fullName)}</strong><span>${componentLabel(scene.background)} · ${escapeHTML(scene.background.name)}</span><small>Python fallback: Nocturne Ferns / Safe Night</small></div><div class="track-controls"><button class="compact-button" type="button" data-change-track="background">Replace</button><button class="compact-button" type="button" data-track-settings="background">Tune</button></div></div>
          <div class="score-track"><span class="track-label">Track 2<br>Clock overlay</span><div class="track-content"><strong>${scene.overlayEnabled ? escapeHTML(scene.overlay.name) : "Overlay disabled"}</strong><span>${scene.overlayEnabled ? `${scene.opacity}% opacity · ${scene.translation} LED translation` : "The background remains visible alone"}</span><small>${scene.overlayEnabled ? "Enabled · clip to wall · stale lease 30s" : "Optional track"}</small></div><div class="track-controls"><button class="compact-button" type="button" data-toggle-overlay>${scene.overlayEnabled ? "Disable" : "Enable"}</button><button class="compact-button" type="button" data-track-settings="overlay">Tune</button></div></div>
          <div class="score-settings"><label class="inline-field">Overlay opacity <input type="range" min="0" max="100" value="${scene.opacity}" data-scene="opacity"><span>${scene.opacity}%</span></label><label class="inline-field">Vertical translation <input type="range" min="-30" max="30" value="${scene.translation}" data-scene="translation"><span>${scene.translation} LEDs</span></label><label class="inline-field">Clip policy <select data-scene="clip"><option>Clip to wall</option><option>Wrap vertically</option><option>Reject overflow</option></select></label><label class="inline-field">Stale policy <select><option>Keep with 30s lease</option><option>Hide overlay</option><option>Stop scene</option></select></label></div>
          <footer class="score-foot"><span class="validation-state">Valid provider roles and fallback</span><div class="action-row"><button class="quiet-button" type="button" data-validate-scene>Validate again</button><button class="primary-button" type="button" data-save-scene>${scene.saved ? "Saved layout" : "Save layout only"}</button></div></footer>
        </section>
        <aside class="scene-stage" aria-label="Whole-scene isolated preview">
          ${canvasMarkup("preview", scene.background.seed + (scene.overlayEnabled ? 17 : 0), "Preview of composed background and clock scene")}
          <div class="preview-context"><div><span class="eyebrow">Whole-scene preview</span><h2>Room context, not scene content</h2></div><div class="context-block"><strong>Preview vibe · ${state.vibe}</strong><span>Inherited from global room layer; override would stay isolated.</span></div><div class="context-block"><strong>Plant material · 3 active</strong><span>Illuminate 70% · refract 45% · slow zone 30%</span></div><div class="context-block drift-warning"><strong>Preset source drift</strong><span>Overlay preset changed since this scene draft loaded. Review before saving.</span></div><p class="preview-provenance">Scene presets save layout, components, presets, and overrides only. They never capture vibe, plant modifiers, power, or brightness.</p><div class="scene-actions"><button class="quiet-button" type="button" data-preview-vibe>Override preview vibe</button><button class="quiet-button" type="button" data-target-update>Update background only</button><button class="live-button" type="button" data-take-scene>Take validated scene live</button></div></div>
        </aside>
      </div>
    </section>`;
  }

  function renderTouch() {
    workspace.innerHTML = `<section class="view-frame" aria-labelledby="touchHeading"><div class="touch-layout"><div class="touch-wall">${canvasMarkup("live", state.live.seed + 41, `Interactive live output for ${state.live.fullName}`)}<span class="touch-cursor" style="left:${state.pointer.x / 32 * 100}%;top:${state.pointer.y / 138 * 100}%" aria-hidden="true"></span></div><div class="interaction-panel"><span class="eyebrow live-color">Interaction takes over</span><h1 id="touchHeading">Play the wall, not the controls.</h1><p>Touch the physical-format surface or use the large game controls. These actions target live output; browsing controls step out of the way.</p><div class="mode-switch" role="group" aria-label="Interaction type"><button class="is-selected" type="button">Primary point</button><button type="button" data-hole>Random hole</button><button type="button" data-hole>Placed hole</button></div><div class="coordinate-readout"><span id="coordText">x ${state.pointer.x} · y ${state.pointer.y}</span><span>strength 0.80 · logical 32×138</span></div><div class="dpad" aria-label="Game controls"><button class="up" data-dpad="up" aria-label="Up">↑</button><button class="left" data-dpad="left" aria-label="Left">←</button><button class="drop" data-dpad="drop">DROP</button><button class="right" data-dpad="right" aria-label="Right">→</button><button class="rot-l" data-dpad="rotate-left">↶ ROTATE</button><button class="down" data-dpad="down" aria-label="Down">↓</button><button class="rot-r" data-dpad="rotate-right">ROTATE ↷</button></div><div class="action-row"><button class="quiet-button" type="button" data-nav="find">Exit to library</button><button class="stop-button" type="button" data-stop>Stop wall</button></div></div></div></section>`;
  }

  function renderHealth() {
    workspace.innerHTML = `<section class="view-frame" aria-labelledby="healthHeading"><div class="view-heading"><div><span class="eyebrow">From meaning to evidence</span><h1 id="healthHeading">The wall is keeping up.</h1></div><p>One expected limitation is explained below; it does not prevent tonight’s show.</p></div><div class="health-layout"><article class="health-summary"><span class="health-orb" aria-hidden="true"></span><div><h2>Hardware connected · output healthy</h2><p>All four receiver lanes agree on the active background. Frame delivery is within budget.</p></div><button class="quiet-button" type="button" data-refresh-health>Refresh receiver status</button></article><div class="health-columns"><section class="evidence-panel"><header class="evidence-head"><h3>Performance</h3><p>Human summary first; exact timing stays available.</p></header><div class="metric-list"><div class="metric-row"><span>Frame rate<small>60 target</small></span><b>58.7 FPS</b></div><div class="metric-row"><span>Render time<small>95th percentile</small></span><b>6.4 ms</b></div><div class="metric-row"><span>Send time<small>four SPI buses</small></span><b>3.1 ms</b></div><div class="metric-row"><span>Uptime<small>continuous controller runtime</small></span><b>19h 42m</b></div><div class="metric-row"><span>Frames<small>since controller start</small></span><b>4,171,803</b></div></div><details class="evidence-disclosure"><summary>Exact driver and device map evidence</summary><pre class="raw-evidence">spi0.0 → receiver 1 → LEDs 0–1103
spi0.1 → receiver 2 → LEDs 1104–2207
spi1.0 → receiver 3 → LEDs 2208–3311
spi1.1 → receiver 4 → LEDs 3312–4415</pre></details></section><section class="evidence-panel"><header class="evidence-head"><h3>Four receivers</h3><p>Agreement, fallback, release, and telemetry are distinct signals.</p></header><div class="receiver-list">${[1, 2, 3, 4].map((number) => `<div class="receiver-row"><span class="receiver-number">${number}</span><span><strong>${number === 3 ? "Active · expected limited return path" : "Active · agrees with controller"}</strong><small>${number === 3 ? "Telemetry incomplete · release acceptance not reported by this hardware path" : `Prism Rain build 24.8.17 · overlay host-composited`}</small></span><span class="${number === 3 ? "health-degraded" : "health-ok"}">${number === 3 ? "Expected degraded policy" : "Healthy"}</span></div>`).join("")}</div><details class="evidence-disclosure"><summary>Transport, quarantine, and staged/active evidence</summary><pre class="raw-evidence">transport_policy: hybrid-accept
fallback_active: false
quarantined_receivers: []
staged_background: receiver_native:prism-rain
active_background: receiver_native:prism-rain
overlay_mode: host_composited</pre></details></section></div></div></section>`;
  }

  function renderMore() {
    workspace.innerHTML = `<section class="view-frame" aria-labelledby="moreHeading"><div class="view-heading"><div><span class="eyebrow">Specialized workspaces</span><h1 id="moreHeading">Tools when you need them.</h1></div><p>Creation, calibration, and developer maintenance are available without competing with ordinary room operation.</p></div><div class="tool-shelf">${[["paint", "▦", "Painter & plant masks", "Paint pixels, clear to black, save frames, and edit foliage or seven-globe semantic layers.", "Prototype: undo proposed"], ["emoji", "☺", "Emoji arrangement", "Arrange expressive pixel glyphs on the tall physical canvas with a specialized placement flow.", "Specialized workflow"], ["developer", "⌘", "Developer room", "Refresh all plugins, reload one component, and inspect catalog-only, quarantined, or test content.", "Expert access"]].map(([key, icon, title, description, note]) => `<button class="tool-row" type="button" data-tool="${key}"><span class="tool-icon" aria-hidden="true">${icon}</span><span class="tool-copy"><strong>${title}</strong><span>${description}</span><small>${note}</small></span><span class="tool-arrow">→</span></button>`).join("")}</div>${state.activeTool ? renderSubtool(state.activeTool) : ""}</section>`;
  }

  function renderSubtool(tool) {
    if (tool === "developer") return `<section class="subtool-panel" aria-label="Developer room"><span class="eyebrow amber-color">Expert room</span><h2>Maintenance stays deliberate.</h2><p>52 discovered components · 3 not show-ready · hardware-connected mode.</p><div class="action-row"><button class="quiet-button" type="button" data-dev="refresh">Refresh all plugins</button><button class="quiet-button" type="button" data-dev="reload">Reload selected host plugin</button><button class="quiet-button" type="button" data-dev="catalog">Open lab content in Find</button></div></section>`;
    const title = tool === "paint" ? "Painter & masks" : "Emoji arrangement";
    return `<section class="subtool-panel" aria-label="${title}"><span class="eyebrow">Lower-fidelity specialized surface</span><h2>${title}</h2><div class="paint-demo">${canvasMarkup("preview", tool === "paint" ? 92 : 121, `${title} preview`)}<div><p>${tool === "paint" ? "Brush sparse pixel updates on the logical 32×138 surface, then save a frame or enter a separate semantic-mask layer. Undo is proposed by this prototype and would require frontend history." : "Select an emoji, place it on the tall canvas, and adjust its arrangement before any live apply."}</p><div class="action-row"><button class="quiet-button" type="button">Undo</button><button class="quiet-button" type="button">Clear to black</button><button class="primary-button" type="button">Save draft</button></div></div></div></section>`;
  }

  function openTakeLive(item, description) {
    pendingLive = item;
    $("#dialogDraftTitle").textContent = item.fullName || item.name;
    $("#dialogLiveTitle").textContent = state.liveStopped ? "Stopped wall" : state.live.fullName;
    $("#dialogDescription").textContent = description || "The current wall output will be replaced. Global vibe and plant layers remain independent.";
    $("#dialogBrightness").textContent = state.brightness;
    dialog.showModal();
  }

  function openComparison() {
    const grid = $("#comparisonGrid");
    $("#comparisonTitle").textContent = `Compare ${state.compare.length} tall looks`;
    grid.innerHTML = state.compare.map((item, index) => `<article class="comparison-choice">
      ${canvasMarkup("preview", item.seed + index * 5, `Comparison preview of ${item.fullName}`)}
      <div class="comparison-choice-copy">
        <span class="eyebrow">Choice ${index + 1}</span>
        <h3>${escapeHTML(item.fullName)}</h3>
        <p>${escapeHTML(item.description)}</p>
        <span class="comparison-identity">${escapeHTML(item.provider)} : ${escapeHTML(item.componentId)}<br>preset ${escapeHTML(item.presetId)}</span>
        <button class="primary-button" type="button" data-choose-comparison="${escapeHTML(item.id)}">Continue auditioning this look</button>
      </div>
    </article>`).join("");
    comparisonDialog.showModal();
    requestAnimationFrame(paintAllCanvases);
  }

  function stopWall() {
    if (state.liveStopped) return showToast("The wall is already stopped.");
    state.liveStopped = true;
    syncLiveStrip();
    render();
    showToast("Output stopped. Power remains on.");
  }

  function openRoomDrawer() {
    const drawer = $("#roomDrawer");
    drawer.classList.add("is-open");
    drawer.setAttribute("aria-hidden", "false");
    $(".scrim").hidden = false;
    $("[data-room-toggle]").setAttribute("aria-expanded", "true");
    $("[data-room-close]", drawer).focus();
  }

  function closeRoomDrawer() {
    const drawer = $("#roomDrawer");
    drawer.classList.remove("is-open");
    drawer.setAttribute("aria-hidden", "true");
    $(".scrim").hidden = true;
    $("[data-room-toggle]").setAttribute("aria-expanded", "false");
  }

  function findById(id) {
    return presets.find((item) => item.id === id) || components.find((item) => item.key === id);
  }

  document.addEventListener("click", (event) => {
    const nav = event.target.closest("[data-nav]");
    if (nav) { event.preventDefault(); setView(nav.dataset.nav); return; }
    if (event.target.closest("[data-open-health]")) return setView("health");
    if (event.target.closest("[data-room-toggle]")) return openRoomDrawer();
    if (event.target.closest("[data-room-close]")) return closeRoomDrawer();
    if (event.target.closest("[data-stop]")) return stopWall();
    const intent = event.target.closest("[data-intent]");
    if (intent) {
      state.query = ({ quiet: "quiet", clock: "clock", play: "games" })[intent.dataset.intent];
      state.filter = intent.dataset.intent === "play" ? "games" : "all";
      return setView("find");
    }
    const audition = event.target.closest("[data-audition]");
    if (audition) { state.draft = findById(audition.dataset.audition); state.query = ""; state.filter = "all"; return setView("find"); }
    const filter = event.target.closest("[data-filter]");
    if (filter) { state.filter = filter.dataset.filter; return renderFind(); }
    const mode = event.target.closest("[data-index-mode]");
    if (mode) { state.indexMode = mode.dataset.indexMode; return renderFind(); }
    const select = event.target.closest("[data-select]");
    if (select) { state.draft = normalizeSelected(findById(select.dataset.select)); return renderFind(); }
    if (event.target.closest("[data-favorite]")) {
      state.favoriteKeys.has(state.draft.id) ? state.favoriteKeys.delete(state.draft.id) : state.favoriteKeys.add(state.draft.id);
      renderFind(); return showToast(state.favoriteKeys.has(state.draft.id) ? "Saved as a local prototype favorite." : "Removed from favorites.");
    }
    if (event.target.closest("[data-compare]")) {
      if (!state.compare.some((item) => item.id === state.draft.id)) {
        if (state.compare.length === 3) return showToast("Compare set is full. Remove one look first.");
        state.compare.push(state.draft);
      }
      state.compareCollapsed = false;
      renderFind(); return showToast("Added to the three-look compare set.");
    }
    const remove = event.target.closest("[data-remove-compare]");
    if (remove) { state.compare = state.compare.filter((item) => item.id !== remove.dataset.removeCompare); return renderFind(); }
    if (event.target.closest("[data-compare-toggle]")) { state.compareCollapsed = !state.compareCollapsed; return renderFind(); }
    if (event.target.closest("[data-review-compare]")) return openComparison();
    const comparisonChoice = event.target.closest("[data-choose-comparison]");
    if (comparisonChoice) {
      state.draft = findById(comparisonChoice.dataset.chooseComparison);
      comparisonDialog.close();
      renderFind();
      return showToast("That choice is back on the audition stage. Live output is unchanged.");
    }
    if (event.target.closest("[data-take-live]")) return openTakeLive(state.draft);
    if (event.target.closest("[data-live-adjust]")) { state.draft = state.live; state.filter = "all"; state.query = ""; return setView("find"); }
    if (event.target.closest("[data-toggle-overlay]")) { state.scene.overlayEnabled = !state.scene.overlayEnabled; state.scene.saved = false; return renderCompose(); }
    if (event.target.closest("[data-change-track='background']")) { state.filter = "show"; state.query = ""; return setView("find"); }
    if (event.target.closest("[data-track-settings]")) return showToast("Track parameters are represented by the score controls below.");
    if (event.target.closest("[data-validate-scene]")) return showToast("Scene is valid: providers, roles, overlay policy, and Python fallback agree.");
    if (event.target.closest("[data-save-scene]")) { state.scene.saved = true; renderCompose(); return showToast("Saved layout only. Vibe, plants, power, and output state were excluded."); }
    if (event.target.closest("[data-take-scene]")) return openTakeLive({ ...state.scene.background, fullName: "Evening room score" }, "The validated two-track scene will replace current output atomically. Vibe and plant layers remain global and independent.");
    if (event.target.closest("[data-preview-vibe]")) return showToast("Preview vibe changed to Quiet here only. Live Cozy vibe is untouched.");
    if (event.target.closest("[data-target-update]")) return showToast("Background target update staged; overlay and live output are unchanged until confirmed.");
    const dpad = event.target.closest("[data-dpad]");
    if (dpad) return showToast(`${dpad.dataset.dpad.replace("-", " ")} sent to the live game.`);
    if (event.target.closest("[data-hole]")) return showToast("Puncture interaction sent to live output.");
    if (event.target.closest("[data-refresh-health]")) return showToast("Receiver refresh accepted. Evidence is current as of now.");
    const tool = event.target.closest("[data-tool]");
    if (tool) { state.activeTool = tool.dataset.tool; return renderMore(); }
    const developer = event.target.closest("[data-dev]");
    if (developer) {
      if (developer.dataset.dev === "catalog") { state.filter = "lab"; state.query = ""; return setView("find"); }
      return showToast(developer.dataset.dev === "refresh" ? "Plugin refresh requested." : "Selected host plugin reloaded.");
    }
    if (event.target.closest("[data-add-modifier]")) return showToast("Modifier chooser would show only vocabulary compatible with the current content and exclusivity rules.");
    if (event.target.closest("[data-room-apply]")) {
      state.roomDirty = false;
      $("#roomSaveState").textContent = "Room layers applied globally";
      syncLiveStrip();
      closeRoomDrawer();
      return showToast(`${state.vibe} vibe and three plant layers now follow live output and future starts.`);
    }
  });

  document.addEventListener("input", (event) => {
    if (event.target.id === "librarySearch") {
      state.query = event.target.value;
      const caret = event.target.selectionStart;
      renderFind();
      const input = $("#librarySearch");
      input.focus(); input.setSelectionRange(caret, caret);
    }
    if (event.target.matches("[data-param]")) {
      const key = event.target.dataset.param;
      state.params[key] = event.target.type === "checkbox" ? event.target.checked : event.target.type === "range" ? Number(event.target.value) : event.target.value;
      const output = $(`#${key}Value`);
      if (output) output.textContent = key === "drift" ? state.params[key].toFixed(2) : state.params[key];
    }
    if (event.target.matches("[data-scene]")) {
      const key = event.target.dataset.scene;
      state.scene[key] = event.target.type === "range" ? Number(event.target.value) : event.target.value;
      state.scene.saved = false;
      renderCompose();
    }
    if (event.target.closest(".modifier-row")) {
      const output = $("output", event.target.closest(".modifier-row"));
      output.textContent = `${Math.round(Number(event.target.value) * 100)}%`;
      state.roomDirty = true;
      $("#roomSaveState").textContent = "Room changes not yet applied";
    }
  });

  document.addEventListener("change", (event) => {
    if (event.target.matches("[data-vibe]")) return;
    if (event.target.id === "plantAware") {
      state.roomDirty = true;
      $("#roomSaveState").textContent = "Room changes not yet applied";
    }
  });

  $(".vibe-options").addEventListener("click", (event) => {
    const button = event.target.closest("[data-vibe]");
    if (!button) return;
    state.vibe = button.dataset.vibe;
    $$("[data-vibe]").forEach((option) => option.classList.toggle("is-selected", option === button));
    state.roomDirty = true;
    $("#roomSaveState").textContent = `${state.vibe} selected · not yet applied`;
  });

  $("#confirmTakeLive").addEventListener("click", () => {
    if (!pendingLive) return;
    state.live = pendingLive;
    state.liveStopped = false;
    state.liveDirty = state.params.drift !== 0.42 || state.params.density !== 64 || state.params.palette !== "Moss and rose";
    if ($("#atomicPower").checked) state.power = true;
    pendingLive = null;
    syncLiveStrip();
    setTimeout(() => { setView("now"); showToast("Live handoff complete. The wall is running the confirmed look."); }, 0);
  });

  workspace.addEventListener("pointerdown", (event) => {
    const frame = event.target.closest(".touch-wall .wall-frame");
    if (!frame) return;
    const rect = frame.getBoundingClientRect();
    state.pointer.x = Math.max(0, Math.min(31, Math.round((event.clientX - rect.left) / rect.width * 31)));
    state.pointer.y = Math.max(0, Math.min(137, Math.round((event.clientY - rect.top) / rect.height * 137)));
    renderTouch();
    showToast(`Primary interaction sent at ${state.pointer.x}, ${state.pointer.y} with strength 0.80.`);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "/" && state.view === "find" && document.activeElement !== $("#librarySearch")) {
      event.preventDefault(); $("#librarySearch").focus();
    }
    if (state.view === "find" && ["ArrowDown", "ArrowUp"].includes(event.key) && !["INPUT", "SELECT"].includes(document.activeElement.tagName)) {
      event.preventDefault();
      const results = filteredItems();
      const activeId = state.indexMode === "presets" ? state.draft.id : state.draft.componentKey;
      const current = Math.max(0, results.findIndex((item) => (item.id || item.key) === activeId));
      const next = Math.max(0, Math.min(results.length - 1, current + (event.key === "ArrowDown" ? 1 : -1)));
      state.draft = normalizeSelected(results[next]); renderFind();
    }
    if (state.view === "find" && event.key.toLowerCase() === "c" && !["INPUT", "SELECT"].includes(document.activeElement.tagName)) {
      const button = $("[data-compare]"); if (button) button.click();
    }
    if (event.key === "Escape" && $("#roomDrawer").classList.contains("is-open")) closeRoomDrawer();
  });

  function paintAllCanvases(time = performance.now()) {
    $$("canvas.wall-canvas").forEach((canvas) => paintCanvas(canvas, time));
  }

  function paintCanvas(canvas, time) {
    const ctx = canvas.getContext("2d");
    const seed = Number(canvas.dataset.seed || 1);
    const stopped = canvas.closest(".is-stopped");
    const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const tick = reduced ? 0 : time * (0.00018 + state.params.drift * 0.00025);
    ctx.fillStyle = "#060a07";
    ctx.fillRect(0, 0, 64, 276);
    if (stopped) return;
    const palettes = [
      ["#0f2720", "#49cfad", "#f1849e", "#f2bc67"],
      ["#101b2c", "#4ecce8", "#8064de", "#ffc56e"],
      ["#27131f", "#f05f81", "#6fd0a5", "#ffd6a2"],
      ["#15251b", "#8cc36d", "#3b8b7a", "#e39b63"]
    ];
    const palette = palettes[seed % palettes.length];
    ctx.fillStyle = palette[0]; ctx.fillRect(0, 0, 64, 276);
    for (let y = 0; y < 276; y += 4) {
      for (let x = 0; x < 64; x += 4) {
        const wave = Math.sin(y * 0.071 + tick * 9 + seed) + Math.cos(x * 0.19 - tick * 7 + y * 0.017);
        if (wave > 1.02) { ctx.globalAlpha = 0.25 + (wave - 1) * 0.24; ctx.fillStyle = palette[1]; ctx.fillRect(x, y, 3, 3); }
        else if (wave < -1.18) { ctx.globalAlpha = 0.34; ctx.fillStyle = palette[2]; ctx.fillRect(x, y, 2, 4); }
      }
    }
    ctx.globalAlpha = 0.42;
    for (let i = 0; i < 7; i += 1) {
      const gx = 11 + (i % 2) * 34 + Math.sin(seed + i) * 3;
      const gy = 24 + i * 36;
      ctx.strokeStyle = palette[3]; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(gx, gy, 6 + (i % 3), 0, Math.PI * 2); ctx.stroke();
    }
    ctx.globalAlpha = 0.32; ctx.fillStyle = "#08120c";
    for (let i = 0; i < 19; i += 1) {
      const fx = 4 + ((i * 17 + seed * 5) % 57);
      const fy = 8 + ((i * 43 + seed * 11) % 260);
      ctx.beginPath(); ctx.ellipse(fx, fy, 6, 2.5, (i % 4) * 0.7, 0, Math.PI * 2); ctx.fill();
    }
    if (seed % 3 === 0 || state.view === "compose") {
      ctx.globalAlpha = 0.9; ctx.fillStyle = "#f2f4df"; ctx.font = "bold 9px ui-monospace";
      ctx.fillText("9:42", 18, 134);
    }
    ctx.globalAlpha = 1;
  }

  window.addEventListener("hashchange", () => setView(location.hash.slice(1), false));
  setView(location.hash.slice(1) || "now", false);
  const animationLoop = (time) => { paintAllCanvases(time); requestAnimationFrame(animationLoop); };
  requestAnimationFrame(animationLoop);
})();
