# Browser scene contract v1

`ledgrid.browser-scene` version 1 is the portable, untrusted-document boundary
for the browser composer. It represents exactly one opaque background, zero or
one fixed Clock layer, one installation-profile reference, and one known Python
fallback. The host adapts a validated document to `ledgrid.scene-state` only at
the existing composition/activation boundary.

## Document

```json
{
  "schema": "ledgrid.browser-scene",
  "schema_version": 1,
  "revision": 17,
  "background": {
    "provider": "python",
    "component_id": "gradient",
    "component_digest": "<lowercase sha256>",
    "runtime_digest": "<lowercase sha256>",
    "parameter_schema_version": 1,
    "parameters": {"speed": 0.7}
  },
  "layers": [
    {
      "role": "clock",
      "component": {
        "provider": "python",
        "component_id": "clock_overlay",
        "component_digest": "<lowercase sha256>",
        "runtime_digest": "<lowercase sha256>",
        "parameter_schema_version": 1,
        "parameters": {"show_seconds": true}
      },
      "enabled": true,
      "opacity": 220,
      "blend_mode": "source_over"
    }
  ],
  "installation_profile": {"digest": "<lowercase sha256>"},
  "fallback": {
    "provider": "python",
    "component_id": "gradient",
    "component_digest": "<lowercase sha256>",
    "runtime_digest": "<lowercase sha256>",
    "parameter_schema_version": 1,
    "parameters": {"speed": 0.7}
  }
}
```

`preset_id` plus `preset_fingerprint` may be included together on a component
reference. Every other field is closed: unknown fields, providers, components,
roles, blend modes, parameter names, or schema versions fail validation.

For a Python background, `fallback` must match its component and parameters.
A receiver-native background must name a separate Python fallback. Parameter
types, finite numeric values, options, and ranges are checked against the exact
catalog schema. Path parameters accept only managed relative defaults/options;
they cannot introduce an absolute, traversing, or unknown asset path.

## Catalog binding and capability

Composer bootstrap records and `/api/v1/components` expose:

- `component_digest`
- `parameter_schema_version`
- `browser_runtime.digest`
- `browser_capabilities.previewable`
- `browser_capabilities.saveable`
- `browser_capabilities.activation_ready`
- `browser_capabilities.reason`
- `browser_capabilities.managed_identity`

The managed identity repeats provider, component ID, component digest, runtime
digest, and parameter-schema version. Receiver-native identities additionally
contain the managed bundle and expected payload digests. A document must match
these values exactly. Preview-only components remain saveable when their runtime
is verified, while activation still fails closed with the catalog reason.

## HTTP boundaries

- `POST /api/v1/composer/presets/validate` accepts a browser-scene document
  directly for read-only import validation. The response kind is
  `browser_scene`; its draft contains both the canonical `browser_scene` and the
  adapted legacy `scene`.
- `POST /api/v1/composer/presets` remains the component-preset endpoint. Its
  request is `ledgrid.browser-composer-save` version 1 with `component_key`,
  `name`, `params`, and `overwrite`; it does not accept a full browser scene.
- `POST /api/v1/scene-presets` accepts `{name, description?, scene}` where
  `scene` may be the browser-scene document directly. It persists that canonical
  browser document under the existing scene-preset envelope.
- `POST /api/v1/scene/validate` accepts the browser-scene document directly and
  returns `{valid, scene, preset_diagnostics}`. `scene` is the adapted host
  payload so existing controller clients remain compatible.
- `POST /api/v1/scene/checks` accepts an envelope containing the exact
  browser-scene `scene`, `global_settings`, and advisory `browser_evidence`. When
  the development/canary capability is enabled, it binds those values to the
  current controller session, state revision, live identity, selected
  installation profile, runtime identities, and qualification record for 120
  seconds. The `201` response contains a single-use `check_token`, canonical
  `basis`, `basis_digest`, expiry, and qualification result. This Check does not
  change wall output.
- `PUT` or `POST /api/v1/scene` is the guarded activation submission boundary.
  It does **not** accept a bare browser-scene document as authority. The request
  must repeat the exact checked `scene` and `global_settings`, carry the
  `check_token` plus expected controller session and state revision, and include
  an `Idempotency-Key` header. Missing preconditions fail with `428`; expired,
  reused-for-different-input, or drifted checks fail closed without queueing a
  controller command.
- An accepted exact submission returns `202` with a durable `activation_id`,
  current `phase`, `pending: true`, `status_url`, and exact-retry result. Queue
  acceptance is Pending, never Active.
- `GET /api/v1/scene/activations/<activation_id>` is the correlated status
  boundary. Only a fresh terminal `active` observation whose complete identity
  matches the checked basis may be presented as Active. The resource separately
  records requested, normalized, and observed identity; controller revisions;
  telemetry completeness/freshness; rollback availability/result; optional
  camera observation; and any error.
- `DELETE /api/v1/scene/activations/<activation_id>` requests correlated
  cancellation, and
  `POST /api/v1/scene/activations/<activation_id>/rollback` requests the retained
  exact-snapshot rollback only when the status resource advertises it.
- Bare `DELETE /api/v1/scene`, component PATCH, legacy start, and preset-apply
  aliases fail closed. The separate `POST /api/stop` action remains the immediate
  safety Stop for any output mode; it is not a scene-activation shortcut.

## Import limits

Untrusted import/save JSON is limited to 256 KiB, 16 nesting levels, 4,096
values, and 16 KiB per string. Non-finite numbers and the keys `__proto__`,
`constructor`, and `prototype` are rejected before persistence or activation.
