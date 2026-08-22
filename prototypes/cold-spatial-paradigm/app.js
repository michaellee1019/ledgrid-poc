/* Lumen Loom is a local interaction prototype. It performs no network requests. */
(function () {
  'use strict';

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  const familyColors = {
    Ambient: '#73a8ff',
    Time: '#ff745f',
    Play: '#d7f96c',
    Pixel: '#b690ff',
    Calibration: '#edb75f',
    Diagnostics: '#74ddbd',
    Touch: '#f29ac0'
  };

  const componentSeed = [
    ['Tidepool', 'Ambient', 'background', 'host-python'],
    ['Mist Weave', 'Ambient', 'background', 'host-python'],
    ['Aurora Fold', 'Ambient', 'background', 'host-python'],
    ['Firefly Choir', 'Ambient', 'background', 'host-python'],
    ['Rain Memory', 'Ambient', 'background', 'host-python'],
    ['Soft Current', 'Ambient', 'background', 'receiver-native'],
    ['Moss Pulse', 'Ambient', 'background', 'host-python'],
    ['Lantern Drift', 'Ambient', 'background', 'host-python'],
    ['Prism Weather', 'Ambient', 'background', 'receiver-native'],
    ['Solar Dust', 'Ambient', 'background', 'host-python'],
    ['Moon Milk', 'Ambient', 'background', 'host-python'],
    ['Kelp Field', 'Ambient', 'background', 'host-python'],
    ['Ember Veil', 'Ambient', 'background', 'receiver-native'],
    ['Cloud Loom', 'Ambient', 'background', 'host-python'],
    ['Quiet Stars', 'Ambient', 'background', 'host-python'],
    ['Color Breathing', 'Ambient', 'background', 'receiver-native'],
    ['Field Clock', 'Time', 'overlay', 'host-python'],
    ['Word Clock', 'Time', 'overlay', 'host-python'],
    ['Orbit Clock', 'Time', 'overlay', 'receiver-native'],
    ['Falling Minutes', 'Time', 'overlay', 'host-python'],
    ['Weather Glyph', 'Time', 'overlay', 'host-python'],
    ['Room Almanac', 'Time', 'overlay', 'host-python'],
    ['Sun Arc', 'Time', 'overlay', 'receiver-native'],
    ['Message Ribbon', 'Time', 'overlay', 'host-python'],
    ['Snake Garden', 'Play', 'full-scene', 'host-python'],
    ['Tetris Canopy', 'Play', 'full-scene', 'host-python'],
    ['Pong Rain', 'Play', 'full-scene', 'host-python'],
    ['Life Terraces', 'Play', 'full-scene', 'host-python'],
    ['Flock Maze', 'Play', 'full-scene', 'host-python'],
    ['Falling Sand', 'Play', 'full-scene', 'host-python'],
    ['Portal Seeds', 'Play', 'full-scene', 'host-python'],
    ['Tiny Invaders', 'Play', 'full-scene', 'host-python'],
    ['Ribbon Koi', 'Pixel', 'background', 'host-python'],
    ['Night Train', 'Pixel', 'background', 'host-python'],
    ['Tall Botanica', 'Pixel', 'background', 'host-python'],
    ['Moon Rabbit', 'Pixel', 'background', 'host-python'],
    ['City Windows', 'Pixel', 'background', 'host-python'],
    ['Pocket Monsters', 'Pixel', 'background', 'host-python'],
    ['Deep Diver', 'Pixel', 'background', 'host-python'],
    ['Festival Flags', 'Pixel', 'background', 'host-python'],
    ['Globe Survey', 'Calibration', 'full-scene', 'host-python'],
    ['Foliage Survey', 'Calibration', 'full-scene', 'host-python'],
    ['Strip Compass', 'Calibration', 'full-scene', 'receiver-native'],
    ['Camera Plane', 'Calibration', 'full-scene', 'host-python'],
    ['Occlusion Proof', 'Calibration', 'full-scene', 'host-python'],
    ['Lane Truth', 'Diagnostics', 'full-scene', 'receiver-native'],
    ['Frame Pulse', 'Diagnostics', 'full-scene', 'host-python'],
    ['Sparse Boundary', 'Diagnostics', 'full-scene', 'receiver-native'],
    ['Receiver Breath', 'Diagnostics', 'full-scene', 'receiver-native'],
    ['Light Painter', 'Touch', 'full-scene', 'host-python'],
    ['Emoji Gravity', 'Touch', 'full-scene', 'host-python'],
    ['Hole Field', 'Touch', 'full-scene', 'host-python']
  ];

  const specialNames = {
    Tidepool: ['Slow Glass', 'Blue Silt', 'Warm Estuary', 'Night Shelf', 'Clear Current', 'Storm Pearl'],
    'Field Clock': ['Tall Numerals', 'Soft Hourglass', 'Small Analog', 'Botanical Minutes', 'Quiet Seconds', 'Dawn Dial'],
    'Ribbon Koi': ['Coral Pair', 'Indigo School', 'Golden Turn', 'Rain Koi', 'Midnight Pond', 'Festival Swim']
  };
  const qualities = ['Quiet', 'Vivid', 'Soft', 'Long', 'Small', 'Dappled', 'Warm', 'Blue', 'Open', 'Slow', 'Bright', 'Deep'];
  const forms = ['Study', 'Drift', 'Field', 'Loop', 'Fold', 'Current', 'Trace', 'Weather', 'Chorus', 'Room', 'Pattern', 'Passage'];

  function hashString(value) {
    let hash = 2166136261;
    for (let i = 0; i < value.length; i += 1) {
      hash ^= value.charCodeAt(i);
      hash = Math.imul(hash, 16777619);
    }
    return hash >>> 0;
  }

  function hslFor(value, offset = 0, light = 58) {
    return `hsl(${(hashString(value) + offset) % 360} 76% ${light}%)`;
  }

  const components = componentSeed.map((seed, componentIndex) => {
    const [name, family, role, provider] = seed;
    const presetCount = componentIndex < 32 ? 6 : 5; // 32×6 + 20×5 = 292
    const names = specialNames[name] || Array.from({ length: presetCount }, (_, presetIndex) => {
      const h = hashString(`${name}-${presetIndex}`);
      return `${qualities[h % qualities.length]} ${forms[(h >>> 5) % forms.length]}`;
    });
    const presets = Array.from({ length: presetCount }, (_, presetIndex) => {
      const identity = `${provider}:${name.toLowerCase().replace(/[^a-z0-9]+/g, '-')}/${presetIndex + 1}`;
      let availability = 'ready';
      const serial = componentIndex * 7 + presetIndex;
      if (serial === 41 || serial === 252) availability = 'build';
      if (serial === 119 || serial === 307) availability = 'unavailable';
      if (serial === 201) availability = 'quarantined';
      return {
        id: identity,
        component: name,
        title: names[presetIndex],
        family,
        role,
        provider,
        availability,
        cadence: family === 'Time' ? '1 semantic tick/min' : provider === 'receiver-native' ? '40 fps local' : `${20 + ((componentIndex + presetIndex) % 4) * 10} fps source`,
        plantResponse: family === 'Play' ? 'routes around globe clearance' : family === 'Pixel' ? 'soft foliage occlusion' : family === 'Calibration' ? 'reveals exact geometry' : 'hushes beneath foliage',
        color: hslFor(identity),
        accent: hslFor(identity, 97, 68)
      };
    });
    return { name, family, role, provider, presets };
  });

  const presets = components.flatMap(component => component.presets);
  if (components.length !== 52 || presets.length !== 292) throw new Error('Atlas contract must stay 52 components / 292 presets.');
  const presetById = new Map(presets.map(preset => [preset.id, preset]));

  const firstClock = presets.find(preset => preset.component === 'Field Clock' && preset.title === 'Tall Numerals');
  const defaultBackground = presets.find(preset => preset.component === 'Tidepool' && preset.title === 'Slow Glass');
  const altBackground = presets.find(preset => preset.component === 'Ribbon Koi' && preset.title === 'Coral Pair');
  const thirdBackground = presets.find(preset => preset.component === 'Aurora Fold');

  const initialScene = {
    backgroundId: defaultBackground.id,
    overlayId: firstClock.id,
    opacity: 86,
    placementX: 0,
    placementY: 18,
    stalePolicy: 'hold',
    fallback: 'Soft Hourglass',
    vibe: 'Cozy',
    plants: { foliage: 35, globes: 70, edges: 0, vines: 0 },
    fps: 120,
    speed: 100
  };

  let scene = structuredClone(initialScene);
  let liveScene = structuredClone(initialScene);
  let savedScene = structuredClone(initialScene);
  let comparison = [defaultBackground.id, altBackground.id, thirdBackground.id];
  let selectedId = defaultBackground.id;
  let selectedFamily = 'All';
  let searchTerm = '';
  let selectedLayer = 'background';
  let touchMode = 'point';
  let running = true;
  let powered = true;
  let wallDrift = false;
  let point = { x: 50, y: 50 };
  let toastTimer;
  let holdStart = 0;
  let holdFrame = 0;
  let animationFrame = 0;

  function clone(value) { return JSON.parse(JSON.stringify(value)); }

  function presetName(id) {
    const preset = presetById.get(id);
    return preset ? `${preset.component} / ${preset.title}` : 'No overlay';
  }

  function sceneComparable(value) {
    return {
      backgroundId: value.backgroundId,
      overlayId: value.overlayId || null,
      opacity: value.opacity,
      placementX: value.placementX,
      placementY: value.placementY,
      stalePolicy: value.stalePolicy,
      fallback: value.fallback,
      vibe: value.vibe,
      plants: value.plants,
      fps: value.fps,
      speed: value.speed
    };
  }

  function diffKeys(a, b) {
    const diffs = [];
    if (a.backgroundId !== b.backgroundId) diffs.push('background');
    if ((a.overlayId || null) !== (b.overlayId || null)) diffs.push('overlay');
    if (a.opacity !== b.opacity || a.placementX !== b.placementX || a.placementY !== b.placementY || a.stalePolicy !== b.stalePolicy || a.fallback !== b.fallback) diffs.push('overlay layout');
    if (a.vibe !== b.vibe) diffs.push('vibe');
    if (JSON.stringify(a.plants) !== JSON.stringify(b.plants)) diffs.push('plant material');
    if (a.fps !== b.fps || a.speed !== b.speed) diffs.push('tempo');
    return diffs;
  }

  function isSaved() {
    return JSON.stringify(sceneComparable(scene)) === JSON.stringify(sceneComparable(savedScene));
  }

  function updateState() {
    const diffs = diffKeys(liveScene, scene);
    const dirty = diffs.length > 0;
    const saveState = $('#saveState');
    const liveNow = $('#liveNowButton');
    const liveState = $('#liveState');
    const takeLabel = $('#takeLiveLabel');
    const bg = presetById.get(scene.backgroundId);
    const overlay = scene.overlayId ? presetById.get(scene.overlayId) : null;

    $('#backgroundName').textContent = bg ? bg.title : 'None';
    $('#backgroundProvider').textContent = bg ? bg.provider : '—';
    $('#overlayName').textContent = overlay ? overlay.title : 'No overlay';
    $('#overlayProvider').textContent = overlay ? overlay.provider : '—';
    $('#overlayOpacity').value = scene.opacity;
    $('#opacityOutput').textContent = `${scene.opacity}%`;
    $('#placementOutput').textContent = `${scene.placementX === 0 ? 'center' : scene.placementX < 0 ? 'left' : 'right'} · y ${scene.placementY}`;
    $('#stalePolicy').value = scene.stalePolicy;
    $('#fallbackSelect').value = scene.fallback;

    saveState.className = 'save-state';
    if (!isSaved()) {
      saveState.textContent = 'UNSAVED REHEARSAL';
      saveState.classList.add('dirty');
    } else if (dirty) {
      saveState.textContent = 'SAVED · NOT LIVE';
      saveState.classList.add('dirty');
    } else if (wallDrift) {
      saveState.textContent = 'SAVED · WALL DRIFT';
      saveState.classList.add('drift');
    } else {
      saveState.textContent = 'LAYOUT SAVED · LIVE MATCH';
    }

    liveNow.classList.toggle('is-dirty', dirty && !wallDrift);
    liveNow.classList.toggle('is-drift', wallDrift);
    liveNow.classList.toggle('is-stopped', !running && powered);
    liveNow.classList.toggle('is-off', !powered);
    $('#liveTitle').textContent = presetName(liveScene.backgroundId) + (liveScene.overlayId ? ` + ${presetById.get(liveScene.overlayId).title}` : '');
    if (!powered) liveState.textContent = 'POWER OFF';
    else if (!running) liveState.textContent = 'STOPPED · CONTENT HELD';
    else if (wallDrift) liveState.textContent = 'WALL DRIFT · REAPPLY';
    else if (dirty) liveState.textContent = `STAGED APART · ${diffs.length} ${diffs.length === 1 ? 'CHANGE' : 'CHANGES'}`;
    else liveState.textContent = 'RUNNING · MATCH';
    takeLabel.textContent = wallDrift ? 'Review & reapply scene' : dirty ? `Review ${diffs.length} ${diffs.length === 1 ? 'change' : 'changes'}` : 'Scene already matches wall';
  }

  function markSceneChange(message) {
    updateState();
    if (message) showToast(message);
  }

  function showToast(message) {
    const toast = $('#toast');
    toast.textContent = message;
    toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove('show'), 2400);
  }

  function buildCategoryOrbit() {
    const orbit = $('#categoryOrbit');
    ['All', ...Object.keys(familyColors)].forEach(family => {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = family === 'All' ? 'Whole atlas' : family;
      button.dataset.family = family;
      button.style.setProperty('--family-color', family === 'All' ? '#e9e6da' : familyColors[family]);
      button.classList.toggle('active', family === selectedFamily);
      button.addEventListener('click', () => {
        selectedFamily = family;
        $$('.category-orbit button').forEach(node => node.classList.toggle('active', node.dataset.family === family));
        filterAtlas();
      });
      orbit.appendChild(button);
    });
  }

  function buildAtlas() {
    const field = $('#materialField');
    components.forEach(component => {
      const spool = document.createElement('article');
      spool.className = 'component-spool';
      spool.dataset.family = component.family;
      spool.dataset.component = component.name.toLowerCase();
      const label = document.createElement('div');
      label.className = 'spool-label';
      label.innerHTML = `<strong>${component.name}</strong><small>${component.provider} · ${component.role}</small>`;
      const strandSet = document.createElement('div');
      strandSet.className = 'strand-set';
      component.presets.forEach(preset => {
        const strand = document.createElement('button');
        strand.type = 'button';
        strand.className = 'preset-strand';
        strand.draggable = true;
        strand.dataset.presetId = preset.id;
        strand.dataset.availability = preset.availability;
        strand.dataset.search = `${preset.component} ${preset.title} ${preset.family} ${preset.role} ${preset.provider}`.toLowerCase();
        strand.style.setProperty('--strand', `linear-gradient(to bottom, ${preset.accent}, ${preset.color})`);
        strand.title = `${preset.title} · ${preset.provider} · ${preset.role} · ${preset.availability}`;
        strand.setAttribute('aria-label', strand.title);
        strand.addEventListener('click', () => addToComparison(preset.id));
        strand.addEventListener('dblclick', () => stagePreset(preset.id));
        strand.addEventListener('dragstart', event => {
          event.dataTransfer.setData('text/plain', preset.id);
          event.dataTransfer.effectAllowed = 'copy';
        });
        strandSet.appendChild(strand);
      });
      spool.append(label, strandSet);
      field.appendChild(spool);
    });
    syncAtlasSelection();
  }

  function filterAtlas() {
    let visiblePresets = 0;
    let visibleComponents = 0;
    $$('.component-spool').forEach(spool => {
      let componentVisible = false;
      $$('.preset-strand', spool).forEach(strand => {
        const familyMatch = selectedFamily === 'All' || spool.dataset.family === selectedFamily;
        const textMatch = !searchTerm || strand.dataset.search.includes(searchTerm);
        const visible = familyMatch && textMatch;
        strand.classList.toggle('hidden', !visible);
        if (visible) {
          visiblePresets += 1;
          componentVisible = true;
        }
      });
      spool.classList.toggle('hidden', !componentVisible);
      if (componentVisible) visibleComponents += 1;
    });
    $('#visiblePresetCount').textContent = visiblePresets;
    $('#visibleComponentCount').textContent = visibleComponents;
  }

  function syncAtlasSelection() {
    $$('.preset-strand').forEach(strand => strand.classList.toggle('active', strand.dataset.presetId === selectedId));
  }

  function addToComparison(id) {
    selectedId = id;
    if (!comparison.includes(id)) comparison = [...comparison.slice(-2), id];
    syncAtlasSelection();
    renderSpecimens();
    updateMaterialNotes(presetById.get(id));
  }

  function updateMaterialNotes(preset) {
    if (!preset) return;
    $('#noteProvider').textContent = preset.provider;
    $('#noteRole').textContent = preset.role;
    $('#noteCadence').textContent = preset.cadence;
    $('#notePlant').textContent = preset.plantResponse;
  }

  function renderSpecimens() {
    const table = $('#specimenTable');
    table.textContent = '';
    comparison.forEach(id => {
      const preset = presetById.get(id);
      const figure = document.createElement('figure');
      figure.className = `specimen${id === selectedId ? ' selected' : ''}`;
      const frame = document.createElement('div');
      frame.className = 'specimen-frame';
      frame.draggable = true;
      frame.dataset.presetId = id;
      frame.tabIndex = 0;
      frame.setAttribute('role', 'button');
      frame.setAttribute('aria-label', `Select ${preset.title}; drag to rehearsal`);
      const canvas = document.createElement('canvas');
      canvas.width = 64;
      canvas.height = 276;
      canvas.dataset.previewId = id;
      const provider = document.createElement('span');
      provider.className = 'provider-pin';
      provider.textContent = preset.provider === 'receiver-native' ? 'receiver' : 'host';
      frame.append(canvas, provider);
      if (preset.availability !== 'ready') {
        const veil = document.createElement('span');
        veil.className = 'availability-veil';
        veil.textContent = preset.availability === 'build' ? 'build required' : preset.availability;
        frame.appendChild(veil);
      }
      frame.addEventListener('click', () => {
        selectedId = id;
        renderSpecimens();
        syncAtlasSelection();
        updateMaterialNotes(preset);
      });
      frame.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          selectedId = id;
          stagePreset(id);
        }
      });
      frame.addEventListener('dragstart', event => {
        event.dataTransfer.setData('text/plain', id);
        event.dataTransfer.effectAllowed = 'copy';
      });
      const caption = document.createElement('figcaption');
      caption.innerHTML = `<strong>${preset.title}</strong><small>${preset.component} · ${preset.role}</small>`;
      const place = document.createElement('button');
      place.type = 'button';
      place.className = 'place-specimen';
      place.textContent = preset.role === 'overlay' ? 'Place as overlay' : preset.role === 'full-scene' ? 'Place full scene' : 'Place as background';
      place.addEventListener('click', () => stagePreset(id));
      figure.append(frame, caption, place);
      table.appendChild(figure);
    });
  }

  function stagePreset(id) {
    const preset = presetById.get(id);
    if (!preset) return;
    selectedId = id;
    if (preset.availability !== 'ready') {
      showToast(`${preset.title} is ${preset.availability}; it can be rehearsed but cannot be staged live.`);
      renderSpecimens();
      return;
    }
    if (preset.role === 'overlay') {
      scene.overlayId = id;
      selectedLayer = 'overlay';
      $('#overlayThread').classList.remove('removed');
      $('#overlayControls').classList.remove('removed');
      $('#addOverlay').classList.remove('visible');
    } else {
      scene.backgroundId = id;
      selectedLayer = 'background';
      if (preset.role === 'full-scene') {
        scene.overlayId = null;
        $('#overlayThread').classList.add('removed');
        $('#overlayControls').classList.add('removed');
        $('#addOverlay').classList.add('visible');
      }
    }
    syncLayerSelection();
    markSceneChange(`${preset.title} placed in rehearsal. The physical wall is unchanged.`);
  }

  function syncLayerSelection() {
    $('#backgroundThread').classList.toggle('selected', selectedLayer === 'background');
    $('#overlayThread').classList.toggle('selected', selectedLayer === 'overlay');
  }

  function drawMaterial(ctx, preset, tick, width, height, clear = true, opacity = 1) {
    if (!preset) return;
    const seed = hashString(preset.id);
    const phase = tick * (0.35 + (seed % 8) / 14);
    ctx.save();
    ctx.globalAlpha = opacity;
    if (clear) {
      ctx.fillStyle = '#050504';
      ctx.fillRect(0, 0, width, height);
    }

    if (preset.family === 'Ambient') {
      const gradient = ctx.createLinearGradient(0, 0, width, height);
      gradient.addColorStop(0, hslFor(preset.id, phase * 8, 12));
      gradient.addColorStop(.52, hslFor(preset.id, 70 + Math.sin(phase) * 25, 34));
      gradient.addColorStop(1, hslFor(preset.id, 155 + phase * 4, 13));
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, width, height);
      for (let i = 0; i < 11; i += 1) {
        const y = ((i * height / 9) + Math.sin(phase + i * 1.7) * height * .08 + height) % height;
        ctx.fillStyle = i % 2 ? hslFor(preset.id, 97 + phase * 3, 31) : hslFor(preset.id, phase * 4, 24);
        ctx.fillRect(0, y, width, Math.max(1, height * .018));
      }
    } else if (preset.family === 'Time') {
      const x = width * (.5 + scene.placementX / 100);
      const y = height * (Math.max(5, Math.min(90, scene.placementY)) / 100);
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      ctx.shadowColor = preset.accent;
      ctx.shadowBlur = Math.max(3, width * .06);
      ctx.fillStyle = '#f4f0dd';
      ctx.font = `600 ${Math.max(8, width * .27)}px ui-monospace, monospace`;
      ctx.fillText('10', x, y);
      ctx.fillText('42', x, y + width * .26);
      ctx.shadowBlur = 0;
      ctx.fillStyle = preset.color;
      ctx.fillRect(x - width * .25, y + width * .58, width * .5, Math.max(1, height * .005));
    } else if (preset.family === 'Play') {
      const cell = Math.max(2, Math.floor(width / 8));
      ctx.fillStyle = '#080b09';
      ctx.fillRect(0, 0, width, height);
      for (let y = 0; y < height; y += cell) {
        for (let x = 0; x < width; x += cell) {
          if (((x / cell) * 7 + (y / cell) * 3 + Math.floor(phase * 2) + seed) % 13 < 3) {
            ctx.fillStyle = (x + y) % (cell * 4) ? preset.color : preset.accent;
            ctx.fillRect(x + 1, y + 1, cell - 1, cell - 1);
          }
        }
      }
    } else if (preset.family === 'Pixel') {
      ctx.fillStyle = '#060609';
      ctx.fillRect(0, 0, width, height);
      const unit = Math.max(2, Math.floor(width / 16));
      for (let band = 0; band < 5; band += 1) {
        const centerY = height * (.12 + band * .2) + Math.sin(phase + band) * unit * 2;
        for (let x = -2; x < 18; x += 1) {
          const shape = Math.abs(x - 7) + Math.abs(((x * 5 + band + seed) % 5) - 2);
          if (shape < 9) {
            ctx.fillStyle = x % 3 ? preset.color : preset.accent;
            ctx.fillRect(x * unit + Math.sin(phase + band) * unit, centerY + ((x * x + band) % 5) * unit, unit, unit);
          }
        }
      }
    } else if (preset.family === 'Calibration' || preset.family === 'Diagnostics') {
      ctx.fillStyle = '#050505';
      ctx.fillRect(0, 0, width, height);
      const lanes = preset.family === 'Diagnostics' ? 8 : 4;
      for (let i = 0; i < lanes; i += 1) {
        ctx.fillStyle = `hsl(${(i * 360 / lanes + seed) % 360} 90% 55%)`;
        ctx.fillRect(i * width / lanes, 0, width / lanes - 1, height);
      }
      ctx.fillStyle = '#fff';
      const sweepY = (phase * height * .12) % height;
      ctx.fillRect(0, sweepY, width, Math.max(2, height * .008));
    } else {
      ctx.fillStyle = '#080609';
      ctx.fillRect(0, 0, width, height);
      for (let i = 0; i < 25; i += 1) {
        const x = ((seed >>> (i % 16)) + i * 19 + phase * 6) % width;
        const y = (i * height / 21 + Math.sin(phase + i) * 10 + height) % height;
        ctx.fillStyle = i % 2 ? preset.color : preset.accent;
        ctx.beginPath();
        ctx.arc(x, y, Math.max(1, width * .018), 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.restore();
  }

  function renderCanvases(now) {
    animationFrame = now * .001 * (scene.speed / 100);
    $$('canvas[data-preview-id]').forEach(canvas => {
      const preset = presetById.get(canvas.dataset.previewId);
      drawMaterial(canvas.getContext('2d'), preset, animationFrame, canvas.width, canvas.height, true, 1);
    });
    const canvas = $('#sceneCanvas');
    const ctx = canvas.getContext('2d');
    const background = presetById.get(scene.backgroundId);
    drawMaterial(ctx, background, animationFrame, canvas.width, canvas.height, true, 1);
    if (scene.overlayId) drawMaterial(ctx, presetById.get(scene.overlayId), animationFrame, canvas.width, canvas.height, false, scene.opacity / 100);
    requestAnimationFrame(renderCanvases);
  }

  function validateScene(showMessage = true) {
    const background = presetById.get(scene.backgroundId);
    const overlay = scene.overlayId ? presetById.get(scene.overlayId) : null;
    let result = { valid: true, message: 'Scene valid · provider roles compatible · output routes present.' };
    if (!background || background.availability !== 'ready') result = { valid: false, message: 'Background is not live-ready. Choose a ready strand before crossing.' };
    else if (background.role === 'full-scene' && overlay) result = { valid: false, message: 'This full-scene material cannot carry an overlay.' };
    else if (overlay && (overlay.role !== 'overlay' || overlay.availability !== 'ready')) result = { valid: false, message: 'Clock overlay is unavailable or has the wrong role.' };
    const message = $('#validationMessage');
    if (showMessage) {
      message.textContent = result.message;
      message.classList.toggle('error', !result.valid);
      setTimeout(() => { if (message.textContent === result.message) message.textContent = ''; }, 4300);
    }
    return result;
  }

  function buildTakeDiff() {
    const from = presetName(liveScene.backgroundId) + (liveScene.overlayId ? ` + ${presetById.get(liveScene.overlayId).title}` : '');
    const to = presetName(scene.backgroundId) + (scene.overlayId ? ` + ${presetById.get(scene.overlayId).title}` : '');
    $('#takeDiff').innerHTML = `<article><small>ON THE WALL</small><strong>${from}</strong></article><span>→</span><article><small>STAGED REHEARSAL</small><strong>${to}</strong></article>`;
    const result = validateScene(false);
    const preflight = $('#preflight');
    preflight.classList.toggle('invalid', !result.valid);
    preflight.innerHTML = result.valid
      ? '<span>✓</span><p><strong>Preflight valid</strong><small>Content ready · overlay compatible · four output routes present</small></p>'
      : `<span>!</span><p><strong>Cannot cross yet</strong><small>${result.message}</small></p>`;
    $('#holdButton').disabled = !result.valid;
    return result.valid;
  }

  function openThreshold() {
    const diffs = diffKeys(liveScene, scene);
    if (!diffs.length && !wallDrift) {
      showToast('Rehearsal already matches the physical wall. Nothing to take live.');
      return;
    }
    buildTakeDiff();
    $('#thresholdDialog').showModal();
  }

  function beginHold(event) {
    if ($('#holdButton').disabled) return;
    event.preventDefault();
    holdStart = performance.now();
    if (typeof event.pointerId === 'number') $('#holdButton').setPointerCapture?.(event.pointerId);
    const step = now => {
      if (!holdStart) return;
      const progress = Math.min(1, (now - holdStart) / 1200);
      $('#holdFill').style.transform = `scaleX(${progress})`;
      if (progress >= 1) {
        completeTakeLive();
      } else {
        holdFrame = requestAnimationFrame(step);
      }
    };
    holdFrame = requestAnimationFrame(step);
  }

  function cancelHold() {
    holdStart = 0;
    cancelAnimationFrame(holdFrame);
    $('#holdFill').style.transform = 'scaleX(0)';
    $('#holdButton').classList.remove('complete');
  }

  function completeTakeLive() {
    holdStart = 0;
    cancelAnimationFrame(holdFrame);
    liveScene = clone(scene);
    savedScene = clone(scene);
    wallDrift = false;
    powered = true;
    running = true;
    $('#powerButton').classList.add('power-on');
    $('#powerButton').setAttribute('aria-pressed', 'true');
    $('#runButton').textContent = 'Stop';
    $('#holdButton').classList.add('complete');
    updateState();
    setTimeout(() => {
      $('#thresholdDialog').close();
      cancelHold();
      showToast('Scene taken live · wall and saved layout now match.');
    }, 280);
  }

  function openDrawer(id) {
    $$('.drawer.open').forEach(drawer => { drawer.classList.remove('open'); drawer.setAttribute('aria-hidden', 'true'); });
    const drawer = $(`#${id}`);
    if (!drawer) return;
    drawer.classList.add('open');
    drawer.setAttribute('aria-hidden', 'false');
    $('#scrim').hidden = false;
    setTimeout(() => $('.drawer-close', drawer)?.focus(), 160);
  }

  function closeDrawers() {
    $$('.drawer.open').forEach(drawer => { drawer.classList.remove('open'); drawer.setAttribute('aria-hidden', 'true'); });
    $('#scrim').hidden = true;
  }

  function setPoint(event) {
    const wall = $('#ghostWall');
    const rect = wall.getBoundingClientRect();
    const next = {
      x: Math.max(0, Math.min(100, (event.clientX - rect.left) / rect.width * 100)),
      y: Math.max(0, Math.min(100, (event.clientY - rect.top) / rect.height * 100))
    };
    if (touchMode === 'dpad') {
      point.x = Math.max(0, Math.min(100, point.x + (next.x < 40 ? -8 : next.x > 60 ? 8 : 0)));
      point.y = Math.max(0, Math.min(100, point.y + (next.y < 40 ? -4 : next.y > 60 ? 4 : 0)));
    } else point = next;
    const marker = $('#pointMarker');
    marker.style.left = `${point.x}%`;
    marker.style.top = `${point.y}%`;
    marker.classList.add('visible');
    marker.classList.toggle('hole', touchMode === 'hole');
    $('#touchHint').textContent = touchMode === 'hole'
      ? `Hole rehearsed at strip ${Math.round(point.x * 31 / 100)} · LED ${Math.round(point.y * 137 / 100)}.`
      : touchMode === 'dpad'
        ? `D-pad moved focus to strip ${Math.round(point.x * 31 / 100)} · LED ${Math.round(point.y * 137 / 100)}.`
        : `Point at strip ${Math.round(point.x * 31 / 100)} · LED ${Math.round(point.y * 137 / 100)}.`;
  }

  function bindEvents() {
    $('#searchInput').addEventListener('input', event => { searchTerm = event.target.value.trim().toLowerCase(); filterAtlas(); });
    $('#clearSearch').addEventListener('click', () => { $('#searchInput').value = ''; searchTerm = ''; filterAtlas(); $('#searchInput').focus(); });
    $('#showAllButton').addEventListener('click', () => {
      selectedFamily = 'All'; searchTerm = ''; $('#searchInput').value = '';
      $$('.category-orbit button').forEach(button => button.classList.toggle('active', button.dataset.family === 'All'));
      filterAtlas();
    });
    $('#atlasHelp').addEventListener('click', () => showToast('52 component spools hold all 292 presets. Nothing is paginated or hidden behind “more.”'));

    const ghost = $('#ghostWall');
    ghost.addEventListener('dragover', event => { event.preventDefault(); ghost.classList.add('drag-over'); });
    ghost.addEventListener('dragleave', () => ghost.classList.remove('drag-over'));
    ghost.addEventListener('drop', event => {
      event.preventDefault();
      ghost.classList.remove('drag-over');
      stagePreset(event.dataTransfer.getData('text/plain'));
    });
    ghost.addEventListener('click', setPoint);

    $('#backgroundThread').addEventListener('click', () => { selectedLayer = 'background'; syncLayerSelection(); });
    $('#overlayThread').addEventListener('click', () => { selectedLayer = 'overlay'; syncLayerSelection(); });
    $('#removeOverlay').addEventListener('click', event => {
      event.stopPropagation();
      scene.overlayId = null;
      $('#overlayThread').classList.add('removed');
      $('#overlayControls').classList.add('removed');
      $('#addOverlay').classList.add('visible');
      selectedLayer = 'background';
      syncLayerSelection();
      markSceneChange('Overlay removed in rehearsal.');
    });
    $('#addOverlay').addEventListener('click', () => stagePreset(firstClock.id));
    $('#overlayOpacity').addEventListener('input', event => { scene.opacity = Number(event.target.value); markSceneChange(); });
    $$('.dpad button').forEach(button => button.addEventListener('click', () => {
      const move = button.dataset.move;
      if (move === 'center') { scene.placementX = 0; scene.placementY = 18; }
      if (move === 'left') scene.placementX = Math.max(-35, scene.placementX - 8);
      if (move === 'right') scene.placementX = Math.min(35, scene.placementX + 8);
      if (move === 'up') scene.placementY = Math.max(5, scene.placementY - 4);
      if (move === 'down') scene.placementY = Math.min(86, scene.placementY + 4);
      markSceneChange();
    }));
    $('#stalePolicy').addEventListener('change', event => { scene.stalePolicy = event.target.value; markSceneChange(); });
    $('#fallbackSelect').addEventListener('change', event => { scene.fallback = event.target.value; markSceneChange(); });

    $$('#touchMode button').forEach(button => button.addEventListener('click', () => {
      touchMode = button.dataset.mode;
      $$('#touchMode button').forEach(item => item.classList.toggle('active', item === button));
      $('#pointMarker').classList.toggle('hole', touchMode === 'hole');
      $('#touchHint').textContent = touchMode === 'point' ? 'Tap the ghost wall to place an interaction point.' : touchMode === 'hole' ? 'Tap to rehearse a gravity hole; live remains untouched.' : 'Tap a wall edge to move the rehearsal focus.';
    }));

    $('#validateButton').addEventListener('click', () => validateScene(true));
    $('#saveLayoutButton').addEventListener('click', () => {
      savedScene = clone(scene);
      updateState();
      showToast('Layout saved. The physical wall was not changed.');
    });
    $('#takeLiveButton').addEventListener('click', openThreshold);
    $('#cancelDialog').addEventListener('click', () => { cancelHold(); $('#thresholdDialog').close(); });
    $('#thresholdDialog').addEventListener('close', cancelHold);
    $('#holdButton').addEventListener('pointerdown', beginHold);
    ['pointerup', 'pointercancel', 'pointerleave'].forEach(type => $('#holdButton').addEventListener(type, cancelHold));
    $('#holdButton').addEventListener('keydown', event => { if ((event.key === ' ' || event.key === 'Enter') && !holdStart) beginHold(event); });
    $('#holdButton').addEventListener('keyup', event => { if (event.key === ' ' || event.key === 'Enter') cancelHold(); });

    $('#brightness').addEventListener('input', event => {
      $('#brightnessOutput').textContent = `${event.target.value}%`;
      $('#sceneCanvas').style.filter = `brightness(${Math.max(25, Number(event.target.value)) / 62})`;
    });
    $('#brightness').addEventListener('change', event => showToast(`Wall light set to ${event.target.value}% · direct global control.`));
    $('#powerButton').addEventListener('click', () => {
      powered = !powered;
      if (!powered) running = false;
      $('#powerButton').classList.toggle('power-on', powered);
      $('#powerButton').setAttribute('aria-pressed', String(powered));
      $('#powerButton').setAttribute('aria-label', powered ? 'Turn wall power off' : 'Turn wall power on');
      $('#runButton').textContent = running ? 'Stop' : 'Start';
      updateState();
      showToast(powered ? 'Wall power on; content remains stopped until Start.' : 'Wall power off. Rehearsal continues locally.');
    });
    $('#runButton').addEventListener('click', () => {
      if (!powered) powered = true;
      running = !running;
      $('#runButton').textContent = running ? 'Stop' : 'Start';
      $('#powerButton').classList.toggle('power-on', powered);
      updateState();
      showToast(running ? 'Current live scene started.' : 'Live output stopped; staged rehearsal preserved.');
    });

    const vibes = [
      ['Neutral', '#e9e6da', 8], ['Quiet', '#73a8ff', 13], ['Cozy', '#edb75f', 19], ['Vivid', '#b690ff', 15], ['Celebration', '#ff745f', 10]
    ];
    vibes.forEach(([name, color, height]) => {
      const button = document.createElement('button');
      button.type = 'button'; button.dataset.vibe = name; button.title = name;
      button.style.setProperty('--vibe-color', color); button.style.setProperty('--arc-height', `${height}px`);
      button.innerHTML = `<span>${name}</span>`;
      button.classList.toggle('active', scene.vibe === name);
      button.addEventListener('click', () => {
        scene.vibe = name;
        $$('.vibe-arc button').forEach(item => item.classList.toggle('active', item === button));
        $('#vibeValue').textContent = name;
        markSceneChange(`Vibe set to ${name}; plant material settings remain independent.`);
      });
      $('#vibeArc').appendChild(button);
    });

    $$('#plantGestures input').forEach(input => input.addEventListener('input', event => {
      scene.plants[event.target.dataset.plant] = Number(event.target.value);
      $('output', event.target.parentElement).textContent = event.target.value;
      const active = Object.values(scene.plants).filter(value => value > 0).length;
      $('#plantSummary').textContent = `${active} ${active === 1 ? 'gesture' : 'gestures'}`;
      markSceneChange();
    }));
    $('#fpsControl').addEventListener('input', event => { scene.fps = Number(event.target.value); $('#fpsOutput').textContent = event.target.value; markSceneChange(); });
    $('#speedControl').addEventListener('input', event => { scene.speed = Number(event.target.value); $('#speedOutput').textContent = `${(scene.speed / 100).toFixed(2)}×`; markSceneChange(); });

    $$('[data-drawer]').forEach(button => button.addEventListener('click', () => openDrawer(button.dataset.drawer)));
    $$('.drawer-close').forEach(button => button.addEventListener('click', closeDrawers));
    $('#scrim').addEventListener('click', closeDrawers);
    $('#liveNowButton').addEventListener('click', () => openDrawer('healthDrawer'));
    $('#simulateDriftButton').addEventListener('click', () => {
      wallDrift = !wallDrift;
      updateState();
      showToast(wallDrift ? 'Drift simulated: observed wall state no longer matches its receipt.' : 'Drift simulation cleared.');
      closeDrawers();
    });
    document.addEventListener('keydown', event => { if (event.key === 'Escape') closeDrawers(); });
  }

  buildCategoryOrbit();
  buildAtlas();
  renderSpecimens();
  updateMaterialNotes(defaultBackground);
  bindEvents();
  updateState();
  requestAnimationFrame(renderCanvases);
})();
