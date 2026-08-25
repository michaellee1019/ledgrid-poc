#include "ledgrid/native_module.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>

#include "ledgrid/sha256.hpp"

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

bool all_zero(const std::uint8_t* input, std::size_t size) {
  std::uint8_t combined = 0;
  for (std::size_t index = 0; index < size; ++index) combined |= input[index];
  return combined == 0;
}

bool digest_equal(const std::uint8_t* left, const std::uint8_t* right) {
  return std::memcmp(left, right, 32) == 0;
}

std::uint16_t saturating_u16(std::uint32_t value) {
  return static_cast<std::uint16_t>(std::min<std::uint32_t>(value, UINT16_MAX));
}

std::uint16_t increment_u16(std::uint16_t value) {
  return value == UINT16_MAX ? value : static_cast<std::uint16_t>(value + 1U);
}

void clear_binding(NativeModuleBinding* binding) {
  if (binding != nullptr) *binding = {};
}

bool binding_digest_equal(const NativeModuleBinding& binding,
                          const std::uint8_t* bundle,
                          const std::uint8_t* payload) {
  return binding.present &&
         digest_equal(binding.descriptor.bundle_digest, bundle) &&
         digest_equal(binding.descriptor.payload_digest, payload);
}

}  // namespace

bool decode_native_typed_parameters(const std::uint8_t* data,
                                    std::size_t size,
                                    NativeModuleParameters* output) {
  if (data == nullptr || output == nullptr || size < 2 ||
      size > kNativeModuleMaxParameterBytes ||
      data[0] != kNativeTypedParameterVersion ||
      data[1] > LEDGRID_NATIVE_BACKGROUND_MAX_PARAMETERS) {
    return false;
  }
  NativeModuleParameters decoded{};
  decoded.count = data[1];
  std::size_t cursor = 2;
  for (std::uint8_t index = 0; index < decoded.count; ++index) {
    if (size - cursor < 4) return false;
    auto& entry = decoded.entries[index];
    entry.id = read_u16(data + cursor);
    cursor += 2;
    entry.type = data[cursor++];
    entry.reserved_zero = data[cursor++];
    // IDs are not merely sorted: ABI v2 defines them as the exact zero-based
    // positions of names in canonical schema order. Accepting a sparse sequence
    // would let a syntactically valid blob address a different schema slot than
    // the host signed and previewed.
    if (entry.id != index || entry.reserved_zero != 0) {
      return false;
    }
    switch (static_cast<ledgrid_native_parameter_type_v2>(entry.type)) {
      case LEDGRID_NATIVE_PARAMETER_INT32:
        if (size - cursor < 4) return false;
        entry.value.integer = static_cast<std::int32_t>(read_u32(data + cursor));
        cursor += 4;
        break;
      case LEDGRID_NATIVE_PARAMETER_FLOAT32: {
        if (size - cursor < 4) return false;
        const std::uint32_t bits = read_u32(data + cursor);
        std::memcpy(&entry.value.real, &bits, sizeof(bits));
        if (!std::isfinite(entry.value.real)) return false;
        cursor += 4;
        break;
      }
      case LEDGRID_NATIVE_PARAMETER_BOOL:
        if (size - cursor < 1 || data[cursor] > 1) return false;
        entry.value.boolean = data[cursor++];
        break;
      case LEDGRID_NATIVE_PARAMETER_ENUM:
        if (size - cursor < 2) return false;
        entry.value.enum_index = read_u16(data + cursor);
        cursor += 2;
        break;
      case LEDGRID_NATIVE_PARAMETER_COLOR_RGB:
        if (size - cursor < 3) return false;
        std::memcpy(entry.value.color, data + cursor, 3);
        cursor += 3;
        break;
      default:
        return false;
    }
  }
  if (cursor != size) return false;
  std::memcpy(decoded.canonical, data, size);
  decoded.canonical_size = static_cast<std::uint16_t>(size);
  sha256(data, size, decoded.digest);
  *output = decoded;
  return true;
}

bool native_module_descriptor_equal(const NativeModuleDescriptor& left,
                                    const NativeModuleDescriptor& right) {
  return digest_equal(left.bundle_digest, right.bundle_digest) &&
         digest_equal(left.payload_digest, right.payload_digest) &&
         left.payload_size == right.payload_size && left.abi == right.abi &&
         left.target == right.target &&
         left.global_strips == right.global_strips &&
         left.local_strips == right.local_strips &&
         left.leds_per_strip == right.leds_per_strip &&
         left.global_strip_offset == right.global_strip_offset &&
         left.cadence_hz == right.cadence_hz &&
         left.parameter_schema_revision == right.parameter_schema_revision &&
         left.flags == right.flags;
}

bool native_module_binding_equal(const NativeModuleBinding& left,
                                 const NativeModuleBinding& right) {
  return left.present == right.present &&
         (!left.present ||
          native_module_descriptor_equal(left.descriptor, right.descriptor));
}

NativeModuleManager::NativeModuleManager(
    NativeModuleStore* store, NativeModulePersistence* persistence,
    NativeModuleBackend* backend, NativeModuleClock* clock,
    std::uint8_t* scratch, std::size_t scratch_size, bool enabled)
    : store_(store), persistence_(persistence), backend_(backend), clock_(clock),
      scratch_(scratch), scratch_size_(scratch_size), enabled_(enabled) {
  presentation_.vibe.struct_size = sizeof(ledgrid_native_vibe_v2);
  presentation_.vibe.luminance_q8_8 = 256;
  presentation_.vibe.tempo_q8_8 = 256;
  presentation_.vibe.chroma_q8_8 = 256;
  presentation_.vibe.energy_q8_8 = 256;
  presentation_.modifier_view.struct_size =
      sizeof(ledgrid_native_modifier_view_v2);
  presentation_.modifier_view.entries = presentation_.modifiers;
  presentation_.profile_view.struct_size =
      sizeof(ledgrid_native_profile_view_v2);
  presentation_.profile_view.sections = presentation_.profile_sections;
}

