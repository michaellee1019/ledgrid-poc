# Lumen Loom prototype

A dependency-free, cold-concept control prototype for the 32×138 living-plant LED wall. It explores a material atlas, poster-scale isolated previews, direct scene weaving, independent room/plant climates, and a deliberate take-live threshold.

## Run

Open `index.html` directly, or serve this directory:

```sh
cd prototypes/cold-spatial-paradigm
python3 -m http.server 8080
```

Then open `http://localhost:8080`.

No build, package install, server API, or network access is required. The take-live interaction changes only in-memory prototype state.

## Core walkthrough

1. Scan the atlas overview. It begins with all 52 components and all 292 presets visible as strands.
2. Filter by a material family or “call a quality” such as `clock`, `receiver`, `playable`, or `mist`.
3. Touch strands to place up to three tall moving specimens on the isolated light table.
4. Use a specimen's place action, double-click a strand, or drag a specimen onto the ghost wall.
5. Adjust the background/overlay weave, clock opacity/placement/stale policy/fallback, or tap the wall in Point/Hole/D-pad mode.
6. Change Global Vibe and Plant Material independently. Adjust FPS ceiling and speed.
7. Validate, then use **Save layout only** and observe that the wall remains staged apart.
8. Choose **Cross the threshold**, inspect the old→new receipt, then hold for 1.2 seconds to simulate taking the scene live.
9. Open **Wall pulse** and simulate drift to inspect the distinct saved/live/observed state.
10. Resize below 560 px to exercise the phone posture.

Build-only, unavailable, and quarantined strands can be previewed but cannot be staged. Full-scene material removes an incompatible clock overlay.

## Checks

```sh
./check.sh
```

The check verifies JavaScript syntax, the exact corpus contract, required safety/state copy, responsive styles, local-only assets, and the absence of network/hardware mutation primitives.

## Files

- `index.html` — semantic work surfaces and dialogs
- `styles.css` — spatial/material desktop and phone design
- `app.js` — generated 52/292 corpus, canvas loops, direct manipulation, and state machine
- `PARADIGM.md` — concept rationale, invariants, and production boundary
- `check.mjs` / `check.sh` — dependency-free static verification

## Scope

Core composition and safety interactions are functional. Painter/mask persistence, emoji physics, actual games, diagnostics execution, calibration editing, and developer operations are explicitly lower-fidelity. No backend files, live configuration, or hardware are touched.
