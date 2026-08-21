#pragma once

#include <cstddef>
#include <cstdint>

#include "ledgrid/installation_profile.hpp"
#include "ledgrid/protocol.hpp"

#ifndef LEDGRID_ENABLE_INSTALLATION_PROFILES
#define LEDGRID_ENABLE_INSTALLATION_PROFILES 0
#endif

namespace ledgrid {

constexpr std::uint32_t kInstallationProfileCacheReserveBytes = 512U * 1024U;
constexpr std::size_t kInstallationProfileMaxChunkBytes =
    kAnimationPipelineMaxTransactionBytes - kAnimationPipelineCrcBytes -
    kInstallationProfileChunkHeaderBytes;

struct InstallationProfileBinding {
  bool present = false;
  std::uint8_t global_id[32] = {};
  std::uint8_t payload_digest[32] = {};
};

struct InstallationProfileLedger {
  std::uint64_t generation = 0;
  InstallationProfileBinding active{};
  InstallationProfileBinding staged{};
  InstallationProfileBinding rollback{};
};

class InstallationProfileStore {
 public:
  virtual ~InstallationProfileStore() = default;
  virtual bool ready() const = 0;
  virtual std::uint32_t capacity_bytes() const = 0;
  virtual std::uint32_t used_bytes() const = 0;
  virtual std::uint32_t reserve_bytes() const = 0;
  virtual std::uint64_t mutation_generation() const = 0;
  virtual bool probe(const std::uint8_t digest[32], std::uint32_t* size) const = 0;
  virtual bool touch(const std::uint8_t digest[32]) = 0;
  virtual bool can_stage(
      std::uint32_t size, const InstallationProfileLedger& pins,
      std::uint32_t* reclaimable) const = 0;
  virtual bool begin_part(
      const std::uint8_t digest[32], std::uint32_t size,
      const InstallationProfileLedger& pins, std::uint32_t* evicted) = 0;
  virtual bool write_part(
      std::uint32_t offset, const std::uint8_t* data, std::size_t size) = 0;
  virtual bool read_part(
      std::uint32_t offset, std::uint8_t* data, std::size_t size) const = 0;
  virtual bool commit_part(const std::uint8_t digest[32]) = 0;
  virtual void abort_part() = 0;
  virtual bool read_committed(
      const std::uint8_t digest[32], std::uint32_t offset,
      std::uint8_t* data, std::size_t size) const = 0;
  virtual bool remove(const std::uint8_t digest[32]) = 0;
};

class InstallationProfilePersistence {
 public:
  virtual ~InstallationProfilePersistence() = default;
  virtual bool load(InstallationProfileLedger* ledger) = 0;
  virtual bool save(const InstallationProfileLedger& ledger) = 0;
};

class InstallationProfileManager {
 public:
  InstallationProfileManager(
      InstallationProfileStore* store,
      InstallationProfilePersistence* persistence,
      std::uint8_t* scratch,
      std::size_t scratch_size,
      bool enabled = LEDGRID_ENABLE_INSTALLATION_PROFILES != 0);

  bool begin();
  void configure_identity(std::uint8_t logical_receiver_id, bool reversed);
  InstallationProfileResult process(
      const std::uint8_t* command, std::size_t size);
  InstallationProfileStatusV1 status() const;
  const InstallationProfileLedger& ledger() const { return ledger_; }
  const InstallationProfileViewV1& active_view() const { return active_view_; }

 private:
  InstallationProfileResult finish(InstallationProfileResult result);
  InstallationProfileResult preflight(const std::uint8_t*, std::size_t);
  InstallationProfileResult begin_transfer(const std::uint8_t*, std::size_t);
  InstallationProfileResult chunk(const std::uint8_t*, std::size_t);
  InstallationProfileResult finalize(const std::uint8_t*, std::size_t);
  InstallationProfileResult verify(const std::uint8_t*, std::size_t);
  InstallationProfileResult activate(const std::uint8_t*, std::size_t);
  InstallationProfileResult restore(const std::uint8_t*, std::size_t);
  InstallationProfileResult abort(const std::uint8_t*, std::size_t);
  bool binding_valid(
      const InstallationProfileBinding& binding,
      InstallationProfileViewV1* view = nullptr,
      InstallationProfileError* error = nullptr) const;
  bool bindings_valid(const InstallationProfileLedger& ledger) const;
  bool save_ledger(const InstallationProfileLedger& candidate);
  bool refresh_active_view(const InstallationProfileLedger& candidate);
  std::uint64_t calculate_preflight_token() const;

  InstallationProfileStore* store_ = nullptr;
  InstallationProfilePersistence* persistence_ = nullptr;
  std::uint8_t* scratch_ = nullptr;
  std::size_t scratch_size_ = 0;
  bool enabled_ = false;
  bool initialized_ = false;
  bool cache_integrity_ok_ = true;
  std::uint8_t logical_receiver_id_ = 0xFF;
  bool reversed_ = false;
  std::uint16_t expected_origin_ = 0;
  std::uint16_t transfer_origin_ = 0;
  InstallationProfileLedger ledger_{};
  InstallationProfileViewV1 active_view_{};
  InstallationProfileResult result_ = InstallationProfileResult::None;
  InstallationProfileTransferState transfer_state_ =
      InstallationProfileTransferState::Idle;
  InstallationProfileError decoder_error_ = InstallationProfileError::None;
  bool preflight_can_stage_ = false;
  bool last_probe_found_ = false;
  std::uint64_t preflight_token_ = 0;
  std::uint32_t preflight_reclaimable_ = 0;
  std::uint32_t transfer_total_ = 0;
  std::uint32_t transfer_received_ = 0;
  std::uint8_t preflight_global_id_[32] = {};
  std::uint8_t preflight_payload_digest_[32] = {};
  std::uint8_t transfer_global_id_[32] = {};
  std::uint8_t transfer_payload_digest_[32] = {};
  std::uint32_t writes_ = 0;
  std::uint32_t evictions_ = 0;
  std::uint16_t stages_ = 0;
  std::uint16_t verifies_ = 0;
  std::uint16_t activations_ = 0;
  std::uint16_t restores_ = 0;
};

bool installation_profile_binding_equal(
    const InstallationProfileBinding& left,
    const InstallationProfileBinding& right);

}  // namespace ledgrid
