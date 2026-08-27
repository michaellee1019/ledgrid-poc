#!/usr/bin/env python3
"""Build the repository-owned receiver-native browser preview deterministically."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "web/static/generated/composer/aurora_curtains_native.wasm"
BRIDGE = ROOT / "animation/browser_preview/native/aurora_curtains_bridge.cpp"
PLUGIN_SOURCE = ROOT / "animation/plugins/aurora_curtains_native/native/background.cpp"
ABI_INCLUDE = ROOT / "firmware/esp32/include"
EXPORTS = (
    "_lg_browser_init",
    "_lg_browser_set_parameters",
    "_lg_browser_render",
    "_lg_browser_pixels",
    "_lg_browser_pixels_size",
    "_lg_browser_width",
    "_lg_browser_height",
    "_lg_browser_changed",
    "_lg_browser_last_error",
    "_lg_browser_cleanup",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build(
    output: Path = DEFAULT_OUTPUT,
    *,
    compiler: str | None = None,
    repo_root: Path = ROOT,
) -> Path:
    selected = compiler or os.environ.get("EMXX") or "em++"
    executable = shutil.which(selected)
    if executable is None:
        raise RuntimeError("Emscripten C++ compiler em++ is unavailable")
    root = repo_root.resolve()
    bridge = root / BRIDGE.relative_to(ROOT)
    plugin = root / PLUGIN_SOURCE.relative_to(ROOT)
    include = root / ABI_INCLUDE.relative_to(ROOT)
    for path, label in ((bridge, "browser bridge"), (plugin, "native plugin source")):
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(
                f"{label} is missing or is not a regular source file: {path}"
            )

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    environment = {
        **os.environ,
        "LC_ALL": "C",
        "LANG": "C",
        "SOURCE_DATE_EPOCH": "0",
        "ZERO_AR_DATE": "1",
        "EM_CACHE": os.environ.get("EM_CACHE", "/private/tmp/ledgrid-browser-em-cache"),
    }
    command = (
        executable,
        "-std=c++17",
        "-Os",
        "-g0",
        "-fno-exceptions",
        "-fno-rtti",
        "-fno-ident",
        "-ffp-contract=off",
        "-fvisibility=hidden",
        "-ffunction-sections",
        "-fdata-sections",
        "-frandom-seed=ledgrid-browser-native-v2",
        "-DLG_HOST_PREVIEW=1",
        "-I",
        str(include),
        "-sSTANDALONE_WASM=1",
        "-sFILESYSTEM=0",
        "-sALLOW_MEMORY_GROWTH=0",
        "-sINITIAL_MEMORY=1048576",
        "-sSTACK_SIZE=65536",
        "-sASSERTIONS=0",
        "-sERROR_ON_UNDEFINED_SYMBOLS=1",
        f"-sEXPORTED_FUNCTIONS={','.join(EXPORTS)}",
        "-Wl,--no-entry",
        "-Wl,--strip-all",
        "-o",
        str(output),
        str(bridge),
        str(plugin),
    )
    completed = subprocess.run(
        command,
        # Homebrew's Emscripten 6 system-library recipes currently spell some
        # inputs relative to the working directory. A shallow, writable build
        # cwd keeps first-run cache generation valid even for deep worktrees.
        cwd=Path("/private/tmp"),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"browser-native build failed: {detail}")
    if output.read_bytes()[:4] != b"\0asm":
        raise RuntimeError("browser-native build did not produce a WebAssembly module")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check", action="store_true", help="build twice and require byte identity"
    )
    args = parser.parse_args()
    output = build(args.output)
    if args.check:
        with tempfile.TemporaryDirectory(
            prefix="ledgrid-browser-native-check-"
        ) as name:
            second = build(Path(name) / output.name)
            if output.read_bytes() != second.read_bytes():
                raise RuntimeError(
                    "repeated browser-native builds are not byte-identical"
                )
    print(f"{output} {output.stat().st_size} bytes sha256={sha256(output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
