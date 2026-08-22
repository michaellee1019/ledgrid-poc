(function () {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  const state = {
    route: "trailhead",
    goal: "calm",
    activeMoment: 0,
    liveName: "Deep Current · North Star",
    liveBase: "Deep Current",
    savedName: "Evening settle",
    liveRunning: true,
    powered: true,
    drift: false,
    draftDirty: false,
    vibe: "quiet",
    brightness: 58,
    fps: 60,
    speed: 1,
    guideFilter: "all",
    readiness: "all",
    query: "",
    compare: [],
    takeLiveSource: "journey",
    backgroundIndex: 0,
    gesture: { x: 16, y: 69, review: false }
  };

  const journeyProfiles = {
    calm: {
      eyebrow: "A GUIDED JOURNEY",
      title: "Calm the room",
      intro: "A gradual descent from restless to settled—no content hunting required.",
      moments: [
        { name: "Exhale", time: "0–12 min", marker: "NOW", desc: "Breathing Moss settles the room slowly.", scene: "breathing-moss", thumb: "soft-teal", tags: ["background", "no clock"], recipe: "Breathing Moss · Tidepool preset" },
        { name: "Gather inward", time: "12–34 min", marker: "+12", desc: "Ember Moss warms as the room settles.", scene: "ember-moss", thumb: "amber-moss", tags: ["background", "Quiet vibe"], recipe: "Ember Moss · Low hearth preset" },
        { name: "Land softly", time: "34–45 min", marker: "+34", desc: "Night Canopy brings the clock back.", scene: "night-canopy", thumb: "night", tags: ["background + overlay", "fade transition"], recipe: "Night Canopy · North Star overlay" }
      ]
    },
    host: {
      eyebrow: "A GUEST JOURNEY",
      title: "Host guests",
      intro: "Give arrival, gathering and departure their own energy without tending the wall all evening.",
      moments: [
        { name: "Open the door", time: "0–10 min", marker: "NOW", desc: "Candle Rain warms the room without demanding attention.", scene: "candle-rain", thumb: "amber-moss", tags: ["background", "Cozy vibe"], recipe: "Candle Rain · Porchlight preset" },
        { name: "Gather", time: "10–38 min", marker: "+10", desc: "Party Sparks slowly becomes more social.", scene: "party-sparks", thumb: "night", tags: ["background", "Celebration later"], recipe: "Party Sparks · Easy company preset" },
        { name: "Afterglow", time: "38–45 min", marker: "+38", desc: "Ember Moss lands the room and restores time.", scene: "ember-moss", thumb: "soft-teal", tags: ["background + overlay", "until stopped"], recipe: "Ember Moss · North Star overlay" }
      ]
    },
    play: {
      eyebrow: "A PLAY SESSION",
      title: "Play together",
      intro: "Warm up, invite participation, then return the room to calm when the session ends.",
      moments: [
        { name: "Warm up", time: "0–5 min", marker: "NOW", desc: "Portal Moths teaches point interactions gently.", scene: "portal-moths", thumb: "soft-teal", tags: ["interactive", "point"], recipe: "Portal Moths · Friendly swarm" },
        { name: "Play", time: "5–35 min", marker: "+5", desc: "Tetris Garden opens a shared D-pad session.", scene: "tetris-garden", thumb: "night", tags: ["full scene", "D-pad"], recipe: "Tetris Garden · Co-op climb" },
        { name: "Cool down", time: "35–45 min", marker: "+35", desc: "Fireflies absorbs the last gestures and fades.", scene: "fireflies", thumb: "amber-moss", tags: ["background", "slow fade"], recipe: "Fireflies · Trail memory" }
      ]
    },
    schedule: {
      eyebrow: "SCHEDULED ROOM ARC",
      title: "Author an evening arc",
      intro: "Write how the room changes over time. Each turn is rehearsed now and begins on its own later.",
      moments: [
        { name: "Warm welcome", time: "6:30–7:15", marker: "6:30", desc: "Candle Rain meets arriving guests.", scene: "candle-rain", thumb: "amber-moss", tags: ["scheduled", "Cozy vibe"], recipe: "Candle Rain · Porchlight preset" },
        { name: "Shared spark", time: "7:15–8:15", marker: "7:15", desc: "Constellation Field invites gentle gestures.", scene: "constellation", thumb: "soft-teal", tags: ["scheduled", "point interaction"], recipe: "Constellation Field · Guest trails" },
        { name: "Afterglow", time: "8:15–until stop", marker: "8:15", desc: "Night Canopy restores the clock and stays.", scene: "night-canopy", thumb: "night", tags: ["scheduled", "until stopped"], recipe: "Night Canopy · North Star overlay" }
      ]
    }
  };

  const componentNames = [
    "Deep Current", "Breathing Moss", "Ember Moss", "Night Canopy", "Aurora Drip", "Constellation Field",
    "Candle Rain", "North Star Clock", "Word Clock", "Party Sparks", "Compiled Rainbow", "Coral Bloom",
    "Kelp Dream", "Fireflies", "Digital Rain", "Weather Loom", "Tetris Garden", "Snake Trail", "Pong Climb",
    "Memory Bloom", "Pixel Cinema", "Emoji Parade", "Canvas Painter", "Starfall", "Cloud Chamber", "Lava Ladder",
    "Wave Tank", "Sand Story", "Orbital Choir", "Portal Moths", "Habitat Swarm", "Globe Bumpers", "Clock Overlay",
    "Moon Dial", "Binary Tower", "Sunset Meter", "Ambient Bands", "Quiet Noise", "Light Quilt", "Lichen Pulse",
    "Ocean Columns", "Confetti Column", "Guestbook Glow", "Flow Field", "Lane Diagnostic", "Strip Compass",
    "Native Diagonal", "Plant Mask Preview", "Frame Timing", "Calibration Crosshair", "Firmware Rainbow", "Recovery Lantern"
  ];
  const presetSuffixes = ["Still", "Tidepool", "Canopy", "Long dusk", "Soft focus", "Night loop"];
  const goalCycle = ["calm", "host", "play", "readable", "calm", "host", "care"];
  const palettePairs = [
    ["#082921", "#3d9672", "#d5d285"], ["#11162d", "#6f75aa", "#84c8b0"], ["#2c1610", "#c46f3e", "#ded07d"],
    ["#071f25", "#268a91", "#87d9b2"], ["#191329", "#995e88", "#e1a86a"], ["#15270f", "#6f9b47", "#b7d990"]
  ];

  const components = componentNames.map((name, index) => {
    let role = "background";
    if ([7, 32].includes(index)) role = "overlay";
    if ([8, 16, 17, 18, 19, 20, 21, 22, 44, 45, 47, 48, 49].includes(index)) role = "full scene";
    const provider = [10, 46, 50, 51].includes(index) ? "receiver native" : "host Python";
    return { name, role, provider, index };
  });

  const possibilities = [];
  components.forEach((component, componentIndex) => {
    const count = componentIndex < 32 ? 6 : 5;
    for (let presetIndex = 0; presetIndex < count; presetIndex += 1) {
      const id = possibilities.length;
      let readiness = "Ready";
      if (id === 151 || id === 274) readiness = "Quarantined";
      else if (id > 0 && id % 67 === 0) readiness = "Unavailable";
      else if (component.provider === "receiver native" && component.name !== "Compiled Rainbow") readiness = "Build required";
      const goal = componentIndex >= 44 ? "care" : goalCycle[(componentIndex + presetIndex) % goalCycle.length];
      const extra = component.role === "overlay" || component.name.includes("Clock") || component.name.includes("Dial") ? "readable" : goal;
      possibilities.push({
        id,
        component: component.name,
        name: `${component.name} · ${presetSuffixes[presetIndex]}`,
        preset: presetSuffixes[presetIndex],
        provider: component.provider,
        role: component.role,
        readiness,
        goal,
        extra,
        palette: palettePairs[(componentIndex + presetIndex) % palettePairs.length],
        description: descriptionFor(goal, component.role, component.provider)
      });
    }
  });

  function descriptionFor(goal, role, provider) {
    if (goal === "care") return "For checking geometry, presentation, or receiver behavior.";
    if (role === "overlay") return "Keeps essential time legible without taking over the room.";
    if (goal === "play") return "Responds to people and rewards shared attention.";
    if (goal === "host") return "Social energy that stays behind the conversation.";
    if (provider === "receiver native") return "Runs locally on receivers after a trusted build.";
    return "A long-running atmosphere designed to soften the room.";
  }

  function routeTo(route) {
    state.route = route;
    $$(".view").forEach(view => view.classList.toggle("active", view.dataset.view === route));
    $$("[data-route]").forEach(button => {
      if (button.classList.contains("nav-item") || button.closest(".mobile-nav")) {
        button.classList.toggle("active", button.dataset.route === route);
      }
    });
    window.scrollTo({ top: 0, behavior: "smooth" });
    if (route === "field-guide" && !$(".possibility-card")) renderGuide();
    requestAnimationFrame(drawAllWalls);
  }

  function openLayer(id) {
    const layer = document.getElementById(id);
    if (!layer) return;
    layer.classList.add("open");
    layer.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    const focusable = $("button:not([disabled]), input:not([disabled]), select", layer);
    if (focusable) setTimeout(() => focusable.focus(), 80);
    drawAllWalls();
  }

  function closeLayer(id) {
    const layer = document.getElementById(id);
    if (!layer) return;
    layer.classList.remove("open");
    layer.setAttribute("aria-hidden", "true");
    if (!$(".modal.open, .live-drawer.open")) document.body.style.overflow = "";
  }

  function toast(title, detail, icon = "✓") {
    const element = document.createElement("div");
    element.className = "toast";
    element.innerHTML = `<span>${icon}</span><div><strong>${title}</strong><small>${detail}</small></div>`;
    $("#toastRegion").appendChild(element);
    setTimeout(() => element.remove(), 4200);
  }

  function markDraft(label = "Draft changed") {
    state.draftDirty = true;
    $("#draftLabel").textContent = label;
    $("#draftSub").textContent = "Changes are rehearsed; wall untouched";
    $("#draftLabel").closest(".draft-state").classList.add("dirty");
    $("#studioDraftLabel").textContent = "Draft changed · wall untouched";
    $("#studioDraftLabel").closest(".draft-state").classList.add("dirty");
  }

  function updateLiveUI() {
    $("#liveSceneName").textContent = state.liveRunning ? state.liveName : "Scene stopped · wall idle";
    $("#drawerSceneName").textContent = state.liveRunning ? state.liveBase : "No active scene";
    $("#drawerSceneName").nextElementSibling.textContent = state.liveRunning ? `${titleCase(state.vibe)} vibe · ${state.brightness}% brightness` : "Presentation is idle";
    $("#confirmCurrentName").textContent = state.liveName;
    $("#syncBadge").textContent = state.drift ? "Differs from saved" : "Matches saved";
    $("#syncBadge").classList.toggle("synced", !state.drift);
    $("#syncBadge").classList.toggle("drift", state.drift);
    $("#drawerSyncLine").textContent = state.drift ? `Saved layout “${state.savedName}” differs from live` : `Saved as “${state.savedName}” · no drift`;
    $("#liveStopButton").textContent = state.liveRunning ? "■ Stop scene" : "▶ Resume scene";
    $("#powerButton").classList.toggle("off", !state.powered);
    $("#powerButton").classList.toggle("on", state.powered);
    $("#powerButton b").textContent = state.powered ? "On" : "Off";
  }

  function titleCase(value) { return value.charAt(0).toUpperCase() + value.slice(1); }

  function setJourneyGoal(goal) {
    if (goal === "diagnose") { routeTo("care"); return; }
    if (goal === "paint") { openInteraction("paint"); return; }
    const resolved = journeyProfiles[goal] ? goal : "calm";
    state.goal = resolved;
    state.activeMoment = 0;
    const profile = journeyProfiles[resolved];
    $("#journeyEyebrow").textContent = profile.eyebrow;
    $("#journeyTitle").textContent = profile.title;
    $("#journeyIntro").textContent = profile.intro;
    $$(".moment-card").forEach((card, index) => {
      const moment = profile.moments[index];
      card.dataset.scene = moment.scene;
      card.dataset.moment = moment.name.toLowerCase().replace(/[^a-z]+/g, "-");
      $(".moment-node b", card).textContent = index + 1;
      $(".moment-node span", card).textContent = moment.marker;
      $(".moment-thumb", card).className = `moment-thumb ${moment.thumb}`;
      $(".moment-time", card).textContent = moment.time;
      $("h3", card).textContent = moment.name;
      $("p", card).textContent = moment.desc;
      $(".moment-copy div", card).innerHTML = moment.tags.map(tag => `<span>${tag}</span>`).join("");
      card.classList.toggle("active", index === 0);
    });
    updateRehearsal(0);
    if (resolved === "schedule") $("#journeyStart").value = "At 6:30 PM";
    else $("#journeyStart").value = "Now";
    routeTo("journey");
  }

  function updateRehearsal(index) {
    const profile = journeyProfiles[state.goal];
    const moment = profile.moments[index];
    state.activeMoment = index;
    $$(".moment-card").forEach((card, i) => card.classList.toggle("active", i === index));
    $("#rehearsalTitle").textContent = moment.name;
    $("#recipeName").textContent = moment.recipe;
    $("#journeyCanvas").dataset.scene = moment.scene;
    $("#journeyClock").classList.toggle("hidden", !(moment.scene.includes("night") || state.goal === "schedule" && index === 2));
    drawAllWalls();
  }

  function renderGuide() {
    const q = state.query.toLowerCase().trim();
    const filtered = possibilities.filter(item => {
      const goalMatch = state.guideFilter === "all" || item.goal === state.guideFilter || item.extra === state.guideFilter;
      const readinessMatch = state.readiness === "all" || item.readiness === "Ready";
      const text = `${item.name} ${item.component} ${item.provider} ${item.role} ${item.goal} ${item.extra} ${item.readiness}`.toLowerCase();
      return goalMatch && readinessMatch && (!q || text.includes(q));
    });
    $("#guideCount").textContent = filtered.length;
    $("#guideMeta").textContent = filtered.length === 292
      ? "Showing the full field guide · ready, build-only, unavailable and quarantined items are labeled"
      : `${filtered.length} matches · availability and provider identities remain explicit`;
    $("#guideEndCopy").textContent = filtered.length === 292
      ? "All 292 possibilities are present. There is no hidden “load more.”"
      : `All ${filtered.length} matching possibilities are shown. No results are truncated.`;
    const html = filtered.map(item => {
      const colors = item.palette;
      const readinessClass = item.readiness.toLowerCase().replace(" required", "");
      const disabled = item.readiness === "Unavailable" || item.readiness === "Quarantined";
      return `<article class="possibility-card${state.compare.includes(item.id) ? " selected" : ""}${disabled ? " disabled-card" : ""}" data-id="${item.id}">
        <div class="possibility-thumb" style="--thumb-bg:linear-gradient(165deg,${colors[0]},${colors[1]} 60%,${colors[0]});--thumb-art:radial-gradient(circle at 55% 18%,${colors[2]} 0 3px,transparent 4px)"></div>
        <div class="possibility-copy"><span class="readiness ${readinessClass}">${item.readiness}</span><h3 title="${item.name}">${item.name}</h3><p title="${item.description}">${item.description}</p>
          <div class="possibility-tags"><span>${item.provider}</span><span>${item.role}</span><span>${item.goal}</span></div>
          <div class="possibility-actions"><button data-action="rehearse">Rehearse tall</button><button data-action="compare" class="${state.compare.includes(item.id) ? "added" : ""}">${state.compare.includes(item.id) ? "Added ✓" : "+ Compare"}</button></div>
        </div></article>`;
    }).join("");
    $("#possibilityGrid").innerHTML = html || `<div class="compare-empty"><div><strong>No path found</strong><p>Try a different purpose or phrase. The source field guide still contains all 292 possibilities.</p></div></div>`;
    updateCompareButton();
  }

  function updateCompareButton() {
    $("#compareCount").textContent = state.compare.length;
    $("#openCompareButton").disabled = state.compare.length === 0;
  }

  function toggleCompare(id) {
    const existing = state.compare.indexOf(id);
    if (existing >= 0) state.compare.splice(existing, 1);
    else if (state.compare.length < 3) state.compare.push(id);
    else { toast("Comparison deck is full", "Remove one possibility before adding another.", "3"); return; }
    renderGuide();
  }

  function openCompare(ids = state.compare) {
    if (!ids.length) return;
    const slots = ids.slice(0, 3).map(id => possibilityCompareMarkup(possibilities[id])).join("");
    const empty = Array.from({ length: Math.max(0, 3 - ids.length) }, () => `<div class="compare-empty"><div><strong>Add another possibility</strong><p>The deck holds up to three real-shape rehearsals.</p></div></div>`).join("");
    $("#compareGrid").innerHTML = slots + empty;
    openLayer("compareModal");
  }

  function possibilityCompareMarkup(item) {
    const statusClass = item.readiness.toLowerCase().replace(" required", "");
    const liveAllowed = item.readiness === "Ready";
    return `<article class="compare-slot" data-id="${item.id}"><div class="compare-wall"><canvas class="wall-canvas" width="32" height="138" data-scene="guide-${item.id}"></canvas></div><div class="compare-details"><span class="readiness ${statusClass}">${item.readiness}</span><h3>${item.name}</h3><p>${item.description}</p><dl><dt>Runs on</dt><dd>${item.provider}</dd><dt>Role</dt><dd>${item.role}</dd><dt>Plant response</dt><dd>${item.goal === "care" ? "Geometry diagnostic" : "Shadow, obstacle supported"}</dd><dt>Preview truth</dt><dd>${item.provider === "receiver native" ? "Host simulation · no framebuffer readback" : "Isolated host render"}</dd></dl><button class="primary-button" data-compare-live="${item.id}" ${liveAllowed ? "" : "disabled"}>${liveAllowed ? "Review & take live" : item.readiness}</button></div></article>`;
  }

  function prepareTakeLive(source, itemId) {
    state.takeLiveSource = source;
    // The confirmation must become the sole foreground layer; otherwise the
    // comparison deck could visually obscure the physical-change review.
    closeLayer("compareModal");
    let name;
    let meta;
    let scene = "breathing-moss";
    if (source === "guide") {
      const item = possibilities[itemId];
      name = item.name;
      meta = `${item.provider} · ${item.role} · ready`;
      scene = `guide-${item.id}`;
    } else if (source === "scene") {
      const background = ["Breathing Moss", "Compiled Rainbow", "Ember Moss"][state.backgroundIndex];
      name = `${background}${$("#overlayEnabled").checked ? " + North Star" : ""}`;
      meta = "One scene · begins now · until stopped";
      scene = ["breathing-moss", "compiled-rainbow", "ember-moss"][state.backgroundIndex];
    } else {
      const profile = journeyProfiles[state.goal];
      name = profile.title;
      meta = `${profile.moments.length} moments · ${$("#journeyStart").value.toLowerCase()} · 45 min`;
      scene = profile.moments[0].scene;
    }
    $("#confirmProposedName").textContent = name;
    $("#confirmProposedMeta").textContent = meta;
    $("#confirmCanvas").dataset.scene = scene;
    $("#impactTiming").textContent = source === "journey" ? "Runs as a timed journey" : "Starts now and runs until stopped";
    $("#confirmAcknowledge").checked = false;
    $("#holdLiveButton").disabled = true;
    $("#holdLiveButton").classList.remove("holding");
    openLayer("takeLiveModal");
  }

  let holdTimer = null;
  function beginHold(event) {
    if ($("#holdLiveButton").disabled) return;
    event.preventDefault();
    $("#holdLiveButton").classList.add("holding");
    holdTimer = window.setTimeout(completeTakeLive, 1000);
  }
  function cancelHold() {
    if (holdTimer) window.clearTimeout(holdTimer);
    holdTimer = null;
    $("#holdLiveButton").classList.remove("holding");
  }
  function completeTakeLive() {
    const proposed = $("#confirmProposedName").textContent;
    state.liveName = proposed;
    state.liveBase = proposed.split(" + ")[0].split(" · ")[0];
    state.liveRunning = true;
    state.powered = true;
    state.drift = proposed !== state.savedName;
    state.draftDirty = false;
    updateLiveUI();
    closeLayer("takeLiveModal");
    closeLayer("compareModal");
    toast("The wall is now live", `${proposed} replaced the previous scene. Saved state now differs.`, "●");
    cancelHold();
  }

  function updateStudioPreview() {
    const backgrounds = [
      { name: "Breathing Moss", meta: "Tidepool · Python host", scene: "breathing-moss", provider: "Host-rendered background" },
      { name: "Compiled Rainbow", meta: "Continuous · receiver native", scene: "compiled-rainbow", provider: "Host simulation · not receiver framebuffer readback" },
      { name: "Ember Moss", meta: "Low hearth · Python host", scene: "ember-moss", provider: "Host-rendered background" }
    ];
    const background = backgrounds[state.backgroundIndex];
    $("#backgroundName").textContent = background.name;
    $("#backgroundMeta").textContent = background.meta;
    $("#studioCanvas").dataset.scene = background.scene;
    $("#providerReadout").textContent = background.provider;
    $("#studioClock").style.display = $("#overlayEnabled").checked ? "block" : "none";
    $("#overlayControls").style.opacity = $("#overlayEnabled").checked ? "1" : ".45";
    $("#studioClock").style.opacity = String(Number($("#opacityRange").value) / 100);
    $("#validationResult").classList.remove("valid");
    $("#validationResult strong").textContent = "Draft changed · validate again";
    $("#validationResult p").textContent = "The physical wall remains on its current scene.";
    markDraft();
    drawAllWalls();
  }

  function openInteraction(tool = "gesture") {
    $$(".tool-tabs button").forEach(button => button.classList.toggle("active", button.dataset.tool === tool));
    $$("[data-tool-panel]").forEach(panel => panel.classList.toggle("active", panel.dataset.toolPanel === tool));
    openLayer("interactionModal");
  }

  function moveGesture(direction) {
    const step = direction === "center" ? 0 : 3;
    if (direction === "up") state.gesture.y = Math.max(0, state.gesture.y - step);
    if (direction === "down") state.gesture.y = Math.min(137, state.gesture.y + step);
    if (direction === "left") state.gesture.x = Math.max(0, state.gesture.x - step);
    if (direction === "right") state.gesture.x = Math.min(31, state.gesture.x + step);
    if (direction === "center") { state.gesture.x = 16; state.gesture.y = 69; }
    state.gesture.review = false;
    updateGestureUI();
  }

  function updateGestureUI() {
    const point = $("#gesturePoint");
    point.style.left = `${(state.gesture.x / 31) * 100}%`;
    point.style.top = `${(state.gesture.y / 137) * 100}%`;
    $("#gestureCoords").textContent = `x ${state.gesture.x} · y ${state.gesture.y} · preview only`;
    $("#sendGestureButton").textContent = state.gesture.review
      ? `Confirm: send ${$("#gestureKind").value.toLowerCase()} at x${state.gesture.x}, y${state.gesture.y}`
      : "Review one live gesture";
  }

  function drawAllWalls(now = performance.now()) {
    $$("canvas.wall-canvas").forEach(canvas => {
      if (canvas.offsetParent !== null) drawWall(canvas, now / 1000);
    });
  }

  function hashString(value) {
    let hash = 2166136261;
    for (let i = 0; i < value.length; i += 1) { hash ^= value.charCodeAt(i); hash = Math.imul(hash, 16777619); }
    return Math.abs(hash >>> 0);
  }

  function drawWall(canvas, time) {
    const ctx = canvas.getContext("2d", { alpha: false });
    const scene = canvas.dataset.scene || "deep-current";
    const hash = hashString(scene);
    const image = ctx.createImageData(32, 138);
    const data = image.data;
    const calm = scene.includes("moss") || scene.includes("current") || scene.includes("night");
    const bright = scene.includes("party") || scene.includes("rainbow") || scene.includes("guide");
    for (let y = 0; y < 138; y += 1) {
      for (let x = 0; x < 32; x += 1) {
        const i = (y * 32 + x) * 4;
        const wave = Math.sin(y * .13 + time * (calm ? .42 : 1.05) + Math.sin(x * .31)) * .5 + .5;
        const vein = Math.sin(x * .52 - y * .055 + time * .7 + (hash % 17)) * .5 + .5;
        const spark = Math.sin(x * 2.41 + y * .91 + time * 2.2 + hash) > .968 ? 1 : 0;
        const hue = hash % 6;
        let r = 5 + wave * (hue === 2 || hue === 4 ? 48 : 14);
        let g = 15 + wave * (hue === 2 ? 42 : 74) + vein * 20;
        let b = 18 + vein * (hue === 1 || hue === 3 ? 76 : 42);
        if (scene.includes("ember") || scene.includes("candle")) { r += wave * 105; g += wave * 30; b *= .45; }
        if (scene.includes("night")) { r *= .5; g *= .55; b += wave * 45; }
        if (scene.includes("rainbow") || scene.includes("party")) {
          r = 35 + 90 * (Math.sin(x * .22 + time) * .5 + .5);
          g = 30 + 95 * (Math.sin(y * .08 + time + 2.1) * .5 + .5);
          b = 35 + 100 * (Math.sin((x + y) * .07 + time + 4.2) * .5 + .5);
        }
        if (bright || spark) { r += spark * 130; g += spark * 120; b += spark * 85; }
        const edgeShade = x < 4 || x > 27 ? .58 : 1;
        data[i] = Math.min(255, r * edgeShade);
        data[i + 1] = Math.min(255, g * edgeShade);
        data[i + 2] = Math.min(255, b * edgeShade);
        data[i + 3] = 255;
      }
    }
    ctx.putImageData(image, 0, 0);
  }

  let lastFrame = 0;
  function animateWalls(timestamp) {
    if (timestamp - lastFrame > 90) { drawAllWalls(timestamp); lastFrame = timestamp; }
    requestAnimationFrame(animateWalls);
  }

  // One event router keeps the static prototype dependency-free and easy to audit.
  document.addEventListener("click", event => {
    const routeButton = event.target.closest("[data-route]");
    if (routeButton) {
      event.preventDefault();
      if (routeButton.dataset.goal) setJourneyGoal(routeButton.dataset.goal);
      else routeTo(routeButton.dataset.route);
      closeLayer("liveDrawer");
      return;
    }
    const goalButton = event.target.closest("[data-goal]");
    if (goalButton) { setJourneyGoal(goalButton.dataset.goal); return; }
    const closeButton = event.target.closest("[data-close]");
    if (closeButton) { closeLayer(closeButton.dataset.close); return; }
    const atmosphere = event.target.closest(".atmosphere-trigger");
    if (atmosphere) { openLayer("atmosphereModal"); return; }
    const takeLive = event.target.closest(".take-live-trigger");
    if (takeLive) { prepareTakeLive(takeLive.dataset.source); return; }
    const momentCard = event.target.closest(".moment-card");
    if (momentCard) { updateRehearsal($$(".moment-card").indexOf(momentCard)); return; }
    const possibilityCard = event.target.closest(".possibility-card");
    if (possibilityCard) {
      const action = event.target.closest("[data-action]");
      if (!action) return;
      const id = Number(possibilityCard.dataset.id);
      if (action.dataset.action === "compare") toggleCompare(id);
      if (action.dataset.action === "rehearse") openCompare([id]);
      return;
    }
    const compareLive = event.target.closest("[data-compare-live]");
    if (compareLive) { prepareTakeLive("guide", Number(compareLive.dataset.compareLive)); return; }
    const filter = event.target.closest("[data-filter]");
    if (filter) {
      state.guideFilter = filter.dataset.filter;
      $$("[data-filter]").forEach(button => button.classList.toggle("active", button === filter));
      renderGuide();
      return;
    }
    const vibeButton = event.target.closest("[data-vibe]");
    if (vibeButton) {
      state.vibe = vibeButton.dataset.vibe;
      $$("[data-vibe]").forEach(button => button.classList.toggle("active", button === vibeButton));
      $("#atmosphereSummary").textContent = `${titleCase(state.vibe)} · shadow 60% · obstacle 100%`;
      return;
    }
    const modifierButton = event.target.closest("[data-modifier]");
    if (modifierButton) {
      $$(`[data-modifier]`, modifierButton.closest(".modifier-family")).forEach(button => button.classList.toggle("active", button === modifierButton));
      return;
    }
    const toolTab = event.target.closest("[data-tool]");
    if (toolTab) { openInteraction(toolTab.dataset.tool); return; }
    const gestureDirection = event.target.closest("#gesturePad [data-direction]");
    if (gestureDirection) { moveGesture(gestureDirection.dataset.direction); return; }
    const placement = event.target.closest("#placementPad [data-direction]");
    if (placement) {
      const labels = { up: "Centered · y −15", down: "Centered · y −9", left: "x −3 · y −12", right: "x +3 · y −12", center: "Centered · y −12" };
      $("#placementValue").textContent = labels[placement.dataset.direction];
      markDraft();
    }
  });

  $("#liveRibbon").addEventListener("click", () => openLayer("liveDrawer"));
  $("#tuningTrigger").addEventListener("click", () => openLayer("tuningModal"));
  $("#touchToolsTrigger").addEventListener("click", () => openInteraction("gesture"));
  $("#maskEditorTrigger").addEventListener("click", () => openInteraction("masks"));
  $("#openCompareButton").addEventListener("click", () => openCompare());
  $("#availabilityFilter").addEventListener("click", event => {
    state.readiness = state.readiness === "all" ? "ready" : "all";
    event.currentTarget.textContent = state.readiness === "all" ? "Any readiness ▾" : "Ready only ×";
    event.currentTarget.classList.toggle("active", state.readiness !== "all");
    renderGuide();
  });
  $("#guideSearch").addEventListener("input", event => { state.query = event.target.value; renderGuide(); });
  $("#journeyScrubber").addEventListener("input", event => {
    const minute = Number(event.target.value);
    $("#previewTime").textContent = `${String(minute).padStart(2, "0")}:00`;
    updateRehearsal(minute < 12 ? 0 : minute < 34 ? 1 : 2);
  });
  $$(".duration-control button").forEach(button => button.addEventListener("click", () => {
    $$(".duration-control button").forEach(item => item.classList.toggle("active", item === button));
    markDraft("Journey length changed");
  }));
  $("#journeyStart").addEventListener("change", () => markDraft("Start time changed"));
  $("#addMomentButton").addEventListener("click", () => toast("Turn added to the draft", "Choose a purpose for the new turn in the next editor step.", "+"));
  $("#saveJourneyButton").addEventListener("click", () => {
    state.savedName = journeyProfiles[state.goal].title;
    state.draftDirty = false;
    $("#draftLabel").textContent = "Journey saved · not live";
    $("#draftSub").textContent = "The physical wall is still untouched";
    $("#draftLabel").closest(".draft-state").classList.remove("dirty");
    toast("Journey saved only", "It is available for later; the live wall did not change.");
  });

  $("#backgroundSelector").addEventListener("click", () => { state.backgroundIndex = (state.backgroundIndex + 1) % 3; updateStudioPreview(); });
  $("#overlayEnabled").addEventListener("change", updateStudioPreview);
  $("#opacityRange").addEventListener("input", event => { $("#opacityValue").textContent = `${event.target.value}%`; updateStudioPreview(); });
  $("#stalePolicy").addEventListener("change", updateStudioPreview);
  $("#advancedDisclosure").addEventListener("click", () => $("#advancedControls").classList.toggle("open"));
  $("#sceneSpeed").addEventListener("input", event => { $("#sceneSpeedValue").textContent = `${(event.target.value / 100).toFixed(2)}×`; updateStudioPreview(); });
  $("#validateSceneButton").addEventListener("click", () => {
    const validation = $("#validationResult");
    validation.classList.add("valid");
    $("span", validation).textContent = "✓";
    $("strong", validation).textContent = "Valid · safe to apply";
    $("p", validation).textContent = state.backgroundIndex === 1 ? "Native foundation is bound; Python fallback and clock stale policy are valid." : "Roles, provider, overlay placement, fallback and stale policy pass.";
    toast("Scene contract is valid", "Validation did not start or change live playback.");
  });
  $("#saveLayoutButton").addEventListener("click", () => {
    state.savedName = `Studio layout · ${$("#backgroundName").textContent}`;
    state.draftDirty = false;
    $("#studioDraftLabel").textContent = "Layout saved · not applied";
    $("#studioDraftLabel").closest(".draft-state").classList.remove("dirty");
    state.drift = state.liveName !== state.savedName;
    updateLiveUI();
    toast("Layout saved only", "The scene is reusable. The physical wall did not change.");
  });

  $("#confirmAcknowledge").addEventListener("change", event => { $("#holdLiveButton").disabled = !event.target.checked; });
  $("#holdLiveButton").addEventListener("pointerdown", beginHold, { passive: false });
  ["pointerup", "pointerleave", "pointercancel"].forEach(type => $("#holdLiveButton").addEventListener(type, cancelHold));

  $("#brightnessRange").addEventListener("input", event => {
    state.brightness = Number(event.target.value);
    $("#brightnessValue").textContent = `${state.brightness}%`;
    $("#tuningBrightness").value = state.brightness;
    $("#tuningBrightnessValue").textContent = `${state.brightness}%`;
  });
  $("#liveStopButton").addEventListener("click", () => {
    state.liveRunning = !state.liveRunning;
    updateLiveUI();
    toast(state.liveRunning ? "Scene resumed" : "Scene stopped", state.liveRunning ? state.liveName : "The wall is idle; saved content remains available.", state.liveRunning ? "▶" : "■");
  });
  $("#powerButton").addEventListener("click", () => {
    state.powered = !state.powered;
    if (!state.powered) state.liveRunning = false;
    updateLiveUI();
    toast(state.powered ? "Wall power on" : "Wall power off", state.powered ? "Presentation is ready to resume." : "Output is dark; saved state was retained.", "◉");
  });

  $("#applyAtmosphereButton").addEventListener("click", () => {
    state.drift = true;
    updateLiveUI();
    closeLayer("atmosphereModal");
    toast("Global atmosphere applied", `${titleCase(state.vibe)} vibe and plant material behavior changed independently of the scene.`, "◐");
  });

  function bindRange(id, outputId, transform) {
    $(id).addEventListener("input", event => $(outputId).textContent = transform(Number(event.target.value)));
  }
  bindRange("#tuningBrightness", "#tuningBrightnessValue", value => `${value}%`);
  bindRange("#tuningFps", "#tuningFpsValue", value => String(value));
  bindRange("#tuningSpeed", "#tuningSpeedValue", value => `${(value / 100).toFixed(2)}×`);
  $("#applyTuningButton").addEventListener("click", () => {
    state.brightness = Number($("#tuningBrightness").value);
    state.fps = Number($("#tuningFps").value);
    state.speed = Number($("#tuningSpeed").value) / 100;
    $("#brightnessRange").value = state.brightness;
    $("#brightnessValue").textContent = `${state.brightness}%`;
    $("#fpsSummary").textContent = `${state.fps} FPS`;
    $("#speedSummary").textContent = `${state.speed.toFixed(2)}×`;
    closeLayer("tuningModal");
    toast("Global output tuning applied", `${state.brightness}% · ${state.fps} FPS · ${state.speed.toFixed(2)}× speed`, "↯");
  });

  $("#gestureWall").addEventListener("click", event => {
    const rect = event.currentTarget.getBoundingClientRect();
    state.gesture.x = Math.max(0, Math.min(31, Math.round(((event.clientX - rect.left) / rect.width) * 31)));
    state.gesture.y = Math.max(0, Math.min(137, Math.round(((event.clientY - rect.top) / rect.height) * 137)));
    state.gesture.review = false;
    updateGestureUI();
  });
  $("#gestureKind").addEventListener("change", () => { state.gesture.review = false; updateGestureUI(); });
  $("#sendGestureButton").addEventListener("click", () => {
    if (!state.gesture.review) {
      state.gesture.review = true;
      updateGestureUI();
      toast("Live gesture staged", "Check the coordinate and confirm once more. The wall is still untouched.", "?");
    } else {
      toast("Live gesture sent", `${$("#gestureKind").value} at x${state.gesture.x}, y${state.gesture.y} · prototype only`, "→");
      state.gesture.review = false;
      updateGestureUI();
    }
  });

  $("#refreshHealthButton").addEventListener("click", event => {
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = "↻ Checking…";
    setTimeout(() => { button.disabled = false; button.textContent = "↻ Refresh check"; toast("Wall check refreshed", "Presentation is healthy; expected one-way receiver semantics are unchanged."); }, 900);
  });

  document.addEventListener("keydown", event => {
    if (event.key === "Escape") $$(".modal.open, .live-drawer.open").forEach(layer => closeLayer(layer.id));
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); routeTo("field-guide"); setTimeout(() => $("#guideSearch").focus(), 50); }
  });

  updateLiveUI();
  updateGestureUI();
  renderGuide();
  requestAnimationFrame(animateWalls);
})();
