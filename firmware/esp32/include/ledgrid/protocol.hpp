#pragma once

#include <cstddef>
#include <cstdint>

#include "ledgrid/animation_pipeline_contract.hpp"
#include "ledgrid/native_module.hpp"

namespace ledgrid {

constexpr std::uint8_t kStatusProtocolVersion = 2;
// The original 64 bytes were fully assigned, so stagger_phases starts a new
// word past them. Hosts read the snapshot by offset and treat a zero here as
// "firmware predates the field" rather than as a legal phase count.
constexpr std::size_t kStatusBytesV2 = 68;
constexpr std::uint8_t kStatusProtocolVersionV3 = 3;
constexpr std::size_t kStatusBytesV3 = 320;
constexpr std::uint8_t kStatusProtocolVersionV4 = 4;
constexpr std::size_t kStatusBytesV4 = 416;
constexpr std::uint8_t kStatusProtocolVersionV5 = 5;
constexpr std::size_t kStatusBytesV5 = 768;
constexpr std::uint8_t kStatusProtocolVersionV6 = 6;
constexpr std::size_t kStatusBytesV6 = 1216;
// ESP32-S3 SPI slave DMA requires every Host write to be a multiple of one
// 32-bit word.  The transport envelope carries an exact semantic length and
// CRC-covered zero padding so command parsers never mistake DMA padding for
// command data.  Receivers continue to accept legacy packets during rolling
// deployment; Hosts only emit the envelope after discovering its capability.
constexpr std::uint8_t kAlignedEnvelopeVersion = 1;
constexpr std::size_t kAlignedEnvelopeHeaderBytes = 4;
constexpr std::size_t kSpiDmaAlignmentBytes = 4;
constexpr std::size_t kAlignedEnvelopeMaxSemanticBytes =
    kAnimationPipelineMaxTransactionBytes - kAlignedEnvelopeHeaderBytes -
    kAnimationPipelineCrcBytes;
constexpr std::size_t kInstallationProfilePreflightBytes = 69;
constexpr std::size_t kInstallationProfileBeginBytes = 81;
constexpr std::size_t kInstallationProfileChunkHeaderBytes = 5;
constexpr std::size_t kInstallationProfileFinalizeBytes = 65;
constexpr std::size_t kInstallationProfileVerifyBytes = 65;
constexpr std::size_t kInstallationProfileActivateBytes = 73;
constexpr std::size_t kInstallationProfileRestoreBytes = 204;
constexpr std::size_t kInstallationProfileAbortBytes = 1;

enum class ReceiverCommand : std::uint8_t {
  SetPixel = 0x01,
  SetBrightness = 0x02,
  Show = 0x03,
  Clear = 0x04,
  SetRange = 0x05,
  SetAll = 0x06,
  Config = 0x07,
  StatusQuery = 0x08,
  SetLaneMask = 0x09,
  SetStagger = 0x0A,
  AlignedEnvelope = 0x0B,
  LocalBackgroundStart = 0x10,
  LocalBackgroundStop = 0x11,
  LocalBackgroundParameters = 0x12,
  ControllerSessionBegin = 0x20,
  PresentationContextBegin = 0x21,
  PresentationContextSet = 0x22,
  PresentationContextCommit = 0x23,
  OverlayBegin = 0x30,
  OverlayPatch = 0x31,
  OverlayCommit = 0x32,
  OverlayClear = 0x33,
  OverlayRenew = 0x34,
  OverlayPatchBatch = 0x35,
  InstallationProfilePreflight = 0x40,
  InstallationProfileBegin = 0x41,
  InstallationProfileChunk = 0x42,
  InstallationProfileFinalize = 0x43,
  InstallationProfileVerify = 0x44,
  InstallationProfileActivate = 0x45,
  InstallationProfileRestore = 0x46,
  InstallationProfileAbort = 0x47,
  NativeModuleProbe = 0x50,
  NativeModulePreflight = 0x51,
  NativeModuleBegin = 0x52,
  NativeModuleChunk = 0x53,
  NativeModuleFinalize = 0x54,
  NativeModuleVerify = 0x55,
  NativeModuleActivate = 0x56,
  NativeModuleStop = 0x57,
  NativeModuleParameters = 0x58,
  NativeModuleRemove = 0x59,
  NativeModuleAbort = 0x5A,
  NativeModuleRestore = 0x5B,
  NativeModuleQuarantineClear = 0x5C,
  Ping = 0xFF,
};

enum ReceiverCapability : std::uint32_t {
  kCapabilityStaticLocalBackground = 1U << 0U,
  kCapabilityPresentationContextV1 = 1U << 1U,
  kCapabilityStatusV3 = 1U << 2U,
  kCapabilityExplicitBaseOwnership = 1U << 3U,
  kCapabilitySparseOverlayV1 = 1U << 4U,
  kCapabilitySparseOverlayBatchV1 = 1U << 5U,
  kCapabilityInstallationProfileV1 = 1U << 6U,
  kCapabilityStatusV5 = 1U << 7U,
  kCapabilityStatusV6 = 1U << 8U,
  kCapabilityNativeModuleV2 = 1U << 9U,
  kCapabilityNativeModuleCacheV1 = 1U << 10U,
  kCapabilityNativeTypedParametersV1 = 1U << 11U,
  kCapabilityNativeQuarantineV1 = 1U << 12U,
  kCapabilityNativeGuardedLoaderV1 = 1U << 13U,
  kCapabilityAlignedEnvelopeV1 = 1U << 14U,
};

struct ReceiverPacketPayload {
  const std::uint8_t* data = nullptr;
  std::size_t size = 0;
  bool aligned_envelope = false;
};

enum class ReceiverOperationResult : std::uint8_t {
  None = 0,
  Ok = 1,
  Unsupported = 2,
  InvalidSize = 3,
  InvalidCommand = 4,
  InvalidState = 5,
  InvalidContext = 6,
  DigestMismatch = 7,
  RenderFailed = 8,
  StaleRevision = 9,
  Conflict = 10,
};

enum class BaseTransitionReason : std::uint8_t {
  Boot = 0,
  LocalStart = 1,
  LocalStop = 2,
  HostTakeover = 3,
  ReceiverRestart = 4,
  LocalRenderFailure = 5,
};

enum class PresentationContextState : std::uint8_t {
  None = 0,
  Staging = 1,
  Ready = 2,
  Active = 3,
};

enum class ReceiverDispatchRoute : std::uint8_t {
  Reject = 0,
  StatusQuery = 1,
  Runtime = 2,
  Operational = 3,
  HostFullFrame = 4,
  InstallationProfile = 5,
  NativeModule = 6,
};

enum class InstallationProfileResult : std::uint8_t {
  None = 0, Ok = 1, Unsupported = 2, InvalidSize = 3, InvalidState = 4,
  InvalidToken = 5, InvalidOffset = 6, DigestMismatch = 7,
  InvalidProfile = 8, WrongDevice = 9, WrongGeometry = 10,
  StorageError = 11, NoSpace = 12, NotFound = 13, Conflict = 14,
  Pinned = 15, IntegrityError = 16,
};

enum class InstallationProfileTransferState : std::uint8_t {
  Idle = 0, PreflightReady = 1, Receiving = 2, Finalizing = 3,
  Staged = 4, Failed = 5,
};

// Portable command-envelope policy shared by native tests and the live SPI
// dispatcher. It validates complete payload sizes before mutable handling and
// makes frame-publication/base-ownership effects auditable.
struct ReceiverDispatchDecision {
  ReceiverDispatchRoute route = ReceiverDispatchRoute::Reject;
  ReceiverOperationResult result = ReceiverOperationResult::InvalidCommand;
  bool publishes_host_frame = false;
  bool may_claim_base = false;
};

// Tracks the command identity paired with the status-v3 operation sequence.
// Exhaustion is fail-closed: callers must not dispatch when begin() returns
// false, so a sequence value can never be reused for a different operation.
class ReceiverOperationTracker {
 public:
  ReceiverOperationTracker() = default;
  ReceiverOperationTracker(
      std::uint32_t sequence, std::uint8_t last_processed_command)
      : sequence_(sequence),
        last_processed_command_(last_processed_command) {}

