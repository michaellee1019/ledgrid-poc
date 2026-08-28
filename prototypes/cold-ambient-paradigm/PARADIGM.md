# Room Tune: an appliance grammar for a living wall

## The product idea

Room Tune treats the LED wall as a room appliance, not a media server. The first question is always **“What should the room do?”** A person can answer with a household outcome—settle, welcome people, help me focus, play, keep me posted, or make something—without knowing what an animation, plugin, provider, preset, scene, overlay, or renderer is.

The interface then behaves like a calm radio tuner:

1. Pick an intention.
2. Turn through a narrow “look river” selected for that intention.
3. See one convincing, wall-shaped preview at a time.
4. Compare two at full wall proportions if needed.
5. Use a sentence-shaped button that names exactly what will become live.

This is not a reskinned dashboard. It replaces a system taxonomy with a small product grammar.

## The grammar

### 1. Intention

The top rail completes the phrase **“Make the room…”** Its outcomes cross animation types:

- **settle** can contain atmospheric art, gentle simulation, pixel art, or a subdued clock;
- **welcome people** can contain warm art, playful GIFs, flags, or a celebration scene;
- **help me focus** can contain ordered simulations, gradients, or information;
- **play** can contain autonomous games and direct interactions;
- **keep me posted** can contain clocks and other information;
- **make something** can contain painter, emoji, masks, and authored looks.

Providers, roles, build state, and quarantine remain attached to every item, but do not define the household-facing navigation.

### 2. Look

A **look** is the thing a person recognizes and names: “Boreal Hush,” not `aurora_curtains + quiet.json`. It may resolve to a preset, a full-scene compatibility component, or a catalog-only source.

The **look river** is a typographic ledger, not a grid of tiles. It can hold hundreds of entries while preserving complete names, source identity, provider, role, and availability. The large preview is the focus; the river is the tuning scale.

The fixture is contract-scale: 52 sources and exactly 292 curated preset names. It includes host-Python and receiver-native identity, background/overlay/full-scene roles, and visible ready/build-only/unavailable/quarantined states.

### 3. Two-sheet scene

Scene composition uses the physical metaphor of two transparent sheets:

- one moving atmosphere underneath;
- one optional clock sheet on top.

The sheet stack naturally enforces the current contract: one background and at most the fixed clock overlay. Opacity, placement, stale behavior, and fallback unfold inside the clock/safety sheets. The preview never mutates live output.

Scene memory is explicit and readable:

- **Saved exactly** — current layout matches the saved scene preset;
- **Unsaved arrangement** — the isolated draft differs from the saved layout;
- **Wall changed elsewhere** — live revision drift makes apply unsafe until reload.

“Save layout only” states the persistence boundary at the point of action. Vibe, plant semantics, brightness, FPS, and pace are deliberately excluded.

### 4. Room character

Neutral, Quiet, Cozy, Vivid, and Celebration are presented as a **room character**, independent from every look and scene. This makes the global relationship understandable: a person can keep the same scene and warm it up, quiet it down, or make it celebratory.

Brightness, frame target, and operator pace are also whole-wall controls. They sit in a progressive “operator” fold because most household changes do not require them.

### 5. Plant language

Plant semantics are translated into verbs and constrained by their actual composition rules.

- Several light behaviors may coexist: illuminate, shadow, refract, hue shift, liquid glass, and emitter.
- Exactly zero or one field behavior can be chosen: attractor, repulsor, or slow zone.
- Exactly zero or one surface behavior can be chosen: obstacle, portal, bumper, hazard, or habitat.

The remote writes a plain-language sentence describing the resolved combination. Protocol names remain as secondary labels so an expert can verify what will be sent.

### 6. Live line

The always-visible **live line** is the appliance’s truth surface. Green means the physical wall. Violet tape means an isolated preview. Confirmation contrasts “Live now” with “Will become live” and repeats the complete destination name.

Stop is always one tap away. Starting restores the previous local live state. In this prototype all state is in-memory and no backend call is made.

## Expert unfolding

Low technical literacy does not mean hiding system truth. The interface reveals it in increasing depth:

1. household outcome and complete look name;
2. source, provider, role, and availability;
3. scene sheet details and persistence state;
4. output and plant contract controls;
5. plain-language wall care;
6. technical evidence and the maintainer shelf.

Developer/test content, native package evidence, quarantine, raw receiver evidence, and calibration remain reachable through **More** and **Wall care**, but do not dominate the home surface.

## Health model

Health starts with an observable claim rather than a green score: **“The picture is reaching all four wall sections.”** The four receiver sections are drawn as parts of the physical wall.

The demonstrated expected-degraded state distinguishes:

- transport operational;
- all four sections receiving output;
- only two sections reporting telemetry;
- telemetry incomplete;
- release acceptance blocked;
- visual verification still required.

Symptom paths translate “dark section,” “occasional flash,” “sluggish motion,” and “stopped clock” into the next evidence to gather. Technical evidence is available below. The prototype preserves the important caveat that zero receiver errors cannot prove WS2812 signal, power, grounding, or strip health.

## Responsive behavior

Desktop uses three roles across the page: intention, physical preview, and chosen look. The catalog river follows below.

Phone stacks those roles in the same decision order, makes the live line a full-width truth surface, keeps the five core appliance areas in a bottom remote, and moves specialist tools into **More**. Complete names wrap; they are never ellipsized.

## What is intentionally lower fidelity

Painter, emoji arrangement, and plant-mask editing are reachable and lightly interactive, but labeled as lower-fidelity concepts. Developer ledger rows are also navigational concepts rather than implemented tools. Camera calibration, photographed acceptance, actual native bundle validation, and hardware telemetry are not simulated beyond their product states.
