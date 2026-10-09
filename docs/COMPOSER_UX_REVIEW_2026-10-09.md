# Composer UX review — October 9, 2026

Scope: current Browse / Edit / Playlists workspace, desktop and phone. This is a
source and isolated-browser review, not a physical-device usability study or wall
acceptance. Tracked in `ledgrid-poc-ib7.148`.

## Design basis

Composer is a live instrument with a catalog and an inspector. Retain its wide
catalog / preview / inspector split, with task navigation on narrow screens.
[Apple’s split-view guidance](https://developer.apple.com/design/human-interface-guidelines/split-views)
supports adjacent panes for related content; this mapping is our application of
that pattern, not a requirement to reproduce a native toolkit.

Keep frequently used controls readily available and visually group related
navigation, following [Apple’s toolbar guidance](https://developer.apple.com/design/human-interface-guidelines/toolbars).
Keep secondary settings discoverable through disclosure, following
[NN/g’s progressive-disclosure guidance](https://www.nngroup.com/articles/progressive-disclosure/).
Existing Output performance, optional Widgets/Plants, and Connection details
already follow this principle. Do not add another settings layer.

Compactness should come from less chrome and repetition, not smaller hit areas.
[WCAG 2.2 target-size guidance](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum)
sets a 24 CSS-pixel minimum with exceptions; this project retains its more generous
44-pixel phone controls. This review does not certify full WCAG conformance.

## Findings and implemented changes

| Finding | Change | Result |
| --- | --- | --- |
| Phone collections wrap to two rows and compete visually with main navigation. | One segmented row, shorter labels, quieter selected surface. | Animations, Favorites and Saved stay together at 320 and 390 pixels. |
| Browse gives a secondary playlist action primary-button weight. | Use the existing text-button style and concise label. | Action remains reachable without dominating the catalog. |
| Catalog repeats an eyebrow, Gallery heading and search terminology. | Remove the redundant eyebrow visually; use Animations consistently. | Clearer language and less vertical chrome. |
| Tall thumbnails and cards limit scanning. | Reduce thumbnail width while preserving 33:138 aspect; use two-line descriptions. | First card approximately 118px tall versus 168px before; full description remains in selection detail. |
| Mobile text fields inherit small type; safe-area top/side padding is overridden. | 16px field text and restored safe-area padding. | More legible input and retained inset handling; real iOS behavior still needs device evidence. |
| Focus treatment varies between ordinary buttons and semantic controls. | Explicit focus-visible outline for buttons, disclosure, inputs and selects. | Keyboard position remains apparent. |

At 390×844, the first card begins near y=337 instead of y=399. Navigation targets
remain at least 44px high. These measurements describe the isolated fixture.

## Architecture and remaining observations

Keep presentation changes in the template and CSS. Existing layout code owns
navigation and panel preferences; existing scene logic owns edits/publication;
observed wall receipts own Active state. This pass does not modify those contracts.
The service-worker shell version is advanced so returning installations can obtain
the updated presentation through the existing update flow.

The phone preview rail and persistent output area still consume significant room.
Changing either needs a specific interaction design for preview access and always
reachable Stop, rather than simply hiding status. No new obligation is opened;
revisit if phone use shows those regions prevent essential editing.

`CURRENT_UX_ACCEPTANCE.md` still describes draft-first activation and catalog
expansion, whereas the current interface explicitly performs live edits and the
current repository instructions constrain expansion. Treat that document as stale
for those claims. A future contract-document reconciliation should use the current
accepted behavior; this visual pass does not redefine activation semantics.

## Validation

- 23 focused desktop/responsive and offline-shell/PWA tests pass.
- New density probe: 320, 390 and 1440px; single-row collections, touch targets,
  bounded card size, no horizontal overflow, search, selection, Edit, Playlists,
  Preview visibility and keyboard traversal.
- Existing controls probe: 390 and 1440px; primary controls, advanced field focus
  and values, Widgets and Plants remain reachable.
- Screenshots inspected at phone and desktop sizes.

The full native-build fixture could not start because it requires PlatformIO
6.1.19 and the environment has 6.2.0. Browser checks instead used the existing
Composer unit-test app with temporary stores and a fake wall channel. Its offline
and failed-live-update messages are expected; no installed-wall success is claimed.
