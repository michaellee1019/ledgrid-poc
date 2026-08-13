"""Canonical Phase 3A receiver presentation-context wire contract.

The Pi resolves presentation state.  Receivers consume these exact bytes; they
never look up a vibe profile or plant geometry locally.  Packet serializers
return the command bytes before the transport's trailing CRC-16/CCITT-FALSE.
"""

from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping

from animation.core.plant_awareness import PLANT_MODIFIER_IDS, PlantModifierState
from animation.core.presentation_contracts import (
    CANONICAL_VIBE_IDS,
    VIBE_PALETTE_ROLES,
    ResolvedVibe,
)


PRESENTATION_CONTEXT_VERSION: Final = 1
PRESENTATION_CONTEXT_BEGIN: Final = 0x21
PRESENTATION_CONTEXT_SET: Final = 0x22
PRESENTATION_CONTEXT_COMMIT: Final = 0x23

SESSION_ID_BYTES: Final = 16
DIGEST_BYTES: Final = 32
Q8_8_ONE: Final = 256
Q8_8_MAX: Final = 0xFFFF

BEGIN_BYTES: Final = 58
SET_BASE_BYTES: Final = 145
SET_ENTRY_BYTES: Final = 3
SET_MAX_BYTES: Final = SET_BASE_BYTES + SET_ENTRY_BYTES * len(PLANT_MODIFIER_IDS)
COMMIT_BYTES: Final = 74

VIBE_ID_TO_WIRE: Final[Mapping[str, int]] = MappingProxyType({
    vibe_id: index for index, vibe_id in enumerate(CANONICAL_VIBE_IDS, start=1)
})
PLANT_MODIFIER_ID_TO_WIRE: Final[Mapping[str, int]] = MappingProxyType({
    modifier_id: index
    for index, modifier_id in enumerate(PLANT_MODIFIER_IDS, start=1)
})

_EXPECTED_CAPABILITY_KEYS: Final = frozenset(("chroma_scale", "energy"))


