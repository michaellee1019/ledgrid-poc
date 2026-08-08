#pragma once

#include <cstddef>
#include <cstdint>

namespace ledgrid {

class Sha256 {
 public:
  Sha256();
  void update(const std::uint8_t* data, std::size_t size);
  void finish(std::uint8_t digest[32]);

 private:
  void transform(const std::uint8_t block[64]);
  std::uint32_t state_[8] = {};
  std::uint64_t total_bytes_ = 0;
  std::uint8_t buffer_[64] = {};
  std::size_t buffered_ = 0;
  bool finished_ = false;
};

}  // namespace ledgrid
