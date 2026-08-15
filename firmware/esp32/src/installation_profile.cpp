#include "ledgrid/installation_profile.hpp"

#include <cstring>

namespace ledgrid {
namespace {

constexpr std::size_t kFixedHeaderBytes = 112;
constexpr std::size_t kSectionEntryBytes = 24;
constexpr std::size_t kProfileHeaderBytes =
    kFixedHeaderBytes + kSectionEntryBytes * kInstallationProfileSectionCountV1;
constexpr std::uint8_t kExpectedEncodings[kInstallationProfileSectionCountV1] = {
    1, 2, 2, 2, 2, 1, 3, 4, 4};

std::uint16_t read_u16(const std::uint8_t* input) {
  return static_cast<std::uint16_t>(
      (static_cast<std::uint16_t>(input[0]) << 8U) | input[1]);
}

std::uint32_t read_u32(const std::uint8_t* input) {
  return (static_cast<std::uint32_t>(input[0]) << 24U) |
         (static_cast<std::uint32_t>(input[1]) << 16U) |
         (static_cast<std::uint32_t>(input[2]) << 8U) | input[3];
}

bool all_zero(const std::uint8_t* input, std::size_t size) {
  std::uint8_t combined = 0;
  for (std::size_t index = 0; index < size; ++index) combined |= input[index];
  return combined == 0;
}

std::uint32_t rotate_right(std::uint32_t value, unsigned count) {
  return (value >> count) | (value << (32U - count));
}

class StreamingSha256 {
 public:
  void update(const std::uint8_t* data, std::size_t size) {
    if (data == nullptr || size == 0) return;
    total_bytes_ += size;
    while (size != 0) {
      const std::size_t room = sizeof(buffer_) - buffered_;
      const std::size_t amount = size < room ? size : room;
      std::memcpy(buffer_ + buffered_, data, amount);
      buffered_ += amount;
      data += amount;
      size -= amount;
      if (buffered_ == sizeof(buffer_)) {
        transform(buffer_);
        buffered_ = 0;
      }
    }
  }

  void finish(std::uint8_t output[32]) {
    const std::uint64_t bit_count = total_bytes_ * 8U;
    buffer_[buffered_++] = 0x80;
    if (buffered_ > 56) {
      std::memset(buffer_ + buffered_, 0, sizeof(buffer_) - buffered_);
      transform(buffer_);
      buffered_ = 0;
    }
    std::memset(buffer_ + buffered_, 0, 56 - buffered_);
    for (std::size_t index = 0; index < 8; ++index) {
      buffer_[63 - index] =
          static_cast<std::uint8_t>(bit_count >> (index * 8U));
    }
    transform(buffer_);
    for (std::size_t index = 0; index < 8; ++index) {
      output[index * 4] = static_cast<std::uint8_t>(state_[index] >> 24U);
      output[index * 4 + 1] = static_cast<std::uint8_t>(state_[index] >> 16U);
      output[index * 4 + 2] = static_cast<std::uint8_t>(state_[index] >> 8U);
      output[index * 4 + 3] = static_cast<std::uint8_t>(state_[index]);
    }
  }

