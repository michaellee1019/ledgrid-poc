#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>

#include "ledgrid/animation_pipeline_contract.hpp"
#include "ledgrid/native_background_abi_v2.h"

#ifndef LEDGRID_ENABLE_RECEIVER_NATIVE_MODULES
#define LEDGRID_ENABLE_RECEIVER_NATIVE_MODULES 0
#endif

namespace ledgrid {

class NativeModuleWatchdogGate {
 public:
  bool arm() {
    bool expected = false;
    return armed_.compare_exchange_strong(
        expected, true, std::memory_order_acq_rel);
  }
  bool cancel() { return armed_.exchange(false, std::memory_order_acq_rel); }
  bool expire() { return armed_.exchange(false, std::memory_order_acq_rel); }
  bool armed() const { return armed_.load(std::memory_order_acquire); }

 private:
  std::atomic<bool> armed_{false};
};

constexpr std::uint16_t kNativeModuleAbiV2 = 2;
constexpr std::uint8_t kNativeModuleTargetEsp32S3 = 1;
constexpr std::uint32_t kNativeModuleMaxPayloadBytes = 512U * 1024U;
constexpr std::uint32_t kNativeModuleCacheReserveBytes = 512U * 1024U;
constexpr std::uint32_t kNativeModuleWatchdogUs = 25000;
constexpr std::size_t kNativeModuleMaxParameterBytes = 1024;
constexpr std::uint8_t kNativeTypedParameterVersion = 1;
constexpr std::size_t kNativeModuleDescriptorWireBytes = 85;
constexpr std::size_t kNativeModuleProbeBytes = 33;
constexpr std::size_t kNativeModulePreflightBytes = 86;
constexpr std::size_t kNativeModuleBeginBytes = 94;
constexpr std::size_t kNativeModuleChunkHeaderBytes = 5;
constexpr std::size_t kNativeModuleFinalizeBytes = 65;
constexpr std::size_t kNativeModuleVerifyBytes = 65;
constexpr std::size_t kNativeModuleActivateHeaderBytes = 87;
constexpr std::size_t kNativeModuleParametersHeaderBytes = 71;
constexpr std::size_t kNativeModuleStopBytes = 1;
constexpr std::size_t kNativeModuleRemoveBytes = 65;
constexpr std::size_t kNativeModuleAbortBytes = 1;
constexpr std::size_t kNativeModuleRestoreBytes = 204;
constexpr std::size_t kNativeModuleQuarantineClearBytes = 33;
constexpr std::size_t kNativeModuleMaxChunkBytes =
    kAnimationPipelineMaxTransactionBytes - kAnimationPipelineCrcBytes -
    kNativeModuleChunkHeaderBytes;

enum class NativeModuleResult : std::uint8_t {
  None = 0,
  Ok = 1,
  Unsupported = 2,
  InvalidSize = 3,
  InvalidCommand = 4,
  InvalidState = 5,
  DigestMismatch = 6,
  WrongAbi = 7,
  WrongTarget = 8,
  WrongGeometry = 9,
  StorageError = 10,
  NoSpace = 11,
  NotFound = 12,
  Conflict = 13,
  InvalidToken = 14,
  Pinned = 15,
  IntegrityError = 16,
  InvalidParameters = 17,
  Quarantined = 18,
  LoadFailed = 19,
  EntrypointFailed = 20,
  InitializeFailed = 21,
  ContextFailed = 22,
  RenderFailed = 23,
  CleanupFailed = 24,
  UnloadFailed = 25,
  Watchdog = 26,
};

enum class NativeModuleTransferState : std::uint8_t {
  Idle = 0,
  PreflightReady = 1,
  Receiving = 2,
  Finalizing = 3,
  Staged = 4,
  Active = 5,
  Failed = 6,
  Quarantined = 7,
};

enum class NativeModulePhase : std::uint8_t {
  None = 0,
  Load = 1,
  Entrypoint = 2,
  Initialize = 3,
  ContextUpdate = 4,
  Render = 5,
  Cleanup = 6,
  Unload = 7,
};

struct NativeModuleTopology {
  bool configured = false;
  std::uint8_t logical_receiver_id = 0xFF;
  std::uint16_t global_strips = 0;
  std::uint8_t local_strips = 0;
  std::uint16_t leds_per_strip = 0;
  std::uint16_t global_strip_offset = 0;
  bool reverse_local_strip_order = false;
};

struct NativeModuleDescriptor {
  std::uint8_t bundle_digest[32] = {};
  std::uint8_t payload_digest[32] = {};
  std::uint32_t payload_size = 0;
  std::uint16_t abi = 0;
  std::uint8_t target = 0;
  std::uint16_t global_strips = 0;
  std::uint8_t local_strips = 0;
  std::uint16_t leds_per_strip = 0;
  std::uint16_t global_strip_offset = 0;
  std::uint16_t cadence_hz = 0;
  std::uint32_t parameter_schema_revision = 0;
  std::uint8_t flags = 0;
};

struct NativeModuleBinding {
  bool present = false;
  NativeModuleDescriptor descriptor{};
};

struct NativeModuleLedger {
  std::uint64_t generation = 0;
  NativeModuleBinding active{};
  NativeModuleBinding staged{};
  NativeModuleBinding rollback{};
};

struct NativeModuleParameters {
  ledgrid_native_parameter_v2 entries[
      LEDGRID_NATIVE_BACKGROUND_MAX_PARAMETERS] = {};
  std::uint8_t count = 0;
  std::uint8_t canonical[kNativeModuleMaxParameterBytes] = {};
  std::uint16_t canonical_size = 0;
  std::uint8_t digest[32] = {};
};

struct NativeModuleActivation {
  std::uint64_t scene_epoch_ns = 0;
  std::uint32_t deterministic_seed = 0;
  NativeModuleParameters parameters{};
};

struct NativeModulePresentation {
  ledgrid_native_vibe_v2 vibe{};
  ledgrid_native_modifier_v2 modifiers[
      LEDGRID_NATIVE_BACKGROUND_MAX_MODIFIERS] = {};
  ledgrid_native_modifier_view_v2 modifier_view{};
  ledgrid_native_profile_section_v2 profile_sections[
      LEDGRID_NATIVE_BACKGROUND_MAX_PROFILE_SECTIONS] = {};
  ledgrid_native_profile_view_v2 profile_view{};
};

struct NativeModuleRenderResult {
  bool changed = false;
  std::uint64_t next_deadline_scene_time_us = 0;
};

struct NativeModuleStatusV1 {
  NativeModuleResult result = NativeModuleResult::None;
  NativeModuleTransferState transfer_state = NativeModuleTransferState::Idle;
  NativeModulePhase watchdog_phase = NativeModulePhase::None;
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
  std::uint8_t transfer_bundle_digest[32] = {};
  std::uint8_t transfer_payload_digest[32] = {};
  std::uint8_t active_bundle_digest[32] = {};
  std::uint8_t active_payload_digest[32] = {};
  std::uint8_t staged_bundle_digest[32] = {};
  std::uint8_t staged_payload_digest[32] = {};
  std::uint8_t rollback_bundle_digest[32] = {};
  std::uint8_t rollback_payload_digest[32] = {};
  std::uint8_t quarantine_payload_digest[32] = {};
  std::uint32_t active_parameter_schema_revision = 0;
  std::uint16_t active_cadence_hz = 0;
  std::uint8_t active_local_strips = 0;
  std::uint8_t active_target = 0;
  std::uint16_t active_global_strips = 0;
  std::uint16_t active_leds_per_strip = 0;
  std::uint16_t active_global_strip_offset = 0;
  std::uint16_t active_parameter_size = 0;
  std::uint8_t active_parameter_digest[32] = {};
  std::uint16_t last_load_us = 0;
  std::uint16_t last_initialize_us = 0;
  std::uint16_t last_context_us = 0;
  std::uint16_t last_render_us = 0;
  std::uint16_t max_phase_us = 0;
  std::uint16_t watchdog_events = 0;
  std::uint32_t writes = 0;
  std::uint32_t evictions = 0;
  std::uint16_t stages = 0;
  std::uint16_t verifies = 0;
  std::uint16_t activations = 0;
  std::uint16_t restores = 0;
  std::uint16_t quarantines = 0;
};

// Native rendering continues independently while the SPI response queue drains.
// Pair the result produced by a native command with the exact status-v3
// operation identity so a later render success/failure cannot rewrite that
// command's acknowledgement before the host observes it.
class NativeModuleOperationResultLatch {
 public:
  void record(std::uint32_t sequence, std::uint8_t command,
              NativeModuleResult result) {
    sequence_ = sequence;
    command_ = command;
    result_ = result;
    valid_ = true;
  }

