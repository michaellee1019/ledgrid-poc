#include "ledgrid/installation_profile_store.hpp"

#include <algorithm>
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
  for (std::size_t index = 0; index < 8; ++index) value = (value << 8U) | input[index];
  return value;
}

bool equal_digest(const std::uint8_t* left, const std::uint8_t* right) {
  return std::memcmp(left, right, 32) == 0;
}

bool all_zero(const std::uint8_t* value, std::size_t size) {
  std::uint8_t combined = 0;
  for (std::size_t index = 0; index < size; ++index) combined |= value[index];
  return combined == 0;
}

std::uint16_t increment_u16(std::uint16_t value) {
  return value == UINT16_MAX ? value : static_cast<std::uint16_t>(value + 1U);
}

void copy_binding_to_status(
    const InstallationProfileBinding& binding,
    std::uint8_t global[32], std::uint8_t payload[32]) {
  if (!binding.present) return;
  std::memcpy(global, binding.global_id, 32);
  std::memcpy(payload, binding.payload_digest, 32);
}

}  // namespace

bool installation_profile_binding_equal(
    const InstallationProfileBinding& left,
    const InstallationProfileBinding& right) {
  if (left.present != right.present) return false;
  return !left.present ||
      (equal_digest(left.global_id, right.global_id) &&
       equal_digest(left.payload_digest, right.payload_digest));
}

InstallationProfileManager::InstallationProfileManager(
    InstallationProfileStore* store,
    InstallationProfilePersistence* persistence,
    std::uint8_t* scratch,
    std::size_t scratch_size,
    bool enabled)
    : store_(store), persistence_(persistence), scratch_(scratch),
      scratch_size_(scratch_size), enabled_(enabled) {}

bool InstallationProfileManager::begin() {
  if (!enabled_) return false;
  if (store_ == nullptr || persistence_ == nullptr || scratch_ == nullptr ||
      scratch_size_ < 2U * kInstallationProfileReceiverBytesV1 ||
      !store_->ready()) {
    cache_integrity_ok_ = false;
    result_ = InstallationProfileResult::StorageError;
    return false;
  }
  InstallationProfileLedger loaded{};
  if (!persistence_->load(&loaded)) {
    cache_integrity_ok_ = false;
    result_ = InstallationProfileResult::IntegrityError;
    return false;
  }
  ledger_ = loaded;
  initialized_ = true;
  transfer_state_ = ledger_.staged.present
      ? InstallationProfileTransferState::Staged
      : InstallationProfileTransferState::Idle;
  // Installed direction is provisioned later by CONFIG. Persisted bindings are
  // deliberately not decoded against a guessed identity during early boot.
  if (logical_receiver_id_ <= 3) configure_identity(logical_receiver_id_, reversed_);
  return true;
}

void InstallationProfileManager::configure_identity(
    std::uint8_t logical_receiver_id, bool reversed) {
  logical_receiver_id_ = logical_receiver_id;
  reversed_ = reversed;
  static constexpr std::uint16_t kInstalledOrigins[4] = {0, 8, 24, 16};
  expected_origin_ = logical_receiver_id_ <= 3
      ? kInstalledOrigins[logical_receiver_id_] : 0;
  if (!initialized_ || logical_receiver_id_ > 3) {
    active_view_ = {};
    return;
  }
  bool repaired = false;
  for (InstallationProfileBinding* binding :
       {&ledger_.active, &ledger_.staged, &ledger_.rollback}) {
    if (binding->present && !binding_valid(*binding)) {
      *binding = {};
      repaired = true;
    }
  }
  if (repaired) {
    cache_integrity_ok_ = false;
    result_ = InstallationProfileResult::IntegrityError;
    if (ledger_.generation != UINT64_MAX) ++ledger_.generation;
    if (!persistence_->save(ledger_)) {
      result_ = InstallationProfileResult::StorageError;
      active_view_ = {};
      return;
    }
  }
  if (!refresh_active_view(ledger_)) {
    active_view_ = {};
    cache_integrity_ok_ = false;
    result_ = InstallationProfileResult::IntegrityError;
    return;
  }
  // Integrity reports the current fail-closed ledger/cache relationship. Keep
  // the result/decoder fields as history, but permit a cleared bad binding to
  // be repaired by a subsequent authenticated install.
  cache_integrity_ok_ = bindings_valid(ledger_);
}

