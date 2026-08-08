#pragma once

#include <cstddef>
#include <cstdint>

#include "ledgrid/protocol.hpp"

namespace ledgrid {

constexpr std::uint32_t kMaxNativeAssetBytes = 512U * 1024U;
constexpr std::uint32_t kMaxFrameTrackAssetBytes = 2560U * 1024U;
constexpr std::uint32_t kMinimumFilesystemReserveBytes = 512U * 1024U;

struct CacheEntryView {
  const std::uint8_t* digest = nullptr;
  std::uint32_t access = 0;
};

int select_inactive_lru(
    const CacheEntryView* entries, std::size_t count,
    const std::uint8_t active_digest[32]);

struct AssetDescriptor {
  std::uint8_t digest[32] = {};
  std::uint32_t total_size = 0;
  AssetKind kind = AssetKind::None;
  std::uint16_t abi = 0;
  std::uint16_t target = 0;
  std::uint8_t strip_count = 0;
  std::uint16_t leds_per_strip = 0;
  std::uint8_t logical_device = 0;
};

// Implementations must keep staging data under a .part name, expose only
// committed assets from probe(), and make commit_part() an atomic rename.
class AssetStore {
 public:
  virtual ~AssetStore() = default;
  virtual bool ready() const { return true; }
  virtual bool probe(const std::uint8_t digest[32]) const = 0;
  virtual bool describe(
      const std::uint8_t digest[32], AssetDescriptor* descriptor) const = 0;
  virtual bool begin_part(const AssetDescriptor& descriptor) = 0;
  virtual bool write_part(
      std::uint32_t offset, const std::uint8_t* data, std::size_t size) = 0;
  virtual bool read_part(
      std::uint32_t offset, std::uint8_t* data, std::size_t size) const = 0;
  // Hardware implementation verifies payload-local properties (ELF imports or
  // complete frame-track structure) and filesystem reserve. The signed index
  // and trust anchor are checked before begin_part is called.
  virtual OperationResult validate_part(const AssetDescriptor& descriptor) = 0;
  virtual bool commit_part(const std::uint8_t digest[32]) = 0;
  virtual void discard_part() = 0;
  virtual bool remove(const std::uint8_t digest[32]) = 0;
  virtual std::uint32_t free_bytes() const = 0;
  virtual std::uint32_t used_bytes() const = 0;
  virtual bool committed_path(
      const std::uint8_t digest[32], char* output,
      std::size_t output_size) const {
    (void)digest; (void)output; (void)output_size;
    return false;
  }
  virtual bool read_committed(
      const std::uint8_t digest[32], std::uint32_t offset,
      std::uint8_t* data, std::size_t size) const {
    (void)digest; (void)offset; (void)data; (void)size;
    return false;
  }
  virtual void set_active_digest(const std::uint8_t digest[32]) {
    (void)digest;
  }
};

class UploadManager {
 public:
  explicit UploadManager(AssetStore* store) : store_(store) {}

  bool probe(const std::uint8_t digest[32]) const;
  OperationResult begin(const AssetDescriptor& descriptor);
  OperationResult chunk(
      std::uint32_t offset, const std::uint8_t* data, std::size_t size);
  OperationResult commit(const std::uint8_t digest[32]);
  OperationResult remove(
      const std::uint8_t digest[32], const std::uint8_t active_digest[32],
      bool asset_active);
  void abort();

  UploadState state() const { return state_; }
  std::uint32_t received_bytes() const { return received_bytes_; }
  std::uint32_t total_bytes() const { return descriptor_.total_size; }
  const AssetDescriptor& descriptor() const { return descriptor_; }

 private:
  bool descriptor_valid(const AssetDescriptor& descriptor) const;
  bool same_descriptor(const AssetDescriptor& descriptor) const;
  bool part_digest_matches() const;

  AssetStore* store_ = nullptr;
  AssetDescriptor descriptor_{};
  UploadState state_ = UploadState::Idle;
  std::uint32_t received_bytes_ = 0;
};

}  // namespace ledgrid
