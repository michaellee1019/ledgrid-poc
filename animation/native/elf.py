"""Complete structural validation for ESP32-S3 native-background ELFs."""

from __future__ import annotations

import io
from dataclasses import dataclass

from elftools.elf.descriptions import describe_e_machine
from elftools.elf.dynamic import DynamicSection
from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection

from .constants import ELF_ENTRYPOINT, MAX_PAYLOAD_BYTES
from .errors import NativeElfError

_FORBIDDEN_SECTION_NAMES = frozenset(
    (".init", ".fini", ".init_array", ".fini_array", ".preinit_array", ".ctors", ".dtors")
)
_FORBIDDEN_SECTION_TYPES = frozenset(("SHT_INIT_ARRAY", "SHT_FINI_ARRAY", "SHT_PREINIT_ARRAY"))
_FORBIDDEN_DYNAMIC_TAGS = frozenset(
    (
        "DT_INIT", "DT_FINI", "DT_INIT_ARRAY", "DT_FINI_ARRAY",
        "DT_PREINIT_ARRAY", "DT_NEEDED", "DT_RPATH", "DT_RUNPATH", "DT_TEXTREL",
    )
)
_ESP32_S3_ELF_FLAGS = 0x300


@dataclass(frozen=True)
class ElfInspection:
    exports: tuple[str, ...]
    imports: tuple[str, ...]
    sections: tuple[str, ...]


def _visible_dynamic_symbols(
    elf: ELFFile,
) -> tuple[set[str], set[str], dict[str, str], dict[str, tuple[int, int]]]:
    exports: set[str] = set()
    imports: set[str] = set()
    types: dict[str, str] = {}
    spans: dict[str, tuple[int, int]] = {}
    dynamic_symbols = elf.get_section_by_name(".dynsym")
    if not isinstance(dynamic_symbols, SymbolTableSection):
        raise NativeElfError("target ELF has no dynamic symbol table")
    for symbol in dynamic_symbols.iter_symbols():
        name = symbol.name
        if not name:
            continue
        bind = symbol["st_info"]["bind"]
        visibility = symbol["st_other"]["visibility"]
        if bind not in {"STB_GLOBAL", "STB_WEAK"}:
            continue
        if symbol["st_shndx"] == "SHN_UNDEF":
            imports.add(name)
            continue
        if visibility in {"STV_DEFAULT", "STV_PROTECTED"}:
            exports.add(name)
            types[name] = symbol["st_info"]["type"]
            spans[name] = (int(symbol["st_value"]), int(symbol["st_size"]))
    return exports, imports, types, spans


def validate_target_elf(data: bytes) -> ElfInspection:
    """Reject any payload outside the narrow loader-independent ELF contract."""

    if not isinstance(data, bytes) or not data or len(data) > MAX_PAYLOAD_BYTES:
        raise NativeElfError("target payload must be non-empty and at most 512 KiB")
    try:
        elf = ELFFile(io.BytesIO(data))
    except Exception as exc:
        raise NativeElfError(f"target payload is not a structurally valid ELF: {exc}") from exc
    if elf.elfclass != 32 or not elf.little_endian:
        raise NativeElfError("target payload must be little-endian ELF32")
    if elf.header["e_type"] != "ET_DYN":
        raise NativeElfError("target payload must be an ELF shared object (ET_DYN)")
    if elf.header["e_machine"] != "EM_XTENSA":
        machine = describe_e_machine(elf.header["e_machine"])
        raise NativeElfError(f"target payload must use Xtensa machine type, got {machine}")
    identity = elf.header["e_ident"]
    if (
        identity["EI_OSABI"] != "ELFOSABI_SYSV"
        or int(identity["EI_ABIVERSION"]) != 0
        or elf.header["e_version"] != "EV_CURRENT"
        or int(elf.header["e_entry"]) != 0
        or int(elf.header["e_flags"]) != _ESP32_S3_ELF_FLAGS
    ):
        raise NativeElfError("target ELF OSABI/version/ESP32-S3 flags are incompatible")

    sections: list[str] = []
    for section in elf.iter_sections():
        sections.append(section.name)
        if (
            section.data_size > 0
            and (
                section.name in _FORBIDDEN_SECTION_NAMES
                or section["sh_type"] in _FORBIDDEN_SECTION_TYPES
            )
        ):
            raise NativeElfError(f"target payload contains forbidden initializer/finalizer section {section.name!r}")
        if isinstance(section, DynamicSection):
            for tag in section.iter_tags():
                if tag.entry.d_tag in _FORBIDDEN_DYNAMIC_TAGS:
                    raise NativeElfError(
                        f"target payload contains forbidden dynamic tag {tag.entry.d_tag}"
                    )

    exports, imports, types, spans = _visible_dynamic_symbols(elf)
    if exports != {ELF_ENTRYPOINT}:
        raise NativeElfError(
            f"target payload exports must be exactly [{ELF_ENTRYPOINT!r}], got {sorted(exports)}"
        )
    if types.get(ELF_ENTRYPOINT) != "STT_FUNC":
        raise NativeElfError("target payload entrypoint must be a function")
    if imports:
        raise NativeElfError(f"target payload imports forbidden symbols: {sorted(imports)}")

    # Fail closed on executable+writable, overlapping, or malformed load maps.
    load_ranges: list[tuple[int, int, int]] = []
    for segment in elf.iter_segments():
        offset = int(segment["p_offset"])
        size = int(segment["p_filesz"])
        if offset < 0 or size < 0 or offset + size > len(data):
            raise NativeElfError("target ELF segment escapes the payload bytes")
        flags = int(segment["p_flags"])
        if segment["p_type"] != "PT_LOAD":
            continue
        memory_size = int(segment["p_memsz"])
        address = int(segment["p_vaddr"])
        alignment = int(segment["p_align"])
        if (
            memory_size < size
            or memory_size > 2 * 1024 * 1024
            or alignment < 1
            or alignment & (alignment - 1)
            or address % alignment != offset % alignment
        ):
            raise NativeElfError("target ELF contains a malformed load segment")
        if flags & 0x1 and flags & 0x2:
            raise NativeElfError("target ELF contains a writable executable load segment")
        load_ranges.append((address, address + memory_size, flags))
    if not load_ranges or not any(flags & 0x1 for _start, _end, flags in load_ranges):
        raise NativeElfError("target ELF has no executable load segment")
    ordered_ranges = sorted(load_ranges)
    if any(left[1] > right[0] for left, right in zip(ordered_ranges, ordered_ranges[1:])):
        raise NativeElfError("target ELF load segments overlap")
    entrypoint_address, entrypoint_size = spans[ELF_ENTRYPOINT]
    if entrypoint_size <= 0:
        raise NativeElfError("target ELF entrypoint has an empty symbol span")
    if not any(
        start <= entrypoint_address
        and entrypoint_address + entrypoint_size <= end
        and flags & 0x1
        for start, end, flags in load_ranges
    ):
        raise NativeElfError("target ELF entrypoint is outside executable memory")
    return ElfInspection(tuple(sorted(exports)), tuple(sorted(imports)), tuple(sections))
