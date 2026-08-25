#pragma once

#include <cstddef>
#include <cstdint>

namespace ledgrid {

class Sha256 {
 public:
  Sha256();
  void update(const std::uint8_t* data, std::size_t size);
  void finish(std::uint8_t output[32]);

 private:
  std::uint32_t state_[8] = {};
  std::uint8_t buffer_[64] = {};
  std::size_t buffered_ = 0;
  std::uint64_t total_bytes_ = 0;
  bool finished_ = false;
};

void sha256(const std::uint8_t* data, std::size_t size, std::uint8_t output[32]);

}  // namespace ledgrid
