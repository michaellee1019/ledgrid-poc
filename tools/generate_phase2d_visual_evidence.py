#!/usr/bin/env python3
"""Generate deterministic Phase 2D semantic-vibe contact-sheet evidence."""

from __future__ import annotations

import argparse
import binascii
import struct
import sys
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from animation.core.base import RenderedFrame  # noqa: E402
from animation.core.feature_flags import AnimationPipelineFeatureFlags  # noqa: E402
from animation.core.manager import AnimationManager, PreviewLEDController  # noqa: E402
from animation.core.plugin_loader import AnimationPluginLoader  # noqa: E402
from animation.core.presentation_contracts import (  # noqa: E402
    CANONICAL_VIBE_IDS,
    AnimationRuntimeContext,
    ResolvedVibe,
    resolve_vibe,
)
from animation.core.receiver_presentation import (  # noqa: E402
    Q8_8_ONE,
    quantize_q8_8,
)
from animation.core.receiver_static_component import (  # noqa: E402
    receiver_static_component_descriptor,
    render_compiled_rainbow_preview,
)

WALL_WIDTH = 32
WALL_HEIGHT = 138
PIXEL_COUNT = WALL_WIDTH * WALL_HEIGHT
TILE_SCALE = 3
TILE_WIDTH = WALL_WIDTH * TILE_SCALE
TILE_HEIGHT = WALL_HEIGHT * TILE_SCALE
LEFT_LABEL_WIDTH = 214
SHEET_MARGIN = 12
COLUMN_GAP = 10
HEADER_HEIGHT = 72
COLUMN_HEADER_HEIGHT = 24
ROW_GAP = 24

FIXED_TIMEZONE = timezone(timedelta(hours=-4), "EDT")
FIXED_CLOCK = datetime(2026, 8, 21, 16, 24, 36, tzinfo=FIXED_TIMEZONE)
FIXED_WALL_TIME = FIXED_CLOCK.timestamp()
FIXED_SCENE_EPOCH = 0x20260821
FIXED_RECEIVER_SCENE_TIME_US = 654_321
FIXED_RECEIVER_COMMON_SEED = 0x1234_5678
CAPTURE_FPS = 12
CAPTURE_SECONDS = 4
CAPTURE_TIMES = tuple(
    frame_index / CAPTURE_FPS
    for frame_index in range(CAPTURE_FPS * CAPTURE_SECONDS + 1)
)

SHEET_TITLE = "PHASE 2D SEMANTIC VIBE EVIDENCE"
SHEET_SUBTITLE = "32X138 / FIXED SEEDS CLOCK AND CANONICAL RUNTIME CONTEXTS"
HYBRID_DISCLOSURE = "HOST-SIMULATION / NOT RECEIVER FRAMEBUFFER READBACK"

BACKGROUND = (8, 10, 16)
PANEL = (19, 23, 34)
BORDER = (68, 76, 96)
PRIMARY_TEXT = (238, 242, 250)
SECONDARY_TEXT = (158, 171, 194)
DISCLOSURE_TEXT = (255, 190, 78)

HYBRID_FLAGS = AnimationPipelineFeatureFlags(
    receiver_local_background=True,
    receiver_sparse_overlay=True,
)


@dataclass(frozen=True)
class RowSpec:
    row_id: str
    family_label: str
    component_label: str
    plugin_id: str | None
    config: Mapping[str, object]
    hybrid: bool = False


@dataclass(frozen=True)
class TileEvidence:
    row_id: str
    vibe_id: str
    physical_rgb: np.ndarray
    source_label: str
    framebuffer_readback: bool

    @property
    def digest(self) -> str:
        return sha256(self.physical_rgb.tobytes()).hexdigest()


@dataclass(frozen=True)
class ContactSheetEvidence:
    pixels: np.ndarray
    tiles: tuple[TileEvidence, ...]


