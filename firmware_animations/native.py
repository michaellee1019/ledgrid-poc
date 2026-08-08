"""Reproducible external-toolchain contracts for trusted native builds."""

from __future__ import annotations

import os
import shlex
import subprocess
import ctypes
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .constants import DEFAULT_IMPORT_ALLOWLIST, LEDS_PER_STRIP, LOCAL_STRIPS, RECEIVER_COUNT
from .errors import NativeToolchainError, PackageValidationError
from .manifest import validate_parameters
from .tracks import ImageFrames, generate_preview_webp


@dataclass(frozen=True)
class NativeBuildCommands:
    esp32: tuple[str, ...]
    host_preview: tuple[str, ...]


@dataclass(frozen=True)
class HostRenderRun:
    """Frames and exact per-receiver callback timings from a trusted host build."""

    frames: tuple[bytes, ...]
    render_ms: tuple[float, ...]


def native_build_commands(
    sources: Sequence[str | Path],
    *,
    sdk_include: str | Path,
    module_output: str | Path,
    host_output: str | Path,
    esp_cxx: str = "xtensa-esp32s3-elf-g++",
    host_cxx: str = "c++",
) -> NativeBuildCommands:
    source_args = tuple(str(Path(item)) for item in sources)
    if not source_args:
        raise NativeToolchainError("at least one native source is required")
    common = ("-std=c++17", "-fPIC", "-fno-exceptions", "-fno-rtti", "-I", str(Path(sdk_include)))
    esp32 = (
        esp_cxx, *common, "-Os", "-shared", "-nostdlib", "-fno-ident",
        "-frandom-seed=ledgrid-animation-v1", "-Wl,--gc-sections", "-Wl,--build-id=none",
        "-o", str(Path(module_output)), *source_args,
    )
    host = (
        host_cxx, *common, "-O2", "-shared", "-DLGA_HOST_PREVIEW=1",
        "-o", str(Path(host_output)), *source_args,
    )
    return NativeBuildCommands(esp32, host)


def run_native_build(commands: NativeBuildCommands, *, cwd: str | Path | None = None) -> None:
    env = {**os.environ, "LC_ALL": "C", "SOURCE_DATE_EPOCH": "0"}
    for label, command in (("ESP32-S3 module", commands.esp32), ("host preview", commands.host_preview)):
        try:
            completed = subprocess.run(command, cwd=cwd, env=env, check=False, capture_output=True, text=True)
        except OSError as exc:
            raise NativeToolchainError(f"cannot run {label} compiler {command[0]!r}: {exc}") from exc
        if completed.returncode:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise NativeToolchainError(f"{label} build failed: {detail}")


def undefined_imports(module: str | Path, *, nm: str = "xtensa-esp32s3-elf-nm") -> list[str]:
    try:
        completed = subprocess.run((nm, "-u", str(Path(module))), check=False, capture_output=True, text=True)
    except OSError as exc:
        raise NativeToolchainError(f"cannot inspect native imports with {nm!r}: {exc}") from exc
    if completed.returncode:
        raise NativeToolchainError(f"native import inspection failed: {completed.stderr.strip()}")
    imports = sorted({line.split()[-1] for line in completed.stdout.splitlines() if line.strip()})
    forbidden = set(imports) - set(DEFAULT_IMPORT_ALLOWLIST)
    if forbidden:
        raise PackageValidationError(f"native module imports forbidden symbols: {sorted(forbidden)}")
    try:
        exports_result = subprocess.run((nm, "-g", str(Path(module))), check=False, capture_output=True, text=True)
    except OSError as exc:
        raise NativeToolchainError(f"cannot inspect native exports with {nm!r}: {exc}") from exc
    if exports_result.returncode:
        raise NativeToolchainError(f"native export inspection failed: {exports_result.stderr.strip()}")
    exports = {line.split()[-1] for line in exports_result.stdout.splitlines() if line.strip()}
    if "ledgrid_animation_v1" not in exports:
        raise PackageValidationError("native module does not export ledgrid_animation_v1")
    return imports


def shell_display(command: Sequence[str]) -> str:
    return shlex.join(command)