bool NativeModuleManager::begin() {
  if (!enabled_) return false;
  if (store_ == nullptr || persistence_ == nullptr || backend_ == nullptr ||
      clock_ == nullptr || scratch_ == nullptr || scratch_size_ < 256 ||
      !store_->ready()) {
    cache_integrity_ok_ = false;
    result_ = NativeModuleResult::StorageError;
    return false;
  }
  NativeModuleLedger loaded{};
  std::uint8_t attributed_payload[32] = {};
  NativeModulePhase attributed_phase = NativeModulePhase::None;
  if (!persistence_->load(&loaded, quarantine_payload_, attributed_payload,
                          &attributed_phase)) {
    cache_integrity_ok_ = false;
    result_ = NativeModuleResult::IntegrityError;
    return false;
  }
  ledger_ = loaded;
  initialized_ = true;
  if (attributed_phase != NativeModulePhase::None &&
      !all_zero(attributed_payload, 32)) {
    std::memcpy(quarantine_payload_, attributed_payload, 32);
    attributed_phase_ = attributed_phase;
    executing_ = false;
    if (ledger_.active.present &&
        digest_equal(ledger_.active.descriptor.payload_digest,
                     attributed_payload)) {
      clear_binding(&ledger_.active);
      if (ledger_.generation != UINT64_MAX) ++ledger_.generation;
    }
    quarantines_ = increment_u16(quarantines_);
    transfer_state_ = NativeModuleTransferState::Quarantined;
    result_ = NativeModuleResult::Quarantined;
    persistence_->save(ledger_, quarantine_payload_);
    persistence_->clear_phase();
  } else {
    transfer_state_ = ledger_.staged.present
        ? NativeModuleTransferState::Staged
        : NativeModuleTransferState::Idle;
  }
  for (const auto* binding : {&ledger_.active, &ledger_.staged,
                              &ledger_.rollback}) {
    if (binding->present && !binding_cached(*binding)) {
      cache_integrity_ok_ = false;
      result_ = NativeModuleResult::IntegrityError;
    }
  }
  return true;
}

void NativeModuleManager::configure_topology(
    const NativeModuleTopology& topology) {
  topology_ = topology;
}

void NativeModuleManager::configure_presentation(
    const NativeModulePresentation& presentation) {
  presentation_ = presentation;
  presentation_.modifier_view.entries = presentation_.modifiers;
  presentation_.profile_view.sections = presentation_.profile_sections;
}

NativeModuleResult NativeModuleManager::finish(NativeModuleResult result) {
  result_ = result;
  if (result != NativeModuleResult::Ok &&
      transfer_state_ == NativeModuleTransferState::Finalizing) {
    transfer_state_ = NativeModuleTransferState::Failed;
  }
  return result;
}

bool NativeModuleManager::parse_descriptor(
    const std::uint8_t* bytes, NativeModuleDescriptor* descriptor) const {
  if (bytes == nullptr || descriptor == nullptr) return false;
  NativeModuleDescriptor parsed{};
  std::memcpy(parsed.bundle_digest, bytes, 32);
  std::memcpy(parsed.payload_digest, bytes + 32, 32);
  parsed.payload_size = read_u32(bytes + 64);
  parsed.abi = read_u16(bytes + 68);
  parsed.target = bytes[70];
  parsed.global_strips = read_u16(bytes + 71);
  parsed.local_strips = bytes[73];
  parsed.leds_per_strip = read_u16(bytes + 74);
  parsed.global_strip_offset = read_u16(bytes + 76);
  parsed.cadence_hz = read_u16(bytes + 78);
  parsed.parameter_schema_revision = read_u32(bytes + 80);
  parsed.flags = bytes[84];
  *descriptor = parsed;
  return true;
}

bool NativeModuleManager::descriptor_valid(
    const NativeModuleDescriptor& descriptor) const {
  return topology_.configured && !all_zero(descriptor.bundle_digest, 32) &&
         !all_zero(descriptor.payload_digest, 32) &&
         descriptor.payload_size > 0 &&
         descriptor.payload_size <= kNativeModuleMaxPayloadBytes &&
         descriptor.abi == kNativeModuleAbiV2 &&
         descriptor.target == kNativeModuleTargetEsp32S3 &&
         descriptor.global_strips == topology_.global_strips &&
         descriptor.local_strips == topology_.local_strips &&
         descriptor.leds_per_strip == topology_.leds_per_strip &&
         descriptor.global_strip_offset == topology_.global_strip_offset &&
         descriptor.local_strips >= 1 && descriptor.local_strips <= 8 &&
         descriptor.global_strip_offset <= descriptor.global_strips &&
         descriptor.local_strips <=
             descriptor.global_strips - descriptor.global_strip_offset &&
         descriptor.cadence_hz >= 1 && descriptor.cadence_hz <= 200 &&
         descriptor.parameter_schema_revision != 0 && descriptor.flags == 0;
}

bool NativeModuleManager::binding_cached(
    const NativeModuleBinding& binding) const {
  if (!binding.present) return true;
  std::uint32_t size = 0;
  return store_ != nullptr &&
         store_->probe(binding.descriptor.payload_digest, &size) &&
         size == binding.descriptor.payload_size;
}

bool NativeModuleManager::binding_integrity_valid(
    const NativeModuleBinding& binding) {
  if (!binding_cached(binding)) return false;
  Sha256 hasher;
  std::uint32_t offset = 0;
  while (offset < binding.descriptor.payload_size) {
    const std::size_t amount = std::min<std::size_t>(
        scratch_size_, binding.descriptor.payload_size - offset);
    if (!store_->read_committed(binding.descriptor.payload_digest, offset,
                                scratch_, amount)) {
      return false;
    }
    hasher.update(scratch_, amount);
    offset += static_cast<std::uint32_t>(amount);
  }
  std::uint8_t digest[32] = {};
  hasher.finish(digest);
  return digest_equal(digest, binding.descriptor.payload_digest);
}

bool NativeModuleManager::save_ledger(
    const NativeModuleLedger& candidate) {
  if (!binding_cached(candidate.active) || !binding_cached(candidate.staged) ||
      !binding_cached(candidate.rollback) || persistence_ == nullptr ||
      !persistence_->save(candidate, quarantine_payload_)) {
    return false;
  }
  ledger_ = candidate;
  return true;
}

bool NativeModuleManager::save_state() {
  return persistence_ != nullptr &&
         persistence_->save(ledger_, quarantine_payload_);
}

std::uint64_t NativeModuleManager::calculate_preflight_token() const {
  std::uint64_t hash = 1469598103934665603ULL;
  const auto mix = [&](const std::uint8_t* bytes, std::size_t size,
                       std::uint64_t* value) {
    for (std::size_t index = 0; index < size; ++index) {
      *value = (*value ^ bytes[index]) * 1099511628211ULL;
    }
  };
  mix(reinterpret_cast<const std::uint8_t*>(&preflight_descriptor_),
      sizeof(preflight_descriptor_), &hash);
  const std::uint64_t values[] = {
      ledger_.generation,
      store_ == nullptr ? 0 : store_->mutation_generation(),
      topology_.logical_receiver_id,
      topology_.reverse_local_strip_order ? 1U : 0U};
  mix(reinterpret_cast<const std::uint8_t*>(values), sizeof(values), &hash);
  return hash == 0 ? 1 : hash;
}