InstallationProfileResult InstallationProfileManager::finish(
    InstallationProfileResult result) {
  result_ = result;
  if (result != InstallationProfileResult::Ok &&
      result != InstallationProfileResult::None &&
      transfer_state_ == InstallationProfileTransferState::Finalizing) {
    transfer_state_ = InstallationProfileTransferState::Failed;
  }
  return result;
}

std::uint64_t InstallationProfileManager::calculate_preflight_token() const {
  std::uint64_t hash = 1469598103934665603ULL;
  auto mix = [&](const std::uint8_t* data, std::size_t size) {
    for (std::size_t index = 0; index < size; ++index) {
      hash = (hash ^ data[index]) * 1099511628211ULL;
    }
  };
  mix(preflight_global_id_, 32);
  mix(preflight_payload_digest_, 32);
  const std::uint64_t values[] = {
      ledger_.generation, store_ == nullptr ? 0 : store_->mutation_generation(),
      transfer_total_, logical_receiver_id_, reversed_ ? 1U : 0U};
  mix(reinterpret_cast<const std::uint8_t*>(values), sizeof(values));
  return hash == 0 ? 1 : hash;
}

InstallationProfileResult InstallationProfileManager::preflight(
    const std::uint8_t* command, std::size_t size) {
  if (size != 69) return finish(InstallationProfileResult::InvalidSize);
  if (!initialized_ || logical_receiver_id_ > 3) {
    return finish(InstallationProfileResult::InvalidState);
  }
  const std::uint32_t total = read_u32(command + 65);
  if (total != kInstallationProfileReceiverBytesV1 ||
      all_zero(command + 1, 32) || all_zero(command + 33, 32)) {
    return finish(InstallationProfileResult::InvalidSize);
  }
  if (ledger_.staged.present &&
      (!equal_digest(ledger_.staged.global_id, command + 1) ||
       !equal_digest(ledger_.staged.payload_digest, command + 33))) {
    return finish(InstallationProfileResult::InvalidState);
  }
  std::memcpy(preflight_global_id_, command + 1, 32);
  std::memcpy(preflight_payload_digest_, command + 33, 32);
  transfer_total_ = total;
  std::uint32_t committed_size = 0;
  last_probe_found_ = store_->probe(command + 33, &committed_size) &&
                      committed_size == total;
  preflight_reclaimable_ = 0;
  preflight_can_stage_ = last_probe_found_ ||
      store_->can_stage(total, ledger_, &preflight_reclaimable_);
  if (!preflight_can_stage_) {
    preflight_token_ = 0;
    transfer_state_ = InstallationProfileTransferState::Failed;
    return finish(InstallationProfileResult::NoSpace);
  }
  preflight_token_ = calculate_preflight_token();
  transfer_state_ = InstallationProfileTransferState::PreflightReady;
  return finish(InstallationProfileResult::Ok);
}

