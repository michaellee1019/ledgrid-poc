import json
import subprocess
import sys
from pathlib import Path

from tools.generate_firmware_animation_pipeline_golden import render_header


REPO_ROOT = Path(__file__).resolve().parents[2]
JSON_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "animation_pipeline_v1.json"
CPP_FIXTURE = (
    REPO_ROOT
    / "firmware"
    / "esp32"
    / "test"
    / "fixtures"
    / "animation_pipeline_v1.hpp"
)


def test_cpp_firmware_fixture_is_exact_derivative_of_json_authority():
    fixture = json.loads(JSON_FIXTURE.read_text(encoding="utf-8"))
    assert CPP_FIXTURE.read_text(encoding="utf-8") == render_header(fixture)


def test_firmware_fixture_check_command_passes():
    subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "generate_firmware_animation_pipeline_golden.py"),
            "--check",
        ],
        cwd=REPO_ROOT,
        check=True,
    )
