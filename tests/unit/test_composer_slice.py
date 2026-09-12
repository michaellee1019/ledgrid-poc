"""Focused local UI/API proof for the bounded Composer slice."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from animation.plugins.clock_overlay import ClockOverlayAnimation
from animation.plugins.conway_life import ConwayLifeAnimation
from web.app import AnimationWebInterface
from web.composer_final_preview import NATIVE_AURORA_BUNDLE_DIGEST
from web.scene_look_store import SceneLookStore
from web.working_draft_store import WorkingDraftStore


class _Controller:
    strip_count = 33
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip


class _PreviewManager:
    controller = _Controller()
    plugin_loader = None


class _WallChannel:
    """Historical controller channel: Composer must never write to it."""

    def __init__(self) -> None:
        self.commands: list[dict] = []

    def send_command(self, action: str, **data: object) -> None:
        self.commands.append({"action": action, **data})


def _scene(overlays: list[dict] | None = None) -> dict:
    return {
        "origin": "composer",
        "scene": {
            "schema": "ledgrid.scene.v1",
            "vibe": "quiet",
            "master_brightness": 1,
            "background": {
                "slot_id": "background",
                "component_id": "aurora_curtains",
                "version": 1,
                "provider": "python",
                "role": "background",
                "parameters": {
                    "curtain_density": 0.56,
                    "fold_depth": 0.58,
                    "glow_intensity": 0.62,
                    "source_fps": 30,
                    "seed": 4201,
                },
            },
            "overlays": overlays or [],
        },
    }


def _overlay(slot_id: str, parameters: dict | None = None) -> dict:
    return {
        "slot_id": slot_id,
        "component": {
            "component_id": "clock_overlay",
            "version": 1,
            "provider": "python",
            "role": "overlay",
            "parameters": parameters or {},
        },
        "enabled": True,
        "opacity": 192,
        "placement": {
            "strip_translation": 0,
            "led_translation": 0,
            "clip_policy": "clip_to_wall",
        },
        "stale_policy": {"policy": "hold"},
    }


def _conway(slot_id: str = "conway_lower", parameters: dict | None = None) -> dict:
    return {
        "slot_id": slot_id,
        "component": {
            "component_id": "conway_life", "version": 1,
            "provider": "python", "role": "overlay", "parameters": parameters or {},
        },
        "enabled": True, "opacity": 190,
        "placement": {"strip_translation": 0, "led_translation": 0, "clip_policy": "clip_to_wall"},
        "stale_policy": {"policy": "hold"},
    }


def _current_scene() -> dict:
    return {
        "schema": "ledgrid.scene.v2",
        "background": {"component_id": "native_aurora", "version": 1, "provider": "receiver_native", "role": "background", "bundle_digest": NATIVE_AURORA_BUNDLE_DIGEST, "parameters": {"gain": .62, "source_fps": 30, "seed": 4201}},
        "animation": {"component_id": "aurora_curtains", "version": 1, "provider": "python", "role": "animation", "parameters": {"curtain_density": .56, "fold_depth": .58, "glow_intensity": .62, "source_fps": 30, "seed": 4201}},
        "widgets": [], "plants": {"effects": {"version": 1, "active": [], "strengths": {}}},
        "look": {"palette_id": "mist", "pace": .7, "presentation_brightness": .82},
    }


class ComposerSliceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.wall = _WallChannel()
        self.interface = AnimationWebInterface(
            self.wall, _PreviewManager(), local_mode=True,
        )
        self.interface.composer_looks = SceneLookStore(Path(self.tmp.name) / "looks.json")
        self.interface.working_draft = WorkingDraftStore(Path(self.tmp.name) / "recovery.json")
        self.client = self.interface.app.test_client()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_root_is_the_simple_local_composer_not_a_preview_or_dashboard(self) -> None:
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("Operations", html)
        self.assertIn("Installed final", html)
        self.assertIn("conway_life", Path("web/static/js/composer_slice.js").read_text(encoding="utf-8"))
        self.assertIn("tetris", Path("web/static/js/composer_slice.js").read_text(encoding="utf-8"))
        self.assertIn('id="liveAction"', html)
        self.assertNotIn("previewCanvas", html)
        self.assertNotIn("Tools", html)
        self.assertIn("/static/css/composer_slice.css", html)
        self.assertIn("/static/js/composer_slice.js", html)
        self.assertNotIn("/static/css/composer.css", html)

    def test_gallery_projects_each_current_show_animation_once_without_mutation(self) -> None:
        before_draft = self.interface.working_draft.get()
        before_commands = list(self.interface.composer_control.commands)
        response = self.client.get('/api/composer/gallery')
        self.assertEqual(response.status_code, 200, response.get_json())
        payload = response.get_json()
        entries = payload['entries']
        expected = [
            descriptor for descriptor in self.interface.composer_catalog.descriptors
            if descriptor.role.value == 'animation'
        ]
        self.assertEqual(len(entries), len(expected))
        self.assertEqual(
            {(entry['provider'], entry['component_id'], entry['version']) for entry in entries},
            {(descriptor.provider.value, descriptor.component_id, descriptor.version) for descriptor in expected},
        )
        self.assertEqual(len({entry['key'] for entry in entries}), len(entries))
        self.assertNotIn('plant_calibration', {entry['component_id'] for entry in entries})
        self.assertNotIn('strip_order', {entry['component_id'] for entry in entries})
        self.assertTrue(all(entry['available'] and isinstance(entry['preset_count'], int) for entry in entries))
        self.assertRegex(payload['catalog_digest'], r'^[0-9a-f]{64}$')
        self.assertEqual(self.interface.working_draft.get(), before_draft)
        self.assertEqual(self.interface.composer_control.commands, before_commands)

    def test_gallery_uses_fixed_inert_preview_cache_and_single_live_publisher(self) -> None:
        script = Path('web/static/js/composer_slice.js').read_text(encoding='utf-8')
        css = Path('web/static/css/composer_slice.css').read_text(encoding='utf-8')
        html = self.client.get('/').get_data(as_text=True)
        for element_id in ('gallerySearch', 'galleryGrid', 'galleryMore', 'galleryDetail'):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn("const GALLERY_FIXED_PREVIEW", script)
        self.assertIn("preview(galleryScene(entry), GALLERY_FIXED_PREVIEW)", script)
        self.assertIn("`${state.gallery.digest}:${entry.key}:default`", script)
        self.assertIn("state.gallery.rendered += GALLERY_PAGE_SIZE", script)
        self.assertGreaterEqual(css.count("aspect-ratio: 33 / 138"), 4)
        self.assertNotIn("height: 3.85rem", css)
        self.assertNotIn("height: 43px", css)
        selection = script[script.index('async function selectGalleryEntry'):script.index('function renderGallery', script.index('async function selectGalleryEntry'))]
        self.assertIn('await submit(next, {intentToken, rememberEdit: true, previous})', selection)
        self.assertLess(selection.index('if (!intentIsCurrent(intentToken) || published.coalesced) return;'), selection.index('refreshGallerySelection();'))
        self.assertIn('if (!intentIsCurrent(intentToken)) return;', selection)
        self.assertIn("select.setAttribute('aria-current', String(state.scene?.animation?.component_id === entry.component_id))", script)
        self.assertNotIn('/activate', selection)
        self.assertNotIn('/check', selection)

    def test_each_gallery_default_has_a_fixed_inert_33_by_138_preview(self) -> None:
        entries = self.client.get('/api/composer/gallery').get_json()['entries']
        before_draft = self.interface.working_draft.get()
        before_commands = list(self.interface.composer_control.commands)
        for entry in entries:
            with self.subTest(component=entry['component_id']):
                scene = _current_scene()
                scene['animation'] = {
                    'component_id': entry['component_id'],
                    'version': entry['version'],
                    'provider': entry['provider'],
                    'role': entry['role'],
                    'parameters': entry['parameters'],
                }
                response = self.client.post('/api/composer/preview', json={
                    'origin': 'composer', 'scene': scene,
                    'preview': {
                        'monotonic_elapsed': 17,
                        'wall_time': '2026-01-01T12:00:00+00:00',
                    },
                })
                self.assertEqual(response.status_code, 200, response.get_json())
                frame = response.get_json()['frame']
                self.assertEqual((frame['width'], frame['height'], frame['encoding']), (33, 138, 'rgb_u8_base64'))
        self.assertEqual(self.interface.working_draft.get(), before_draft)
        self.assertEqual(self.interface.composer_control.commands, before_commands)

    def test_http_browser_uuid_fallback_is_rfc4122_shaped(self) -> None:
        script = Path("web/static/js/composer_slice.js").read_text(encoding="utf-8")
        helper = script[
            script.index("function newUuid()") : script.index(
                "  // A page lifetime client id"
            )
        ]
        javascript = """
