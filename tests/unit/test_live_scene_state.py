"""Focused live-first Scene v2 publication contracts."""

from __future__ import annotations

import copy
import unittest

from ipc.scene_contract import LocalSceneAdapter, SceneContractError
from tests.unit.test_scene_activation_contract import _catalog, _request, _scene
from web.app import AnimationWebInterface
from web.live_scene_state import LiveSceneBlocked, LiveSceneStale, LiveSceneState


class _Channel:
    def __init__(self) -> None:
        self.commands: list[dict] = []
        self.fail_next = False

    def send_command(self, action: str, **data: object) -> dict:
        if self.fail_next:
            self.fail_next = False
            raise TimeoutError("adapter timeout")
        command = {"action": action, **data}
        self.commands.append(command)
        return command


class _Controller:
    strip_count = 33
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip


class _PreviewManager:
    controller = _Controller()
    plugin_loader = None


class LiveSceneStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = _catalog()
        self.channel = _Channel()
        self.state = LiveSceneState(self.catalog, LocalSceneAdapter(self.catalog), self.channel)

    @staticmethod
    def _request(seed: int = 17) -> dict:
        request = _request()
        request["scene"]["animation"]["parameters"] = {"seed": seed}
        return request

    def test_valid_continuous_edits_and_look_changes_publish_newest_scene(self) -> None:
        first = self.state.submit(self._request(1), client_id="a", mutation_id="one", client_sequence=1)
        second = self.state.submit(self._request(2), client_id="a", mutation_id="two", client_sequence=2)
        self.assertTrue(first["published"])
        self.assertTrue(second["published"])
        self.assertEqual(second["state"], "live")
        self.assertEqual(second["desired"], second["observed"])
        self.assertEqual((second["revision"], second["desired_revision"], second["observed_revision"]), (2, 2, 2))
        self.assertEqual(len(self.channel.commands), 2)

    def test_invalid_edit_never_disturbs_last_valid_desired_or_observed(self) -> None:
        before = self.state.submit(self._request(1), client_id="a")
        invalid = self._request(2)
        invalid["scene"]["look"]["presentation_brightness"] = 9
        with self.assertRaises(SceneContractError):
            self.state.submit(invalid, client_id="a")
        after = self.state.snapshot(client_id="a")
        self.assertEqual(after["desired"], before["desired"])
        self.assertEqual(after["observed"], before["observed"])
        self.assertEqual(after["revision"], before["revision"])

    def test_retries_and_delayed_sequences_cannot_replace_newer_scene(self) -> None:
        first = self.state.submit(self._request(1), client_id="a", mutation_id="drag-1", client_sequence=1)
        retry = self.state.submit(self._request(1), client_id="a", mutation_id="drag-1", client_sequence=1)
        self.assertTrue(retry["exact_retry"])
        self.assertEqual(len(self.channel.commands), 1)
        newest = self.state.submit(self._request(2), client_id="a", mutation_id="drag-2", client_sequence=2)
        with self.assertRaises(LiveSceneStale):
            self.state.submit(self._request(1), client_id="a", mutation_id="late", client_sequence=1)
        self.assertEqual(self.state.snapshot()["desired"], newest["desired"])
        self.assertEqual(first["revision"], 1)

    def test_old_exact_retry_returns_current_authoritative_revision_not_cached_snapshot(self) -> None:
        first = self.state.submit(self._request(1), client_id="a", mutation_id="drag-1", client_sequence=100)
        newer = self.state.submit(self._request(2), client_id="b", mutation_id="drag-2", client_sequence=1)
        retry = self.state.submit(self._request(1), client_id="a", mutation_id="drag-1", client_sequence=100)
        self.assertTrue(retry["exact_retry"])
        self.assertFalse(retry["published"])
        self.assertEqual(retry["mutation_basis"], first["desired"])
        self.assertEqual(retry["revision"], newer["revision"])
        self.assertEqual(retry["desired"], newer["desired"])
        self.assertTrue(retry["undo_invalidated"])

    def test_same_scene_is_coalesced_without_a_second_publication(self) -> None:
        self.state.submit(self._request(1), client_id="a")
        again = self.state.submit(self._request(1), client_id="a")
        self.assertTrue(again["coalesced"])
        self.assertFalse(again["published"])
        self.assertEqual(len(self.channel.commands), 1)

    def test_stop_keeps_current_edits_local_until_explicit_go_live(self) -> None:
        initial = self.state.submit(self._request(1), client_id="a")
        stopped = self.state.stop(client_id="a")
        local = self.state.submit(self._request(2), client_id="a")
        self.assertEqual(stopped["state"], "stopped")
        self.assertIsNone(stopped["observed"])
        self.assertFalse(local["published"])
        self.assertEqual(len(self.channel.commands), 2)  # activate then stop
        resumed = self.state.go_live(client_id="a")
        self.assertEqual(resumed["state"], "live")
        self.assertEqual(resumed["observed"], local["desired"])
        self.assertNotEqual(initial["desired"], resumed["desired"])

    def test_go_live_exposes_blockers_and_disconnect_requires_explicit_rearm(self) -> None:
        with self.assertRaises(LiveSceneBlocked) as empty:
            self.state.go_live()
        self.assertEqual(empty.exception.blockers[0]["code"], "no_scene")
        self.state.submit(self._request(1), client_id="a")
        self.state.set_connected(False)
        local = self.state.submit(self._request(2), client_id="a")
        self.assertEqual(local["state"], "disconnected")
        self.state.set_connected(True)
        reconnected = self.state.snapshot()
        self.assertEqual(reconnected["state"], "stopped")
        self.assertFalse(reconnected["armed"])
        self.assertEqual(len(self.channel.commands), 1)
        resumed = self.state.go_live(client_id="a")
        self.assertEqual(resumed["state"], "live")
        self.assertEqual(len(self.channel.commands), 2)

    def test_newest_client_wins_and_marks_other_client_undo_stale(self) -> None:
        first = self.state.submit(self._request(1), client_id="a", client_sequence=100)
        second = self.state.submit(self._request(2), client_id="b", client_sequence=1)
        a_view = self.state.snapshot(client_id="a")
        self.assertGreater(second["revision"], first["revision"])
        self.assertTrue(a_view["undo_invalidated"])
        self.assertEqual(a_view["undo_invalidation_revision"], second["revision"])
        acknowledged = self.state.acknowledge_undo_invalidation(client_id="a", revision=second["revision"])
        self.assertFalse(acknowledged["undo_invalidated"])
        self.assertFalse(self.state.snapshot(client_id="a")["undo_invalidated"])
        self.assertFalse(self.state.snapshot(client_id="b")["undo_invalidated"])

    def test_timeout_preserves_current_scene_and_go_live_offers_recovery(self) -> None:
        self.state.stop()
        self.state.submit(self._request(1), client_id="a")
        self.channel.fail_next = True
        with self.assertRaises(TimeoutError):
            self.state.go_live(client_id="a")
        recovering = self.state.snapshot()
        self.assertEqual(recovering["state"], "recovery")
        self.assertEqual(recovering["last_error"], "adapter timeout")
        # The failed attempt never observes the scene; a later explicit retry
        # resumes the still-current canonical scene.
        resumed = self.state.go_live(client_id="a")
        self.assertEqual(resumed["state"], "live")

    def test_stop_timeout_disarms_and_keeps_following_edits_local_until_go_live(self) -> None:
        self.state.submit(self._request(1), client_id="a")
        self.channel.fail_next = True
        with self.assertRaises(TimeoutError):
            self.state.stop(client_id="a")
        recovering = self.state.snapshot(client_id="a")
        self.assertEqual(recovering["state"], "recovery")
        self.assertFalse(recovering["running"])
        self.assertFalse(recovering["armed"])
        local = self.state.submit(self._request(2), client_id="a")
        self.assertEqual(local["state"], "recovery")
        self.assertFalse(local["published"])
        self.assertEqual(len(self.channel.commands), 1)
        self.assertEqual(self.state.go_live(client_id="a")["state"], "live")

    def test_composer_routes_expose_advisory_check_and_live_first_controls(self) -> None:
        """The Flask seam must not reintroduce a Check token or activation route."""
        interface = AnimationWebInterface(_Channel(), _PreviewManager(), local_mode=True)
        # The product catalog is migrated separately.  Replace only this test
        # double so the route contract is exercised against qualified v2 data.
        interface.composer_catalog = self.catalog
        interface.composer_adapter = LocalSceneAdapter(self.catalog)
        interface.composer_control = self.channel
        interface.composer_live = LiveSceneState(self.catalog, interface.composer_adapter, self.channel)
        client = interface.app.test_client()
        checked = client.post("/api/composer/check", json=self._request(1))
        self.assertEqual(checked.status_code, 200)
        self.assertTrue(checked.get_json()["valid"])
        self.assertNotIn("token", checked.get_json())
        scene = self._request(1)
        scene.update({"client_id": "a", "mutation_id": "one", "client_sequence": 1})
        published = client.post("/api/composer/scene", json=scene)
        self.assertEqual(published.status_code, 200)
        self.assertEqual(published.get_json()["state"], "live")
        self.assertEqual(client.post("/api/composer/stop", json={"client_id": "a"}).get_json()["status"]["state"], "stopped")
        self.assertEqual(client.post("/api/composer/go-live", json={"client_id": "a"}).get_json()["status"]["state"], "live")
        self.assertEqual(client.post("/api/composer/activate", json={}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