def _uint64(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an unsigned 64-bit integer")
    if not 0 <= value <= 0xFFFF_FFFF_FFFF_FFFF:
        raise ValueError(f"{name} must fit in an unsigned 64-bit integer")
    return value


def _session_id(value: bytes) -> bytes:
    if not isinstance(value, bytes):
        raise TypeError("controller_session_id must be bytes")
    if len(value) != SESSION_ID_BYTES:
        raise ValueError(f"controller_session_id must contain {SESSION_ID_BYTES} bytes")
    return value


def _digest_bytes(name: str, value: str) -> bytes:
    if not isinstance(value, str) or len(value) != DIGEST_BYTES * 2:
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    try:
        encoded = bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest") from exc
    if value != value.lower() or encoded.hex() != value:
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    return encoded


def quantize_q8_8(value: float, *, name: str = "value", maximum: int = Q8_8_MAX) -> int:
    """Round a finite non-negative scalar to unsigned Q8.8, half upward."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite non-negative number")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0.0:
        raise ValueError(f"{name} must be a finite non-negative number")
    quantized = math.floor(numeric * Q8_8_ONE + 0.5)
    if quantized > maximum:
        raise ValueError(f"{name} overflows unsigned Q8.8")
    return quantized


def apply_luminance_u8(channel: int, luminance_q8_8: int) -> int:
    """Apply the receiver's one and only luminance scale to one RGB byte."""

    if isinstance(channel, bool) or not isinstance(channel, int) or not 0 <= channel <= 255:
        raise ValueError("channel must be an unsigned byte")
    if (
        isinstance(luminance_q8_8, bool)
        or not isinstance(luminance_q8_8, int)
        or not 0 <= luminance_q8_8 <= Q8_8_ONE
    ):
        raise ValueError("luminance_q8_8 must be between zero and Q8.8 unity")
    return min(255, (channel * luminance_q8_8 + 128) // Q8_8_ONE)


def _canonical_plant_state(state: PlantModifierState) -> PlantModifierState:
    if not isinstance(state, PlantModifierState):
        raise TypeError("plant_modifiers must be a PlantModifierState")
    payload = {
        "version": state.version,
        "active": list(state.active),
        "strengths": dict(state.strengths),
    }
    normalized = PlantModifierState.from_payload(payload)
    if set(state.strengths) != set(state.active):
        raise ValueError("plant modifier strengths must exactly match active IDs")
    if (
        tuple(state.active) != normalized.active
        or dict(state.strengths) != dict(normalized.strengths)
    ):
        raise ValueError("plant modifier state must use canonical ID order and resolved strengths")
    return PlantModifierState(
        version=normalized.version,
        active=normalized.active,
        strengths=MappingProxyType(dict(normalized.strengths)),
    )


def _plant_body(state: PlantModifierState) -> bytes:
    body = bytearray((state.version, len(state.active)))
    for modifier_id in state.active:
        body.append(PLANT_MODIFIER_ID_TO_WIRE[modifier_id])
        body.extend(struct.pack(">H", quantize_q8_8(
            state.strengths[modifier_id], name=f"strengths[{modifier_id}]", maximum=Q8_8_ONE
        )))
    return bytes(body)


def plant_modifier_digest(state: PlantModifierState) -> bytes:
    """Digest the exact canonical modifier bytes (never installation geometry)."""

    return hashlib.sha256(_plant_body(_canonical_plant_state(state))).digest()


@dataclass(frozen=True)
class ReceiverPresentationContext:
    """Complete host-resolved context staged before receiver activation."""

    controller_session_id: bytes
    scene_revision: int
    scene_epoch: int
    present_at_scene_time_us: int
    vibe: ResolvedVibe
    plant_modifiers: PlantModifierState
    plant_revision: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "controller_session_id", _session_id(self.controller_session_id))
        _uint64("scene_revision", self.scene_revision)
        _uint64("scene_epoch", self.scene_epoch)
        _uint64("present_at_scene_time_us", self.present_at_scene_time_us)
        _uint64("plant_revision", self.plant_revision)
        if not isinstance(self.vibe, ResolvedVibe):
            raise TypeError("vibe must be a ResolvedVibe")
        profile = self.vibe.profile
        state = self.vibe.state
        if state.vibe_id not in VIBE_ID_TO_WIRE:
            raise ValueError(f"unknown canonical vibe ID {state.vibe_id!r}")
        if profile.profile_version > 0xFFFF_FFFF:
            raise ValueError("vibe profile version must fit in an unsigned 32-bit integer")
        if tuple(profile.palette_roles) != VIBE_PALETTE_ROLES:
            raise ValueError("vibe palette roles must use the complete canonical role order")
        if set(profile.capability_values) != _EXPECTED_CAPABILITY_KEYS:
            raise ValueError("vibe capability values must contain exactly chroma_scale and energy")
        _digest_bytes("resolved_profile_digest", state.resolved_profile_digest)
        quantize_q8_8(profile.tempo_scale, name="tempo_scale")
        quantize_q8_8(
            profile.luminance_scale, name="luminance_scale", maximum=Q8_8_ONE
        )
        quantize_q8_8(profile.capability_values["chroma_scale"], name="chroma_scale")
        quantize_q8_8(profile.capability_values["energy"], name="energy")
        object.__setattr__(
            self, "plant_modifiers", _canonical_plant_state(self.plant_modifiers)
        )

    @property
    def plant_digest(self) -> bytes:
        return plant_modifier_digest(self.plant_modifiers)

    @property
    def context_digest(self) -> bytes:
        return hashlib.sha256(_context_body(self)).digest()


def _context_body(context: ReceiverPresentationContext) -> bytes:
    profile = context.vibe.profile
    state = context.vibe.state
    body = bytearray(struct.pack(
        ">QBIQ",
        context.scene_revision,
        VIBE_ID_TO_WIRE[state.vibe_id],
        profile.profile_version,
        state.revision,
    ))
    body.extend(_digest_bytes("resolved_profile_digest", state.resolved_profile_digest))
    for role in VIBE_PALETTE_ROLES:
        body.extend(profile.palette_roles[role])
    body.extend(struct.pack(
        ">HHHHBQ",
        quantize_q8_8(profile.tempo_scale, name="tempo_scale"),
        quantize_q8_8(profile.luminance_scale, name="luminance_scale", maximum=Q8_8_ONE),
        quantize_q8_8(profile.capability_values["chroma_scale"], name="chroma_scale"),
        quantize_q8_8(profile.capability_values["energy"], name="energy"),
        context.plant_modifiers.version,
        context.plant_revision,
    ))
    body.extend(context.plant_digest)
    plant = _plant_body(context.plant_modifiers)
    body.append(plant[1])
    body.extend(plant[2:])
    return bytes(body)


def serialize_presentation_context_begin(context: ReceiverPresentationContext) -> bytes:
    packet = struct.pack(
        ">BB16sQ32s",
        PRESENTATION_CONTEXT_BEGIN,
        PRESENTATION_CONTEXT_VERSION,
        context.controller_session_id,
        context.scene_revision,
        context.context_digest,
    )
    if len(packet) != BEGIN_BYTES:  # contract assertion, not input validation
        raise AssertionError("presentation-context BEGIN layout drifted")
    return packet


def serialize_presentation_context_set(context: ReceiverPresentationContext) -> bytes:
    packet = struct.pack(
        ">BB16s", PRESENTATION_CONTEXT_SET, PRESENTATION_CONTEXT_VERSION,
        context.controller_session_id,
    ) + _context_body(context)
    expected = SET_BASE_BYTES + SET_ENTRY_BYTES * len(context.plant_modifiers.active)
    if len(packet) != expected:  # contract assertion, not input validation
        raise AssertionError("presentation-context SET layout drifted")
    return packet


def serialize_presentation_context_commit(context: ReceiverPresentationContext) -> bytes:
    packet = struct.pack(
        ">BB16sQQQ32s",
        PRESENTATION_CONTEXT_COMMIT,
        PRESENTATION_CONTEXT_VERSION,
        context.controller_session_id,
        context.scene_revision,
        context.scene_epoch,
        context.present_at_scene_time_us,
        context.context_digest,
    )
    if len(packet) != COMMIT_BYTES:  # contract assertion, not input validation
        raise AssertionError("presentation-context COMMIT layout drifted")
    return packet


def serialize_presentation_context(
    context: ReceiverPresentationContext,
) -> tuple[bytes, bytes, bytes]:
    """Return BEGIN, SET, COMMIT command bytes for one atomic stage."""

    return (
        serialize_presentation_context_begin(context),
        serialize_presentation_context_set(context),
        serialize_presentation_context_commit(context),
    )


# ``encode_*`` is the concise driver-facing spelling.  Keep ``serialize_*``
# explicit for contract and fixture code.
encode_presentation_context_begin = serialize_presentation_context_begin
encode_presentation_context_set = serialize_presentation_context_set
encode_presentation_context_commit = serialize_presentation_context_commit
