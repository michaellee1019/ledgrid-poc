"""Apply the narrow symbol-table cleanup fix to elf_loader 1.3.2."""

from __future__ import annotations

import hashlib
from pathlib import Path


COMPONENT_HASH = "9f7f6efa06e0847adeba6c9910c4308f33aae7d35ba0cc36c0850105c45e9874"
PRISTINE_SOURCE_SHA256 = (
    "b4ec622eb83afa657ed780b5b3176e18858f588f1ddfc468f08dd3827c7815c9"
)
PATCHED_SOURCE_SHA256 = (
    "d0cc41e2917f21bc522fef91a0637ae0e6e1825b160b783ffe115f3b711f3bf5"
)
PRISTINE_GUARD = b"    if (elf->num && elf->symtab) {\n"
PATCHED_GUARD = b"    if (elf->symtab) {\n"


def patch_source(source: bytes) -> bytes:
    """Patch exactly the vulnerable deinit guard in the pinned source."""
    if source.count(PRISTINE_GUARD) != 1 or PATCHED_GUARD in source:
        raise RuntimeError("elf_loader 1.3.2 cleanup guard is not the pinned source")
    return source.replace(PRISTINE_GUARD, PATCHED_GUARD, 1)


def patch_component(project_dir: Path) -> bool:
    """Patch a resolved component, returning true only when it changed."""
    component = project_dir / "managed_components" / "espressif__elf_loader"
    source_path = component / "src" / "esp_elf.c"
    hash_path = component / ".component_hash"
    if not source_path.is_file() or not hash_path.is_file():
        raise RuntimeError("pinned espressif/elf_loader 1.3.2 was not resolved")
    if hash_path.read_text(encoding="ascii").strip() != COMPONENT_HASH:
        raise RuntimeError("resolved espressif/elf_loader component hash is not pinned")

    source = source_path.read_bytes()
    digest = hashlib.sha256(source).hexdigest()
    if digest == PATCHED_SOURCE_SHA256:
        return False
    if digest != PRISTINE_SOURCE_SHA256:
        raise RuntimeError("resolved esp_elf.c does not match pinned elf_loader 1.3.2")

    patched = patch_source(source)
    if hashlib.sha256(patched).hexdigest() != PATCHED_SOURCE_SHA256:
        raise RuntimeError("elf_loader cleanup patch produced an unexpected source digest")
    source_path.write_bytes(patched)
    return True



def main() -> None:
    import sys

    changed = patch_component(Path(sys.argv[1]))
    if changed:
        print("Applied pinned elf_loader 1.3.2 symbol-table cleanup patch")


if __name__ == "__main__":
    main()