ROW_SPECS = (
    RowSpec(
        "atmosphere",
        "ATMOSPHERE",
        "TIDAL BIOLUMINESCENCE",
        "tidal_bioluminescence",
        {"seed": 4501, "density": 0.82, "brightness": 0.72},
    ),
    RowSpec(
        "living",
        "LIVING",
        "CYCLIC REEF",
        "cyclic_reef",
        {
            "seed": 13101,
            "density": 1.0,
            "brightness": 0.68,
            "state_count": 5,
            "threshold": 2,
            "mutation": 0.002,
            "grazer_density": 0.5,
            "edge_glow": 0.65,
        },
    ),
    RowSpec(
        "math",
        "MATH",
        "QUASICRYSTAL BLOOM",
        "quasicrystal_bloom",
        {
            "seed": 2701,
            "brightness": 0.66,
            "symmetry": 10,
            "spatial_scale": 2.8,
            "warp": 0.34,
        },
    ),
    RowSpec(
        "receiver_hybrid",
        "RECEIVER HYBRID",
        "COMPILED RAINBOW + CLOCK OVERLAY",
        None,
        {
            "preferred_cadence_hz": 30,
            "common_seed": FIXED_RECEIVER_COMMON_SEED,
        },
        hybrid=True,
    ),
)

SHEET_WIDTH = (
    LEFT_LABEL_WIDTH
    + len(CANONICAL_VIBE_IDS) * TILE_WIDTH
    + (len(CANONICAL_VIBE_IDS) - 1) * COLUMN_GAP
    + SHEET_MARGIN
)
SHEET_HEIGHT = (
    HEADER_HEIGHT
    + COLUMN_HEADER_HEIGHT
    + len(ROW_SPECS) * TILE_HEIGHT
    + (len(ROW_SPECS) - 1) * ROW_GAP
    + SHEET_MARGIN
)


_FONT_5X7 = {
    " ": "00000/00000/00000/00000/00000/00000/00000",
    "-": "00000/00000/00000/11111/00000/00000/00000",
    "+": "00000/00100/00100/11111/00100/00100/00000",
    "/": "00001/00010/00100/01000/10000/00000/00000",
    ":": "00000/00100/00100/00000/00100/00100/00000",
    "?": "01110/10001/00001/00010/00100/00000/00100",
    "0": "01110/10001/10011/10101/11001/10001/01110",
    "1": "00100/01100/00100/00100/00100/00100/01110",
    "2": "01110/10001/00001/00010/00100/01000/11111",
    "3": "11110/00001/00001/01110/00001/00001/11110",
    "4": "00010/00110/01010/10010/11111/00010/00010",
    "5": "11111/10000/10000/11110/00001/00001/11110",
    "6": "01110/10000/10000/11110/10001/10001/01110",
    "7": "11111/00001/00010/00100/01000/01000/01000",
    "8": "01110/10001/10001/01110/10001/10001/01110",
    "9": "01110/10001/10001/01111/00001/00001/01110",
    "A": "01110/10001/10001/11111/10001/10001/10001",
    "B": "11110/10001/10001/11110/10001/10001/11110",
    "C": "01110/10001/10000/10000/10000/10001/01110",
    "D": "11110/10001/10001/10001/10001/10001/11110",
    "E": "11111/10000/10000/11110/10000/10000/11111",
    "F": "11111/10000/10000/11110/10000/10000/10000",
    "G": "01110/10001/10000/10111/10001/10001/01110",
    "H": "10001/10001/10001/11111/10001/10001/10001",
    "I": "01110/00100/00100/00100/00100/00100/01110",
    "J": "00111/00010/00010/00010/10010/10010/01100",
    "K": "10001/10010/10100/11000/10100/10010/10001",
    "L": "10000/10000/10000/10000/10000/10000/11111",
    "M": "10001/11011/10101/10101/10001/10001/10001",
    "N": "10001/11001/10101/10011/10001/10001/10001",
    "O": "01110/10001/10001/10001/10001/10001/01110",
    "P": "11110/10001/10001/11110/10000/10000/10000",
    "Q": "01110/10001/10001/10001/10101/10010/01101",
    "R": "11110/10001/10001/11110/10100/10010/10001",
    "S": "01111/10000/10000/01110/00001/00001/11110",
    "T": "11111/00100/00100/00100/00100/00100/00100",
    "U": "10001/10001/10001/10001/10001/10001/01110",
    "V": "10001/10001/10001/10001/10001/01010/00100",
    "W": "10001/10001/10001/10101/10101/10101/01010",
    "X": "10001/10001/01010/00100/01010/10001/10001",
    "Y": "10001/10001/01010/00100/00100/00100/00100",
    "Z": "11111/00001/00010/00100/01000/10000/11111",
}