InstallationProfileResult InstallationProfileManager::begin_transfer(
    const std::uint8_t* command, std::size_t size) {
  if (size != 81) return finish(InstallationProfileResult::InvalidSize);
  if (transfer_state_ != InstallationProfileTransferState::PreflightReady ||
      read_u64(command + 1) != preflight_token_ ||
      preflight_token_ != calculate_preflight_token()) {
    return finish(InstallationProfileResult::InvalidToken);
  }
  if (!equal_digest(command + 9, preflight_global_id_) ||
      !equal_digest(command + 41, preflight_payload_digest_) ||
      read_u32(command + 73) != transfer_total_) {
    return finish(InstallationProfileResult::Conflict);
  }
  if (command[77] != logical_receiver_id_) {
    return finish(InstallationProfileResult::WrongDevice);
  }
  transfer_origin_ = read_u16(command + 78);
  if (transfer_origin_ != expected_origin_ ||
      (command[80] & ~1U) != 0 || ((command[80] & 1U) != 0) != reversed_) {
    return finish(InstallationProfileResult::WrongGeometry);
  }
  std::memcpy(transfer_global_id_, command + 9, 32);
  std::memcpy(transfer_payload_digest_, command + 41, 32);
  transfer_received_ = 0;
  if (last_probe_found_) {
    InstallationProfileBinding binding{};
    binding.present = true;
    std::memcpy(binding.global_id, transfer_global_id_, 32);
    std::memcpy(binding.payload_digest, transfer_payload_digest_, 32);
    InstallationProfileError error = InstallationProfileError::None;
    if (!binding_valid(binding, nullptr, &error)) {
      decoder_error_ = error;
      cache_integrity_ok_ = false;
      for (const auto* pinned :
           {&ledger_.active, &ledger_.staged, &ledger_.rollback}) {
        if (pinned->present &&
            equal_digest(pinned->payload_digest, transfer_payload_digest_)) {
          return finish(InstallationProfileResult::IntegrityError);
        }
      }
      // A committed object whose name/metadata matches but whose bytes do not
      // is an inactive cache miss, not a permanently poisoned hit.
      if (!store_->remove(transfer_payload_digest_)) {
        return finish(InstallationProfileResult::StorageError);
      }
      last_probe_found_ = false;
    } else {
      InstallationProfileLedger candidate = ledger_;
      candidate.staged = binding;
      if (candidate.generation == UINT64_MAX) {
        return finish(InstallationProfileResult::InvalidState);
      }
      ++candidate.generation;
      if (!save_ledger(candidate)) return finish(InstallationProfileResult::StorageError);
      cache_integrity_ok_ = bindings_valid(ledger_);
      transfer_received_ = transfer_total_;
      transfer_state_ = InstallationProfileTransferState::Staged;
      stages_ = increment_u16(stages_);
      return finish(InstallationProfileResult::Ok);
    }
  }
  std::uint32_t evicted = 0;
  if (!store_->begin_part(
          transfer_payload_digest_, transfer_total_, ledger_, &evicted)) {
    transfer_state_ = InstallationProfileTransferState::Failed;
    return finish(InstallationProfileResult::StorageError);
  }
  evictions_ = UINT32_MAX - evictions_ < evicted ? UINT32_MAX : evictions_ + evicted;
  transfer_state_ = InstallationProfileTransferState::Receiving;
  return finish(InstallationProfileResult::Ok);
}

InstallationProfileResult InstallationProfileManager::chunk(
    const std::uint8_t* command, std::size_t size) {
  if (size < 6 || size > kAnimationPipelineMaxTransactionBytes -
                           kAnimationPipelineCrcBytes) {
    return finish(InstallationProfileResult::InvalidSize);
  }
  if (transfer_state_ != InstallationProfileTransferState::Receiving) {
    return finish(InstallationProfileResult::InvalidState);
  }
  const std::uint32_t offset = read_u32(command + 1);
  const std::size_t amount = size - kInstallationProfileChunkHeaderBytes;
  if (amount > kInstallationProfileMaxChunkBytes || offset > transfer_total_ ||
      amount > transfer_total_ - offset) {
    return finish(InstallationProfileResult::InvalidSize);
  }
  if (offset < transfer_received_) {
    if (amount > transfer_received_ - offset || amount > scratch_size_) {
      return finish(InstallationProfileResult::InvalidOffset);
    }
    std::uint8_t* work = scratch_ + kInstallationProfileReceiverBytesV1;
    if (!store_->read_part(offset, work, amount)) {
      return finish(InstallationProfileResult::StorageError);
    }
    return finish(std::memcmp(work, command + 5, amount) == 0
                      ? InstallationProfileResult::Ok
                      : InstallationProfileResult::Conflict);
  }
  if (offset != transfer_received_) {
    return finish(InstallationProfileResult::InvalidOffset);
  }
  if (!store_->write_part(offset, command + 5, amount)) {
    transfer_state_ = InstallationProfileTransferState::Failed;
    return finish(InstallationProfileResult::StorageError);
  }
  transfer_received_ += static_cast<std::uint32_t>(amount);
  writes_ = writes_ == UINT32_MAX ? writes_ : writes_ + 1U;
  return finish(InstallationProfileResult::Ok);
}

