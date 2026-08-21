"""Private subprocess worker for host native-background preview execution."""

from __future__ import annotations

import argparse
import colorsys
import ctypes
import json
import math
import time
from pathlib import Path
from typing import Any, Mapping

from .constants import (
    ABI_VERSION,
    GLOBAL_STRIPS,
    LEDS_PER_STRIP,
    LOCAL_STRIPS,
    MAX_STATE_ALIGNMENT,
    MAX_STATE_BYTES,
    NEUTRAL_PALETTE,
    RECEIVER_VIEWS,
)
from .schema import validate_parameter_schema, validate_parameters

_CANARY_BYTES = 64
_PREFIX = 0xA5
_SUFFIX = 0x5A


class ParameterValue(ctypes.Union):
    _fields_ = [
        ("integer", ctypes.c_int32),
        ("real", ctypes.c_float),
        ("boolean", ctypes.c_uint8),
        ("enum_index", ctypes.c_uint16),
        ("color", ctypes.c_uint8 * 3),
    ]


class Parameter(ctypes.Structure):
    _fields_ = [
        ("id", ctypes.c_uint16),
        ("type", ctypes.c_uint8),
        ("reserved_zero", ctypes.c_uint8),
        ("value", ParameterValue),
    ]


class Vibe(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("profile_version", ctypes.c_uint32),
        ("revision", ctypes.c_uint64),
        ("palette", (ctypes.c_uint8 * 3) * 8),
        ("tempo_q8_8", ctypes.c_uint16),
        ("luminance_q8_8", ctypes.c_uint16),
        ("chroma_q8_8", ctypes.c_uint16),
        ("energy_q8_8", ctypes.c_uint16),
    ]


class Modifier(ctypes.Structure):
    _fields_ = [
        ("id", ctypes.c_uint8),
        ("reserved_zero", ctypes.c_uint8),
        ("strength_q8_8", ctypes.c_uint16),
    ]


class ModifierView(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("revision", ctypes.c_uint64),
        ("entries", ctypes.POINTER(Modifier)),
        ("count", ctypes.c_uint8),
        ("reserved_zero", ctypes.c_uint8 * 7),
    ]


class ProfileSection(ctypes.Structure):
    _fields_ = [
        ("id", ctypes.c_uint16),
        ("encoding", ctypes.c_uint8),
        ("element_width", ctypes.c_uint8),
        ("element_count", ctypes.c_uint32),
        ("data", ctypes.POINTER(ctypes.c_uint8)),
    ]


class ProfileView(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("global_strips", ctypes.c_uint16),
        ("leds_per_strip", ctypes.c_uint16),
        ("global_strip_offset", ctypes.c_uint16),
        ("local_strips", ctypes.c_uint16),
        ("sections", ctypes.POINTER(ProfileSection)),
        ("section_count", ctypes.c_uint8),
        ("clearance_radius", ctypes.c_uint8),
        ("reverse_local_strip_order", ctypes.c_uint8),
        ("reserved_zero", ctypes.c_uint8 * 5),
    ]


RandomCallback = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32))
HsvCallback = ctypes.CFUNCTYPE(
    None,
    ctypes.c_uint16,
    ctypes.c_uint8,
    ctypes.c_uint8,
    ctypes.POINTER(ctypes.c_uint8),
)
TrigCallback = ctypes.CFUNCTYPE(ctypes.c_int16, ctypes.c_uint16)


class Helpers(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_uint32),
        ("struct_size", ctypes.c_uint32),
        ("random_u32", RandomCallback),
        ("hsv_to_rgb", HsvCallback),
        ("sin_q15", TrigCallback),
        ("cos_q15", TrigCallback),
    ]