def _glyph_rows(character: str) -> tuple[str, ...]:
    return tuple(_FONT_5X7.get(character, _FONT_5X7["?"]).split("/"))


def text_width(text: str, scale: int = 1) -> int:
    return max(0, len(text) * 6 * scale - scale)


def draw_text(
    canvas: np.ndarray,
    x: int,
    y: int,
    text: str,
    color: Sequence[int],
    *,
    scale: int = 1,
) -> None:
    """Draw deterministic 5x7 uppercase pixel text into an RGB canvas."""

    for character_index, character in enumerate(text.upper()):
        origin_x = x + character_index * 6 * scale
        for row_index, row in enumerate(_glyph_rows(character)):
            for column_index, active in enumerate(row):
                if active == "1":
                    top = y + row_index * scale
                    left = origin_x + column_index * scale
                    canvas[top : top + scale, left : left + scale] = color


def physical_rgb_to_visual(frame: np.ndarray) -> np.ndarray:
    """Convert strip-major, LED-zero-at-bottom bytes to visual top-left RGB."""

    array = np.asarray(frame)
    if array.dtype != np.uint8 or array.shape != (PIXEL_COUNT, 3):
        raise ValueError(
            f"physical RGB frame must be uint8 with shape ({PIXEL_COUNT}, 3)"
        )
    physical = array.reshape(WALL_WIDTH, WALL_HEIGHT, 3)
    return np.ascontiguousarray(physical[:, ::-1, :].transpose(1, 0, 2))


def build_runtime_context(
    animation,
    resolved: ResolvedVibe,
    *,
    elapsed: float,
    frame_index: int,
) -> AnimationRuntimeContext:
    """Build the exact host context subset consumed by one preview component."""

    authored_speed = float(animation.get_authored_parameter("speed", 1.0))
    capabilities = animation.VIBE_CAPABILITIES
    tempo_scale = (
        resolved.profile.tempo_scale if "tempo" in capabilities else 1.0
    )
    luminance_scale = (
        resolved.profile.luminance_scale if "luminance" in capabilities else 1.0
    )
    effective_time_scale = authored_speed * tempo_scale
    return AnimationRuntimeContext(
        wall_time=FIXED_WALL_TIME,
        unscaled_elapsed=elapsed,
        scaled_elapsed=elapsed * effective_time_scale,
        frame_index=frame_index,
        scene_epoch=FIXED_SCENE_EPOCH,
        global_width=WALL_WIDTH,
        height=WALL_HEIGHT,
        local_strip_offset=0,
        local_width=WALL_WIDTH,
        vibe_id=resolved.state.vibe_id,
        vibe_profile_version=resolved.state.profile_version,
        resolved_profile_digest=resolved.state.resolved_profile_digest,
        palette_roles=resolved.profile.palette_roles,
        capability_values=resolved.profile.capability_values,
        tempo_scale=tempo_scale,
        luminance_scale=luminance_scale,
        operator_tempo_scale=1.0,
        authored_speed=authored_speed,
        effective_time_scale=effective_time_scale,
        installation_profile_view={},
        plant_modifiers={},
    )


def _pixels(rendered) -> np.ndarray:
    return rendered.pixels if isinstance(rendered, RenderedFrame) else rendered


def render_python_frame(
    animation_class,
    controller: PreviewLEDController,
    config: Mapping[str, object],
    vibe_id: str,
) -> np.ndarray:
    """Render one migrated Python component with exact manager presentation."""

    animation = animation_class(controller, dict(config))
    resolved = resolve_vibe(vibe_id)
    rendered = None
    for frame_index, elapsed in enumerate(CAPTURE_TIMES):
        rendered = animation.generate_frame_with_context(
            build_runtime_context(
                animation,
                resolved,
                elapsed=elapsed,
                frame_index=frame_index,
            )
        )
    if rendered is None:  # pragma: no cover - CAPTURE_TIMES is a frozen non-empty tuple.
        raise AssertionError("capture sequence cannot be empty")
    source = np.asarray(_pixels(rendered), dtype=np.uint8)
    presented, _changed = AnimationManager._apply_vibe_presentation(
        animation,
        source,
        profile=resolved.profile,
        changed=True,
        state=AnimationManager._empty_presentation_state(),
        force_refresh=True,
    )
    return np.ascontiguousarray(presented).copy()