  bool apply(std::uint32_t sequence, std::uint8_t command,
             NativeModuleStatusV1* status) const {
    if (!valid_ || status == nullptr || sequence != sequence_ ||
        command != command_) {
      return false;
    }
    status->result = result_;
    return true;
  }

 private:
  bool valid_ = false;
  std::uint32_t sequence_ = 0;
  std::uint8_t command_ = 0;
  NativeModuleResult result_ = NativeModuleResult::None;
};

class NativeModuleStore {
 public:
  virtual ~NativeModuleStore() = default;
  virtual bool ready() const = 0;
  virtual std::uint32_t capacity_bytes() const = 0;
  virtual std::uint32_t used_bytes() const = 0;
  virtual std::uint32_t reserve_bytes() const = 0;
  virtual std::uint64_t mutation_generation() const = 0;
  virtual bool probe(const std::uint8_t digest[32], std::uint32_t* size) const = 0;
  virtual bool touch(const std::uint8_t digest[32]) = 0;
  virtual bool can_stage(std::uint32_t size, const NativeModuleLedger& pins,
                         std::uint32_t* reclaimable) const = 0;
  virtual bool begin_part(const std::uint8_t digest[32], std::uint32_t size,
                          const NativeModuleLedger& pins,
                          std::uint32_t* evicted) = 0;
  virtual bool write_part(std::uint32_t offset, const std::uint8_t* data,
                          std::size_t size) = 0;
  virtual bool read_part(std::uint32_t offset, std::uint8_t* data,
                         std::size_t size) const = 0;
  virtual bool commit_part(const std::uint8_t digest[32]) = 0;
  virtual void abort_part() = 0;
  virtual bool read_committed(const std::uint8_t digest[32],
                              std::uint32_t offset, std::uint8_t* data,
                              std::size_t size) const = 0;
  virtual bool remove(const std::uint8_t digest[32]) = 0;
  virtual bool committed_path(const std::uint8_t digest[32], char* output,
                              std::size_t output_size) const = 0;
};

class NativeModulePersistence {
 public:
  virtual ~NativeModulePersistence() = default;
  virtual bool load(NativeModuleLedger* ledger,
                    std::uint8_t quarantined_payload[32],
                    std::uint8_t attributed_payload[32],
                    NativeModulePhase* attributed_phase) = 0;
  virtual bool save(const NativeModuleLedger& ledger,
                    const std::uint8_t quarantined_payload[32]) = 0;
  virtual bool mark_phase(const std::uint8_t payload[32],
                          NativeModulePhase phase) = 0;
  virtual bool clear_phase() = 0;
};

class NativeModuleBackend {
 public:
  virtual ~NativeModuleBackend() = default;
  virtual bool load(const char* path) = 0;
  virtual bool resolve_entrypoint() = 0;
  virtual bool initialize(const NativeModuleDescriptor& descriptor,
                          const NativeModuleTopology& topology,
                          const NativeModuleActivation& activation) = 0;
  virtual bool update_context(const NativeModuleParameters& parameters,
                              const NativeModulePresentation& presentation) = 0;
  virtual bool render(std::uint64_t unscaled_scene_time_us,
                      std::uint64_t scaled_scene_time_us,
                      std::uint64_t frame_index, std::uint8_t* rgb_output,
                      std::size_t rgb_output_size,
                      NativeModuleRenderResult* result) = 0;
  virtual bool cleanup() = 0;
  virtual bool unload() = 0;
};

class NativeModuleClock {
 public:
  virtual ~NativeModuleClock() = default;
  virtual std::uint64_t now_us() const = 0;
};

class NativeModuleWatchdog {
 public:
  virtual ~NativeModuleWatchdog() = default;
  virtual bool arm(NativeModulePhase phase) = 0;
  virtual void disarm() = 0;
};

bool decode_native_typed_parameters(const std::uint8_t* data,
                                    std::size_t size,
                                    NativeModuleParameters* output);
bool native_module_descriptor_equal(const NativeModuleDescriptor& left,
                                    const NativeModuleDescriptor& right);
bool native_module_binding_equal(const NativeModuleBinding& left,
                                 const NativeModuleBinding& right);

class NativeModuleManager {
 public:
  NativeModuleManager(NativeModuleStore* store,
                      NativeModulePersistence* persistence,
                      NativeModuleBackend* backend,
                      NativeModuleClock* clock,
                      std::uint8_t* scratch, std::size_t scratch_size,
                      bool enabled =
                          LEDGRID_ENABLE_RECEIVER_NATIVE_MODULES != 0);