 private:
  void transform(const std::uint8_t block[64]) {
    static constexpr std::uint32_t rounds[64] = {
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
    std::uint32_t words[64] = {};
    for (std::size_t index = 0; index < 16; ++index) {
      words[index] = read_u32(block + index * 4U);
    }
    for (std::size_t index = 16; index < 64; ++index) {
      const std::uint32_t s0 = rotate_right(words[index - 15], 7) ^
                               rotate_right(words[index - 15], 18) ^
                               (words[index - 15] >> 3U);
      const std::uint32_t s1 = rotate_right(words[index - 2], 17) ^
                               rotate_right(words[index - 2], 19) ^
                               (words[index - 2] >> 10U);
      words[index] = words[index - 16] + s0 + words[index - 7] + s1;
    }
    std::uint32_t a = state_[0], b = state_[1], c = state_[2], d = state_[3];
    std::uint32_t e = state_[4], f = state_[5], g = state_[6], h = state_[7];
    for (std::size_t index = 0; index < 64; ++index) {
      const std::uint32_t sum1 =
          rotate_right(e, 6) ^ rotate_right(e, 11) ^ rotate_right(e, 25);
      const std::uint32_t choice = (e & f) ^ (~e & g);
      const std::uint32_t temp1 =
          h + sum1 + choice + rounds[index] + words[index];
      const std::uint32_t sum0 =
          rotate_right(a, 2) ^ rotate_right(a, 13) ^ rotate_right(a, 22);
      const std::uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
      const std::uint32_t temp2 = sum0 + majority;
      h = g; g = f; f = e; e = d + temp1;
      d = c; c = b; b = a; a = temp1 + temp2;
    }
    state_[0] += a; state_[1] += b; state_[2] += c; state_[3] += d;
    state_[4] += e; state_[5] += f; state_[6] += g; state_[7] += h;
  }

  std::uint32_t state_[8] = {
      0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
      0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
  std::uint8_t buffer_[64] = {};
  std::size_t buffered_ = 0;
  std::uint64_t total_bytes_ = 0;
};

std::uint32_t crc32(const std::uint8_t* data, std::size_t size) {
  std::uint32_t crc = 0xFFFFFFFFU;
  for (std::size_t index = 0; index < size; ++index) {
    crc ^= data[index];
    for (unsigned bit = 0; bit < 8; ++bit) {
      crc = (crc >> 1U) ^ (0xEDB88320U & (0U - (crc & 1U)));
    }
  }
  return ~crc;
}

bool fail(InstallationProfileError value, InstallationProfileViewV1* view,
          InstallationProfileError* error) {
  if (view != nullptr) *view = {};
  if (error != nullptr) *error = value;
  return false;
}

}  // namespace

bool decode_installation_profile_receiver_v1(
    const std::uint8_t* encoded, std::size_t encoded_size,
    const InstallationProfileReceiverExpectationV1& expectation,
    InstallationProfileViewV1* view, InstallationProfileError* error) {
  if (view == nullptr) {
    return fail(InstallationProfileError::NullArgument, view, error);
  }
  *view = {};
  if (error != nullptr) *error = InstallationProfileError::None;
  if (encoded_size != kInstallationProfileReceiverBytesV1 ||
      encoded_size > kInstallationProfileMaximumBytesV1) {
    return fail(InstallationProfileError::InvalidSize, view, error);
  }
  if (encoded == nullptr) {
    return fail(InstallationProfileError::NullArgument, view, error);
  }
  if (std::memcmp(encoded, "LGIP", 4) != 0) {
    return fail(InstallationProfileError::InvalidMagic, view, error);
  }
  if (read_u16(encoded + 4) != kInstallationProfileFormatV1) {
    return fail(InstallationProfileError::UnsupportedVersion, view, error);
  }
  if (read_u16(encoded + 6) != kFixedHeaderBytes ||
      read_u32(encoded + 32) != encoded_size || encoded[24] > 4 ||
      encoded[25] != 7) {
    return fail(InstallationProfileError::InvalidHeader, view, error);
  }
  const std::uint32_t flags = read_u32(encoded + 8);
  if ((flags & ~1U) != 0) {
    return fail(InstallationProfileError::InvalidFlags, view, error);
  }
  const bool reversed = (flags & 1U) != 0;
  const std::uint16_t global_strips = read_u16(encoded + 12);
  const std::uint16_t leds_per_strip = read_u16(encoded + 14);
  const std::uint16_t origin = read_u16(encoded + 16);
  const std::uint16_t strip_count = read_u16(encoded + 18);
  const std::uint32_t pixel_count = read_u32(encoded + 20);
  if (global_strips != kInstallationProfileGlobalStripsV1 ||
      leds_per_strip != kInstallationProfileLedsPerStripV1 ||
      strip_count != kInstallationProfileReceiverStripsV1 ||
      pixel_count != kInstallationProfileReceiverPixelsV1) {
    return fail(InstallationProfileError::WrongGeometry, view, error);
  }
  if (origin != expectation.strip_origin || origin % strip_count != 0 ||
      origin + strip_count > global_strips) {
    return fail(InstallationProfileError::WrongOrigin, view, error);
  }
  if (reversed != expectation.reversed_strip_order) {
    return fail(InstallationProfileError::WrongDirection, view, error);
  }
  if (read_u16(encoded + 26) != kInstallationProfileSectionCountV1 ||
      read_u16(encoded + 28) != kSectionEntryBytes) {
    return fail(InstallationProfileError::InvalidSectionTable, view, error);
  }
  if (read_u16(encoded + 30) != 0 || !all_zero(encoded + 100, 12)) {
    return fail(InstallationProfileError::InvalidReservedBytes, view, error);
  }

  StreamingSha256 digest;
  static constexpr std::uint8_t zeros[32] = {};
  digest.update(encoded, 68);
  digest.update(zeros, sizeof(zeros));
  digest.update(encoded + 100, encoded_size - 100);
  std::uint8_t calculated_digest[32] = {};
  digest.finish(calculated_digest);
  if (std::memcmp(calculated_digest, encoded + 68, 32) != 0) {
    return fail(InstallationProfileError::ContentDigestMismatch, view, error);
  }

  const std::uint8_t* sections[kInstallationProfileSectionCountV1] = {};
  std::size_t expected_offset = kProfileHeaderBytes;
  for (std::size_t index = 0; index < kInstallationProfileSectionCountV1;
       ++index) {
    const std::uint8_t* entry = encoded + kFixedHeaderBytes + index * 24U;
    const std::uint32_t offset = read_u32(entry + 8);
    const std::uint32_t length = read_u32(entry + 12);
    if (read_u16(entry) != index + 1U ||
        entry[2] != kExpectedEncodings[index] || entry[3] != 1 ||
        read_u32(entry + 4) != pixel_count || length != pixel_count ||
        read_u32(entry + 20) != 0 || offset != expected_offset) {
      return fail(InstallationProfileError::InvalidSectionMetadata, view, error);
    }
    if (offset > encoded_size || length > encoded_size - offset) {
      return fail(InstallationProfileError::SectionOutOfBounds, view, error);
    }
    sections[index] = encoded + offset;
    if (crc32(sections[index], length) != read_u32(entry + 16)) {
      return fail(InstallationProfileError::SectionCrcMismatch, view, error);
    }
    expected_offset = offset + length;
  }
  if (expected_offset != encoded_size) {
    return fail(InstallationProfileError::InvalidSectionMetadata, view, error);
  }

  for (std::size_t index = 0; index < pixel_count; ++index) {
    const std::uint8_t category = sections[0][index];
    const std::uint8_t region = sections[5][index];
    if (category > 2) {
      return fail(InstallationProfileError::InvalidCategory, view, error);
    }
    for (std::size_t boolean_section = 1; boolean_section <= 4;
         ++boolean_section) {
      if (sections[boolean_section][index] > 1) {
        return fail(InstallationProfileError::InvalidBoolean, view, error);
      }
    }
    if (region > 7) {
      return fail(InstallationProfileError::InvalidGlobeRegion, view, error);
    }
    if ((category == 2) != (region != 0)) {
      return fail(InstallationProfileError::CategoryRegionMismatch, view, error);
    }
    const bool foliage = category == 1;
    const bool globe = category == 2;
    const bool obstacle = foliage || globe;
    if (obstacle && sections[1][index] == 0) {
      return fail(InstallationProfileError::ClearanceObstacleMismatch, view,
                  error);
    }
    if (sections[2][index] != 0 && !foliage) {
      return fail(InstallationProfileError::FoliageEdgeMismatch, view, error);
    }
    if (sections[3][index] != 0 && !globe) {
      return fail(InstallationProfileError::GlobeEdgeMismatch, view, error);
    }
    if (sections[4][index] != 0 && !obstacle) {
      return fail(InstallationProfileError::ObstacleEdgeMismatch, view, error);
    }
    if ((sections[6][index] == 0) != obstacle) {
      return fail(InstallationProfileError::DistanceObstacleMismatch, view,
                  error);
    }
    if (static_cast<std::int8_t>(sections[7][index]) == -128 ||
        static_cast<std::int8_t>(sections[8][index]) == -128) {
      return fail(InstallationProfileError::InvalidNormal, view, error);
    }
  }

  view->encoded = encoded;
  view->encoded_size = encoded_size;
  view->global_strip_count = global_strips;
  view->leds_per_strip = leds_per_strip;
  view->strip_origin = origin;
  view->strip_count = strip_count;
  view->pixel_count = pixel_count;
  view->clearance_radius = encoded[24];
  view->reversed_strip_order = reversed;
  std::memcpy(view->calibration_digest, encoded + 36, 32);
  std::memcpy(view->content_digest, encoded + 68, 32);
  view->category = sections[0];
  view->clearance = sections[1];
  view->foliage_edge = sections[2];
  view->globe_edge = sections[3];
  view->obstacle_edge = sections[4];
  view->globe_region = sections[5];
  view->distance = sections[6];
  view->normal_x = reinterpret_cast<const std::int8_t*>(sections[7]);
  view->normal_y = reinterpret_cast<const std::int8_t*>(sections[8]);
  return true;
}

}  // namespace ledgrid
