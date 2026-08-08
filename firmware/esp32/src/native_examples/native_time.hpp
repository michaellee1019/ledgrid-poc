#pragma once

#include <stdint.h>

namespace ledgrid_native_example {

// ESP32-S3 modules are linked without a target runtime. These bounded helpers
// perform the one 64-bit modulo and multiply needed for analytic animation time
// using shifts/adds only, so the shared object does not import compiler division
// routines. Work is fixed at 64 bits plus at most 23 multiplier bits.
inline uint64_t modulo_u64(uint64_t value, uint64_t modulus) {
  const uint32_t words[2] = {
      static_cast<uint32_t>(value >> 32U), static_cast<uint32_t>(value)};
  uint64_t remainder = 0U;
  for (uint8_t word = 0; word < 2U; ++word) {
    for (int8_t bit = 31; bit >= 0; --bit) {
      remainder = (remainder << 1U) |
                  ((words[word] >> static_cast<uint8_t>(bit)) & 1U);
      if (remainder >= modulus) remainder -= modulus;
    }
  }
  return remainder;
}

inline uint64_t add_modulo(uint64_t left, uint64_t right,
                           uint64_t modulus) {
  const uint64_t sum = left + right;
  return sum >= modulus ? sum - modulus : sum;
}

inline uint64_t multiply_modulo_u32(uint64_t value, uint32_t multiplier,
                                    uint64_t modulus) {
  uint64_t result = 0U;
  uint64_t addend = modulo_u64(value, modulus);
  while (multiplier != 0U) {
    if ((multiplier & 1U) != 0U)
      result = add_modulo(result, addend, modulus);
    multiplier >>= 1U;
    if (multiplier != 0U)
      addend = add_modulo(addend, addend, modulus);
  }
  return result;
}

inline uint32_t divide_bounded_by_billion(uint64_t value,
                                          uint32_t maximum_quotient) {
  uint32_t quotient = 0U;
  for (int8_t bit = 30; bit >= 0; --bit) {
    const uint32_t part = 1U << static_cast<uint8_t>(bit);
    if (part > maximum_quotient) continue;
    const uint64_t candidate = static_cast<uint64_t>(1000000000U) * part;
    if (value >= candidate) {
      value -= candidate;
      quotient |= part;
    }
  }
  return quotient;
}

inline uint32_t phase_from_elapsed(uint64_t elapsed_us,
                                   uint32_t speed_permille,
                                   uint32_t units_per_second,
                                   uint32_t phase_steps) {
  const uint32_t rate = speed_permille * units_per_second;
  const uint64_t modulus =
      static_cast<uint64_t>(phase_steps) * 1000000000ULL;
  const uint64_t numerator = multiply_modulo_u32(elapsed_us, rate, modulus);
  return divide_bounded_by_billion(numerator, phase_steps - 1U);
}

}  // namespace ledgrid_native_example
