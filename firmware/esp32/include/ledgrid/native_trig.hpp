#pragma once

#include <cstdint>

namespace ledgrid {
namespace native_trig_detail {

// One closed quarter turn, rounded to the ABI's nearest-even Q15 values.
// Read-only flash data: no startup computation, allocation, or double-precision
// libm calls in the receiver render loop.
inline constexpr std::int16_t kSinQuarter[16385] = {
#include "ledgrid/native_sin_quarter.inc"
};

}  // namespace native_trig_detail

inline std::int16_t native_sin_q15(std::uint16_t phase) {
  const std::uint16_t offset = phase & 0x3fffU;
  const std::uint16_t index = (phase & 0x4000U) != 0U
                                  ? 16384U - offset
                                  : offset;
  const std::int16_t magnitude = native_trig_detail::kSinQuarter[index];
  return (phase & 0x8000U) != 0U ? -magnitude : magnitude;
}

inline std::int16_t native_cos_q15(std::uint16_t phase) {
  return native_sin_q15(static_cast<std::uint16_t>(phase + 16384U));
}

}  // namespace ledgrid