InstallationProfileResult InstallationProfileManager::finalize(
    const std::uint8_t* command, std::size_t size) {
  if (size != 65) return finish(InstallationProfileResult::InvalidSize);
  if (transfer_state_ == InstallationProfileTransferState::Staged &&
      equal_digest(command + 1, transfer_global_id_) &&
      equal_digest(command + 33, transfer_payload_digest_)) {
    return finish(InstallationProfileResult::Ok);
  }
  if (transfer_state_ != InstallationProfileTransferState::Receiving ||
      transfer_received_ != transfer_total_) {
    return finish(InstallationProfileResult::InvalidState);
  }
  if (!equal_digest(command + 1, transfer_global_id_) ||
      !equal_digest(command + 33, transfer_payload_digest_)) {
    return finish(InstallationProfileResult::Conflict);
  }
  transfer_state_ = InstallationProfileTransferState::Finalizing;
  std::uint8_t* work = scratch_ + kInstallationProfileReceiverBytesV1;
  if (!store_->read_part(0, work, transfer_total_)) {
    return finish(InstallationProfileResult::StorageError);
  }
  std::uint8_t digest[32] = {};
  sha256(work, transfer_total_, digest);
  if (!equal_digest(digest, transfer_payload_digest_)) {
    store_->abort_part();
    return finish(InstallationProfileResult::DigestMismatch);
  }
  InstallationProfileViewV1 view{};
  InstallationProfileReceiverExpectationV1 expectation{
      transfer_origin_, reversed_};
  if (!decode_installation_profile_receiver_v1(
          work, transfer_total_, expectation, &view, &decoder_error_)) {
    store_->abort_part();
    return finish(InstallationProfileResult::InvalidProfile);
  }
  if (!store_->commit_part(transfer_payload_digest_)) {
    return finish(InstallationProfileResult::StorageError);
  }
  InstallationProfileLedger candidate = ledger_;
  candidate.staged.present = true;
  std::memcpy(candidate.staged.global_id, transfer_global_id_, 32);
  std::memcpy(candidate.staged.payload_digest, transfer_payload_digest_, 32);
  if (candidate.generation == UINT64_MAX) {
    return finish(InstallationProfileResult::InvalidState);
  }
  ++candidate.generation;
  if (!save_ledger(candidate)) return finish(InstallationProfileResult::StorageError);
  cache_integrity_ok_ = bindings_valid(ledger_);
  transfer_state_ = InstallationProfileTransferState::Staged;
  stages_ = increment_u16(stages_);
  return finish(InstallationProfileResult::Ok);
}

bool InstallationProfileManager::binding_valid(
    const InstallationProfileBinding& binding,
    InstallationProfileViewV1* output,
    InstallationProfileError* error) const {
  if (!binding.present) {
    if (output != nullptr) *output = {};
    if (error != nullptr) *error = InstallationProfileError::None;
    return true;
  }
  std::uint32_t size = 0;
  if (store_ == nullptr || !store_->probe(binding.payload_digest, &size) ||
      size != kInstallationProfileReceiverBytesV1 ||
      scratch_size_ < 2U * size) return false;
  std::uint8_t* work = scratch_ + kInstallationProfileReceiverBytesV1;
  if (!store_->read_committed(binding.payload_digest, 0, work, size)) return false;
  std::uint8_t digest[32] = {};
  sha256(work, size, digest);
  if (!equal_digest(digest, binding.payload_digest)) return false;
  InstallationProfileReceiverExpectationV1 expectation{
      expected_origin_, reversed_};
  InstallationProfileViewV1 view{};
  if (!decode_installation_profile_receiver_v1(
          work, size, expectation, &view, error)) return false;
  if (!store_->touch(binding.payload_digest)) return false;
  if (output != nullptr) *output = view;
  return true;
}

bool InstallationProfileManager::bindings_valid(
    const InstallationProfileLedger& ledger) const {
  return binding_valid(ledger.active) && binding_valid(ledger.staged) &&
         binding_valid(ledger.rollback);
}

InstallationProfileResult InstallationProfileManager::verify(
    const std::uint8_t* command, std::size_t size) {
  if (size != 65) return finish(InstallationProfileResult::InvalidSize);
  InstallationProfileBinding binding{};
  binding.present = true;
  std::memcpy(binding.global_id, command + 1, 32);
  std::memcpy(binding.payload_digest, command + 33, 32);
  if (!ledger_.staged.present ||
      !installation_profile_binding_equal(binding, ledger_.staged)) {
    return finish(InstallationProfileResult::InvalidState);
  }
  if (!binding_valid(binding, nullptr, &decoder_error_)) {
    cache_integrity_ok_ = false;
    return finish(InstallationProfileResult::IntegrityError);
  }
  verifies_ = increment_u16(verifies_);
  return finish(InstallationProfileResult::Ok);
}

