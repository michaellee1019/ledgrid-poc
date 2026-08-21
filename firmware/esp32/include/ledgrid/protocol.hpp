#pragma once

#include <cstddef>
#include <cstdint>

#include "ledgrid/animation_pipeline_contract.hpp"

namespace ledgrid {

constexpr std::uint8_t kStatusProtocolVersion = 2;
constexpr std::size_t kStatusBytesV2 = 64;
constexpr std::uint8_t kStatusProtocolVersionV3 = 3;
constexpr std::size_t kStatusBytesV3 = 320;
constexpr std::uint8_t kStatusProtocolVersionV4 = 4;
constexpr std::size_t kStatusBytesV4 = 416;
constexpr std::uint8_t kStatusProtocolVersionV5 = 5;
constexpr std::size_t kStatusBytesV5 = 768;
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
};

// Status v3 retains every v2 field at byte offsets 5..63. The extended fields
// are intentionally fixed-width so old hosts can keep parsing the v2 prefix.
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

bool command_may_claim_base(ReceiverCommand command);
ReceiverDispatchDecision classify_receiver_dispatch(
    const std::uint8_t* command,
    std::size_t size,
    std::size_t active_rgb_bytes,
    BaseMode base_mode,
    bool local_background_enabled,
    bool installation_profiles_enabled = false);
bool receiver_packet_crc_valid(
    const std::uint8_t* packet,
    std::size_t packet_size,
    std::uint16_t* computed_crc = nullptr);
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
bool parse_logical_receiver_id(
    const std::uint8_t* command,
    std::size_t size,
    std::uint8_t current_id,
    std::uint8_t* logical_receiver_id);

}  // namespace ledgrid