class _ParameterValue(ctypes.Union):
    _fields_ = [
        ("integer", ctypes.c_int32), ("real", ctypes.c_float),
        ("boolean", ctypes.c_uint8), ("enum_value", ctypes.c_char_p),
        ("color", ctypes.c_uint8 * 3),
    ]


class _Parameter(ctypes.Structure):
    _fields_ = [
        ("name", ctypes.c_char_p), ("type", ctypes.c_uint8),
        ("reserved", ctypes.c_uint8 * 3), ("value", _ParameterValue),
    ]


class _Context(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_uint32), ("local_strips", ctypes.c_uint8),
        ("leds_per_strip", ctypes.c_uint16), ("global_strip_offset", ctypes.c_uint16),
        ("elapsed_us", ctypes.c_uint64), ("scaled_elapsed_us", ctypes.c_uint64),
        ("frame_index", ctypes.c_uint32), ("parameters", ctypes.POINTER(_Parameter)),
        ("parameter_count", ctypes.c_uint8), ("rgb_output", ctypes.POINTER(ctypes.c_uint8)),
        ("rgb_output_size", ctypes.c_size_t),
    ]


_RANDOM = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32))
_HSV = ctypes.CFUNCTYPE(None, ctypes.c_uint16, ctypes.c_uint8, ctypes.c_uint8, ctypes.POINTER(ctypes.c_uint8))
_RGB565 = ctypes.CFUNCTYPE(ctypes.c_uint16, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8)
_TRIG = ctypes.CFUNCTYPE(ctypes.c_float, ctypes.c_float)


class _Helpers(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_uint32), ("random_u32", _RANDOM),
        ("hsv_to_rgb", _HSV), ("rgb_to_565", _RGB565),
        ("sin_f32", _TRIG), ("cos_f32", _TRIG),
    ]


_INIT = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(_Context), ctypes.POINTER(_Helpers), ctypes.POINTER(ctypes.c_void_p))
_RENDER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(_Context))
_CLEANUP = ctypes.CFUNCTYPE(None, ctypes.c_void_p)


class _Api(ctypes.Structure):
    _fields_ = [("abi_version", ctypes.c_uint32), ("initialize", _INIT), ("render", _RENDER), ("cleanup", _CLEANUP)]


def _preview_parameters(
    schema: Mapping[str, Mapping[str, Any]], values: Mapping[str, Any]
) -> tuple[Any, list[bytes]]:
    type_ids = {"int": 1, "float": 2, "bool": 3, "enum": 4, "color": 5}
    strings: list[bytes] = []
    parameters = (_Parameter * len(schema))()
    for index, (name, spec) in enumerate(sorted(schema.items())):
        name_bytes = name.encode("ascii")
        strings.append(name_bytes)
        parameters[index].name = name_bytes
        parameters[index].type = type_ids[spec["type"]]
        default = values[name]
        if spec["type"] == "int":
            parameters[index].value.integer = default
        elif spec["type"] == "float":
            parameters[index].value.real = default
        elif spec["type"] == "bool":
            parameters[index].value.boolean = 1 if default else 0
        elif spec["type"] == "color":
            value = bytes.fromhex(default[1:])
            parameters[index].value.color[:] = value
        else:
            enum_bytes = default.encode("utf-8")
            strings.append(enum_bytes)
            parameters[index].value.enum_value = enum_bytes
    return parameters, strings


