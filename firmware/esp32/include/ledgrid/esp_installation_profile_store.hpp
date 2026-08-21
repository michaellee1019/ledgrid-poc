#pragma once

#include <cstddef>
#include <cstdint>
#include <cstdio>

#include "ledgrid/installation_profile_store.hpp"

namespace ledgrid {

class EspInstallationProfileStore final : public InstallationProfileStore {
 public:
  ~EspInstallationProfileStore() override;
  bool begin();
  bool ready() const override { return ready_; }
  std::uint32_t capacity_bytes() const override;
  std::uint32_t used_bytes() const override;
  std::uint32_t reserve_bytes() const override {
    return kInstallationProfileCacheReserveBytes;
  }
  std::uint64_t mutation_generation() const override {
    return mutation_generation_;
  }
  bool probe(const std::uint8_t digest[32], std::uint32_t* size) const override;
  bool touch(const std::uint8_t digest[32]) override;
  bool can_stage(
      std::uint32_t size, const InstallationProfileLedger& pins,
      std::uint32_t* reclaimable) const override;
  bool begin_part(
      const std::uint8_t digest[32], std::uint32_t size,
      const InstallationProfileLedger& pins, std::uint32_t* evicted) override;
  bool write_part(
      std::uint32_t offset, const std::uint8_t* data, std::size_t size) override;
  bool read_part(
      std::uint32_t offset, std::uint8_t* data, std::size_t size) const override;
  bool commit_part(const std::uint8_t digest[32]) override;
  void abort_part() override;
  bool read_committed(
      const std::uint8_t digest[32], std::uint32_t offset,
      std::uint8_t* data, std::size_t size) const override;
  bool remove(const std::uint8_t digest[32]) override;

 private:
  bool path_for(
      const std::uint8_t digest[32], const char* suffix,
      char* output, std::size_t output_size) const;
  bool write_metadata(
      const std::uint8_t digest[32], std::uint32_t size,
      std::uint32_t access, const char* path) const;
  bool read_metadata(
      const std::uint8_t digest[32], std::uint32_t* size,
      std::uint32_t* access) const;

  mutable std::uint32_t access_counter_ = 1;
  std::uint64_t mutation_generation_ = 1;
  std::FILE* part_file_ = nullptr;
  std::uint8_t part_digest_[32] = {};
  std::uint32_t part_size_ = 0;
  std::uint32_t part_received_ = 0;
  bool ready_ = false;
};

class NvsInstallationProfilePersistence final
    : public InstallationProfilePersistence {
 public:
  bool begin();
  bool load(InstallationProfileLedger* ledger) override;
  bool save(const InstallationProfileLedger& ledger) override;

 private:
  bool ready_ = false;
};

}  // namespace ledgrid
