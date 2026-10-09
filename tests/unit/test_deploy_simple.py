from pathlib import Path
import json
import subprocess
from unittest.mock import patch

import pytest

from tools.deployment import deploy_entrypoint, deploy_target, flash_receivers


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)


def candidate(root, tag="new"):
    write(root / "incoming/scripts/start_systemd.sh", "#!/bin/bash\n")
    write(root / "incoming/web/app.py", tag)
    write(root / "incoming/config/default.json", '{"default": true}')


def personal_data(root):
    write(root / "run_state/composer_looks.json", '{"looks": ["mine"]}')
    write(root / "run_state/composer_playlists.json", '{"playlists": ["mine"]}')
    write(root / "run_state/before_deploy.json", '{"brightness": 0, "params": {}, "power": false}')
    write(root / "presets/animations/rainbow/mine.json", '{"speed": 2}')
    write(root / "config/plant_pixel_map_32x138.json", '{"calibration": "measured"}')


def test_first_deployment_backs_up_and_keeps_personal_data_on_replacement(tmp_path):
    personal_data(tmp_path)
    (tmp_path / "config/plant_pixel_map_32x138.json").chmod(0o444)
    candidate(tmp_path)
    app = deploy_target.install_app(tmp_path)
    assert app == tmp_path / "app"
    assert (app / "run_state/composer_looks.json").read_text() == '{"looks": ["mine"]}'
    assert (app / "config/plant_pixel_map_32x138.json").is_symlink()
    assert (app / "config/plant_pixel_map_32x138.json").stat().st_mode & 0o200
    backups = list((tmp_path / "data-backups").iterdir())
    assert (backups[0] / "0/run_state/before_deploy.json").read_text().find('"brightness": 0') >= 0
    # App replacement never follows its data symlinks to delete personal data.
    write(app / "run_state/composer_looks.json", '{"looks": ["newer edit"]}')
    candidate(tmp_path, "second")
    deploy_target.install_app(tmp_path)
    assert (app / "web/app.py").read_text() == "second"
    assert (app / "run_state/composer_looks.json").read_text() == '{"looks": ["newer edit"]}'
    assert (app / "presets/animations/rainbow/mine.json").exists()


def test_migration_uses_current_release_calibration_and_preserves_source(tmp_path):
    release = tmp_path / "releases/old"
    write(release / "config/globe.json", '{"measured": true}')
    (tmp_path / "current").symlink_to(release)
    candidate(tmp_path)
    deploy_target.install_app(tmp_path)
    assert (tmp_path / "app/config/globe.json").read_text() == '{"measured": true}'
    assert (release / "config/globe.json").exists()


def test_interrupted_data_copy_can_resume_without_accepting_partial_file(tmp_path):
    personal_data(tmp_path)
    original = deploy_target.shutil.copy2
    interrupted = False

    def fail_during_copy(source, target):
        nonlocal interrupted
        if not interrupted and str(target).endswith("before_deploy.json.migration"):
            interrupted = True
            write(Path(target), "partial")
            raise OSError("power interruption")
        return original(source, target)

    with patch.object(deploy_target.shutil, "copy2", side_effect=fail_during_copy):
        with pytest.raises(OSError, match="power interruption"):
            deploy_target.migrate_data(tmp_path)
    assert not (tmp_path / "data/.migration-complete").exists()
    deploy_target.migrate_data(tmp_path)
    assert json.loads((tmp_path / "data/run_state/before_deploy.json").read_text())["brightness"] == 0
    assert (tmp_path / "run_state/before_deploy.json").exists()


def test_failed_dependency_install_leaves_data_and_service_stopped(tmp_path):
    personal_data(tmp_path)
    candidate(tmp_path)
    calls = []

    def commands(*args, **kwargs):
        calls.append(tuple(map(str, args)))
        if "pip" in args:
            raise subprocess.CalledProcessError(1, args)

    with patch.object(deploy_target, "command", side_effect=commands):
        with pytest.raises(subprocess.CalledProcessError):
            deploy_target.deploy(tmp_path, "python", 1)
    assert "stop" in calls[0]
    assert not any("start" in command for command in calls)
    assert (tmp_path / "data/presets/animations/rainbow/mine.json").exists()
    # Rerunning after the failure can replace the incomplete app again.
    candidate(tmp_path, "fixed")
    deploy_target.install_app(tmp_path)
    assert (tmp_path / "app/web/app.py").read_text() == "fixed"


def mapping_and_devices():
    entries = [{"logical_device": index, "spi_route": list(route), "hardware_serial": f"00:00:00:00:00:{index:02x}"} for index, route in enumerate(flash_receivers.ROUTES)]
    devices = [{"port": f"/dev/ttyACM{4-index}", "hwid": f"USB VID:PID=303A:1001 SER=0000000000{index:02x} LOCATION=1-{index}"} for index in range(5)]
    return {"identities": entries}, devices


def test_flash_uses_explicit_serial_order_and_refuses_missing_or_duplicate(tmp_path):
    mapping, devices = mapping_and_devices()
    assert flash_receivers.mapped_ports(mapping, devices) == [f"/dev/ttyACM{index}" for index in reversed(range(5))]
    with pytest.raises(ValueError, match="absent"):
        flash_receivers.mapped_ports(mapping, devices[:-1])
    with pytest.raises(ValueError, match="multiple"):
        flash_receivers.mapped_ports(mapping, [*devices, devices[0]])
    mapping["identities"][3]["spi_route"] = [0, 0]
    with pytest.raises(ValueError, match="routes"):
        flash_receivers.mapped_ports(mapping, devices)