  bool begin(std::uint8_t command);
  std::uint32_t sequence() const { return sequence_; }
  std::uint8_t last_processed_command() const {
    return last_processed_command_;
  }
  bool exhausted() const { return sequence_ == UINT32_MAX; }

 private:
  std::uint32_t sequence_ = 0;
  std::uint8_t last_processed_command_ = 0;
};

struct ReceiverStatusV2 {
  std::uint8_t flags = 0;
  std::uint8_t active_strips = 0;
  std::uint8_t lane_mask = 0xFF;
  std::uint16_t leds_per_strip = 0;
  std::uint16_t queued_transactions = 0;
  std::uint32_t packets = 0;
  std::uint32_t crc_errors = 0;
  std::uint32_t crc_ok_packets = 0;
  std::uint32_t frames_accepted = 0;
  std::uint32_t frames_displayed = 0;
  std::uint32_t frames_superseded = 0;
  std::uint32_t publish_drops = 0;
  std::uint32_t spi_queue_errors = 0;
  std::uint16_t last_crc_us = 0;
  std::uint16_t last_copy_us = 0;
  std::uint16_t last_encode_us = 0;
  std::uint16_t last_show_us = 0;
  std::uint32_t last_accepted_sequence = 0;
  std::uint32_t last_displayed_sequence = 0;
  std::uint32_t display_errors = 0;
  std::uint8_t stagger_phases = 1;
};

// Status v3 retains the v2 core fields at byte offsets 5..63 and the lane mask
// at byte 7. Capabilities already occupy bytes 64..67, so stagger_phases uses
// reserved byte 314 without shifting any established v3-v5 field.
struct ReceiverStatusV3 : ReceiverStatusV2 {
  std::uint32_t capabilities = 0;
  std::uint8_t base_mode = 0;
  std::uint8_t foreground_state = 0;
  std::uint8_t maintenance_state = 0;
  BaseTransitionReason transition_reason = BaseTransitionReason::Boot;
  ReceiverOperationResult last_result = ReceiverOperationResult::None;
  PresentationContextState context_state = PresentationContextState::None;
  std::uint16_t component_id = 0;
  std::uint16_t preferred_cadence_hz = 0;
  std::uint16_t luminance_q8_8 = 256;
  std::uint32_t global_strip_offset = 0;
  std::uint32_t common_seed = 0;
  std::uint64_t scene_epoch = 0;
  std::uint64_t active_context_scene_revision = 0;
  std::uint64_t active_vibe_revision = 0;
  std::uint64_t active_modifier_revision = 0;
  std::uint32_t cadence_deadlines = 0;
  std::uint32_t rendered_frames = 0;
  std::uint32_t missed_cadence = 0;
  std::uint16_t last_render_us = 0;
  std::uint16_t max_render_us = 0;
  std::uint64_t last_frame_scene_time_us = 0;
  std::uint8_t active_context_digest[32] = {};
  std::uint8_t active_vibe_digest[32] = {};
  std::uint8_t active_modifier_digest[32] = {};
  std::uint64_t staged_context_scene_revision = 0;
  std::uint8_t staged_context_digest[32] = {};
  std::uint8_t active_controller_session[16] = {};
  std::uint8_t staged_controller_session[16] = {};
  std::uint8_t logical_receiver_id = 0;
  std::uint8_t last_processed_command = 0;
  std::uint32_t operation_sequence = 0;
};

// Status v4 is negotiated only after a legacy-safe v3 query exposes the
// sparse-overlay capability. Bytes 0..319 remain an exact status-v3 prefix.
struct ReceiverStatusV4 : ReceiverStatusV3 {
  OverlayOperationResult overlay_result = OverlayOperationResult::None;
  OverlayUpdateKind overlay_update_kind = OverlayUpdateKind::FullSnapshot;
  std::uint16_t overlay_expected_patches = 0;
  std::uint16_t overlay_accepted_patches = 0;
  std::uint16_t overlay_committed_coverage_pixels = 0;
  std::uint64_t overlay_committed_generation = 0;
  std::uint64_t overlay_staged_generation = 0;
  std::uint64_t foreground_scene_revision = 0;
  std::uint64_t foreground_scene_epoch = 0;
  std::uint64_t foreground_base_revision = 0;
  std::uint64_t foreground_present_at_scene_time_us = 0;
  std::uint32_t overlay_lease_ms = 0;
  std::uint32_t overlay_lease_remaining_ms = 0;
  std::uint8_t overlay_session[kControllerSessionBytes] = {};
  std::uint32_t overlay_composite_frames = 0;
  std::uint16_t overlay_last_composite_us = 0;
  std::uint16_t overlay_max_composite_us = 0;
  std::uint32_t overlay_commits = 0;
  std::uint32_t overlay_expirations = 0;
};

struct InstallationProfileStatusV1 {
  InstallationProfileResult result = InstallationProfileResult::None;
  InstallationProfileTransferState transfer_state =
      InstallationProfileTransferState::Idle;
  std::uint8_t decoder_error = 0;
  std::uint8_t flags = 0;
  std::uint32_t capacity_bytes = 0;
  std::uint32_t used_bytes = 0;
  std::uint32_t free_bytes = 0;
  std::uint32_t reserve_bytes = 0;
  std::uint32_t reclaimable_bytes = 0;
  std::uint32_t received_bytes = 0;
  std::uint32_t total_bytes = 0;
  std::uint64_t state_generation = 0;
  std::uint64_t preflight_token = 0;
  std::uint8_t last_probe_payload_digest[32] = {};
  std::uint8_t transfer_global_id[32] = {};
  std::uint8_t transfer_payload_digest[32] = {};
  std::uint8_t active_global_id[32] = {};
  std::uint8_t active_payload_digest[32] = {};
  std::uint8_t staged_global_id[32] = {};
  std::uint8_t staged_payload_digest[32] = {};
  std::uint8_t rollback_global_id[32] = {};
  std::uint8_t rollback_payload_digest[32] = {};
  std::uint32_t writes = 0;
  std::uint32_t evictions = 0;
  std::uint16_t stages = 0;
  std::uint16_t verifies = 0;
  std::uint16_t activations = 0;
  std::uint16_t restores = 0;
};

struct ReceiverStatusV5 : ReceiverStatusV4 {
  InstallationProfileStatusV1 installation_profile{};
};

// Status v6 is negotiated only when the native-module capability is present.
// Bytes 0..767 remain an exact status-v5 prefix.
struct ReceiverStatusV6 : ReceiverStatusV5 {
  NativeModuleStatusV1 native_module{};
};

bool encode_receiver_status_v2(
    const ReceiverStatusV2& status,
    std::uint8_t* output,
    std::size_t output_size);

bool encode_receiver_status_v3(
    const ReceiverStatusV3& status,
    std::uint8_t* output,
    std::size_t output_size);
bool encode_receiver_status_v4(
    const ReceiverStatusV4& status,
    std::uint8_t* output,
    std::size_t output_size);
bool encode_receiver_status_v5(
    const ReceiverStatusV5& status,
    std::uint8_t* output,
    std::size_t output_size);
bool encode_receiver_status_v6(
    const ReceiverStatusV6& status,
    std::uint8_t* output,
    std::size_t output_size);

bool command_may_claim_base(ReceiverCommand command);
ReceiverDispatchDecision classify_receiver_dispatch(
    const std::uint8_t* command,
    std::size_t size,
    std::size_t active_rgb_bytes,
    BaseMode base_mode,
    bool local_background_enabled,
    bool installation_profiles_enabled = false,
    bool receiver_native_modules_enabled = false);
bool receiver_packet_crc_valid(
    const std::uint8_t* packet,
    std::size_t packet_size,
    std::uint16_t* computed_crc = nullptr);
bool decode_receiver_packet_payload(
    const std::uint8_t* packet,
    std::size_t packet_size,
    ReceiverPacketPayload* payload);
// Decode framing after receiver_packet_crc_valid() has already accepted this
// exact packet.  Keeping this separate avoids hashing every live frame twice.
bool decode_crc_valid_receiver_packet_payload(
    const std::uint8_t* packet,
    std::size_t packet_size,
    ReceiverPacketPayload* payload);
bool valid_status_query(const std::uint8_t* command, std::size_t size);
bool valid_status_query(
    const std::uint8_t* command,
    std::size_t size,
    bool sparse_overlay_enabled);
bool valid_status_query(
    const std::uint8_t* command,
    std::size_t size,
    bool sparse_overlay_enabled,
    bool installation_profiles_enabled);
bool valid_status_query(
    const std::uint8_t* command,
    std::size_t size,
    bool sparse_overlay_enabled,
    bool installation_profiles_enabled,
    bool receiver_native_modules_enabled);
bool parse_logical_receiver_id(
    const std::uint8_t* command,
    std::size_t size,
    std::uint8_t current_id,
    std::uint8_t* logical_receiver_id);
bool parse_receiver_topology(
    const std::uint8_t* command,
    std::size_t size,
    std::uint8_t current_id,
    std::uint16_t current_global_offset,
    std::uint8_t* logical_receiver_id,
    std::uint16_t* global_strip_offset);

}  // namespace ledgrid