NativeModuleResult NativeModuleManager::probe(
    const std::uint8_t* command, std::size_t size) {
  if (size != kNativeModuleProbeBytes || all_zero(command + 1, 32)) {
    return finish(NativeModuleResult::InvalidSize);
  }
  std::memcpy(transfer_descriptor_.payload_digest, command + 1, 32);
  std::uint32_t ignored = 0;
  last_probe_found_ = store_->probe(command + 1, &ignored);
  // PROBE answers an existence question; absence is a successful observation,
  // not a failed command. The found flag and echoed digest carry the answer so
  // hosts can distinguish a clean miss from transport/receiver failure.
  return finish(NativeModuleResult::Ok);
}

NativeModuleResult NativeModuleManager::preflight(
    const std::uint8_t* command, std::size_t size) {
  if (size != kNativeModulePreflightBytes) {
    return finish(NativeModuleResult::InvalidSize);
  }
  NativeModuleDescriptor descriptor{};
  parse_descriptor(command + 1, &descriptor);
  if (!descriptor_valid(descriptor)) {
    if (descriptor.abi != kNativeModuleAbiV2)
      return finish(NativeModuleResult::WrongAbi);
    if (descriptor.target != kNativeModuleTargetEsp32S3)
      return finish(NativeModuleResult::WrongTarget);
    return finish(NativeModuleResult::WrongGeometry);
  }
  if (ledger_.staged.present &&
      !native_module_descriptor_equal(ledger_.staged.descriptor, descriptor)) {
    return finish(NativeModuleResult::InvalidState);
  }
  preflight_descriptor_ = descriptor;
  std::uint32_t cached_size = 0;
  last_probe_found_ = store_->probe(descriptor.payload_digest, &cached_size) &&
                      cached_size == descriptor.payload_size;
  preflight_reclaimable_ = 0;
  if (!last_probe_found_ &&
      !store_->can_stage(descriptor.payload_size, ledger_,
                         &preflight_reclaimable_)) {
    preflight_token_ = 0;
    transfer_state_ = NativeModuleTransferState::Failed;
    return finish(NativeModuleResult::NoSpace);
  }
  preflight_token_ = calculate_preflight_token();
  transfer_state_ = NativeModuleTransferState::PreflightReady;
  return finish(NativeModuleResult::Ok);
}

NativeModuleResult NativeModuleManager::begin_transfer(
    const std::uint8_t* command, std::size_t size) {
  if (size != kNativeModuleBeginBytes) {
    return finish(NativeModuleResult::InvalidSize);
  }
  NativeModuleDescriptor descriptor{};
  parse_descriptor(command + 9, &descriptor);
  if (transfer_state_ != NativeModuleTransferState::PreflightReady ||
      read_u64(command + 1) != preflight_token_ ||
      preflight_token_ != calculate_preflight_token()) {
    return finish(NativeModuleResult::InvalidToken);
  }
  if (!native_module_descriptor_equal(descriptor, preflight_descriptor_)) {
    return finish(NativeModuleResult::Conflict);
  }
  transfer_descriptor_ = descriptor;
  transfer_received_ = 0;
  if (last_probe_found_) {
    NativeModuleLedger candidate = ledger_;
    candidate.staged.present = true;
    candidate.staged.descriptor = descriptor;
    if (candidate.generation == UINT64_MAX) {
      return finish(NativeModuleResult::InvalidState);
    }
    ++candidate.generation;
    if (!store_->touch(descriptor.payload_digest) || !save_ledger(candidate)) {
      return finish(NativeModuleResult::StorageError);
    }
    transfer_received_ = descriptor.payload_size;
    transfer_state_ = NativeModuleTransferState::Staged;
    stages_ = increment_u16(stages_);
    return finish(NativeModuleResult::Ok);
  }
  std::uint32_t evicted = 0;
  if (!store_->begin_part(descriptor.payload_digest, descriptor.payload_size,
                          ledger_, &evicted)) {
    transfer_state_ = NativeModuleTransferState::Failed;
    return finish(NativeModuleResult::StorageError);
  }
  evictions_ = UINT32_MAX - evictions_ < evicted
      ? UINT32_MAX : evictions_ + evicted;
  transfer_state_ = NativeModuleTransferState::Receiving;
  return finish(NativeModuleResult::Ok);
}

NativeModuleResult NativeModuleManager::chunk(
    const std::uint8_t* command, std::size_t size) {
  if (size < 6 || size > kAnimationPipelineMaxTransactionBytes -
                                  kAnimationPipelineCrcBytes) {
    return finish(NativeModuleResult::InvalidSize);
  }
  if (transfer_state_ != NativeModuleTransferState::Receiving) {
    return finish(NativeModuleResult::InvalidState);
  }
  const std::uint32_t offset = read_u32(command + 1);
  const std::size_t amount = size - kNativeModuleChunkHeaderBytes;
  if (amount > kNativeModuleMaxChunkBytes ||
      offset > transfer_descriptor_.payload_size ||
      amount > transfer_descriptor_.payload_size - offset) {
    return finish(NativeModuleResult::InvalidSize);
  }
  if (offset < transfer_received_) {
    if (amount > transfer_received_ - offset || amount > scratch_size_ ||
        !store_->read_part(offset, scratch_, amount)) {
      return finish(NativeModuleResult::Conflict);
    }
    return finish(std::memcmp(scratch_, command + 5, amount) == 0
                      ? NativeModuleResult::Ok
                      : NativeModuleResult::Conflict);
  }
  if (offset != transfer_received_) return finish(NativeModuleResult::Conflict);
  if (!store_->write_part(offset, command + 5, amount)) {
    transfer_state_ = NativeModuleTransferState::Failed;
    return finish(NativeModuleResult::StorageError);
  }
  transfer_received_ += static_cast<std::uint32_t>(amount);
  if (writes_ != UINT32_MAX) ++writes_;
  return finish(NativeModuleResult::Ok);
}