def render_host_frames(
    host_library: str | Path,
    metadata: Mapping[str, Any],
    *,
    frame_count: int = 12,
    duration_ms: int = 80,
    parameters: Mapping[str, Any] | None = None,
) -> HostRenderRun:
    """Execute a trusted host build at exact four-receiver wall geometry.

    This function is intentionally authoring-side only. Uploaded target ELF
    payloads are never loaded by the controller or dashboard process.
    """
    if not 1 <= frame_count <= 120 or not 10 <= duration_ms <= 1000:
        raise NativeToolchainError("preview frame count or duration is outside safe bounds")
    try:
        library = ctypes.CDLL(str(Path(host_library)))
        entry = library.ledgrid_animation_v1
        entry.argtypes = []
        entry.restype = ctypes.POINTER(_Api)
        api_pointer = entry()
    except (OSError, AttributeError) as exc:
        raise NativeToolchainError(f"cannot load host preview renderer: {exc}") from exc
    if not api_pointer or api_pointer.contents.abi_version != 1:
        raise NativeToolchainError("host preview renderer returned an incompatible ABI")
    api = api_pointer.contents
    schema = metadata.get("parameter_schema", {})
    if not isinstance(schema, Mapping):
        raise NativeToolchainError("native preview parameter schema must be an object")
    try:
        resolved = validate_parameters(schema, parameters)
    except PackageValidationError as exc:
        raise NativeToolchainError(f"invalid native preview parameters: {exc}") from exc
    parameter_array, keepalive = _preview_parameters(schema, resolved)
    def random_u32(state: Any) -> int:
        value = int(state[0])
        value ^= (value << 13) & 0xFFFFFFFF
        value ^= value >> 17
        value ^= (value << 5) & 0xFFFFFFFF
        state[0] = value & 0xFFFFFFFF
        return state[0]
    def hsv_to_rgb(hue: int, saturation: int, value: int, output: Any) -> None:
        import colorsys
        red, green, blue = colorsys.hsv_to_rgb(hue / 65535.0, saturation / 255.0, value / 255.0)
        output[0], output[1], output[2] = round(red * 255), round(green * 255), round(blue * 255)
    def rgb_to_565(red: int, green: int, blue: int) -> int:
        return ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
    def sin_f32(radians: float) -> float:
        import math
        return math.sin(radians)
    def cos_f32(radians: float) -> float:
        import math
        return math.cos(radians)
    random_callback = _RANDOM(random_u32)
    hsv_callback = _HSV(hsv_to_rgb)
    rgb565_callback = _RGB565(rgb_to_565)
    sin_callback = _TRIG(sin_f32)
    cos_callback = _TRIG(cos_f32)
    helpers = _Helpers(
        1, random_callback, hsv_callback, rgb565_callback,
        sin_callback, cos_callback,
    )
    _ = (
        keepalive, random_callback, hsv_callback, rgb565_callback,
        sin_callback, cos_callback,
    )
    frames: list[bytes] = []
    render_ms: list[float] = []
    states = [ctypes.c_void_p() for _ in range(RECEIVER_COUNT)]
    initialized = 0
    try:
        for device in range(RECEIVER_COUNT):
            output = (ctypes.c_uint8 * (LOCAL_STRIPS * LEDS_PER_STRIP * 3))()
            context = _Context(
                1, LOCAL_STRIPS, LEDS_PER_STRIP, device * LOCAL_STRIPS, 0, 0, 0,
                parameter_array, len(parameter_array), output, len(output),
            )
            if api.initialize(
                ctypes.byref(context), ctypes.byref(helpers),
                ctypes.byref(states[device]),
            ) != 0:
                raise NativeToolchainError(f"host preview initialization failed for receiver {device}")
            initialized += 1
        for frame_index in range(frame_count):
            local_frames = []
            for device in range(RECEIVER_COUNT):
                output = (ctypes.c_uint8 * (LOCAL_STRIPS * LEDS_PER_STRIP * 3))()
                elapsed_us = frame_index * duration_ms * 1000
                context = _Context(
                    1, LOCAL_STRIPS, LEDS_PER_STRIP, device * LOCAL_STRIPS,
                    elapsed_us, elapsed_us, frame_index,
                    parameter_array, len(parameter_array),
                    output, len(output),
                )
                started_ns = time.perf_counter_ns()
                result = api.render(states[device], ctypes.byref(context))
                render_ms.append((time.perf_counter_ns() - started_ns) / 1_000_000.0)
                if result != 0:
                    raise NativeToolchainError(f"host preview render failed at frame {frame_index}, receiver {device}")
                local_frames.append(bytes(output))
            frames.append(b"".join(local_frames))
    finally:
        for device in range(initialized):
            api.cleanup(states[device])
    return HostRenderRun(tuple(frames), tuple(render_ms))


def render_host_preview(
    host_library: str | Path,
    metadata: Mapping[str, Any],
    *,
    frame_count: int = 12,
    duration_ms: int = 80,
) -> bytes:
    """Execute a trusted host build to create a package preview."""
    run = render_host_frames(
        host_library, metadata, frame_count=frame_count,
        duration_ms=duration_ms,
    )
    return generate_preview_webp(
        ImageFrames(run.frames, (duration_ms,) * frame_count, 0)
    )
