#pragma once

#include <cstddef>
#include <cstdint>

#include "ledgrid/asset_upload.hpp"

namespace ledgrid {

class AnimationBackend {
 public:
  virtual ~AnimationBackend() = default;
  virtual std::uint32_t capabilities() const = 0;
  virtual bool available(AssetKind kind) const = 0;
  virtual OperationResult start(
      const AssetDescriptor& descriptor, std::uint16_t global_strip_offset,
      const std::uint8_t* parameters, std::size_t parameter_size) = 0;
  virtual void stop() = 0;
  virtual OperationResult restart() = 0;
  virtual OperationResult update_parameters(
      const std::uint8_t* parameters, std::size_t parameter_size) = 0;
  virtual bool render(
      std::uint64_t now_us, std::uint8_t* rgb_output,
      std::size_t rgb_output_size, bool* changed) = 0;
};

class ReceiverPersistence {
 public:
  virtual ~ReceiverPersistence() = default;
  virtual bool mark_active(const std::uint8_t digest[32]) = 0;
  virtual void clear_active() = 0;
  virtual void mark_quarantined(const std::uint8_t digest[32]) = 0;
  virtual void clear_quarantined(const std::uint8_t digest[32]) = 0;
};

}  // namespace ledgrid