NativeModuleResult NativeModuleManager::finalize(
    const std::uint8_t* command, std::size_t size) {
  if (size != kNativeModuleFinalizeBytes) {
    return finish(NativeModuleResult::InvalidSize);
  }
  if (transfer_state_ == NativeModuleTransferState::Staged &&
      binding_digest_equal(ledger_.staged, command + 1, command + 33)) {
    return finish(NativeModuleResult::Ok);
  }
  if (transfer_state_ != NativeModuleTransferState::Receiving ||
      transfer_received_ != transfer_descriptor_.payload_size ||
      !digest_equal(command + 1, transfer_descriptor_.bundle_digest) ||
      !digest_equal(command + 33, transfer_descriptor_.payload_digest)) {
    return finish(NativeModuleResult::InvalidState);
  }
  transfer_state_ = NativeModuleTransferState::Finalizing;
  Sha256 hasher;
  std::uint32_t offset = 0;
  while (offset < transfer_descriptor_.payload_size) {
    const std::size_t amount = std::min<std::size_t>(
        scratch_size_, transfer_descriptor_.payload_size - offset);
    if (!store_->read_part(offset, scratch_, amount)) {
      store_->abort_part();
      return finish(NativeModuleResult::StorageError);
    }
    hasher.update(scratch_, amount);
    offset += static_cast<std::uint32_t>(amount);
  }
  std::uint8_t digest[32] = {};
  hasher.finish(digest);
  if (!digest_equal(digest, transfer_descriptor_.payload_digest)) {
    store_->abort_part();
    return finish(NativeModuleResult::DigestMismatch);
  }
  if (!store_->commit_part(transfer_descriptor_.payload_digest)) {
    store_->abort_part();
    return finish(NativeModuleResult::StorageError);
  }
  NativeModuleLedger candidate = ledger_;
  candidate.staged.present = true;
  candidate.staged.descriptor = transfer_descriptor_;
  if (candidate.generation == UINT64_MAX) {
    return finish(NativeModuleResult::InvalidState);
  }
  ++candidate.generation;
  if (!save_ledger(candidate)) return finish(NativeModuleResult::StorageError);
  transfer_state_ = NativeModuleTransferState::Staged;
  stages_ = increment_u16(stages_);
  return finish(NativeModuleResult::Ok);
}

NativeModuleResult NativeModuleManager::verify(
    const std::uint8_t* command, std::size_t size) {
  if (size != kNativeModuleVerifyBytes) {
    return finish(NativeModuleResult::InvalidSize);
  }
  if (!binding_digest_equal(ledger_.staged, command + 1, command + 33)) {
    return finish(NativeModuleResult::NotFound);
  }
  if (!descriptor_valid(ledger_.staged.descriptor) ||
      !binding_integrity_valid(ledger_.staged)) {
    cache_integrity_ok_ = false;
    return finish(NativeModuleResult::IntegrityError);
  }
  verifies_ = increment_u16(verifies_);
  return finish(NativeModuleResult::Ok);
}

bool NativeModuleManager::phase_begin(
    NativeModulePhase phase, const std::uint8_t payload_digest[32]) {
  attributed_phase_ = phase;
  phase_started_us_ = clock_->now_us();
  std::memcpy(phase_payload_digest_, payload_digest, 32);
  if (!persistence_->mark_phase(payload_digest, phase)) return false;
  if (watchdog_ != nullptr && !watchdog_->arm(phase)) {
    persistence_->clear_phase();
    return false;
  }
  return true;
}

bool NativeModuleManager::phase_end(
    NativeModulePhase phase, std::uint16_t* duration) {
  (void)phase;
  const std::uint64_t now = clock_->now_us();
  const std::uint64_t elapsed = now >= phase_started_us_ ? now - phase_started_us_ : 0;
  const std::uint16_t bounded = saturating_u16(
      static_cast<std::uint32_t>(std::min<std::uint64_t>(elapsed, UINT32_MAX)));
  if (duration != nullptr) *duration = bounded;
  max_phase_us_ = std::max(max_phase_us_, bounded);
  if (watchdog_ != nullptr) watchdog_->disarm();
  attributed_phase_ = NativeModulePhase::None;
  return persistence_->clear_phase() && elapsed <= kNativeModuleWatchdogUs;
}

NativeModulePhase NativeModuleManager::recover_backend(
    const std::uint8_t payload_digest[32], bool* watchdog_failure) {
  NativeModulePhase recovery_failure = NativeModulePhase::None;
  std::uint16_t ignored = 0;
  if (watchdog_failure != nullptr) *watchdog_failure = false;

  // A phase_begin failure can leave an armed watchdog when a custom watchdog
  // implementation rejects after partially arming. Disarm before attempting a
  // separately attributed recovery call.
  if (watchdog_ != nullptr) watchdog_->disarm();

  if (backend_state_may_exist_) {
    if (phase_begin(NativeModulePhase::Cleanup, payload_digest)) {
      const bool cleaned = backend_->cleanup();
      backend_state_may_exist_ = false;
      const bool timed = phase_end(NativeModulePhase::Cleanup, &ignored);
      if (!cleaned || !timed) {
        recovery_failure = NativeModulePhase::Cleanup;
        if (!timed && watchdog_failure != nullptr) *watchdog_failure = true;
      }
    } else {
      recovery_failure = NativeModulePhase::Cleanup;
      if (watchdog_ != nullptr) watchdog_->disarm();
    }
  }

  if (backend_module_may_be_loaded_) {
    if (phase_begin(NativeModulePhase::Unload, payload_digest)) {
      const bool unloaded = backend_->unload();
      backend_module_may_be_loaded_ = false;
      const bool timed = phase_end(NativeModulePhase::Unload, &ignored);
      if ((!unloaded || !timed) &&
          recovery_failure == NativeModulePhase::None) {
        recovery_failure = NativeModulePhase::Unload;
      }
      if (!timed && watchdog_failure != nullptr) *watchdog_failure = true;
    } else {
      if (recovery_failure == NativeModulePhase::None) {
        recovery_failure = NativeModulePhase::Unload;
      }
      if (watchdog_ != nullptr) watchdog_->disarm();
    }
  }
  return recovery_failure;
}

