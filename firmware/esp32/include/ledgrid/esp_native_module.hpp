#pragma once

#include <cstddef>
#include <cstdint>
#include <cstdio>

#include "ledgrid/native_module.hpp"

namespace ledgrid {

class EspNativeModuleStore final : public NativeModuleStore {
 public:
  ~EspNativeModuleStore() override;
  bool begin();
  bool ready() const override { return ready_; }
  std::uint32_t capacity_bytes() const override;
  std::uint32_t used_bytes() const override;
  std::uint32_t reserve_bytes() const override {
    return kNativeModuleCacheReserveBytes;
  }
  std::uint64_t mutation_generation() const override {
    return mutation_generation_;
  }
  bool probe(const std::uint8_t digest[32], std::uint32_t* size) const override;
  bool touch(const std::uint8_t digest[32]) override;
  bool can_stage(std::uint32_t size, const NativeModuleLedger& pins,
                 std::uint32_t* reclaimable) const override;
  bool begin_part(const std::uint8_t digest[32], std::uint32_t size,
                  const NativeModuleLedger& pins,
                  std::uint32_t* evicted) override;
  bool write_part(std::uint32_t offset, const std::uint8_t* data,
                  std::size_t size) override;
  bool read_part(std::uint32_t offset, std::uint8_t* data,
                 std::size_t size) const override;
  bool commit_part(const std::uint8_t digest[32]) override;
  void abort_part() override;
  bool read_committed(const std::uint8_t digest[32], std::uint32_t offset,
                      std::uint8_t* data, std::size_t size) const override;
  bool remove(const std::uint8_t digest[32]) override;
  bool committed_path(const std::uint8_t digest[32], char* output,
                      std::size_t output_size) const override;

 private:
  bool path_for(const std::uint8_t digest[32], const char* suffix,
                char* output, std::size_t output_size) const;
  bool write_metadata(const std::uint8_t digest[32], std::uint32_t size,
                      std::uint32_t access, const char* path) const;
  bool read_metadata(const std::uint8_t digest[32], std::uint32_t* size,
                     std::uint32_t* access) const;

  mutable std::uint32_t access_counter_ = 1;
  std::uint64_t mutation_generation_ = 1;
  std::FILE* part_file_ = nullptr;
  std::uint8_t part_digest_[32] = {};
  std::uint32_t part_size_ = 0;
  std::uint32_t part_received_ = 0;
  bool ready_ = false;
};

class NvsNativeModulePersistence final : public NativeModulePersistence {
 public:
  bool begin();
  bool load(NativeModuleLedger* ledger, std::uint8_t quarantined_payload[32],
            std::uint8_t attributed_payload[32],
            NativeModulePhase* attributed_phase) override;
  bool save(const NativeModuleLedger& ledger,
            const std::uint8_t quarantined_payload[32]) override;
  bool mark_phase(const std::uint8_t payload[32],
                  NativeModulePhase phase) override;
  bool clear_phase() override;

 private:
  bool ready_ = false;
};

class EspNativeModuleClock final : public NativeModuleClock {
 public:
  std::uint64_t now_us() const override;
};

class EspNativeModuleWatchdog final : public NativeModuleWatchdog {
 public:
  ~EspNativeModuleWatchdog() override;
  bool arm(NativeModulePhase phase) override;
  void disarm() override;
  void expire_from_timer();

 private:
  void* timer_ = nullptr;
  NativeModuleWatchdogGate gate_{};
};

class EspNativeModuleBackend final : public NativeModuleBackend {
 public:
  ~EspNativeModuleBackend() override;
  bool load(const char* path) override;
  bool resolve_entrypoint() override;
  bool initialize(const NativeModuleDescriptor& descriptor,
                  const NativeModuleTopology& topology,
                  const NativeModuleActivation& activation) override;
  bool update_context(const NativeModuleParameters& parameters,
                      const NativeModulePresentation& presentation) override;
  bool render(std::uint64_t unscaled_scene_time_us,
              std::uint64_t scaled_scene_time_us, std::uint64_t frame_index,
              std::uint8_t* rgb_output, std::size_t rgb_output_size,
              NativeModuleRenderResult* result) override;
  bool cleanup() override;
  bool unload() override;

 private:
  void* module_handle_ = nullptr;
  const ledgrid_native_background_api_v2* api_ = nullptr;
  void* state_ = nullptr;
};

}  // namespace ledgrid