  bool begin();
  void set_watchdog(NativeModuleWatchdog* watchdog) { watchdog_ = watchdog; }
  void configure_topology(const NativeModuleTopology& topology);
  void configure_scene(bool active, std::uint64_t scene_epoch) {
    presentation_scene_active_ = active;
    presentation_scene_epoch_ = scene_epoch;
  }
  void configure_presentation(const NativeModulePresentation& presentation);
  NativeModuleResult process(const std::uint8_t* command, std::size_t size);
  bool render(std::uint64_t unscaled_scene_time_us,
              std::uint64_t scaled_scene_time_us, std::uint64_t frame_index,
              std::uint8_t* rgb_output, std::size_t rgb_output_size,
              NativeModuleRenderResult* result);
  void host_takeover();
  NativeModuleStatusV1 status() const;
  const NativeModuleLedger& ledger() const { return ledger_; }
  bool active() const { return executing_; }

 private:
  NativeModuleResult finish(NativeModuleResult result);
  NativeModuleResult probe(const std::uint8_t*, std::size_t);
  NativeModuleResult preflight(const std::uint8_t*, std::size_t);
  NativeModuleResult begin_transfer(const std::uint8_t*, std::size_t);
  NativeModuleResult chunk(const std::uint8_t*, std::size_t);
  NativeModuleResult finalize(const std::uint8_t*, std::size_t);
  NativeModuleResult verify(const std::uint8_t*, std::size_t);
  NativeModuleResult activate(const std::uint8_t*, std::size_t);
  NativeModuleResult update_parameters(const std::uint8_t*, std::size_t);
  NativeModuleResult stop(const std::uint8_t*, std::size_t);
  NativeModuleResult remove(const std::uint8_t*, std::size_t);
  NativeModuleResult abort(const std::uint8_t*, std::size_t);
  NativeModuleResult restore(const std::uint8_t*, std::size_t);
  NativeModuleResult clear_quarantine(const std::uint8_t*, std::size_t);
  NativeModuleResult fail_phase(NativeModulePhase phase,
                                NativeModuleResult failure);
  NativeModulePhase recover_backend(
      const std::uint8_t payload_digest[32], bool* watchdog_failure);
  bool descriptor_valid(const NativeModuleDescriptor& descriptor) const;
  bool parse_descriptor(const std::uint8_t* bytes,
                        NativeModuleDescriptor* descriptor) const;
  bool binding_cached(const NativeModuleBinding& binding) const;
  bool binding_integrity_valid(const NativeModuleBinding& binding);
  bool save_ledger(const NativeModuleLedger& candidate);
  bool save_state();
  std::uint64_t calculate_preflight_token() const;
  bool phase_begin(NativeModulePhase phase,
                   const std::uint8_t payload_digest[32]);
  bool phase_end(NativeModulePhase phase, std::uint16_t* duration);
  void copy_status_binding(const NativeModuleBinding& binding,
                           std::uint8_t bundle[32],
                           std::uint8_t payload[32]) const;

