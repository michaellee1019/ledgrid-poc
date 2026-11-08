"""Resolve SSH client options for workstation-to-Pi deployment."""

from __future__ import annotations

import os
import stat
from pathlib import Path

DEFAULT_SSH_BASE = (
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=10",
    "-o",
    "StrictHostKeyChecking=accept-new",
)


def _identity_candidates(root: Path, explicit_key: str | None) -> list[Path]:
    candidates: list[Path] = []
    if explicit_key:
        candidates.append(Path(explicit_key))
    for env_name in ("SSH_KEY", "LEDGRID_SSH_KEY"):
        value = os.environ.get(env_name)
        if value:
            candidates.append(Path(value))
    candidates.append(root / ".gpt-key")
    return candidates


def _resolve_identity(path: Path, root: Path) -> Path | None:
    identity = path.expanduser()
    if not identity.is_absolute():
        identity = root / identity
    if not identity.is_file() or not os.access(identity, os.R_OK):
        return None
    mode = stat.S_IMODE(identity.stat().st_mode)
    if mode & (stat.S_IRGRP | stat.S_IROTH | stat.S_IWGRP | stat.S_IWOTH):
        raise ValueError(
            "SSH identity permissions must not grant group/other access: "
            f"{identity} has mode {mode:04o}"
        )
    return identity.resolve()


def ssh_options(root: Path, explicit_key: str | None = None) -> list[str]:
    """Return ``ssh`` options, using a dedicated key when one is configured."""
    for candidate in _identity_candidates(root, explicit_key):
        resolved = _resolve_identity(candidate, root)
        if resolved is not None:
            return ["-i", str(resolved), "-o", "IdentitiesOnly=yes", *DEFAULT_SSH_BASE]
    return list(DEFAULT_SSH_BASE)
