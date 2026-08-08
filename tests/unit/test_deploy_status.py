import json
import tempfile
import unittest
from pathlib import Path

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


if __name__ == '__main__':
    unittest.main()
