# Wall Studio — Finder UX prototype

Wall Studio is an independent, dependency-free interaction prototype for the 32 × 138 LED plant wall. It explores a late–Big Cat-era Mac utility paradigm: a persistent physical-output strip, Finder source list, hierarchical column browser, outline/table alternatives, contextual inspectors, attached sheets, and compact operational evidence.

It contains fixture data only. No action calls or mutates the live backend.

## Run it

From this directory:

```sh
python3 -m http.server 8088
```

Then open `http://localhost:8088/`.

No package install, build, font download, image download, or network access is required. Canvas previews are generated locally by `app.js`.

Run the lightweight checks with:

```sh
node checks/check-structure.mjs
```

## Review paths

1. **Browse at catalog scale.** Start in All Content. Move through category → component → preset. Search for `native`, `clock`, or `calibration`. Long identities wrap in place. Unsupported catalog entries remain visible and disabled.
2. **Inspect without performing.** Change Motion, Density, or Palette. The preview provenance remains explicit and the live strip does not change.
3. **Compare.** Use the toolbar’s third view button or **Add to Compare**. Three aspect-correct candidates show complete preset, component, provider, role, and provenance identities. Choose **Take This Live…** to see the deliberate sheet.
4. **Operate safely.** The graphite live strip always shows physical-wall identity, saved state, frame rate, power, brightness, and Stop. Stop is immediate and leaves power on. Power-off uses a consequential sheet.
5. **Compose a scene.** Open **Scene Composer** in the source list. Select Background, Clock Overlay, or Validation in the outline to change the inspector. Validate, save layout, or perform through explicit actions.
6. **Edit independent globals.** Open **Vibe & Plant Material**. Change the segmented vibe and exclusive Field/Surface pop-up menus. Apply through a sheet that states presets and scenes remain unchanged.
7. **Diagnose.** Open **Operations**, disclose evidence groups, and select Receiver C. Its configured reduced-telemetry state is explained as expected degradation, not a generic failure.
8. **Review on phone.** At 760 px or below, library navigation becomes source → category → component → preset → detail drill-in with a toolbar Back button. Controls use 44 px touch targets.

## Files

- `index.html` — semantic application structure and all workflow destinations
- `styles.css` — local Aqua/graphite visual system and responsive hierarchy
- `app.js` — exact-scale fixture catalog, canvas renderer, interaction state, sheets, and drill-in navigation
- `PRODUCT_DECISIONS.md` — product paradigm, assumptions, gaps, and backend mapping
- `checks/check-structure.mjs` — dependency-free structural and accessibility smoke checks

## Fixture scale

The catalog generates exactly 52 components and 292 presets: 32 components have six presets and 20 have five. Categories contain 15 ambient, 8 clock/information, 8 interactive/game, 7 GIF/pixel-art, 7 diagnostic/calibration, and 7 developer/test components. Eleven components use the receiver-native fixture provider. Forty-three are show-ready; unsupported states remain catalog-visible.
