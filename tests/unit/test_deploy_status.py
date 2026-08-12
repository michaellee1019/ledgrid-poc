import json
import tempfile
import unittest
from pathlib import Path

from scripts.start_server import controller_status_payload, resolve_active_release_id
from web.app import AnimationWebInterface


class _Controller:
    strip_count = 1
    leds_per_strip = 1
    total_leds = 1


class _PreviewManager:
    controller = _Controller()
    preview_controller = controller

    def list_animations(self):
        return []


class _StatusChannel:
    def __init__(self, status):
        self.status = status

    def read_status(self):
        return self.status


class DeployStatusTests(unittest.TestCase):
    RELEASE_ID = "a" * 64

    def test_status_apis_include_deploy_timestamp(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            channel = _StatusChannel({"is_running": True})
            interface = AnimationWebInterface(channel, _PreviewManager())
            interface.deployment_status_path = Path(temporary_dir) / "deployment.json"
            interface.deployment_status_path.write_text(json.dumps({
                "deploy_timestamp": 123.5,
            }))
            client = interface.app.test_client()

            self.assertEqual(client.get('/api/status').get_json()['deploy_timestamp'], 123.5)
            self.assertEqual(client.get('/api/stats').get_json()['deploy_timestamp'], 123.5)

    def test_empty_status_still_includes_deploy_timestamp(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            interface = AnimationWebInterface(_StatusChannel(None), _PreviewManager())
            interface.deployment_status_path = Path(temporary_dir) / "deployment.json"
            interface.deployment_status_path.write_text(json.dumps({
                "deploy_timestamp": 456.75,
            }))

            self.assertEqual(
                interface.app.test_client().get('/api/status').get_json()['deploy_timestamp'],
                456.75,
            )

    def test_api_reports_web_and_controller_release_identity(self):
        channel = _StatusChannel({
            "is_running": True,
            "release_id": self.RELEASE_ID,
        })
        interface = AnimationWebInterface(
            channel,
            _PreviewManager(),
            release_id=self.RELEASE_ID,
        )

        status = interface.app.test_client().get('/api/status').get_json()

        self.assertEqual(status['release_id'], self.RELEASE_ID)
        self.assertEqual(status['controller_release_id'], self.RELEASE_ID)
        self.assertTrue(status['release_consistent'])

    def test_api_exposes_release_mismatch_and_missing_controller_status(self):
        mismatch = AnimationWebInterface(
            _StatusChannel({"release_id": "b" * 64}),
            _PreviewManager(),
            release_id=self.RELEASE_ID,
        ).app.test_client().get('/api/status').get_json()
        self.assertEqual(mismatch['release_id'], self.RELEASE_ID)
        self.assertEqual(mismatch['controller_release_id'], "b" * 64)
        self.assertFalse(mismatch['release_consistent'])

        missing = AnimationWebInterface(
            _StatusChannel(None),
            _PreviewManager(),
            release_id=self.RELEASE_ID,
        ).app.test_client().get('/api/status').get_json()
        self.assertEqual(missing['release_id'], self.RELEASE_ID)
        self.assertIsNone(missing['controller_release_id'])
        self.assertFalse(missing['release_consistent'])

    def test_controller_snapshot_carries_exact_release_identity(self):
        class Manager:
            @staticmethod
            def get_current_frame():
                return {"frame_count": 7}

            @staticmethod
            def get_current_status():
                return {"is_running": True}

        payload = controller_status_payload(
            Manager(),
            release_id=self.RELEASE_ID,
            last_command_id="command-1",
            updated_at=123.5,
        )

        self.assertEqual(payload['release_id'], self.RELEASE_ID)
        self.assertEqual(payload['last_command_id'], "command-1")
        self.assertEqual(payload['updated_at'], 123.5)
        self.assertTrue(payload['is_running'])
        self.assertEqual(payload['frame_count'], 7)


class ActiveReleaseIdentityTests(unittest.TestCase):
    RELEASE_ID = "c" * 64

    def setUp(self):
        self.temporary_dir = tempfile.TemporaryDirectory()
        self.deploy_root = Path(self.temporary_dir.name)
        self.release = self.deploy_root / "releases" / self.RELEASE_ID
        self.release.mkdir(parents=True)

    def tearDown(self):
        self.temporary_dir.cleanup()

    def write_metadata(self, **overrides):
        payload = {
            "schema_version": 1,
            "id": self.RELEASE_ID,
            "digest": self.RELEASE_ID,
        }
        payload.update(overrides)
        (self.release / ".release.json").write_text(json.dumps(payload))

    def select_release(self, target=None):
        if target is not None:
            (self.deploy_root / target).mkdir(parents=True, exist_ok=True)
        (self.deploy_root / "current").symlink_to(
            target or Path("releases") / self.RELEASE_ID,
        )

    def test_resolves_only_metadata_selected_by_current(self):
        self.write_metadata()
        self.select_release()

        self.assertEqual(resolve_active_release_id(self.release), self.RELEASE_ID)
        self.assertEqual(resolve_active_release_id(self.deploy_root / "current"), self.RELEASE_ID)

    def test_legacy_root_without_release_metadata_has_no_identity(self):
        legacy_root = self.deploy_root / "legacy"
        legacy_root.mkdir()
        self.assertIsNone(resolve_active_release_id(legacy_root))

    def test_rejects_invalid_mismatched_or_unselected_release_metadata(self):
        cases = (
            ({"id": "not-a-digest", "digest": "not-a-digest"}, None, "invalid identity"),
            ({"digest": "d" * 64}, None, "invalid identity"),
            ({}, Path("releases") / ("d" * 64), "not selected"),
        )
        for metadata, current_target, message in cases:
            with self.subTest(message=message):
                current = self.deploy_root / "current"
                current.unlink(missing_ok=True)
                self.write_metadata(**metadata)
                self.select_release(current_target)
                with self.assertRaisesRegex(RuntimeError, message):
                    resolve_active_release_id(self.release)

    def test_selected_release_without_metadata_fails_closed(self):
        self.select_release()
        with self.assertRaisesRegex(RuntimeError, "missing .release.json"):
            resolve_active_release_id(self.release)


if __name__ == '__main__':
    unittest.main()