NativeModuleResult NativeModuleManager::fail_phase(
    NativeModulePhase phase, NativeModuleResult failure) {
  std::uint8_t failed_payload[32] = {};
  std::memcpy(failed_payload, phase_payload_digest_, sizeof(failed_payload));
  bool recovery_watchdog = false;
  const NativeModulePhase recovery_failure =
      recover_backend(failed_payload, &recovery_watchdog);
  std::memcpy(quarantine_payload_, failed_payload, 32);
  attributed_phase_ = recovery_failure == NativeModulePhase::None
      ? phase : recovery_failure;
  executing_ = false;
  if (ledger_.active.present &&
      digest_equal(ledger_.active.descriptor.payload_digest,
                   quarantine_payload_)) {
    clear_binding(&ledger_.active);
    if (ledger_.generation != UINT64_MAX) ++ledger_.generation;
  }
  transfer_state_ = NativeModuleTransferState::Quarantined;
  quarantines_ = increment_u16(quarantines_);
  if (failure == NativeModuleResult::Watchdog || recovery_watchdog) {
    watchdog_events_ = increment_u16(watchdog_events_);
  }
  save_state();
  persistence_->clear_phase();
  return finish(failure);
}

NativeModuleResult NativeModuleManager::activate(
    const std::uint8_t* command, std::size_t size) {
  if (size < kNativeModuleActivateHeaderBytes ||
      size > kNativeModuleActivateHeaderBytes + kNativeModuleMaxParameterBytes) {
    return finish(NativeModuleResult::InvalidSize);
  }
  if (!presentation_scene_active_ ||
      read_u64(command + 73) != presentation_scene_epoch_) {
    return finish(NativeModuleResult::InvalidState);
  }
  const std::uint16_t parameter_size = read_u16(command + 85);
  if (size != kNativeModuleActivateHeaderBytes + parameter_size ||
      read_u64(command + 1) != ledger_.generation ||
      !binding_digest_equal(ledger_.staged, command + 9, command + 41)) {
    return finish(NativeModuleResult::Conflict);
  }
  if (digest_equal(command + 41, quarantine_payload_)) {
    return finish(NativeModuleResult::Quarantined);
  }
  if (!descriptor_valid(ledger_.staged.descriptor) ||
      !binding_integrity_valid(ledger_.staged)) {
    cache_integrity_ok_ = false;
    return finish(NativeModuleResult::IntegrityError);
  }
  if (!executing_ &&
      (backend_module_may_be_loaded_ || backend_state_may_exist_)) {
    return finish(NativeModuleResult::InvalidState);
  }
  NativeModuleParameters parameters{};
  if (!decode_native_typed_parameters(command + 87, parameter_size,
                                      &parameters)) {
    return finish(NativeModuleResult::InvalidParameters);
  }
  char path[160] = {};
  if (!store_->committed_path(command + 41, path, sizeof(path))) {
    return finish(NativeModuleResult::NotFound);
  }

  // The loader owns one module at a time. Preserve the accepted binding in
  // the ledger while guarded cleanup/unload releases its runtime instance;
  // only a fully initialized candidate moves that binding into rollback.
  if (executing_) {
    const auto* active_payload = ledger_.active.descriptor.payload_digest;
    if (!phase_begin(NativeModulePhase::Cleanup, active_payload)) {
      return fail_phase(NativeModulePhase::Cleanup,
                        NativeModuleResult::StorageError);
    }
    const bool cleaned = backend_->cleanup();
    backend_state_may_exist_ = false;
    std::uint16_t elapsed = 0;
    const bool cleanup_timed =
        phase_end(NativeModulePhase::Cleanup, &elapsed);
    if (!cleanup_timed) {
      return fail_phase(NativeModulePhase::Cleanup,
                        NativeModuleResult::Watchdog);
    }
    if (!cleaned) {
      return fail_phase(NativeModulePhase::Cleanup,
                        NativeModuleResult::CleanupFailed);
    }
    if (!phase_begin(NativeModulePhase::Unload, active_payload)) {
      return fail_phase(NativeModulePhase::Unload,
                        NativeModuleResult::StorageError);
    }
    const bool unloaded = backend_->unload();
    backend_module_may_be_loaded_ = false;
    const bool unload_timed =
        phase_end(NativeModulePhase::Unload, &elapsed);
    if (!unload_timed) {
      return fail_phase(NativeModulePhase::Unload,
                        NativeModuleResult::Watchdog);
    }
    if (!unloaded) {
      return fail_phase(NativeModulePhase::Unload,
                        NativeModuleResult::UnloadFailed);
    }
    executing_ = false;
    rendered_once_ = false;
    last_native_deadline_scene_time_us_ = 0;
  }
  activation_ = {};
  activation_.scene_epoch_ns = read_u64(command + 73);
  activation_.deterministic_seed = read_u32(command + 81);
  activation_.parameters = parameters;

  std::uint16_t elapsed = 0;
  const auto* staged_payload = ledger_.staged.descriptor.payload_digest;
  if (!phase_begin(NativeModulePhase::Load, staged_payload))
    return fail_phase(NativeModulePhase::Load, NativeModuleResult::StorageError);
  const bool loaded = backend_->load(path);
  backend_module_may_be_loaded_ = true;
  const bool load_timed = phase_end(NativeModulePhase::Load, &elapsed);
  last_load_us_ = elapsed;
  if (!load_timed) return fail_phase(NativeModulePhase::Load, NativeModuleResult::Watchdog);
  if (!loaded) return fail_phase(NativeModulePhase::Load, NativeModuleResult::LoadFailed);

  if (!phase_begin(NativeModulePhase::Entrypoint, staged_payload))
    return fail_phase(NativeModulePhase::Entrypoint, NativeModuleResult::StorageError);
  const bool resolved = backend_->resolve_entrypoint();
  const bool entry_timed = phase_end(NativeModulePhase::Entrypoint, &elapsed);
  if (!entry_timed) return fail_phase(NativeModulePhase::Entrypoint, NativeModuleResult::Watchdog);
  if (!resolved) return fail_phase(NativeModulePhase::Entrypoint,
                                   NativeModuleResult::EntrypointFailed);

  if (!phase_begin(NativeModulePhase::Initialize, staged_payload))
    return fail_phase(NativeModulePhase::Initialize, NativeModuleResult::StorageError);
  const bool initialized = backend_->initialize(
      ledger_.staged.descriptor, topology_, activation_);
  backend_state_may_exist_ = true;
  const bool init_timed = phase_end(NativeModulePhase::Initialize, &elapsed);
  last_initialize_us_ = elapsed;
  if (!init_timed) return fail_phase(NativeModulePhase::Initialize, NativeModuleResult::Watchdog);
  if (!initialized) return fail_phase(NativeModulePhase::Initialize,
                                      NativeModuleResult::InitializeFailed);

  if (!phase_begin(NativeModulePhase::ContextUpdate, staged_payload))
    return fail_phase(NativeModulePhase::ContextUpdate, NativeModuleResult::StorageError);
  const bool context = backend_->update_context(parameters, presentation_);
  const bool context_timed = phase_end(NativeModulePhase::ContextUpdate, &elapsed);
  last_context_us_ = elapsed;
  if (!context_timed) return fail_phase(NativeModulePhase::ContextUpdate, NativeModuleResult::Watchdog);
  if (!context) return fail_phase(NativeModulePhase::ContextUpdate,
                                  NativeModuleResult::ContextFailed);

  NativeModuleLedger candidate = ledger_;
  displaced_binding_ = candidate.rollback;
  candidate.rollback = candidate.active;
  candidate.active = candidate.staged;
  clear_binding(&candidate.staged);
  if (candidate.generation == UINT64_MAX) {
    return fail_phase(NativeModulePhase::Initialize,
                      NativeModuleResult::InvalidState);
  }
  ++candidate.generation;
  if (!save_ledger(candidate)) {
    return fail_phase(NativeModulePhase::Initialize,
                      NativeModuleResult::StorageError);
  }
  active_parameters_ = parameters;
  executing_ = true;
  rendered_once_ = false;
  last_native_deadline_scene_time_us_ = 0;
  transfer_state_ = NativeModuleTransferState::Active;
  activations_ = increment_u16(activations_);
  return finish(NativeModuleResult::Ok);
}