const assert = require('node:assert/strict');
const vm = require('node:vm');
const context = {
  crypto: {getRandomValues(bytes) {
    for (let index = 0; index < bytes.length; index += 1) bytes[index] = index;
    return bytes;
  }},
  Uint8Array,
};
vm.runInNewContext(process.argv[1] + '; result = newUuid();', context);
assert.match(context.result, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
"""
        completed = subprocess.run(
            ["node", "-e", javascript, helper],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(script.count("browserCrypto.randomUUID()"), 1)
        self.assertEqual(script.count("mutation_id: newUuid()"), 2)
        self.assertIn("const selectionMutation = newUuid(); const selectionSequence = ++state.sequence;", script)

    def test_advisory_check_and_scene_submission_keep_wall_channel_inert(self) -> None:
        scene = _current_scene()
        checked = self.client.post("/api/composer/check", json={"origin": "composer", "scene": scene})
        self.assertEqual(checked.status_code, 200)
        self.assertTrue(checked.get_json()["valid"])
        self.assertEqual(checked.get_json()["status"]["state"], "ready")
        live = self.client.post("/api/composer/scene", json={"origin": "composer", "scene": scene, "client_id": "desktop", "mutation_id": "browser-intent-1", "client_sequence": 1})
        self.assertEqual(live.status_code, 200)
        self.assertEqual(live.get_json()["state"], "live")
        self.assertEqual(live.get_json()["wall_mutations"], 0)
        self.assertEqual(self.wall.commands, [])
        self.assertEqual(len(self.interface.composer_control.commands), 1)

    def test_valid_direct_edit_stays_live_and_bad_input_is_rejected(self) -> None:
        first = _current_scene()
        self.assertEqual(self.client.post("/api/composer/scene", json={"origin": "composer", "scene": first, "client_id": "desktop", "client_sequence": 1}).status_code, 200)
        changed = _current_scene()
        changed["look"] = {**changed["look"], "pace": 1.2}
        published = self.client.post("/api/composer/scene", json={"origin": "composer", "scene": changed, "client_id": "desktop", "client_sequence": 2})
        self.assertEqual(published.get_json()["state"], "live")
        rejected = self.client.post("/api/composer/check", json={"origin": "dashboard"})
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(rejected.get_json()["status"]["state"], "live")

    def test_global_speed_pace_renders_persists_and_publishes_while_live(self) -> None:
        scene = _current_scene()
        scene["look"] = {**scene["look"], "pace": 1.35}

        preview = self.client.post(
            "/api/composer/preview",
            json={"origin": "composer", "scene": scene},
        )
        self.assertEqual(preview.status_code, 200, preview.get_json())

        saved = self.client.post(
            "/api/composer/looks",
            json={"name": "Fast global scene", "scene": scene},
        )
        self.assertEqual(saved.status_code, 200, saved.get_json())
        look_id = saved.get_json()["look"]["id"]
        reloaded = self.client.get(f"/api/composer/looks/{look_id}")
        self.assertEqual(reloaded.status_code, 200, reloaded.get_json())
        self.assertEqual(reloaded.get_json()["look"]["scene"]["look"]["pace"], 1.35)

        live = self.client.post(
            "/api/composer/scene",
            json={"origin": "composer", "scene": scene, "client_id": "speed", "client_sequence": 1},
        )
        self.assertEqual(live.status_code, 200, live.get_json())
        self.assertTrue(live.get_json()["published"])
        self.assertEqual(live.get_json()["desired"], live.get_json()["observed"])
        self.assertEqual(self.interface.working_draft.get()["scene"]["look"]["pace"], 1.35)

    def test_preview_exposes_runtime_widget_placement_diagnostics(self) -> None:
        scene = _current_scene()
        scene["widgets"] = [{
            "id": "clock", "component": {"component_id": "clock_overlay", "version": 1, "provider": "python", "role": "widget", "parameters": {}},
            "visible": True, "placement": {"mode": "manual", "strip_translation": 0, "led_translation": -8},
        }]
        preview = self.client.post("/api/composer/preview", json={"origin": "composer", "scene": scene, "preview": {"monotonic_elapsed": 12.0, "wall_time": "2026-08-31T12:00:00+00:00"}})
        self.assertEqual(preview.status_code, 200)
        placement = preview.get_json()["widget_placements"]["clock"]
        self.assertIn("warning", placement)
        self.assertIn("plant_overlap_pixels", placement)

    def test_exact_scene_retry_is_idempotent_without_a_check_token(self) -> None:
        request = {"origin": "composer", "scene": _current_scene(), "client_id": "desktop", "mutation_id": "same-intent", "client_sequence": 1}
        self.assertFalse(self.client.post("/api/composer/scene", json=request).get_json()["exact_retry"])
        retry = self.client.post("/api/composer/scene", json=request).get_json()
        self.assertTrue(retry["exact_retry"])
        self.assertEqual(retry["state"], "live")

    def test_current_recovery_hydrates_the_newest_remote_scene_and_invalidates_undo(self) -> None:
        first = _current_scene()
        self.assertEqual(self.client.post("/api/composer/scene", json={
            "origin": "composer", "scene": first, "client_id": "first-tab", "client_sequence": 1,
        }).status_code, 200)
        initial = self.client.get("/api/composer/recovery?client_id=first-tab").get_json()
        self.assertEqual(initial["recovery"]["scene"], first)
        changed = _current_scene()
        changed["look"] = {**changed["look"], "pace": 1.15}
        self.assertEqual(self.client.post("/api/composer/scene", json={
            "origin": "composer", "scene": changed, "client_id": "second-tab", "client_sequence": 1,
        }).status_code, 200)
        refreshed = self.client.get("/api/composer/recovery?client_id=first-tab").get_json()
        self.assertEqual(refreshed["recovery"]["scene"], changed)
        self.assertTrue(refreshed["status"]["undo_invalidated"])
        self.assertNotIn("draft", refreshed)

    def test_recovery_prefers_the_atomic_live_scene_over_a_stale_persisted_copy(self) -> None:
        first = _current_scene()
        self.client.post("/api/composer/scene", json={"origin": "composer", "scene": first, "client_id": "first", "client_sequence": 1})
        stale_recovery = self.interface.working_draft.get()
        changed = _current_scene()
        changed["look"] = {**changed["look"], "pace": 1.35}
        self.client.post("/api/composer/scene", json={"origin": "composer", "scene": changed, "client_id": "second", "client_sequence": 1})
        # A stale disk read must not be allowed to pair with the newer live
        # revision; /recovery takes the scene from the live-state lock first.
        self.interface.working_draft.get = lambda: stale_recovery
        body = self.client.get("/api/composer/recovery?client_id=first").get_json()
        self.assertTrue(body["recovery"]["authoritative"])
        self.assertEqual(body["recovery"]["scene"], changed)
        self.assertEqual(body["recovery"]["basis"], body["status"]["current"])

    def test_corrupt_recovery_is_a_reachable_data_error_with_current_status(self) -> None:
        self.interface.working_draft.path.write_text(
            '{"schema":"ledgrid.composer.recovery.v2","basis":{},"opened_look_id":null,"saved_at":1,"scene":{"schema":"ledgrid.scene.v2"}}',
            encoding="utf-8",
        )
        response = self.client.get("/api/composer/recovery?client_id=startup")
        self.assertEqual(response.status_code, 400)
        self.assertIn("status", response.get_json())
        script = Path("web/static/js/composer_slice.js").read_text(encoding="utf-8")
        self.assertIn("function recoverFromInvalidRecovery(body)", script)
        self.assertIn("if (response.status >= 500)", script)
        self.assertIn("if (error.serverUnavailable)", script)

    def test_catalog_uses_the_real_clock_overlay_descriptor_and_client_preserves_error_state(self) -> None:
        descriptor = ClockOverlayAnimation.component_descriptor()
        self.assertIs(
            self.interface.composer_catalog.require(
                provider="python", component_id="clock_overlay", version=1,
            ),
            descriptor,
        )
        self.assertIs(
            self.interface.composer_catalog.require(
                provider="python", component_id="conway_life", version=1,
            ),
            ConwayLifeAnimation.component_descriptor(),
        )
        with self.assertRaises(ValueError):
            self.interface.composer_catalog.require(
                provider="python", component_id="alert", version=1,
            )
        script = Path("web/static/js/composer_slice.js").read_text(encoding="utf-8")
        self.assertIn("const requestEndpoint = endpoint || (builtin ? '/built-ins/open' : '/scene');", script)
        self.assertIn("async function flushPublication()", script)
        self.assertIn("renderStatus(result.status || result);", script)
        self.assertIn("refreshInFlight", script)
        self.assertIn("recoveryMatchesStatus", script)

    def test_browser_defaults_live_and_coalesces_to_the_newest_valid_scene(self) -> None:
        html = Path("web/templates/composer.html").read_text(encoding="utf-8")
        script = Path("web/static/js/composer_slice.js").read_text(encoding="utf-8")

        self.assertIn('id="liveAction" class="button primary" type="button">Stop</button>', html)
        self.assertIn("status: {connected: true, running: true, armed: true", script)
        self.assertIn("publication: {queued: null, afterStop: null, inFlight: null, scheduled: false}", script)
        self.assertIn("replacement?.resolve({coalesced: true});", script)
        self.assertIn("if (!body.status?.current) await submit(state.scene);", script)
        self.assertIn("try { await guardedWallActivation(entry.scene, true, entry.targetFps); }", script)
        self.assertNotIn("activateWall", script)
        self.assertNotIn("queueOperatorSpeed", script)
        self.assertNotIn("queueTargetFps", script)
        self.assertNotIn("Check authorization", script)
        self.assertIn("window.addEventListener('online', refreshStatus);", script)
        self.assertIn("if (status.undo_invalidated) await acknowledgeUndo(status.undo_invalidation_revision);", script)
        self.assertIn("`${api}/stop`", script)
        self.assertNotIn("`${api}/go-live`", script)

    def test_scene_history_preserves_native_text_undo_and_syncs_action_availability(self) -> None:
        script = Path("web/static/js/composer_slice.js").read_text(encoding="utf-8")
        history = script[
            script.index("function updateHistoryActions") : script.index(
                "async function loadLibrary"
            )
        ]
        javascript = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
class Node { constructor() { this.disabled = null; } }
const nodes = {'#undoScene': new Node(), '#redoScene': new Node(), '#operationMessage': new Node()};
const state = {history: [], redo: [], scene: {revision: 2}, dirty: false};
const context = {
  assert, nodes, state, structuredClone, $: (selector) => nodes[selector],
  applyScene(scene) { state.scene = structuredClone(scene); },
  submit() { return Promise.resolve(); },
};
const run = vm.runInNewContext(process.argv[1] + `
  ;(async () => {
    updateHistoryActions();
    assert.equal(nodes['#undoScene'].disabled, true);
    assert.equal(nodes['#redoScene'].disabled, true);
    remember({revision: 1});
    assert.equal(nodes['#undoScene'].disabled, false);
    assert.equal(nodes['#redoScene'].disabled, true);
    await rewind('undo');
    assert.equal(nodes['#undoScene'].disabled, true);
    assert.equal(nodes['#redoScene'].disabled, false);
    await rewind('redo');
    assert.equal(nodes['#undoScene'].disabled, false);
    assert.equal(nodes['#redoScene'].disabled, true);
    clearHistory();
    assert.equal(nodes['#undoScene'].disabled, true);
    assert.equal(nodes['#redoScene'].disabled, true);

    for (const editingTarget of [
      {closest: (selector) => { assert.match(selector, /input, textarea/); return {}; }},
      {closest: () => ({})},
      {closest: () => ({})},
    ]) {
      let prevented = false;
      handleSceneHistoryShortcut({metaKey: true, ctrlKey: false, shiftKey: false, key: 'z', target: editingTarget, preventDefault() { prevented = true; }});
      assert.equal(prevented, false);
    }
    remember({revision: 1});
    let prevented = false;
    handleSceneHistoryShortcut({metaKey: false, ctrlKey: true, shiftKey: false, key: 'Z', target: {closest: () => null}, preventDefault() { prevented = true; }});
    assert.equal(prevented, true);
    await Promise.resolve();
    assert.equal(nodes['#undoScene'].disabled, true);
    assert.equal(nodes['#redoScene'].disabled, false);
    prevented = false;
    handleSceneHistoryShortcut({metaKey: true, ctrlKey: false, shiftKey: true, key: 'z', target: {closest: () => null}, preventDefault() { prevented = true; }});
    assert.equal(prevented, true);
  })()
`, context);
Promise.resolve(run).catch((error) => { console.error(error); process.exitCode = 1; });
"""
        completed = subprocess.run(
            ["node", "-e", javascript, history], check=False, capture_output=True, text=True
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_save_feedback_is_local_accessible_and_survives_status_polling(self) -> None:
        html = Path("web/templates/composer.html").read_text(encoding="utf-8")
        script = Path("web/static/js/composer_slice.js").read_text(encoding="utf-8")
        css = Path("web/static/css/composer_slice.css").read_text(encoding="utf-8")
        save_source = script[
            script.index("function saveFeedback") : script.index(
                "  async function rewind", script.index("function saveFeedback")
            )
        ]
        status_source = script[
            script.index("function renderStatus") : script.index(
                "  function updateHistoryActions", script.index("function renderStatus")
            )
        ]
        self.assertIn('id="saveFeedback"', html)
        self.assertIn('role="status"', html)
        self.assertIn('aria-live="polite"', html)
        self.assertIn('aria-describedby="saveFeedback"', html)
        self.assertIn('.save-feedback[data-state="error"]', css)
        self.assertNotIn("operationMessage", save_source)
        self.assertNotIn("saveFeedback", status_source)
        javascript = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
class Node {
  constructor(value = '') { this.value = value; this.textContent = ''; this.hidden = true; this.dataset = {}; this.open = false; this.focused = false; }
  focus() { this.focused = true; }
}
const nodes = {
  '#sceneName': new Node('   '), '#secondaryOperations': new Node(),
  '#saveFeedback': new Node(), '#saveState': new Node(),
};
const requests = [];
const state = {selection: null, dirty: true};
const context = {
  assert, nodes, state, requests, replies: [], api: '/api/composer', process, console,
  $: (selector) => nodes[selector],
  sceneFromControls: () => ({schema: 'ledgrid.scene.v2'}),
  loadLibrary: async () => {},
  fetch: async (url, options) => {
    requests.push({url, options});
    const next = context.replies.shift();
    return {ok: next.ok, json: async () => next.body};
  },
};
vm.runInNewContext(process.argv[1] + `
  ;(async () => {
    await save(false);
    assert.equal(requests.length, 0);
    assert.equal(nodes['#saveFeedback'].textContent, 'Enter a scene name before saving.');
    assert.equal(nodes['#saveFeedback'].dataset.state, 'error');
    assert.equal(nodes['#secondaryOperations'].open, true);
    assert.equal(nodes['#sceneName'].focused, true);

    nodes['#sceneName'].value = 'Northern glow';
    replies = [{ok: true, body: {look: {id: 'one', name: 'Northern glow'}}}];
    await save(false);
    assert.equal(requests.at(-1).url, '/api/composer/looks');
    assert.equal(nodes['#saveFeedback'].textContent, 'Saved Northern glow.');
    assert.equal(nodes['#saveFeedback'].dataset.state, 'success');
    assert.equal(nodes['#sceneName'].focused, true, 'successful save must not steal focus');

    nodes['#sceneName'].value = 'Northern glow copy';
    replies = [{ok: true, body: {look: {id: 'two', name: 'Northern glow copy'}}}];
    await save(true);
    assert.equal(requests.at(-1).url, '/api/composer/looks');
    assert.equal(nodes['#saveFeedback'].textContent, 'Saved Northern glow copy.');

    state.selection = {kind: 'look', id: 'two', name: 'Northern glow copy'};
    nodes['#sceneName'].value = 'Keep this typed name';
    replies = [{ok: false, body: {error: 'Disk is temporarily unavailable'}}];
    await save(false);
    assert.equal(requests.at(-1).url, '/api/composer/looks/save');
    assert.equal(nodes['#sceneName'].value, 'Keep this typed name');
    assert.equal(nodes['#saveFeedback'].textContent, 'Disk is temporarily unavailable');
    assert.equal(nodes['#saveFeedback'].dataset.state, 'error');

    replies = [{ok: true, body: {}}];
    await save(false);
    assert.equal(nodes['#saveFeedback'].textContent, 'Saved Northern glow copy.');
    assert.equal(nodes['#saveFeedback'].dataset.state, 'success');
  })().catch((error) => { console.error(error); process.exitCode = 1; });
`, context);
"""
        completed = subprocess.run(
            ["node", "-e", javascript, save_source],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn('document.addEventListener(\'keydown\', handleSceneHistoryShortcut);', script)
        self.assertIn('[contenteditable]:not([contenteditable="false"])', script)

    def test_all_live_intents_share_the_serialized_newest_scene_publisher(self) -> None:
        script = Path("web/static/js/composer_slice.js").read_text(encoding="utf-8")
        self.assertIn("await submit(body.look.scene, {", script)
        self.assertIn("endpoint: `/looks/${item.id}/open`", script)
        self.assertIn("targetFps = boundedTargetFps($('#targetFps').value)", script)
        self.assertIn("globalSettingsForWall(scene, power, targetFps)", script)
        self.assertIn("if (state.wall.dirty || state.publication.queued || state.publication.afterStop || state.publication.inFlight) return;", script)
        self.assertIn("if (status.connected && state.wall.dirty && state.scene", script)
        self.assertIn("!state.wall.retryBlocked", script)
        self.assertIn("automatic: true", script)
        self.assertIn("state.publication.afterStop", script)
        self.assertIn("{kind: 'stop', scene: structuredClone(state.scene), resolve, reject}", script)

    def test_stale_library_lookups_are_discarded_before_they_can_publish_or_mutate_controls(self) -> None:
        script = Path("web/static/js/composer_slice.js").read_text(encoding="utf-8")
        helper = script[
            script.index("const beginIntent") : script.index("  const sleep")
        ]
        javascript = """
const assert = require('node:assert/strict');
const vm = require('node:vm');
const context = {state: {intent: 0}, assert};
vm.runInNewContext(process.argv[1] + '; first = beginIntent(); newerEdit = beginIntent(); firstCurrent = intentIsCurrent(first); newerCurrent = intentIsCurrent(newerEdit); stopped = beginIntent(); editCurrentAfterStop = intentIsCurrent(newerEdit);', context);
assert.equal(context.firstCurrent, false);
assert.equal(context.newerCurrent, true);
assert.equal(context.editCurrentAfterStop, false);
"""
        completed = subprocess.run(["node", "-e", javascript, helper], check=False, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(script.count("if (!intentIsCurrent(intentToken)) return;"), 4)
        self.assertEqual(script.count("if (!intentIsCurrent(intentToken) || publication.coalesced) return;"), 2)
        self.assertIn("if (!intentIsCurrent(intentToken)) return;\n      state.selection = item;", script)
        self.assertIn("if (intentIsCurrent(intentToken) && error.status !== 409)", script)
        self.assertIn("beginIntent();\n    if (!state.scene) return Promise.resolve();", script)
        self.assertIn("await submit(state.scene, {intentToken: state.intent, automatic: true})", script)

        in_flight_javascript = """
const assert = require('node:assert/strict');
const vm = require('node:vm');
const context = {state: {intent: 0}, assert};
const run = vm.runInNewContext(process.argv[1] + `
  ;(async () => {
    const lookupIntent = beginIntent();
    let resolveSubmit;
    const submitted = new Promise((resolve) => { resolveSubmit = resolve; });
    const completion = submitted.then(() => intentIsCurrent(lookupIntent));
    beginIntent();
    resolveSubmit();
    assert.equal(await completion, false);
  })()
`, context);
Promise.resolve(run).catch((error) => { console.error(error); process.exitCode = 1; });
"""
        in_flight = subprocess.run(["node", "-e", in_flight_javascript, helper], check=False, capture_output=True, text=True)
        self.assertEqual(in_flight.returncode, 0, in_flight.stderr)

    def test_invalid_authored_feedback_survives_status_poll_until_a_successful_edit(self) -> None:
        script = Path("web/static/js/composer_slice.js").read_text(encoding="utf-8")
        self.assertIn("authoredValidationError: null", script)
        self.assertIn(
            "state.authoredValidationError || state.wall.activationError || status.last_error ||",
            script,
        )
        submit = script[
            script.index("async function flushPublication") : script.index("async function edit")
        ]
        self.assertIn(
            "state.authoredValidationError = response.ok ? null :",
            submit,
        )
        self.assertLess(
            submit.index("state.authoredValidationError = response.ok ? null :"),
            submit.index("renderStatus(result.status || result)"),
        )
        refresh = script[
            script.index("async function refreshStatus") : script.index(
                "document.addEventListener('visibilitychange'"
            )
        ]
        self.assertNotIn("authoredValidationError =", refresh)

    def test_terminal_wall_failure_requires_fresh_intent_while_offline_recovery_remains_automatic(self) -> None:
        script = Path("web/static/js/composer_slice.js").read_text(encoding="utf-8")
        publication = script[
            script.index("async function flushPublication") : script.index("async function edit")
        ]
        refresh = script[
            script.index("async function refreshStatus") : script.index(
                "document.addEventListener('visibilitychange'"
            )
        ]
        javascript = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const source = process.argv[1];
const draft = {revision: 1, animation: {component_id: 'pinball'}};
const state = {
  status: {connected: true, running: true, armed: true}, scene: null,
  sequence: 0, intent: 0, submitting: false, refreshInFlight: false,
  authoredValidationError: null,
  publication: {queued: null, afterStop: null, inFlight: null, scheduled: false},
  wall: {activating: false, dirty: false, activationError: null, retryBlocked: false, observation: null},
};
const stats = {publicationPosts: 0, activationAttempts: 0};
const control = {mode: 'terminal'};
let releaseQueuedActivation;
let queuedActivationEntered;
const queuedActivationGate = new Promise((resolve) => { releaseQueuedActivation = resolve; });
const queuedActivationStarted = new Promise((resolve) => { queuedActivationEntered = resolve; });
async function fetch(_url, options = {}) {
  if (options.method === 'POST') stats.publicationPosts += 1;
  return {ok: true, json: async () => ({status: {connected: true, running: true, armed: true}})};
}
async function guardedWallActivation() {
  stats.activationAttempts += 1;
  const attemptMode = control.mode;
  if (attemptMode === 'queued-terminal') {
    queuedActivationEntered();
    await queuedActivationGate;
  }
  if (attemptMode === 'terminal' || attemptMode === 'queued-terminal') {
    const error = new Error('Activation rolled back.'); error.code = 'activation_terminal'; throw error;
  }
  if (attemptMode === 'offline') {
    const error = new Error('Wall server unavailable.'); error.code = 'offline'; throw error;
  }
  state.wall.activationError = null;
  state.wall.retryBlocked = false;
  state.wall.dirty = Boolean(state.publication.queued || state.publication.afterStop);
}
async function requestJson(url) {
  if (url.includes('/settings/observed')) return {target_fps: 150};
  return {connected: true, running: true, armed: true};
}
const nodes = {'#targetFps': {value: '150'}, '#liveAction': {disabled: false}, '#operationMessage': {textContent: ''}};
const context = {
  assert, state, draft, fetch, guardedWallActivation, requestJson, queueMicrotask,
  structuredClone, api: '/api/composer', clientId: 'phone', stats, control,
  queuedActivationStarted, releaseQueuedActivation,
  $: (selector) => nodes[selector],
  boundedTargetFps: (value) => Number(value), newUuid: () => `id-${stats.publicationPosts + 1}`,
  beginIntent: () => ++state.intent, intentIsCurrent: (intent) => intent === state.intent,
  remember() {}, syncComponentPresetUI() {}, schedulePreview() {}, renderStatus(payload) { state.status = payload.status || payload; },
  wallStatus: () => state.status, stopOutputNow: async () => {}, syncFrameRateObservation() {}, acknowledgeUndo: async () => {},
};
const run = vm.runInNewContext(source + `
  ;(async () => {
    await submit(draft);
    assert.equal(stats.publicationPosts, 1);
    assert.equal(stats.activationAttempts, 1);
    assert.equal(state.wall.retryBlocked, true);
    assert.equal(state.wall.dirty, true);
    assert.deepEqual(state.scene, draft);
    for (let poll = 0; poll < 3; poll += 1) await refreshStatus();
    assert.equal(stats.publicationPosts, 1, 'terminal failure must stay single-shot across polls');
    assert.equal(stats.activationAttempts, 1);

    await retryWallActivation();
    assert.equal(stats.publicationPosts, 2, 'explicit retry sends exactly once');
    assert.equal(stats.activationAttempts, 2);
    assert.equal(state.wall.retryBlocked, true);

    const newer = {revision: 2, animation: {component_id: 'aurora_curtains'}};
    await submit(newer);
    assert.equal(stats.publicationPosts, 3, 'newer edit sends exactly once');
    assert.deepEqual(state.scene, newer);
    assert.equal(state.wall.retryBlocked, true);

    control.mode = 'offline';
    const offlineDraft = {revision: 3, animation: {component_id: 'cellular_tapestry'}};
    await submit(offlineDraft);
    assert.equal(state.wall.retryBlocked, false, 'transport outage remains recoverable');
    control.mode = 'success';
    await refreshStatus();
    await refreshStatus();
    assert.equal(stats.publicationPosts, 5, 'reconnect poll retries once, then clean state stays idle');
    assert.equal(state.wall.dirty, false);
    assert.deepEqual(state.scene, offlineDraft);

    control.mode = 'queued-terminal';
    const olderPromise = submit({revision: 4, animation: {component_id: 'pinball'}});
    await queuedActivationStarted;
    const newest = {revision: 5, animation: {component_id: 'frostwork'}};
    const newestPromise = submit(newest);
    control.mode = 'success';
    releaseQueuedActivation();
    await Promise.all([olderPromise, newestPromise]);
    assert.equal(stats.publicationPosts, 7);
    assert.equal(state.wall.retryBlocked, false, 'superseded failure cannot block newer intent');
    assert.equal(state.wall.activationError, null);
    assert.equal(state.wall.dirty, false);
    assert.deepEqual(state.scene, newest);
  })()
`, context);
Promise.resolve(run).catch((error) => { console.error(error); process.exitCode = 1; });
"""
        completed = subprocess.run(
            ["node", "-e", javascript, publication + refresh],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_wall_activation_failure_stays_visible_until_an_exact_acknowledgement(self) -> None:
        script = Path("web/static/js/composer_slice.js").read_text(encoding="utf-8")
        adapter = script[
            script.index("function managedWallComponent") : script.index(
                "  function globalSettingsForWall"
            )
        ]
        identity = script[
            script.index("const identity") : script.index("  const beginIntent")
        ]
        status = script[
            script.index("function renderStatus") : script.index(
                "  async function acknowledgeUndo"
            )
        ]
        javascript = """
const assert = require('node:assert/strict');
const vm = require('node:vm');
const source = process.argv[1];
class Node {
  constructor() { this.textContent = ''; this.hidden = true; this.disabled = false; this.children = []; }
  replaceChildren() { this.children = []; this.textContent = ''; }
  append(child) { this.children.push(child); }
  addEventListener(type, listener) { this.listener = listener; }
}
const nodes = Object.fromEntries([
  '#connectionState', '#observedIdentity', '#diagnosticObserved', '#desiredIdentity',
  '#sceneRevision', '#sceneIdentity', '#saveState', '#liveAction',
  '#wallActivationFailure', '#operationMessage',
].map((selector) => [selector, new Node()]));
const prior = {revision: 7, digest: 'a'.repeat(64)};
const context = {
  assert, nodes, prior, JSON, Number, Boolean, Math, Object, Set, structuredClone,
  document: {
    createTextNode(text) { return {textContent: text}; },
    createElement() { return new Node(); },
  },
  retryWallActivation() {},
  state: {
    status: {}, revision: 0, dirty: false, selection: null, authoredValidationError: null,
    publication: {queued: null, afterStop: null, inFlight: null},
    wall: {
      bootstrap: {components: [
        ['receiver_native', 'native_aurora', 'background'],
        ['python', 'conway_life', 'animation'],
        ['python', 'clock_overlay', 'overlay'],
        ['python', 'emoji_arranger', 'overlay'],
      ].map(([provider, plugin_id, role]) => ({
        provider, plugin_id, role,
        browser_capabilities: {activation_ready: true, managed_identity: {
          provider, component_id: plugin_id, component_digest: 'c'.repeat(64),
          runtime_digest: 'd'.repeat(64), parameter_schema_version: 1,
        }},
      }))},
      observation: {
        controller_session_id: 'session', controller_state_revision: 7, is_running: true,
        installation_profile_digest: 'e'.repeat(64), active_identity: {scene_identity: prior},
      },
      scene: {revision: 7}, activating: false, dirty: false,
      activationError: 'Activation rejected by mocked wall.',
    },
  },
  $: (selector) => nodes[selector],
};
vm.runInNewContext(source + `
  ; const authored = {schema: 'ledgrid.scene.v2', background: {component_id: 'native_aurora', provider: 'receiver_native', bundle_digest: 'f'.repeat(64), parameters: {gain: .37, seed: 12}}, animation: {component_id: 'conway_life', provider: 'python', parameters: {seed: 23}}, widgets: [
    {id: 'status.clock', visible: false, component: {component_id: 'clock_overlay', provider: 'python', parameters: {show_seconds: true}}},
    {id: 'message', visible: true, component: {component_id: 'emoji_arranger', provider: 'python', parameters: {text: 'HI'}}},
  ], look: {pace: .35, palette_id: 'ember', presentation_brightness: 1.75}, plants: {effects: {version: 1, active: ['shadow'], strengths: {shadow: .5}}}};
  const scene = browserSceneForWall(authored);
  assert.equal(scene.schema, 'ledgrid.browser-scene-v2');
  assert.equal(JSON.stringify(scene.scene), JSON.stringify(authored));
  assert.deepEqual(scene.components.map(item => item.slot_id), ['background', 'animation', 'widget:status.clock', 'widget:message']);
  assert.equal(scene.components[0].parameters.gain, .37);
  assert.equal(scene.components[1].parameters.seed, 23);
  renderStatus({connected: true, running: true, armed: true, current: {revision: 8, digest: 'b'.repeat(64)}, desired: {revision: 8, digest: 'b'.repeat(64)}, observed: {revision: 8, digest: 'b'.repeat(64)}, revision: 8});
  assert.equal(nodes['#wallActivationFailure'].hidden, false);
  assert.equal(nodes['#wallActivationFailure'].children[0].textContent, 'Live update failed. ');
  assert.equal(nodes['#operationMessage'].textContent, 'Activation rejected by mocked wall.');
  assert.equal(nodes['#wallActivationFailure'].children[1].textContent, 'Retry once');
  assert.equal(nodes['#wallActivationFailure'].children[1].disabled, false);
  assert.equal(nodes['#liveAction'].disabled, false);
  assert.equal(nodes['#observedIdentity'].textContent, 'r7 · ' + 'a'.repeat(64));
  assert.equal(nodes['#desiredIdentity'].textContent, 'r7 · ' + 'a'.repeat(64));
  state.wall.activationError = null;
  renderStatus({connected: true, running: true, armed: true, current: {revision: 8, digest: 'b'.repeat(64)}, desired: {revision: 8, digest: 'b'.repeat(64)}, observed: {revision: 8, digest: 'b'.repeat(64)}, revision: 8});
  assert.equal(nodes['#wallActivationFailure'].hidden, true);
  assert.equal(nodes['#wallActivationFailure'].children.length, 0);
  assert.equal(nodes['#observedIdentity'].textContent, 'r8 · ' + 'b'.repeat(64));
`, context);
"""
        completed = subprocess.run(
            ["node", "-e", javascript, identity + adapter + status],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_readding_after_primary_removal_selects_the_missing_slot_and_checks(self) -> None:
        """The client must restore the missing Conway lower slot without duplicates."""
        script = Path("web/static/js/composer_slice.js").read_text(encoding="utf-8")
        self.assertIn("await submit(next, {rememberEdit: true});", script)
        # Equivalent authored output after add-two, remove-Conway, add again.
        response = self.client.post("/api/composer/scene", json={"origin": "composer", "scene": _current_scene(), "client_id": "desktop", "client_sequence": 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["state"], "live")

    def test_third_overlay_is_rejected_before_local_adapter_mutation(self) -> None:
        rejected = self.client.post("/api/composer/check", json=_scene([_conway(), _overlay("clock_upper"), _overlay("extra")]))
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("legacy or forbidden", rejected.get_json()["error"])
        self.assertEqual(self.interface.composer_control.commands, [])


if __name__ == "__main__":
    unittest.main()