def apply_receiver_luminance_rgba(
    frame: np.ndarray, luminance_q8_8: int
) -> np.ndarray:
    """Apply receiver Q8.8 luminance once to premultiplied overlay RGB."""

    array = np.asarray(frame)
    if array.dtype != np.uint8 or array.shape != (PIXEL_COUNT, 4):
        raise ValueError(
            f"receiver overlay must be uint8 with shape ({PIXEL_COUNT}, 4)"
        )
    if not np.all(array[:, :3] <= array[:, 3:4]):
        raise ValueError("receiver overlay must be premultiplied RGBA8")
    if not isinstance(luminance_q8_8, int) or isinstance(luminance_q8_8, bool):
        raise TypeError("receiver luminance must be an integer")
    if not 0 <= luminance_q8_8 <= Q8_8_ONE:
        raise ValueError("receiver luminance must be between zero and Q8.8 unity")

    output = np.empty_like(array)
    working = array[:, :3].astype(np.uint16)
    working *= luminance_q8_8
    working += 128
    np.floor_divide(working, Q8_8_ONE, out=working)
    np.minimum(working, array[:, 3:4], out=working)
    np.copyto(output[:, :3], working, casting="unsafe")
    np.copyto(output[:, 3], array[:, 3])
    return output


def source_over_receiver_preview(base: np.ndarray, foreground: np.ndarray) -> np.ndarray:
    """Blend premultiplied foreground over receiver RGB with v1 integer math."""

    opaque = np.asarray(base)
    overlay = np.asarray(foreground)
    if opaque.dtype != np.uint8 or opaque.shape != (PIXEL_COUNT, 3):
        raise ValueError(f"receiver base must be uint8 with shape ({PIXEL_COUNT}, 3)")
    if overlay.dtype != np.uint8 or overlay.shape != (PIXEL_COUNT, 4):
        raise ValueError(
            f"receiver foreground must be uint8 with shape ({PIXEL_COUNT}, 4)"
        )
    if not np.all(overlay[:, :3] <= overlay[:, 3:4]):
        raise ValueError("receiver foreground must be premultiplied RGBA8")

    working = opaque.astype(np.uint16)
    inverse_alpha = 255 - overlay[:, 3].astype(np.uint16)
    working *= inverse_alpha[:, None]
    working += 127
    np.floor_divide(working, 255, out=working)
    working += overlay[:, :3].astype(np.uint16)
    np.minimum(working, 255, out=working)
    return working.astype(np.uint8)


class _EvidenceClockOverlayMixin:
    """Pin the physical-wall clock timezone while still consuming wall_time."""

    def _clock_now(self):
        context = self.presentation_context
        if context is None:
            return FIXED_CLOCK
        return self._apply_clock_offset(
            datetime.fromtimestamp(context.wall_time, tz=FIXED_TIMEZONE)
        )


def render_hybrid_frame(
    clock_overlay_class,
    controller: PreviewLEDController,
    parameters: Mapping[str, object],
    vibe_id: str,
    *,
    feature_flags: AnimationPipelineFeatureFlags = HYBRID_FLAGS,
) -> np.ndarray:
    """Render the feature-gated receiver rainbow plus Python clock overlay."""

    descriptor = receiver_static_component_descriptor(feature_flags)
    if descriptor is None:
        raise RuntimeError("receiver hybrid evidence requires both rollout gates")
    if descriptor["provider"] != "receiver_native" or descriptor["role"] != "background":
        raise RuntimeError("compiled rainbow descriptor contract drifted")

    resolved = resolve_vibe(vibe_id)
    luminance = quantize_q8_8(
        resolved.profile.luminance_scale,
        name="luminance_scale",
        maximum=Q8_8_ONE,
    )
    base = render_compiled_rainbow_preview(
        FIXED_RECEIVER_SCENE_TIME_US,
        parameters,
        strip_count=WALL_WIDTH,
        leds_per_strip=WALL_HEIGHT,
        global_strip_offset=0,
        luminance_q8_8=luminance,
    )

    fixed_clock_class = type(
        "EvidenceClockOverlay",
        (_EvidenceClockOverlayMixin, clock_overlay_class),
        {},
    )
    overlay = fixed_clock_class(
        controller,
        {
            "face": "digital",
            "palette": "amber",
            "format_24h": True,
            "show_seconds": True,
            "position_y": 0.5,
            "scale": 1,
            "glow": 0.7,
            "brightness": 1.0,
            "opacity": 1.0,
            "backdrop_opacity": 0.18,
            "backdrop_padding": 1,
        },
    )
    overlay_frame = overlay.generate_frame_with_context(
        build_runtime_context(overlay, resolved, elapsed=0.667, frame_index=133)
    )
    foreground = apply_receiver_luminance_rgba(overlay_frame.pixels, luminance)
    return source_over_receiver_preview(base, foreground)


