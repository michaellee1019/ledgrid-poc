"""Resolve SSH client options for workstation-to-Pi deployment."""

from __future__ import annotations

import os
import shlex
import stat
import sys
from pathlib import Path

DEFAULT_SSH_BASE = (
    "-F",
    "/dev/null",
    "-o",
    "IdentityAgent=none",
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=10",
    "-o",
    "StrictHostKeyChecking=accept-new",
)


def resolve_identity(root: Path, explicit_key: str | None = None) -> Path:
    configured = explicit_key or os.environ.get("SSH_KEY") or os.environ.get("LEDGRID_SSH_KEY") or ".gpt-key"
    identity = Path(configured).expanduser()
    if not identity.is_absolute():
        identity = root / identity
    if not identity.is_file() or not os.access(identity, os.R_OK):
        raise ValueError(f"Dedicated SSH key is missing or unreadable: {identity}")
    mode = stat.S_IMODE(identity.stat().st_mode)
    if mode & (stat.S_IRGRP | stat.S_IROTH | stat.S_IWGRP | stat.S_IWOTH):
        raise ValueError(
            "SSH identity permissions must not grant group/other access: "
            f"{identity} has mode {mode:04o}"
        )
    resolved = identity.resolve()
    if any(ord(char) < 32 for char in str(resolved)):
        raise ValueError("Dedicated SSH key path contains a control character")
    return resolved


def ssh_options(root: Path, explicit_key: str | None = None) -> list[str]:
    """Return SSH options for the required dedicated wall identity."""
    return ["-i", str(resolve_identity(root, explicit_key)), "-o", "IdentitiesOnly=yes", *DEFAULT_SSH_BASE]


if __name__ == "__main__":
    try:
        options = ssh_options(Path(__file__).resolve().parents[2])
        print(shlex.join(["ssh", *options]) if sys.argv[1:] == ["--rsync"] else "\n".join(options))
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
