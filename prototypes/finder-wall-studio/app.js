(() => {
  "use strict";

  const categorySpecs = [
    { name: "Ambient Art", icon: "✦", description: "Show-ready ambient backgrounds and generative studies", count: 15 },
    { name: "Clocks & Information", icon: "◷", description: "Time, weather, household and installation information", count: 8 },
    { name: "Interactive & Games", icon: "◆", description: "Point interaction, autonomous play and game controls", count: 8 },
    { name: "GIF & Pixel Art", icon: "▣", description: "Looping art packs and prepared pixel compositions", count: 7 },
    { name: "Diagnostics & Calibration", icon: "⊞", description: "Installation, mapping and signal-verification content", count: 7 },
    { name: "Developer & Test", icon: "⌘", description: "Build-only, quarantined and engineering content", count: 7 }
  ];

  const namesByCategory = [
    [
      "Cozy Ember Canopy", "Rain Ladder Through Monstera Leaves", "Moonlit Kelp Current", "Slow Aurora for the Tall Room", "Firefly Habitat at Blue Hour",
      "Graphite Fountain with Moss Reflections", "Sunrise Understory Gradient", "Quiet Copper Mycelium", "Celebration Confetti Vine", "Winter Window Light",
      "Deep Aquarium Caustics", "Falling Ginkgo Ribbons", "Pollinator Drift Field", "Candlelit Topographic Bloom", "Long-Form Chromatic Weather Study for the Living Wall"
    ],
    [
      "Horizon Numerals", "Botanical Flip Clock", "Daylight Arc & Civil Twilight", "Household Weather Ribbon", "Next Event: Calm Calendar", "Moon Phase Among Leaves", "Transit Departure Ladder", "Studio Timecode Overlay"
    ],
    [
      "Pond Puncture Playground", "Canopy Snake", "Falling Blocks: Portrait Well", "Repulsor Fireflies", "Four-Way Seed Drop", "Autonomous Habitat Survey", "Portal Pinball Vines", "Touch-Ripple Rain Game"
    ],
    [
      "Tiny Hikers Ascending the Plant Wall", "Koi Postcard Loop", "Eight-Bit Garden Almanac", "Dancing Houseplants Collection", "Seasonal Window Sprites", "Miniature Train Through Ferns", "Archival Emoji Tapestry"
    ],
    [
      "Receiver Quadrant Agreement", "LED Address Serpentine Walk", "Foliage Occlusion Proof", "Seven Globe Mask Verification", "SPI Bus Color Bars", "Frame-Pacing Staircase", "Camera Homography Alignment Grid"
    ],
    [
      "Native Overlay Lease Torture Test", "Catalog-Only Shader Manifest", "Quarantined Frame Allocation Probe", "Unavailable Receiver Firmware Sample", "Build-Only Signed Module Harness", "Unsupported Full-Scene Compatibility Fixture", "Transport Loss Recovery Laboratory"
    ]
  ];

  const receiverIndexes = new Set([2, 5, 9, 14, 17, 20, 23, 26, 29, 32, 35]);
  const favoriteIndexes = new Set([0, 2, 4, 7, 13, 16, 23, 28, 32]);
  const recentIndexes = new Set([0, 1, 3, 4, 7, 9, 12, 15, 18, 23, 24, 27, 31, 34]);
  const availabilityTail = ["build-only", "unavailable", "build-only", "quarantined", "unavailable", "build-only", "catalog-only", "unsupported", "build-only"];
  const presetWords = ["Hearthside", "Dusk Study", "Quiet Passage", "Gallery Default", "Saturated Variation", "Low-Luminance Overnight"];
  const descriptions = [
    "Low embers travel behind the canopy with restrained gold sparks and a warm breathing cadence.",
    "Layered motion moves through calibrated foliage regions while preserving a stable, quiet silhouette.",
    "A long-form portrait composition authored for the physical wall rather than a cropped landscape display.",
    "An installation-ready study with semantic palette, tempo, luminance, and plant-mask support.",
    "Crisp pixel movement stays legible through leaves and across the four receiver quadrants."
  ];

  let globalIndex = 0;
  const components = namesByCategory.flatMap((names, categoryIndex) => names.map((name, localIndex) => {
    const index = globalIndex++;
    const provider = receiverIndexes.has(index) ? "receiver.native" : "host.python";
    const role = categoryIndex === 1 ? "overlay" : (index === 48 || index === 50 ? "compatibility full-scene" : "background");
    const availability = index < 43 ? "show-ready" : availabilityTail[index - 43];
    const presetCount = index < 32 ? 6 : 5;
    const presets = Array.from({ length: presetCount }, (_, presetIndex) => ({
      id: `preset-${index + 1}-${presetIndex + 1}`,
      name: index === 0 && presetIndex === 0 ? "Hearthside" : index === 0 && presetIndex === 1 ? "Embers Behind the Long Philodendron Silhouette" : presetWords[presetIndex],
      description: descriptions[(index + presetIndex) % descriptions.length]
    }));
    return {
      id: `component-${index + 1}`,
      index,
      name,
      category: categorySpecs[categoryIndex].name,
      categoryIndex,
      provider,
      role,
      availability,
      presets,
      favorite: favoriteIndexes.has(index),
      recent: recentIndexes.has(index),
      tags: [categoryIndex === 4 ? "calibration" : "portrait", provider === "receiver.native" ? "native" : "python", index % 2 ? "quiet" : "show"],
      description: `${descriptions[index % descriptions.length]} ${provider === "receiver.native" ? "Its native preview is a labeled host-build simulation, never receiver framebuffer readback." : "It supports isolated host preview without changing the physical wall."}`
    };
  }));

  const state = {
    source: "all",
    category: categorySpecs[0].name,
    component: components[0],
    preset: components[0].presets[0],
    view: "columns",
    compare: [
      { component: components[0], preset: components[0].presets[0] },
      { component: components[2], preset: components[2].presets[1] },
      { component: components[14], preset: components[14].presets[2] }
    ],
    workspace: "library",
    mobileLevel: "sources",
    pendingAction: null,
    wallPower: true,
    vibe: "Cozy"
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const app = $("#app");
  const isMobile = () => window.matchMedia("(max-width: 760px)").matches;

  function escapeHTML(value) {
    return String(value).replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
  }

  function sourceComponents() {
    const query = $("#library-search").value.trim().toLowerCase();
    let result = components.filter(component => {
      if (state.source === "favorites" && !component.favorite) return false;
      if (state.source === "recent" && !component.recent) return false;
      if (state.source === "show-ready" && component.availability !== "show-ready") return false;
      if (state.source === "receiver" && component.provider !== "receiver.native") return false;
      return true;
    });
    if (query) {
      result = result.filter(component => [component.name, component.provider, component.role, component.category, component.tags.join(" "), ...component.presets.map(preset => preset.name)].join(" ").toLowerCase().includes(query));
    }
    return result;
  }

  function labelAvailability(value) {
    return ({ "show-ready": "Show-ready", "build-only": "Build-only", unavailable: "Unavailable", quarantined: "Quarantined", "catalog-only": "Catalog only", unsupported: "Unsupported" })[value] || value;
  }

  function renderLibrary() {
    const filtered = sourceComponents();
    const categoryCounts = categorySpecs.map(category => ({ ...category, filteredCount: filtered.filter(item => item.category === category.name).length }));
    if (!categoryCounts.some(item => item.name === state.category && item.filteredCount)) {
      state.category = categoryCounts.find(item => item.filteredCount)?.name || categorySpecs[0].name;
    }
    const categoryItems = filtered.filter(component => component.category === state.category);
    if (!categoryItems.includes(state.component)) state.component = categoryItems[0] || null;
    if (state.component && !state.component.presets.includes(state.preset)) state.preset = state.component.presets[0];

    $("#category-list").innerHTML = categoryCounts.map(category => `
      <button type="button" role="option" aria-selected="${category.name === state.category}" class="${category.name === state.category ? "selected" : ""}" data-category="${escapeHTML(category.name)}" ${category.filteredCount ? "" : "disabled"}>
        <span class="type-icon" aria-hidden="true">${category.icon}</span><span class="list-content"><strong>${escapeHTML(category.name)}</strong><span class="list-meta">${escapeHTML(category.description)}</span></span><span class="chevron" aria-hidden="true">›</span>
      </button>`).join("");
    $("#category-count").textContent = categoryCounts.filter(item => item.filteredCount).length;

    $("#component-list").innerHTML = categoryItems.length ? categoryItems.map(component => {
      const selectable = component.availability === "show-ready";
      return `<button type="button" role="option" aria-selected="${component === state.component}" class="${component === state.component ? "selected " : ""}${selectable ? "" : "unavailable"}" data-component="${component.id}" ${selectable ? "" : `disabled aria-disabled="true" title="${escapeHTML(labelAvailability(component.availability))}: visible for catalog honesty but unavailable for selection"`}>
        <span class="type-icon" aria-hidden="true">${component.provider === "receiver.native" ? "▣" : "▦"}</span><span class="list-content"><strong>${escapeHTML(component.name)}</strong><span class="list-meta">${escapeHTML(component.provider)} · ${escapeHTML(component.role)}</span></span>${selectable ? '<span class="chevron" aria-hidden="true">›</span>' : `<span class="state-mark">${escapeHTML(labelAvailability(component.availability))}</span>`}
      </button>`;
    }).join("") : `<p class="empty-message">No components match this source and search.</p>`;
    $("#component-count").textContent = categoryItems.length;

    $("#preset-list").innerHTML = state.component ? state.component.presets.map(preset => `
      <button type="button" role="option" aria-selected="${preset === state.preset}" class="${preset === state.preset ? "selected" : ""}" data-preset="${preset.id}">
        <span class="type-icon" aria-hidden="true">◫</span><span class="list-content"><strong>${escapeHTML(preset.name)}</strong><span class="list-meta">Named preset · ${escapeHTML(state.component.provider)}</span></span><span class="chevron" aria-hidden="true">›</span>
      </button>`).join("") : "";
    $("#preset-count").textContent = state.component?.presets.length || 0;
    updateDetail();
    updatePath();
    $("#status-summary").textContent = `${filtered.length} of 52 components, ${filtered.reduce((sum, component) => sum + component.presets.length, 0)} of 292 presets`;
    $("#status-selection").textContent = state.preset ? "1 preset selected" : "No selection";
  }

  function updateDetail() {
    if (!state.component || !state.preset) return;
    $("#preview-title").textContent = state.preset.name;
    $("#preview-component-name").textContent = state.component.name;
    $("#preview-provider").textContent = state.component.provider;
    $("#preview-role").textContent = state.component.role;
    $("#preview-compat").textContent = labelAvailability(state.component.availability);
    $("#preview-description").textContent = state.preset.description;
    $("#favorite-button").textContent = state.component.favorite ? "★" : "☆";
    $("#favorite-button").setAttribute("aria-pressed", String(state.component.favorite));
    $("#favorite-button").setAttribute("aria-label", state.component.favorite ? "Remove from favorites" : "Add to favorites");
    $("#main-preview").setAttribute("aria-label", `Isolated preview of ${state.preset.name} from ${state.component.name} on a 32 by 138 LED canvas`);
    drawWall($("#main-preview"), `${state.component.id}-${state.preset.id}`, { motion: Number($("#motion-slider").value), density: Number($("#density-slider").value), palette: $("#palette-select").value });
  }

  function updatePath() {
    const parts = $("#path-parts");
    const sourceNames = { all: "All Content", favorites: "Favorites", recent: "Recently Played", "show-ready": "Show-Ready", receiver: "Receiver-Native" };
    parts.innerHTML = `<li><button type="button" data-path-level="source">${sourceNames[state.source]}</button></li><li><button type="button" data-path-level="category">${escapeHTML(state.category)}</button></li>${state.component ? `<li><button type="button" data-path-level="component">${escapeHTML(state.component.name)}</button></li>` : ""}${state.preset ? `<li aria-current="page">${escapeHTML(state.preset.name)}</li>` : ""}`;
  }

  function hashSeed(input) {
    let hash = 2166136261;
    for (const char of input) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619);
    return hash >>> 0;
  }

  function seeded(seed) {
    let value = seed || 1;
    return () => {
      value = (Math.imul(value, 1664525) + 1013904223) >>> 0;
      return value / 4294967296;
    };
  }

  const palettes = {
    "Warm Canopy": [[8, 10, 10], [61, 23, 14], [151, 55, 25], [238, 151, 54], [255, 218, 123]],
    "Moonlit Moss": [[3, 8, 10], [8, 34, 37], [22, 85, 76], [74, 149, 120], [168, 221, 175]],
    "Winter Graphite": [[5, 8, 12], [28, 41, 55], [59, 81, 102], [119, 146, 164], [215, 230, 236]],
    Vivid: [[5, 5, 18], [69, 25, 139], [14, 130, 162], [235, 76, 99], [255, 202, 49]]
  };

  function drawWall(canvas, seedText, options = {}) {
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const random = seeded(hashSeed(seedText));
    const palette = palettes[options.palette] || (seedText.includes("material") ? palettes["Moonlit Moss"] : (hashSeed(seedText) % 3 === 0 ? palettes.Vivid : palettes["Warm Canopy"]));
    const scaleX = canvas.width / 32;
    const scaleY = canvas.height / 138;
    const density = (options.density ?? 62) / 100;
    ctx.fillStyle = "#030607";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    for (let y = 0; y < 138; y++) {
      for (let x = 0; x < 32; x++) {
        const wave = (Math.sin(y * .115 + x * .28 + (hashSeed(seedText) % 17)) + 1) / 2;
        const foliageGap = ((x - 15) ** 2 / 120 + ((y % 48) - 25) ** 2 / 760) < .8 && y % 52 > 10;
        const chance = random();
        let level = Math.floor((wave * .55 + chance * .45) * palette.length);
        if (chance > density || foliageGap && (x + y) % 4 !== 0) level = 0;
        level = Math.min(palette.length - 1, Math.max(0, level));
        const color = palette[level];
        ctx.fillStyle = `rgb(${color[0]},${color[1]},${color[2]})`;
        ctx.fillRect(x * scaleX, y * scaleY, Math.ceil(scaleX - .25), Math.ceil(scaleY - .25));
      }
    }
    ctx.fillStyle = "rgba(255,255,255,.14)";
    for (let i = 0; i < 12; i++) {
      const x = Math.floor(random() * 32) * scaleX;
      const y = Math.floor(random() * 138) * scaleY;
      ctx.fillRect(x, y, Math.max(1, scaleX - .5), Math.max(1, scaleY - .5));
    }
  }

  function renderOutline() {
    $("#outline-body").innerHTML = sourceComponents().map(component => `<tr data-outline-component="${component.id}"><td><button type="button"><span aria-hidden="true">${component.provider === "receiver.native" ? "▣" : "▦"}</span>${escapeHTML(component.name)}</button></td><td>${escapeHTML(component.provider)}</td><td>${escapeHTML(component.role)}</td><td>${component.presets.length}</td><td>${escapeHTML(labelAvailability(component.availability))}</td></tr>`).join("");
  }

  function renderCompare() {
    const grid = $("#compare-grid");
    if (!state.compare.length) {
      grid.innerHTML = `<div class="info-callout"><strong>No candidates yet.</strong><p>Select a preset in the library and choose Add to Compare.</p></div>`;
      return;
    }
    grid.innerHTML = state.compare.map((candidate, index) => `<article class="compare-card"><header><p class="eyebrow">CANDIDATE ${index + 1} · ISOLATED ${candidate.component.provider === "receiver.native" ? "HOST-BUILD SIMULATION" : "HOST PREVIEW"}</p><h3>${escapeHTML(candidate.preset.name)}</h3><p>${escapeHTML(candidate.component.name)}</p></header><div class="compare-canvas-wrap"><canvas class="compare-canvas" width="96" height="414" data-compare-canvas="${index}" aria-label="Isolated 32 by 138 preview of ${escapeHTML(candidate.preset.name)}"></canvas></div><div class="compare-meta"><div class="compare-identity"><span>${escapeHTML(candidate.component.provider)}</span><span>${escapeHTML(candidate.component.role)}</span><span>${escapeHTML(labelAvailability(candidate.component.availability))}</span></div><p>${escapeHTML(candidate.preset.description)}</p><button class="default-button" type="button" data-compare-live="${index}">Take This Live…</button></div></article>`).join("");
    $$('[data-compare-canvas]').forEach(canvas => {
      const candidate = state.compare[Number(canvas.dataset.compareCanvas)];
      drawWall(canvas, `${candidate.component.id}-${candidate.preset.id}`);
    });
  }

  function setView(view) {
    state.view = view;
    $$(".view-switcher button").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.view === view)));
    $$("#library-workspace .view-panel").forEach(panel => panel.classList.remove("active"));
    $(`#${view === "columns" ? "column-browser" : view === "outline" ? "outline-view" : "compare-view"}`).classList.add("active");
    if (view === "outline") renderOutline();
    if (view === "compare") renderCompare();
    if (isMobile()) setMobileLevel(view === "columns" ? "categories" : "detail");
  }

  const workspaceTitles = {
    library: ["Show Library", "52 components · 292 presets"],
    scene: ["Scene Composer", "Layout-only composition"],
    materials: ["Vibe & Plant Material", "Independent global settings"],
    operations: ["Operations", "Hardware-connected evidence"],
    painter: ["Painter & Masks", "Direct manipulation tools"],
    emoji: ["Emoji Arranger", "Specialized placement workflow"],
    developer: ["Developer Tools", "Restricted engineering controls"]
  };

  function showWorkspace(name) {
    state.workspace = name;
    $$(".workspace").forEach(workspace => workspace.classList.remove("active"));
    $(`#${name}-workspace`).classList.add("active");
    const title = workspaceTitles[name];
    $("#toolbar-title").textContent = title[0];
    $("#toolbar-subtitle").textContent = title[1];
    $(".search-field").hidden = name !== "library";
    $$(".view-switcher, #show-inspector").forEach(element => element.hidden = name !== "library");
    if (name !== "library") {
      const locations = {
        scene: ["Scenes", "Scene Composer"],
        materials: ["Wall", "Vibe & Plant Material"],
        operations: ["Wall", "Operations"],
        painter: ["Tools", "Painter & Masks"],
        emoji: ["Tools", "Emoji Arranger"],
        developer: ["Tools", "Developer"]
      };
      const location = locations[name];
      $("#path-parts").innerHTML = `<li>${escapeHTML(location[0])}</li><li aria-current="page">${escapeHTML(location[1])}</li>`;
    } else {
      updatePath();
    }
    if (name === "scene") drawWall($("#scene-preview"), "scene-evening-clock", { palette: "Moonlit Moss", density: 69 });
    if (name === "materials") drawWall($("#material-preview"), `material-${state.vibe}`, { palette: state.vibe === "Vivid" || state.vibe === "Celebration" ? "Vivid" : "Moonlit Moss", density: 66 });
    if (isMobile()) setMobileLevel(name === "library" ? "categories" : "detail");
  }

  function setMobileLevel(level) {
    state.mobileLevel = level;
    app.dataset.mobileLevel = level;
    const labels = { sources: "Sources", categories: "Categories", components: state.category, presets: state.component?.name || "Components", detail: state.workspace === "library" ? state.preset?.name || "Preview" : workspaceTitles[state.workspace]?.[0] || "Detail" };
    $("#mobile-level-label").textContent = labels[level];
    $("#mobile-back-label").textContent = level === "categories" || (level === "detail" && state.workspace !== "library") ? "Sources" : ({ components: "Categories", presets: state.category, detail: state.component?.name })[level] || "Back";
  }

  function toast(message) {
    const element = $("#toast");
    element.textContent = message;
    element.classList.add("visible");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => element.classList.remove("visible"), 2600);
  }

  function configureSheet({ title, description, icon = "▦", confirm = "Apply", rows, action }) {
    $("#sheet-title").textContent = title;
    $("#sheet-description").textContent = description;
    $("#sheet-icon").textContent = icon;
    $("#sheet-confirm").textContent = confirm;
    $("#sheet-summary").innerHTML = rows.map(([term, detail]) => `<div><dt>${escapeHTML(term)}</dt><dd>${escapeHTML(detail)}</dd></div>`).join("");
    state.pendingAction = action;
    $("#action-sheet").showModal();
  }

  function askTakeLive(component = state.component, preset = state.preset) {
    configureSheet({
      title: `Take “${preset.name}” Live?`,
      description: "This will replace the current physical wall output. The isolated preview variant will be applied atomically with the selected preset.",
      confirm: "Take Live",
      rows: [["Complete identity", `${component.name} — ${preset.name} · ${component.provider}`], ["Will change", "Animation, preset, and supported parameters"], ["Will not change", "Power, brightness, global vibe, or plant material"]],
      action: () => {
        $("#live-name").textContent = `${component.name} — ${preset.name}`;
        $("#live-provider").textContent = `${component.provider} / ${component.role}`;
        $("#live-save-state").textContent = "Applied";
        if (!state.wallPower) setWallPower(true);
        toast(`“${preset.name}” is now on the physical wall.`);
      }
    });
  }

  function setWallPower(on) {
    state.wallPower = on;
    $("#power-button").setAttribute("aria-pressed", String(on));
    $("#power-label").textContent = on ? "On" : "Off";
    $("#power-button").classList.toggle("off", !on);
    $(".on-air-dot").style.background = on ? "#e04230" : "#6e777d";
  }

  function setSceneInspector(node) {
    const content = $("#scene-inspector-content");
    const title = $("#scene-inspector-title");
    if (node === "background") {
      title.textContent = "Background";
      content.innerHTML = `<fieldset class="native-fieldset"><legend>Component</legend><label>Background<select><option>Cozy Ember Canopy</option><option>Moonlit Kelp Current</option></select></label><label>Preset<select><option>Hearthside</option><option>Dusk Study</option></select></label></fieldset><div class="info-callout"><strong>Receiver-native safety</strong><p>If a native background is selected, whole-scene preview uses its known Python fallback and labels that provenance.</p></div>`;
    } else if (node === "overlay") {
      title.textContent = "Clock Overlay";
      content.innerHTML = `<fieldset class="native-fieldset"><legend>Overlay</legend><label class="checkbox-row"><input type="checkbox" checked> Enabled</label><label>Opacity <input type="range" min="0" max="100" value="82"></label><label>Strip translation <input type="number" min="-31" max="31" value="0"></label><label>LED translation <input type="number" min="-137" max="137" value="-4"></label><label>Clip policy<select><option>Clip to wall</option><option>Wrap</option></select></label><label>Lease / stale policy<select><option>Keep last valid frame</option><option>Disable overlay</option></select></label></fieldset>`;
    } else if (node === "validation") {
      title.textContent = "Validation";
      content.innerHTML = `<dl class="key-value-list"><div><dt>Background</dt><dd>Compatible</dd></div><div><dt>Overlay</dt><dd>Compatible</dd></div><div><dt>Native fallback</dt><dd>Known</dd></div><div><dt>Dirty</dt><dd>Preview differs</dd></div><div><dt>Live drift</dt><dd>Background preset</dd></div></dl>`;
    } else {
      title.textContent = "Scene";
      content.innerHTML = `<fieldset class="native-fieldset"><legend>Scene Identity</legend><label>Name<input value="Evening with Clock"></label><label>Stale policy<select><option>Keep last valid frame</option><option>Disable overlay</option><option>Stop scene</option></select></label></fieldset><div class="info-callout"><strong>Scene presets are layout-only.</strong><p>They never capture global vibe, plant material, wall power, or hardware brightness.</p></div>`;
    }
  }

  function setReceiver(receiver) {
    const degraded = receiver === "C";
    const positions = { A: "Upper West", B: "Upper East", C: "Lower West", D: "Lower East" };
    $("#receiver-title").textContent = `Receiver ${receiver} · ${positions[receiver]}`;
    $("#receiver-details").innerHTML = `<div><dt>Agreement</dt><dd><span class="mini-dot good"></span> Confirmed</dd></div><div><dt>Transport</dt><dd>${degraded ? "Reduced telemetry (configured)" : "Full telemetry"}</dd></div><div><dt>Active background</dt><dd>Cozy Ember Canopy</dd></div><div><dt>Overlay mode</dt><dd>Host composited</dd></div><div><dt>Frame sequence</dt><dd>529,884</dd></div><div><dt>Last contact</dt><dd>${degraded ? "61" : "24"} ms ago</dd></div><div><dt>Errors</dt><dd>None</dd></div>`;
    $("#degraded-explainer").hidden = !degraded;
  }

  $("#category-list").addEventListener("click", event => {
    const button = event.target.closest("[data-category]");
    if (!button) return;
    state.category = button.dataset.category;
    state.component = sourceComponents().find(component => component.category === state.category) || null;
    state.preset = state.component?.presets[0] || null;
    renderLibrary();
    if (isMobile()) setMobileLevel("components");
  });

  $("#component-list").addEventListener("click", event => {
    const button = event.target.closest("[data-component]");
    if (!button) return;
    state.component = components.find(component => component.id === button.dataset.component);
    state.preset = state.component.presets[0];
    renderLibrary();
    if (isMobile()) setMobileLevel("presets");
  });

  $("#preset-list").addEventListener("click", event => {
    const button = event.target.closest("[data-preset]");
    if (!button) return;
    state.preset = state.component.presets.find(preset => preset.id === button.dataset.preset);
    renderLibrary();
    if (isMobile()) setMobileLevel("detail");
  });

  $("#source-list").addEventListener("click", event => {
    const button = event.target.closest("button");
    if (!button) return;
    $$("#source-list button").forEach(item => item.classList.toggle("selected", item === button));
    if (button.dataset.source) {
      state.source = button.dataset.source;
      showWorkspace("library");
      setView("columns");
      renderLibrary();
      if (isMobile()) setMobileLevel("categories");
      return;
    }
    const destination = button.dataset.destination;
    if (destination === "now-playing") {
      toast("Current output is always available in the persistent live strip.");
      if (isMobile()) setMobileLevel("sources");
    } else if (destination === "scene" || destination === "scene-saved") showWorkspace("scene");
    else if (["materials", "operations", "painter", "emoji", "developer"].includes(destination)) showWorkspace(destination);
  });

  $$(".view-switcher button").forEach(button => button.addEventListener("click", () => setView(button.dataset.view)));
  $("#library-search").addEventListener("input", () => { showWorkspace("library"); renderLibrary(); renderOutline(); });
  $("#show-inspector").addEventListener("click", event => { const hidden = $("#library-detail").classList.toggle("hidden"); event.currentTarget.setAttribute("aria-pressed", String(!hidden)); });

  $("#motion-slider").addEventListener("input", event => { $("#motion-output").textContent = (event.target.value / 100).toFixed(2); updateDetail(); });
  $("#density-slider").addEventListener("input", event => { $("#density-output").textContent = `${event.target.value}%`; updateDetail(); });
  $("#palette-select").addEventListener("change", updateDetail);
  $("#favorite-button").addEventListener("click", () => { state.component.favorite = !state.component.favorite; updateDetail(); toast(state.component.favorite ? "Added to Favorites." : "Removed from Favorites."); });
  $("#take-live").addEventListener("click", () => askTakeLive());
  $("#add-compare").addEventListener("click", () => {
    if (!state.compare.some(item => item.preset.id === state.preset.id)) {
      if (state.compare.length >= 3) state.compare.shift();
      state.compare.push({ component: state.component, preset: state.preset });
    }
    setView("compare");
    toast("Candidate added to isolated comparison.");
  });
  $("#clear-compare").addEventListener("click", () => { state.compare = []; renderCompare(); });
  $("#compare-grid").addEventListener("click", event => { const button = event.target.closest("[data-compare-live]"); if (button) { const candidate = state.compare[Number(button.dataset.compareLive)]; askTakeLive(candidate.component, candidate.preset); } });

  $("#outline-body").addEventListener("click", event => {
    const row = event.target.closest("[data-outline-component]");
    if (!row) return;
    $$("#outline-body tr").forEach(item => item.classList.toggle("selected", item === row));
    const component = components.find(item => item.id === row.dataset.outlineComponent);
    $("#outline-inspector").innerHTML = `<p class="eyebrow">COMPONENT INSPECTOR</p><h2>${escapeHTML(component.name)}</h2><div class="identity-box"><span>${escapeHTML(component.provider)}</span><span>${escapeHTML(component.role)}</span><span>${escapeHTML(labelAvailability(component.availability))}</span></div><p>${escapeHTML(component.description)}</p><p><strong>${component.presets.length} named presets</strong></p>`;
  });

  $("#power-button").addEventListener("click", () => {
    if (!state.wallPower) { setWallPower(true); toast("Wall power is on. Output remains stopped until started."); return; }
    configureSheet({ title: "Turn Wall Power Off?", description: "This will immediately black out the physical installation and stop the running output.", icon: "⏻", confirm: "Power Off", rows: [["Wall", "Physical 32 × 138 installation"], ["Will change", "Power off and stop output"], ["Brightness", "Stored at the current value"]], action: () => { setWallPower(false); $("#live-name").textContent = "Output stopped"; $("#live-save-state").textContent = "Stopped"; toast("Physical wall power is off."); } });
  });
  $("#stop-button").addEventListener("click", () => { $("#live-name").textContent = "Output stopped"; $("#live-provider").textContent = "Power remains on · ready"; $("#live-save-state").textContent = "Stopped"; toast("Output stopped. Wall power remains on."); });
  $("#live-brightness").addEventListener("input", event => { $("#brightness-output").textContent = event.target.value; $("#live-save-state").textContent = "Adjusted"; });
  $("#live-brightness").addEventListener("change", event => toast(`Hardware brightness set to ${event.target.value}.`));

  $("#action-sheet").addEventListener("close", event => { if (event.currentTarget.returnValue === "confirm" && state.pendingAction) state.pendingAction(); state.pendingAction = null; });

  $(".outline-tree").addEventListener("click", event => {
    const row = event.target.closest("[data-scene-node]");
    if (!row) return;
    $$(".tree-row").forEach(item => { item.classList.toggle("selected", item === row); item.setAttribute("aria-selected", String(item === row)); });
    setSceneInspector(row.dataset.sceneNode);
  });
  $("#validate-scene").addEventListener("click", () => toast("Scene valid: fallback and overlay lease confirmed."));
  $("#save-scene").addEventListener("click", () => configureSheet({ title: "Save Scene Layout?", description: "Save the component layout and overrides as a named scene preset.", icon: "▤", confirm: "Save Layout", rows: [["Will save", "Background, overlay, overrides and stale policy"], ["Will not save", "Vibe, plant material, power or brightness"], ["Existing preset", "Evening with Clock (will create a new revision)"]], action: () => toast("Scene layout saved. Global settings were not captured.") }));
  $("#perform-scene").addEventListener("click", () => configureSheet({ title: "Perform “Evening with Clock”?", description: "The validated scene will replace the current physical wall output.", icon: "▤", confirm: "Perform Scene", rows: [["Background", "Cozy Ember Canopy — Hearthside"], ["Overlay", "Horizon Numerals — Soft Graphite"], ["Global settings", "Remain independent and unchanged"]], action: () => { $("#live-name").textContent = "Scene: Evening with Clock"; $("#live-provider").textContent = "host scene / background + clock_overlay"; $("#live-save-state").textContent = "Saved"; toast("Scene is now performing on the physical wall."); } }));

  $$(".vibe-segments button").forEach(button => button.addEventListener("click", () => {
    state.vibe = button.dataset.vibe;
    $$(".vibe-segments button").forEach(item => item.setAttribute("aria-checked", String(item === button)));
    $("#material-preview-title").textContent = `${state.vibe} + ${$("#field-modifier").value} + ${$("#surface-modifier").value}`;
    drawWall($("#material-preview"), `material-${state.vibe}`, { palette: state.vibe === "Vivid" || state.vibe === "Celebration" ? "Vivid" : "Moonlit Moss", density: 66 });
  }));
  $$(".modifier-row input[type=range], .popup-grid input[type=range]").forEach(input => input.addEventListener("input", () => { const output = input.parentElement.querySelector("output"); if (output) output.textContent = (input.value / 100).toFixed(2); }));
  [$("#field-modifier"), $("#surface-modifier")].forEach(select => select.addEventListener("change", () => { $("#material-preview-title").textContent = `${state.vibe} + ${$("#field-modifier").value} + ${$("#surface-modifier").value}`; }));
  $("#apply-globals").addEventListener("click", () => configureSheet({ title: "Apply Global Vibe & Plant Material?", description: "These independent global settings will update supported semantics on the physical wall.", icon: "◆", confirm: "Apply Globals", rows: [["Vibe", state.vibe], ["Field", `${$("#field-modifier").value} at 0.54`], ["Surface", `${$("#surface-modifier").value} at 0.83`], ["Presets / scenes", "Remain unchanged"]], action: () => toast("Global vibe and plant material applied independently.") }));

  $("#operations-outline").addEventListener("click", event => {
    const group = event.target.closest("[data-disclosure]");
    if (group) {
      const expanded = group.getAttribute("aria-expanded") !== "true";
      group.setAttribute("aria-expanded", String(expanded));
      group.querySelector(".disclosure").textContent = expanded ? "▼" : "▶";
      $(`[data-group="${group.dataset.disclosure}"]`).classList.toggle("collapsed", !expanded);
      return;
    }
    const row = event.target.closest("[data-receiver]");
    if (row) { $$(".receiver-row").forEach(item => item.classList.toggle("selected", item === row)); setReceiver(row.dataset.receiver); }
  });
  $("#refresh-health").addEventListener("click", () => toast("Status refreshed: all receivers agree."));
  $("#target-fps").addEventListener("change", event => toast(`Target frame rate staged at ${event.target.value} fps in this fixture.`));
  $("#operator-speed").addEventListener("input", event => { $("#operator-speed-output").textContent = `${(event.target.value / 100).toFixed(2)}×`; });
  $("#operator-speed").addEventListener("change", event => toast(`Operator speed staged at ${(event.target.value / 100).toFixed(2)}× in this fixture.`));

  $("#mobile-back").addEventListener("click", () => {
    if (state.workspace !== "library") { showWorkspace("library"); setMobileLevel("sources"); return; }
    const previous = { detail: "presets", presets: "components", components: "categories", categories: "sources" }[state.mobileLevel] || "sources";
    if (state.view !== "columns") setView("columns");
    setMobileLevel(previous);
  });
  $("#path-parts").addEventListener("click", event => { const button = event.target.closest("[data-path-level]"); if (!button || !isMobile()) return; setMobileLevel(({ source: "categories", category: "components", component: "presets" })[button.dataset.pathLevel]); });

  window.addEventListener("resize", () => { if (isMobile() && !app.dataset.mobileLevel) setMobileLevel("sources"); });

  app.dataset.mobileLevel = isMobile() ? "sources" : "sources";
  renderLibrary();
  renderCompare();
  renderOutline();
  drawWall($("#scene-preview"), "scene-evening-clock", { palette: "Moonlit Moss", density: 69 });
  drawWall($("#material-preview"), "material-Cozy", { palette: "Moonlit Moss", density: 66 });
})();
