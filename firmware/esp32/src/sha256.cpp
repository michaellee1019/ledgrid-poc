#include "ledgrid/sha256.hpp"

#include <cstring>

namespace ledgrid {
namespace {

constexpr std::uint32_t kRound[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b,
    0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01,
    0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7,
    0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
    0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152,
    0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
    0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
    0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819,
    0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08,
    0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f,
    0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};

std::uint32_t rotate(std::uint32_t value, std::uint8_t bits) {
  return (value >> bits) | (value << (32U - bits));
}

std::uint32_t read_u32(const std::uint8_t* value) {
  return (static_cast<std::uint32_t>(value[0]) << 24U) |
         (static_cast<std::uint32_t>(value[1]) << 16U) |
         (static_cast<std::uint32_t>(value[2]) << 8U) | value[3];
}

void write_u32(std::uint8_t* output, std::uint32_t value) {
  output[0] = static_cast<std::uint8_t>(value >> 24U);
  output[1] = static_cast<std::uint8_t>(value >> 16U);
  output[2] = static_cast<std::uint8_t>(value >> 8U);
  output[3] = static_cast<std::uint8_t>(value);
}

}  // namespace

Sha256::Sha256()
    : state_{0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
             0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19} {}

void Sha256::transform(const std::uint8_t block[64]) {
  std::uint32_t words[64] = {};
  for (std::size_t i = 0; i < 16; ++i) words[i] = read_u32(block + i * 4U);
  for (std::size_t i = 16; i < 64; ++i) {
    const std::uint32_t s0 =
        rotate(words[i - 15], 7) ^ rotate(words[i - 15], 18) ^
        (words[i - 15] >> 3U);
    const std::uint32_t s1 =
        rotate(words[i - 2], 17) ^ rotate(words[i - 2], 19) ^
        (words[i - 2] >> 10U);
    words[i] = words[i - 16] + s0 + words[i - 7] + s1;
  }
  std::uint32_t a = state_[0], b = state_[1], c = state_[2], d = state_[3];
  std::uint32_t e = state_[4], f = state_[5], g = state_[6], h = state_[7];
  for (std::size_t i = 0; i < 64; ++i) {
    const std::uint32_t sum1 =
        rotate(e, 6) ^ rotate(e, 11) ^ rotate(e, 25);
    const std::uint32_t choose = (e & f) ^ (~e & g);
    const std::uint32_t temp1 = h + sum1 + choose + kRound[i] + words[i];
    const std::uint32_t sum0 =
        rotate(a, 2) ^ rotate(a, 13) ^ rotate(a, 22);
    const std::uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
    const std::uint32_t temp2 = sum0 + majority;
    h = g;
    g = f;
    f = e;
    e = d + temp1;
    d = c;
    c = b;
    b = a;
    a = temp1 + temp2;
  }
  state_[0] += a;
  state_[1] += b;
  state_[2] += c;
  state_[3] += d;
  state_[4] += e;
  state_[5] += f;
  state_[6] += g;
  state_[7] += h;
}

void Sha256::update(const std::uint8_t* data, std::size_t size) {
  if (finished_ || data == nullptr || size == 0) return;
  total_bytes_ += size;
  while (size > 0) {
    const std::size_t space = sizeof(buffer_) - buffered_;
    const std::size_t copy = size < space ? size : space;
    std::memcpy(buffer_ + buffered_, data, copy);
    buffered_ += copy;
    data += copy;
    size -= copy;
    if (buffered_ == sizeof(buffer_)) {
      transform(buffer_);
      buffered_ = 0;
    }
  }
}

void Sha256::finish(std::uint8_t digest[32]) {
  if (digest == nullptr) return;
  if (!finished_) {
    const std::uint64_t bits = total_bytes_ * 8U;
    buffer_[buffered_++] = 0x80U;
    if (buffered_ > 56) {
      std::memset(buffer_ + buffered_, 0, sizeof(buffer_) - buffered_);
      transform(buffer_);
      buffered_ = 0;
    }
    std::memset(buffer_ + buffered_, 0, 56U - buffered_);
    for (std::size_t i = 0; i < 8; ++i) {
      buffer_[63U - i] = static_cast<std::uint8_t>(bits >> (i * 8U));
    }
    transform(buffer_);
    finished_ = true;
  }
  for (std::size_t i = 0; i < 8; ++i) write_u32(digest + i * 4U, state_[i]);
}

}  // namespace ledgrid
