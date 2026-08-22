(() => {
  "use strict";

  const COMPONENTS = [
    ["ascii_drop", "ASCII Drop", 5, "focus", "lively", "python", "background"],
    ["aurora_curtains", "Aurora Curtains", 4, "settle", "gentle", "python", "background"],
    ["aurora_curtains_native", "Aurora Curtains (Native)", 2, "settle", "gentle", "receiver_native", "background", "build-only"],
    ["canopy_cup", "Canopy Cup", 12, "play", "lively", "python", "full_scene"],
    ["cellular_tapestry", "Cellular Tapestry", 3, "focus", "gentle", "python", "background"],
    ["christmas_tree", "Christmas Tree", 4, "gather", "gentle", "python", "full_scene"],
    ["circadian_window", "Circadian Window", 4, "know", "still", "python", "background"],
    ["clock", "Clock", 24, "know", "still", "python", "full_scene"],
    ["clock_overlay", "Clock Overlay", 0, "know", "still", "python", "overlay"],
    ["cloud_canyon", "Cloud Canyon", 4, "settle", "gentle", "python", "background"],
    ["conway_life", "Conway Life", 15, "focus", "lively", "python", "background"],
    ["cyclic_reef", "Cyclic Reef", 3, "settle", "gentle", "python", "background"],
    ["desert_wind", "Desert Wind", 4, "settle", "gentle", "python", "background"],
    ["emoji", "Emoji", 4, "make", "still", "python", "full_scene"],
    ["emoji_arranger", "Emoji Arranger", 0, "make", "still", "python", "full_scene"],
    ["firefly_synchrony", "Firefly Synchrony", 4, "settle", "gentle", "python", "background"],
    ["fireworks", "Fireworks", 5, "gather", "lively", "python", "full_scene"],
    ["flame_burst", "Flame Burst", 5, "gather", "lively", "python", "background"],
    ["flow_field_silk", "Flow Field Silk", 3, "settle", "gentle", "python", "background"],
    ["fluid_tank", "Fluid Tank", 6, "play", "gentle", "python", "full_scene"],
    ["frostwork", "Frostwork", 4, "focus", "still", "python", "background"],
    ["gif_animation", "GIF and Pixel Art", 32, "gather", "gentle", "python", "full_scene"],
    ["gradient", "Gradient", 5, "focus", "still", "python", "background"],
    ["lava_lamp", "Lava Lamp", 18, "settle", "gentle", "python", "background"],
    ["living_ecosystem", "Living Ecosystem", 9, "settle", "gentle", "python", "full_scene"],
    ["living_stained_glass", "Living Stained Glass", 8, "gather", "gentle", "python", "background"],
    ["maze_chase", "Maze Chase", 5, "play", "lively", "python", "full_scene"],
    ["moonlit_fog_banks", "Moonlit Fog Banks", 4, "settle", "gentle", "python", "background"],
    ["night_train_windows", "Night Train Windows", 4, "settle", "gentle", "python", "background"],
    ["physarum_network", "Physarum Network", 4, "focus", "gentle", "python", "background"],
    ["pinball", "Pinball", 5, "play", "lively", "python", "full_scene"],
    ["pixel_chase", "Pixel Chase", 0, "play", "lively", "python", "full_scene"],
    ["pixel_quest", "Pixel Quest", 4, "play", "lively", "python", "full_scene"],
    ["plant_calibration", "Plant Calibration", 0, "make", "still", "python", "full_scene", "unavailable"],
    ["plant_glow", "Plant Glow", 15, "gather", "gentle", "python", "background"],
    ["plant_mask_highlight", "Plant Mask Highlight", 0, "make", "still", "python", "full_scene", "unavailable"],
    ["quasicrystal_bloom", "Quasicrystal Bloom", 4, "focus", "gentle", "python", "background"],
    ["rain_on_glass", "Rain on Glass", 4, "settle", "gentle", "python", "background"],
    ["rainbow", "Rainbow", 4, "gather", "lively", "python", "background"],
    ["reaction_diffusion_garden", "Reaction Diffusion Garden", 4, "focus", "gentle", "python", "background"],
    ["simple_test", "Simple Test", 0, "make", "still", "python", "full_scene", "quarantined"],
    ["snake", "Snake", 10, "play", "lively", "python", "full_scene"],
    ["solid", "Solid Color", 5, "focus", "still", "python", "background"],
    ["sparkle", "Sparkle", 5, "gather", "gentle", "python", "background"],
    ["spiral_single", "Single Spiral", 0, "focus", "gentle", "python", "background"],
    ["strip_order", "Strip Order", 0, "make", "still", "python", "full_scene", "quarantined"],
    ["tetris", "Tetris", 5, "play", "lively", "python", "full_scene"],
    ["tidal_bioluminescence", "Tidal Bioluminescence", 3, "settle", "gentle", "python", "background"],
    ["waterfall_veil", "Waterfall Veil", 3, "settle", "gentle", "python", "background"],
    ["wave", "Wave", 6, "settle", "gentle", "python", "background"],
    ["wind_in_the_reeds", "Wind in the Reeds", 3, "settle", "gentle", "python", "background"],
    ["world_flags", "World Flags", 8, "know", "gentle", "python", "full_scene"]
  ].map(([id, title, presetCount, intent, motion, provider, role, availability = "ready"]) => ({
    id, title, presetCount, intent, motion, provider, role, availability
  }));

  const ACTUAL_PRESET_NAMES = {
    ascii_drop: ["Amber Terminal", "Cyan Datastream", "Maximum Overflow", "Love Letter", "Matrix Rain"],
    aurora_curtains: ["Violet Solar Choir", "Solar Morning Curtains", "Boreal Hush", "Polar Night"],
    aurora_curtains_native: ["Solar Wind Curtains", "Quiet Aurora Curtains"],
    canopy_cup: ["Plant Uprising", "Barrel Temple Climb", "Sunset Crystal Falls", "Cinematic No-HUD", "Webline Rooftops", "Power-Up Pandemonium", "Impossible Valley Relay", "Neon Vine Night", "Fern Gully Flutter", "Championship Speedrun", "Storybook Slow Motion", "Seven-Realm Grand Prix"],
    cellular_tapestry: ["Rule 30 Festival", "Quiet Rule 90 Lace", "Midnight Rule 110"],
    christmas_tree: ["Sugarplum Party", "Fireside Homecoming", "Silent Night", "Northern Lights Eve"],
    circadian_window: ["Pastel Noon Window", "Quiet Real Time", "Midnight Window", "Day in a Minute"],
    clock: ["Office 24 Hour", "Calendar Glance", "Violet Drift Grid", "Temporal Monoliths", "Daypart Horizon", "Night Shift", "Remote Team +6 Hours", "Aurora Orbits", "Aurora Hourglass", "Three Quiet Signals", "Bioluminescent Tide", "Lunar Binary Constellation", "Bedside Amber", "Binary Constellation", "Tidal Orrery", "Violet Sandstorm", "Last Light Horizon", "Amber Afterglow", "Precision Seconds", "Classic Wall Clock", "Chrono Scan", "Remote Team -8 Hours", "Midnight Planetarium", "Forest Night Almanac"],
    cloud_canyon: ["Sunset Overcast", "Daylight Cloud Canyon", "Silver Clearing", "Deep Moon Canyon"],
    conway_life: ["Synthwave Sunset", "Classic Green on Black", "Ice Crystal", "Pulsar Observatory", "Arcade Afterlife", "R-pentomino Laboratory", "Earth: Living Cities", "Oscillator Orchard", "Solar Embers", "Deep Space Acorn", "Maximum Chaos", "Neon Glider Storm", "Gosper Glider Foundry", "Bioluminescent Tide", "Aurora Garden"],
    cyclic_reef: ["Reef Competition", "Quiet Polyps", "Midnight Reef Cavities"],
    desert_wind: ["Violet Night Dunes", "Candlelit Dunes", "Quiet Ochre", "Martian Saltation"],
    emoji: ["Ice Heart", "Neon Grin", "Valentine Heart", "Golden Smile"],
    firefly_synchrony: ["Firefly Assembly", "Lantern Meadow", "Meadow Murmurs", "Calm Pockets"],
    fireworks: ["Grand Finale", "Quiet Sparklers", "Patriotic Salute", "Neon Crackle", "Golden Willows"],
    flame_burst: ["Afterburner", "Solar Pulse", "Rapid Ignition", "Campfire Bloom", "Off-Center Comet"],
    flow_field_silk: ["Braided Current", "Quiet Tidal Silk", "Shadow Thread"],
    fluid_tank: ["Quiet Aquarium", "Bubble Column", "Caustic Laboratory", "Flash Flood", "Storm Tank", "She Cute"],
    frostwork: ["Crystal Front", "Quiet Window Frost", "Pastel Daybreak Frost", "Midnight Rime"],
    gif_animation: ["Koi Ribbon", "Balloon Blobs", "Cozy Shelf Naps", "Moon Bunny Meadow", "Curtain Cat Watch", "Hydrangea Rain", "Cupcake Sprinkle Party", "Jellyfish Lanterns", "Peach Orchard", "Cotton Candy Clouds", "Penguin Ice Fishing", "Tiny Robot Patrol", "Cactus Bloom Dance", "Cozy Window Cats", "Frog Pond Ripple", "Sunflower Hamsters", "Happy Star Fall", "Sleepy Bat Cave", "Snails After Rain", "Pocket Rocket", "Firefly Bottle", "Planet Parade", "Axolotl Bubble Column", "Moonlit Ducks", "Mushroom Village", "Lantern Tree", "Coral Fish Friends", "Campfire Ghost Stories", "Shy Ghost Parade", "Bumblebee Garden", "Grape Bounce", "Jolly Slime Stack"],
    gradient: ["Hypercolor Scan", "Forest Dawn", "Arctic Horizon", "Miami Sunset", "Ember Shift"],
    lava_lamp: ["Seven Bowl Portals", "Quiet Lava", "Ruby Vintage", "Ocean Blue", "Habitat Pools", "Midnight Violet", "Violet Glass", "Slow Giants", "Busy Bubbles", "Stormy Wax", "Classic Amber", "Cotton Candy", "Bowl Emitter", "Toxic Lime", "Bowl Bumpers", "Foliage Refraction", "Solar Flare", "Living Wall Showcase"],
    living_ecosystem: ["Temperate Wetland", "Serengeti Migration", "Perpetual Golden Hour", "Midnight Fireflies", "Synthetic Gene Lab", "Neon Microverse", "Autumn Mosaic", "Bioluminescent Exoplanet", "Boreal Night"],
    living_stained_glass: ["Rose Cathedral", "Aurora Transept", "Synthwave Basilica", "Daylight Rose Window", "Sea Glass Chapel", "Candlelight Mosaic", "Night Nave", "Pastel Chapel"],
    maze_chase: ["Family Maze", "Hunter Vision", "Nightmare Tunnel", "Classic Chase", "Midnight Pursuit"],
    moonlit_fog_banks: ["Quiet Banks", "Aurora Morning Fog", "Sleeping Ridge", "Silver Halo"],
    night_train_windows: ["Ember Express", "Moonlit Berth", "Synthwave Commuter", "Quiet Sleeper"],
    physarum_network: ["Electric Transport", "Quiet Mycelium", "Dark Detours", "Synthwave Mycelium"],
    pinball: ["Midnight Attract Mode", "Neon Casino", "Multiball Mayhem", "Slow Motion Replay", "Tournament Table"],
    pixel_quest: ["Scenic Journey", "Cinematic No HUD", "Pixel Speedrun", "Heroic Adventure"],
    plant_glow: ["Cosmic Ivy", "Earthbound Garden", "Jackpot Jungle", "Neon Pinball Vines", "Candy Roots", "Twilight Orchids", "Ember Veins", "Moonlit Moss", "Blue Hour Pinball", "Prismatic Multiball", "Aurora Canopy", "Bioluminescent Reef", "Arcade Overgrowth", "Hearth Vines", "Slow Table Garden"],
    quasicrystal_bloom: ["Twelvefold Portal", "Daylight Prism", "Quiet Decagon", "Fivefold Midnight"],
    rain_on_glass: ["Amber Downpour", "Pastel Sunshower", "Window Drizzle", "Garden After Midnight"],
    rainbow: ["Pastel Drift", "Hyperspectrum", "Classic Spectrum", "Reverse Prism"],
    reaction_diffusion_garden: ["Coral Chemistry", "Aurora Conservatory", "Quiet Fingerprints", "Midnight Cells"],
    snake: ["Neon Duel", "Prism Switchbacks", "Comet Garden", "Rainbow River", "Portal Bloom", "Classic Orchard", "Electric Hive", "Ice Labyrinth", "Fire Serpents", "Koi at Midnight"],
    solid: ["Rose Quartz", "Ultraviolet Pulse", "Deep Ocean", "Forest Breath", "Warm Linen"],
    sparkle: ["Diamond Dust", "Candlelight", "Starlight", "Emerald Fireflies", "Confetti Storm"],
    tetris: ["Classic Quartet", "Impossible Shift", "Cooperative Swarm", "Solo Zen", "Avalanche Factory"],
    tidal_bioluminescence: ["Violet Bloom Tide", "Slack Tide", "Midnight Plankton"],
    waterfall_veil: ["Moonfall Cascade", "Forest Trickle", "Hidden Falls"],
    wave: ["Ultraviolet Static", "Solar Radio", "Chill Raining Fish Tank", "Moonlit Tide", "Synthwave Ribbons", "Emerald Breath"],
    wind_in_the_reeds: ["Harvest Gust", "Reedbed Lull", "Winter Moon Reeds"],
    world_flags: ["Mini Banner Rush", "Brazil Festival", "Grand Banners", "Reverse Parade", "World Parade", "Japan Rising Sun", "Ukraine Solidarity", "Stars and Stripes"]
  };

  const INTENTS = {
    settle: ["Let the room exhale.", "Slow, low-contrast movement selected for winding down. Nothing changes on the wall while you look."],
    gather: ["Make the room feel welcoming.", "Warm, sociable light for dinner, conversation, and arriving home."],
    focus: ["Give the room a steady pulse.", "Ordered motion and restrained color that can sit beside concentrated work."],
    play: ["Turn the wall into a playmate.", "Games and touch-responsive worlds, with live controls one step away."],
    know: ["Let the room keep me posted.", "Time and useful information that stays legible without taking over the room."],
    make: ["Put something of mine on the wall.", "Pixel art, emoji, and authored color—plus specialist making tools when you need them."],
    all: ["Browse the whole wall library.", "All 52 sources and 292 named presets, including content that can only be previewed or serviced by a maintainer."]
  };

  const DESCRIPTION_BY_INTENT = {
    settle: "Low-pressure movement that gives plants and shadows room to breathe.",
    gather: "A warmer, more social composition with enough color to hold the room together.",
    focus: "Structured motion and readable rhythm without a loud focal point.",
    play: "A reactive world designed for steering, pointing, or taking turns.",
    know: "Information shaped for the tall wall and readable at room distance.",
    make: "An authored look made from pixels, symbols, or a precise color field."
  };

  const PALETTES = [
    ["#0b1821", "#21556a", "#8bc5c1", "#d5b878"],
    ["#160e20", "#4b316c", "#a657a0", "#efd08d"],
    ["#061914", "#1c5e48", "#72a85e", "#d4d083"],
    ["#1c0d08", "#70321f", "#db7f47", "#ffd493"],
    ["#071128", "#143f79", "#4fd9e8", "#ef70d7"],
    ["#10151b", "#344452", "#728593", "#d7d9ca"]
  ];

  const state = {
    route: "tune",
    intent: "settle",
    motion: "gentle",
    search: "",
    visibleCount: 9,
    selectedId: "aurora_curtains:boreal-hush",
    live: { name: "Boreal Hush", source: "Aurora Curtains", vibe: "Cozy", brightness: 42, memory: "saved", running: true },
    lastLive: null,
    compare: [],
    scene: { backgroundId: "aurora_curtains:boreal-hush", clock: true, opacity: 84, placement: "top", x: 0, y: 0, stale: "hold", memory: "saved" },
    vibe: "cozy",
    pending: null,
    play: { mode: "point", x: 16, y: 69, strength: 60, holes: [] }
  };

  const slug = value => value.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  const hash = value => [...value].reduce((sum, char) => ((sum << 5) - sum + char.charCodeAt(0)) | 0, 2166136261) >>> 0;
  const catalog = [];
  COMPONENTS.forEach((component, componentIndex) => {
    const names = ACTUAL_PRESET_NAMES[component.id] || [];
    for (let index = 0; index < component.presetCount; index += 1) {
      const name = names[index] || `${["Quiet", "Luminous", "Slow", "Midnight", "Soft", "Prismatic"][index % 6]} ${component.title} ${index + 1}`;
      catalog.push({
        id: `${component.id}:${slug(name)}`,
        componentId: component.id,
        component: component.title,
        name,
        intent: component.intent,
        motion: component.motion,
        provider: component.provider,
        role: component.role,
        availability: component.availability,
        description: DESCRIPTION_BY_INTENT[component.intent],
        palette: PALETTES[(componentIndex + index) % PALETTES.length]
      });
    }
  });

  const SOURCE_ONLY_ITEMS = COMPONENTS.filter(component => component.presetCount === 0).map((component, index) => ({
    id: `${component.id}:source`, componentId: component.id, component: component.title,
    name: component.title, intent: component.intent, motion: component.motion,
    provider: component.provider, role: component.role, availability: component.availability,
    description: component.role === "overlay" ? "A fixed clock layer for two-sheet scenes." : "A specialist source without curated household presets.",
    palette: PALETTES[(index + 2) % PALETTES.length], sourceOnly: true
  }));

  const byId = id => catalog.find(item => item.id === id) || SOURCE_ONLY_ITEMS.find(item => item.id === id) || catalog[0];

  const dom = {
    tuneTitle: document.getElementById("tune-title"), intentDescription: document.getElementById("intent-description"),
    search: document.getElementById("catalog-search"), list: document.getElementById("catalog-list"),
    summary: document.getElementById("catalog-summary"), heading: document.getElementById("catalog-heading"),
    selectionName: document.getElementById("selection-name"), selectionSource: document.getElementById("selection-source"),
    selectionDescription: document.getElementById("selection-description"), selectionIndex: document.getElementById("selection-index"),
    selectionCount: document.getElementById("selection-count"), providerNote: document.getElementById("provider-note"),
    setLive: document.getElementById("set-live"), heroWall: document.getElementById("hero-wall"),
    liveName: document.getElementById("live-name"), liveDetail: document.getElementById("live-detail"),
    compareDialog: document.getElementById("compare-dialog"), comparisonBay: document.getElementById("comparison-bay"),
    confirmDialog: document.getElementById("confirm-dialog"), toast: document.getElementById("toast"),
    moreButton: document.getElementById("more-button"), moreMenu: document.getElementById("more-menu"),
    sceneWall: document.getElementById("scene-wall"), sceneBackgroundName: document.getElementById("scene-background-name"),
    sceneBackgroundProvider: document.getElementById("scene-background-provider"), sceneProviderNote: document.getElementById("scene-provider-note"),
    clockSheet: document.getElementById("clock-sheet"), memory: document.getElementById("memory-state"),
    validation: document.getElementById("validation-line"), vibeWall: document.getElementById("vibe-wall"),
    playWall: document.getElementById("play-wall"), interactionLog: document.getElementById("interaction-log"),
    diagnosis: document.getElementById("diagnosis"), toolDialog: document.getElementById("tool-dialog"),
    toolTitle: document.getElementById("tool-title"), toolBody: document.getElementById("tool-body")
  };

  function currentResults() {
    const query = state.search.trim().toLowerCase();
    const all = state.intent === "all" ? [...catalog, ...SOURCE_ONLY_ITEMS] : catalog.filter(item => item.intent === state.intent);
    return all.filter(item => {
      const motionMatch = state.intent === "all" || state.motion === "gentle" || item.motion === state.motion || (state.motion === "still" && item.motion === "gentle");
      const textMatch = !query || `${item.name} ${item.component} ${item.description} ${item.provider} ${item.availability}`.toLowerCase().includes(query);
      return motionMatch && textMatch;
    });
  }

  function selected() {
    const results = currentResults();
    return results.find(item => item.id === state.selectedId) || byId(state.selectedId) || results[0] || catalog[0];
  }

  function providerLabel(item) {
    return item.provider === "receiver_native" ? "Receiver-native package" : "Python on host";
  }

  function availabilityLabel(item) {
    if (item.availability === "build-only") return "Build-only · preview available";
    if (item.availability === "unavailable") return "Unavailable on this wall";
    if (item.availability === "quarantined") return "Quarantined · maintainer only";
    return item.role === "overlay" ? "Scene overlay only" : "Preview";
  }

  function selectItem(id, { scroll = false } = {}) {
    state.selectedId = id;
    renderTune();
    if (scroll) document.querySelector(".selection").scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function renderTune() {
    const [title, description] = INTENTS[state.intent];
    dom.tuneTitle.textContent = title;
    dom.intentDescription.textContent = description;
    document.querySelectorAll("[data-intent]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.intent === state.intent)));
    document.querySelectorAll("[data-motion]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.motion === state.motion)));
    const results = currentResults();
    if (!results.some(item => item.id === state.selectedId) && results.length) state.selectedId = results[0].id;
    const item = selected();
    const selectedIndex = Math.max(0, results.findIndex(result => result.id === item.id));
    dom.selectionName.textContent = item.name;
    dom.selectionSource.textContent = `${item.component} · ${providerLabel(item)}`;
    dom.selectionDescription.textContent = item.description;
    dom.selectionIndex.textContent = String(selectedIndex + 1).padStart(2, "0");
    dom.selectionCount.textContent = String(results.length).padStart(2, "0");
    dom.heading.textContent = state.intent === "all" ? "The complete source ledger" : `Selected for “${state.intent === "gather" ? "welcome people" : state.intent}”`;
    dom.summary.textContent = `${results.length} matches · complete names always shown`;
    dom.providerNote.hidden = item.provider !== "receiver_native" && item.availability === "ready";
    if (item.provider === "receiver_native") dom.providerNote.textContent = "Host-build simulation preview — not receiver framebuffer readback. Provider identity remains receiver_native.";
    else if (item.availability !== "ready") dom.providerNote.textContent = `${availabilityLabel(item)}. Catalog visibility does not mean this source can run live.`;
    const ready = item.availability === "ready" && !item.sourceOnly && item.role !== "overlay";
    dom.setLive.disabled = !ready;
    dom.setLive.querySelector("span").textContent = ready ? "Set the live wall to" : availabilityLabel(item);
    dom.setLive.querySelector("strong").textContent = ready ? `“${item.name}”` : item.component;
    document.getElementById("compose-from-look").hidden = item.role !== "background" || item.availability !== "ready";
    renderCatalog(results);
  }

  function renderCatalog(results) {
    const shown = results.slice(0, state.visibleCount);
    dom.list.innerHTML = "";
    shown.forEach((item, index) => {
      const li = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "catalog-item";
      button.dataset.itemId = item.id;
      button.setAttribute("aria-current", item.id === state.selectedId ? "true" : "false");
      button.disabled = item.availability === "quarantined" && item.sourceOnly;
      button.innerHTML = `<span>${String(index + 1).padStart(2, "0")}</span><strong></strong><small></small><b></b>`;
      button.querySelector("strong").textContent = item.name;
      button.querySelector("small").textContent = `${item.component} · ${providerLabel(item)} · ${item.role.replace("_", " ")}`;
      button.querySelector("b").textContent = availabilityLabel(item);
      li.append(button);
      dom.list.append(li);
    });
    const more = document.getElementById("load-more");
    more.hidden = results.length <= shown.length;
    more.textContent = `Show ${Math.min(12, results.length - shown.length)} more names`;
  }

  function setRoute(route) {
    state.route = route;
    document.querySelectorAll("[data-view]").forEach(view => {
      view.hidden = view.dataset.view !== route;
      view.classList.toggle("is-active", view.dataset.view === route);
    });
    document.querySelectorAll(".utility-nav [data-route]").forEach(button => {
      if (button.dataset.route === route) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    dom.moreMenu.hidden = true;
    dom.moreButton.setAttribute("aria-expanded", "false");
    document.getElementById("main").focus({ preventScroll: true });
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function toast(message) {
    dom.toast.textContent = message;
    dom.toast.classList.add("is-visible");
    window.clearTimeout(toast.timeout);
    toast.timeout = window.setTimeout(() => dom.toast.classList.remove("is-visible"), 2600);
  }

  function updateLiveHeader() {
    dom.liveName.textContent = state.live.running ? state.live.name : "Stopped";
    dom.liveDetail.textContent = state.live.running
      ? `${state.live.vibe} · ${state.live.brightness}% · ${state.live.memory === "saved" ? "saved exactly" : state.live.memory === "dirty" ? "unsaved changes" : "changed elsewhere"}`
      : "Wall dark · power off";
    document.getElementById("stop-live").textContent = state.live.running ? "Stop wall" : "Start wall";
    document.getElementById("scene-live-name").textContent = state.live.running ? state.live.name : "Stopped";
  }

  function openConfirmation(type, name, detail) {
    state.pending = { type, name };
    document.getElementById("confirm-live").textContent = state.live.running ? state.live.name : "Stopped";
    document.getElementById("confirm-next").textContent = name;
    document.getElementById("confirm-button-name").textContent = `“${name}”`;
    document.getElementById("confirm-detail").textContent = detail;
    dom.confirmDialog.showModal();
  }

  function applyPending() {
    if (!state.pending) return;
    if (state.pending.type === "look") {
      const item = selected();
      state.live = { ...state.live, name: item.name, source: item.component, running: true, memory: "saved" };
      toast(`Live wall is now “${item.name}”.`);
    } else if (state.pending.type === "scene") {
      const background = byId(state.scene.backgroundId);
      state.live = { ...state.live, name: `${background.name}${state.scene.clock ? " + Clock" : ""}`, source: "Two-sheet scene", running: true, memory: state.scene.memory === "saved" ? "saved" : "dirty" };
      toast("The two-sheet scene is now live.");
    } else if (state.pending.type === "atmosphere") {
      state.live.vibe = state.vibe[0].toUpperCase() + state.vibe.slice(1);
      state.live.brightness = Number(document.getElementById("brightness").value);
      toast("Room character and plant behavior applied independently.");
    }
    state.pending = null;
    updateLiveHeader();
  }

  function renderCompare() {
    const first = selected();
    const results = currentResults();
    const index = Math.max(0, results.findIndex(item => item.id === first.id));
    const second = results[(index + 1) % results.length] || catalog[1];
    state.compare = [first.id, second.id];
    dom.comparisonBay.innerHTML = "";
    state.compare.forEach(id => {
      const item = byId(id);
      const side = document.createElement("section");
      side.className = "compare-side";
      side.innerHTML = `<canvas class="pixel-wall compare-wall" width="32" height="138" aria-label="Isolated preview"></canvas><div><p class="eyebrow"></p><h3></h3><p class="compare-description"></p><button class="quiet-button" type="button">Use this preview</button></div>`;
      side.querySelector("canvas").dataset.itemId = item.id;
      side.querySelector(".eyebrow").textContent = `${item.component} · ${providerLabel(item)}`;
      side.querySelector("h3").textContent = item.name;
      side.querySelector(".compare-description").textContent = item.description;
      side.querySelector("button").addEventListener("click", () => {
        dom.compareDialog.close();
        selectItem(item.id, { scroll: true });
      });
      dom.comparisonBay.append(side);
    });
    dom.compareDialog.showModal();
  }

  function renderScene() {
    const background = byId(state.scene.backgroundId);
    dom.sceneBackgroundName.textContent = background.name;
    dom.sceneBackgroundProvider.textContent = `${background.component} · ${providerLabel(background)}`;
    dom.sceneProviderNote.hidden = background.provider !== "receiver_native";
    dom.sceneProviderNote.textContent = "Host-build simulation preview — not receiver framebuffer readback. Known Python fallback will be used if native validation fails.";
    document.getElementById("clock-enabled").checked = state.scene.clock;
    dom.clockSheet.hidden = !state.scene.clock;
    dom.clockSheet.style.opacity = String(state.scene.opacity / 100);
    const baseTop = { top: 12, middle: 50, bottom: 87 }[state.scene.placement];
    dom.clockSheet.style.top = `calc(${baseTop}% + ${state.scene.y * .28}px)`;
    dom.clockSheet.style.left = `calc(50% + ${state.scene.x * .35}px)`;
    document.getElementById("opacity-output").value = `${state.scene.opacity}%`;
    document.getElementById("clock-x-output").value = String(state.scene.x);
    document.getElementById("clock-y-output").value = String(state.scene.y);
    document.querySelectorAll("[data-placement]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.placement === state.scene.placement)));
    renderMemory();
  }

  function renderMemory() {
    dom.memory.dataset.state = state.scene.memory;
    const copy = {
      saved: ["Saved exactly", "Matches “Evening time”"],
      dirty: ["Unsaved arrangement", "Preview differs from the saved layout"],
      drift: ["Wall changed elsewhere", "Reload live state before applying"]
    }[state.scene.memory];
    dom.memory.querySelector("strong").textContent = copy[0];
    dom.memory.querySelector("small").textContent = copy[1];
  }

  function markSceneDirty() {
    if (state.scene.memory !== "drift") state.scene.memory = "dirty";
    dom.validation.className = "validation-line";
    dom.validation.textContent = "Preview changed. Live wall is still untouched.";
    renderScene();
  }

  const VIBE_PALETTES = {
    neutral: ["#080a10", "#202634", "#5c6880", "#e0e4ec"],
    quiet: ["#05090e", "#111e28", "#2f4852", "#97a88e"],
    cozy: ["#100705", "#351810", "#703b20", "#f4a456"],
    vivid: ["#040514", "#12174c", "#2d429a", "#46e1ff"],
    celebration: ["#0c0314", "#351050", "#70209d", "#ff4eae"]
  };

  function plantSentence() {
    const visual = [...document.querySelectorAll(".visual-group input:checked")].map(input => input.value);
    const field = document.querySelector('input[name="field"]:checked').value;
    const surface = document.querySelector('input[name="surface"]:checked').value;
    const phrases = [];
    if (visual.includes("illuminate")) phrases.push("edges glow");
    if (visual.includes("shadow")) phrases.push("leaves cast soft shadows");
    if (visual.includes("refract")) phrases.push("light bends like glass");
    if (visual.includes("hue_shift")) phrases.push("color shifts at leaves");
    if (visual.includes("liquid_glass")) phrases.push("light ripples through leaves");
    if (visual.includes("emitter")) phrases.push("globes create motion");
    const fieldCopy = { none: "", attractor: "motion is drawn toward plants", repulsor: "motion is pushed away", slow_zone: "motion slows nearby" }[field];
    const surfaceCopy = { none: "motion passes through", obstacle: "motion flows around foliage", portal: "motion enters and emerges", bumper: "motion bounces away", hazard: "motion disappears on contact", habitat: "motion gathers and lives there" }[surface];
    if (fieldCopy) phrases.push(fieldCopy);
    phrases.push(surfaceCopy);
    const sentence = phrases.length ? `${phrases[0][0].toUpperCase()}${phrases[0].slice(1)}${phrases.length > 1 ? `; ${phrases.slice(1).join("; ")}` : ""}.` : "Plants do not alter the current look.";
    document.getElementById("plant-sentence").textContent = sentence;
  }

  function drawWall(canvas, item, frame = 0, paletteOverride = null) {
    if (!canvas || !item) return;
    const ctx = canvas.getContext("2d", { alpha: false });
    const palette = paletteOverride || item.palette;
    const seed = hash(item.id);
    for (let y = 0; y < 138; y += 1) {
      for (let x = 0; x < 32; x += 1) {
        const wave = Math.sin((y * .105) + (x * .22) + frame * .08 + (seed % 37)) + Math.cos((x * .31) - frame * .04 + (seed % 11));
        const noise = Math.sin((x * 17.17 + y * 7.13 + seed) * .13) * .55;
        let colorIndex = Math.max(0, Math.min(palette.length - 1, Math.floor(((wave + noise + 2.7) / 5.4) * palette.length)));
        if ((seed + x * 19 + y * 7 + frame) % 83 === 0) colorIndex = palette.length - 1;
        ctx.fillStyle = palette[colorIndex];
        ctx.fillRect(x, y, 1, 1);
      }
    }
  }

  function drawPlay(frame) {
    const item = byId("snake:koi-at-midnight");
    drawWall(dom.playWall, item, frame);
    const ctx = dom.playWall.getContext("2d");
    state.play.holes.forEach(hole => {
      ctx.fillStyle = "#020302";
      ctx.beginPath();
      ctx.arc(hole.x, hole.y, 3.2, 0, Math.PI * 2);
      ctx.fill();
    });
    ctx.fillStyle = state.play.mode === "hole" ? "#f6c56f" : "#f4f0d8";
    ctx.beginPath();
    ctx.arc(state.play.x, state.play.y, 2.1, 0, Math.PI * 2);
    ctx.fill();
  }

  function animate() {
    let frame = 0;
    const tick = () => {
      frame += 1;
      drawWall(dom.heroWall, selected(), frame);
      drawWall(dom.sceneWall, byId(state.scene.backgroundId), frame);
      drawWall(dom.vibeWall, selected(), frame, VIBE_PALETTES[state.vibe]);
      document.querySelectorAll(".compare-wall").forEach(canvas => drawWall(canvas, byId(canvas.dataset.itemId), frame));
      drawPlay(frame);
    };
    tick();
    return window.setInterval(tick, window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 900 : 120);
  }

  function openTool(tool) {
    const copy = {
      paint: ["Paint pixels", "Tap or drag the wall to sketch. A resolved version would support color, brush size, sparse updates, and painter presets."],
      emoji: ["Arrange emoji", "Choose a symbol, then place it on the tall wall. A resolved version would add sizing, ordering, and safe-area feedback."],
      mask: ["Map leaves and globes", "A calibrated workflow would separate soft foliage from seven fixed globe regions and show clearance before saving evidence."]
    }[tool];
    dom.toolTitle.textContent = copy[0];
    dom.toolBody.innerHTML = `<div class="tool-concept"><canvas width="32" height="138" aria-label="Lower-fidelity ${copy[0]} concept"></canvas><div><p></p><div class="tool-actions"></div></div></div>`;
    dom.toolBody.querySelector("p").textContent = copy[1];
    const actions = dom.toolBody.querySelector(".tool-actions");
    if (tool === "emoji") {
      actions.className = "emoji-shelf";
      ["🌿", "🌙", "🙂", "✨", "❤️", "🐸"].forEach(symbol => {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = symbol;
        button.setAttribute("aria-label", `Place ${symbol}`);
        button.addEventListener("click", () => toast(`${symbol} placed in the concept preview.`));
        actions.append(button);
      });
    } else {
      ["Brush", "Undo", tool === "mask" ? "Show clearance" : "Save concept"].forEach(label => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "quiet-button";
        button.textContent = label;
        button.addEventListener("click", () => toast(`${label} is a lower-fidelity placeholder.`));
        actions.append(button);
      });
    }
    const canvas = dom.toolBody.querySelector("canvas");
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#07110a";
    ctx.fillRect(0, 0, 32, 138);
    for (let i = 0; i < 90; i += 1) {
      ctx.fillStyle = tool === "mask" ? (i % 4 ? "#305d39" : "#f2a74b") : ["#9cc76e", "#ebbf69", "#8cb9c6"][i % 3];
      ctx.fillRect((i * 17) % 32, (i * 31) % 138, tool === "mask" ? 2 : 1, tool === "mask" ? 3 : 1);
    }
    if (tool === "paint") {
      const paint = event => {
        const rect = canvas.getBoundingClientRect();
        const x = Math.floor((event.clientX - rect.left) / rect.width * 32);
        const y = Math.floor((event.clientY - rect.top) / rect.height * 138);
        ctx.fillStyle = "#f2c66d";
        ctx.fillRect(x - 1, y - 1, 3, 3);
      };
      canvas.addEventListener("pointerdown", event => { canvas.setPointerCapture(event.pointerId); paint(event); });
      canvas.addEventListener("pointermove", event => { if (event.buttons) paint(event); });
    }
    dom.toolDialog.showModal();
  }

  document.addEventListener("click", event => {
    const routeButton = event.target.closest("[data-route]");
    if (routeButton) setRoute(routeButton.dataset.route);
    const intentButton = event.target.closest("[data-intent]");
    if (intentButton) {
      state.intent = intentButton.dataset.intent;
      state.search = "";
      dom.search.value = "";
      state.visibleCount = 9;
      setRoute("tune");
      renderTune();
    }
    const motionButton = event.target.closest("[data-motion]");
    if (motionButton) { state.motion = motionButton.dataset.motion; state.visibleCount = 9; renderTune(); }
    const itemButton = event.target.closest("[data-item-id]");
    if (itemButton && !itemButton.disabled) selectItem(itemButton.dataset.itemId, { scroll: true });
    const placementButton = event.target.closest("[data-placement]");
    if (placementButton) { state.scene.placement = placementButton.dataset.placement; markSceneDirty(); }
    const vibeButton = event.target.closest("[data-vibe]");
    if (vibeButton) {
      state.vibe = vibeButton.dataset.vibe;
      document.querySelectorAll("[data-vibe]").forEach(button => button.setAttribute("aria-pressed", String(button === vibeButton)));
      toast(`${vibeButton.textContent.trim().split(/\s{2,}/)[0]} selected in preview only.`);
    }
    const interactionButton = event.target.closest("[data-interaction]");
    if (interactionButton) {
      state.play.mode = interactionButton.dataset.interaction;
      document.querySelectorAll("[data-interaction]").forEach(button => button.setAttribute("aria-pressed", String(button === interactionButton)));
      dom.interactionLog.textContent = state.play.mode === "hole" ? "Ready to make a hole." : "Ready for a point.";
    }
    const directionButton = event.target.closest("[data-direction]");
    if (directionButton) movePlay(directionButton.dataset.direction);
    const symptomButton = event.target.closest("[data-symptom]");
    if (symptomButton) diagnose(symptomButton.dataset.symptom, symptomButton);
    const toolButton = event.target.closest("[data-tool]");
    if (toolButton) openTool(toolButton.dataset.tool);
  });

  dom.search.addEventListener("input", () => { state.search = dom.search.value; state.intent = state.search ? "all" : state.intent; state.visibleCount = 12; renderTune(); });
  document.getElementById("all-catalog").addEventListener("click", () => { state.intent = "all"; state.search = ""; dom.search.value = ""; state.visibleCount = 16; renderTune(); document.getElementById("catalog-heading").scrollIntoView({ behavior: "smooth" }); });
  document.getElementById("load-more").addEventListener("click", () => { state.visibleCount += 12; renderTune(); });
  document.getElementById("previous-look").addEventListener("click", () => {
    const results = currentResults(); const index = Math.max(0, results.findIndex(item => item.id === selected().id)); selectItem(results[(index - 1 + results.length) % results.length].id);
  });
  document.getElementById("next-look").addEventListener("click", () => {
    const results = currentResults(); const index = Math.max(0, results.findIndex(item => item.id === selected().id)); selectItem(results[(index + 1) % results.length].id);
  });
  document.getElementById("compare-look").addEventListener("click", renderCompare);
  dom.setLive.addEventListener("click", () => openConfirmation("look", selected().name, "This sends one start action for the selected preset. Preview timing, vibe, and plant exploration remain isolated until you confirm."));
  document.getElementById("compose-from-look").addEventListener("click", () => { state.scene.backgroundId = selected().id; state.scene.memory = "dirty"; renderScene(); setRoute("scene"); });
  document.getElementById("change-background").addEventListener("click", () => { setRoute("tune"); toast("Choose a background, then use “Add a clock over this look”."); });

  document.getElementById("stop-live").addEventListener("click", () => {
    if (state.live.running) { state.lastLive = { ...state.live }; state.live.running = false; toast("Wall stopped in the prototype."); }
    else { state.live = { ...(state.lastLive || state.live), running: true }; toast("Wall started with the previous live state."); }
    updateLiveHeader();
  });
  document.getElementById("inspect-live").addEventListener("click", () => toast(`${state.live.name} · ${state.live.source} · ${state.live.memory} state.`));
  dom.moreButton.addEventListener("click", () => { const open = dom.moreMenu.hidden; dom.moreMenu.hidden = !open; dom.moreButton.setAttribute("aria-expanded", String(open)); });

  document.getElementById("confirm-apply").addEventListener("click", event => { event.preventDefault(); applyPending(); dom.confirmDialog.close(); });
  dom.confirmDialog.addEventListener("close", () => { if (dom.confirmDialog.returnValue === "cancel") state.pending = null; });

  document.querySelectorAll(".sheet-heading").forEach(button => button.addEventListener("click", () => {
    const body = button.nextElementSibling; const open = button.getAttribute("aria-expanded") === "true"; button.setAttribute("aria-expanded", String(!open)); body.hidden = open;
  }));
  document.getElementById("clock-enabled").addEventListener("change", event => { state.scene.clock = event.target.checked; markSceneDirty(); });
  document.getElementById("clock-opacity").addEventListener("input", event => { state.scene.opacity = Number(event.target.value); markSceneDirty(); });
  document.getElementById("clock-x").addEventListener("input", event => { state.scene.x = Number(event.target.value); markSceneDirty(); });
  document.getElementById("clock-y").addEventListener("input", event => { state.scene.y = Number(event.target.value); markSceneDirty(); });
  document.querySelectorAll('input[name="stale"]').forEach(input => input.addEventListener("change", event => { state.scene.stale = event.target.value; markSceneDirty(); }));
  document.getElementById("validate-scene").addEventListener("click", () => {
    dom.validation.className = "validation-line is-valid";
    dom.validation.textContent = "Valid: one background, optional clock overlay, placement clipped to wall, and a known Python fallback.";
  });
  document.getElementById("save-scene").addEventListener("click", () => {
    state.scene.memory = "saved"; renderMemory(); dom.validation.className = "validation-line is-valid";
    dom.validation.textContent = "Saved “Evening time” as layout only. Vibe, plants, brightness, FPS, and pace were not captured.";
  });
  document.getElementById("drift-demo").addEventListener("click", () => {
    state.scene.memory = "drift"; renderMemory(); dom.validation.className = "validation-line is-warning";
    dom.validation.textContent = "Live wall revision advanced elsewhere. Reload before applying to avoid overwriting that change.";
  });
  document.getElementById("apply-scene").addEventListener("click", () => {
    if (state.scene.memory === "drift") { toast("Reload live state first; this draft is drifting from the wall."); return; }
    const background = byId(state.scene.backgroundId); const name = `${background.name}${state.scene.clock ? " + Clock" : ""}`;
    openConfirmation("scene", name, "This applies the composed scene. The saved scene contains layout only; global vibe, plants, brightness, FPS, and pace remain independent.");
  });

  document.querySelectorAll(".plant-group input").forEach(input => input.addEventListener("change", plantSentence));
  document.getElementById("plant-strength").addEventListener("input", event => { document.getElementById("plant-strength-output").value = `${event.target.value}%`; });
  document.getElementById("brightness").addEventListener("input", event => { document.getElementById("brightness-output").value = `${event.target.value}%`; });
  document.getElementById("apply-atmosphere").addEventListener("click", () => openConfirmation("atmosphere", state.live.name, `This changes global vibe to ${state.vibe}, plant semantics, brightness, target FPS, and operator pace. It does not change or save the current scene.`));

  function positionPlay(event) {
    const rect = dom.playWall.getBoundingClientRect();
    state.play.x = Math.max(0, Math.min(31, Math.round((event.clientX - rect.left) / rect.width * 31)));
    state.play.y = Math.max(0, Math.min(137, Math.round((event.clientY - rect.top) / rect.height * 137)));
    if (state.play.mode === "hole" && event.type === "pointerdown") state.play.holes.push({ x: state.play.x, y: state.play.y });
    dom.interactionLog.textContent = `${state.play.mode === "hole" ? "Hole" : "Point"} at x ${state.play.x}, y ${state.play.y}, strength ${state.play.strength}%. Local prototype only.`;
  }
  dom.playWall.addEventListener("pointerdown", event => { dom.playWall.setPointerCapture(event.pointerId); positionPlay(event); });
  dom.playWall.addEventListener("pointermove", event => { if (event.buttons && state.play.mode === "point") positionPlay(event); });
  dom.playWall.addEventListener("keydown", event => {
    const directions = { ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right", Enter: "center", " ": "center" };
    if (directions[event.key]) { event.preventDefault(); movePlay(directions[event.key]); }
  });
  document.getElementById("interaction-strength").addEventListener("input", event => { state.play.strength = Number(event.target.value); document.getElementById("interaction-strength-output").value = `${event.target.value}%`; });

  function movePlay(direction) {
    if (direction === "up") state.play.y = Math.max(0, state.play.y - 4);
    if (direction === "down") state.play.y = Math.min(137, state.play.y + 4);
    if (direction === "left") state.play.x = Math.max(0, state.play.x - 2);
    if (direction === "right") state.play.x = Math.min(31, state.play.x + 2);
    if (direction === "center" && state.play.mode === "hole") state.play.holes.push({ x: state.play.x, y: state.play.y });
    dom.interactionLog.textContent = `${direction === "center" ? "Action" : `D-pad ${direction}`} at x ${state.play.x}, y ${state.play.y}. Local prototype only.`;
  }

  function diagnose(symptom, button) {
    document.querySelectorAll("[data-symptom]").forEach(item => item.setAttribute("aria-pressed", String(item === button)));
    const diagnoses = {
      dark: ["First, compare the four wall sections.", "A completely dark 8-lane section points toward receiver power, SPI wiring, or its output path. Sections 3 and 4 are write-only today, so visually verify them before trusting telemetry."],
      flash: ["Software counters cannot rule out the LED signal path.", "Correlate flashes with an output-rate sweep, then inspect ground reference, level shifter, strip data connections, and power transients. Zero CRC errors is not proof downstream."],
      slow: ["Frame generation has comfortable headroom right now.", "Actual rate is 29.7 of 30 FPS and average frame time is 8.1 ms. Refresh evidence during the slowdown and compare generate time with send time."],
      clock: ["Check whether the clock sheet is stale or intentionally cleared.", "Inspect the scene revision and overlay lease. A hold policy keeps the last frame; clear-after-lease removes it when updates stop."]
    }[symptom];
    dom.diagnosis.innerHTML = "";
    const strong = document.createElement("strong"); const p = document.createElement("p");
    strong.textContent = diagnoses[0]; p.textContent = diagnoses[1]; dom.diagnosis.append(strong, p);
  }

  document.getElementById("refresh-evidence").addEventListener("click", event => {
    event.currentTarget.disabled = true; event.currentTarget.textContent = "Refreshing…";
    window.setTimeout(() => { event.currentTarget.disabled = false; event.currentTarget.textContent = "Refresh evidence"; toast("Evidence refreshed. Expected-degraded state is unchanged."); }, 650);
  });

  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && !dom.moreMenu.hidden) { dom.moreMenu.hidden = true; dom.moreButton.setAttribute("aria-expanded", "false"); dom.moreButton.focus(); }
    if (state.route === "tune" && !event.metaKey && !event.ctrlKey && !event.altKey && !["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) {
      if (event.key === "ArrowRight") document.getElementById("next-look").click();
      if (event.key === "ArrowLeft") document.getElementById("previous-look").click();
    }
  });

  if (catalog.length !== 292 || COMPONENTS.length !== 52) {
    throw new Error(`Fixture contract mismatch: ${COMPONENTS.length} components, ${catalog.length} presets`);
  }

  renderTune();
  renderScene();
  plantSentence();
  updateLiveHeader();
  animate();
  const initialView = new URLSearchParams(window.location.search).get("view");
  if (["tune", "scene", "atmosphere", "play", "make", "health", "developer"].includes(initialView)) setRoute(initialView);
})();