NativeModuleResult NativeModuleManager::update_parameters(
    const std::uint8_t* command, std::size_t size) {
  if (size < kNativeModuleParametersHeaderBytes ||
      size > kNativeModuleParametersHeaderBytes + kNativeModuleMaxParameterBytes ||
      !executing_ ||
      !binding_digest_equal(ledger_.active, command + 1, command + 33) ||
      read_u32(command + 65) !=
          ledger_.active.descriptor.parameter_schema_revision) {
    return finish(NativeModuleResult::InvalidState);
  }
  const std::uint16_t parameter_size = read_u16(command + 69);
  if (size != kNativeModuleParametersHeaderBytes + parameter_size) {
    return finish(NativeModuleResult::InvalidSize);
  }
  NativeModuleParameters parameters{};
  if (!decode_native_typed_parameters(command + 71, parameter_size,
                                      &parameters)) {
    return finish(NativeModuleResult::InvalidParameters);
  }
  if (!phase_begin(NativeModulePhase::ContextUpdate,
                   ledger_.active.descriptor.payload_digest))
    return fail_phase(NativeModulePhase::ContextUpdate, NativeModuleResult::StorageError);
  const bool updated = backend_->update_context(parameters, presentation_);
  std::uint16_t elapsed = 0;
  const bool timed = phase_end(NativeModulePhase::ContextUpdate, &elapsed);
  last_context_us_ = elapsed;
  if (!timed) return fail_phase(NativeModulePhase::ContextUpdate, NativeModuleResult::Watchdog);
  if (!updated) return fail_phase(NativeModulePhase::ContextUpdate,
                                  NativeModuleResult::ContextFailed);
  active_parameters_ = parameters;
  return finish(NativeModuleResult::Ok);
}

NativeModuleResult NativeModuleManager::stop(
    const std::uint8_t*, std::size_t size) {
  if (size != kNativeModuleStopBytes) return finish(NativeModuleResult::InvalidSize);
  if (executing_) {
    if (!phase_begin(NativeModulePhase::Cleanup,
                     ledger_.active.descriptor.payload_digest))
      return fail_phase(NativeModulePhase::Cleanup, NativeModuleResult::StorageError);
    const bool cleaned = backend_->cleanup();
    backend_state_may_exist_ = false;
    std::uint16_t elapsed = 0;
    const bool cleanup_timed = phase_end(NativeModulePhase::Cleanup, &elapsed);
    if (!cleanup_timed) return fail_phase(NativeModulePhase::Cleanup, NativeModuleResult::Watchdog);
    if (!cleaned) return fail_phase(NativeModulePhase::Cleanup,
                                    NativeModuleResult::CleanupFailed);
    if (!phase_begin(NativeModulePhase::Unload,
                     ledger_.active.descriptor.payload_digest))
      return fail_phase(NativeModulePhase::Unload, NativeModuleResult::StorageError);
    const bool unloaded = backend_->unload();
    backend_module_may_be_loaded_ = false;
    const bool unload_timed = phase_end(NativeModulePhase::Unload, &elapsed);
    if (!unload_timed) return fail_phase(NativeModulePhase::Unload, NativeModuleResult::Watchdog);
    if (!unloaded) return fail_phase(NativeModulePhase::Unload,
                                     NativeModuleResult::UnloadFailed);
  }
  NativeModuleLedger candidate = ledger_;
  if (candidate.active.present) candidate.rollback = candidate.active;
  clear_binding(&candidate.active);
  if (candidate.generation != UINT64_MAX) ++candidate.generation;
  if (!save_ledger(candidate)) return finish(NativeModuleResult::StorageError);
  executing_ = false;
  rendered_once_ = false;
  last_native_deadline_scene_time_us_ = 0;
  transfer_state_ = candidate.staged.present
      ? NativeModuleTransferState::Staged : NativeModuleTransferState::Idle;
  return finish(NativeModuleResult::Ok);
}

NativeModuleResult NativeModuleManager::remove(
    const std::uint8_t* command, std::size_t size) {
  if (size != kNativeModuleRemoveBytes) {
    return finish(NativeModuleResult::InvalidSize);
  }
  for (const auto* binding : {&ledger_.active, &ledger_.staged,
                              &ledger_.rollback}) {
    // The cache is content-addressed by payload digest. A different signed
    // bundle may legitimately reuse the same ELF bytes, so full-binding
    // equality is insufficient protection: deleting either alias would remove
    // the bytes backing every pinned alias.
    if (binding->present &&
        digest_equal(binding->descriptor.payload_digest, command + 33)) {
      return finish(NativeModuleResult::Pinned);
    }
  }
  std::uint32_t ignored = 0;
  if (!store_->probe(command + 33, &ignored)) return finish(NativeModuleResult::Ok);
  return finish(store_->remove(command + 33) ? NativeModuleResult::Ok
                                              : NativeModuleResult::StorageError);
}