bool InstallationProfileManager::refresh_active_view(
    const InstallationProfileLedger& candidate) {
  active_view_ = {};
  if (!candidate.active.present) return true;
  std::uint32_t size = 0;
  if (!store_->probe(candidate.active.payload_digest, &size) ||
      size != kInstallationProfileReceiverBytesV1 ||
      !store_->read_committed(candidate.active.payload_digest, 0, scratch_, size)) {
    return false;
  }
  std::uint8_t digest[32] = {};
  sha256(scratch_, size, digest);
  if (!equal_digest(digest, candidate.active.payload_digest)) return false;
  InstallationProfileReceiverExpectationV1 expectation{
      expected_origin_, reversed_};
  if (!decode_installation_profile_receiver_v1(
          scratch_, size, expectation, &active_view_, &decoder_error_)) {
    return false;
  }
  return store_->touch(candidate.active.payload_digest);
}

bool InstallationProfileManager::save_ledger(
    const InstallationProfileLedger& candidate) {
  if (!refresh_active_view(candidate)) return false;
  if (!persistence_->save(candidate)) {
    refresh_active_view(ledger_);
    return false;
  }
  ledger_ = candidate;
  return true;
}

InstallationProfileResult InstallationProfileManager::activate(
    const std::uint8_t* command, std::size_t size) {
  if (size != 73) return finish(InstallationProfileResult::InvalidSize);
  const std::uint64_t expected = read_u64(command + 1);
  InstallationProfileBinding desired{};
  desired.present = true;
  std::memcpy(desired.global_id, command + 9, 32);
  std::memcpy(desired.payload_digest, command + 41, 32);
  if (ledger_.active.present &&
      installation_profile_binding_equal(ledger_.active, desired) &&
      !ledger_.staged.present) {
    return finish(ledger_.generation != 0 && expected == ledger_.generation - 1
                      ? InstallationProfileResult::Ok
                      : InstallationProfileResult::Conflict);
  }
  if (expected != ledger_.generation) return finish(InstallationProfileResult::Conflict);
  if (!installation_profile_binding_equal(ledger_.staged, desired) ||
      !binding_valid(desired, nullptr, &decoder_error_)) {
    return finish(InstallationProfileResult::InvalidState);
  }
  InstallationProfileLedger candidate = ledger_;
  candidate.rollback = ledger_.active;
  candidate.active = desired;
  candidate.staged = {};
  if (candidate.generation == UINT64_MAX) return finish(InstallationProfileResult::InvalidState);
  ++candidate.generation;
  if (!save_ledger(candidate)) return finish(InstallationProfileResult::StorageError);
  transfer_state_ = InstallationProfileTransferState::Idle;
  activations_ = increment_u16(activations_);
  return finish(InstallationProfileResult::Ok);
}

InstallationProfileResult InstallationProfileManager::restore(
    const std::uint8_t* command, std::size_t size) {
  if (size != 204) return finish(InstallationProfileResult::InvalidSize);
  if (read_u64(command + 1) != ledger_.generation) {
    return finish(InstallationProfileResult::Conflict);
  }
  InstallationProfileLedger candidate = ledger_;
  InstallationProfileBinding* bindings[] = {
      &candidate.active, &candidate.staged, &candidate.rollback};
  std::size_t offset = 9;
  for (auto* binding : bindings) {
    if (command[offset] > 1) return finish(InstallationProfileResult::InvalidSize);
    binding->present = command[offset] == 1;
    std::memcpy(binding->global_id, command + offset + 1, 32);
    std::memcpy(binding->payload_digest, command + offset + 33, 32);
    if ((!binding->present &&
         (!all_zero(binding->global_id, 32) ||
          !all_zero(binding->payload_digest, 32))) ||
        (binding->present &&
         (all_zero(binding->global_id, 32) ||
          all_zero(binding->payload_digest, 32) ||
          !binding_valid(*binding, nullptr, &decoder_error_)))) {
      return finish(InstallationProfileResult::IntegrityError);
    }
    offset += 65;
  }
  if (candidate.generation == UINT64_MAX) return finish(InstallationProfileResult::InvalidState);
  ++candidate.generation;
  if (!save_ledger(candidate)) return finish(InstallationProfileResult::StorageError);
  transfer_state_ = candidate.staged.present
      ? InstallationProfileTransferState::Staged
      : InstallationProfileTransferState::Idle;
  restores_ = increment_u16(restores_);
  return finish(InstallationProfileResult::Ok);
}