  NativeModuleStore* store_ = nullptr;
  NativeModulePersistence* persistence_ = nullptr;
  NativeModuleBackend* backend_ = nullptr;
  NativeModuleClock* clock_ = nullptr;
  NativeModuleWatchdog* watchdog_ = nullptr;
  std::uint8_t* scratch_ = nullptr;
  std::size_t scratch_size_ = 0;
  bool enabled_ = false;
  bool initialized_ = false;
  bool cache_integrity_ok_ = true;
  bool executing_ = false;
  // These track calls that may have partially succeeded. The backend contract
  // requires cleanup/unload to be idempotent, so failure recovery can safely
  // release a half-initialized module before a different payload is tried.
  bool backend_module_may_be_loaded_ = false;
  bool backend_state_may_exist_ = false;
  bool presentation_scene_active_ = false;
  std::uint64_t presentation_scene_epoch_ = 0;
  bool last_probe_found_ = false;
  NativeModuleTopology topology_{};
  NativeModuleLedger ledger_{};
  // One activation can displace the prior rollback slot. Retain its descriptor
  // in RAM until the distributed host transaction either converges or sends
  // RESTORE; it is intentionally not a durable cache pin after activation.
  NativeModuleBinding displaced_binding_{};
  NativeModuleDescriptor preflight_descriptor_{};
  NativeModuleDescriptor transfer_descriptor_{};
  NativeModuleParameters active_parameters_{};
  NativeModuleActivation activation_{};
  NativeModulePresentation presentation_{};
  NativeModuleResult result_ = NativeModuleResult::None;
  NativeModuleTransferState transfer_state_ = NativeModuleTransferState::Idle;
  NativeModulePhase attributed_phase_ = NativeModulePhase::None;
  std::uint8_t quarantine_payload_[32] = {};
  std::uint64_t preflight_token_ = 0;
  std::uint32_t preflight_reclaimable_ = 0;
  std::uint32_t transfer_received_ = 0;
  std::uint64_t phase_started_us_ = 0;
  std::uint8_t phase_payload_digest_[32] = {};
  std::uint64_t last_native_deadline_scene_time_us_ = 0;
  bool rendered_once_ = false;
  std::uint16_t last_load_us_ = 0;
  std::uint16_t last_initialize_us_ = 0;
  std::uint16_t last_context_us_ = 0;
  std::uint16_t last_render_us_ = 0;
  std::uint16_t max_phase_us_ = 0;
  std::uint16_t watchdog_events_ = 0;
  std::uint32_t writes_ = 0;
  std::uint32_t evictions_ = 0;
  std::uint16_t stages_ = 0;
  std::uint16_t verifies_ = 0;
  std::uint16_t activations_ = 0;
  std::uint16_t restores_ = 0;
  std::uint16_t quarantines_ = 0;
};

}  // namespace ledgrid
