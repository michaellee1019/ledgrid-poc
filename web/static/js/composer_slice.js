(() => {
  'use strict';
  const root = document.querySelector('.composer');
  const api = root.dataset.apiRoot;
  const $ = (selector) => document.querySelector(selector);
  const nativeDigest = 'd0b8c0f9c7d55a8f58b6156e20c59afe6e4c5a7e2821cb6b3a29d9af81c296bf';
  function newUuid() {
    const browserCrypto = typeof globalThis.crypto === 'object' ? globalThis.crypto : null;
    if (typeof browserCrypto?.randomUUID === 'function') return browserCrypto.randomUUID();
    if (typeof browserCrypto?.getRandomValues === 'function') {
      const bytes = browserCrypto.getRandomValues(new Uint8Array(16));
      bytes[6] = (bytes[6] & 0x0f) | 0x40;
      bytes[8] = (bytes[8] & 0x3f) | 0x80;
      const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
      return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    }
    return `composer-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  }
  // A page lifetime client id makes a reload a new mutation stream.  Reusing
  // an id while resetting its sequence would make the next authored edit stale.
  const clientId = newUuid();
  const state = { status: null, library: {items: [], favorites: []}, filter: 'all', query: '', selection: null,
    scene: null, history: [], redo: [], sequence: 0, submitting: false, previewGeneration: 0, refreshInFlight: false, dirty: false, componentPresets: {}, authoredValidationError: null,
    wall: {
      bootstrap: null, observation: null, scene: null, activating: false, dirty: false,
      adoptedLook: null, adoptedVibeId: null,
    } };
  const identity = (value) => value ? `r${value.revision} · ${value.digest}` : 'None';
  const sleep = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));
  async function requestJson(url, options = {}) {
    let response;
    try { response = await fetch(url, {cache: 'no-store', ...options}); }
    catch (_) { const error = new Error('Wall server unavailable.'); error.code = 'offline'; throw error; }
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(body.error || `Wall request failed (${response.status}).`);
      error.code = body.code; error.status = response.status; error.blockers = body.blockers;
      throw error;
    }
    return body;
  }
  const number = (id) => Number($(id).value);
  const plantOptics = Object.freeze([
    {id: 'illuminate', label: 'Illuminate', enabled: '#plantIlluminateEnabled', strength: '#plantIlluminateStrength', value: '#plantIlluminateValue'},
    {id: 'shadow', label: 'Shadow', enabled: '#plantShadowEnabled', strength: '#plantShadowStrength', value: '#plantShadowValue'},
    {id: 'hue_shift', label: 'Hue shift', enabled: '#plantHueShiftEnabled', strength: '#plantHueShiftStrength', value: '#plantHueShiftValue'},
  ]);
  const plantOpticIds = new Set(plantOptics.map(({id}) => id));
  const recoveryMatchesStatus = (body) => Boolean(body.recovery?.authoritative && body.status?.current && body.recovery?.basis?.digest === body.status.current.digest && body.recovery?.basis?.revision === body.status.current.revision);
  const clockParameters = (existing = {}) => ({...existing, format_24h: $('#clockFormat').value === '24', show_seconds: $('#clockSeconds').checked, clock_offset_minutes: Math.trunc(number('#clockTimeOffset'))});
  const emojiParameters = () => ({text: $('#emojiText').value, x_offset: Math.trunc(number('#emojiXOffset')), y_offset: Math.trunc(number('#emojiYOffset')), char_spacing: Math.trunc(number('#emojiCharSpacing')), line_spacing: Math.trunc(number('#emojiLineSpacing')), scroll_speed: number('#emojiScrollSpeed'), pulse_speed: number('#emojiPulseSpeed')});
  const fireflyParameters = (existing = {}) => ({...existing, population: Math.trunc(number('#fireflyPopulation')), synchrony: number('#fireflySynchrony'), wandering: number('#fireflyWandering'), pulse_softness: number('#fireflyPulseSoftness'), meadow_glow: number('#fireflyMeadowGlow')});
  const fireworksParameters = () => ({launch_cadence: number('#fireworksCadence'), shell_population: Math.trunc(number('#fireworksPopulation')), burst_size: number('#fireworksBurstSize'), burst_style: $('#fireworksStyle').value, gravity: number('#fireworksGravity'), trails: number('#fireworksTrails'), crackle: number('#fireworksCrackle'), twinkle: number('#fireworksTwinkle'), seed: Math.trunc(number('#fireworksSeed'))});
  const lavaParameters = (existing = {}) => ({...existing, blob_count: Math.trunc(number('#lavaBlobCount')), blob_scale: number('#lavaBlobScale'), viscosity: number('#lavaViscosity'), heat: number('#lavaHeat'), turbulence: number('#lavaTurbulence'), glow: number('#lavaGlow'), seed: Math.trunc(number('#lavaSeed'))});
  const flameParameters = (existing = {}) => ({...existing, ignition_cadence: number('#flameCadence'), flare_size: number('#flameSize'), ember_linger: number('#flameEmbers'), flicker: number('#flameFlicker')});
  const fluidParameters = (existing = {}) => ({...existing, flow_rate: number('#fluidFlow'), current: number('#fluidCurrent'), bubble_lift: number('#fluidBubbles'), surface_energy: number('#fluidSurface')});
  // Keep seed and HUD in an authored preset/remix even though the instrument
  // deliberately exposes only the six immediate race controls.
  const canopyParameters = (existing = {}) => ({...existing, world_theme: $('#canopyWorld').value, qualifying_heats: Math.trunc(number('#canopyHeats')), course_difficulty: number('#canopyCourse'), enemy_density: number('#canopyDensity'), rivalry: number('#canopyRivalry'), powerup_rate: number('#canopyPowerups')});
  const reefParameters = (existing = {}) => ({...existing, species_count: Math.trunc(number('#reefSpecies')), takeover_threshold: Math.trunc(number('#reefThreshold')), mutation: number('#reefMutation'), grazers: Math.trunc(number('#reefGrazers')), boundary_glow: number('#reefGlow'), topology: $('#reefTopology').value, pace: number('#reefPace'), seed: Math.trunc(number('#reefSeed'))});
  const tetrisParameters = (existing = {}) => ({...existing, tetromino_count: Math.trunc(number('#tetrisPieces')), fall_rate: number('#tetrisFallRate'), bot_imperfection: number('#tetrisRisk'), smooth_drop: $('#tetrisSmoothDrop').checked});
  const lavaInteractionParameters = (parameters = {}) => Object.fromEntries(['interaction_radius', 'interaction_strength'].filter((name) => Object.hasOwn(parameters, name)).map((name) => [name, parameters[name]]));
  const snakeParameters = (existing = {}) => ({...existing, move_cadence: number('#snakeCadence'), snake_count: Math.trunc(number('#snakeCount')), food_count: Math.trunc(number('#snakeFood')), growth_per_food: Math.trunc(number('#snakeGrowth')), ruleset: $('#snakeRules').value, obstacles: $('#snakeObstacles').value, trails: number('#snakeTrails'), glow: number('#snakeGlow'), seed: Math.trunc(number('#snakeSeed'))});
  const mazeParameters = (existing = {}) => ({...existing, chase_cadence_hz: number('#mazeCadence'), difficulty: number('#mazeDifficulty'), show_ai_targets: $('#mazeRadar').checked});
  const pinballParameters = (existing = {}) => ({...existing, table_tick_hz: number('#pinballTicks'), chaos: number('#pinballChaos')});
  const questParameters = (existing = {}) => ({...existing, quest_cadence_hz: number('#questCadence'), difficulty: number('#questDifficulty'), show_hud: $('#questHud').checked});
  const asciiDropParameters = (existing = {}) => ({...existing, phrase: $('#asciiPhrase').value, story: $('#asciiStory').value, fall_speed: number('#asciiSpeed'), density: number('#asciiDensity')});
  const emojiAnimationParameters = (existing = {}) => ({...existing, face: $('#emojiFace').value, mood: $('#emojiMood').value, pulse_hz: number('#emojiAnimationPulse'), scale: number('#emojiAnimationScale')});
  const treeParameters = (existing = {}) => ({...existing, season: $('#treeSeason').value, tree_height: Math.trunc(number('#treeHeight')), snowfall: number('#treeSnowfall')});
  const trainParameters = (existing = {}) => ({...existing, route: $('#trainRoute').value, travel_speed: number('#trainSpeed'), window_glow: number('#trainGlow')});
  const ambientIds = Object.freeze(['gradient', 'rainbow', 'solid', 'sparkle', 'wave']);
  const ambientParameters = (id, existing = {}) => {
    const fields = {gradient: ['gradientDirection','gradientDrift','gradientMotion','gradientSeed'], rainbow: ['rainbowBands','rainbowTravel','rainbowDirection','rainbowSeed'], solid: ['solidGlow','solidBreath','solidSeed'], sparkle: ['sparkleDensity','sparkleLinger','sparkleTwinkle','sparkleNight','sparkleSeed'], wave: ['waveAxis','waveFrequency','waveTravel','waveShape','waveDirection','waveSeed']}[id];
    const names = {gradient: ['direction','drift','motion','seed'], rainbow: ['bands','travel','direction','seed'], solid: ['glow','breath','seed'], sparkle: ['density','linger','twinkle','night','seed'], wave: ['axis','frequency','travel','shape','direction','seed']}[id];
    return {...existing, ...Object.fromEntries(fields.map((field, index) => { const control = document.getElementById(field); const name = names[index]; return [name, control.tagName === 'SELECT' ? control.value : (name === 'seed' || name === 'direction' ? Math.trunc(number(`#${field}`)) : number(`#${field}`))]; }))};
  };
  const atmosphereIds = Object.freeze(['circadian_window', 'cloud_canyon', 'desert_wind', 'moonlit_fog_banks', 'rain_on_glass', 'tidal_bioluminescence', 'waterfall_veil']);
  const sculptureIds = Object.freeze(['cellular_tapestry', 'flow_field_silk', 'frostwork', 'living_stained_glass', 'quasicrystal_bloom', 'living_ecosystem', 'physarum_network', 'reaction_diffusion_garden', 'wind_in_the_reeds']);
  const sculptureSpecs = Object.freeze({
    cellular_tapestry: {prefix:'tapestry', defaults:{motion:.48,density:.54,background_level:.16,seed:2801,rule:90,mutation:.01,wrap:true,row_interval:.55}, fields:[['rule','Rule'],['mutation','Mutation'],['row_interval','Weave pace']]},
    flow_field_silk: {prefix:'silk', defaults:{motion:.52,density:.5,background_level:.14,seed:1901,turbulence:.35,persistence:.8}, fields:[['turbulence','Curl'],['persistence','Silk linger'],['motion','Drift']]},
    frostwork: {prefix:'frost', defaults:{motion:.46,density:.48,background_level:.14,seed:1401,temperature:.35,melt_cycle:.55}, fields:[['temperature','Cold growth'],['melt_cycle','Melt cycle'],['density','Crystal density']]},
    living_stained_glass: {prefix:'glass', defaults:{motion:.34,density:.52,background_level:.18,seed:2201,lead_width:.3,light_direction:.4}, fields:[['lead_width','Leadwork'],['light_direction','Light angle'],['motion','Pane drift']]},
    quasicrystal_bloom: {prefix:'bloom', defaults:{motion:.42,density:.56,background_level:.16,seed:2701,symmetry:10,spatial_scale:2.4,warp:.18}, fields:[['symmetry','Symmetry'],['spatial_scale','Rosette scale'],['warp','Bloom warp']]},
    living_ecosystem: {prefix:'ecosystem', defaults:{motion:.55,density:.62,background_level:.22,seed:7319,migration:.55,predator_pressure:.38,canopy_density:.58,mutation:.1,night_life:.42}, fields:[['migration','Migration'],['predator_pressure','Hunter pressure'],['canopy_density','Canopy cover']]},
    physarum_network: {prefix:'physarum', defaults:{motion:.58,density:.6,background_level:.16,seed:10101,agent_count:700,branching:.75,diffusion:.62,nutrient_layout:'constellation',pulse_visibility:.4}, fields:[['agent_count','Explorers'],['branching','Route branching'],['diffusion','Trail diffusion']]},
    reaction_diffusion_garden: {prefix:'garden', defaults:{motion:.52,density:.58,background_level:.14,seed:9101,morphology:'coral',growth_rate:1,seeding_mode:'scattered',edge_glow:.65,color_by_age:.6,perturbation_interval:24}, fields:[['growth_rate','Growth'],['edge_glow','Front glow'],['color_by_age','History color']]},
    wind_in_the_reeds: {prefix:'reeds', defaults:{motion:.5,density:.58,background_level:.18,seed:6101,wind:.65,gustiness:.55,stem_density:1,season:'late_summer',motes:.45,silhouette_strength:.5}, fields:[['wind','Wind bend'],['gustiness','Gust fronts'],['stem_density','Stem density']]},
  });
  const sculptureControlId = (id, name) => `${sculptureSpecs[id].prefix}${name[0].toUpperCase()}${name.slice(1)}`;
  const sculptureParameters = (id, existing = {}) => ({...existing, ...Object.fromEntries(sculptureSpecs[id].fields.map(([name]) => [name, ['rule','symmetry','agent_count'].includes(name) ? Math.trunc(number(`#${sculptureControlId(id, name)}`)) : number(`#${sculptureControlId(id, name)}`)]))});
  const atmosphereSpecs = Object.freeze({
    circadian_window: {prefix: 'circadian', defaults: {motion: .35, density: .5, mood: 'natural', seed: 29001, hour: -1, time_scale: 1}, fields: [['motion','Motion'],['density','Sky detail'],['mood','Sky mood',[['natural','Natural'],['ember','Ember'],['sleeper','Sleeper'],['pastel','Pastel']]],['seed','Sky seed'],['hour','Clock hour'],['time_scale','Day scale']]},
    cloud_canyon: {prefix: 'cloud', defaults: {motion: .42, density: .46, mood: 'moonlit', seed: 4301}, fields: [['motion','Cloud drift'],['density','Bank density'],['mood','Canyon mood',[['moonlit','Moonlit'],['daylight','Daylight'],['ember','Ember'],['violet','Violet']]],['seed','Canyon seed']]},
    desert_wind: {prefix: 'desert', defaults: {motion: .35, density: .5, mood: 'ochre', seed: 8001}, fields: [['motion','Wind drift'],['density','Sand detail'],['mood','Dune mood',[['ochre','Ochre'],['mars','Mars'],['predawn','Predawn'],['candlelight','Candlelight']]],['seed','Dune seed']]},
    moonlit_fog_banks: {prefix: 'fog', defaults: {motion: .35, density: .5, mood: 'moonlit', seed: 7101}, fields: [['motion','Fog drift'],['density','Bank density'],['mood','Fog mood',[['moonlit','Moonlit'],['predawn','Predawn'],['sleeper','Sleeper'],['aurora','Aurora']]],['seed','Fog seed']]},
    rain_on_glass: {prefix: 'rain', defaults: {motion: .42, density: .46, mood: 'moonlit', seed: 4101}, fields: [['motion','Drop fall'],['density','Droplet count'],['mood','Rain mood',[['moonlit','Moonlit'],['garden','Garden'],['ember','Ember'],['pastel','Pastel']]],['seed','Rain seed']]},
    tidal_bioluminescence: {prefix: 'tidal', defaults: {motion: .42, density: .46, mood: 'moonlit', seed: 4501}, fields: [['motion','Swell pace'],['density','Plankton'],['mood','Tide mood',[['moonlit','Moonlit'],['boreal','Boreal'],['violet','Violet']]],['seed','Tide seed']]},
    waterfall_veil: {prefix: 'waterfall', defaults: {motion: .42, density: .46, mood: 'garden', seed: 4401}, fields: [['motion','Fall speed'],['density','Stream count'],['mood','Falls mood',[['garden','Garden'],['moonlit','Moonlit'],['violet','Violet']]],['seed','Falls seed']]},
  });
  const atmosphereControlId = (id, name) => `${atmosphereSpecs[id].prefix}${name[0].toUpperCase()}${name.slice(1)}`;
  const atmosphereParameters = (id, existing = {}) => ({...existing, ...Object.fromEntries(atmosphereSpecs[id].fields.map(([name]) => {
    const control = document.getElementById(atmosphereControlId(id, name));
    return [name, name === 'mood' ? control.value : (name === 'seed' ? Math.trunc(Number(control.value)) : Number(control.value))];
  }))});
  const emojiWidget = () => ({id: 'composer-emoji-message', component: {component_id: 'emoji_arranger', version: 1, provider: 'python', role: 'widget', parameters: emojiParameters()}, visible: true, placement: {mode: 'manual', strip_translation: 0, led_translation: 0}});
  const componentPresetTargets = Object.freeze({aurora_curtains: 'aurora-curtains-preset-cards', conway_life: 'conway-life-preset-cards', tetris: 'tetris-preset-cards', firefly_synchrony: 'firefly-synchrony-preset-cards', fireworks: 'fireworksPresetCards', flame_burst: 'flame-burst-preset-cards', fluid_tank: 'fluid-tank-preset-cards', lava_lamp: 'lavaPresetCards', snake: 'snakePresetCards', cyclic_reef: 'reefPresetCards', canopy_cup: 'canopyPresetCards', maze_chase: 'mazePresetCards', pinball: 'pinballPresetCards', pixel_quest: 'questPresetCards', ascii_drop: 'asciiDropPresetCards', emoji: 'emojiAnimationPresetCards', christmas_tree: 'christmasTreePresetCards', night_train_windows: 'nightTrainPresetCards', gradient: 'gradient-preset-cards', rainbow: 'rainbow-preset-cards', solid: 'solid-preset-cards', sparkle: 'sparkle-preset-cards', wave: 'wave-preset-cards'});
  const atmospherePresetTargets = Object.freeze(Object.fromEntries(atmosphereIds.map((id) => [id, `${id.replaceAll('_', '-')}-preset-cards`])));
  const sculpturePresetTargets = Object.freeze(Object.fromEntries(sculptureIds.map((id) => [id, `${id.replaceAll('_', '-')}-preset-cards`])));
  const componentControls = Object.freeze({aurora_curtains: ['#curtainDensity', '#foldDepth', '#glowIntensity'], conway_life: ['#lifeSeed', '#lifeRate'], tetris: ['#tetrisPieces', '#tetrisFallRate', '#tetrisRisk', '#tetrisSmoothDrop'], firefly_synchrony: ['#fireflyPopulation', '#fireflySynchrony', '#fireflyWandering', '#fireflyPulseSoftness', '#fireflyMeadowGlow'], fireworks: ['#fireworksCadence', '#fireworksPopulation', '#fireworksBurstSize', '#fireworksStyle', '#fireworksGravity', '#fireworksTrails', '#fireworksCrackle', '#fireworksTwinkle', '#fireworksSeed'], flame_burst: ['#flameCadence', '#flameSize', '#flameEmbers', '#flameFlicker'], fluid_tank: ['#fluidFlow', '#fluidCurrent', '#fluidBubbles', '#fluidSurface'], lava_lamp: ['#lavaBlobCount', '#lavaBlobScale', '#lavaViscosity', '#lavaHeat', '#lavaTurbulence', '#lavaGlow', '#lavaSeed'], snake: ['#snakeCadence', '#snakeCount', '#snakeFood', '#snakeGrowth', '#snakeRules', '#snakeObstacles', '#snakeTrails', '#snakeGlow', '#snakeSeed'], canopy_cup: ['#canopyWorld', '#canopyHeats', '#canopyCourse', '#canopyDensity', '#canopyRivalry', '#canopyPowerups'], cyclic_reef: ['#reefSpecies', '#reefThreshold', '#reefMutation', '#reefGrazers', '#reefGlow', '#reefTopology', '#reefPace', '#reefSeed'], maze_chase: ['#mazeCadence', '#mazeDifficulty', '#mazeRadar'], pinball: ['#pinballTicks', '#pinballChaos'], pixel_quest: ['#questCadence', '#questDifficulty', '#questHud'], ascii_drop: ['#asciiPhrase', '#asciiStory', '#asciiSpeed', '#asciiDensity'], emoji: ['#emojiFace', '#emojiMood', '#emojiAnimationPulse', '#emojiAnimationScale'], christmas_tree: ['#treeSeason', '#treeHeight', '#treeSnowfall'], night_train_windows: ['#trainRoute', '#trainSpeed', '#trainGlow'], gradient: ['#gradientDirection','#gradientDrift','#gradientMotion','#gradientSeed'], rainbow: ['#rainbowBands','#rainbowTravel','#rainbowDirection','#rainbowSeed'], solid: ['#solidGlow','#solidBreath','#solidSeed'], sparkle: ['#sparkleDensity','#sparkleLinger','#sparkleTwinkle','#sparkleNight','#sparkleSeed'], wave: ['#waveAxis','#waveFrequency','#waveTravel','#waveShape','#waveDirection','#waveSeed']});
  const atmosphereControls = Object.freeze(Object.fromEntries(atmosphereIds.map((id) => [id, atmosphereSpecs[id].fields.map(([name]) => `#${atmosphereControlId(id, name)}`)])));
  const sculptureControls = Object.freeze(Object.fromEntries(sculptureIds.map((id) => [id, sculptureSpecs[id].fields.map(([name]) => `#${sculptureControlId(id, name)}`)])));

  function installPixelStoryControls() {
    const field = (id, label, value, options = null) => { const wrapper = document.createElement('label'); wrapper.textContent = label; const control = document.createElement(options ? 'select' : 'input'); control.id = id; if (options) options.forEach(([optionValue, title]) => control.append(new Option(title, optionValue))); else { control.type = id === 'asciiPhrase' ? 'text' : 'number'; control.step = '.01'; } control.value = value; wrapper.append(control); $('#animationControls').append(wrapper); };
    field('asciiPhrase', 'ASCII phrase', 'HELLO'); field('asciiStory', 'ASCII story', 'terminal', [['terminal','Terminal'],['matrix','Matrix'],['love','Love letter'],['datastream','Datastream'],['overflow','Overflow']]); field('asciiSpeed', 'Glyph speed', '13'); field('asciiDensity', 'Glyph density', '.45');
    field('emojiFace', 'Emoji type', 'smile', [['smile','Smile'],['heart','Heart']]); field('emojiMood', 'Emoji mood', 'golden', [['golden','Golden'],['neon','Neon'],['rose','Rose'],['ice','Ice']]); field('emojiAnimationPulse', 'Emoji pulse', '.8'); field('emojiAnimationScale', 'Emoji scale', '1');
    field('treeSeason', 'Tree season', 'classic', [['classic','Classic'],['party','Party'],['quiet','Quiet'],['blizzard','Blizzard']]); field('treeHeight', 'Tree height', '58'); field('treeSnowfall', 'Tree snowfall', '.35');
    field('trainRoute', 'Train route', 'sleeper', [['sleeper','Sleeper'],['moonlit','Moonlit'],['ember','Ember'],['synthwave','Synthwave']]); field('trainSpeed', 'Train pace', '1'); field('trainGlow', 'Window glow', '.65');
  }
  function installTetrisControls() {
    const target = $('#animationControls');
    const numberField = (id, label, value, minimum, maximum, step) => {
      const wrapper = document.createElement('label'); wrapper.textContent = label;
      const control = document.createElement('input'); control.id = id; control.type = 'number';
      control.min = minimum; control.max = maximum; control.step = step; control.value = value;
      wrapper.append(control); target.append(wrapper);
    };
    numberField('tetrisPieces', 'Pieces', '5', '1', '128', '1');
    numberField('tetrisFallRate', 'Fall rate', '3', '.2', '5', '.1');
    numberField('tetrisRisk', 'Bot risk', '.18', '0', '.6', '.01');
    const wrapper = document.createElement('label'); wrapper.textContent = 'Smooth drop';
    const control = document.createElement('input'); control.id = 'tetrisSmoothDrop'; control.type = 'checkbox'; control.checked = true;
    wrapper.append(control); target.append(wrapper);
  }
  function installAmbientControls() {
    const field = (id, label, value, options = null) => { const wrapper = document.createElement('label'); wrapper.textContent = label; const control = document.createElement(options ? 'select' : 'input'); control.id = id; if (options) options.forEach(([key, name]) => control.append(new Option(name, key))); else { control.type = 'number'; control.step = '.01'; } control.value = value; wrapper.append(control); $('#animationControls').append(wrapper); };
    field('gradientDirection','Gradient direction','vertical',[['vertical','Vertical'],['horizontal','Horizontal'],['diagonal','Diagonal']]); field('gradientDrift','Gradient drift','.22'); field('gradientMotion','Gradient motion','.72'); field('gradientSeed','Gradient seed','6101');
    field('rainbowBands','Rainbow bands','1.4'); field('rainbowTravel','Rainbow travel','.65'); field('rainbowDirection','Rainbow direction','1'); field('rainbowSeed','Rainbow seed','6102'); field('solidGlow','Solid glow','.68'); field('solidBreath','Solid breath','0'); field('solidSeed','Solid seed','6103');
    field('sparkleDensity','Sparkle density','.2'); field('sparkleLinger','Sparkle linger','.65'); field('sparkleTwinkle','Sparkle twinkle','.72'); field('sparkleNight','Sparkle night','.08'); field('sparkleSeed','Sparkle seed','6104');
    field('waveAxis','Wave axis','vertical',[['vertical','Vertical'],['horizontal','Horizontal'],['diagonal','Diagonal']]); field('waveFrequency','Wave frequency','2'); field('waveTravel','Wave travel','.45'); field('waveShape','Wave shape','.8'); field('waveDirection','Wave direction','1'); field('waveSeed','Wave seed','6105');
  }
  function installAtmosphereControls() {
    const target = $('#animationControls');
    atmosphereIds.forEach((id) => atmosphereSpecs[id].fields.forEach(([name, label, options]) => {
      const wrapper = document.createElement('label'); wrapper.textContent = label;
      const control = document.createElement(options ? 'select' : 'input'); control.id = atmosphereControlId(id, name);
      if (options) options.forEach(([value, title]) => control.append(new Option(title, value)));
      else { control.type = 'number'; control.step = name === 'seed' ? '1' : '.01'; if (name === 'seed') { control.min = '0'; control.max = '999999'; } }
      control.value = atmosphereSpecs[id].defaults[name]; wrapper.append(control); target.append(wrapper);
    }));
  }
  function installSculptureControls() {
    const target = $('#animationControls');
    sculptureIds.forEach((id) => sculptureSpecs[id].fields.forEach(([name, label]) => {
      const wrapper = document.createElement('label'); wrapper.textContent = label; const control = document.createElement('input'); control.type = 'number'; control.step = name === 'rule' || name === 'symmetry' ? '1' : '.01'; control.id = sculptureControlId(id, name); control.value = sculptureSpecs[id].defaults[name]; wrapper.append(control); target.append(wrapper);
    }));
  }

  function sameLocalParameters(left, right) {
    if (left === right) return true;
    if (!left || !right || typeof left !== 'object' || typeof right !== 'object') return false;
    const leftKeys = Object.keys(left).sort(); const rightKeys = Object.keys(right).sort();
    return leftKeys.length === rightKeys.length && leftKeys.every((key, index) => key === rightKeys[index] && sameLocalParameters(left[key], right[key]));
  }
  function componentLabel(componentId) { return [...$('#animationChoice').options].find((option) => option.value === componentId)?.textContent || componentId; }
  function setComponentRegion(target, visible) {
    if (!target) return;
    target.hidden = !visible; target.inert = !visible; target.setAttribute('aria-hidden', String(!visible));
    target.querySelectorAll('button,input,select,textarea').forEach((control) => { control.disabled = !visible; });
  }
  function nestComponentControls() {
    const animationInspector = $('#animation-title').closest('.inspector');
    [
      ['snake', '#snake-title'],
      ['canopy_cup', '#canopy-title'],
      ['cyclic_reef', '#reef-title'],
      ['maze_chase pinball pixel_quest', '#arcade-trio-title'],
    ].forEach(([componentIds, headingSelector]) => {
      const region = document.querySelector(headingSelector)?.closest('.inspector');
      if (!region) return;
      region.classList.add('component-controls');
      region.dataset.animationComponents = componentIds;
    });
    document.querySelectorAll('.control-workspace > .inspector[data-animation-components]').forEach((region) => {
      if (region.parentElement === animationInspector) return;
      region.classList.add('component-controls');
      animationInspector.append(region);
    });
  }
  const semanticControls = [];
  const PALETTE_SWATCHES = Object.freeze({
    mist: ['#172034', '#7193c7', '#e2efff'], neutral: ['#151515', '#777', '#eee'],
    spectrum: ['#d6275c', '#36c5f0', '#f6d743'], ember: ['#2a0c07', '#e0522d', '#ffca70'],
  });
  function semanticScope() { return [...document.querySelectorAll('.look-inspector, #animationControls, [data-animation-components]')]; }
  function isSemanticControl(control) { return control.matches('input:not([type="hidden"]), select, textarea') && !control.dataset.semanticInstrument; }
  function dispatchSemanticEdit(control, previous = null) { void edit({target: control}, previous); }
  function finiteControlValue(control) { return String(control.value).trim() !== '' && Number.isFinite(Number(control.value)); }
  function normalControlValue(control, value) {
    let next = Number(value); const min = Number(control.min); const max = Number(control.max); const step = Number(control.step);
    if (!Number.isFinite(next)) return null;
    if (Number.isFinite(min)) next = Math.max(min, next);
    if (Number.isFinite(max)) next = Math.min(max, next);
    if (Number.isFinite(step) && step > 0) { const origin = Number.isFinite(min) ? min : 0; next = origin + Math.round((next - origin) / step) * step; }
    return control.step === '1' ? Math.round(next) : Number(next.toFixed(6));
  }
  function controlDefault(control) { return control.dataset.semanticDefault; }
  function controlWords(control) { return String(control.id || '').replace(/([a-z0-9])([A-Z])/g, '$1 $2').split(/[^a-z0-9]+/i).filter(Boolean).map((word) => word.toLowerCase()); }
  function isSeedControl(control) { return controlWords(control).includes('seed'); }
  function addControlMeta(label, control) {
    const defaults = controlDefault(control); if (defaults == null) return;
    const reset = document.createElement('button'); reset.type = 'button'; reset.className = 'semantic-reset'; reset.textContent = 'Reset'; reset.setAttribute('aria-label', `Reset ${label.firstChild.textContent.trim()} to default`);
    reset.addEventListener('click', () => { control.value = defaults; control.checked = defaults === 'true'; syncSemanticControls(); dispatchSemanticEdit(control); });
    const meta = document.createElement('small'); meta.className = 'semantic-default'; meta.textContent = `Default ${defaults}`;
    label.append(meta, reset);
  }
  function decorateNumeric(label, control) {
    if (control.min === '' || control.max === '') return;
    const range = document.createElement('input'); range.type = 'range'; range.className = 'semantic-range'; range.min = control.min; range.max = control.max; range.step = control.step && control.step !== 'any' ? control.step : '0.01'; range.setAttribute('aria-label', `${label.firstChild.textContent.trim()} adjustment`);
    const wrap = document.createElement('span'); wrap.className = 'semantic-number'; control.classList.add('semantic-exact'); control.before(wrap); wrap.append(range, control);
    const record = {kind: 'number', control, range, lastValue: control.value, dragPrevious: null, dragChanged: false}; semanticControls.push(record);
    const commitRangeGesture = () => {
      if (!record.dragPrevious) return;
      const previous = record.dragPrevious; const changed = record.dragChanged; record.dragPrevious = null; record.dragChanged = false;
      if (!changed) return;
      dispatchSemanticEdit(control, previous);
    };
    range.addEventListener('pointerdown', () => { record.dragPrevious = structuredClone(state.scene || defaultScene()); record.dragChanged = false; });
    range.addEventListener('pointerup', () => { const gesture = record.dragPrevious; setTimeout(() => { if (record.dragPrevious === gesture) commitRangeGesture(); }, 0); });
    range.addEventListener('pointercancel', commitRangeGesture);
    range.addEventListener('change', commitRangeGesture);
    range.addEventListener('input', () => {
      const value = normalControlValue(control, range.value); if (value == null) return; const changed = String(value) !== control.value; control.value = String(value); record.lastValue = control.value;
      if (record.dragPrevious) { record.dragChanged ||= changed; state.dirty = true; schedulePreview(); }
      else dispatchSemanticEdit(control);
    });
    const protect = (event) => { if (finiteControlValue(control)) { control.removeAttribute('aria-invalid'); record.lastValue = control.value; range.value = control.value; return; } control.setAttribute('aria-invalid', 'true'); event.stopImmediatePropagation(); };
    control.addEventListener('input', protect); control.addEventListener('change', protect); control.addEventListener('blur', () => { if (!finiteControlValue(control)) control.value = record.lastValue; });
    if (isSeedControl(control)) {
      const randomize = document.createElement('button'); randomize.type = 'button'; randomize.className = 'semantic-randomize'; randomize.textContent = 'Randomize';
      randomize.addEventListener('click', () => { const min = Math.ceil(Number(control.min)); const max = Math.floor(Number(control.max)); const bytes = new Uint32Array(1); crypto.getRandomValues(bytes); control.value = String(min + (bytes[0] % Math.max(1, max - min + 1))); range.value = control.value; record.lastValue = control.value; dispatchSemanticEdit(control); });
      wrap.after(randomize);
    }
    addControlMeta(label, control);
  }
  function decorateChoices(label, control) {
    const options = [...control.options]; const palette = control.id === 'previewPalette';
    if (!palette && options.length > 5) { addControlMeta(label, control); return; }
    const choices = document.createElement('span'); choices.className = palette ? 'semantic-palettes' : 'semantic-choices'; choices.setAttribute('role', 'radiogroup'); choices.setAttribute('aria-label', label.firstChild.textContent.trim());
    const buttons = options.map((option, index) => { const button = document.createElement('button'); button.type = 'button'; button.className = palette ? 'semantic-palette' : 'semantic-choice'; button.dataset.value = option.value; button.setAttribute('role', 'radio');
      if (palette) { const swatch = document.createElement('span'); swatch.className = 'semantic-swatch'; (PALETTE_SWATCHES[option.value] || ['#1b2d35', '#4f8290', '#b9e9dc']).forEach((color) => { const stripe = document.createElement('i'); stripe.style.background = color; swatch.append(stripe); }); button.append(swatch, document.createTextNode(option.textContent)); } else button.textContent = option.textContent;
      button.addEventListener('click', () => { control.value = option.value; syncSemanticControls(); dispatchSemanticEdit(control); });
      button.addEventListener('keydown', (event) => { if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return; event.preventDefault(); const direction = event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 1; const next = buttons[(index + direction + buttons.length) % buttons.length]; next.focus(); next.click(); }); choices.append(button); return button; });
    control.classList.add('semantic-source'); control.after(choices); semanticControls.push({kind: 'choices', control, buttons}); addControlMeta(label, control);
  }
  function decorateSwitch(label, control) {
    const button = document.createElement('button'); button.type = 'button'; button.className = 'semantic-switch'; button.setAttribute('role', 'switch'); button.setAttribute('aria-label', label.firstChild.textContent.trim()); button.addEventListener('click', () => { control.checked = !control.checked; syncSemanticControls(); dispatchSemanticEdit(control); }); control.classList.add('semantic-source'); control.after(button); semanticControls.push({kind: 'switch', control, button}); addControlMeta(label, control);
  }
  function decorateExactNumber(label, control) {
    const record = {kind: 'exact', control, lastValue: control.value}; semanticControls.push(record);
    const protect = (event) => { if (finiteControlValue(control)) { control.removeAttribute('aria-invalid'); record.lastValue = control.value; return; } control.setAttribute('aria-invalid', 'true'); event.stopImmediatePropagation(); };
    control.addEventListener('input', protect); control.addEventListener('change', protect); control.addEventListener('blur', () => { if (!finiteControlValue(control)) control.value = record.lastValue; });
    addControlMeta(label, control);
  }
  function decorateText(label, control) { control.classList.add('semantic-text'); addControlMeta(label, control); }
  function installSemanticControls() {
    semanticScope().forEach((scope) => scope.querySelectorAll('input,select,textarea').forEach((control) => {
      if (!isSemanticControl(control)) return; control.dataset.semanticInstrument = 'true'; control.dataset.semanticDefault = control.type === 'checkbox' ? String(control.checked) : control.value;
      const label = control.closest('label'); if (!label) return; label.classList.add('semantic-control');
      if (control.type === 'checkbox') decorateSwitch(label, control);
      else if (control.tagName === 'SELECT') decorateChoices(label, control);
      else if (control.type === 'number' && control.min !== '' && control.max !== '') decorateNumeric(label, control);
      else if (control.type === 'number') decorateExactNumber(label, control);
      else decorateText(label, control);
    }));
    syncSemanticControls();
  }
  function syncSemanticControls() { semanticControls.forEach((record) => {
    if (record.kind === 'number') { if (finiteControlValue(record.control)) { record.range.value = record.control.value; record.lastValue = record.control.value; record.control.removeAttribute('aria-invalid'); } }
    else if (record.kind === 'exact' && finiteControlValue(record.control)) { record.lastValue = record.control.value; record.control.removeAttribute('aria-invalid'); }
    else if (record.kind === 'choices') record.buttons.forEach((button) => { const selected = button.dataset.value === record.control.value; button.setAttribute('aria-checked', String(selected)); button.tabIndex = selected ? 0 : -1; });
    else if (record.kind === 'switch') record.button.setAttribute('aria-checked', String(record.control.checked));
  }); }
  function presetStatus() {
    let target = $('#animationPresetState');
    if (!target) {
      target = document.createElement('p'); target.id = 'animationPresetState'; target.className = 'status-line'; target.setAttribute('aria-live', 'polite');
      $('#animationControls').before(target);
    }
    return target;
  }
  // These are presentation choices only.  The scene continues to retain every
  // authored value, while the first encounter with a renderer reads as a small
  // playable instrument instead of an undifferentiated settings form.
  const advancedControlSuffixes = Object.freeze({
    conway_life: ['#lifeSeed'],
    fireworks: ['#fireworksCrackle', '#fireworksTwinkle', '#fireworksSeed'],
    lava_lamp: ['#lavaTurbulence', '#lavaGlow', '#lavaSeed'],
    snake: ['#snakeTrails', '#snakeGlow', '#snakeSeed'],
    cyclic_reef: ['#reefPace', '#reefSeed'],
    gradient: ['#gradientSeed'], rainbow: ['#rainbowSeed'], solid: ['#solidSeed'],
    sparkle: ['#sparkleSeed'], wave: ['#waveSeed'],
  });
  function allComponentControlSelectors(componentId) {
    return componentControls[componentId] || atmosphereControls[componentId] || sculptureControls[componentId] || [];
  }
  function disclosureHost(componentId) {
    if (componentControls[componentId] || atmosphereControls[componentId] || sculptureControls[componentId]) {
      const control = $(allComponentControlSelectors(componentId)[0]);
      return control?.closest('.field-grid');
    }
    return null;
  }
  function clearControlDisclosure() {
    document.querySelectorAll('.control-disclosure').forEach((disclosure) => {
      const host = disclosure.parentElement;
      [...disclosure.querySelectorAll(':scope label')].forEach((label) => host.append(label));
      disclosure.remove();
    });
  }
  function buildControlDisclosure(componentId) {
    clearControlDisclosure();
    const selectors = allComponentControlSelectors(componentId);
    const host = disclosureHost(componentId);
    if (!host || !selectors.length) return;
    const controls = selectors.map((selector) => ({selector, label: $(selector)?.closest('label')})).filter(({label}) => label);
    if (!controls.length) return;
    const advanced = new Set(advancedControlSuffixes[componentId] || selectors.filter((selector) => /seed|offset|path|diagnostic|runtime/i.test(selector)));
    const primaryLabels = controls.filter(({selector}) => !advanced.has(selector)).map(({label}) => label);
    const advancedLabels = controls.filter(({selector}) => advanced.has(selector)).map(({label}) => label);
    const disclosure = document.createElement('section');
    disclosure.className = 'control-disclosure';
    disclosure.setAttribute('aria-label', `${componentLabel(componentId)} controls`);
    const primary = document.createElement('div');
    primary.className = 'primary-control-grid';
    primary.setAttribute('aria-label', 'Primary controls');
    primaryLabels.forEach((label) => primary.append(label));
    disclosure.append(primary);
    if (advancedLabels.length) {
      const details = document.createElement('details');
      details.className = 'advanced-controls';
      const summary = document.createElement('summary');
      summary.textContent = 'Advanced tuning';
      const grid = document.createElement('div');
      grid.className = 'advanced-control-grid';
      grid.setAttribute('aria-label', 'Advanced controls');
      advancedLabels.forEach((label) => grid.append(label));
      details.append(summary, grid);
      disclosure.append(details);
    }
    host.prepend(disclosure);
  }
  function syncComponentPresetUI() {
    const componentId = state.scene?.animation?.component_id;
    [...Object.entries(componentPresetTargets), ...Object.entries(atmospherePresetTargets), ...Object.entries(sculpturePresetTargets)].forEach(([id, targetId]) => setComponentRegion(document.getElementById(targetId), id === componentId));
    [...Object.entries(componentControls), ...Object.entries(atmosphereControls), ...Object.entries(sculptureControls)].forEach(([id, selectors]) => selectors.forEach((selector) => {
      const control = $(selector); if (!control) return;
      const visible = id === componentId; const label = control.closest('label') || control;
      label.hidden = !visible; control.disabled = !visible; control.setAttribute('aria-hidden', String(!visible));
    }));
    document.querySelectorAll('[data-animation-components]').forEach((region) => {
      setComponentRegion(region, region.dataset.animationComponents.split(' ').includes(componentId));
    });
    const choices = state.componentPresets[componentId] || [];
    const selected = choices.find((choice) => sameLocalParameters(choice.parameters, state.scene?.animation?.parameters));
    const cards = document.getElementById(componentPresetTargets[componentId] || atmospherePresetTargets[componentId] || sculpturePresetTargets[componentId]);
    if (cards) cards.querySelectorAll('button').forEach((button) => {
      const active = selected?.name === button.textContent; button.setAttribute('aria-pressed', String(active)); button.classList.toggle('active', active);
    });
    const status = presetStatus();
    status.textContent = selected ? `Preset: ${selected.name}.` : choices.length ? `Custom remix — ${componentLabel(componentId)} parameters differ from every authored preset.` : `No authored presets for ${componentLabel(componentId)}.`;
    buildControlDisclosure(componentId);
  }
  function rememberComponentPresets(componentId, presets) { state.componentPresets[componentId] = presets; syncComponentPresetUI(); }
  function clockWidgets(scene = state.scene) { return (scene?.widgets || []).filter((widget) => widget.component?.component_id === 'clock_overlay'); }
  function syncClockPresetUI() {
    const clocks = clockWidgets(); const selected = (state.componentPresets.clock_overlay || []).find((choice) => clocks.length === 1 && sameLocalParameters(choice.parameters, clocks[0].component?.parameters));
    $('#clockPresetCards').querySelectorAll('button').forEach((button) => { const active = selected?.name === button.textContent; button.setAttribute('aria-pressed', String(active)); button.classList.toggle('active', active); });
  }

  function defaultScene() {
    return { schema: 'ledgrid.scene.v2',
      background: {component_id: 'native_aurora', version: 1, provider: 'receiver_native', role: 'background', bundle_digest: nativeDigest, parameters: {gain: number('#backgroundGain'), source_fps: 30, seed: 4201}},
      animation: {component_id: 'aurora_curtains', version: 1, provider: 'python', role: 'animation', parameters: {curtain_density: number('#curtainDensity'), fold_depth: number('#foldDepth'), glow_intensity: number('#glowIntensity'), source_fps: 30, seed: 4201}},
      widgets: [], plants: {effects: {version: 1, active: [], strengths: {}}},
      look: {palette_id: $('#previewPalette').value, pace: number('#wallPace'), presentation_brightness: number('#sceneLuminance')} };
  }
  function syncPlantOpticControl(optic) {
    const enabled = $(optic.enabled).checked;
    const strength = $(optic.strength);
    strength.disabled = !enabled;
    $(optic.value).textContent = `${Math.round(Number(strength.value) * 100)}%`;
  }
  function renderPlantOpticsStatus() {
    const enabled = plantOptics.filter((optic) => $(optic.enabled).checked);
    $('#plantsStatus').textContent = enabled.length ? `${enabled.map(({label}) => label).join(', ')} active as final optics.` : 'No final plant optics active.';
  }
  function applyPlantOptics(next) {
    const effects = next.plants?.effects || {};
    const active = Array.isArray(effects.active) ? effects.active : [];
    const strengths = effects.strengths && typeof effects.strengths === 'object' ? effects.strengths : {};
    const preservedActive = active.filter((id) => !plantOpticIds.has(id));
    const preservedStrengths = Object.fromEntries(Object.entries(strengths).filter(([id]) => !plantOpticIds.has(id)));
    plantOptics.forEach((optic) => {
      if (!$(optic.enabled).checked) return;
      preservedActive.push(optic.id);
      preservedStrengths[optic.id] = number(optic.strength);
    });
    next.plants = {effects: {version: 1, active: preservedActive, strengths: preservedStrengths}};
  }
  function sceneFromControls() {
    const next = structuredClone(state.scene || defaultScene());
    next.background.parameters = {...next.background.parameters, gain: number('#backgroundGain')};
    const choice = $('#animationChoice').value;
    if (choice !== next.animation.component_id) next.animation = sculptureIds.includes(choice)
      ? {component_id: choice, version: 1, provider: 'python', role: 'animation', parameters: sculptureParameters(choice, sculptureSpecs[choice].defaults)}
      : atmosphereIds.includes(choice)
      ? {component_id: choice, version: 1, provider: 'python', role: 'animation', parameters: atmosphereParameters(choice)}
      : ambientIds.includes(choice)
      ? {component_id: choice, version: 1, provider: 'python', role: 'animation', parameters: ambientParameters(choice)}
      : choice === 'conway_life'
      ? {component_id: 'conway_life', version: 1, provider: 'python', role: 'animation', parameters: {seed: number('#lifeSeed'), rule: 'B3/S23', initial_density: .14, generations_per_second: number('#lifeRate'), seed_cells: []}}
      : choice === 'tetris'
        ? {component_id: 'tetris', version: 1, provider: 'python', role: 'animation', parameters: tetrisParameters({seed: 4201, smooth_drop_strength: .6, smooth_drop_max_pieces: 32, render_fps: 150, high_density_render_fps: 150})}
        : choice === 'firefly_synchrony'
          ? {component_id: 'firefly_synchrony', version: 1, provider: 'python', role: 'animation', parameters: fireflyParameters({seed: 7319, coupling_radius: 8})}
          : choice === 'fireworks'
            ? {component_id: 'fireworks', version: 1, provider: 'python', role: 'animation', parameters: fireworksParameters()}
          : choice === 'flame_burst'
            ? {component_id: 'flame_burst', version: 1, provider: 'python', role: 'animation', parameters: flameParameters({seed: 6201})}
          : choice === 'fluid_tank'
            ? {component_id: 'fluid_tank', version: 1, provider: 'python', role: 'animation', parameters: fluidParameters({seed: 6211})}
          : choice === 'canopy_cup'
            ? {component_id: 'canopy_cup', version: 1, provider: 'python', role: 'animation', parameters: canopyParameters({seed: 4242, show_hud: true})}
          : choice === 'cyclic_reef'
            ? {component_id: 'cyclic_reef', version: 1, provider: 'python', role: 'animation', parameters: reefParameters()}
          : choice === 'lava_lamp'
            ? {component_id: 'lava_lamp', version: 1, provider: 'python', role: 'animation', parameters: lavaParameters()}
          : choice === 'snake'
            ? {component_id: 'snake', version: 1, provider: 'python', role: 'animation', parameters: snakeParameters()}
            : choice === 'maze_chase'
              ? {component_id: 'maze_chase', version: 1, provider: 'python', role: 'animation', parameters: mazeParameters({seed: 1980, render_fps: 60})}
              : choice === 'pinball'
                ? {component_id: 'pinball', version: 1, provider: 'python', role: 'animation', parameters: pinballParameters({seed: 95, render_fps: 100})}
                : choice === 'pixel_quest'
                ? {component_id: 'pixel_quest', version: 1, provider: 'python', role: 'animation', parameters: questParameters({seed: 1986, render_fps: 45})}
                : choice === 'ascii_drop'
                  ? {component_id: 'ascii_drop', version: 1, provider: 'python', role: 'animation', parameters: asciiDropParameters({seed: 8088})}
                  : choice === 'emoji'
                    ? {component_id: 'emoji', version: 1, provider: 'python', role: 'animation', parameters: emojiAnimationParameters({seed: 2026})}
                    : choice === 'christmas_tree'
                      ? {component_id: 'christmas_tree', version: 1, provider: 'python', role: 'animation', parameters: treeParameters({twinkle_hz: 1, seed: 1225})}
                      : choice === 'night_train_windows'
                        ? {component_id: 'night_train_windows', version: 1, provider: 'python', role: 'animation', parameters: trainParameters({star_density: .35, seed: 1984})}
        : {component_id: 'aurora_curtains', version: 1, provider: 'python', role: 'animation', parameters: {curtain_density: number('#curtainDensity'), fold_depth: number('#foldDepth'), glow_intensity: number('#glowIntensity'), source_fps: 30, seed: 4201}};
    else if (sculptureIds.includes(choice)) next.animation.parameters = sculptureParameters(choice, next.animation.parameters);
    else if (atmosphereIds.includes(choice)) next.animation.parameters = atmosphereParameters(choice, next.animation.parameters);
    else if (ambientIds.includes(choice)) next.animation.parameters = ambientParameters(choice, next.animation.parameters);
    else if (choice === 'conway_life') next.animation.parameters = {...next.animation.parameters, seed: number('#lifeSeed'), generations_per_second: number('#lifeRate')};
    else if (choice === 'tetris') next.animation.parameters = tetrisParameters(next.animation.parameters);
    else if (choice === 'aurora_curtains') next.animation.parameters = {...next.animation.parameters, curtain_density: number('#curtainDensity'), fold_depth: number('#foldDepth'), glow_intensity: number('#glowIntensity')};
    else if (choice === 'firefly_synchrony') next.animation.parameters = fireflyParameters(next.animation.parameters);
    else if (choice === 'fireworks') next.animation.parameters = fireworksParameters();
    else if (choice === 'flame_burst') next.animation.parameters = flameParameters(next.animation.parameters);
    else if (choice === 'fluid_tank') next.animation.parameters = fluidParameters(next.animation.parameters);
    else if (choice === 'canopy_cup') next.animation.parameters = canopyParameters(next.animation.parameters);
    else if (choice === 'cyclic_reef') next.animation.parameters = reefParameters(next.animation.parameters);
    else if (choice === 'lava_lamp') next.animation.parameters = lavaParameters(next.animation.parameters);
    else if (choice === 'snake') next.animation.parameters = snakeParameters(next.animation.parameters);
    else if (choice === 'maze_chase') next.animation.parameters = mazeParameters(next.animation.parameters);
    else if (choice === 'pinball') next.animation.parameters = pinballParameters(next.animation.parameters);
    else if (choice === 'pixel_quest') next.animation.parameters = questParameters(next.animation.parameters);
    else if (choice === 'ascii_drop') next.animation.parameters = asciiDropParameters(next.animation.parameters);
    else if (choice === 'emoji') next.animation.parameters = emojiAnimationParameters(next.animation.parameters);
    else if (choice === 'christmas_tree') next.animation.parameters = treeParameters(next.animation.parameters);
    else if (choice === 'night_train_windows') next.animation.parameters = trainParameters(next.animation.parameters);
    const clockIndexes = next.widgets.reduce((indexes, widget, index) => widget.component?.component_id === 'clock_overlay' ? [...indexes, index] : indexes, []);
    if (clockIndexes.length === 1) { const clock = next.widgets[clockIndexes[0]]; if (state.lastControl === 'clockEnabled') clock.visible = $('#clockEnabled').checked; else if (['clockFormat', 'clockSeconds', 'clockTimeOffset'].includes(state.lastControl)) clock.component.parameters = clockParameters(clock.component.parameters); else if (state.lastControl === 'clockOffset') clock.placement = {...clock.placement, mode: 'manual', strip_translation: clock.placement.strip_translation ?? 0, led_translation: Math.trunc(number('#clockOffset'))}; }
    else if (clockIndexes.length === 0 && state.lastControl === 'clockEnabled' && $('#clockEnabled').checked) next.widgets.push({id: 'composer-clock', component: {component_id: 'clock_overlay', version: 1, provider: 'python', role: 'widget', parameters: clockParameters()}, visible: true, placement: {mode: 'manual', strip_translation: 0, led_translation: -8}});
    const emojiIndexes = next.widgets.reduce((indexes, widget, index) => widget.component?.component_id === 'emoji_arranger' ? [...indexes, index] : indexes, []);
    if (emojiIndexes.length === 1) { const emoji = next.widgets[emojiIndexes[0]]; if (state.lastControl === 'emojiEnabled') emoji.visible = $('#emojiEnabled').checked; else if (state.lastControl?.startsWith('emoji')) emoji.component.parameters = emojiParameters(); }
    else if (emojiIndexes.length === 0 && state.lastControl === 'emojiEnabled' && $('#emojiEnabled').checked) next.widgets.push(emojiWidget());
    applyPlantOptics(next);
    next.look = {palette_id: $('#previewPalette').value, pace: number('#wallPace'), presentation_brightness: number('#sceneLuminance')};
    return next;
  }
  function applyScene(scene) {
    state.scene = structuredClone(scene);
    const animation = scene.animation || {};
    const parameters = animation.parameters || {};
    if (![...$('#animationChoice').options].some((option) => option.value === animation.component_id)) $('#animationChoice').append(new Option(`${animation.component_id} (preserved)`, animation.component_id));
    $('#animationChoice').value = animation.component_id;
    if (ambientIds.includes(animation.component_id)) {
      const mapping = {gradient: [['gradientDirection','direction','vertical'],['gradientDrift','drift',.22],['gradientMotion','motion',.72],['gradientSeed','seed',6101]], rainbow: [['rainbowBands','bands',1.4],['rainbowTravel','travel',.65],['rainbowDirection','direction',1],['rainbowSeed','seed',6102]], solid: [['solidGlow','glow',.68],['solidBreath','breath',0],['solidSeed','seed',6103]], sparkle: [['sparkleDensity','density',.2],['sparkleLinger','linger',.65],['sparkleTwinkle','twinkle',.72],['sparkleNight','night',.08],['sparkleSeed','seed',6104]], wave: [['waveAxis','axis','vertical'],['waveFrequency','frequency',2],['waveTravel','travel',.45],['waveShape','shape',.8],['waveDirection','direction',1],['waveSeed','seed',6105]]};
      mapping[animation.component_id].forEach(([field, key, fallback]) => { document.getElementById(field).value = parameters[key] ?? fallback; });
    }
    if (sculptureIds.includes(animation.component_id)) sculptureSpecs[animation.component_id].fields.forEach(([name]) => {
      document.getElementById(sculptureControlId(animation.component_id, name)).value = parameters[name] ?? sculptureSpecs[animation.component_id].defaults[name];
    });
    if (atmosphereIds.includes(animation.component_id)) atmosphereSpecs[animation.component_id].fields.forEach(([name]) => {
      document.getElementById(atmosphereControlId(animation.component_id, name)).value = parameters[name] ?? atmosphereSpecs[animation.component_id].defaults[name];
    });
    $('#lifeSeed').value = parameters.seed ?? 4201; $('#lifeRate').value = parameters.generations_per_second ?? 5;
    const tetris = animation.component_id === 'tetris' ? parameters : {};
    $('#tetrisPieces').value = tetris.tetromino_count ?? 5; $('#tetrisFallRate').value = tetris.fall_rate ?? 3; $('#tetrisRisk').value = tetris.bot_imperfection ?? .18; $('#tetrisSmoothDrop').checked = tetris.smooth_drop ?? true;
    const aurora = animation.component_id === 'aurora_curtains' ? parameters : {};
    $('#curtainDensity').value = aurora.curtain_density ?? .56; $('#foldDepth').value = aurora.fold_depth ?? .58;
    $('#glowIntensity').value = aurora.glow_intensity ?? .62; $('#backgroundGain').value = scene.background?.parameters?.gain ?? .62;
    $('#mazeCadence').value = parameters.chase_cadence_hz ?? 9; $('#mazeDifficulty').value = parameters.difficulty ?? .82; $('#mazeRadar').checked = Boolean(parameters.show_ai_targets);
    $('#pinballTicks').value = parameters.table_tick_hz ?? 40.5; $('#pinballChaos').value = parameters.chaos ?? .72;
    $('#questCadence').value = parameters.quest_cadence_hz ?? 12; $('#questDifficulty').value = parameters.difficulty ?? 1; $('#questHud').checked = parameters.show_hud ?? true;
    const ascii = animation.component_id === 'ascii_drop' ? parameters : {}; $('#asciiPhrase').value = ascii.phrase ?? 'HELLO'; $('#asciiStory').value = ascii.story ?? 'terminal'; $('#asciiSpeed').value = ascii.fall_speed ?? 13; $('#asciiDensity').value = ascii.density ?? .45;
    const emojiAnimation = animation.component_id === 'emoji' ? parameters : {}; $('#emojiFace').value = emojiAnimation.face ?? 'smile'; $('#emojiMood').value = emojiAnimation.mood ?? 'golden'; $('#emojiAnimationPulse').value = emojiAnimation.pulse_hz ?? .8; $('#emojiAnimationScale').value = emojiAnimation.scale ?? 1;
    const tree = animation.component_id === 'christmas_tree' ? parameters : {}; $('#treeSeason').value = tree.season ?? 'classic'; $('#treeHeight').value = tree.tree_height ?? 58; $('#treeSnowfall').value = tree.snowfall ?? .35;
    const train = animation.component_id === 'night_train_windows' ? parameters : {}; $('#trainRoute').value = train.route ?? 'sleeper'; $('#trainSpeed').value = train.travel_speed ?? 1; $('#trainGlow').value = train.window_glow ?? .65;
    const firefly = animation.component_id === 'firefly_synchrony' ? parameters : {};
    $('#fireflyPopulation').value = firefly.population ?? 100; $('#fireflySynchrony').value = firefly.synchrony ?? .85;
    $('#fireflyWandering').value = firefly.wandering ?? .55; $('#fireflyPulseSoftness').value = firefly.pulse_softness ?? .5; $('#fireflyMeadowGlow').value = firefly.meadow_glow ?? .12;
    const fireworks = animation.component_id === 'fireworks' ? parameters : {};
    $('#fireworksCadence').value = fireworks.launch_cadence ?? 1.15; $('#fireworksPopulation').value = fireworks.shell_population ?? 54; $('#fireworksBurstSize').value = fireworks.burst_size ?? .29; $('#fireworksStyle').value = fireworks.burst_style ?? 'mixed'; $('#fireworksGravity').value = fireworks.gravity ?? .38; $('#fireworksTrails').value = fireworks.trails ?? .72; $('#fireworksCrackle').value = fireworks.crackle ?? .24; $('#fireworksTwinkle').value = fireworks.twinkle ?? .35; $('#fireworksSeed').value = fireworks.seed ?? 1776;
    const flame = animation.component_id === 'flame_burst' ? parameters : {}; $('#flameCadence').value = flame.ignition_cadence ?? .9; $('#flameSize').value = flame.flare_size ?? .42; $('#flameEmbers').value = flame.ember_linger ?? .45; $('#flameFlicker').value = flame.flicker ?? .35;
    const fluid = animation.component_id === 'fluid_tank' ? parameters : {}; $('#fluidFlow').value = fluid.flow_rate ?? .72; $('#fluidCurrent').value = fluid.current ?? .35; $('#fluidBubbles').value = fluid.bubble_lift ?? .42; $('#fluidSurface').value = fluid.surface_energy ?? .38;
    const lava = animation.component_id === 'lava_lamp' ? parameters : {};
    $('#lavaBlobCount').value = lava.blob_count ?? 7; $('#lavaBlobScale').value = lava.blob_scale ?? 1; $('#lavaViscosity').value = lava.viscosity ?? .68; $('#lavaHeat').value = lava.heat ?? .72; $('#lavaTurbulence').value = lava.turbulence ?? .24; $('#lavaGlow').value = lava.glow ?? .58; $('#lavaSeed').value = lava.seed ?? 1977;
    const canopy = animation.component_id === 'canopy_cup' ? parameters : {};
    $('#canopyWorld').value = canopy.world_theme ?? 'tournament'; $('#canopyHeats').value = canopy.qualifying_heats ?? 7; $('#canopyCourse').value = canopy.course_difficulty ?? 1; $('#canopyDensity').value = canopy.enemy_density ?? .55; $('#canopyRivalry').value = canopy.rivalry ?? .55; $('#canopyPowerups').value = canopy.powerup_rate ?? .6;
    const reef = animation.component_id === 'cyclic_reef' ? parameters : {};
    $('#reefSpecies').value = reef.species_count ?? 5; $('#reefThreshold').value = reef.takeover_threshold ?? 2; $('#reefMutation').value = reef.mutation ?? .002; $('#reefGrazers').value = reef.grazers ?? 8; $('#reefGlow').value = reef.boundary_glow ?? .55; $('#reefTopology').value = reef.topology ?? 'wrap'; $('#reefPace').value = reef.pace ?? 1; $('#reefSeed').value = reef.seed ?? 13100;
    const snake = animation.component_id === 'snake' ? parameters : {};
    $('#snakeCadence').value = snake.move_cadence ?? 9; $('#snakeCount').value = snake.snake_count ?? 3; $('#snakeFood').value = snake.food_count ?? 5; $('#snakeGrowth').value = snake.growth_per_food ?? 3; $('#snakeRules').value = snake.ruleset ?? 'wrap'; $('#snakeObstacles').value = snake.obstacles ?? 'none'; $('#snakeTrails').value = snake.trails ?? .72; $('#snakeGlow').value = snake.glow ?? .55; $('#snakeSeed').value = snake.seed ?? 1976;
    const clocks = (scene.widgets || []).filter((widget) => widget.component?.component_id === 'clock_overlay');
    const clock = clocks.length === 1 ? clocks[0] : null;
    const clockParameters = clock?.component?.parameters || {};
    $('#clockEnabled').checked = Boolean(clock?.visible); $('#clockFormat').value = clockParameters.format_24h ? '24' : '12'; $('#clockSeconds').checked = clockParameters.show_seconds ?? true; $('#clockTimeOffset').value = clockParameters.clock_offset_minutes ?? 0; $('#clockOffset').value = clock?.placement?.led_translation ?? -8;
    const emojis = (scene.widgets || []).filter((widget) => widget.component?.component_id === 'emoji_arranger');
    const emoji = emojis.length === 1 ? emojis[0] : null; const message = emoji?.component?.parameters || {};
    $('#emojiEnabled').checked = Boolean(emoji?.visible); $('#emojiText').value = message.text ?? 'HI🔥'; $('#emojiXOffset').value = message.x_offset ?? 8; $('#emojiYOffset').value = message.y_offset ?? 3;
    $('#emojiCharSpacing').value = message.char_spacing ?? 1; $('#emojiLineSpacing').value = message.line_spacing ?? 1; $('#emojiScrollSpeed').value = message.scroll_speed ?? 0; $('#emojiPulseSpeed').value = message.pulse_speed ?? .5;
    if (clocks.length > 1) placementWarning({warning: 'Multiple Clock widgets are preserved; this inspector edits only a scene with one Clock widget.'});
    $('#previewPalette').value = scene.look?.palette_id ?? 'mist'; $('#wallPace').value = scene.look?.pace ?? .7; $('#sceneLuminance').value = scene.look?.presentation_brightness ?? .82;
    const plantEffects = scene.plants?.effects || {}; const activeOptics = new Set(plantEffects.active || []); const strengths = plantEffects.strengths || {};
    plantOptics.forEach((optic) => { $(optic.enabled).checked = activeOptics.has(optic.id); $(optic.strength).value = strengths[optic.id] ?? .5; syncPlantOpticControl(optic); });
    renderPlantOpticsStatus();
    if (clocks.length <= 1) placementWarning(); syncClockPresetUI();
    syncComponentPresetUI();
    syncSemanticControls();
  }
  function placementWarning(placement = null) { const warning = $('#widgetWarning'); warning.hidden = !placement?.warning; warning.textContent = placement?.warning || ''; }
  function drawFrame(frame) {
    if (!frame || frame.encoding !== 'rgb_u8_base64') throw new Error('Preview returned an unsupported frame.');
    const bytes = Uint8Array.from(atob(frame.pixels), (character) => character.charCodeAt(0));
    const canvas = $('#scenePreview'); const context = canvas.getContext('2d'); const image = context.createImageData(frame.width, frame.height);
    for (let strip = 0; strip < frame.width; strip += 1) for (let led = 0; led < frame.height; led += 1) { const source = (strip * frame.height + led) * 3; const target = ((frame.height - 1 - led) * frame.width + strip) * 4; image.data[target] = bytes[source]; image.data[target + 1] = bytes[source + 1]; image.data[target + 2] = bytes[source + 2]; image.data[target + 3] = 255; }
    context.putImageData(image, 0, 0);
  }
  async function preview(scene) { let response; try { response = await fetch(`${api}/preview`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({origin: 'composer', scene})}); } catch (_) { const error = new Error('Local Composer server unavailable.'); error.previewUnavailable = true; throw error; } const body = await response.json(); if (!response.ok) { const error = new Error(body.error || 'Preview could not render.'); error.previewUnavailable = response.status >= 500; throw error; } return body; }
  const previewScheduler = new window.ComposerPreviewScheduler({
    request: preview,
    isVisible: () => !document.hidden,
    onFrame: (body) => { drawFrame(body.frame); $('#previewIdentity').textContent = identity(body.basis); $('#previewStatus').textContent = 'Installed final runtime frame.'; placementWarning(Object.values(body.widget_placements || {}).find((placement) => placement.warning)); },
    onError: (error) => { $('#previewStatus').textContent = error.message || 'Preview could not render.'; if (error.previewUnavailable) window.dispatchEvent(new Event('composer-server-unavailable')); },
  });
  const vibeForPalette = Object.freeze({mist: 'quiet', neutral: 'neutral', spectrum: 'vivid', ember: 'cozy'});
  const paletteForVibe = Object.freeze({quiet: 'mist', neutral: 'neutral', vivid: 'spectrum', celebration: 'spectrum', cozy: 'ember'});
  function managedWallComponent(componentId, role = 'background') {
    return state.wall.bootstrap?.components?.find((component) => (
      component.provider === 'python' && component.plugin_id === componentId && component.role === role
      && component.browser_capabilities?.managed_identity
    ));
  }
  function wallComponentReference(componentId, parameters, role = 'background') {
    const component = managedWallComponent(componentId, role);
    const managed = component?.browser_capabilities?.managed_identity;
    if (!managed) throw new Error(`${componentId} is not activation-ready on this wall.`);
    const managedParameters = structuredClone(parameters || {});
    // Older starter/saved scenes carry a Clock-local color.  Clock now derives
    // color from the Scene palette, so do not send that retired field through
    // the strict managed activation schema.
    if (componentId === 'clock_overlay') delete managedParameters.color;
    return {
      provider: managed.provider, component_id: managed.component_id,
      component_digest: managed.component_digest, runtime_digest: managed.runtime_digest,
      parameter_schema_version: managed.parameter_schema_version,
      parameters: managedParameters,
    };
  }
  function observedWallIdentity(scene = state.wall.scene, observation = state.wall.observation) {
    const active = observation?.active_identity?.scene_identity;
    if (!scene || !active?.digest) return null;
    return {revision: Number.isSafeInteger(scene.revision) ? scene.revision : active.revision, digest: active.digest};
  }
  function wallStatus(lastError = null) {
    const observation = state.wall.observation;
    const selected = observedWallIdentity();
    const running = Boolean(observation?.is_running && selected);
    return {
      connected: Boolean(observation?.controller_session_id), running, armed: running,
      current: selected, desired: selected, observed: running ? selected : null,
      revision: Number(observation?.controller_state_revision || selected?.revision || 0),
      last_error: lastError,
    };
  }
  function composerSceneFromWall(scene, observation) {
    const selected = scene?.background?.provider === 'python' ? scene.background : scene?.known_python_fallback;
    if (!selected?.plugin_id) throw new Error('The selected wall scene has no editable Python animation.');
    const parameters = {...structuredClone(selected.resolved_parameters || {}), ...structuredClone(selected.parameter_overrides || {})};
    const clock = (scene.overlays || []).find((overlay) => overlay.slot_id === 'clock_overlay' && overlay.enabled);
    const speedBaseline = Number(state.wall.bootstrap?.global_control_contract?.operator_speed_baseline || .3);
    const vibeId = observation?.vibe?.state?.vibe_id || observation?.vibe?.vibe_id || 'neutral';
    const plantModifiers = observation?.plant_modifiers || {version: 1, active: [], strengths: {}};
    return {
      schema: 'ledgrid.scene.v2',
      background: {
        component_id: 'native_aurora', version: 1, provider: 'receiver_native', role: 'background',
        bundle_digest: nativeDigest,
        parameters: {gain: 0, source_fps: 30, seed: Math.trunc(Number(parameters.seed || 0))},
      },
      animation: {component_id: selected.plugin_id, version: 1, provider: 'python', role: 'animation', parameters},
      widgets: clock ? [{
        id: 'clock', visible: true,
        component: {
          component_id: 'clock_overlay', version: 1, provider: 'python', role: 'widget',
          parameters: {...structuredClone(clock.component?.resolved_parameters || {}), ...structuredClone(clock.component?.parameter_overrides || {})},
        },
        placement: {
          mode: 'manual', strip_translation: Math.trunc(Number(clock.placement?.strip_translation || 0)),
          led_translation: Math.trunc(Number(clock.placement?.led_translation ?? -8)),
        },
      }] : [],
      plants: {effects: {
        version: 1, active: structuredClone(plantModifiers.active || []),
        strengths: structuredClone(plantModifiers.strengths || {}),
      }},
      look: {
        palette_id: paletteForVibe[vibeId] || 'neutral',
        pace: Math.max(0, Math.min(2, Number(observation?.animation_speed_scale || speedBaseline) / speedBaseline)),
        presentation_brightness: Math.max(0, Math.min(2, Number(observation?.brightness ?? 128) / 255)),
      },
    };
  }
  function browserSceneForWall(scene) {
    const background = wallComponentReference(scene.animation.component_id, scene.animation.parameters);
    const layers = [];
    const clock = (scene.widgets || []).find((widget) => widget.visible && widget.component?.component_id === 'clock_overlay');
    if (clock) layers.push({
      role: 'clock', component: wallComponentReference('clock_overlay', clock.component.parameters, 'overlay'),
      enabled: true, opacity: 255, blend_mode: 'source_over',
    });
    const profileDigest = state.wall.observation?.installation_profile_digest
      || state.wall.bootstrap?.installation_profile?.digest;
    if (!/^[0-9a-f]{64}$/.test(profileDigest || '')) throw new Error('The wall has no managed installation profile.');
    return {
      schema: 'ledgrid.browser-scene', schema_version: 1,
      revision: Math.max(1, Number(state.wall.scene?.revision || 1)),
      background, layers, installation_profile: {digest: profileDigest}, fallback: structuredClone(background),
    };
  }
  function globalSettingsForWall(scene, power) {
    const observation = state.wall.observation || {};
    const bootstrap = state.wall.bootstrap || {};
    const lookUnchanged = Boolean(state.wall.adoptedLook
      && JSON.stringify(scene.look || {}) === JSON.stringify(state.wall.adoptedLook));
    const vibeId = lookUnchanged && state.wall.adoptedVibeId
      ? state.wall.adoptedVibeId
      : (vibeForPalette[scene.look?.palette_id] || 'neutral');
    const profile = bootstrap.vibe_profiles?.find((item) => item.vibe_id === vibeId)
      || bootstrap.vibe_profiles?.find((item) => item.vibe_id === 'neutral');
    if (!profile?.resolved_profile_digest) throw new Error('The selected wall vibe is unavailable.');
    const allowedModifiers = new Set(bootstrap.global_control_contract?.plant_modifier_ids || []);
    const active = (scene.plants?.effects?.active || []).filter((id) => allowedModifiers.has(id));
    const strengths = Object.fromEntries(active.map((id) => [id, Math.max(0, Math.min(1, Number(scene.plants.effects.strengths?.[id] ?? .5)))]));
    const revision = Number(observation.active_identity?.global_settings_identity?.revision ?? observation.controller_state_revision);
    if (!Number.isSafeInteger(revision) || revision < 0) throw new Error('The wall settings observation has no usable revision.');
    const baseline = Number(bootstrap.global_control_contract?.operator_speed_baseline || .3);
    const brightness = lookUnchanged
      ? Number(observation.brightness ?? 128)
      : Math.round(Number(scene.look?.presentation_brightness ?? .5) * 255);
    const animationSpeed = lookUnchanged
      ? Number(observation.animation_speed_scale ?? baseline)
      : baseline * Math.max(0, Math.min(2, Number(scene.look?.pace ?? 1)));
    return {
      schema: 'ledgrid.global-settings-state', schema_version: 1, revision,
      vibe: {vibe_id: profile.vibe_id, profile_version: profile.profile_version, resolved_profile_digest: profile.resolved_profile_digest},
      plant_modifiers: {version: 1, active, strengths},
      output: {
        power: Boolean(power), brightness: Math.max(0, Math.min(255, Math.round(brightness))),
        animation_speed_scale: Math.max(0, animationSpeed),
        target_fps: Math.max(1, Math.min(200, Math.round(Number(observation.target_fps || 30)))),
      },
    };
  }
  async function refreshWallStatus({adopt = false} = {}) {
    if (!state.wall.bootstrap) state.wall.bootstrap = await requestJson('/api/v1/composer/bootstrap');
    const priorRevision = state.wall.scene?.revision;
    const [scenePayload, observation] = await Promise.all([
      requestJson('/api/v1/scene'), requestJson('/api/v1/composer/settings/observed'),
    ]);
    state.wall.scene = scenePayload.scene || null;
    state.wall.observation = observation;
    const shouldAdopt = Boolean(scenePayload.scene && (adopt || (
      !state.wall.dirty && priorRevision != null && priorRevision !== scenePayload.scene.revision
    )));
    if (shouldAdopt) {
      const current = composerSceneFromWall(scenePayload.scene, observation);
      if (![...$('#animationChoice').options].some((option) => option.value === current.animation.component_id)) {
        $('#animationChoice').append(new Option(current.animation.component_id.replaceAll('_', ' '), current.animation.component_id));
      }
      state.scene = current; state.selection = null; state.history = []; state.redo = []; state.dirty = false; state.wall.dirty = false;
      state.wall.adoptedLook = structuredClone(current.look);
      state.wall.adoptedVibeId = observation?.vibe?.state?.vibe_id || observation?.vibe?.vibe_id || null;
      $('#sceneName').value = `Currently playing · ${current.animation.component_id.replaceAll('_', ' ')}`;
      applyScene(current); schedulePreview();
    }
    renderStatus(wallStatus());
    return scenePayload;
  }
  function renderStatus(payload) {
    const status = payload.status || payload; state.status = status;
    state.revision = Math.max(state.revision || 0, status.revision || 0);
    $('#connectionState').textContent = status.connected ? (status.running ? 'Connected · output running' : 'Connected · output stopped') : 'Disconnected';
    $('#observedIdentity').textContent = identity(status.observed); $('#diagnosticObserved').textContent = identity(status.observed); $('#desiredIdentity').textContent = identity(status.desired); $('#sceneRevision').textContent = String(status.revision ?? 0);
    $('#sceneIdentity').textContent = identity(status.current); $('#saveState').textContent = state.dirty ? 'Unsaved changes' : (state.selection?.kind === 'look' ? 'Saved look' : 'Current scene');
    $('#liveAction').textContent = status.running && status.armed && status.current && !state.wall.dirty ? 'Stop' : 'Go Live';
    $('#operationMessage').textContent = state.authoredValidationError || status.last_error || (status.running ? (state.wall.dirty ? 'Draft differs from the wall. Use Go Live to publish it.' : 'Exact controller observation is live.') : 'Use Go Live to start this scene.');
  }
  async function acknowledgeUndo(revision) { state.history = []; state.redo = []; await fetch(`${api}/undo-ack`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({client_id: clientId, revision})}); }
  function schedulePreview() { const generation = ++state.previewGeneration; const candidate = sceneFromControls(); state.scene = candidate; previewScheduler.submitAuthored(candidate, {generation}).catch((error) => { if (generation === state.previewGeneration) $('#previewStatus').textContent = error.message; }); }
  function remember(previous) { state.history.push(previous); if (state.history.length > 40) state.history.shift(); state.redo = []; }
  async function submit(scene, {builtin = false, rememberEdit = false, previous = null} = {}) {
    if (rememberEdit) remember(previous || structuredClone(state.scene || defaultScene()));
    state.scene = scene; state.wall.dirty = true; syncComponentPresetUI(); schedulePreview(); state.submitting = true;
    const endpoint = builtin ? '/built-ins/open' : '/scene';
    const body = builtin ? {scene, client_id: clientId, mutation_id: newUuid(), client_sequence: ++state.sequence} : {origin: 'composer', scene, client_id: clientId, mutation_id: newUuid(), client_sequence: ++state.sequence};
    try { const response = await fetch(`${api}${endpoint}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}); const result = await response.json(); state.authoredValidationError = response.ok ? null : (result.error || 'Current scene could not be accepted.'); if (state.status) renderStatus(state.status); if (!response.ok) throw Object.assign(new Error(state.authoredValidationError), {result}); return result; }
    finally { state.submitting = false; }
  }
  async function edit(event, priorScene = null) { state.lastControl = event?.target?.id || null; const previous = priorScene || structuredClone(state.scene || defaultScene()); const next = sceneFromControls(); state.dirty = true; try { await submit(next, {rememberEdit: true, previous}); } catch (error) { state.scene = previous; applyScene(previous); $('#operationMessage').textContent = error.message; } }
  async function loadFireworksPresets() {
    try {
      const response = await fetch(`${api}/components/fireworks/presets`); const body = await response.json(); if (!response.ok) throw new Error(body.error);
      const target = $('#fireworksPresetCards'); target.replaceChildren();
      body.presets.forEach((preset) => { const button = document.createElement('button'); button.type = 'button'; button.className = 'button secondary'; button.textContent = preset.name; button.title = preset.description || preset.name; button.addEventListener('click', async () => { if (state.scene?.animation?.component_id !== 'fireworks') return; const previous = structuredClone(state.scene); const next = structuredClone(state.scene); next.animation.parameters = preset.parameters; state.dirty = true; applyScene(next); try { await submit(next, {rememberEdit: true}); } catch (error) { state.scene = previous; applyScene(previous); $('#operationMessage').textContent = error.message; } }); target.append(button); });
      rememberComponentPresets('fireworks', body.presets);
    } catch (error) { $('#operationMessage').textContent = error.message || 'Fireworks presets are unavailable.'; }
  }
  async function loadSnakePresets() {
    try {
      const response = await fetch(`${api}/components/snake/presets`); const body = await response.json(); if (!response.ok) throw new Error(body.error);
      const target = $('#snakePresetCards'); target.replaceChildren();
      body.presets.forEach((preset) => { const button = document.createElement('button'); button.type = 'button'; button.className = 'button secondary'; button.textContent = preset.name; button.title = preset.description || preset.name; button.addEventListener('click', async () => { if (state.scene?.animation?.component_id !== 'snake') return; const previous = structuredClone(state.scene); const next = structuredClone(state.scene); next.animation.parameters = preset.parameters; state.dirty = true; applyScene(next); try { await submit(next, {rememberEdit: true}); } catch (error) { state.scene = previous; applyScene(previous); $('#operationMessage').textContent = error.message; } }); target.append(button); });
      rememberComponentPresets('snake', body.presets);
      document.querySelectorAll('[data-snake-control]').forEach((node) => { node.addEventListener('change', edit); node.addEventListener('input', edit); });
    } catch (error) { $('#operationMessage').textContent = error.message || 'Snake presets are unavailable.'; }
  }
  async function loadLavaPresets() {
    try {
      const response = await fetch(`${api}/components/lava_lamp/presets`); const body = await response.json(); if (!response.ok) throw new Error(body.error);
      const target = $('#lavaPresetCards'); target.replaceChildren();
      body.presets.forEach((preset) => { const button = document.createElement('button'); button.type = 'button'; button.className = 'button secondary'; button.textContent = preset.name; button.title = preset.description || preset.name; button.addEventListener('click', async () => { if (state.scene?.animation?.component_id !== 'lava_lamp') return; const previous = structuredClone(state.scene); const next = structuredClone(state.scene); next.animation.parameters = {...preset.parameters, ...lavaInteractionParameters(next.animation.parameters)}; state.dirty = true; applyScene(next); try { await submit(next, {rememberEdit: true}); } catch (error) { state.scene = previous; applyScene(previous); $('#operationMessage').textContent = error.message; } }); target.append(button); });
      rememberComponentPresets('lava_lamp', body.presets);
    } catch (error) { $('#operationMessage').textContent = error.message || 'Lava Lamp presets are unavailable.'; }
  }
  async function loadReefPresets() {
    try {
      const response = await fetch(`${api}/components/cyclic_reef/presets`); const body = await response.json(); if (!response.ok) throw new Error(body.error);
      const target = $('#reefPresetCards'); target.replaceChildren();
      body.presets.forEach((preset) => { const button = document.createElement('button'); button.type = 'button'; button.className = 'button secondary'; button.textContent = preset.name; button.title = preset.description || preset.name; button.addEventListener('click', async () => { if (state.scene?.animation?.component_id !== 'cyclic_reef') return; const previous = structuredClone(state.scene); const next = structuredClone(state.scene); next.animation.parameters = preset.parameters; state.dirty = true; applyScene(next); try { await submit(next, {rememberEdit: true}); } catch (error) { state.scene = previous; applyScene(previous); $('#operationMessage').textContent = error.message; } }); target.append(button); });
      rememberComponentPresets('cyclic_reef', body.presets);
      document.querySelectorAll('[data-reef-control]').forEach((node) => { node.addEventListener('change', edit); node.addEventListener('input', edit); });
    } catch (error) { $('#operationMessage').textContent = error.message || 'Cyclic Reef presets are unavailable.'; }
  }
  async function loadClockPresets() {
    try {
      const response = await fetch(`${api}/components/clock_overlay/presets`); const body = await response.json(); if (!response.ok) throw new Error(body.error);
      const target = $('#clockPresetCards'); target.replaceChildren();
      body.presets.forEach((preset) => { const button = document.createElement('button'); button.type = 'button'; button.className = 'button secondary'; button.textContent = preset.name; button.title = preset.description || preset.name; button.addEventListener('click', async () => { const clocks = clockWidgets(); if (clocks.length > 1) { placementWarning({warning: 'Multiple Clock widgets are preserved; choose one before applying a Clock preset.'}); return; } const previous = structuredClone(state.scene || defaultScene()); const next = structuredClone(state.scene || defaultScene()); const clock = clockWidgets(next)[0]; if (clock) clock.component.parameters = preset.parameters; else next.widgets.push({id: 'composer-clock', component: {component_id: 'clock_overlay', version: 1, provider: 'python', role: 'widget', parameters: preset.parameters}, visible: true, placement: {mode: 'manual', strip_translation: 0, led_translation: -8}}); state.dirty = true; applyScene(next); try { await submit(next, {rememberEdit: true}); } catch (error) { state.scene = previous; applyScene(previous); $('#operationMessage').textContent = error.message; } }); target.append(button); });
      state.componentPresets.clock_overlay = body.presets; syncClockPresetUI();
    } catch (error) { $('#operationMessage').textContent = error.message || 'Clock presets are unavailable.'; }
  }
  function existingPresetCards(componentId) {
    const targetId = componentPresetTargets[componentId] || `${componentId.replaceAll('_', '-')}-preset-cards`;
    let target = document.getElementById(targetId);
    if (!target) {
      target = document.createElement('div'); target.id = targetId; target.className = 'operation-row'; target.setAttribute('aria-label', `${componentId} presets`);
      $('#animationControls').before(target);
    }
    return target;
  }
  async function loadExistingComponentPresets(componentId) {
    try {
      const response = await fetch(`${api}/components/${componentId}/presets`); const body = await response.json(); if (!response.ok) throw new Error(body.error);
      const target = existingPresetCards(componentId); target.replaceChildren();
      body.presets.forEach((preset) => { const button = document.createElement('button'); button.type = 'button'; button.className = 'button secondary'; button.textContent = preset.name; button.title = preset.description || preset.name; button.addEventListener('click', async () => { if (state.scene?.animation?.component_id !== componentId) return; const previous = structuredClone(state.scene); const next = structuredClone(state.scene); next.animation.parameters = preset.parameters; state.dirty = true; applyScene(next); try { await submit(next, {rememberEdit: true}); } catch (error) { state.scene = previous; applyScene(previous); $('#operationMessage').textContent = error.message; } }); target.append(button); });
      rememberComponentPresets(componentId, body.presets);
    } catch (error) { $('#operationMessage').textContent = error.message || `${componentId} presets are unavailable.`; }
  }
  function filteredItems() { const query = state.query.toLocaleLowerCase(); let items = state.library.items.filter((item) => item.name.toLocaleLowerCase().includes(query)); if (state.filter === 'favorites') items = items.filter((item) => state.library.favorites.some((favorite) => favorite.kind === item.kind && favorite.id === item.id)); else if (state.filter !== 'all') items = items.filter((item) => item.kind === state.filter); return items; }
  const primaryInstruments = Object.freeze({
    lava_lamp: {label: 'Lava Lamp', accepted: 'Lava stirred on the installed final scene.', rejected: 'Lava Lamp did not accept that stir.'},
    flame_burst: {label: 'Flame Burst', accepted: 'Flame Burst ignited on the installed final scene.', rejected: 'Flame Burst did not accept that ignition.'},
    fluid_tank: {label: 'Fluid Tank', accepted: 'Fluid Tank pulsed on the installed final scene.', rejected: 'Fluid Tank did not accept that flow pulse.'},
  });
  async function triggerInstrumentAtPointer(event) {
    const componentId = state.scene?.animation?.component_id;
    const trigger = primaryInstruments[componentId];
    if (event.button !== 0 || event.isPrimary === false || !trigger) return;
    const status = state.status;
    if (!status?.running || !status?.armed || !status?.current) return;
    const canvas = $('#scenePreview'); const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const x = Math.min(32.999, Math.max(0, (event.clientX - rect.left) * 33 / rect.width));
    const y = Math.min(137.999, Math.max(0, (1 - (event.clientY - rect.top) / rect.height) * 138));
    try {
      const response = await fetch('/api/interaction', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({kind: 'primary', x, y, strength: 1})});
      const body = await response.json(); if (!response.ok || body.accepted !== true) throw new Error(body.error || trigger.rejected);
      $('#previewStatus').textContent = trigger.accepted;
    } catch (error) { $('#operationMessage').textContent = error.message || `${trigger.label} trigger unavailable.`; }
  }
  function renderLibrary() { const target = $('#libraryList'); target.replaceChildren(); const items = filteredItems(); $('#libraryEmpty').hidden = items.length > 0; items.forEach((item) => { const button = document.createElement('button'); button.type = 'button'; button.className = 'button'; button.setAttribute('aria-current', String(state.selection?.kind === item.kind && state.selection?.id === item.id)); button.innerHTML = `<span>${item.name}</span><span class="library-kind">${item.kind === 'starter' ? 'Built-in' : 'Saved'}</span>`; button.addEventListener('click', () => openItem(item)); const row = document.createElement('li'); row.append(button); target.append(row); }); document.querySelectorAll('[data-library-filter]').forEach((button) => button.classList.toggle('active', button.dataset.libraryFilter === state.filter)); }
  async function openItem(item) {
    try {
      if (item.kind === 'look') { const response = await fetch(`${api}/looks/${item.id}/open`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({client_id: clientId, mutation_id: newUuid(), client_sequence: ++state.sequence})}); const result = await response.json(); if (!response.ok) throw new Error(result.error); state.scene = result.look.scene; $('#sceneName').value = result.look.name; }
      else { const starterResponse = await fetch(`${api}/starters/${item.id}`); const starter = (await starterResponse.json()).starter; applyScene(starter.scene); await submit(starter.scene, {builtin: true}); $('#sceneName').value = starter.name; }
      state.selection = item; state.history = []; state.redo = []; state.dirty = false; applyScene(state.scene); schedulePreview(); renderLibrary(); renderStatus(state.status);
    } catch (error) { $('#operationMessage').textContent = error.message || 'Scene could not be opened.'; }
  }
  function focusable(dialog) { return [...dialog.querySelectorAll('button,[href],input,select,textarea,[tabindex]:not([tabindex="-1"])')].filter((node) => !node.disabled); }
  function openDialog(dialog) { const prior = document.activeElement; dialog.showModal(); focusable(dialog)[0]?.focus(); const trap = (event) => { if (event.key === 'Escape') { event.preventDefault(); dialog.close(); } if (event.key !== 'Tab') return; const nodes = focusable(dialog); const first = nodes[0]; const last = nodes.at(-1); if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); } }; dialog.addEventListener('keydown', trap); dialog.addEventListener('close', () => { dialog.removeEventListener('keydown', trap); prior?.focus(); }, {once: true}); }
  function blockers(result) { const list = $('#readinessList'); list.replaceChildren(); (result.blockers || [{message: result.error || 'Go Live is not ready.', recovery: 'Review connection and current scene.'}]).forEach((blocker) => { const item = document.createElement('li'); item.textContent = `${blocker.message} ${blocker.recovery || ''}`; list.append(item); }); openDialog($('#readinessDialog')); }
  async function waitForExactActivation(accepted, controllerSessionId) {
    const statusUrl = accepted.status_url || `/api/v1/scene/activations/${encodeURIComponent(accepted.activation_id)}`;
    const startedAt = Date.now();
    while (Date.now() - startedAt < 120000) {
      const status = await requestJson(statusUrl);
      if (status.activation_id !== accepted.activation_id) throw new Error('The activation acknowledgement changed identity.');
      if (status.controller?.session_id !== controllerSessionId) throw new Error('The wall restarted before it observed this scene.');
      if (status.phase === 'active') {
        if (JSON.stringify(status.requested_identity) !== JSON.stringify(status.observed_identity)) {
          throw new Error('The wall did not observe the exact checked scene.');
        }
        return status;
      }
      if (['failed', 'timed_out', 'rolled_back'].includes(status.phase)) throw new Error(status.error || `Activation ${status.phase}.`);
      await sleep(500);
    }
    throw new Error('The wall did not acknowledge this scene in time.');
  }
  async function guardedWallActivation(scene, power) {
    await refreshWallStatus();
    const browserScene = browserSceneForWall(scene);
    const globalSettings = globalSettingsForWall(scene, power);
    const checked = await requestJson('/api/v1/scene/checks', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({scene: browserScene, global_settings: globalSettings}),
    });
    if (!checked.check_token || !checked.basis?.controller) throw new Error('The wall returned an incomplete Check authorization.');
    const accepted = await requestJson('/api/v1/scene', {
      method: 'PUT', headers: {'Content-Type': 'application/json', 'Idempotency-Key': newUuid()},
      body: JSON.stringify({
        check_token: checked.check_token,
        expected_controller_session_id: checked.basis.controller.session_id,
        expected_controller_state_revision: checked.basis.controller.state_revision,
        scene: browserScene, global_settings: globalSettings,
      }),
    });
    await waitForExactActivation(accepted, checked.basis.controller.session_id);
    state.dirty = false; state.wall.dirty = false;
    await refreshWallStatus({adopt: true});
  }
  async function liveAction() {
    if (state.wall.activating || !state.scene) return;
    const stop = Boolean(state.status?.running && state.status?.current && !state.wall.dirty);
    state.wall.activating = true; $('#liveAction').disabled = true; $('#liveAction').textContent = stop ? 'Stopping…' : 'Checking…';
    $('#operationMessage').textContent = stop ? 'Checking the exact controller revision before Stop…' : 'Checking this scene against the exact controller revision…';
    try { await guardedWallActivation(sceneFromControls(), !stop); }
    catch (error) { renderStatus(wallStatus(error.message)); blockers({error: error.message, blockers: error.blockers}); }
    finally { state.wall.activating = false; $('#liveAction').disabled = false; renderStatus(state.status || wallStatus()); }
  }
  async function check() { try { const response = await fetch(`${api}/check`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({origin: 'composer', scene: sceneFromControls()})}); const result = await response.json(); $('#checkMessage').textContent = response.ok ? 'This is advisory; it does not change output.' : (result.error || 'Check could not complete.'); const details = $('#checkDetails'); details.replaceChildren(); [['Scene identity', identity(result.basis)], ['Connection', result.status?.connected ? 'Connected' : 'Disconnected'], ['Publication', result.status?.armed ? 'Immediate when edited' : 'Use Go Live to arm output']].forEach(([term, description]) => { const entry = document.createElement('div'); entry.innerHTML = `<dt>${term}</dt><dd>${description}</dd>`; details.append(entry); }); if (result.status) renderStatus(result); openDialog($('#checkDialog')); } catch (error) { $('#operationMessage').textContent = error.message; } }
  async function save(as) { try { const scene = sceneFromControls(); const name = $('#sceneName').value.trim(); if (as || state.selection?.kind !== 'look') { if (!name) throw new Error('Name this scene before Save As.'); const response = await fetch(`${api}/looks`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name, scene})}); const result = await response.json(); if (!response.ok) throw new Error(result.error); state.selection = {kind: 'look', id: result.look.id, name: result.look.name}; }
      else { const response = await fetch(`${api}/looks/save`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({scene})}); const result = await response.json(); if (!response.ok) throw new Error(result.error); }
      await loadLibrary(); state.dirty = false; $('#saveState').textContent = 'Saved';
    } catch (error) { $('#operationMessage').textContent = error.message; } }
  async function rewind(direction) { const source = direction === 'undo' ? state.history : state.redo; const next = source.pop(); if (!next) return; const opposite = direction === 'undo' ? state.redo : state.history; opposite.push(structuredClone(state.scene)); state.dirty = true; applyScene(next); try { await submit(next); } catch (error) { $('#operationMessage').textContent = error.message; } }
  async function loadLibrary() { const response = await fetch(`${api}/library`); state.library = await response.json(); renderLibrary(); }
  function wire() {
    ['#backgroundGain','#curtainDensity','#foldDepth','#glowIntensity','#animationChoice','#lifeSeed','#lifeRate','#tetrisPieces','#tetrisFallRate','#tetrisRisk','#tetrisSmoothDrop','#fireflyPopulation','#fireflySynchrony','#fireflyWandering','#fireflyPulseSoftness','#fireflyMeadowGlow','#fireworksCadence','#fireworksPopulation','#fireworksBurstSize','#fireworksStyle','#fireworksGravity','#fireworksTrails','#fireworksCrackle','#fireworksTwinkle','#fireworksSeed','#flameCadence','#flameSize','#flameEmbers','#flameFlicker','#fluidFlow','#fluidCurrent','#fluidBubbles','#fluidSurface','#lavaBlobCount','#lavaBlobScale','#lavaViscosity','#lavaHeat','#lavaTurbulence','#lavaGlow','#lavaSeed','#canopyWorld','#canopyHeats','#canopyCourse','#canopyDensity','#canopyRivalry','#canopyPowerups','#mazeCadence','#mazeDifficulty','#mazeRadar','#pinballTicks','#pinballChaos','#questCadence','#questDifficulty','#questHud','#asciiPhrase','#asciiStory','#asciiSpeed','#asciiDensity','#emojiFace','#emojiMood','#emojiAnimationPulse','#emojiAnimationScale','#treeSeason','#treeHeight','#treeSnowfall','#trainRoute','#trainSpeed','#trainGlow','#clockEnabled','#clockOffset','#emojiEnabled','#emojiText','#emojiXOffset','#emojiYOffset','#emojiCharSpacing','#emojiLineSpacing','#emojiScrollSpeed','#emojiPulseSpeed','#previewPalette','#wallPace','#sceneLuminance', ...Object.values(componentControls).flat().filter((selector) => selector.startsWith('#gradient') || selector.startsWith('#rainbow') || selector.startsWith('#solid') || selector.startsWith('#sparkle') || selector.startsWith('#wave'))].forEach((selector) => $(selector).addEventListener('change', edit));
    ['#clockFormat','#clockSeconds','#clockTimeOffset'].forEach((selector) => $(selector).addEventListener('input', edit));
    ['#tetrisPieces','#tetrisFallRate','#tetrisRisk','#fireworksCadence','#fireworksPopulation','#fireworksBurstSize','#fireworksGravity','#fireworksTrails','#fireworksCrackle','#fireworksTwinkle','#flameCadence','#flameSize','#flameEmbers','#flameFlicker','#fluidFlow','#fluidCurrent','#fluidBubbles','#fluidSurface','#lavaBlobCount','#lavaBlobScale','#lavaViscosity','#lavaHeat','#lavaTurbulence','#lavaGlow'].forEach((selector) => $(selector).addEventListener('input', edit));
    Object.values(atmosphereControls).flat().forEach((selector) => { $(selector).addEventListener('change', edit); $(selector).addEventListener('input', edit); });
    Object.values(sculptureControls).flat().forEach((selector) => { $(selector).addEventListener('change', edit); $(selector).addEventListener('input', edit); });
    Object.values(componentControls).flat().filter((selector) => selector.startsWith('#gradient') || selector.startsWith('#rainbow') || selector.startsWith('#solid') || selector.startsWith('#sparkle') || selector.startsWith('#wave')).forEach((selector) => $(selector).addEventListener('input', edit));
    // Phrase editing is the ASCII instrument itself: publish each real text
    // input so the Preview and live remix visibly answer while typing.
    ['#asciiPhrase'].forEach((selector) => $(selector).addEventListener('input', edit));
    plantOptics.forEach((optic) => {
      $(optic.enabled).addEventListener('change', (event) => { syncPlantOpticControl(optic); renderPlantOpticsStatus(); edit(event); });
      $(optic.strength).addEventListener('input', (event) => { syncPlantOpticControl(optic); renderPlantOpticsStatus(); edit(event); });
    });
    $('#removeEmoji').addEventListener('click', async () => { const next = structuredClone(state.scene || defaultScene()); next.widgets = next.widgets.filter((widget) => widget.component?.component_id !== 'emoji_arranger'); state.lastControl = 'removeEmoji'; try { await submit(next, {rememberEdit: true}); applyScene(next); } catch (error) { $('#operationMessage').textContent = error.message; } });
    $('#scenePreview').addEventListener('pointerdown', triggerInstrumentAtPointer);
    $('#librarySearch').addEventListener('input', (event) => { state.query = event.target.value; renderLibrary(); }); document.querySelectorAll('[data-library-filter]').forEach((button) => button.addEventListener('click', () => { state.filter = button.dataset.libraryFilter; renderLibrary(); }));
    $('#openScene').addEventListener('click', () => $('#librarySearch').focus()); $('#saveScene').addEventListener('click', () => save(false)); $('#saveAsScene').addEventListener('click', () => save(true)); $('#undoScene').addEventListener('click', () => rewind('undo')); $('#redoScene').addEventListener('click', () => rewind('redo')); $('#liveAction').addEventListener('click', liveAction); $('#checkScene').addEventListener('click', check); document.querySelectorAll('[data-dialog-close]').forEach((button) => button.addEventListener('click', () => button.closest('dialog').close()));
    document.addEventListener('keydown', (event) => { if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== 'z') return; event.preventDefault(); rewind(event.shiftKey ? 'redo' : 'undo'); });
  }
  function syncSecondaryOperations() { $('#secondaryOperations').open = !window.matchMedia('(max-width: 760px)').matches; }
  const phoneLayout = window.matchMedia('(max-width: 760px)');
  phoneLayout.addEventListener('change', syncSecondaryOperations);
  syncSecondaryOperations(); installPixelStoryControls(); installTetrisControls(); installAmbientControls(); installAtmosphereControls(); installSculptureControls(); nestComponentControls(); installSemanticControls(); wire(); applyScene(defaultScene());
  if (![...$('#animationChoice').options].some((option) => option.value === 'snake')) $('#animationChoice').append(new Option('Snake Garden', 'snake'));
  if (![...$('#animationChoice').options].some((option) => option.value === 'canopy_cup')) $('#animationChoice').append(new Option('Canopy Cup', 'canopy_cup'));
  if (![...$('#animationChoice').options].some((option) => option.value === 'maze_chase')) $('#animationChoice').append(new Option('Maze Chase', 'maze_chase'));
  if (![...$('#animationChoice').options].some((option) => option.value === 'pinball')) $('#animationChoice').append(new Option('Arcade Pinball', 'pinball'));
  if (![...$('#animationChoice').options].some((option) => option.value === 'pixel_quest')) $('#animationChoice').append(new Option('Pixel Quest', 'pixel_quest'));
  if (![...$('#animationChoice').options].some((option) => option.value === 'ascii_drop')) $('#animationChoice').append(new Option('ASCII Drop', 'ascii_drop'));
  if (![...$('#animationChoice').options].some((option) => option.value === 'emoji')) $('#animationChoice').append(new Option('Emoji Animation', 'emoji'));
  if (![...$('#animationChoice').options].some((option) => option.value === 'christmas_tree')) $('#animationChoice').append(new Option('Christmas Tree', 'christmas_tree'));
  if (![...$('#animationChoice').options].some((option) => option.value === 'night_train_windows')) $('#animationChoice').append(new Option('Night Train Windows', 'night_train_windows'));
  [['gradient','Gradient Field'],['rainbow','Rainbow River'],['solid','Solid Glow'],['sparkle','Sparkle Night'],['wave','Wave Ribbons']].forEach(([id, name]) => { if (![...$('#animationChoice').options].some((option) => option.value === id)) $('#animationChoice').append(new Option(name, id)); });
  [['circadian_window','Circadian Window'],['cloud_canyon','Cloud Canyon'],['desert_wind','Desert Wind'],['moonlit_fog_banks','Moonlit Fog Banks'],['rain_on_glass','Rain on Glass'],['tidal_bioluminescence','Tidal Bioluminescence'],['waterfall_veil','Waterfall Veil']].forEach(([id, name]) => { if (![...$('#animationChoice').options].some((option) => option.value === id)) $('#animationChoice').append(new Option(name, id)); });
  [['cellular_tapestry','Cellular Tapestry'],['flow_field_silk','Flow-Field Silk'],['frostwork','Frostwork'],['living_stained_glass','Living Stained Glass'],['quasicrystal_bloom','Quasicrystal Bloom'],['living_ecosystem','Living Ecosystem'],['physarum_network','Physarum Network'],['reaction_diffusion_garden','Reaction-Diffusion Garden'],['wind_in_the_reeds','Wind in the Reeds']].forEach(([id, name]) => { if (![...$('#animationChoice').options].some((option) => option.value === id)) $('#animationChoice').append(new Option(name, id)); });
  async function refreshStatus() { if (state.refreshInFlight || state.wall.activating) return; state.refreshInFlight = true; try { await refreshWallStatus(); } catch (error) { renderStatus(wallStatus(error.message)); } finally { state.refreshInFlight = false; } }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshStatus(); });
  function recoverFromInvalidRecovery(body) { state.revision = body.status?.revision || 0; applyScene(defaultScene()); if (body.status) renderStatus(body.status); $('#operationMessage').textContent = `${body.error || 'Saved current scene needs recovery.'} Select a built-in scene or use Go Live to replace it.`; }
  async function hydrateCurrentScene() {
    try {
      const payload = await refreshWallStatus({adopt: true});
      if (payload.scene) return;
    } catch (wallError) {
      $('#operationMessage').textContent = `${wallError.message} Loading the last local draft instead.`;
    }
    let response;
    try { response = await fetch(`${api}/recovery?client_id=${encodeURIComponent(clientId)}`); }
    catch (_) { const error = new Error('Local Composer server unavailable.'); error.serverUnavailable = true; throw error; }
    const body = await response.json();
    if (!response.ok) { const error = new Error(body.error || 'Current scene recovery is unavailable.'); if (response.status >= 500) { error.serverUnavailable = true; throw error; } recoverFromInvalidRecovery(body); return; }
    if (body.recovery) {
      state.scene = body.recovery.scene; state.selection = body.recovery.opened_look_id ? {kind:'look', id:body.recovery.opened_look_id} : null;
      state.dirty = false; state.wall.dirty = true; applyScene(state.scene); renderStatus(wallStatus());
    } else { applyScene(defaultScene()); state.wall.dirty = true; renderStatus(wallStatus()); }
  }
  hydrateCurrentScene().then(loadLibrary).then(loadFireworksPresets).then(loadSnakePresets).then(loadLavaPresets).then(loadReefPresets).then(loadClockPresets).then(() => Promise.all(['flame_burst', 'fluid_tank', 'aurora_curtains', 'conway_life', 'tetris', 'firefly_synchrony', 'canopy_cup', 'maze_chase', 'pinball', 'pixel_quest', 'ascii_drop', 'emoji', 'christmas_tree', 'night_train_windows', ...ambientIds, ...atmosphereIds, ...sculptureIds].map(loadExistingComponentPresets))).then(() => { previewScheduler.start(); schedulePreview(); return refreshStatus(); }).then(() => { setInterval(() => { if (!document.hidden) refreshStatus(); }, 2500); }).catch((error) => { $('#operationMessage').textContent = error.message || 'Local Composer server unavailable.'; if (error.serverUnavailable) window.dispatchEvent(new Event('composer-server-unavailable')); });
})();
