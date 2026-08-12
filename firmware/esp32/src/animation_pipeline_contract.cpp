#include "ledgrid/animation_pipeline_contract.hpp"

#include <climits>

namespace ledgrid {
namespace {

constexpr std::uint16_t kCrc16NibbleTable[16] = {
    0x0000, 0x1021, 0x2042, 0x3063, 0x4084, 0x50A5, 0x60C6, 0x70E7,
    0x8108, 0x9129, 0xA14A, 0xB16B, 0xC18C, 0xD1AD, 0xE1CE, 0xF1EF,
};

void write_u16(std::uint8_t* output, std::uint16_t value) {
  output[0] = static_cast<std::uint8_t>(value >> 8U);
  output[1] = static_cast<std::uint8_t>(value);
}

void write_u64(std::uint8_t* output, std::uint64_t value) {
  for (std::size_t index = 0; index < 8U; ++index) {
    output[index] = static_cast<std::uint8_t>(value >> (56U - index * 8U));
  }
}

std::uint8_t saturating_add_u8(std::uint8_t first, std::uint8_t second) {
  const std::uint16_t sum = static_cast<std::uint16_t>(first) + second;
  return static_cast<std::uint8_t>(sum > UINT8_MAX ? UINT8_MAX : sum);
}

bool digest_equal(const Digest256& first, const Digest256& second) {
  std::uint8_t difference = 0;
  for (std::size_t index = 0; index < kSnapshotDigestBytes; ++index) {
    difference |= static_cast<std::uint8_t>(
        first.bytes[index] ^ second.bytes[index]);
  }
  return difference == 0;
}

}  // namespace

CounterRelation compare_monotonic_counter(
    std::uint64_t candidate,
    std::uint64_t current) {
  if (candidate < current) return CounterRelation::Stale;
  if (candidate == current) return CounterRelation::Equal;
  return CounterRelation::Newer;
}

OverlayOperationResult validate_overlay_version_format(
    std::uint8_t version,
    OverlayFormat format) {
  if (version != kAnimationPipelineProtocolVersion) {
    return OverlayOperationResult::UnsupportedVersion;
  }
  if (format != OverlayFormat::PremultipliedRgba8) {
    return OverlayOperationResult::UnsupportedFormat;
  }
  return OverlayOperationResult::Ok;
}

OverlayOperationResult validate_overlay_session_revision(
    bool controller_session_matches,
    std::uint64_t candidate_scene_revision,
    std::uint64_t current_scene_revision) {
  if (!controller_session_matches) {
    return OverlayOperationResult::StaleSession;
  }
  if (candidate_scene_revision < current_scene_revision) {
    return OverlayOperationResult::StaleRevision;
  }
  return OverlayOperationResult::Ok;
}

OverlayOperationResult validate_overlay_generation_begin(
    const OverlayGenerationOrderState& state,
    std::uint64_t generation,
    std::uint64_t prior_generation,
    const Digest256& operation_digest) {
  if (state.has_staged_generation) {
    if (generation == state.staged_generation) {
      return digest_equal(operation_digest, state.staged_operation_digest)
                 ? OverlayOperationResult::Idempotent
                 : OverlayOperationResult::GenerationConflict;
    }
    if (generation < state.staged_generation) {
      return OverlayOperationResult::StaleGeneration;
    }
    // A staged generation must be explicitly aborted before a different one
    // can begin; this prevents an implicit overwrite of partially staged data.
    return OverlayOperationResult::InvalidState;
  }

  if (state.committed_generation == UINT64_MAX) {
    return OverlayOperationResult::CounterExhausted;
  }

  if (generation <= state.committed_generation || generation <= prior_generation) {
    return OverlayOperationResult::StaleGeneration;
  }
  if (prior_generation != state.committed_generation) {
    return OverlayOperationResult::PriorGenerationMismatch;
  }
  return OverlayOperationResult::Ok;
}

OverlayOperationResult accept_overlay_patch(
    OverlayPatchOrderState* state,
    std::uint16_t start,
    std::uint16_t count,
    const Digest256& content_digest) {
  if (state == nullptr || state->expected_patches == 0) {
    return OverlayOperationResult::InvalidState;
  }
  if (count == 0 || count > kMaxRgbaPixelsPerPatch) {
    return OverlayOperationResult::InvalidSize;
  }
  const std::uint32_t end = static_cast<std::uint32_t>(start) + count;
  if (end > kContractLocalPixels) return OverlayOperationResult::OutOfBounds;

  if (state->has_last_patch && start == state->last_start &&
      count == state->last_count) {
    return digest_equal(content_digest, state->last_content_digest)
               ? OverlayOperationResult::Idempotent
               : OverlayOperationResult::PatchConflict;
  }
  if (state->accepted_patches >= state->expected_patches) {
    return OverlayOperationResult::InvalidState;
  }

  if (state->has_last_patch) {
    const std::uint32_t last_end =
        static_cast<std::uint32_t>(state->last_start) + state->last_count;
    if (start < state->last_start) return OverlayOperationResult::PatchOrder;
    if (start < last_end) return OverlayOperationResult::PatchOverlap;
    if (state->update_kind == OverlayUpdateKind::FullSnapshot &&
        start != last_end) {
      return OverlayOperationResult::PatchOrder;
    }
  } else if (state->update_kind == OverlayUpdateKind::FullSnapshot && start != 0) {
    return OverlayOperationResult::PatchOrder;
  }

  state->last_start = start;
  state->last_count = count;
  state->last_content_digest = content_digest;
  state->has_last_patch = true;
  ++state->accepted_patches;
  return OverlayOperationResult::Ok;
}

OverlayOperationResult validate_overlay_commit(
    const OverlayPatchOrderState& state,
    bool base_binding_matches,
    bool lease_expired) {
  if (!base_binding_matches) {
    return OverlayOperationResult::BaseBindingMismatch;
  }
  if (lease_expired) return OverlayOperationResult::LeaseExpired;
  if (state.expected_patches == 0 ||
      state.accepted_patches != state.expected_patches ||
      !state.has_last_patch) {
    return OverlayOperationResult::Incomplete;
  }
  if (state.update_kind == OverlayUpdateKind::FullSnapshot &&
      static_cast<std::uint32_t>(state.last_start) + state.last_count !=
          kContractLocalPixels) {
    return OverlayOperationResult::Incomplete;
  }
  return OverlayOperationResult::Ok;
}

bool encode_overlay_patch_header(
    const OverlayPatchHeader& header,
    std::uint8_t* output,
    std::size_t output_size) {
  if (output == nullptr || output_size < kOverlayPatchHeaderBytes ||
      header.count == 0 || header.count > kMaxRgbaPixelsPerPatch ||
      static_cast<std::uint32_t>(header.start) + header.count >
          kContractLocalPixels) {
    return false;
  }
  output[0] = static_cast<std::uint8_t>(AnimationPipelineCommand::OverlayPatch);
  output[1] = kAnimationPipelineProtocolVersion;
  for (std::size_t index = 0; index < kControllerSessionBytes; ++index) {
    output[2U + index] = header.controller_session[index];
  }
  write_u64(output + 18U, header.generation);
  write_u16(output + 26U, header.start);
  write_u16(output + 28U, header.count);
  return true;
}

std::uint16_t animation_pipeline_crc16_ccitt(
    const std::uint8_t* data,
    std::size_t size) {
  if (data == nullptr && size != 0) return 0;
  std::uint16_t crc = 0xFFFF;
  for (std::size_t index = 0; index < size; ++index) {
    crc ^= static_cast<std::uint16_t>(data[index]) << 8U;
    crc = static_cast<std::uint16_t>(
        (crc << 4U) ^ kCrc16NibbleTable[crc >> 12U]);
    crc = static_cast<std::uint16_t>(
        (crc << 4U) ^ kCrc16NibbleTable[crc >> 12U]);
  }
  return crc;
}

std::uint8_t scale_u8_fixed(std::uint8_t value, std::uint8_t factor) {
  return static_cast<std::uint8_t>(
      (static_cast<std::uint16_t>(value) * factor + 127U) / 255U);
}

PremultipliedRgba8 scale_premultiplied_rgba8(
    PremultipliedRgba8 pixel,
    std::uint8_t opacity) {
  return {
      scale_u8_fixed(pixel.red, opacity),
      scale_u8_fixed(pixel.green, opacity),
      scale_u8_fixed(pixel.blue, opacity),
      scale_u8_fixed(pixel.alpha, opacity),
  };
}

PremultipliedRgba8 source_over_premultiplied_rgba8(
    PremultipliedRgba8 bottom,
    PremultipliedRgba8 top) {
  const std::uint8_t inverse_alpha =
      static_cast<std::uint8_t>(255U - top.alpha);
  return {
      saturating_add_u8(top.red, scale_u8_fixed(bottom.red, inverse_alpha)),
      saturating_add_u8(top.green,
                        scale_u8_fixed(bottom.green, inverse_alpha)),
      saturating_add_u8(top.blue, scale_u8_fixed(bottom.blue, inverse_alpha)),
      saturating_add_u8(top.alpha,
                        scale_u8_fixed(bottom.alpha, inverse_alpha)),
  };
}

void source_over_opaque_rgb8(
    const std::uint8_t base[3],
    PremultipliedRgba8 foreground,
    std::uint8_t output[3]) {
  if (base == nullptr || output == nullptr) return;
  const std::uint8_t inverse_alpha =
      static_cast<std::uint8_t>(255U - foreground.alpha);
  output[0] = saturating_add_u8(
      foreground.red, scale_u8_fixed(base[0], inverse_alpha));
  output[1] = saturating_add_u8(
      foreground.green, scale_u8_fixed(base[1], inverse_alpha));
  output[2] = saturating_add_u8(
      foreground.blue, scale_u8_fixed(base[2], inverse_alpha));
}

bool logical_to_global_pixel(
    std::uint16_t global_strip,
    std::uint16_t led,
    std::uint16_t global_strips,
    std::uint16_t leds_per_strip,
    std::uint32_t* global_flat_index) {
  if (global_flat_index == nullptr || global_strips == 0 ||
      leds_per_strip == 0 || global_strip >= global_strips ||
      led >= leds_per_strip) {
    return false;
  }
  *global_flat_index =
      static_cast<std::uint32_t>(global_strip) * leds_per_strip + led;
  return true;
}

bool logical_to_local_pixel(
    std::uint16_t global_strip,
    std::uint16_t led,
    std::uint16_t global_strip_offset,
    std::uint16_t local_strips,
    std::uint16_t leds_per_strip,
    std::uint32_t* local_flat_index) {
  if (local_flat_index == nullptr || local_strips == 0 ||
      leds_per_strip == 0 || global_strip < global_strip_offset ||
      led >= leds_per_strip) {
    return false;
  }
  const std::uint32_t local_strip =
      static_cast<std::uint32_t>(global_strip) - global_strip_offset;
  if (local_strip >= local_strips) return false;
  *local_flat_index = local_strip * leds_per_strip + led;
  return true;
}

}  // namespace ledgrid