InstallationProfileResult InstallationProfileManager::abort(
    const std::uint8_t*, std::size_t size) {
  if (size != 1) return finish(InstallationProfileResult::InvalidSize);
  store_->abort_part();
  transfer_total_ = 0;
  transfer_received_ = 0;
  preflight_token_ = 0;
  preflight_can_stage_ = false;
  std::memset(transfer_global_id_, 0, 32);
  std::memset(transfer_payload_digest_, 0, 32);
  transfer_state_ = ledger_.staged.present
      ? InstallationProfileTransferState::Staged
      : InstallationProfileTransferState::Idle;
  return finish(InstallationProfileResult::Ok);
}

InstallationProfileResult InstallationProfileManager::process(
    const std::uint8_t* command, std::size_t size) {
  if (!enabled_) return finish(InstallationProfileResult::Unsupported);
  if (command == nullptr || size == 0) return finish(InstallationProfileResult::InvalidSize);
  switch (static_cast<ReceiverCommand>(command[0])) {
    case ReceiverCommand::InstallationProfilePreflight: return preflight(command, size);
    case ReceiverCommand::InstallationProfileBegin: return begin_transfer(command, size);
    case ReceiverCommand::InstallationProfileChunk: return chunk(command, size);
    case ReceiverCommand::InstallationProfileFinalize: return finalize(command, size);
    case ReceiverCommand::InstallationProfileVerify: return verify(command, size);
    case ReceiverCommand::InstallationProfileActivate: return activate(command, size);
    case ReceiverCommand::InstallationProfileRestore: return restore(command, size);
    case ReceiverCommand::InstallationProfileAbort: return abort(command, size);
    default: return finish(InstallationProfileResult::Unsupported);
  }
}

InstallationProfileStatusV1 InstallationProfileManager::status() const {
  InstallationProfileStatusV1 status{};
  status.result = result_;
  status.transfer_state = transfer_state_;
  status.decoder_error = static_cast<std::uint8_t>(decoder_error_);
  status.flags = (cache_integrity_ok_ ? 1U : 0U) |
                 (preflight_can_stage_ ? 1U << 1U : 0U) |
                 (last_probe_found_ ? 1U << 2U : 0U) |
                 (ledger_.active.present ? 1U << 3U : 0U) |
                 (ledger_.staged.present ? 1U << 4U : 0U) |
                 (ledger_.rollback.present ? 1U << 5U : 0U) |
                 (transfer_state_ == InstallationProfileTransferState::Receiving ||
                          transfer_state_ == InstallationProfileTransferState::Finalizing
                      ? 1U << 6U : 0U);
  if (store_ != nullptr && store_->ready()) {
    status.capacity_bytes = store_->capacity_bytes();
    status.used_bytes = store_->used_bytes();
    status.free_bytes = status.capacity_bytes > status.used_bytes
        ? status.capacity_bytes - status.used_bytes : 0;
    status.reserve_bytes = store_->reserve_bytes();
  }
  status.reclaimable_bytes = preflight_reclaimable_;
  status.received_bytes = transfer_received_;
  status.total_bytes = transfer_total_;
  status.state_generation = ledger_.generation;
  status.preflight_token = preflight_token_;
  std::memcpy(status.last_probe_payload_digest, preflight_payload_digest_, 32);
  std::memcpy(status.transfer_global_id, transfer_global_id_, 32);
  std::memcpy(status.transfer_payload_digest, transfer_payload_digest_, 32);
  copy_binding_to_status(ledger_.active, status.active_global_id,
                         status.active_payload_digest);
  copy_binding_to_status(ledger_.staged, status.staged_global_id,
                         status.staged_payload_digest);
  copy_binding_to_status(ledger_.rollback, status.rollback_global_id,
                         status.rollback_payload_digest);
  status.writes = writes_;
  status.evictions = evictions_;
  status.stages = stages_;
  status.verifies = verifies_;
  status.activations = activations_;
  status.restores = restores_;
  return status;
}

}  // namespace ledgrid
