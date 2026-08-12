#pragma once

#include <cstddef>
#include <cstdint>

namespace ledgrid {

// Phase-1-only receiver contract. These definitions intentionally are not
// connected to main.cpp's command dispatcher or display task yet.
constexpr std::uint8_t kAnimationPipelineProtocolVersion = 1;
constexpr std::size_t kAnimationPipelineMaxTransactionBytes = 4096;
constexpr std::size_t kAnimationPipelineCrcBytes = 2;
constexpr std::size_t kControllerSessionBytes = 16;
constexpr std::size_t kSnapshotDigestBytes = 32;
constexpr std::size_t kWireCommandBytes = 1;
constexpr std::size_t kWireVersionBytes = 1;
constexpr std::size_t kWireU16Bytes = 2;
constexpr std::size_t kWireU32Bytes = 4;
constexpr std::size_t kWireU64Bytes = 8;

constexpr std::uint16_t kContractLocalStrips = 8;
constexpr std::uint16_t kContractLedsPerStrip = 138;
constexpr std::uint32_t kContractLocalPixels =
    static_cast<std::uint32_t>(kContractLocalStrips) *
    kContractLedsPerStrip;
constexpr std::size_t kPremultipliedRgbaBytesPerPixel = 4;
constexpr std::size_t kContractLocalRgbaBytes =
    kContractLocalPixels * kPremultipliedRgbaBytesPerPixel;

enum class BaseMode : std::uint8_t {
  StartupFallback = 0,
  LocalBackground = 1,
  HostFullScene = 2,
};

enum class ForegroundState : std::uint8_t {
  Cleared = 0,
  Staging = 1,
  Active = 2,
};

enum class MaintenanceState : std::uint8_t {
  Inactive = 0,
  AssetTransfer = 1,
  CalibrationTransfer = 2,
};

// Current receiver recovery condition. Per-command rejection details belong in
// OverlayOperationResult and do not replace this observable state.
enum class ReceiverFailureState : std::uint8_t {
  None = 0,
  ProtocolRejected = 1,
  OverlayLeaseExpired = 2,
  AssetUnavailable = 3,
  AssetRejected = 4,
  NativeCallbackFailed = 5,
  NativeWatchdogReset = 6,
  PayloadQuarantined = 7,
  GeometryMismatch = 8,
  StorageFailure = 9,
  DegradedAgreement = 10,
};

// The IDs occupy a new namespace and remain dormant until the receiver state
// machine is implemented. Presentation-context commands reserve 0x21-0x23;
// foreground traffic starts at 0x30 so later asset commands need not collide.
enum class AnimationPipelineCommand : std::uint8_t {
  ControllerSessionBegin = 0x20,
  OverlayBegin = 0x30,
  OverlayPatch = 0x31,
  OverlayCommit = 0x32,
  OverlayClear = 0x33,
  OverlayRenew = 0x34,
};

enum class OverlayFormat : std::uint8_t {
  PremultipliedRgba8 = 1,
};

enum class OverlayUpdateKind : std::uint8_t {
  FullSnapshot = 1,
  Delta = 2,
};

enum class OverlayOperationResult : std::uint8_t {
  None = 0,
  Ok = 1,
  Idempotent = 2,
  UnsupportedVersion = 3,
  UnsupportedFormat = 4,
  InvalidSize = 5,
  OutOfBounds = 6,
  StaleSession = 7,
  StaleRevision = 8,
  StaleGeneration = 9,
  GenerationConflict = 10,
  PriorGenerationMismatch = 11,
  PatchOrder = 12,
  PatchOverlap = 13,
  PatchConflict = 14,
  BaseBindingMismatch = 15,
  Incomplete = 16,
  LeaseExpired = 17,
  InvalidState = 18,
  CounterExhausted = 19,
};

enum class CounterRelation : std::int8_t {
  Stale = -1,
  Equal = 0,
  Newer = 1,
};

// All wire integers are unsigned big-endian. Controller session IDs and
// digests are opaque bytes. Header sizes include the command byte and version,
// but exclude payload bytes and the trailing CRC-16/CCITT-FALSE.
//
// CONTROLLER_SESSION_BEGIN:
//   command:u8, version:u8, session:bytes[16], desired_revision:u64,
//   authoritative_snapshot_digest:bytes[32]
// OVERLAY_BEGIN:
//   command:u8, version:u8, session:bytes[16], generation:u64,
//   prior_generation:u64, scene_revision:u64, scene_epoch:u64,
//   base_revision:u64, format:u8, update_kind:u8, expected_patches:u16,
//   lease_ms:u32
// OVERLAY_PATCH:
//   command:u8, version:u8, session:bytes[16], generation:u64,
//   start:u16, count:u16, premultiplied_rgba:bytes[count * 4]
// OVERLAY_COMMIT:
//   command:u8, version:u8, session:bytes[16], generation:u64,
//   scene_epoch:u64, base_revision:u64, present_at_scene_time:u64
// OVERLAY_CLEAR:
//   command:u8, version:u8, session:bytes[16], generation:u64,
//   scene_revision:u64
// OVERLAY_RENEW:
//   command:u8, version:u8, session:bytes[16], generation:u64, lease_ms:u32
constexpr std::size_t kControllerSessionBeginHeaderBytes =
    kWireCommandBytes + kWireVersionBytes + kControllerSessionBytes +
    kWireU64Bytes + kSnapshotDigestBytes;
constexpr std::size_t kOverlayBeginHeaderBytes =
    kWireCommandBytes + kWireVersionBytes + kControllerSessionBytes +
    5U * kWireU64Bytes + 2U + kWireU16Bytes + kWireU32Bytes;
constexpr std::size_t kOverlayPatchHeaderBytes =
    kWireCommandBytes + kWireVersionBytes + kControllerSessionBytes +
    kWireU64Bytes + 2U * kWireU16Bytes;
constexpr std::size_t kOverlayCommitHeaderBytes =
    kWireCommandBytes + kWireVersionBytes + kControllerSessionBytes +
    4U * kWireU64Bytes;
constexpr std::size_t kOverlayClearHeaderBytes =
    kWireCommandBytes + kWireVersionBytes + kControllerSessionBytes +
    2U * kWireU64Bytes;
constexpr std::size_t kOverlayRenewHeaderBytes =
    kWireCommandBytes + kWireVersionBytes + kControllerSessionBytes +
    kWireU64Bytes + kWireU32Bytes;

constexpr std::size_t kMaxRgbaPixelsPerPatch =
    (kAnimationPipelineMaxTransactionBytes - kOverlayPatchHeaderBytes -
     kAnimationPipelineCrcBytes) /
    kPremultipliedRgbaBytesPerPixel;
constexpr std::size_t kContractFullSnapshotPatchCount =
    (kContractLocalPixels + kMaxRgbaPixelsPerPatch - 1U) /
    kMaxRgbaPixelsPerPatch;

static_assert(kAnimationPipelineCrcBytes == 2, "wire CRC width changed");
static_assert(kControllerSessionBeginHeaderBytes == 58,
              "session-begin wire header changed");
static_assert(kOverlayBeginHeaderBytes == 66, "begin wire header changed");
static_assert(kOverlayPatchHeaderBytes == 30, "patch wire header changed");
static_assert(kOverlayCommitHeaderBytes == 50, "commit wire header changed");
static_assert(kOverlayClearHeaderBytes == 34, "clear wire header changed");
static_assert(kOverlayRenewHeaderBytes == 30, "renew wire header changed");
static_assert(kMaxRgbaPixelsPerPatch == 1016, "patch ceiling changed");
static_assert(kContractLocalPixels == 1104, "receiver geometry changed");
static_assert(kContractLocalRgbaBytes == 4416, "receiver RGBA size changed");
static_assert(kContractFullSnapshotPatchCount == 2,
              "full snapshot chunking changed");
static_assert(kOverlayPatchHeaderBytes +
                      kMaxRgbaPixelsPerPatch *
                          kPremultipliedRgbaBytesPerPixel +
                      kAnimationPipelineCrcBytes ==
                  kAnimationPipelineMaxTransactionBytes,
              "maximum patch must exactly fill one SPI transaction");

struct PremultipliedRgba8 {
  std::uint8_t red;
  std::uint8_t green;
  std::uint8_t blue;
  std::uint8_t alpha;
};

struct OverlayPatchHeader {
  std::uint8_t controller_session[kControllerSessionBytes] = {};
  std::uint64_t generation = 0;
  std::uint16_t start = 0;
  std::uint16_t count = 0;
};

struct Digest256 {
  std::uint8_t bytes[kSnapshotDigestBytes] = {};
};

struct OverlayGenerationOrderState {
  std::uint64_t committed_generation = 0;
  std::uint64_t staged_generation = 0;
  Digest256 staged_operation_digest{};
  bool has_staged_generation = false;
};

struct OverlayPatchOrderState {
  std::uint16_t expected_patches = 0;
  std::uint16_t accepted_patches = 0;
  std::uint16_t last_start = 0;
  std::uint16_t last_count = 0;
  Digest256 last_content_digest{};
  OverlayUpdateKind update_kind = OverlayUpdateKind::FullSnapshot;
  bool has_last_patch = false;
};

constexpr std::uint16_t full_snapshot_patch_start(std::size_t patch_index) {
  return static_cast<std::uint16_t>(patch_index * kMaxRgbaPixelsPerPatch);
}

constexpr std::uint16_t full_snapshot_patch_pixels(std::size_t patch_index) {
  const std::size_t start = patch_index * kMaxRgbaPixelsPerPatch;
  return start >= kContractLocalPixels
             ? 0
             : static_cast<std::uint16_t>(
                   (kContractLocalPixels - start) > kMaxRgbaPixelsPerPatch
                       ? kMaxRgbaPixelsPerPatch
                       : (kContractLocalPixels - start));
}

CounterRelation compare_monotonic_counter(
    std::uint64_t candidate,
    std::uint64_t current);

OverlayOperationResult validate_overlay_version_format(
    std::uint8_t version,
    OverlayFormat format);

OverlayOperationResult validate_overlay_session_revision(
    bool controller_session_matches,
    std::uint64_t candidate_scene_revision,
    std::uint64_t current_scene_revision);

OverlayOperationResult validate_overlay_generation_begin(
    const OverlayGenerationOrderState& state,
    std::uint64_t generation,
    std::uint64_t prior_generation,
    const Digest256& operation_digest);

OverlayOperationResult accept_overlay_patch(
    OverlayPatchOrderState* state,
    std::uint16_t start,
    std::uint16_t count,
    const Digest256& content_digest);

OverlayOperationResult validate_overlay_commit(
    const OverlayPatchOrderState& state,
    bool base_binding_matches,
    bool lease_expired);

bool encode_overlay_patch_header(
    const OverlayPatchHeader& header,
    std::uint8_t* output,
    std::size_t output_size);

std::uint16_t animation_pipeline_crc16_ccitt(
    const std::uint8_t* data,
    std::size_t size);

std::uint8_t scale_u8_fixed(std::uint8_t value, std::uint8_t factor);
PremultipliedRgba8 scale_premultiplied_rgba8(
    PremultipliedRgba8 pixel,
    std::uint8_t opacity);
PremultipliedRgba8 source_over_premultiplied_rgba8(
    PremultipliedRgba8 bottom,
    PremultipliedRgba8 top);
void source_over_opaque_rgb8(
    const std::uint8_t base[3],
    PremultipliedRgba8 foreground,
    std::uint8_t output[3]);

bool logical_to_global_pixel(
    std::uint16_t global_strip,
    std::uint16_t led,
    std::uint16_t global_strips,
    std::uint16_t leds_per_strip,
    std::uint32_t* global_flat_index);

bool logical_to_local_pixel(
    std::uint16_t global_strip,
    std::uint16_t led,
    std::uint16_t global_strip_offset,
    std::uint16_t local_strips,
    std::uint16_t leds_per_strip,
    std::uint32_t* local_flat_index);

}  // namespace ledgrid
