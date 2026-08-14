#include "ledgrid/receiver_runtime.hpp"

#include <algorithm>
#include <cstring>

#include "ledgrid/sha256.hpp"
#include "ledgrid/startup_animation.hpp"

namespace ledgrid {
namespace {

std::uint16_t read_u16(const std::uint8_t* input) {
  return static_cast<std::uint16_t>(
      (static_cast<std::uint16_t>(input[0]) << 8U) | input[1]);
}

std::uint32_t read_u32(const std::uint8_t* input) {
  return (static_cast<std::uint32_t>(input[0]) << 24U) |
         (static_cast<std::uint32_t>(input[1]) << 16U) |
         (static_cast<std::uint32_t>(input[2]) << 8U) | input[3];
}

std::uint64_t read_u64(const std::uint8_t* input) {
  std::uint64_t value = 0;
  for (std::size_t index = 0; index < 8; ++index) {
    value = (value << 8U) | input[index];
  }
  return value;
}

bool equal_bytes(const std::uint8_t* left, const std::uint8_t* right,
                 std::size_t size) {
  return std::memcmp(left, right, size) == 0;
}

bool digest_equal(const Digest256& left, const Digest256& right) {
  return equal_bytes(left.bytes, right.bytes, kSnapshotDigestBytes);
}

Digest256 command_digest(const std::uint8_t* command, std::size_t size) {
  Digest256 digest{};
  sha256(command, size, digest.bytes);
  return digest;
}

bool valid_cadence(std::uint16_t cadence) {
  return cadence >= kMinLocalCadenceHz && cadence <= kMaxLocalCadenceHz;
}

std::uint32_t saturating_increment(std::uint32_t value) {
  return value == UINT32_MAX ? value : value + 1U;
}

constexpr std::uint16_t kHueCycleSteps = 6U * 256U;
constexpr std::uint16_t kSpatialStep =
    kHueCycleSteps / kStartupRainbowPeriodPixels;

void hue_to_rgb(std::uint16_t hue, std::uint8_t* rgb) {
  const std::uint8_t sector = hue >> 8U;
  const std::uint8_t ramp = hue & 0xFFU;
  const std::uint8_t falling = 0xFFU - ramp;
  switch (sector) {
    case 0: rgb[0] = 255; rgb[1] = ramp; rgb[2] = 0; break;
    case 1: rgb[0] = falling; rgb[1] = 255; rgb[2] = 0; break;
    case 2: rgb[0] = 0; rgb[1] = 255; rgb[2] = ramp; break;
    case 3: rgb[0] = 0; rgb[1] = falling; rgb[2] = 255; break;
    case 4: rgb[0] = ramp; rgb[1] = 0; rgb[2] = 255; break;
    default: rgb[0] = 255; rgb[1] = 0; rgb[2] = falling; break;
  }
}

}  // namespace

ReceiverRuntime::ReceiverRuntime(bool local_background_enabled)
    : local_background_enabled_(local_background_enabled) {}

bool ReceiverOutputState::advance_revision() {
  if (revision_ == UINT64_MAX) return false;
  ++revision_;
  return true;
}

bool ReceiverOutputState::configure(
    std::uint8_t strip_count, std::uint16_t leds_per_strip) {
  if (strip_count == configuration_.strip_count &&
      leds_per_strip == configuration_.leds_per_strip) {
    return true;
  }
  if (!advance_revision()) return false;
  configuration_.strip_count = strip_count;
  configuration_.leds_per_strip = leds_per_strip;
  return true;
}

bool ReceiverOutputState::set_brightness(std::uint8_t brightness) {
  if (brightness == configuration_.brightness) return true;
  if (!advance_revision()) return false;
  configuration_.brightness = brightness;
  return true;
}

ReceiverOperationResult ReceiverRuntime::finish(ReceiverOperationResult result) {
  last_result_ = result;
  return result;
}

ReceiverOperationResult ReceiverRuntime::finish_overlay(
    OverlayOperationResult result) {
  last_overlay_result_ = result;
  switch (result) {
    case OverlayOperationResult::Ok:
    case OverlayOperationResult::Idempotent:
      return finish(ReceiverOperationResult::Ok);
    case OverlayOperationResult::UnsupportedVersion:
    case OverlayOperationResult::UnsupportedFormat:
      return finish(ReceiverOperationResult::Unsupported);
    case OverlayOperationResult::InvalidSize:
    case OverlayOperationResult::OutOfBounds:
      return finish(ReceiverOperationResult::InvalidSize);
    case OverlayOperationResult::StaleRevision:
    case OverlayOperationResult::StaleGeneration:
      return finish(ReceiverOperationResult::StaleRevision);
    case OverlayOperationResult::GenerationConflict:
    case OverlayOperationResult::PatchConflict:
      return finish(ReceiverOperationResult::Conflict);
    default:
      return finish(ReceiverOperationResult::InvalidState);
  }
}

ReceiverOperationResult ReceiverRuntime::start_local(
    const std::uint8_t* command, std::size_t size) {
  if (!local_background_enabled_) return finish(ReceiverOperationResult::Unsupported);
  if (size != kLocalBackgroundStartBytes) {
    return finish(ReceiverOperationResult::InvalidSize);
  }
  LocalBackgroundParameters candidate{};
  candidate.reverse_local_strip_order = local_.reverse_local_strip_order;
  candidate.component_id = read_u16(command + 1);
  candidate.preferred_cadence_hz = read_u16(command + 3);
  candidate.global_strip_offset = read_u32(command + 5);
  candidate.common_seed = read_u32(command + 9);
  candidate.scene_epoch = read_u64(command + 13);
  if (candidate.component_id != kCompiledRainbowComponentId ||
      !valid_cadence(candidate.preferred_cadence_hz)) {
    return finish(ReceiverOperationResult::InvalidCommand);
  }
  if (context_state_ != PresentationContextState::Active ||
      active_context_.scene_epoch != candidate.scene_epoch) {
    return finish(ReceiverOperationResult::InvalidState);
  }
  local_ = candidate;
  clear_foreground_visibility(true);
#if LEDGRID_ENABLE_LOCAL_BACKGROUND
  session_requires_snapshot_ = true;
#endif
  ++render_generation_;
  base_mode_ = BaseMode::LocalBackground;
  foreground_state_ = ForegroundState::Cleared;
  transition_reason_ = BaseTransitionReason::LocalStart;
  cadence_initialized_ = false;
  force_local_refresh_ = true;
  return finish(ReceiverOperationResult::Ok);
}

ReceiverOperationResult ReceiverRuntime::update_local(
    const std::uint8_t* command, std::size_t size) {
  if (!local_background_enabled_) return finish(ReceiverOperationResult::Unsupported);
  if (size != kLocalBackgroundParametersBytes) {
    return finish(ReceiverOperationResult::InvalidSize);
  }
  if (base_mode_ != BaseMode::LocalBackground) {
    return finish(ReceiverOperationResult::InvalidState);
  }
  const std::uint16_t cadence = read_u16(command + 1);
  if (!valid_cadence(cadence)) return finish(ReceiverOperationResult::InvalidCommand);
  local_.preferred_cadence_hz = cadence;
  local_.global_strip_offset = read_u32(command + 3);
  local_.common_seed = read_u32(command + 7);
  ++render_generation_;
  cadence_initialized_ = false;
  force_local_refresh_ = true;
  return finish(ReceiverOperationResult::Ok);
}

ReceiverOperationResult ReceiverRuntime::context_begin(
    const std::uint8_t* command, std::size_t size) {
  if (!local_background_enabled_) return finish(ReceiverOperationResult::Unsupported);
  if (size != kPresentationContextBeginBytes || command[1] != kPresentationContextVersion) {
    return finish(ReceiverOperationResult::InvalidSize);
  }
  const std::uint8_t* session = command + 2;
  const std::uint64_t revision = read_u64(command + 18);
  const std::uint8_t* digest = command + 26;
  const bool same_staged_session =
      context_state_ != PresentationContextState::None &&
      equal_bytes(session, staged_context_.session, 16);
  if (same_staged_session) {
    if (revision < staged_context_.scene_revision)
      return finish(ReceiverOperationResult::StaleRevision);
    if (revision == staged_context_.scene_revision) {
      if (!equal_bytes(digest, staged_context_.context_digest, 32))
        return finish(ReceiverOperationResult::Conflict);
      return finish(ReceiverOperationResult::Ok);
    }
  }
  const bool same_active_session = context_state_ == PresentationContextState::Active &&
      equal_bytes(session, active_context_.session, 16);
  if (same_active_session) {
    if (revision < active_context_.scene_revision)
      return finish(ReceiverOperationResult::StaleRevision);
    if (revision == active_context_.scene_revision) {
      if (!equal_bytes(digest, active_context_.context_digest, 32))
        return finish(ReceiverOperationResult::Conflict);
      return finish(ReceiverOperationResult::Ok);
    }
  }
  staged_context_ = {};
  std::memcpy(staged_context_.session, session, 16);
  staged_context_.scene_revision = revision;
  std::memcpy(staged_context_.context_digest, digest, 32);
  context_state_ = PresentationContextState::Staging;
  return finish(ReceiverOperationResult::Ok);
}

ReceiverOperationResult ReceiverRuntime::context_set(
    const std::uint8_t* command, std::size_t size) {
  if (!local_background_enabled_) return finish(ReceiverOperationResult::Unsupported);
  if (size < kPresentationContextSetBaseBytes ||
      size > kPresentationContextSetMaxBytes ||
      command[1] != kPresentationContextVersion) {
    return finish(ReceiverOperationResult::InvalidSize);
  }
  const std::uint8_t modifier_count = command[144];
  if (size != kPresentationContextSetBaseBytes +
                  static_cast<std::size_t>(modifier_count) *
                      kPresentationContextSetEntryBytes) {
    return finish(ReceiverOperationResult::InvalidSize);
  }
  std::uint8_t context_digest[32] = {};
  sha256(command + 18, size - 18, context_digest);
  if (context_state_ == PresentationContextState::Active &&
      equal_bytes(command + 2, active_context_.session, 16) &&
      read_u64(command + 18) == active_context_.scene_revision &&
      equal_bytes(context_digest, active_context_.context_digest, 32)) {
    return finish(ReceiverOperationResult::Ok);
  }
  if (context_state_ == PresentationContextState::Ready &&
      equal_bytes(command + 2, staged_context_.session, 16) &&
      read_u64(command + 18) == staged_context_.scene_revision &&
      equal_bytes(context_digest, staged_context_.context_digest, 32)) {
    return finish(ReceiverOperationResult::Ok);
  }
  if (context_state_ != PresentationContextState::Staging ||
      !equal_bytes(command + 2, staged_context_.session, 16) ||
      read_u64(command + 18) != staged_context_.scene_revision)
    return finish(ReceiverOperationResult::InvalidState);
  const std::uint16_t luminance = read_u16(command + 97);
  if (command[26] < 1 || command[26] > 5 || read_u32(command + 27) == 0 ||
      luminance > kQ8_8One || command[103] != 1 ||
      modifier_count > 14) {
    return finish(ReceiverOperationResult::InvalidContext);
  }
  std::uint8_t prior_id = 0;
  std::uint8_t field_count = 0;
  std::uint8_t surface_count = 0;
  for (std::size_t index = 0; index < modifier_count; ++index) {
    const std::size_t offset = 145 + index * 3U;
    const std::uint8_t id = command[offset];
    if (id == 0 || id > 14 || id <= prior_id ||
        read_u16(command + offset + 1) > kQ8_8One) {
      return finish(ReceiverOperationResult::InvalidContext);
    }
    if (id >= 6 && id <= 8) ++field_count;
    if (id >= 9 && id <= 13) ++surface_count;
    prior_id = id;
  }
  if (field_count > 1 || surface_count > 1)
    return finish(ReceiverOperationResult::InvalidContext);
  if (!equal_bytes(context_digest, staged_context_.context_digest, 32)) {
    return finish(ReceiverOperationResult::DigestMismatch);
  }
  std::uint8_t plant_canonical[2 + 14 * 3] = {};
  plant_canonical[0] = command[103];
  plant_canonical[1] = modifier_count;
  if (modifier_count != 0) {
    std::memcpy(plant_canonical + 2, command + 145,
                static_cast<std::size_t>(modifier_count) * 3U);
  }
  std::uint8_t plant_digest[32] = {};
  sha256(plant_canonical, 2 + static_cast<std::size_t>(modifier_count) * 3U,
         plant_digest);
  if (!equal_bytes(plant_digest, command + 112, 32)) {
    return finish(ReceiverOperationResult::DigestMismatch);
  }
  staged_context_.vibe_id = command[26];
  staged_context_.vibe_profile_version = read_u32(command + 27);
  staged_context_.vibe_revision = read_u64(command + 31);
  std::memcpy(staged_context_.vibe_digest, command + 39, 32);
  staged_context_.luminance_q8_8 = luminance;
  staged_context_.modifier_revision = read_u64(command + 104);
  std::memcpy(staged_context_.modifier_digest, command + 112, 32);
  context_state_ = PresentationContextState::Ready;
  return finish(ReceiverOperationResult::Ok);
}

ReceiverOperationResult ReceiverRuntime::context_commit(
    const std::uint8_t* command, std::size_t size) {
  if (!local_background_enabled_) return finish(ReceiverOperationResult::Unsupported);
  if (size != kPresentationContextCommitBytes ||
      command[1] != kPresentationContextVersion) {
    return finish(ReceiverOperationResult::InvalidSize);
  }
  if (context_state_ == PresentationContextState::Active &&
      equal_bytes(command + 2, active_context_.session, 16) &&
      read_u64(command + 18) == active_context_.scene_revision &&
      read_u64(command + 26) == active_context_.scene_epoch &&
      read_u64(command + 34) == active_context_.present_at_scene_time_us &&
      equal_bytes(command + 42, active_context_.context_digest, 32)) {
    return finish(ReceiverOperationResult::Ok);
  }
  if (context_state_ != PresentationContextState::Ready ||
      !equal_bytes(command + 2, staged_context_.session, 16) ||
      read_u64(command + 18) != staged_context_.scene_revision ||
      !equal_bytes(command + 42, staged_context_.context_digest, 32)) {
    return finish(ReceiverOperationResult::InvalidState);
  }
  staged_context_.scene_epoch = read_u64(command + 26);
  staged_context_.present_at_scene_time_us = read_u64(command + 34);
  active_context_ = staged_context_;
  active_context_present_ = true;
#if LEDGRID_ENABLE_LOCAL_BACKGROUND
  if ((committed_overlay_present_ &&
       committed_base_revision_ != active_context_.scene_revision) ||
      (overlay_generation_order_.has_staged_generation &&
       staged_base_revision_ != active_context_.scene_revision)) {
    clear_foreground_visibility(true);
    session_requires_snapshot_ = true;
  }
#endif
  if (base_mode_ == BaseMode::LocalBackground) {
    local_.scene_epoch = active_context_.scene_epoch;
  }
  ++render_generation_;
  context_state_ = PresentationContextState::Active;
  request_local_refresh();
  return finish(ReceiverOperationResult::Ok);
}

void ReceiverRuntime::discard_overlay_staging() {
#if LEDGRID_ENABLE_LOCAL_BACKGROUND
  pending_overlay_present_ = false;
  overlay_generation_order_.has_staged_generation = false;
  overlay_generation_order_.staged_generation = 0;
  overlay_generation_order_.staged_operation_digest = {};
  overlay_patch_order_ = {};
  last_batch_digest_present_ = false;
  last_batch_first_start_ = 0;
  last_batch_span_count_ = 0;
  staged_scene_revision_ = 0;
  staged_scene_epoch_ = 0;
  staged_base_revision_ = 0;
  staged_lease_ms_ = 0;
  staged_started_local_us_ = 0;
  pending_present_at_scene_time_us_ = 0;
  foreground_state_ = committed_overlay_present_ ? ForegroundState::Active
                                                  : ForegroundState::Cleared;
#endif
}

void ReceiverRuntime::clear_foreground_visibility(bool discard_staging) {
#if LEDGRID_ENABLE_LOCAL_BACKGROUND
  const bool changed = committed_overlay_present_;
  committed_overlay_present_ = false;
  committed_coverage_pixels_ = 0;
  committed_has_lease_ = false;
  committed_lease_ms_ = 0;
  committed_lease_deadline_us_ = 0;
  committed_scene_revision_ = 0;
  committed_scene_epoch_ = 0;
  committed_base_revision_ = 0;
  committed_present_at_scene_time_us_ = 0;
  std::memset(overlay_rgba_[committed_plane_], 0, kContractLocalRgbaBytes);
  std::memset(overlay_coverage_[committed_plane_], 0, kContractLocalPixels);
  if (discard_staging) discard_overlay_staging();
  foreground_state_ = pending_overlay_present_ ? ForegroundState::Staging
                                                : ForegroundState::Cleared;
  if (changed) {
    ++foreground_revision_;
    foreground_refresh_pending_ = base_mode_ == BaseMode::LocalBackground;
  } else if (base_mode_ != BaseMode::LocalBackground) {
    foreground_refresh_pending_ = false;
  }
#else
  (void)discard_staging;
#endif
}

ReceiverOperationResult ReceiverRuntime::controller_session_begin(
    const std::uint8_t* command, std::size_t size) {
#if !LEDGRID_ENABLE_LOCAL_BACKGROUND
  (void)command; (void)size;
  return finish_overlay(OverlayOperationResult::InvalidState);
#else
  if (!local_background_enabled_) {
    return finish(ReceiverOperationResult::Unsupported);
  }
  if (size != kControllerSessionBeginHeaderBytes) {
    return finish_overlay(OverlayOperationResult::InvalidSize);
  }
  if (command[1] != kAnimationPipelineProtocolVersion) {
    return finish_overlay(OverlayOperationResult::UnsupportedVersion);
  }
  const std::uint64_t revision = read_u64(command + 18);
  Digest256 digest{};
  std::memcpy(digest.bytes, command + 26, kSnapshotDigestBytes);
  if (controller_session_present_ &&
      equal_bytes(command + 2, controller_session_, kControllerSessionBytes)) {
    if (revision < controller_desired_revision_) {
      return finish_overlay(OverlayOperationResult::StaleRevision);
    }
    if (revision == controller_desired_revision_) {
      return finish_overlay(
          digest_equal(digest, controller_snapshot_digest_)
              ? OverlayOperationResult::Idempotent
              : OverlayOperationResult::GenerationConflict);
    }
  }
  std::memcpy(controller_session_, command + 2, kControllerSessionBytes);
  controller_desired_revision_ = revision;
  controller_snapshot_digest_ = digest;
  controller_session_present_ = true;
  discard_overlay_staging();
  overlay_generation_order_ = {};
  session_requires_snapshot_ = true;
  last_commit_digest_present_ = false;
  last_clear_digest_present_ = false;
  return finish_overlay(OverlayOperationResult::Ok);
#endif
}

ReceiverOperationResult ReceiverRuntime::overlay_begin(
    const std::uint8_t* command, std::size_t size,
    std::uint64_t local_monotonic_us) {
#if !LEDGRID_ENABLE_LOCAL_BACKGROUND
  (void)command; (void)size; (void)local_monotonic_us;
  return finish_overlay(OverlayOperationResult::InvalidState);
#else
  if (!local_background_enabled_) return finish(ReceiverOperationResult::Unsupported);
  if (size != kOverlayBeginHeaderBytes) {
    return finish_overlay(OverlayOperationResult::InvalidSize);
  }
  const auto format = static_cast<OverlayFormat>(command[58]);
  const OverlayOperationResult version =
      validate_overlay_version_format(command[1], format);
  if (version != OverlayOperationResult::Ok) return finish_overlay(version);
  if (!controller_session_present_ ||
      !equal_bytes(command + 2, controller_session_, kControllerSessionBytes)) {
    return finish_overlay(OverlayOperationResult::StaleSession);
  }
  const auto kind = static_cast<OverlayUpdateKind>(command[59]);
  if (kind != OverlayUpdateKind::FullSnapshot &&
      kind != OverlayUpdateKind::Delta) {
    return finish_overlay(OverlayOperationResult::UnsupportedFormat);
  }
  const std::uint16_t expected = read_u16(command + 60);
  if ((kind == OverlayUpdateKind::FullSnapshot &&
       expected != kContractFullSnapshotPatchCount) ||
      (kind == OverlayUpdateKind::Delta && expected > kContractLocalPixels)) {
    return finish_overlay(OverlayOperationResult::InvalidSize);
  }
  if (session_requires_snapshot_ && kind != OverlayUpdateKind::FullSnapshot) {
    return finish_overlay(OverlayOperationResult::InvalidState);
  }
  const std::uint64_t scene_revision = read_u64(command + 34);
  const std::uint64_t scene_epoch = read_u64(command + 42);
  const std::uint64_t base_revision = read_u64(command + 50);
  if (context_state_ != PresentationContextState::Active ||
      base_mode_ != BaseMode::LocalBackground) {
    return finish_overlay(OverlayOperationResult::BaseBindingMismatch);
  }
  if (scene_revision < active_context_.scene_revision) {
    return finish_overlay(OverlayOperationResult::StaleRevision);
  }
  if (scene_revision != active_context_.scene_revision ||
      scene_epoch != active_context_.scene_epoch ||
      base_revision != active_context_.scene_revision) {
    return finish_overlay(OverlayOperationResult::BaseBindingMismatch);
  }
  const Digest256 digest = command_digest(command, size);
  const OverlayOperationResult ordering = validate_overlay_generation_begin(
      overlay_generation_order_, read_u64(command + 18),
      read_u64(command + 26), digest);
  if (ordering != OverlayOperationResult::Ok) return finish_overlay(ordering);

  overlay_generation_order_.has_staged_generation = true;
  overlay_generation_order_.staged_generation = read_u64(command + 18);
  overlay_generation_order_.staged_operation_digest = digest;
  overlay_patch_order_ = {};
  overlay_patch_order_.expected_patches = expected;
  overlay_patch_order_.update_kind = kind;
  last_batch_digest_present_ = false;
  last_batch_first_start_ = 0;
  last_batch_span_count_ = 0;
  staged_scene_revision_ = scene_revision;
  staged_scene_epoch_ = scene_epoch;
  staged_base_revision_ = base_revision;
  staged_lease_ms_ = read_u32(command + 62);
  staged_started_local_us_ = local_monotonic_us;
  staging_coverage_pixels_ = 0;
  if (kind == OverlayUpdateKind::FullSnapshot) {
    std::memset(overlay_rgba_[staging_plane_], 0, kContractLocalRgbaBytes);
    std::memset(overlay_coverage_[staging_plane_], 0, kContractLocalPixels);
  } else {
    std::memcpy(overlay_rgba_[staging_plane_], overlay_rgba_[committed_plane_],
                kContractLocalRgbaBytes);
    std::memcpy(overlay_coverage_[staging_plane_],
                overlay_coverage_[committed_plane_], kContractLocalPixels);
    staging_coverage_pixels_ = committed_coverage_pixels_;
  }
  foreground_state_ = ForegroundState::Staging;
  return finish_overlay(OverlayOperationResult::Ok);
#endif
}

ReceiverOperationResult ReceiverRuntime::overlay_patch(
    const std::uint8_t* command, std::size_t size) {
#if !LEDGRID_ENABLE_LOCAL_BACKGROUND
  (void)command; (void)size;
  return finish_overlay(OverlayOperationResult::InvalidState);
#else
  if (!local_background_enabled_) return finish(ReceiverOperationResult::Unsupported);
  if (size < kOverlayPatchHeaderBytes ||
      command[1] != kAnimationPipelineProtocolVersion) {
    return finish_overlay(size < kOverlayPatchHeaderBytes
        ? OverlayOperationResult::InvalidSize
        : OverlayOperationResult::UnsupportedVersion);
  }
  if (!controller_session_present_ ||
      !equal_bytes(command + 2, controller_session_, kControllerSessionBytes)) {
    return finish_overlay(OverlayOperationResult::StaleSession);
  }
  if (!overlay_generation_order_.has_staged_generation) {
    return finish_overlay(OverlayOperationResult::InvalidState);
  }
  const std::uint64_t generation = read_u64(command + 18);
  if (generation < overlay_generation_order_.staged_generation) {
    return finish_overlay(OverlayOperationResult::StaleGeneration);
  }
  if (generation != overlay_generation_order_.staged_generation) {
    return finish_overlay(OverlayOperationResult::InvalidState);
  }
  const std::uint16_t start = read_u16(command + 26);
  const std::uint16_t count = read_u16(command + 28);
  if (size != kOverlayPatchHeaderBytes +
                  static_cast<std::size_t>(count) *
                      kPremultipliedRgbaBytesPerPixel) {
    return finish_overlay(OverlayOperationResult::InvalidSize);
  }
  for (std::size_t pixel = 0; pixel < count; ++pixel) {
    const std::uint8_t* rgba = command + kOverlayPatchHeaderBytes + pixel * 4U;
    if (rgba[0] > rgba[3] || rgba[1] > rgba[3] || rgba[2] > rgba[3]) {
      return finish_overlay(OverlayOperationResult::UnsupportedFormat);
    }
  }
  const Digest256 digest = command_digest(
      command + kOverlayPatchHeaderBytes,
      size - kOverlayPatchHeaderBytes);
  const OverlayOperationResult accepted = accept_overlay_patch(
      &overlay_patch_order_, start, count, digest);
  if (accepted != OverlayOperationResult::Ok) return finish_overlay(accepted);
  last_batch_digest_present_ = false;
  last_batch_first_start_ = 0;
  last_batch_span_count_ = 0;
  for (std::size_t pixel = 0; pixel < count; ++pixel) {
    const std::size_t index = static_cast<std::size_t>(start) + pixel;
    const std::uint8_t* rgba = command + kOverlayPatchHeaderBytes + pixel * 4U;
    const bool was_covered = overlay_coverage_[staging_plane_][index] != 0;
    const bool covered = rgba[3] != 0;
    std::memcpy(overlay_rgba_[staging_plane_] + index * 4U, rgba, 4);
    overlay_coverage_[staging_plane_][index] = covered ? 1U : 0U;
    if (covered && !was_covered) ++staging_coverage_pixels_;
    if (!covered && was_covered) --staging_coverage_pixels_;
  }
  return finish_overlay(OverlayOperationResult::Ok);
#endif
}

ReceiverOperationResult ReceiverRuntime::overlay_patch_batch(
    const std::uint8_t* command, std::size_t size) {
#if !LEDGRID_ENABLE_LOCAL_BACKGROUND
  (void)command;
  (void)size;
  return finish_overlay(OverlayOperationResult::InvalidState);
#else
  if (!local_background_enabled_) {
    return finish(ReceiverOperationResult::Unsupported);
  }
  if (size < kOverlayPatchBatchHeaderBytes ||
      size > kAnimationPipelineMaxTransactionBytes -
                 kAnimationPipelineCrcBytes) {
    return finish_overlay(OverlayOperationResult::InvalidSize);
  }
  if (command[1] != kAnimationPipelineProtocolVersion) {
    return finish_overlay(OverlayOperationResult::UnsupportedVersion);
  }
  if (!controller_session_present_ ||
      !equal_bytes(command + 2, controller_session_, kControllerSessionBytes)) {
    return finish_overlay(OverlayOperationResult::StaleSession);
  }
  if (!overlay_generation_order_.has_staged_generation) {
    return finish_overlay(OverlayOperationResult::InvalidState);
  }
  const std::uint64_t generation = read_u64(command + 18);
  if (generation < overlay_generation_order_.staged_generation) {
    return finish_overlay(OverlayOperationResult::StaleGeneration);
  }
  if (generation != overlay_generation_order_.staged_generation) {
    return finish_overlay(OverlayOperationResult::InvalidState);
  }

  const std::uint16_t span_count = read_u16(command + 26);
  if (span_count == 0 || span_count > kMaxSinglePixelSpansPerBatch) {
    return finish_overlay(OverlayOperationResult::InvalidSize);
  }

  // Validate the complete CRC-bound batch before changing either the order
  // proof or staging plane. This makes one command acknowledgement proof of an
  // all-or-nothing update even when the final span is malformed.
  std::size_t offset = kOverlayPatchBatchHeaderBytes;
  std::uint32_t batch_units = 0;
  std::uint16_t first_start = 0;
  for (std::size_t span = 0; span < span_count; ++span) {
    if (offset + kOverlayPatchBatchSpanHeaderBytes > size) {
      return finish_overlay(OverlayOperationResult::InvalidSize);
    }
    const std::uint16_t start = read_u16(command + offset);
    const std::uint16_t count = read_u16(command + offset + 2U);
    if (span == 0) first_start = start;
    if (count == 0 || count > kMaxRgbaPixelsPerBatchSpan) {
      return finish_overlay(OverlayOperationResult::InvalidSize);
    }
    if (static_cast<std::uint32_t>(start) + count >
        kContractLocalPixels) {
      return finish_overlay(OverlayOperationResult::OutOfBounds);
    }
    batch_units += static_cast<std::uint32_t>(count) + 1U;
    if (batch_units > 1016U) {
      return finish_overlay(OverlayOperationResult::InvalidSize);
    }
    offset += kOverlayPatchBatchSpanHeaderBytes;
    const std::size_t rgba_bytes =
        static_cast<std::size_t>(count) * kPremultipliedRgbaBytesPerPixel;
    if (offset + rgba_bytes > size) {
      return finish_overlay(OverlayOperationResult::InvalidSize);
    }
    for (std::size_t pixel = 0; pixel < count; ++pixel) {
      const std::uint8_t* rgba = command + offset + pixel * 4U;
      if (rgba[0] > rgba[3] || rgba[1] > rgba[3] || rgba[2] > rgba[3]) {
        return finish_overlay(OverlayOperationResult::UnsupportedFormat);
      }
    }
    offset += rgba_bytes;
  }
  if (offset != size) {
    return finish_overlay(OverlayOperationResult::InvalidSize);
  }

  const Digest256 batch_digest = command_digest(command, size);
  if (last_batch_digest_present_ &&
      first_start == last_batch_first_start_ &&
      span_count == last_batch_span_count_) {
    return finish_overlay(
        digest_equal(batch_digest, last_batch_digest_)
            ? OverlayOperationResult::Idempotent
            : OverlayOperationResult::PatchConflict);
  }

  OverlayPatchOrderState candidate_order = overlay_patch_order_;
  offset = kOverlayPatchBatchHeaderBytes;
  for (std::size_t span = 0; span < span_count; ++span) {
    const std::uint16_t start = read_u16(command + offset);
    const std::uint16_t count = read_u16(command + offset + 2U);
    offset += kOverlayPatchBatchSpanHeaderBytes;
    const std::size_t rgba_bytes =
        static_cast<std::size_t>(count) * kPremultipliedRgbaBytesPerPixel;
    const Digest256 span_digest = command_digest(command + offset, rgba_bytes);
    const OverlayOperationResult accepted = accept_overlay_patch(
        &candidate_order, start, count, span_digest);
    if (accepted != OverlayOperationResult::Ok) {
      // Only a byte-exact retry of the complete latest batch is idempotent.
      // A batch must not append work by embedding a legacy single-span retry.
      return finish_overlay(
          accepted == OverlayOperationResult::Idempotent
              ? OverlayOperationResult::PatchConflict
              : accepted);
    }
    offset += rgba_bytes;
  }

  offset = kOverlayPatchBatchHeaderBytes;
  for (std::size_t span = 0; span < span_count; ++span) {
    const std::uint16_t start = read_u16(command + offset);
    const std::uint16_t count = read_u16(command + offset + 2U);
    offset += kOverlayPatchBatchSpanHeaderBytes;
    for (std::size_t pixel = 0; pixel < count; ++pixel) {
      const std::size_t index = static_cast<std::size_t>(start) + pixel;
      const std::uint8_t* rgba = command + offset + pixel * 4U;
      const bool was_covered =
          overlay_coverage_[staging_plane_][index] != 0;
      const bool covered = rgba[3] != 0;
      std::memcpy(overlay_rgba_[staging_plane_] + index * 4U, rgba, 4);
      overlay_coverage_[staging_plane_][index] = covered ? 1U : 0U;
      if (covered && !was_covered) ++staging_coverage_pixels_;
      if (!covered && was_covered) --staging_coverage_pixels_;
    }
    offset += static_cast<std::size_t>(count) *
              kPremultipliedRgbaBytesPerPixel;
  }
  overlay_patch_order_ = candidate_order;
  last_batch_digest_ = batch_digest;
  last_batch_digest_present_ = true;
  last_batch_first_start_ = first_start;
  last_batch_span_count_ = span_count;
  return finish_overlay(OverlayOperationResult::Ok);
#endif
}

void ReceiverRuntime::activate_pending_overlay(
    std::uint64_t local_monotonic_us) {
#if LEDGRID_ENABLE_LOCAL_BACKGROUND
  if (!pending_overlay_present_) return;
  std::swap(committed_plane_, staging_plane_);
  committed_overlay_present_ = true;
  committed_coverage_pixels_ = staging_coverage_pixels_;
  committed_scene_revision_ = staged_scene_revision_;
  committed_scene_epoch_ = staged_scene_epoch_;
  committed_base_revision_ = staged_base_revision_;
  committed_present_at_scene_time_us_ = pending_present_at_scene_time_us_;
  committed_lease_ms_ = staged_lease_ms_;
  committed_has_lease_ = committed_lease_ms_ != 0;
  if (committed_has_lease_) {
    const std::uint64_t duration =
        static_cast<std::uint64_t>(committed_lease_ms_) * 1000ULL;
    committed_lease_deadline_us_ =
        UINT64_MAX - local_monotonic_us < duration
            ? UINT64_MAX : local_monotonic_us + duration;
  } else {
    committed_lease_deadline_us_ = 0;
  }
  overlay_generation_order_.committed_generation =
      overlay_generation_order_.staged_generation;
  overlay_generation_order_.has_staged_generation = false;
  overlay_generation_order_.staged_generation = 0;
  pending_overlay_present_ = false;
  session_requires_snapshot_ = false;
  foreground_state_ = ForegroundState::Active;
  ++foreground_revision_;
  foreground_refresh_pending_ = base_mode_ == BaseMode::LocalBackground;
#else
  (void)local_monotonic_us;
#endif
}

ReceiverOperationResult ReceiverRuntime::overlay_commit(
    const std::uint8_t* command, std::size_t size,
    std::uint64_t local_monotonic_us) {
#if !LEDGRID_ENABLE_LOCAL_BACKGROUND
  (void)command; (void)size; (void)local_monotonic_us;
  return finish_overlay(OverlayOperationResult::InvalidState);
#else
  if (!local_background_enabled_) return finish(ReceiverOperationResult::Unsupported);
  if (size != kOverlayCommitHeaderBytes) {
    return finish_overlay(OverlayOperationResult::InvalidSize);
  }
  if (command[1] != kAnimationPipelineProtocolVersion) {
    return finish_overlay(OverlayOperationResult::UnsupportedVersion);
  }
  if (!controller_session_present_ ||
      !equal_bytes(command + 2, controller_session_, kControllerSessionBytes)) {
    return finish_overlay(OverlayOperationResult::StaleSession);
  }
  const std::uint64_t generation = read_u64(command + 18);
  const Digest256 digest = command_digest(command, size);
  if (!overlay_generation_order_.has_staged_generation) {
    if (generation == overlay_generation_order_.committed_generation &&
        last_commit_digest_present_ && digest_equal(digest, last_commit_digest_)) {
      return finish_overlay(OverlayOperationResult::Idempotent);
    }
    return finish_overlay(generation < overlay_generation_order_.committed_generation
        ? OverlayOperationResult::StaleGeneration
        : OverlayOperationResult::InvalidState);
  }
  if (pending_overlay_present_) {
    return finish_overlay(
        generation == overlay_generation_order_.staged_generation &&
                last_commit_digest_present_ &&
                digest_equal(digest, last_commit_digest_)
            ? OverlayOperationResult::Idempotent
            : OverlayOperationResult::GenerationConflict);
  }
  if (generation < overlay_generation_order_.staged_generation) {
    return finish_overlay(OverlayOperationResult::StaleGeneration);
  }
  if (generation != overlay_generation_order_.staged_generation) {
    return finish_overlay(OverlayOperationResult::InvalidState);
  }
  const bool binding = base_mode_ == BaseMode::LocalBackground &&
      context_state_ == PresentationContextState::Active &&
      read_u64(command + 26) == staged_scene_epoch_ &&
      read_u64(command + 34) == staged_base_revision_ &&
      active_context_.scene_epoch == staged_scene_epoch_ &&
      active_context_.scene_revision == staged_base_revision_;
  const bool lease_expired = staged_lease_ms_ != 0 &&
      local_monotonic_us >= staged_started_local_us_ &&
      local_monotonic_us - staged_started_local_us_ >=
          static_cast<std::uint64_t>(staged_lease_ms_) * 1000ULL;
  const OverlayOperationResult valid = validate_overlay_commit(
      overlay_patch_order_, binding, lease_expired);
  if (valid != OverlayOperationResult::Ok) return finish_overlay(valid);
  last_commit_digest_ = digest;
  last_commit_digest_present_ = true;
  last_clear_digest_present_ = false;
  pending_present_at_scene_time_us_ = read_u64(command + 42);
  pending_overlay_present_ = true;
  overlay_stats_.commits = saturating_increment(overlay_stats_.commits);
  const std::uint64_t current_scene_time = scene_time_us(local_monotonic_us);
  if (pending_present_at_scene_time_us_ <= current_scene_time) {
    activate_pending_overlay(local_monotonic_us);
  } else {
    foreground_state_ = ForegroundState::Staging;
  }
  return finish_overlay(OverlayOperationResult::Ok);
#endif
}

ReceiverOperationResult ReceiverRuntime::overlay_clear(
    const std::uint8_t* command, std::size_t size) {
#if !LEDGRID_ENABLE_LOCAL_BACKGROUND
  (void)command; (void)size;
  return finish_overlay(OverlayOperationResult::InvalidState);
#else
  if (!local_background_enabled_) return finish(ReceiverOperationResult::Unsupported);
  if (size != kOverlayClearHeaderBytes) {
    return finish_overlay(OverlayOperationResult::InvalidSize);
  }
  if (command[1] != kAnimationPipelineProtocolVersion) {
    return finish_overlay(OverlayOperationResult::UnsupportedVersion);
  }
  if (!controller_session_present_ ||
      !equal_bytes(command + 2, controller_session_, kControllerSessionBytes)) {
    return finish_overlay(OverlayOperationResult::StaleSession);
  }
  const std::uint64_t generation = read_u64(command + 18);
  const Digest256 digest = command_digest(command, size);
  if (generation < overlay_generation_order_.committed_generation ||
      (overlay_generation_order_.has_staged_generation &&
       generation < overlay_generation_order_.staged_generation)) {
    return finish_overlay(OverlayOperationResult::StaleGeneration);
  }
  if (generation == overlay_generation_order_.committed_generation) {
    return finish_overlay(last_clear_digest_present_ &&
                                  digest_equal(digest, last_clear_digest_)
                              ? OverlayOperationResult::Idempotent
                              : OverlayOperationResult::GenerationConflict);
  }
  const std::uint64_t revision = read_u64(command + 26);
  if (context_state_ != PresentationContextState::Active ||
      revision != active_context_.scene_revision) {
    return finish_overlay(revision < active_context_.scene_revision
        ? OverlayOperationResult::StaleRevision
        : OverlayOperationResult::BaseBindingMismatch);
  }
  discard_overlay_staging();
  overlay_generation_order_.committed_generation = generation;
  last_clear_digest_ = digest;
  last_clear_digest_present_ = true;
  last_commit_digest_present_ = false;
  session_requires_snapshot_ = false;
  clear_foreground_visibility(false);
  return finish_overlay(OverlayOperationResult::Ok);
#endif
}

ReceiverOperationResult ReceiverRuntime::overlay_renew(
    const std::uint8_t* command, std::size_t size,
    std::uint64_t local_monotonic_us) {
#if !LEDGRID_ENABLE_LOCAL_BACKGROUND
  (void)command; (void)size; (void)local_monotonic_us;
  return finish_overlay(OverlayOperationResult::InvalidState);
#else
  if (!local_background_enabled_) return finish(ReceiverOperationResult::Unsupported);
  if (size != kOverlayRenewHeaderBytes) {
    return finish_overlay(OverlayOperationResult::InvalidSize);
  }
  if (command[1] != kAnimationPipelineProtocolVersion) {
    return finish_overlay(OverlayOperationResult::UnsupportedVersion);
  }
  if (!controller_session_present_ ||
      !equal_bytes(command + 2, controller_session_, kControllerSessionBytes)) {
    return finish_overlay(OverlayOperationResult::StaleSession);
  }
  service_foreground(local_monotonic_us);
  const std::uint64_t generation = read_u64(command + 18);
  if (generation < overlay_generation_order_.committed_generation) {
    return finish_overlay(OverlayOperationResult::StaleGeneration);
  }
  if (!committed_overlay_present_ ||
      generation != overlay_generation_order_.committed_generation) {
    return finish_overlay(OverlayOperationResult::InvalidState);
  }
  const std::uint32_t lease = read_u32(command + 26);
  if (lease == 0) return finish_overlay(OverlayOperationResult::InvalidSize);
  const std::uint64_t duration = static_cast<std::uint64_t>(lease) * 1000ULL;
  committed_lease_ms_ = lease;
  committed_has_lease_ = true;
  committed_lease_deadline_us_ = UINT64_MAX - local_monotonic_us < duration
      ? UINT64_MAX : local_monotonic_us + duration;
  return finish_overlay(OverlayOperationResult::Ok);
#endif
}

ReceiverOperationResult ReceiverRuntime::process_command(
    const std::uint8_t* command,
    std::size_t size,
    std::uint64_t local_monotonic_us) {
  if (command == nullptr || size == 0) return finish(ReceiverOperationResult::InvalidSize);
  switch (static_cast<ReceiverCommand>(command[0])) {
    case ReceiverCommand::ControllerSessionBegin:
      return controller_session_begin(command, size);
    case ReceiverCommand::LocalBackgroundStart:
      return start_local(command, size);
    case ReceiverCommand::LocalBackgroundStop:
      if (!local_background_enabled_) return finish(ReceiverOperationResult::Unsupported);
      if (size != kLocalBackgroundStopBytes) return finish(ReceiverOperationResult::InvalidSize);
      clear_foreground_visibility(true);
#if LEDGRID_ENABLE_LOCAL_BACKGROUND
      session_requires_snapshot_ = true;
#endif
      base_mode_ = BaseMode::StartupFallback;
      ++render_generation_;
      foreground_state_ = ForegroundState::Cleared;
      transition_reason_ = BaseTransitionReason::LocalStop;
      return finish(ReceiverOperationResult::Ok);
    case ReceiverCommand::LocalBackgroundParameters:
      return update_local(command, size);
    case ReceiverCommand::PresentationContextBegin:
      return context_begin(command, size);
    case ReceiverCommand::PresentationContextSet:
      return context_set(command, size);
    case ReceiverCommand::PresentationContextCommit:
      {
        const bool was_active =
            context_state_ == PresentationContextState::Active;
        const ReceiverOperationResult result = context_commit(command, size);
        if (result == ReceiverOperationResult::Ok && !was_active) {
          context_committed_local_us_ = local_monotonic_us;
        }
        return result;
      }
    case ReceiverCommand::OverlayBegin:
      return overlay_begin(command, size, local_monotonic_us);
    case ReceiverCommand::OverlayPatch:
      return overlay_patch(command, size);
    case ReceiverCommand::OverlayPatchBatch:
      return overlay_patch_batch(command, size);
    case ReceiverCommand::OverlayCommit:
      return overlay_commit(command, size, local_monotonic_us);
    case ReceiverCommand::OverlayClear:
      return overlay_clear(command, size);
    case ReceiverCommand::OverlayRenew:
      return overlay_renew(command, size, local_monotonic_us);
    default:
      return finish(ReceiverOperationResult::InvalidCommand);
  }
}

void ReceiverRuntime::complete_host_frame() {
  clear_foreground_visibility(true);
#if LEDGRID_ENABLE_LOCAL_BACKGROUND
  controller_session_present_ = false;
  std::memset(controller_session_, 0, sizeof(controller_session_));
  controller_desired_revision_ = 0;
  controller_snapshot_digest_ = {};
  overlay_generation_order_ = {};
  session_requires_snapshot_ = true;
  last_commit_digest_present_ = false;
  last_clear_digest_present_ = false;
#endif
  base_mode_ = BaseMode::HostFullScene;
  ++render_generation_;
  foreground_state_ = ForegroundState::Cleared;
  transition_reason_ = BaseTransitionReason::HostTakeover;
  last_result_ = ReceiverOperationResult::Ok;
}

void ReceiverRuntime::receiver_restart() {
  clear_foreground_visibility(true);
#if LEDGRID_ENABLE_LOCAL_BACKGROUND
  session_requires_snapshot_ = true;
#endif
  base_mode_ = BaseMode::StartupFallback;
  ++render_generation_;
  foreground_state_ = ForegroundState::Cleared;
  maintenance_state_ = MaintenanceState::Inactive;
  transition_reason_ = BaseTransitionReason::ReceiverRestart;
  context_state_ = PresentationContextState::None;
  staged_context_ = {};
  active_context_ = {};
  active_context_present_ = false;
  cadence_initialized_ = false;
  context_committed_local_us_ = 0;
#if LEDGRID_ENABLE_LOCAL_BACKGROUND
  controller_session_present_ = false;
  std::memset(controller_session_, 0, sizeof(controller_session_));
  controller_desired_revision_ = 0;
  controller_snapshot_digest_ = {};
  overlay_generation_order_ = {};
  session_requires_snapshot_ = true;
  last_commit_digest_present_ = false;
  last_clear_digest_present_ = false;
#endif
}

bool ReceiverRuntime::local_render_failed_if_current(
    std::uint32_t generation) {
  if (!local_render_still_valid(generation)) return false;
  clear_foreground_visibility(true);
#if LEDGRID_ENABLE_LOCAL_BACKGROUND
  session_requires_snapshot_ = true;
#endif
  base_mode_ = BaseMode::StartupFallback;
  ++render_generation_;
  foreground_state_ = ForegroundState::Cleared;
  transition_reason_ = BaseTransitionReason::LocalRenderFailure;
  last_result_ = ReceiverOperationResult::RenderFailed;
  context_state_ = PresentationContextState::None;
  staged_context_ = {};
  active_context_ = {};
  active_context_present_ = false;
  cadence_initialized_ = false;
  return true;
}

bool ReceiverRuntime::local_frame_due(std::uint64_t now_us) {
  if (base_mode_ != BaseMode::LocalBackground) return false;
  if (force_local_refresh_ || !cadence_initialized_) return true;
  const std::uint64_t deadline = cadence_origin_us_ +
      (next_cadence_index_ * 1000000ULL + local_.preferred_cadence_hz - 1U) /
          local_.preferred_cadence_hz;
  if (now_us < deadline) return false;
  const std::uint64_t elapsed = now_us - cadence_origin_us_;
  const std::uint64_t due_index =
      (elapsed * local_.preferred_cadence_hz) / 1000000ULL;
  const std::uint64_t late_periods =
      due_index > next_cadence_index_ ? due_index - next_cadence_index_ : 0;
  if (late_periods != 0) {
    const std::uint64_t remaining = UINT32_MAX - render_stats_.missed_cadence;
    render_stats_.missed_cadence += static_cast<std::uint32_t>(
        std::min<std::uint64_t>(late_periods, remaining));
  }
  return true;
}

bool ReceiverRuntime::local_frame_rendered_if_current(
    std::uint32_t generation,
    std::uint64_t now_us,
    std::uint64_t frame_scene_time_us,
    std::uint32_t render_us) {
  if (!local_render_still_valid(generation)) return false;
  render_stats_.cadence_deadlines = saturating_increment(render_stats_.cadence_deadlines);
  render_stats_.rendered_frames = saturating_increment(render_stats_.rendered_frames);
  render_stats_.last_render_us = static_cast<std::uint16_t>(
      std::min<std::uint32_t>(render_us, UINT16_MAX));
  render_stats_.max_render_us = std::max(
      render_stats_.max_render_us, render_stats_.last_render_us);
  render_stats_.last_frame_scene_time_us = frame_scene_time_us;
  if (!cadence_initialized_ || force_local_refresh_) {
    cadence_origin_us_ = now_us;
    next_cadence_index_ = 1;
    cadence_initialized_ = true;
  } else {
    const std::uint64_t elapsed = now_us - cadence_origin_us_;
    next_cadence_index_ =
        (elapsed * local_.preferred_cadence_hz) / 1000000ULL + 1U;
  }
  force_local_refresh_ = false;
  return true;
}

void ReceiverRuntime::request_local_refresh() {
  if (base_mode_ == BaseMode::LocalBackground) force_local_refresh_ = true;
}

bool ReceiverRuntime::service_foreground(std::uint64_t local_monotonic_us) {
#if !LEDGRID_ENABLE_LOCAL_BACKGROUND
  (void)local_monotonic_us;
  return false;
#else
  bool changed = false;
  if (pending_overlay_present_ &&
      pending_present_at_scene_time_us_ <= scene_time_us(local_monotonic_us)) {
    activate_pending_overlay(local_monotonic_us);
    changed = true;
  }
  if (committed_overlay_present_ && committed_has_lease_ &&
      local_monotonic_us >= committed_lease_deadline_us_) {
    clear_foreground_visibility(false);
    // Expiry destroys the authoritative committed pixels, so deltas cannot
    // resume until a full snapshot (or a newer authoritative clear) arrives.
    session_requires_snapshot_ = true;
    last_overlay_result_ = OverlayOperationResult::LeaseExpired;
    overlay_stats_.expirations = saturating_increment(overlay_stats_.expirations);
    changed = true;
  }
  return changed;
#endif
}

SparseOverlayStatus ReceiverRuntime::overlay_status(
    std::uint64_t local_monotonic_us) const {
  SparseOverlayStatus status{};
  status.result = last_overlay_result_;
#if LEDGRID_ENABLE_LOCAL_BACKGROUND
  status.update_kind = overlay_patch_order_.update_kind;
  status.expected_patches = overlay_patch_order_.expected_patches;
  status.accepted_patches = overlay_patch_order_.accepted_patches;
  // Coverage describes the plane that is currently visible, never the staged
  // replacement. Staged binding/schedule fields below remain independently
  // observable while the prior committed coverage is being composited.
  status.committed_coverage_pixels = committed_coverage_pixels_;
  status.committed_generation = overlay_generation_order_.committed_generation;
  const bool staged = overlay_generation_order_.has_staged_generation;
  status.staged_generation =
      staged ? overlay_generation_order_.staged_generation : 0;
  if (staged) {
    status.scene_revision = staged_scene_revision_;
    status.scene_epoch = staged_scene_epoch_;
    status.base_revision = staged_base_revision_;
    status.present_at_scene_time_us =
        pending_overlay_present_ ? pending_present_at_scene_time_us_ : 0;
    status.lease_ms = staged_lease_ms_;
    if (staged_lease_ms_ != 0) {
      if (pending_overlay_present_) {
        // The visible lease starts atomically with scheduled activation.
        status.lease_remaining_ms = staged_lease_ms_;
      } else {
        const std::uint64_t lease_us =
            static_cast<std::uint64_t>(staged_lease_ms_) * 1000ULL;
        const std::uint64_t deadline =
            UINT64_MAX - staged_started_local_us_ < lease_us
                ? UINT64_MAX : staged_started_local_us_ + lease_us;
        if (deadline > local_monotonic_us) {
          status.lease_remaining_ms = static_cast<std::uint32_t>(
              std::min<std::uint64_t>(
                  UINT32_MAX,
                  (deadline - local_monotonic_us + 999U) / 1000U));
        }
      }
    }
  } else {
    status.scene_revision = committed_scene_revision_;
    status.scene_epoch = committed_scene_epoch_;
    status.base_revision = committed_base_revision_;
    status.present_at_scene_time_us = committed_present_at_scene_time_us_;
    status.lease_ms = committed_lease_ms_;
    if (committed_overlay_present_ && committed_has_lease_ &&
        committed_lease_deadline_us_ > local_monotonic_us) {
      const std::uint64_t remaining_us =
          committed_lease_deadline_us_ - local_monotonic_us;
      status.lease_remaining_ms = static_cast<std::uint32_t>(
          std::min<std::uint64_t>(UINT32_MAX,
                                  (remaining_us + 999U) / 1000U));
    }
  }
  if (controller_session_present_) {
    std::memcpy(status.session, controller_session_, kControllerSessionBytes);
  }
#else
  (void)local_monotonic_us;
#endif
  return status;
}

bool ReceiverRuntime::composite_foreground(
    const std::uint8_t* base,
    std::size_t pixels,
    std::uint8_t* output,
    std::size_t output_size) const {
  if (base == nullptr || output == nullptr || pixels == 0 ||
      pixels > kContractLocalPixels || output_size < pixels * 3U) {
    return false;
  }
  std::memcpy(output, base, pixels * 3U);
#if LEDGRID_ENABLE_LOCAL_BACKGROUND
  if (!committed_overlay_present_) return true;
  for (std::size_t pixel = 0; pixel < pixels; ++pixel) {
    if (overlay_coverage_[committed_plane_][pixel] == 0) continue;
    const std::uint8_t* rgba = overlay_rgba_[committed_plane_] + pixel * 4U;
    const PremultipliedRgba8 foreground{rgba[0], rgba[1], rgba[2], rgba[3]};
    source_over_opaque_rgb8(base + pixel * 3U, foreground,
                            output + pixel * 3U);
  }
#endif
  return true;
}

void ReceiverRuntime::foreground_composited(std::uint32_t composite_us) {
  foreground_refresh_pending_ = false;
  overlay_stats_.composite_frames =
      saturating_increment(overlay_stats_.composite_frames);
  overlay_stats_.last_composite_us = static_cast<std::uint16_t>(
      std::min<std::uint32_t>(composite_us, UINT16_MAX));
  overlay_stats_.max_composite_us = std::max(
      overlay_stats_.max_composite_us, overlay_stats_.last_composite_us);
}

std::uint64_t ReceiverRuntime::scene_time_us(
    std::uint64_t local_monotonic_us) const {
  // A newer context may be staging while the prior committed context remains
  // authoritative. Do not suspend or rewind receiver-local playback until the
  // replacement commits atomically.
  if (!active_context_present_)
    return 0;
  if (local_monotonic_us <= context_committed_local_us_)
    return active_context_.present_at_scene_time_us;
  const std::uint64_t delta = local_monotonic_us - context_committed_local_us_;
  if (UINT64_MAX - active_context_.present_at_scene_time_us < delta)
    return UINT64_MAX;
  return active_context_.present_at_scene_time_us + delta;
}

std::uint8_t apply_luminance_q8_8(
    std::uint8_t channel, std::uint16_t factor_q8_8) {
  const std::uint32_t value =
      (static_cast<std::uint32_t>(channel) * factor_q8_8 + 128U) / 256U;
  return static_cast<std::uint8_t>(std::min<std::uint32_t>(value, 255U));
}

bool render_compiled_rainbow(
    std::uint64_t elapsed_us,
    const LocalBackgroundParameters& parameters,
    std::uint16_t luminance_q8_8,
    std::uint8_t strip_count,
    std::uint16_t leds_per_strip,
    std::uint8_t* output,
    std::size_t output_size) {
  const std::size_t required =
      static_cast<std::size_t>(strip_count) * leds_per_strip * 3U;
  if (output == nullptr || strip_count == 0 || leds_per_strip == 0 ||
      output_size < required || luminance_q8_8 > kQ8_8One) return false;
  const std::uint16_t motion = static_cast<std::uint16_t>(
      ((elapsed_us % kStartupRainbowCycleUs) * kHueCycleSteps) /
      kStartupRainbowCycleUs);
  const std::uint16_t seed_phase = parameters.common_seed % kHueCycleSteps;
  for (std::uint8_t strip = 0; strip < strip_count; ++strip) {
    const std::uint32_t global_strip = parameters.global_strip_offset + (
        parameters.reverse_local_strip_order
            ? static_cast<std::uint32_t>(strip_count - 1U - strip)
            : strip);
    for (std::uint16_t led = 0; led < leds_per_strip; ++led) {
      const std::uint16_t spatial = static_cast<std::uint16_t>(
          ((global_strip + led) % kStartupRainbowPeriodPixels) * kSpatialStep);
      const std::uint16_t hue = static_cast<std::uint16_t>(
          (spatial + seed_phase + kHueCycleSteps - motion) % kHueCycleSteps);
      const std::size_t offset =
          (static_cast<std::size_t>(strip) * leds_per_strip + led) * 3U;
      hue_to_rgb(hue, output + offset);
      output[offset] = apply_luminance_q8_8(output[offset], luminance_q8_8);
      output[offset + 1] = apply_luminance_q8_8(output[offset + 1], luminance_q8_8);
      output[offset + 2] = apply_luminance_q8_8(output[offset + 2], luminance_q8_8);
    }
  }
  return true;
}

ReceiverRenderTicket capture_render_ticket(
    const ReceiverRuntime& runtime, const ReceiverOutputState& output) {
  ReceiverRenderTicket ticket{};
  ticket.owner = runtime.base_mode();
  ticket.ownership_generation = runtime.render_generation();
  ticket.foreground_revision = runtime.foreground_revision();
  ticket.output_revision = output.revision();
  ticket.output = output.configuration();
  return ticket;
}

bool render_ticket_still_current(
    const ReceiverRuntime& runtime,
    const ReceiverOutputState& output,
    const ReceiverRenderTicket& ticket) {
  return (ticket.owner == BaseMode::StartupFallback ||
          ticket.owner == BaseMode::LocalBackground) &&
         runtime.base_mode() == ticket.owner &&
         runtime.render_generation() == ticket.ownership_generation &&
         runtime.foreground_revision() == ticket.foreground_revision &&
         output.revision() == ticket.output_revision;
}

PhysicalSubmitResult submit_rendered_frame_if_current(
    const ReceiverRuntime& runtime,
    const ReceiverOutputState& output,
    const ReceiverRenderTicket& ticket,
    PhysicalSubmitCallback submit,
    void* submit_context) {
  if (!render_ticket_still_current(runtime, output, ticket)) {
    return PhysicalSubmitResult::Stale;
  }
  if (submit == nullptr || !submit(submit_context, ticket.output)) {
    return PhysicalSubmitResult::DriverRejected;
  }
  return PhysicalSubmitResult::Submitted;
}

}  // namespace ledgrid