NativeModuleResult NativeModuleManager::abort(
    const std::uint8_t*, std::size_t size) {
  if (size != kNativeModuleAbortBytes) return finish(NativeModuleResult::InvalidSize);
  store_->abort_part();
  preflight_descriptor_ = {};
  transfer_descriptor_ = {};
  preflight_token_ = 0;
  transfer_received_ = 0;
  transfer_state_ = ledger_.staged.present
      ? NativeModuleTransferState::Staged
      : executing_ ? NativeModuleTransferState::Active
                   : NativeModuleTransferState::Idle;
  return finish(NativeModuleResult::Ok);
}

NativeModuleResult NativeModuleManager::restore(
    const std::uint8_t* command, std::size_t size) {
  if (size != kNativeModuleRestoreBytes || read_u64(command + 1) != ledger_.generation) {
    return finish(NativeModuleResult::Conflict);
  }
  NativeModuleLedger candidate{};
  candidate.generation = ledger_.generation;
  const NativeModuleBinding* known[] = {
      &ledger_.active, &ledger_.staged, &ledger_.rollback,
      &displaced_binding_};
  NativeModuleBinding* destination[] = {
      &candidate.active, &candidate.staged, &candidate.rollback};
  for (std::size_t slot = 0; slot < 3; ++slot) {
    const std::uint8_t* encoded = command + 9 + slot * 65U;
    if (encoded[0] > 1) return finish(NativeModuleResult::InvalidSize);
    if (encoded[0] == 0) {
      if (!all_zero(encoded + 1, 64)) return finish(NativeModuleResult::Conflict);
      continue;
    }
    bool found = false;
    for (const NativeModuleBinding* binding : known) {
      if (binding_digest_equal(*binding, encoded + 1, encoded + 33)) {
        *destination[slot] = *binding;
        found = true;
        break;
      }
    }
    if (!found || !binding_cached(*destination[slot])) {
      return finish(NativeModuleResult::NotFound);
    }
  }
  if (executing_) {
    if (!phase_begin(NativeModulePhase::Cleanup,
                     ledger_.active.descriptor.payload_digest)) {
      return fail_phase(NativeModulePhase::Cleanup,
                        NativeModuleResult::StorageError);
    }
    const bool cleaned = backend_->cleanup();
    backend_state_may_exist_ = false;
    std::uint16_t elapsed = 0;
    const bool cleanup_timed = phase_end(NativeModulePhase::Cleanup, &elapsed);
    if (!cleanup_timed) {
      return fail_phase(NativeModulePhase::Cleanup,
                        NativeModuleResult::Watchdog);
    }
    if (!cleaned) {
      return fail_phase(NativeModulePhase::Cleanup,
                        NativeModuleResult::CleanupFailed);
    }
    if (!phase_begin(NativeModulePhase::Unload,
                     ledger_.active.descriptor.payload_digest)) {
      return fail_phase(NativeModulePhase::Unload,
                        NativeModuleResult::StorageError);
    }
    const bool unloaded = backend_->unload();
    backend_module_may_be_loaded_ = false;
    const bool unload_timed = phase_end(NativeModulePhase::Unload, &elapsed);
    if (!unload_timed) {
      return fail_phase(NativeModulePhase::Unload,
                        NativeModuleResult::Watchdog);
    }
    if (!unloaded) {
      return fail_phase(NativeModulePhase::Unload,
                        NativeModuleResult::UnloadFailed);
    }
    executing_ = false;
  }
  if (candidate.generation == UINT64_MAX) return finish(NativeModuleResult::InvalidState);
  ++candidate.generation;
  if (!save_ledger(candidate)) return finish(NativeModuleResult::StorageError);
  displaced_binding_ = {};
  transfer_state_ = candidate.staged.present
      ? NativeModuleTransferState::Staged : NativeModuleTransferState::Idle;
  restores_ = increment_u16(restores_);
  return finish(NativeModuleResult::Ok);
}

NativeModuleResult NativeModuleManager::clear_quarantine(
    const std::uint8_t* command, std::size_t size) {
  if (size != kNativeModuleQuarantineClearBytes ||
      all_zero(command + 1, 32)) {
    return finish(NativeModuleResult::InvalidSize);
  }
  if (!digest_equal(command + 1, quarantine_payload_)) {
    return finish(NativeModuleResult::NotFound);
  }
  std::memset(quarantine_payload_, 0, 32);
  attributed_phase_ = NativeModulePhase::None;
  if (!save_state()) return finish(NativeModuleResult::StorageError);
  transfer_state_ = ledger_.staged.present
      ? NativeModuleTransferState::Staged : NativeModuleTransferState::Idle;
  return finish(NativeModuleResult::Ok);
}

NativeModuleResult NativeModuleManager::process(
    const std::uint8_t* command, std::size_t size) {
  if (!enabled_) return finish(NativeModuleResult::Unsupported);
  if (!initialized_ || command == nullptr || size == 0) {
    return finish(NativeModuleResult::InvalidState);
  }
  switch (command[0]) {
    case 0x50: return probe(command, size);
    case 0x51: return preflight(command, size);
    case 0x52: return begin_transfer(command, size);
    case 0x53: return chunk(command, size);
    case 0x54: return finalize(command, size);
    case 0x55: return verify(command, size);
    case 0x56: return activate(command, size);
    case 0x57: return stop(command, size);
    case 0x58: return update_parameters(command, size);
    case 0x59: return remove(command, size);
    case 0x5A: return abort(command, size);
    case 0x5B: return restore(command, size);
    case 0x5C: return clear_quarantine(command, size);
    default: return finish(NativeModuleResult::InvalidCommand);
  }
}

