import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.deployment.preserve_deploy_settings import load_saved_state, save_status


def test_stopped_controls_preserve_selection_and_brightness_zero(tmp_path):
    state = tmp_path / "state.json"
    scene = {"schema": "ledgrid.scene.v2", "background": {"component_id": "world_flags", "parameters": {"flag": "mine"}}}
    state.write_text(json.dumps({"scene": scene, "brightness": 72, "power": True}))
    save_status({"is_running": False, "brightness": 0, "animation_speed_scale": 1.5, "target_fps": 160}, tmp_path / "presets", state)
    saved = load_saved_state(state)
    assert saved["brightness"] == 0
    assert saved["power"] is False
    assert saved["scene"] == scene
    assert saved["animation"] == "world_flags"
    assert saved["params"] == {"flag": "mine"}


def test_current_parameters_are_saved_inline_without_mutating_presets(tmp_path):
    state = tmp_path / "state.json"
    save_status({"is_running": True, "current_animation": "rainbow", "brightness": 0,
                 "animation_info": {"current_params": {"speed": 3}}, "vibe": {"state": {"profile_id": "neutral"}}}, tmp_path / "presets", state)
    saved = load_saved_state(state)
    assert saved["params"] == {"speed": 3}
    assert saved["vibe"] == {"profile_id": "neutral"}
    assert not (tmp_path / "presets").exists()


def test_load_previous_desired_display_controls_without_substitution(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"schema": "ledgrid.desired-display", "scene": {"background": {"component_id": "retired"}},
                                "output": {"master_brightness": 0, "operator_tempo_scale": 2, "power": False, "target_fps": 155}}))
    saved = load_saved_state(state)
    assert saved["brightness"] == 0
    assert saved["animation_speed_scale"] == 2
    assert saved["target_fps"] == 155
    assert saved["animation"] == "retired"
    assert "fallback_scene" not in saved


def test_failed_setting_write_retains_previous_personal_data(tmp_path):
    state = tmp_path / "state.json"
    state.write_text('{"brightness": 0, "params": {}}')
    original = state.read_bytes()
    with patch("tools.deployment.preserve_deploy_settings.os.replace", side_effect=OSError("interrupted")):
        with pytest.raises(OSError):
            save_status({"brightness": 45}, tmp_path / "presets", state)
    assert state.read_bytes() == original
    assert list(tmp_path.glob(".operator-settings-*")) == []


@pytest.mark.parametrize("brightness", [-1, 256, True, "0"])
def test_invalid_controls_do_not_replace_saved_state(tmp_path, brightness):
    state = tmp_path / "state.json"
    state.write_text('{"brightness": 0, "params": {}}')
    with pytest.raises((RuntimeError, TypeError)):
        save_status({"brightness": brightness}, tmp_path / "presets", state)
    assert load_saved_state(state)["brightness"] == 0


def test_missing_settings_starts_dark_and_stopped(tmp_path):
    from tools.deployment.preserve_deploy_settings import load_saved_state
    assert load_saved_state(tmp_path / "new-wall.json") == {"brightness": 0, "power": False}


def test_stopped_new_selection_survives_restart_but_failed_request_does_not(tmp_path):
    state = tmp_path / "state.json"
    original = {"background": {"component_id": "original"}}
    selected = {"background": {"component_id": "selected"}}
    state.write_text(json.dumps({"scene": original, "brightness": 0, "power": False}))
    save_status({"is_running": False, "scene_state": None, "selected_scene": selected}, tmp_path / "presets", state)
    assert load_saved_state(state)["scene"] == selected
    save_status({"is_running": False, "scene_state": None, "requested_scene": original}, tmp_path / "presets", state)
    assert load_saved_state(state)["scene"] == selected
