# Frame Painter Plan

## Goal
Add a utilitarian web UI pane for pixel-art painting that can:
- Edit per-pixel color and brightness.
- Push edits to the physical LED wall with low latency.
- Save/load frames as JSON presets.
- Re-open presets for editing.

## Design

### UI
- New route/page: `/painter` (`web/templates/painter.html`).
- Canvas-based editor sized to the active LED layout (`strip_count x leds_per_strip`).
- Tools:
  - Color picker.
  - Brightness slider.
  - Erase mode.
  - Pixel size (zoom) slider.
  - Clear frame button.
  - Push full frame button.
- Presets panel:
  - Save preset by name.
  - List presets from disk.
  - Open preset into canvas and push to hardware for live editing.

### Data Path
- Painter updates use existing file-based IPC (web process -> controller process).
- New controller commands:
  - `painter_apply_updates`: sparse per-pixel updates.
  - `painter_set_frame`: replace full frame.
  - `painter_clear`: clear output.
- `AnimationManager` maintains painter frame state and pushes to hardware immediately (`set_all_pixels`, optional `show`).

### Preset Storage
- Directory: `presets/frame_painter/`.
- One preset per JSON file, keyed by sanitized preset id.
- Payload shape (v1):
  - `preset_id`, `name`, `created_at`, `updated_at`
  - `led_info`
  - `frame_encoding`
  - `frame_data_length`
  - `frame_data_encoded`

## Implementation Phases

### Phase 1: Backend painter mode
- Add painter state to `AnimationManager` (frame buffer + active mode).
- Add methods to apply sparse updates, set full frame, and clear.
- Add command handling in `scripts/start_server.py`.

### Phase 2: Web API + preset endpoints
- Add painter update/frame/clear APIs.
- Add painter preset list/load/save APIs.
- Add safe preset id sanitization and atomic preset writes.

### Phase 3: Painter UI
- Add `/painter` page and navbar entry.
- Implement canvas painting interactions and throttled update flush.
- Implement preset UX for save/open/refresh.

### Phase 4: Latency tuning
- Lower default controller poll interval from `0.5s` to `0.05s`.
- Keep painter flush interval above poll period to avoid command overwrite in single-file IPC.

## Open Questions
- Should painter mode be treated as “running” in global status UI, or remain a distinct idle-like mode?
- Should presets include human tags/categories (e.g., `holiday`, `testing`)?
- Do we want undo/redo (client-side stack) before adding more drawing tools?
- Should we support an export/import endpoint for preset bundles?
