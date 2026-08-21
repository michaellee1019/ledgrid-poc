"""Shared real-toolchain fixtures for native-background unit tests."""

from __future__ import annotations

import atexit
from functools import lru_cache
from pathlib import Path
import shutil
import subprocess
import tempfile

from animation.native.builder import NativeBuildResult, build_plugin
from animation.native.constants import TARGET_COMPILER_NAME, TARGET_TOOLCHAIN_PACKAGE


SOURCE_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ID = "aurora_curtains_native"
_temporary_roots: list[Path] = []


def toolchain_available() -> bool:
    package = Path.home() / ".platformio/packages" / TARGET_TOOLCHAIN_PACKAGE
    return shutil.which("platformio") is not None and (
        package / "bin" / TARGET_COMPILER_NAME
    ).is_file()


def create_repo() -> Path:
    root = Path(tempfile.mkdtemp(prefix="ledgrid-native-test-root-"))
    _temporary_roots.append(root)
    relative_files = (
        f"animation/plugins/{PLUGIN_ID}/manifest.json",
        f"animation/plugins/{PLUGIN_ID}/native/background.cpp",
        "firmware/esp32/include/ledgrid/native_background_abi_v2.h",
    )
    for relative in relative_files:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCE_ROOT / relative, destination)
    subprocess.run(("git", "init", "-q", os_fspath(root)), check=True)
    subprocess.run(("git", "-C", os_fspath(root), "add", *relative_files), check=True)
    return root


def os_fspath(path: Path) -> str:
    return str(path)


@lru_cache(maxsize=1)
def deterministic_pair() -> tuple[NativeBuildResult, NativeBuildResult]:
    first_root = create_repo()
    second_root = create_repo()
    return (
        build_plugin(first_root, PLUGIN_ID, first_root / "build"),
        build_plugin(second_root, PLUGIN_ID, second_root / "build"),
    )


@atexit.register
def _cleanup() -> None:
    for root in _temporary_roots:
        shutil.rmtree(root, ignore_errors=True)
