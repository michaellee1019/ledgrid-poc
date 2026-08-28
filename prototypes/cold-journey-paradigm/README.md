# Lumen Path prototype

A dependency-free interactive concept for controlling the 32 × 138 living-plant LED wall through purposes, journeys, turns, and rehearsals—not a content taxonomy.

## Run

From this directory:

```bash
python3 -m http.server 4173
```

Then open `http://127.0.0.1:4173`.

You can also open `index.html` directly. No network, install, build step, backend, or hardware connection is used.

## Recommended walkthrough

1. Choose **Calm the room** on Trailhead.
2. Select turns and scrub the 45-minute private rehearsal.
3. Open **Tune this moment**, change the background until **Compiled Rainbow** appears, and notice the receiver-native preview disclaimer.
4. Validate the scene and use **Save layout only** to see saved-versus-live drift remain explicit.
5. Choose **Take journey live**, acknowledge the physical wall, and hold the confirmation for one second.
6. Open the live ribbon to stop/resume, change brightness, or power output.
7. Visit **Field guide**. It shows all 292 possibilities. Search/filter, add up to three to the comparison deck, and inspect readiness/provider/role truth.
8. Open **Vibe & plants** to see independent global atmosphere controls.
9. Visit **Wall care** and expand the expected-degraded receiver explanation.
10. Resize to about 390 px wide to exercise the phone composition.

Secondary paths include Host guests, Play together, Paint with light, scheduled arcs, point/hole/D-pad interaction, emoji/painter handoffs, mask inspection, and deeper developer tools. These are intentionally marked lower fidelity where appropriate.

## Static checks

```bash
python3 checks.py
```

The checker uses only Python’s standard library (and runs `node --check` when Node is available). It verifies local asset references, unique IDs, exact catalog arithmetic (52 components / 292 possibilities), safety copy, responsive CSS, absence of external/network dependencies, and JavaScript syntax.

## Files

- `index.html` — semantic prototype structure and all major product states
- `styles.css` — responsive desktop/phone visual system
- `app.js` — generated 292-item field guide, wall simulation, state model, routing, comparison, confirmation, and tools
- `PARADIGM.md` — rationale, vocabulary, walkthrough, safety model, and scope
- `checks.py` — dependency-free static acceptance checks

## Safety and scope

This directory is a self-contained simulation. It does not import live frontend assets, call repository APIs, start the controller, touch run state, or operate the physical wall.
