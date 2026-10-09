# Animation plugins

The installed wall is 33×138. Python renders complete RGB frames on the host;
five receivers transport and display widths `(8,8,8,8,1)`. Browser previews use
the retained Python engines through Pyodide and the same fixed calibration.

## Package contract

Each built-in animation owns one directory:

```text
animation/plugins/<plugin_id>/
├── __init__.py       # AnimationBase subclass and plugin-specific code
├── manifest.json     # stable registry metadata
├── presets/          # curated JSON presets
├── tests/            # focused unit and behavior tests
└── assets/           # optional files used only by this plugin
```

Only `__init__.py` and `manifest.json` are required. Framework and lifecycle
contracts live in `animation/core/` with tests under `animation/core/tests/`.
Reusable rendering or simulation primitives used by multiple plugins belong in
`animation/libraries/` with tests under `animation/libraries/tests/`.

The package directory and manifest `plugin_id` must agree, and the manifest's
`class` must name the package's one concrete animation class. `icon` is required;
`gallery` is either `show` or `test`. Built-in packages are discovered in sorted
`plugin_id` order. Flat `.py` plugins remain supported only for explicitly
configured external plugin directories.

Existing manifests without component fields retain the Python-background
compatibility default. Newly authored components declare `provider`, `role`,
`entrypoint`, and `cadence` together and set `manifest_version` to `1`. The
current host loader accepts Python
`background`, `overlay`, and compatibility `full_scene` roles and fails closed
when only part of that metadata is present. `clock_overlay` is the reference
explicit overlay; the existing `clock` remains the preset-compatible full scene.

Discovery first scans manifest JSON into versioned descriptors without importing
plugin implementations. The Python adapter then binds the allowlisted class,
classifies stateful implementations, and adds the validated parameter schema and
actual no-config defaults. The unified catalog is filterable by provider and
role. Stateful animations and legacy Clock remain catalog-visible `full_scene`
compatibility components with an explicit non-composable diagnostic. Composer
is their only browser surface; catalog visibility never supplies an executable
browser route.

Root `presets/animations/<plugin_id>/` is a user-writable runtime overlay.
Do not place curated source presets there.

## Rendering and authoring

Use the current Scene v2 component catalog and local parameter schemas.
Composer combines a host background, one animation, Widgets, a Look, and plant
effects. `solid_background` is the inexpensive plain background. Native-only
backgrounds and World Flags are unsupported; saved references remain intact and
produce errors rather than selecting replacements.

Keep simulation time independent of paint cadence, reuse frame buffers, and
report unchanged frames accurately. The controller copies frames before handing
them to its asynchronous presenter. Operator tempo and authored Look pace are
separate controls. Preserve deterministic seeds when changing simulation code.

Run focused plugin tests and `just test-composer-current`. Benchmark changed
per-frame work on the installed wall; desktop timing is development evidence.
Build distributable browser/catalog assets with
`python tools/build_browser_composer_assets.py`. Generated output is ignored by
Git and rebuilt in the deployment staging directory using wall calibration.

See [current validation](CURRENT_COMPOSER_VALIDATION.md) and the animation skill
for the accepted editing and validation workflow.
