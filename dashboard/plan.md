# Dashboard Module Implementation Plan

## Goal
Build a new top-level Python module `dashboard/` as the primary home for all dashboard-specific logic (time display, weather, transit, layout, typography, data adapters). Keep `animation/dashboard.py` as a minimal shim that only adapts the `dashboard` module to the existing animation plugin interface.

## Architecture Decision
- Primary code location: `dashboard/`
- Animation integration boundary: `animation/dashboard.py` (thin wrapper only)
- Existing plugin lifecycle (`AnimationBase`) remains unchanged for compatibility with the current web control/API flow.

## Module Structure
Create the following new package layout:

```text
dashboard/
  __init__.py
  plan.md
  plugin.py                # DashboardAnimationPlugin: core dashboard runtime
  scene.py                 # Scene state model + orchestrator
  layout.py                # Regions, anchors, responsive composition
  renderer.py              # Frame compositor and pixel buffer helpers
  text.py                  # Bitmap text rendering utilities
  fonts/
    __init__.py
    font_3x5.py
    font_5x7.py
    symbols.py             # Optional icon glyphs (weather/transit)
  widgets/
    __init__.py
    base.py                # Widget contract
    clock.py               # Time widget (MVP)
    weather.py             # Weather widget (phase 2)
    transit.py             # Bus arrival widget (phase 2)
  data/
    __init__.py
    models.py              # Typed payloads
    cache.py               # TTL cache + stale fallback
    scheduler.py           # Background refresh coordination
    providers/
      __init__.py
      base.py
      clock.py
      weather.py
      transit.py
  config.py                # Config loading/validation for dashboard
  errors.py                # Domain-specific exceptions and fallback helpers
```

## Shim Strategy (`animation/dashboard.py`)
`animation/dashboard.py` will only:
- import `DashboardAnimationPlugin` from `dashboard.plugin`
- expose metadata constants if needed by plugin discovery UI
- subclass or alias into an `AnimationBase`-compatible class name expected by loader
- map `generate_frame(...)` and `get_parameter_schema(...)` through with no business logic

Constraints for shim:
- no provider implementations
- no font/layout code
- no widget rendering logic
- only compatibility glue and small parameter pass-throughs

## Agent Workstreams (Amended)

### Agent A: Core Runtime + Plugin Bridge
- Implement `dashboard.plugin.DashboardAnimationPlugin` with:
  - `update_data(now)` low-frequency path
  - `render_frame(now, frame_count)` high-frequency path
- Implement `animation/dashboard.py` thin adapter.
- Register plugin name in `animation/core/manager.py` allow-list.

Deliverable:
- Dashboard plugin starts/stops like any existing animation.

### Agent B: Typography + Rendering Foundation
- Implement bitmap font assets under `dashboard/fonts/`.
- Implement `dashboard/text.py` draw primitives:
  - glyph lookup
  - clipped text drawing
  - configurable spacing and color
- Implement `dashboard/renderer.py` pixel buffer composition helpers.

Deliverable:
- Reusable text rendering for clock/weather/transit without duplication.

### Agent C: Layout + Widget Composition
- Implement panel-aware layout engine in `dashboard/layout.py`:
  - safe margins
  - regions (header/body/footer)
  - anchor-based placement
- Implement widget interface in `dashboard/widgets/base.py`.
- Implement `ClockWidget` MVP in `dashboard/widgets/clock.py`.

Deliverable:
- Clock display stable across panel dimensions and strip mappings.

### Agent D: Data Layer + Extensibility
- Implement provider contracts in `dashboard/data/providers/base.py`.
- Implement `ClockProvider` immediately.
- Stub `WeatherProvider` and `TransitProvider` with non-blocking fallback behavior.
- Implement TTL/stale cache in `dashboard/data/cache.py`.
- Implement scheduler so data fetches never block render loop.

Deliverable:
- Extensible provider framework with predictable performance/failure behavior.

### Agent E: Controls, Config, and Validation
- Define dashboard parameters (format, timezone, enabled widgets, palette).
- Add optional config file support (e.g. `config/dashboard.json`) via `dashboard/config.py`.
- Ensure runtime parameter updates integrate with existing `/api/parameters` behavior.
- Add tests for renderer/layout/cache/provider fallback.

Deliverable:
- Dashboard is configurable in real time and safe under missing/failed data sources.

## Phase Plan

### Phase 1 (MVP, clock-only)
- Create package skeleton under `dashboard/`.
- Implement renderer/text/fonts/layout/widget base + clock widget.
- Build shim `animation/dashboard.py`.
- Add to allow-list and verify starts from web UI.

Exit criteria:
- Current time renders correctly and updates every second without dropping frame loop behavior.

### Phase 2 (extensibility baseline)
- Add data scheduler and provider interfaces.
- Add weather/transit widget placeholders that display fallback status.
- Add config loading and validation.

Exit criteria:
- System runs with and without external APIs configured; no blocking/hangs.

### Phase 3 (real data integrations)
- Implement weather and transit providers.
- Add transitions/animations for data changes.
- Harden retries/backoff/timeout behavior.

Exit criteria:
- External data appears reliably and degrades gracefully on API failures.

## Technical Contracts

### Render contract
- Rendering path must be deterministic and non-blocking.
- No network I/O on frame generation path.
- Frame generation must tolerate missing data and still return a valid full frame.

### Data contract
- Providers return typed payloads + freshness metadata.
- Cache provides latest-good value on provider failure.
- Scheduler enforces per-provider refresh intervals and timeouts.

### Widget contract
- Widget API should separate:
  - `consume(data_snapshot, now)` for state updates
  - `render(buffer, layout_slot)` for drawing
- Widgets should not call network directly.

## Risks and Mitigations
- Risk: Shim grows into another logic hub.
  - Mitigation: Enforce “no business logic in `animation/dashboard.py`” in code review.
- Risk: External API latency affects FPS.
  - Mitigation: background scheduler + cache + strict timeouts.
- Risk: Text illegibility on dense panel geometry.
  - Mitigation: two font sizes + layout safe zones + clipping tests.
- Risk: Parameter sprawl.
  - Mitigation: grouped schema defaults and strict validation in `dashboard/config.py`.

## Testing Plan
- Unit tests:
  - `tests/unit/test_dashboard_text.py`
  - `tests/unit/test_dashboard_layout.py`
  - `tests/unit/test_dashboard_cache.py`
  - `tests/unit/test_dashboard_plugin_mvp.py`
- Integration checks:
  - plugin discoverability and start/stop from web UI
  - parameter update path through existing `/api/parameters`
- Manual validation:
  - clock correctness over minute rollovers
  - graceful fallback when weather/transit unavailable

## First Implementation Slice (Immediate Next Work)
1. Scaffold `dashboard/` package with `__init__.py`, `plugin.py`, `scene.py`, `layout.py`, `renderer.py`, `text.py`.
2. Create minimal fonts (`3x5`, `5x7`) and a `ClockWidget`.
3. Implement `animation/dashboard.py` shim and wire allow-list entry.
4. Verify via existing server workflow that dashboard animation appears and runs.