class Init(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_uint32),
        ("struct_size", ctypes.c_uint32),
        ("global_strips", ctypes.c_uint16),
        ("local_strips", ctypes.c_uint16),
        ("leds_per_strip", ctypes.c_uint16),
        ("global_strip_offset", ctypes.c_uint16),
        ("reverse_local_strip_order", ctypes.c_uint8),
        ("reserved_zero", ctypes.c_uint8 * 7),
        ("pixel_count", ctypes.c_uint32),
        ("deterministic_seed", ctypes.c_uint32),
        ("scene_epoch_ns", ctypes.c_uint64),
        ("helpers", ctypes.POINTER(Helpers)),
    ]


class Context(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_uint32),
        ("struct_size", ctypes.c_uint32),
        ("parameters", ctypes.POINTER(Parameter)),
        ("parameter_count", ctypes.c_uint8),
        ("reserved_zero", ctypes.c_uint8 * 7),
        ("vibe", ctypes.POINTER(Vibe)),
        ("modifiers", ctypes.POINTER(ModifierView)),
        ("profile", ctypes.POINTER(ProfileView)),
    ]


class RenderRequest(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_uint32),
        ("struct_size", ctypes.c_uint32),
        ("unscaled_scene_time_us", ctypes.c_uint64),
        ("scaled_scene_time_us", ctypes.c_uint64),
        ("frame_index", ctypes.c_uint64),
        ("rgb_output", ctypes.POINTER(ctypes.c_uint8)),
        ("rgb_output_size", ctypes.c_uint32),
        ("reserved_zero", ctypes.c_uint32),
    ]


class RenderResult(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("status", ctypes.c_int32),
        ("changed", ctypes.c_uint8),
        ("reserved_zero", ctypes.c_uint8 * 7),
        ("next_deadline_scene_time_us", ctypes.c_uint64),
    ]


InitializeCallback = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(Init))
UpdateCallback = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(Context))
RenderCallback = ctypes.CFUNCTYPE(
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.POINTER(RenderRequest),
    ctypes.POINTER(RenderResult),
)
CleanupCallback = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)


class Api(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_uint32),
        ("struct_size", ctypes.c_uint32),
        ("state_size", ctypes.c_uint32),
        ("state_alignment", ctypes.c_uint32),
        ("initialize", InitializeCallback),
        ("update_context", UpdateCallback),
        ("render", RenderCallback),
        ("cleanup", CleanupCallback),
    ]


def _helpers() -> tuple[Helpers, tuple[Any, ...]]:
    def random_u32(state: Any) -> int:
        value = int(state[0])
        value ^= (value << 13) & 0xFFFFFFFF
        value ^= value >> 17
        value ^= (value << 5) & 0xFFFFFFFF
        state[0] = value & 0xFFFFFFFF
        return int(state[0])

    def hsv_to_rgb(hue: int, saturation: int, value: int, output: Any) -> None:
        red, green, blue = colorsys.hsv_to_rgb(
            hue / 65536.0, saturation / 255.0, value / 255.0
        )
        output[0] = round(red * 255)
        output[1] = round(green * 255)
        output[2] = round(blue * 255)

    def sine(phase: int) -> int:
        return round(math.sin(phase * math.tau / 65536.0) * 32767)

    def cosine(phase: int) -> int:
        return sine((phase + 16384) & 0xFFFF)

    callbacks = (
        RandomCallback(random_u32),
        HsvCallback(hsv_to_rgb),
        TrigCallback(sine),
        TrigCallback(cosine),
    )
    return Helpers(ABI_VERSION, ctypes.sizeof(Helpers), *callbacks), callbacks


def _parameters(
    schema: Mapping[str, Mapping[str, Any]], values: Mapping[str, Any]
) -> Any:
    type_ids = {"int": 1, "float": 2, "bool": 3, "str": 4}
    array = (Parameter * len(schema))()
    for index, name in enumerate(sorted(schema)):
        spec = schema[name]
        array[index].id = index
        array[index].type = type_ids[spec["type"]]
        value = values[name]
        if spec["type"] == "int":
            array[index].value.integer = value
        elif spec["type"] == "float":
            array[index].value.real = value
        elif spec["type"] == "bool":
            array[index].value.boolean = 1 if value else 0
        else:
            array[index].value.enum_index = spec["options"].index(value)
    return array


