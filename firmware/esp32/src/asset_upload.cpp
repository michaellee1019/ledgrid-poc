#include "ledgrid/asset_upload.hpp"

#include <algorithm>
#include <cstring>

#include "ledgrid/sha256.hpp"

namespace ledgrid {

int select_inactive_lru(
    const CacheEntryView* entries, std::size_t count,
    const std::uint8_t active_digest[32]) {
  if (entries == nullptr) return -1;
  int selected = -1;
  std::uint32_t oldest = UINT32_MAX;
  for (std::size_t i = 0; i < count; ++i) {
    if (entries[i].digest == nullptr ||
        (active_digest != nullptr &&
         std::memcmp(entries[i].digest, active_digest, 32) == 0)) continue;
    if (selected < 0 || entries[i].access < oldest) {
      selected = static_cast<int>(i);
      oldest = entries[i].access;
    }
  }
  return selected;
}

bool UploadManager::probe(const std::uint8_t digest[32]) const {
  return store_ != nullptr && digest != nullptr && store_->probe(digest);
}

bool UploadManager::descriptor_valid(const AssetDescriptor& descriptor) const {
  if (descriptor.kind != AssetKind::Native &&
      descriptor.kind != AssetKind::FrameTrack) return false;
  const std::uint32_t limit = descriptor.kind == AssetKind::Native
                                  ? kMaxNativeAssetBytes
                                  : kMaxFrameTrackAssetBytes;
  return store_ != nullptr && descriptor.kind != AssetKind::None &&
         descriptor.total_size > 0 && descriptor.total_size <= limit &&
         descriptor.strip_count > 0 && descriptor.leds_per_strip > 0 &&
         (descriptor.kind != AssetKind::Native || descriptor.abi == 1);
}

bool UploadManager::same_descriptor(const AssetDescriptor& descriptor) const {
  return std::memcmp(descriptor_.digest, descriptor.digest, 32) == 0 &&
         descriptor_.total_size == descriptor.total_size &&
         descriptor_.kind == descriptor.kind && descriptor_.abi == descriptor.abi &&
         descriptor_.target == descriptor.target &&
         descriptor_.strip_count == descriptor.strip_count &&
         descriptor_.leds_per_strip == descriptor.leds_per_strip &&
         descriptor_.logical_device == descriptor.logical_device;
}

OperationResult UploadManager::begin(const AssetDescriptor& descriptor) {
  if (!descriptor_valid(descriptor)) return OperationResult::BadSize;
  if (state_ == UploadState::Receiving) {
    return same_descriptor(descriptor) ? OperationResult::Ok
                                       : OperationResult::InvalidState;
  }
  if (store_->probe(descriptor.digest)) {
    descriptor_ = descriptor;
    received_bytes_ = descriptor.total_size;
    state_ = UploadState::Committed;
    return OperationResult::Ok;
  }
  if (!store_->begin_part(descriptor)) {
    state_ = UploadState::Failed;
    return OperationResult::StorageError;
  }
  descriptor_ = descriptor;
  received_bytes_ = 0;
  state_ = UploadState::Receiving;
  return OperationResult::Ok;
}

OperationResult UploadManager::chunk(
    std::uint32_t offset, const std::uint8_t* data, std::size_t size) {
  if (state_ != UploadState::Receiving) return OperationResult::InvalidState;
  if (data == nullptr || size == 0 || size > kMaxAssetChunkBytes ||
      offset > descriptor_.total_size ||
      size > descriptor_.total_size - offset) {
    return OperationResult::BadSize;
  }
  if (offset < received_bytes_) {
    // A fully received retry is idempotent only when its bytes are identical.
    if (size > received_bytes_ - offset) return OperationResult::InvalidState;
    std::uint8_t compare[128];
    std::size_t checked = 0;
    while (checked < size) {
      const std::size_t amount = std::min(sizeof(compare), size - checked);
      if (!store_->read_part(offset + checked, compare, amount) ||
          std::memcmp(compare, data + checked, amount) != 0) {
        return OperationResult::InvalidState;
      }
      checked += amount;
    }
    return OperationResult::Ok;
  }
  if (offset != received_bytes_) return OperationResult::InvalidState;
  if (!store_->write_part(offset, data, size)) {
    state_ = UploadState::Failed;
    store_->discard_part();
    return OperationResult::StorageError;
  }
  received_bytes_ += static_cast<std::uint32_t>(size);
  return OperationResult::Ok;
}

bool UploadManager::part_digest_matches() const {
  Sha256 sha;
  std::uint8_t buffer[256];
  std::uint32_t offset = 0;
  while (offset < descriptor_.total_size) {
    const std::size_t amount = std::min<std::size_t>(
        sizeof(buffer), descriptor_.total_size - offset);
    if (!store_->read_part(offset, buffer, amount)) return false;
    sha.update(buffer, amount);
    offset += static_cast<std::uint32_t>(amount);
  }
  std::uint8_t digest[32] = {};
  sha.finish(digest);
  return std::memcmp(digest, descriptor_.digest, sizeof(digest)) == 0;
}

OperationResult UploadManager::commit(const std::uint8_t digest[32]) {
  if (digest == nullptr ||
      std::memcmp(digest, descriptor_.digest, sizeof(descriptor_.digest)) != 0) {
    return OperationResult::BadDigest;
  }
  if (state_ == UploadState::Committed && store_->probe(digest)) {
    return OperationResult::Ok;
  }
  if (state_ != UploadState::Receiving ||
      received_bytes_ != descriptor_.total_size) {
    return OperationResult::InvalidState;
  }
  state_ = UploadState::Verifying;
  if (!part_digest_matches()) {
    store_->discard_part();
    state_ = UploadState::Failed;
    return OperationResult::BadDigest;
  }
  const OperationResult validated = store_->validate_part(descriptor_);
  if (validated != OperationResult::Ok) {
    store_->discard_part();
    state_ = UploadState::Failed;
    return validated;
  }
  if (!store_->commit_part(descriptor_.digest)) {
    store_->discard_part();
    state_ = UploadState::Failed;
    return OperationResult::StorageError;
  }
  state_ = UploadState::Committed;
  return OperationResult::Ok;
}

OperationResult UploadManager::remove(
    const std::uint8_t digest[32], const std::uint8_t active_digest[32],
    bool asset_active) {
  if (digest == nullptr) return OperationResult::InvalidCommand;
  if (store_ == nullptr) return OperationResult::Unsupported;
  if (asset_active && active_digest != nullptr &&
      std::memcmp(digest, active_digest, 32) == 0) {
    return OperationResult::InvalidState;
  }
  // Removing a missing committed entry is deliberately idempotent.
  return (!store_->probe(digest) || store_->remove(digest))
             ? OperationResult::Ok
             : OperationResult::StorageError;
}

void UploadManager::abort() {
  if (store_ != nullptr && state_ != UploadState::Committed) store_->discard_part();
  descriptor_ = {};
  state_ = UploadState::Idle;
  received_bytes_ = 0;
}

}  // namespace ledgrid