def test_partial_flashing_retries_all_five_without_success_ledger(tmp_path):
    mapping, devices = mapping_and_devices()
    write(tmp_path / "data/run_state/receiver_identity_mapping.json", json.dumps(mapping))
    write(tmp_path / ".platformio-venv/bin/pio", "pio")
    calls = []

    def upload(command, **kwargs):
        calls.append(command)
        if "--upload-port" in command and command[-1] == "/dev/ttyACM2":
            raise subprocess.CalledProcessError(1, command)

    with patch.object(flash_receivers.Path, "home", return_value=tmp_path), patch.object(flash_receivers.subprocess, "check_output", return_value=json.dumps(devices).encode()):
        with patch.object(flash_receivers.subprocess, "run", side_effect=upload):
            with pytest.raises(subprocess.CalledProcessError):
                flash_receivers.flash(tmp_path)
        with patch.object(flash_receivers.subprocess, "run") as retried:
            flash_receivers.flash(tmp_path)
            assert retried.call_count == 6
            assert retried.call_args_list[1].args[0][-1] == "/dev/ttyACM4"
    assert len(calls) == 4
    assert not (tmp_path / "data/run_state/receiver_firmware_inventory.json").exists()


def test_dedicated_key_is_mandatory_and_deploy_directory_is_bounded(tmp_path):
    with pytest.raises(ValueError, match="Dedicated SSH key"):
        deploy_entrypoint.ssh_options(tmp_path, None)
    write(tmp_path / ".gpt-key", "key")
    assert "IdentitiesOnly=yes" in deploy_entrypoint.ssh_options(tmp_path, None)
    for unsafe in ("/", ".", "../root", "wall/../../home", "x; echo secret"):
        with pytest.raises(ValueError):
            deploy_entrypoint.safe_deploy_dir(unsafe)


def test_source_copy_excludes_personal_data_and_stale_generated_assets(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    write(tmp_path / "web/app.py", "source")
    write(tmp_path / "web/static/generated/catalog.json", "generated")
    write(tmp_path / "run_state/private.json", "private")
    write(tmp_path / "presets/animations/private.json", "private")
    write(tmp_path / ".beads/issues.jsonl", "private tracker")
    write(tmp_path / ".gpt-key", "private key")
    paths = deploy_entrypoint.source_paths(tmp_path)
    assert Path("web/app.py") in paths
    assert Path("web/static/generated/catalog.json") not in paths
    assert all(path.parts[0] not in {"run_state", "presets", ".beads", ".gpt-key"} for path in paths)


def test_readiness_reports_no_display_proof():
    with patch.object(deploy_target.subprocess, "run"), patch.object(deploy_target.urllib.request, "urlopen") as opened:
        opened.return_value.__enter__.return_value.status = 200
        deploy_target.readiness(1)
        opened.assert_called_once()


def test_selected_release_calibration_wins_over_stale_root_copy(tmp_path):
    write(tmp_path / "installation_profile_library/profiles/personal.json", '{"calibration":"saved"}')
    write(tmp_path / "config/plant_pixel_map.json", '{"source":"old-root"}')
    write(tmp_path / "current/config/plant_pixel_map.json", '{"source":"current-release"}')
    write(tmp_path / "run_state/settings.json", '{"brightness":255}')
    write(tmp_path / "current/run_state/settings.json", '{"brightness":0}')
    data = deploy_target.migrate_data(tmp_path)
    assert json.loads((data / "config/plant_pixel_map.json").read_text())["source"] == "current-release"
    assert json.loads((data / "run_state/settings.json").read_text())["brightness"] == 255
    backups = list((tmp_path / "data-backups").iterdir())
    assert (backups[0] / "0/config/plant_pixel_map.json").exists()
    assert (backups[0] / "1/config/plant_pixel_map.json").exists()
    assert (backups[0] / "0/installation_profile_library/profiles/personal.json").exists()


def test_preview_assets_use_target_calibration_before_build(tmp_path):
    with patch.object(deploy_entrypoint.subprocess, "check_output", return_value="/home/wall/data/config"), patch.object(deploy_entrypoint.subprocess, "run") as copied:
        deploy_entrypoint.fetch_calibration(tmp_path, "wall@host", "wall", ["-i", "/dedicated-key"])
    command = copied.call_args.args[0]
    assert "wall@host:/home/wall/data/config/" in command
    assert "--include=*.json" in command
    assert command[-1] == str(tmp_path / "config") + "/"


def test_calibration_directory_from_old_readonly_release_can_be_linked(tmp_path):
    personal_data(tmp_path)
    candidate(tmp_path)
    (tmp_path / "incoming/config").chmod(0o555)
    app = deploy_target.install_app(tmp_path)
    assert (app / "config/default.json").is_symlink()
    assert (app / "config/plant_pixel_map_32x138.json").is_symlink()


def test_existing_runtime_environment_is_reused(tmp_path):
    candidate(tmp_path)
    write(tmp_path / "existing-venv/bin/python", "python")
    (tmp_path / "venv").symlink_to(tmp_path / "existing-venv")
    with patch.object(deploy_target, "command") as commands, patch.object(deploy_target, "write_service", return_value={}), patch.object(deploy_target, "readiness"):
        deploy_target.deploy(tmp_path, "python", 1)
    assert not any(call.args[:3] == ("python3", "-m", "venv") for call in commands.call_args_list)
    assert any("pip" in call.args for call in commands.call_args_list)
