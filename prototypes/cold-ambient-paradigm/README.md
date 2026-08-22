# Room Tune prototype

Room Tune is a dependency-free interactive concept for controlling the 32 × 138 living LED plant wall as a calm household appliance.

## Run

From this directory:

```sh
python3 -m http.server 4178
```

Then open `http://127.0.0.1:4178/`.

It also works directly from `file://` because there are no fetches, network assets, modules, or backend requests.

## Suggested walkthrough

1. On **Tune**, change “Make the room…” from settle to another household outcome.
2. Use the arrows, search, or the look river. Open **Compare** to see two synchronized wall-shaped previews.
3. Select a ready look and choose **Set the live wall to…** Observe the explicit live confirmation and updated green live line.
4. Choose **Add a clock over this look**. Change opacity or placement to produce the **Unsaved arrangement** state, save the layout, then simulate external drift.
5. Open **Room + plants**. Change room character, combine visual plant behaviors, and verify that field and surface choices are exclusive.
6. Open **Play** and tap/drag the wall, switch to hole mode, or use the D-pad.
7. Open **Wall care**, pick an observed symptom, then unfold technical evidence.
8. Use **More** for lower-fidelity painter/emoji/mask concepts and the secondary developer shelf.
9. Stop and restart the wall from the always-visible live line.

## Files

- `index.html` — semantic application structure and product copy
- `styles.css` — responsive appliance UI, exact-proportion wall surfaces, focus/reduced-motion support
- `app.js` — contract-scale catalog fixture, rendering simulation, state transitions, compare/scene/health/play interactions
- `PARADIGM.md` — product model and rationale
- `check.mjs` — dependency-free structural and contract checks

## Validate

```sh
node --check app.js
node check.mjs
```

The prototype performs no backend mutation. All “live” actions update only in-memory demo state and are clearly described as prototype behavior where relevant.