def _vibe(overrides: Mapping[str, Any]) -> Vibe:
    value = Vibe()
    value.struct_size = ctypes.sizeof(Vibe)
    value.profile_version = 1
    value.revision = 0
    palette = overrides.get("palette") or NEUTRAL_PALETTE
    for index, color in enumerate(palette):
        value.palette[index][:] = color
    value.tempo_q8_8 = 256
    value.luminance_q8_8 = int(overrides.get("luminance_q8_8", 256))
    value.chroma_q8_8 = 256
    value.energy_q8_8 = 128
    return value


class GuardedBuffer:
    def __init__(self, size: int, *, alignment: int = 1):
        self.size = size
        self.alignment = alignment
        self.raw = (ctypes.c_uint8 * (size + _CANARY_BYTES * 2 + alignment))()
        base = ctypes.addressof(self.raw)
        self.address = (base + _CANARY_BYTES + alignment - 1) & ~(alignment - 1)
        self.prefix = self.address - _CANARY_BYTES
        self.suffix = self.address + size
        ctypes.memset(self.prefix, _PREFIX, _CANARY_BYTES)
        ctypes.memset(self.address, 0, size)
        ctypes.memset(self.suffix, _SUFFIX, _CANARY_BYTES)

    @property
    def pointer(self) -> ctypes.c_void_p:
        return ctypes.c_void_p(self.address)

    def output_pointer(self) -> ctypes.POINTER(ctypes.c_uint8):
        return ctypes.cast(self.pointer, ctypes.POINTER(ctypes.c_uint8))

    def bytes(self) -> bytes:
        return ctypes.string_at(self.address, self.size)

    def verify(self, label: str) -> None:
        if ctypes.string_at(self.prefix, _CANARY_BYTES) != bytes([_PREFIX]) * _CANARY_BYTES:
            raise RuntimeError(f"{label} overwrote its prefix canary")
        if ctypes.string_at(self.suffix, _CANARY_BYTES) != bytes([_SUFFIX]) * _CANARY_BYTES:
            raise RuntimeError(f"{label} overwrote its suffix canary")


def _load_api(path: str) -> tuple[Any, Api]:
    library = ctypes.CDLL(path)
    entry = library.ledgrid_native_background_v2
    entry.argtypes = []
    entry.restype = ctypes.POINTER(Api)
    pointer = entry()
    if not pointer:
        raise RuntimeError("host renderer returned a null API")
    api = pointer.contents
    if api.abi_version != ABI_VERSION or api.struct_size != ctypes.sizeof(Api):
        raise RuntimeError("host renderer returned an incompatible API")
    if (
        not 1 <= api.state_size <= MAX_STATE_BYTES
        or not 1 <= api.state_alignment <= MAX_STATE_ALIGNMENT
        or api.state_alignment & (api.state_alignment - 1)
    ):
        raise RuntimeError("host renderer declares invalid state size/alignment")
    return library, api


def _fingerprint(value: Any) -> bytes:
    return ctypes.string_at(ctypes.addressof(value), ctypes.sizeof(value))


def _require_unchanged(before: bytes, value: Any, label: str) -> None:
    if _fingerprint(value) != before:
        raise RuntimeError(f"host renderer mutated read-only {label}")


