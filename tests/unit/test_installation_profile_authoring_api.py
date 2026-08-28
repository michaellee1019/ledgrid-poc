"""Web contract tests for revisioned installation-profile publication."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from animation.core.installation_profile_authoring import InstallationProfileAuthoring
from animation.core.installation_profile_library import InstallationProfileLibrary
from animation.core.plant_awareness import GLOBE_REGION_ORDER
from web.app import AnimationWebInterface


ROOT = Path(__file__).resolve().parents[2]
PROFILE_FIXTURE = ROOT / "tests" / "fixtures" / "installation_profile_v1.bin"


class _Controller:
    strip_count = 33
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip


class _Manager:
    controller = _Controller()
    preview_controller = controller

    def __init__(self, library: InstallationProfileLibrary, digest: str) -> None:
        self._installation_profile_library = library
        self.selected_digest = digest

    def list_components(self) -> list[dict]:
        return []

    def list_animations(self) -> list[dict]:
        return []

    def get_installation_profile_status(self) -> dict:
        return {"selected_digest": self.selected_digest}


class _Channel:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict]] = []

    def read_status(self) -> dict:
        return {}

    def send_command(self, action: str, **data) -> dict:
        self.commands.append((action, deepcopy(data)))
        return {"command_id": "unexpected"}


class InstallationProfileAuthoringApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.library = InstallationProfileLibrary(self.root / "library")
        self.source = self.library.publish(PROFILE_FIXTURE.read_bytes())
        self.authoring = InstallationProfileAuthoring(
            self.library, self.root / "authoring"
        )
        self.manager = _Manager(self.library, self.source.content_digest)
        self.channel = _Channel()
        self.interface = AnimationWebInterface(
            self.channel,
            self.manager,
            local_mode=True,
            installation_profile_authoring=self.authoring,
            project_root=self.root,
        )
        self.client = self.interface.app.test_client()
        self.base = (
            f"/api/v1/installation-profiles/{self.source.content_digest}"
        )

    @staticmethod
    def changed(draft: dict) -> dict:
        changed = deepcopy(draft)
        occupied = set(changed["masks"]["foliage"])
        for values in changed["masks"]["globes"].values():
            occupied.update(values)
        open_pixel = next(index for index in range(4416) if index not in occupied)
        changed["masks"]["foliage"].append(open_pixel)
        changed["masks"]["foliage"].sort()
        return changed

    def test_bootstrap_exposes_literal_selected_profile_workflow_urls(self) -> None:
        response = self.client.get("/api/v1/composer/bootstrap")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(
            payload["installation_profile"]["digest"], self.source.content_digest
        )
        actions = payload["capabilities"]["server_actions"]
        self.assertEqual(
            actions["installation_profile_draft_url"], self.base + "/draft"
        )
        self.assertEqual(
            actions["installation_profile_publish_url"], self.base + "/publish"
        )
        self.assertEqual(
            actions["installation_profile_artifact_url"], self.base + "/artifact"
        )
        self.assertEqual(self.channel.commands, [])

    def test_get_and_put_require_etag_and_stale_put_has_zero_mutation(self) -> None:
        loaded = self.client.get(self.base + "/draft")
        self.assertEqual(loaded.status_code, 200)
        initial = loaded.get_json()
        self.assertEqual(loaded.headers["ETag"], f'"{initial["revision"]}"')
        self.assertEqual(
            tuple(initial["masks"]["globes"]), GLOBE_REGION_ORDER
        )

        missing = self.client.put(
            self.base + "/draft", json=self.changed(initial)
        )
        self.assertEqual(missing.status_code, 428)

        updated_response = self.client.put(
            self.base + "/draft",
            json=self.changed(initial),
            headers={"If-Match": loaded.headers["ETag"]},
        )
        self.assertEqual(updated_response.status_code, 200)
        updated = updated_response.get_json()
        draft_path = (
            self.root / "authoring" / "drafts" /
            f"{self.source.content_digest}.json"
        )
        after_update = draft_path.read_bytes()

        stale = self.client.put(
            self.base + "/draft",
            json=initial,
            headers={"If-Match": loaded.headers["ETag"]},
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(
            stale.get_json()["current_revision"], updated["revision"]
        )
        self.assertEqual(draft_path.read_bytes(), after_update)
        self.assertEqual(self.channel.commands, [])

    def test_publish_is_immutable_read_only_and_never_selects_live_profile(self) -> None:
        loaded = self.client.get(self.base + "/draft")
        initial = loaded.get_json()
        updated_response = self.client.put(
            self.base + "/draft",
            json=self.changed(initial),
            headers={"If-Match": loaded.headers["ETag"]},
        )
        updated = updated_response.get_json()
        selected_before = self.manager.selected_digest

        published = self.client.post(
            self.base + "/publish",
            headers={"If-Match": updated_response.headers["ETag"]},
        )

        self.assertEqual(published.status_code, 200)
        publication = published.get_json()
        self.assertFalse(publication["selected"])
        self.assertEqual(publication["revision"], updated["revision"])
        self.assertEqual(self.manager.selected_digest, selected_before)
        self.assertEqual(self.channel.commands, [])

        artifact = self.client.get(publication["artifact_url"])
        self.assertEqual(artifact.status_code, 200)
        encoded = artifact.data
        digest_input = bytearray(encoded)
        embedded = bytes(digest_input[68:100]).hex()
        digest_input[68:100] = bytes(32)
        self.assertEqual(embedded, publication["published_digest"])
        self.assertEqual(hashlib.sha256(digest_input).hexdigest(), embedded)
        self.assertEqual(artifact.headers["ETag"], f'"{embedded}"')
        self.assertEqual(
            artifact.headers["Cache-Control"],
            "public, max-age=31536000, immutable",
        )
        conditional = self.client.get(
            publication["artifact_url"], headers={"If-None-Match": f'"{embedded}"'}
        )
        self.assertEqual(conditional.status_code, 304)

    def test_stale_publish_and_legacy_post_cannot_mutate_any_authority(self) -> None:
        loaded = self.client.get(self.base + "/draft")
        initial = loaded.get_json()
        updated = self.client.put(
            self.base + "/draft",
            json=self.changed(initial),
            headers={"If-Match": loaded.headers["ETag"]},
        )
        profiles_before = {
            path.name for path in self.library.profiles_directory.iterdir()
        }
        draft_path = (
            self.root / "authoring" / "drafts" /
            f"{self.source.content_digest}.json"
        )
        draft_before = draft_path.read_bytes()

        stale = self.client.post(
            self.base + "/publish",
            headers={"If-Match": loaded.headers["ETag"]},
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(
            {path.name for path in self.library.profiles_directory.iterdir()},
            profiles_before,
        )
        self.assertEqual(draft_path.read_bytes(), draft_before)

        config = self.root / "config"
        config.mkdir()
        legacy_foliage = config / "plant_pixel_map_32x138.json"
        legacy_globes = config / "plant_globe_map_32x138.json"
        legacy_foliage.write_text(json.dumps({"sentinel": "foliage"}))
        legacy_globes.write_text(json.dumps({"sentinel": "globes"}))
        legacy_before = (legacy_foliage.read_bytes(), legacy_globes.read_bytes())

        compatibility = self.client.get("/api/painter/masks")
        self.assertEqual(compatibility.status_code, 200)
        self.assertTrue(compatibility.get_json()["read_only"])
        retired = self.client.post(
            "/api/painter/masks",
            json={"masks": {"foliage": [], "planter_bowls": []}},
        )
        self.assertEqual(retired.status_code, 405)
        self.assertEqual(
            (legacy_foliage.read_bytes(), legacy_globes.read_bytes()),
            legacy_before,
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(self.channel.commands, [])


if __name__ == "__main__":
    unittest.main()