def render_tiles(
    *,
    feature_flags: AnimationPipelineFeatureFlags = HYBRID_FLAGS,
) -> tuple[TileEvidence, ...]:
    """Render all rows/vibes and fail closed if any evidence tile duplicates."""

    if receiver_static_component_descriptor(feature_flags) is None:
        raise RuntimeError("receiver hybrid evidence requires both rollout gates")
    plugin_ids = tuple(
        spec.plugin_id for spec in ROW_SPECS if spec.plugin_id is not None
    ) + ("clock_overlay",)
    loader = AnimationPluginLoader(allowed_plugins=plugin_ids)
    plugins = loader.load_all_plugins()
    missing = sorted(set(plugin_ids).difference(plugins))
    if missing:
        raise RuntimeError("missing evidence plugins: " + ", ".join(missing))
    overlay_manifest = loader.plugin_manifests["clock_overlay"]
    if overlay_manifest["provider"] != "python" or overlay_manifest["role"] != "overlay":
        raise RuntimeError("clock overlay descriptor contract drifted")

    controller = PreviewLEDController(WALL_WIDTH, WALL_HEIGHT)
    tiles = []
    fingerprints: dict[str, tuple[str, str]] = {}
    for spec in ROW_SPECS:
        for vibe_id in CANONICAL_VIBE_IDS:
            if spec.hybrid:
                frame = render_hybrid_frame(
                    plugins["clock_overlay"],
                    controller,
                    spec.config,
                    vibe_id,
                    feature_flags=feature_flags,
                )
                source_label = HYBRID_DISCLOSURE
            else:
                assert spec.plugin_id is not None
                frame = render_python_frame(
                    plugins[spec.plugin_id], controller, spec.config, vibe_id
                )
                source_label = "HOST PYTHON RENDER"
            tile = TileEvidence(
                row_id=spec.row_id,
                vibe_id=vibe_id,
                physical_rgb=frame,
                source_label=source_label,
                framebuffer_readback=False,
            )
            duplicate = fingerprints.get(tile.digest)
            if duplicate is not None:
                raise RuntimeError(
                    f"evidence tile {spec.row_id}/{vibe_id} duplicates "
                    f"{duplicate[0]}/{duplicate[1]}"
                )
            fingerprints[tile.digest] = (spec.row_id, vibe_id)
            tiles.append(tile)
    return tuple(tiles)


