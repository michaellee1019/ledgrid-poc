"""Content identity for the loopback-only REL-01 fixture runtime."""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path


SOURCE_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
RELEASE_ID_DOMAIN = b"ledgrid-rel01-loopback-release-v1\0"


def fixture_release_id(source_commit: str) -> str:
    """Return the fixture release ID bound to one exact Git commit."""

    if SOURCE_COMMIT_PATTERN.fullmatch(source_commit) is None:
        raise ValueError("qualification fixture source commit is invalid")
    digest = hashlib.sha256()
    digest.update(RELEASE_ID_DOMAIN)
    digest.update(source_commit.encode("ascii"))
    return digest.hexdigest()


def resolve_fixture_source_identity(root: Path) -> tuple[str, str]:
    """Resolve the checked-out commit and its fixture release identity."""

    source_commit = subprocess.run(
        ("git", "rev-parse", "--verify", "HEAD"),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    try:
        release_id = fixture_release_id(source_commit)
    except ValueError as exc:
        raise RuntimeError("qualification fixture source commit is unavailable") from exc
    return source_commit, release_id