bool NativeModuleManager::render(
    std::uint64_t unscaled_scene_time_us,
    std::uint64_t scaled_scene_time_us, std::uint64_t frame_index,
    std::uint8_t* rgb_output, std::size_t rgb_output_size,
    NativeModuleRenderResult* result) {
  if (!executing_ || result == nullptr || rgb_output == nullptr ||
      rgb_output_size != static_cast<std::size_t>(topology_.local_strips) *
                             topology_.leds_per_strip * 3U) {
    finish(NativeModuleResult::InvalidState);
    return false;
  }
  if (!phase_begin(NativeModulePhase::Render,
                   ledger_.active.descriptor.payload_digest)) {
    fail_phase(NativeModulePhase::Render, NativeModuleResult::StorageError);
    return false;
  }
  const bool rendered = backend_->render(
      unscaled_scene_time_us, scaled_scene_time_us, frame_index, rgb_output,
      rgb_output_size, result);
  std::uint16_t elapsed = 0;
  const bool timed = phase_end(NativeModulePhase::Render, &elapsed);
  last_render_us_ = elapsed;
  if (!timed) {
    fail_phase(NativeModulePhase::Render, NativeModuleResult::Watchdog);
    return false;
  }
  if (!rendered) {
    fail_phase(NativeModulePhase::Render, NativeModuleResult::RenderFailed);
    return false;
  }
  const std::uint64_t cadence_period_us =
      (1000000ULL + ledger_.active.descriptor.cadence_hz - 1U) /
      ledger_.active.descriptor.cadence_hz;
  if ((!rendered_once_ && !result->changed) ||
      result->next_deadline_scene_time_us <= unscaled_scene_time_us ||
      result->next_deadline_scene_time_us >
          unscaled_scene_time_us + cadence_period_us ||
      (last_native_deadline_scene_time_us_ != 0 &&
       result->next_deadline_scene_time_us <
           last_native_deadline_scene_time_us_)) {
    fail_phase(NativeModulePhase::Render, NativeModuleResult::RenderFailed);
    return false;
  }
  rendered_once_ = true;
  last_native_deadline_scene_time_us_ =
      result->next_deadline_scene_time_us;
  finish(NativeModuleResult::Ok);
  return true;
}

void NativeModuleManager::host_takeover() {
  if (executing_ || backend_module_may_be_loaded_ ||
      backend_state_may_exist_) {
    // Complete host frames must win even when cleanup is unhealthy. Persist
    // attribution for boot recovery, but never block host ownership here.
    std::uint8_t payload[32] = {};
    if (ledger_.active.present) {
      std::memcpy(payload, ledger_.active.descriptor.payload_digest, 32);
    } else if (!all_zero(quarantine_payload_, 32)) {
      std::memcpy(payload, quarantine_payload_, 32);
    } else {
      std::memcpy(payload, phase_payload_digest_, 32);
    }
    bool recovery_watchdog = false;
    const NativeModulePhase recovery_failure =
        recover_backend(payload, &recovery_watchdog);
    if (recovery_failure != NativeModulePhase::None) {
      std::memcpy(quarantine_payload_, payload, 32);
      attributed_phase_ = recovery_failure;
      if (recovery_watchdog) {
        watchdog_events_ = increment_u16(watchdog_events_);
      }
      quarantines_ = increment_u16(quarantines_);
    }
  }
  NativeModuleLedger candidate = ledger_;
  if (candidate.active.present) candidate.rollback = candidate.active;
  clear_binding(&candidate.active);
  if (candidate.generation != UINT64_MAX) ++candidate.generation;
  save_ledger(candidate);
  executing_ = false;
  rendered_once_ = false;
  last_native_deadline_scene_time_us_ = 0;
  transfer_state_ = candidate.staged.present
      ? NativeModuleTransferState::Staged : NativeModuleTransferState::Idle;
}

void NativeModuleManager::copy_status_binding(
    const NativeModuleBinding& binding, std::uint8_t bundle[32],
    std::uint8_t payload[32]) const {
  if (!binding.present) return;
  std::memcpy(bundle, binding.descriptor.bundle_digest, 32);
  std::memcpy(payload, binding.descriptor.payload_digest, 32);
}

NativeModuleStatusV1 NativeModuleManager::status() const {
  NativeModuleStatusV1 status{};
  status.result = result_;
  status.transfer_state = transfer_state_;
  status.watchdog_phase = attributed_phase_;
  status.flags = (initialized_ ? 1U : 0U) |
                 (last_probe_found_ ? 2U : 0U) |
                 (cache_integrity_ok_ ? 4U : 0U) |
                 (ledger_.active.present ? 8U : 0U) |
                 (ledger_.staged.present ? 16U : 0U) |
                 (ledger_.rollback.present ? 32U : 0U) |
                 (!all_zero(quarantine_payload_, 32) ? 64U : 0U) |
                 (executing_ ? 128U : 0U);
  if (store_ != nullptr && store_->ready()) {
    status.capacity_bytes = store_->capacity_bytes();
    status.used_bytes = store_->used_bytes();
    status.free_bytes = status.capacity_bytes > status.used_bytes
        ? status.capacity_bytes - status.used_bytes : 0;
    status.reserve_bytes = store_->reserve_bytes();
  }
  status.reclaimable_bytes = preflight_reclaimable_;
  status.received_bytes = transfer_received_;
  status.total_bytes = transfer_descriptor_.payload_size;
  status.state_generation = ledger_.generation;
  status.preflight_token = preflight_token_;
  std::memcpy(status.last_probe_payload_digest,
              transfer_descriptor_.payload_digest, 32);
  std::memcpy(status.transfer_bundle_digest,
              transfer_descriptor_.bundle_digest, 32);
  std::memcpy(status.transfer_payload_digest,
              transfer_descriptor_.payload_digest, 32);
  copy_status_binding(ledger_.active, status.active_bundle_digest,
                      status.active_payload_digest);
  copy_status_binding(ledger_.staged, status.staged_bundle_digest,
                      status.staged_payload_digest);
  copy_status_binding(ledger_.rollback, status.rollback_bundle_digest,
                      status.rollback_payload_digest);
  std::memcpy(status.quarantine_payload_digest, quarantine_payload_, 32);
  if (ledger_.active.present) {
    const auto& descriptor = ledger_.active.descriptor;
    status.active_parameter_schema_revision =
        descriptor.parameter_schema_revision;
    status.active_cadence_hz = descriptor.cadence_hz;
    status.active_local_strips = descriptor.local_strips;
    status.active_target = descriptor.target;
    status.active_global_strips = descriptor.global_strips;
    status.active_leds_per_strip = descriptor.leds_per_strip;
    status.active_global_strip_offset = descriptor.global_strip_offset;
    status.active_parameter_size = active_parameters_.canonical_size;
    std::memcpy(status.active_parameter_digest, active_parameters_.digest, 32);
  }
  status.last_load_us = last_load_us_;
  status.last_initialize_us = last_initialize_us_;
  status.last_context_us = last_context_us_;
  status.last_render_us = last_render_us_;
  status.max_phase_us = max_phase_us_;
  status.watchdog_events = watchdog_events_;
  status.writes = writes_;
  status.evictions = evictions_;
  status.stages = stages_;
  status.verifies = verifies_;
  status.activations = activations_;
  status.restores = restores_;
  status.quarantines = quarantines_;
  return status;
}

}  // namespace ledgrid