def _draw_sheet_labels(canvas: np.ndarray) -> None:
    draw_text(canvas, SHEET_MARGIN, 10, SHEET_TITLE, PRIMARY_TEXT, scale=2)
    draw_text(canvas, SHEET_MARGIN, 36, SHEET_SUBTITLE, SECONDARY_TEXT)
    draw_text(canvas, SHEET_MARGIN, 51, HYBRID_DISCLOSURE, DISCLOSURE_TEXT)

    for column, vibe_id in enumerate(CANONICAL_VIBE_IDS):
        x = LEFT_LABEL_WIDTH + column * (TILE_WIDTH + COLUMN_GAP)
        label_x = x + max(0, (TILE_WIDTH - text_width(vibe_id)) // 2)
        draw_text(
            canvas,
            label_x,
            HEADER_HEIGHT + 6,
            vibe_id,
            PRIMARY_TEXT,
        )

    for row, spec in enumerate(ROW_SPECS):
        y = HEADER_HEIGHT + COLUMN_HEADER_HEIGHT + row * (TILE_HEIGHT + ROW_GAP)
        label_y = y + TILE_HEIGHT // 2 - 42
        draw_text(canvas, SHEET_MARGIN, label_y, spec.family_label, PRIMARY_TEXT, scale=2)
        draw_text(
            canvas,
            SHEET_MARGIN,
            label_y + 24,
            spec.component_label,
            SECONDARY_TEXT,
        )
        if spec.hybrid:
            draw_text(
                canvas,
                SHEET_MARGIN,
                label_y + 41,
                "FEATURE GATED",
                DISCLOSURE_TEXT,
            )
            draw_text(
                canvas,
                SHEET_MARGIN,
                label_y + 54,
                "HOST-SIMULATION",
                DISCLOSURE_TEXT,
            )
            draw_text(
                canvas,
                SHEET_MARGIN,
                label_y + 67,
                "NOT FRAMEBUFFER READBACK",
                DISCLOSURE_TEXT,
            )


def build_contact_sheet(
    *,
    feature_flags: AnimationPipelineFeatureFlags = HYBRID_FLAGS,
) -> ContactSheetEvidence:
    """Build deterministic RGB contact-sheet pixels and source evidence."""

    tiles = render_tiles(feature_flags=feature_flags)
    canvas = np.full((SHEET_HEIGHT, SHEET_WIDTH, 3), BACKGROUND, dtype=np.uint8)
    canvas[:, :LEFT_LABEL_WIDTH] = PANEL
    _draw_sheet_labels(canvas)

    for index, tile in enumerate(tiles):
        row = index // len(CANONICAL_VIBE_IDS)
        column = index % len(CANONICAL_VIBE_IDS)
        x = LEFT_LABEL_WIDTH + column * (TILE_WIDTH + COLUMN_GAP)
        y = HEADER_HEIGHT + COLUMN_HEADER_HEIGHT + row * (TILE_HEIGHT + ROW_GAP)
        visual = physical_rgb_to_visual(tile.physical_rgb)
        scaled = np.repeat(
            np.repeat(visual, TILE_SCALE, axis=0), TILE_SCALE, axis=1
        )
        canvas[y : y + TILE_HEIGHT, x : x + TILE_WIDTH] = scaled
        if x > 0:
            canvas[y : y + TILE_HEIGHT, x - 1] = BORDER
        canvas[y : y + TILE_HEIGHT, x + TILE_WIDTH] = BORDER
        if y > 0:
            canvas[y - 1, x : x + TILE_WIDTH] = BORDER
        canvas[y + TILE_HEIGHT, x : x + TILE_WIDTH] = BORDER
    return ContactSheetEvidence(pixels=canvas, tiles=tiles)


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", binascii.crc32(body))


def encode_rgb_png(pixels: np.ndarray) -> bytes:
    """Encode RGB8 with fixed scanline filters and no variable PNG metadata."""

    array = np.asarray(pixels)
    if array.dtype != np.uint8 or array.ndim != 3 or array.shape[2] != 3:
        raise ValueError("PNG source must be an HxWx3 uint8 array")
    height, width, _channels = array.shape
    raw = b"".join(
        b"\x00" + np.ascontiguousarray(row).tobytes() for row in array
    )
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(raw, level=9))
        + _png_chunk(b"IEND", b"")
    )


def write_contact_sheet(
    output: Path,
    *,
    feature_flags: AnimationPipelineFeatureFlags = HYBRID_FLAGS,
) -> tuple[ContactSheetEvidence, str]:
    evidence = build_contact_sheet(feature_flags=feature_flags)
    encoded = encode_rgb_png(evidence.pixels)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(encoded)
    return evidence, sha256(encoded).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "docs" / "phase2d-semantic-vibe-contact-sheet.png",
    )
    args = parser.parse_args()
    evidence, digest = write_contact_sheet(args.output)
    print(
        f"wrote {args.output} ({evidence.pixels.shape[1]}x{evidence.pixels.shape[0]}, "
        f"{len(evidence.tiles)} distinct tiles, sha256={digest})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
