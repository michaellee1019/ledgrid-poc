"""One package supplies a new component to Scene, presets and both editors."""

from datetime import datetime
import json
from pathlib import Path

import pytest

from animation.core.component_catalog import ComponentCatalog
from animation.core.composer_catalog import current_component_catalog, discover_composer_components
from animation.core.manager import PreviewLEDController
from ipc.scene_contract import normalize_composer_scene
from tests.unit.test_composer_slice import _PreviewManager, _WallChannel
from web.app import AnimationWebInterface
from web.composer_component_presets import ComponentPresetCatalog
from web.composer_final_preview import ComposerFinalPreview
from web.starter_looks import get_starter
from web.wall_composer import WallComposer


ROOT = Path(__file__).resolve().parents[2]


def test_manifest_fixture_supplies_factory_validator_and_editor_controls(tmp_path, monkeypatch):
    package = tmp_path / "fixture_components"
    fixture = package / "test_wash"
    fixture.mkdir(parents=True)
    (package / "__init__.py").touch()
    (fixture / "manifest.json").write_text(json.dumps({"class": "TestWash", "composer": True}))
    (fixture / "__init__.py").write_text("""
from animation.libraries.ambient_scene import AmbientSceneAnimation

class TestWash(AmbientSceneAnimation):
    ANIMATION_NAME = "Test Wash"
    ANIMATION_DESCRIPTION = "Test-only wash"
    COMPONENT_ID = "test_wash"
    PREFERRED_PARAMETERS = ("drift",)
    PARAMETER_CONTROLS = {"drift": {"label": "Band travel", "ui_max": 1}}
""")
    monkeypatch.syspath_prepend(str(tmp_path))
    shipped = current_component_catalog()
    discovered = discover_composer_components(package, "fixture_components")
    descriptor, = discovered.descriptors
    catalog = ComponentCatalog((*shipped.descriptors, descriptor))
    presets = ComponentPresetCatalog(ROOT, catalog)
    interface = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True)
    interface.composer_catalog = catalog
    interface.composer_presets = presets
    client = interface.app.test_client()

    gallery = client.get("/api/composer/gallery").get_json()
    public = next(item for item in gallery["entries"] if item["component_id"] == "test_wash")
    assert public["preferred_parameters"] == ["drift"]
    assert public["input_capability"] == {"mode": "none", "actions": {}}
    assert public["parameter_schema"]["drift"]["label"] == "Band travel"
    assert public["parameter_schema"]["drift"]["max"] == 2
    assert public["parameter_schema"]["drift"]["ui_max"] == 1
    bootstrap = client.get("/api/v1/composer/bootstrap?catalog_only=1").get_json()
    published = next(item for item in bootstrap["components"] if item["plugin_id"] == "test_wash")
    assert published["parameter_schema"] == public["parameter_schema"]

    scene = get_starter("aurora")["scene"]
    scene["animation"].update(component_id="test_wash", parameters={"drift": 1.2})
    canonical = normalize_composer_scene({"origin": "composer", "scene": scene}, catalog)
    preview = ComposerFinalPreview(catalog, ROOT, controller=PreviewLEDController(33, 138))
    assert preview.render(canonical, .5, datetime.now().astimezone()).pixels.shape == (4554, 3)
    menu = WallComposer.__new__(WallComposer)
    menu.catalog, menu.manager = catalog, type("Manager", (), {"nimbus_game": lambda _: (None, None)})()
    assert menu._parameter_rows(("animation",), canonical.scene["animation"])[0].name == "Band travel"
    assert presets.normalize_parameters("test_wash", {"drift": 1.2})["drift"] == 1.2
    with pytest.raises(ValueError, match="non-local"):
        presets.normalize_parameters("test_wash", {"drift": {"invalid": True}})
    scene["animation"]["parameters"]["drift"] = {"invalid": True}
    with pytest.raises(ValueError, match="drift"):
        normalize_composer_scene({"origin": "composer", "scene": scene}, catalog)


def test_saved_number_encoding_and_widget_defaults_are_stable():
    catalog = current_component_catalog()
    scene = get_starter("aurora")["scene"]
    scene["animation"]["parameters"]["glow_intensity"] = 1
    canonical = normalize_composer_scene({"origin": "composer", "scene": scene}, catalog)
    assert type(canonical.scene["animation"]["parameters"]["glow_intensity"]) is int
    assert normalize_composer_scene({"origin": "composer", "scene": canonical.scene}, catalog) == canonical
    for name in ("clock_overlay", "emoji_arranger"):
        widget = catalog.require(provider="python", component_id=name, version=1)
        assert widget.role.value == "widget"
        assert widget.normalize_parameters({}) == widget.default_parameters()


def test_preset_import_applies_snake_cross_field_rules():
    interface = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True)
    client = interface.app.test_client()
    path = "/api/v1/composer/presets/validate"
    payload = {"animation": "snake", "params": {"initial_length": 20, "max_length": 24}}
    valid = client.post(path, json=payload)
    assert valid.status_code == 200, valid.get_json()
    payload["params"]["max_length"] = 12
    rejected = client.post(path, json=payload)
    assert rejected.status_code == 400, rejected.get_json()