def run(request: Mapping[str, Any]) -> tuple[dict[str, Any], bytes]:
    manifest = dict(request["manifest"])
    schema, _defaults = validate_parameter_schema(manifest["parameter_schema"])
    values = validate_parameters(schema, request["parameters"])
    frame_count = int(request["frame_count"])
    cadence_period_us = int(request["cadence_period_us"])
    render_budget_ms = float(request["render_budget_ms"])
    scene_times_us = [int(value) for value in request["scene_times_us"]]
    if len(scene_times_us) != frame_count:
        raise RuntimeError("preview scene-time schedule does not match frame count")
    _library, api = _load_api(str(request["host_library"]))
    helpers, helper_keepalive = _helpers()
    helpers_before = _fingerprint(helpers)
    parameter_array = _parameters(schema, values)
    vibe = _vibe(request.get("vibe", {}))
    modifiers = ModifierView(ctypes.sizeof(ModifierView), 0, None, 0, (ctypes.c_uint8 * 7)())
    states: list[GuardedBuffer] = []
    profiles: list[ProfileView] = []
    initialized = 0
    render_ms: list[float] = []
    frames: list[bytes] = []
    # Count complete wall frames with at least one changed receiver, not the
    # number of receiver callbacks that reported a change.
    changed_frames = 0
    missed_deadlines = 0
    _ = helper_keepalive  # Keep Python callback thunks alive through cleanup.
    try:
        for _lane, offset, reverse in RECEIVER_VIEWS:
            state = GuardedBuffer(api.state_size, alignment=api.state_alignment)
            profile = ProfileView(
                ctypes.sizeof(ProfileView),
                GLOBAL_STRIPS,
                LEDS_PER_STRIP,
                offset,
                LOCAL_STRIPS,
                None,
                0,
                0,
                1 if reverse else 0,
                (ctypes.c_uint8 * 5)(),
            )
            init = Init(
                ABI_VERSION,
                ctypes.sizeof(Init),
                GLOBAL_STRIPS,
                LOCAL_STRIPS,
                LEDS_PER_STRIP,
                offset,
                1 if reverse else 0,
                (ctypes.c_uint8 * 7)(),
                LOCAL_STRIPS * LEDS_PER_STRIP,
                0xA17C0A5,
                0x123456789ABCDEF0,
                ctypes.pointer(helpers),
            )
            init_before = _fingerprint(init)
            if api.initialize(state.pointer, ctypes.byref(init)) != 0:
                raise RuntimeError(f"host initialization failed at global offset {offset}")
            _require_unchanged(helpers_before, helpers, "helper table")
            _require_unchanged(init_before, init, "initialization input")
            state.verify("initialize")
            context = Context(
                ABI_VERSION,
                ctypes.sizeof(Context),
                parameter_array,
                len(parameter_array),
                (ctypes.c_uint8 * 7)(),
                ctypes.pointer(vibe),
                ctypes.pointer(modifiers),
                ctypes.pointer(profile),
            )
            context_before = _fingerprint(context)
            parameters_before = _fingerprint(parameter_array)
            vibe_before = _fingerprint(vibe)
            modifiers_before = _fingerprint(modifiers)
            profile_before = _fingerprint(profile)
            if api.update_context(state.pointer, ctypes.byref(context)) != 0:
                raise RuntimeError(f"host context update failed at global offset {offset}")
            _require_unchanged(context_before, context, "context input")
            _require_unchanged(parameters_before, parameter_array, "parameter input")
            _require_unchanged(vibe_before, vibe, "vibe input")
            _require_unchanged(modifiers_before, modifiers, "modifier input")
            _require_unchanged(profile_before, profile, "profile input")
            _require_unchanged(helpers_before, helpers, "helper table")
            state.verify("context update")
            states.append(state)
            profiles.append(profile)
            initialized += 1

        previous_deadlines = [0] * len(RECEIVER_VIEWS)
        previous_local_frames: list[bytes | None] = [None] * len(RECEIVER_VIEWS)
        for frame_index in range(frame_count):
            wall_frame = bytearray(GLOBAL_STRIPS * LEDS_PER_STRIP * 3)
            wall_changed = False
            scene_time_us = scene_times_us[frame_index]
            for device, state in enumerate(states):
                output = GuardedBuffer(LOCAL_STRIPS * LEDS_PER_STRIP * 3)
                request_value = RenderRequest(
                    ABI_VERSION,
                    ctypes.sizeof(RenderRequest),
                    scene_time_us,
                    scene_time_us,
                    frame_index,
                    output.output_pointer(),
                    output.size,
                    0,
                )
                result_guard = GuardedBuffer(
                    ctypes.sizeof(RenderResult), alignment=ctypes.alignment(RenderResult)
                )
                result_pointer = ctypes.cast(
                    result_guard.pointer, ctypes.POINTER(RenderResult)
                )
                result = result_pointer.contents
                result.struct_size = ctypes.sizeof(RenderResult)
                result.status = -2147483647
                result.changed = 0xFF
                result.reserved_zero[:] = (ctypes.c_uint8 * 7)()
                result.next_deadline_scene_time_us = 0
                request_before = _fingerprint(request_value)
                parameters_before = _fingerprint(parameter_array)
                vibe_before = _fingerprint(vibe)
                modifiers_before = _fingerprint(modifiers)
                profile_before = _fingerprint(profiles[device])
                started_ns = time.perf_counter_ns()
                callback_status = api.render(
                    state.pointer, ctypes.byref(request_value), result_pointer
                )
                render_ms.append((time.perf_counter_ns() - started_ns) / 1_000_000.0)
                output.verify("render output")
                result_guard.verify("render result")
                state.verify("render state")
                _require_unchanged(request_before, request_value, "render request")
                _require_unchanged(parameters_before, parameter_array, "parameter input")
                _require_unchanged(vibe_before, vibe, "vibe input")
                _require_unchanged(modifiers_before, modifiers, "modifier input")
                _require_unchanged(profile_before, profiles[device], "profile input")
                _require_unchanged(helpers_before, helpers, "helper table")
                if callback_status != 0 or result.status != 0:
                    raise RuntimeError(
                        f"host render failed at frame {frame_index}, offset {RECEIVER_VIEWS[device][1]}"
                    )
                if result.changed not in {0, 1}:
                    raise RuntimeError("host render returned invalid changed state")
                if result.struct_size != ctypes.sizeof(RenderResult) or any(result.reserved_zero):
                    raise RuntimeError("host render corrupted result metadata/reserved bytes")
                if result.next_deadline_scene_time_us <= scene_time_us:
                    raise RuntimeError("host render returned a stale/nonfuture deadline")
                if result.next_deadline_scene_time_us > scene_time_us + cadence_period_us:
                    raise RuntimeError("host render deadline exceeds its fixed-FPS period")
                if result.next_deadline_scene_time_us < previous_deadlines[device]:
                    raise RuntimeError("host render deadlines move backwards")
                previous_deadlines[device] = result.next_deadline_scene_time_us
                if result.changed:
                    wall_changed = True
                if render_ms[-1] > render_budget_ms:
                    missed_deadlines += 1
                if result.changed:
                    local_frame = output.bytes()
                    previous_local_frames[device] = local_frame
                else:
                    local_frame = previous_local_frames[device]
                    if local_frame is None:
                        raise RuntimeError(
                            "host render returned unchanged before a complete first frame"
                        )
                _lane, offset, reverse = RECEIVER_VIEWS[device]
                strip_bytes = LEDS_PER_STRIP * 3
                for local_strip in range(LOCAL_STRIPS):
                    global_strip = offset + (
                        LOCAL_STRIPS - 1 - local_strip if reverse else local_strip
                    )
                    source_start = local_strip * strip_bytes
                    destination_start = global_strip * strip_bytes
                    wall_frame[destination_start : destination_start + strip_bytes] = (
                        local_frame[source_start : source_start + strip_bytes]
                    )
            if wall_changed:
                changed_frames += 1
            frames.append(bytes(wall_frame))
    finally:
        for state in states[:initialized]:
            if api.cleanup(state.pointer) != 0:
                raise RuntimeError("host cleanup failed")
            _require_unchanged(helpers_before, helpers, "helper table")
            state.verify("cleanup")
    frame_size = GLOBAL_STRIPS * LEDS_PER_STRIP * 3
    return (
        {
            "changed_frames": changed_frames,
            "frame_count": frame_count,
            "frame_size": frame_size,
            "missed_deadlines": missed_deadlines,
            "render_ms": render_ms,
        },
        b"".join(frames),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--frames", type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    result, frames = run(request)
    args.frames.write_bytes(frames)
    args.result.write_text(
        json.dumps(result, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
