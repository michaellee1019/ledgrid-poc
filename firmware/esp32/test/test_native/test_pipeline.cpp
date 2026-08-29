#include <unity.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdio>
#include <cstdint>
#include <utility>
#include <vector>

#include "ledgrid/frame_mailbox.hpp"
#include "ledgrid/protocol.hpp"
#include "ledgrid/startup_animation.hpp"
#include "ledgrid/ws2812_encoder.hpp"

namespace {

std::uint16_t read_u16(const std::uint8_t* input) {
  return static_cast<std::uint16_t>(
      (static_cast<std::uint16_t>(input[0]) << 8) | input[1]);
}

std::uint32_t read_u32(const std::uint8_t* input) {
  return (static_cast<std::uint32_t>(input[0]) << 24) |
         (static_cast<std::uint32_t>(input[1]) << 16) |
         (static_cast<std::uint32_t>(input[2]) << 8) |
         input[3];
}

void write_packet_crc(std::vector<std::uint8_t>* packet) {
  const std::size_t offset =
      packet->size() - ledgrid::kAnimationPipelineCrcBytes;
  const std::uint16_t crc = ledgrid::animation_pipeline_crc16_ccitt(
      packet->data(), offset);
  (*packet)[offset] = static_cast<std::uint8_t>(crc >> 8U);
  (*packet)[offset + 1U] = static_cast<std::uint8_t>(crc);
}

std::vector<std::uint8_t> legacy_packet(
    const std::vector<std::uint8_t>& semantic) {
  std::vector<std::uint8_t> packet(
      semantic.size() + ledgrid::kAnimationPipelineCrcBytes, 0);
  std::copy(semantic.begin(), semantic.end(), packet.begin());
  write_packet_crc(&packet);
  return packet;
}

std::vector<std::uint8_t> aligned_packet(
    const std::vector<std::uint8_t>& semantic) {
  const std::size_t unpadded = ledgrid::kAlignedEnvelopeHeaderBytes +
      semantic.size() + ledgrid::kAnimationPipelineCrcBytes;
  const std::size_t wire_size =
      unpadded + (ledgrid::kSpiDmaAlignmentBytes -
                  unpadded % ledgrid::kSpiDmaAlignmentBytes) %
                     ledgrid::kSpiDmaAlignmentBytes;
  std::vector<std::uint8_t> packet(wire_size, 0);
  packet[0] = static_cast<std::uint8_t>(
      ledgrid::ReceiverCommand::AlignedEnvelope);
  packet[1] = ledgrid::kAlignedEnvelopeVersion;
  packet[2] = static_cast<std::uint8_t>(semantic.size() >> 8U);
  packet[3] = static_cast<std::uint8_t>(semantic.size());
  std::copy(semantic.begin(), semantic.end(),
            packet.begin() + ledgrid::kAlignedEnvelopeHeaderBytes);
  write_packet_crc(&packet);
  return packet;
}

bool is_power_of_two(std::uint16_t value) {
  return value != 0U && (value & (value - 1U)) == 0U;
}

std::vector<std::uint8_t> fec_v2_packet(
    const std::vector<std::uint8_t>& semantic) {
  const auto inner = aligned_packet(semantic);
  std::vector<std::uint8_t> protected_bytes(
      ledgrid::kFecEnvelopeHeaderBytes + inner.size(), 0);
  protected_bytes[0] = static_cast<std::uint8_t>(
      ledgrid::ReceiverCommand::AlignedEnvelope);
  protected_bytes[1] = ledgrid::kFecEnvelopeVersionV2;
  protected_bytes[2] = static_cast<std::uint8_t>(inner.size() >> 8U);
  protected_bytes[3] = static_cast<std::uint8_t>(inner.size());
  std::copy(inner.begin(), inner.end(),
            protected_bytes.begin() + ledgrid::kFecEnvelopeHeaderBytes);
  std::size_t codewords =
      (protected_bytes.size() + ledgrid::kFecV2DataBytes - 1U) /
      ledgrid::kFecV2DataBytes;
  if (codewords % 2U != 0U) ++codewords;
  std::vector<std::uint8_t> packet(
      codewords * ledgrid::kFecV2CodewordBytes, 0);
  std::size_t protected_offset = 0;
  for (std::size_t block = 0; block < codewords; ++block) {
    const std::size_t wire_offset = block * ledgrid::kFecV2CodewordBytes;
    const std::size_t count = std::min(
        ledgrid::kFecV2DataBytes, protected_bytes.size() - protected_offset);
    std::copy(protected_bytes.begin() + protected_offset,
              protected_bytes.begin() + protected_offset + count,
              packet.begin() + wire_offset);
    std::uint16_t syndrome = 0;
    std::uint8_t data_parity = 0;
    std::uint16_t position = 1;
    for (std::size_t byte_index = 0;
         byte_index < ledgrid::kFecV2DataBytes; ++byte_index) {
      const std::uint8_t value = packet[wire_offset + byte_index];
      data_parity ^= static_cast<std::uint8_t>(__builtin_parity(value));
      for (std::uint8_t bit = 0; bit < 8U; ++bit) {
        while (is_power_of_two(position)) ++position;
        if ((value & (1U << bit)) != 0U) syndrome ^= position;
        ++position;
      }
    }
    const std::uint8_t overall = data_parity ^
        static_cast<std::uint8_t>(__builtin_parity(syndrome));
    const std::uint16_t stored = syndrome |
        (static_cast<std::uint16_t>(overall) << ledgrid::kFecV2ParityBits);
    packet[wire_offset + ledgrid::kFecV2DataBytes] =
        static_cast<std::uint8_t>(stored >> 8U);
    packet[wire_offset + ledgrid::kFecV2DataBytes + 1U] =
        static_cast<std::uint8_t>(stored);
    protected_offset += count;
  }
  return packet;
}

std::uint8_t fec_gf_multiply(std::uint8_t left, std::uint8_t right) {
  std::uint8_t result = 0;
  while (right != 0U) {
    if ((right & 1U) != 0U) result ^= left;
    right >>= 1U;
    const bool carry = (left & 0x80U) != 0U;
    left = static_cast<std::uint8_t>(left << 1U);
    if (carry) left ^= 0x1DU;
  }
  return result;
}

std::vector<std::uint8_t> fec_v3_packet(
    const std::vector<std::uint8_t>& semantic) {
  const auto inner = aligned_packet(semantic);
  std::vector<std::uint8_t> protected_bytes(
      ledgrid::kFecEnvelopeHeaderBytes + inner.size(), 0);
  protected_bytes[0] = static_cast<std::uint8_t>(
      ledgrid::ReceiverCommand::AlignedEnvelope);
  protected_bytes[1] = ledgrid::kFecEnvelopeVersionV3;
  protected_bytes[2] = static_cast<std::uint8_t>(inner.size() >> 8U);
  protected_bytes[3] = static_cast<std::uint8_t>(inner.size());
  std::copy(inner.begin(), inner.end(),
            protected_bytes.begin() + ledgrid::kFecEnvelopeHeaderBytes);
  std::size_t codewords =
      (protected_bytes.size() + ledgrid::kFecV3DataBytes - 1U) /
      ledgrid::kFecV3DataBytes;
  codewords += (4U - codewords % 4U) % 4U;
  std::vector<std::uint8_t> packet(
      ledgrid::kFecWireHeaderBytes +
          codewords * ledgrid::kFecV3CodewordBytes,
      0);
  std::copy_n(protected_bytes.begin(), ledgrid::kFecEnvelopeHeaderBytes,
              packet.begin());
  std::copy_n(protected_bytes.begin(), ledgrid::kFecEnvelopeHeaderBytes,
              packet.end() - ledgrid::kFecEnvelopeHeaderBytes);
  const std::size_t matrix_offset = ledgrid::kFecEnvelopeHeaderBytes;
  for (std::size_t block = 0; block < codewords; ++block) {
    std::uint8_t parity0 = 0;
    std::uint8_t parity1 = 0;
    std::uint8_t parity2 = 0;
    for (std::size_t symbol = 0; symbol < ledgrid::kFecV3DataBytes; ++symbol) {
      const std::size_t protected_offset =
          block * ledgrid::kFecV3DataBytes + symbol;
      const std::uint8_t value = protected_offset < protected_bytes.size()
          ? protected_bytes[protected_offset] : 0U;
      packet[matrix_offset + symbol * codewords + block] = value;
      const std::uint8_t coefficient =
          static_cast<std::uint8_t>(symbol + 1U);
      parity0 ^= value;
      parity1 ^= fec_gf_multiply(value, coefficient);
      parity2 ^= fec_gf_multiply(
          value, fec_gf_multiply(coefficient, coefficient));
    }
    packet[matrix_offset + ledgrid::kFecV3DataBytes * codewords + block] =
        parity0;
    packet[matrix_offset + (ledgrid::kFecV3DataBytes + 1U) * codewords + block] =
        parity1;
    packet[matrix_offset + (ledgrid::kFecV3DataBytes + 2U) * codewords + block] =
        parity2;
  }
  return packet;
}

std::uint8_t fec_gf_power(std::uint8_t value, std::uint8_t exponent) {
  std::uint8_t result = 1U;
  while (exponent != 0U) {
    if ((exponent & 1U) != 0U) result = fec_gf_multiply(result, value);
    value = fec_gf_multiply(value, value);
    exponent >>= 1U;
  }
  return result;
}

std::uint8_t fec_gf_inverse(std::uint8_t value) {
  return fec_gf_power(value, 254U);
}

std::size_t fec_rs_wire_offset(
    std::size_t matrix_offset,
    std::size_t symbol,
    std::size_t logical_block,
    std::size_t codewords,
    bool diagonal = true) {
  std::size_t wire_block = logical_block;
  if (diagonal) {
    wire_block += symbol;
    while (wire_block >= codewords) wire_block -= codewords;
  }
  return matrix_offset + symbol * codewords + wire_block;
}

std::vector<std::uint8_t> fec_rs_packet(
    const std::vector<std::uint8_t>& semantic,
    std::uint8_t version,
    std::size_t data_bytes,
    std::size_t parity_bytes,
    std::size_t codeword_bytes,
    bool diagonal = false,
    bool outer_parity = false) {
  const auto inner = aligned_packet(semantic);
  std::vector<std::uint8_t> protected_bytes(
      ledgrid::kFecEnvelopeHeaderBytes + inner.size(), 0);
  protected_bytes[0] = static_cast<std::uint8_t>(
      ledgrid::ReceiverCommand::AlignedEnvelope);
  protected_bytes[1] = version;
  protected_bytes[2] = static_cast<std::uint8_t>(inner.size() >> 8U);
  protected_bytes[3] = static_cast<std::uint8_t>(inner.size());
  std::copy(inner.begin(), inner.end(),
            protected_bytes.begin() + ledgrid::kFecEnvelopeHeaderBytes);
  std::size_t codewords =
      (protected_bytes.size() + data_bytes - 1U) / data_bytes;
  if (outer_parity) ++codewords;
  codewords += (4U - codewords % 4U) % 4U;
  std::vector<std::uint8_t> packet(
      ledgrid::kFecWireHeaderBytes +
          codewords * codeword_bytes,
      0);
  std::copy_n(protected_bytes.begin(), ledgrid::kFecEnvelopeHeaderBytes,
              packet.begin());
  std::copy_n(protected_bytes.begin(), ledgrid::kFecEnvelopeHeaderBytes,
              packet.end() - ledgrid::kFecEnvelopeHeaderBytes);
  const std::size_t matrix_offset = ledgrid::kFecEnvelopeHeaderBytes;
  for (std::size_t block = 0; block < codewords; ++block) {
    std::uint8_t equations[ledgrid::kFecParityBytes]
                          [ledgrid::kFecParityBytes + 1U] = {};
    for (std::size_t symbol = 0; symbol < data_bytes; ++symbol) {
      std::uint8_t value = 0U;
      if (outer_parity && block == codewords - 1U) {
        for (std::size_t data_block = 0;
             data_block + 1U < codewords; ++data_block) {
          const std::size_t protected_offset =
              data_block * data_bytes + symbol;
          if (protected_offset < protected_bytes.size()) {
            value ^= protected_bytes[protected_offset];
          }
        }
      } else {
        const std::size_t protected_offset =
            block * data_bytes + symbol;
        value = protected_offset < protected_bytes.size()
            ? protected_bytes[protected_offset] : 0U;
      }
      packet[fec_rs_wire_offset(
          matrix_offset, symbol, block, codewords, diagonal)] = value;
      const std::uint8_t evaluation =
          static_cast<std::uint8_t>(symbol + 1U);
      std::uint8_t power = 1U;
      for (std::size_t check = 0;
           check < parity_bytes; ++check) {
        equations[check][parity_bytes] ^=
            fec_gf_multiply(value, power);
        power = fec_gf_multiply(power, evaluation);
      }
    }
    for (std::size_t check = 0;
         check < parity_bytes; ++check) {
      for (std::size_t parity = 0;
           parity < parity_bytes; ++parity) {
        const std::uint8_t evaluation = static_cast<std::uint8_t>(
            data_bytes + parity + 1U);
        equations[check][parity] = fec_gf_power(
            evaluation, static_cast<std::uint8_t>(check));
      }
    }
    for (std::size_t column = 0;
         column < parity_bytes; ++column) {
      std::size_t pivot = column;
      while (equations[pivot][column] == 0U) ++pivot;
      for (std::size_t value = column;
           value <= parity_bytes; ++value) {
        std::swap(equations[column][value], equations[pivot][value]);
      }
      const std::uint8_t inverse =
          fec_gf_inverse(equations[column][column]);
      for (std::size_t value = column;
           value <= parity_bytes; ++value) {
        equations[column][value] =
            fec_gf_multiply(equations[column][value], inverse);
      }
      for (std::size_t row = 0;
           row < parity_bytes; ++row) {
        if (row == column) continue;
        const std::uint8_t factor = equations[row][column];
        for (std::size_t value = column;
             value <= parity_bytes; ++value) {
          equations[row][value] ^=
              fec_gf_multiply(factor, equations[column][value]);
        }
      }
    }
    for (std::size_t parity = 0;
         parity < parity_bytes; ++parity) {
      const std::size_t symbol = data_bytes + parity;
      packet[fec_rs_wire_offset(
          matrix_offset, symbol, block, codewords, diagonal)] =
          equations[parity][parity_bytes];
    }
  }
  return packet;
}

std::vector<std::uint8_t> fec_v4_packet(
    const std::vector<std::uint8_t>& semantic) {
  return fec_rs_packet(
      semantic, ledgrid::kFecEnvelopeVersionV4, ledgrid::kFecV4DataBytes,
      ledgrid::kFecV4ParityBytes, ledgrid::kFecV4CodewordBytes);
}

std::vector<std::uint8_t> fec_packet(
    const std::vector<std::uint8_t>& semantic) {
  return fec_rs_packet(
      semantic, ledgrid::kFecEnvelopeVersion, ledgrid::kFecDataBytes,
      ledgrid::kFecParityBytes, ledgrid::kFecCodewordBytes, true, true);
}

std::vector<std::uint8_t> fec_v6_packet(
    const std::vector<std::uint8_t>& semantic) {
  return fec_rs_packet(
      semantic, ledgrid::kFecEnvelopeVersionV6, ledgrid::kFecDataBytes,
      ledgrid::kFecParityBytes, ledgrid::kFecCodewordBytes, true);
}

std::vector<std::uint8_t> fec_v5_packet(
    const std::vector<std::uint8_t>& semantic) {
  return fec_rs_packet(
      semantic, ledgrid::kFecEnvelopeVersionV5, ledgrid::kFecDataBytes,
      ledgrid::kFecParityBytes, ledgrid::kFecCodewordBytes);
}

void test_encoder_emits_parallel_grb_waveform() {
  // Two strips, one RGB pixel each. Only strip 0 green bit 7 is set.
  const std::uint8_t rgb[] = {0x00, 0x80, 0x00, 0x00, 0x00, 0x00};
  std::vector<std::uint8_t> output(ledgrid::ws2812_encoded_size(1));

  const auto result = ledgrid::encode_parallel_grb(
      rgb, sizeof(rgb), 2, 1, 255, output.data(), output.size());

  TEST_ASSERT_TRUE(result.ok);
  TEST_ASSERT_EQUAL_UINT32(output.size(), result.bytes_written);
  TEST_ASSERT_EQUAL_HEX8(0x03, output[0]);
  TEST_ASSERT_EQUAL_HEX8(0x01, output[1]);
  TEST_ASSERT_EQUAL_HEX8(0x00, output[2]);
  TEST_ASSERT_EQUAL_HEX8(0x03, output[3]);
  TEST_ASSERT_EQUAL_HEX8(0x00, output[4]);
  TEST_ASSERT_EQUAL_HEX8(0x00, output[5]);

  // Red begins after the eight green bits and is zero on both lanes.
  TEST_ASSERT_EQUAL_HEX8(0x03, output[24]);
  TEST_ASSERT_EQUAL_HEX8(0x00, output[25]);
  TEST_ASSERT_EQUAL_HEX8(0x00, output[26]);
}

void test_encoder_scales_brightness_before_bit_expansion() {
  const std::uint8_t rgb[] = {0x00, 0xFF, 0x00};
  std::vector<std::uint8_t> output(ledgrid::ws2812_encoded_size(1));

  auto result = ledgrid::encode_parallel_grb(
      rgb, sizeof(rgb), 1, 1, 128, output.data(), output.size());
  TEST_ASSERT_TRUE(result.ok);
  TEST_ASSERT_EQUAL_HEX8(0x01, output[1]);   // Scaled value 128: bit 7 set.
  TEST_ASSERT_EQUAL_HEX8(0x00, output[4]);   // Bit 6 clear.

  result = ledgrid::encode_parallel_grb(
      rgb, sizeof(rgb), 1, 1, 0, output.data(), output.size());
  TEST_ASSERT_TRUE(result.ok);
  for (std::size_t bit = 0; bit < 8; ++bit) {
    TEST_ASSERT_EQUAL_HEX8(0x00, output[bit * 3 + 1]);
  }
}

void test_encoder_refreshes_cached_expansion_when_brightness_changes() {
  constexpr std::uint8_t kRgb[] = {0x63, 0xB7, 0xD2};
  std::vector<std::uint8_t> first(ledgrid::ws2812_encoded_size(1));
  std::vector<std::uint8_t> different(ledgrid::ws2812_encoded_size(1));
  std::vector<std::uint8_t> repeated(ledgrid::ws2812_encoded_size(1));

  TEST_ASSERT_TRUE(ledgrid::encode_parallel_grb(
      kRgb, sizeof(kRgb), 1, 1, 37, first.data(), first.size()).ok);
  TEST_ASSERT_TRUE(ledgrid::encode_parallel_grb(
      kRgb, sizeof(kRgb), 1, 1, 211, different.data(), different.size()).ok);
  TEST_ASSERT_TRUE(ledgrid::encode_parallel_grb(
      kRgb, sizeof(kRgb), 1, 1, 37, repeated.data(), repeated.size()).ok);

  TEST_ASSERT_FALSE(std::equal(first.begin(), first.end(), different.begin()));
  TEST_ASSERT_EQUAL_HEX8_ARRAY(first.data(), repeated.data(), first.size());
}

void test_optimized_encoder_updates_all_eight_lanes() {
  std::array<std::uint8_t, 8U * 3U> rgb{};
  for (std::size_t lane = 0; lane < 8; ++lane) {
    rgb[lane * 3U + 1U] = static_cast<std::uint8_t>(0x80U >> lane);
  }
  std::vector<std::uint8_t> output(ledgrid::ws2812_encoded_size(1), 0xA5);

  TEST_ASSERT_TRUE(ledgrid::initialize_parallel_grb_waveform(
      8, 1, output.data(), output.size()));
  const auto result = ledgrid::encode_parallel_grb_pixels(
      rgb.data(), rgb.size(), 8, 1, 255, output.data(), output.size());

  TEST_ASSERT_TRUE(result.ok);
  for (std::size_t bit = 0; bit < 8; ++bit) {
    TEST_ASSERT_EQUAL_HEX8(0xFF, output[bit * 3U]);
    TEST_ASSERT_EQUAL_HEX8(
        static_cast<std::uint8_t>(1U << bit), output[bit * 3U + 1U]);
    TEST_ASSERT_EQUAL_HEX8(0, output[bit * 3U + 2U]);
  }
}

void test_lane_mask_silences_unselected_lanes() {
  // Full green on all eight lanes, so every emitted bit would be a one.
  std::array<std::uint8_t, 8U * 3U> rgb{};
  for (std::size_t lane = 0; lane < 8; ++lane) {
    rgb[lane * 3U + 1U] = 0xFF;
  }
  std::vector<std::uint8_t> output(ledgrid::ws2812_encoded_size(1), 0xA5);
  constexpr std::uint8_t kMask = 0x05;  // lanes 0 and 2 only

  TEST_ASSERT_TRUE(ledgrid::initialize_parallel_grb_waveform(
      8, 1, output.data(), output.size(), ledgrid::kWs2812ResetUs,
      ledgrid::kWs2812SampleRateHz, kMask));
  const auto result = ledgrid::encode_parallel_grb_pixels(
      rgb.data(), rgb.size(), 8, 1, 255, output.data(), output.size(),
      ledgrid::kWs2812ResetUs, ledgrid::kWs2812SampleRateHz, kMask);
  TEST_ASSERT_TRUE(result.ok);

  // Masked lanes must stay low in the leading sample too, otherwise they would
  // still contribute a switching edge on every bit.
  for (std::size_t bit = 0; bit < 8; ++bit) {
    TEST_ASSERT_EQUAL_HEX8(kMask, output[bit * 3U]);
    TEST_ASSERT_EQUAL_HEX8(kMask, output[bit * 3U + 1U]);
    TEST_ASSERT_EQUAL_HEX8(0x00, output[bit * 3U + 2U]);
  }
}

void test_lane_mask_defaults_to_every_lane() {
  std::array<std::uint8_t, 8U * 3U> rgb{};
  rgb[1] = 0xFF;
  std::vector<std::uint8_t> output(ledgrid::ws2812_encoded_size(1), 0xA5);

  const auto result = ledgrid::encode_parallel_grb(
      rgb.data(), rgb.size(), 8, 1, 255, output.data(), output.size());
  TEST_ASSERT_TRUE(result.ok);
  TEST_ASSERT_EQUAL_HEX8(0xFF, output[0]);
  TEST_ASSERT_EQUAL_HEX8(0x01, output[1]);
}

void test_compact_strip_can_target_an_independent_physical_lane() {
  const std::uint8_t rgb[] = {0, 0xFF, 0};
  std::vector<std::uint8_t> output(ledgrid::ws2812_encoded_size(1), 0xA5);
  constexpr std::uint8_t kPhysicalLane = 0x04;
  // The real driver owns an eight-lane bus even when the configured logical
  // receiver width is one strip.
  TEST_ASSERT_TRUE(ledgrid::initialize_parallel_grb_waveform(
      8, 1, output.data(), output.size(), ledgrid::kWs2812ResetUs,
      ledgrid::kWs2812SampleRateHz, kPhysicalLane));
  const auto result = ledgrid::encode_parallel_grb_pixels(
      rgb, sizeof(rgb), 1, 1, 255, output.data(), output.size(),
      ledgrid::kWs2812ResetUs, ledgrid::kWs2812SampleRateHz, kPhysicalLane,
      ledgrid::kStaggerOff, true);
  TEST_ASSERT_TRUE(result.ok);
  for (std::size_t bit = 0; bit < 8; ++bit) {
    TEST_ASSERT_EQUAL_HEX8(kPhysicalLane, output[bit * 3U]);
    TEST_ASSERT_EQUAL_HEX8(kPhysicalLane, output[bit * 3U + 1U]);
    TEST_ASSERT_EQUAL_HEX8(0, output[bit * 3U + 2U]);
  }
}

// The waveform the encoder produced before staggering existed: every active
// lane rises together on the first sample of each symbol, the data lands on
// the second, and the third is always low. Rebuilt here rather than captured
// so the stagger-off regression guard has an independent reference.
std::vector<std::uint8_t> reference_waveform(
    const std::uint8_t* rgb,
    std::uint8_t strip_count,
    std::uint16_t leds_per_strip) {
  constexpr std::uint8_t kGrbOffsets[3] = {1, 0, 2};
  std::vector<std::uint8_t> expected(
      ledgrid::ws2812_encoded_size(leds_per_strip), 0);
  const std::size_t lane_stride = static_cast<std::size_t>(leds_per_strip) * 3U;
  const auto active_mask =
      strip_count == 8 ? 0xFFU : (1U << strip_count) - 1U;

  std::size_t sample = 0;
  for (std::uint16_t pixel = 0; pixel < leds_per_strip; ++pixel) {
    for (std::uint8_t channel = 0; channel < 3; ++channel) {
      const std::size_t offset =
          static_cast<std::size_t>(pixel) * 3U + kGrbOffsets[channel];
      for (std::uint8_t bit = 0; bit < 8; ++bit) {
        std::uint8_t data = 0;
        for (std::uint8_t lane = 0; lane < strip_count; ++lane) {
          const std::uint8_t value = rgb[offset + lane_stride * lane];
          if ((value & (0x80U >> bit)) != 0) data |= 1U << lane;
        }
        expected[sample] = static_cast<std::uint8_t>(active_mask);
        expected[sample + 1U] = data;
        sample += 3U;
      }
    }
  }
  return expected;
}

// The bit a lane latches for symbol `bit_index`, read at that lane's own
// offset within the symbol.
std::uint8_t staggered_bit(
    const std::vector<std::uint8_t>& output,
    std::size_t bit_index,
    std::uint8_t lane,
    std::uint8_t phases) {
  const std::size_t sample =
      bit_index * ledgrid::kSamplesPerBit + (lane % phases) + 1U;
  return (output[sample] >> lane) & 1U;
}

// Lanes whose output goes low-to-high at `sample`. Sample 0 follows an idle
// bus, so anything high there has just risen.
std::uint8_t rising_lanes(
    const std::vector<std::uint8_t>& output, std::size_t sample) {
  const std::uint8_t previous = sample == 0 ? 0 : output[sample - 1U];
  return static_cast<std::uint8_t>(output[sample] & ~previous);
}

std::uint8_t popcount8(std::uint8_t value) {
  std::uint8_t count = 0;
  for (std::uint8_t bit = 0; bit < 8; ++bit) {
    if ((value & (1U << bit)) != 0) ++count;
  }
  return count;
}

void test_compact_single_strip_round_trips_on_every_staggered_lane() {
  constexpr std::uint16_t kLeds = 138;
  constexpr std::uint8_t kPhases = 3;
  constexpr std::uint8_t kGrbOffsets[3] = {1, 0, 2};
  std::array<std::uint8_t, kLeds * 3U> rgb{};
  for (std::size_t index = 0; index < rgb.size(); ++index) {
    rgb[index] = static_cast<std::uint8_t>(index * 29U + 7U);
  }
  // Exercise the stream's final data bit, including lanes whose phase places
  // it in the first byte of the reset region.
  rgb.back() = 0xFF;

  for (std::uint8_t physical_lane = 0; physical_lane < 8; ++physical_lane) {
    const std::uint8_t lane_mask =
        static_cast<std::uint8_t>(1U << physical_lane);
    std::vector<std::uint8_t> output(
        ledgrid::ws2812_encoded_size(kLeds), 0xA5);
    TEST_ASSERT_TRUE(ledgrid::initialize_parallel_grb_waveform(
        8, kLeds, output.data(), output.size(), ledgrid::kWs2812ResetUs,
        ledgrid::kWs2812SampleRateHz, lane_mask, kPhases));
    TEST_ASSERT_TRUE(ledgrid::encode_parallel_grb_pixels(
        rgb.data(), rgb.size(), 1, kLeds, 255, output.data(), output.size(),
        ledgrid::kWs2812ResetUs, ledgrid::kWs2812SampleRateHz, lane_mask,
        kPhases, true).ok);

    std::size_t bit_index = 0;
    for (std::uint16_t pixel = 0; pixel < kLeds; ++pixel) {
      for (std::uint8_t channel = 0; channel < 3; ++channel) {
        const std::size_t offset =
            static_cast<std::size_t>(pixel) * 3U + kGrbOffsets[channel];
        std::uint8_t decoded = 0;
        for (std::uint8_t bit = 0; bit < 8; ++bit) {
          decoded = static_cast<std::uint8_t>(
              (decoded << 1U) |
              staggered_bit(output, bit_index, physical_lane, kPhases));
          ++bit_index;
        }
        TEST_ASSERT_EQUAL_HEX8(rgb[offset], decoded);
      }
    }
    for (const auto sample : output) {
      TEST_ASSERT_EQUAL_HEX8(0, sample & ~lane_mask);
    }
  }
}

void test_compact_single_strip_broadcasts_to_every_selected_lane() {
  constexpr std::uint16_t kLeds = 138;
  constexpr std::uint8_t kPhases = 3;
  constexpr std::uint8_t kGrbOffsets[3] = {1, 0, 2};
  constexpr std::array<std::uint8_t, 2> kMasks = {0xFF, 0xA4};
  std::array<std::uint8_t, kLeds * 3U> rgb{};
  for (std::size_t index = 0; index < rgb.size(); ++index) {
    rgb[index] = static_cast<std::uint8_t>(index * 37U + 11U);
  }

  for (const std::uint8_t lane_mask : kMasks) {
    std::vector<std::uint8_t> output(
        ledgrid::ws2812_encoded_size(kLeds), 0xA5);
    // The live LCD/I80 driver owns all eight physical outputs even though this
    // frame has one semantic strip. Its mask selects the candidate tail wires.
    TEST_ASSERT_TRUE(ledgrid::initialize_parallel_grb_waveform(
        8, kLeds, output.data(), output.size(), ledgrid::kWs2812ResetUs,
        ledgrid::kWs2812SampleRateHz, lane_mask, kPhases));
    TEST_ASSERT_TRUE(ledgrid::encode_parallel_grb_pixels(
        rgb.data(), rgb.size(), 1, kLeds, 255, output.data(), output.size(),
        ledgrid::kWs2812ResetUs, ledgrid::kWs2812SampleRateHz, lane_mask,
        kPhases, true).ok);

    for (std::uint8_t physical_lane = 0; physical_lane < 8; ++physical_lane) {
      if ((lane_mask & (1U << physical_lane)) == 0) continue;
      std::size_t bit_index = 0;
      for (std::uint16_t pixel = 0; pixel < kLeds; ++pixel) {
        for (std::uint8_t channel = 0; channel < 3; ++channel) {
          const std::size_t offset =
              static_cast<std::size_t>(pixel) * 3U + kGrbOffsets[channel];
          std::uint8_t decoded = 0;
          for (std::uint8_t bit = 0; bit < 8; ++bit) {
            decoded = static_cast<std::uint8_t>(
                (decoded << 1U) |
                staggered_bit(
                    output, bit_index, physical_lane, kPhases));
            ++bit_index;
          }
          TEST_ASSERT_EQUAL_HEX8(rgb[offset], decoded);
        }
      }
    }
    for (const auto sample : output) {
      TEST_ASSERT_EQUAL_HEX8(0, sample & ~lane_mask);
    }
  }
}

void test_stagger_phase_lanes_partitions_every_lane() {
  // One phase keeps every lane together, which is the pre-stagger waveform.
  TEST_ASSERT_EQUAL_HEX8(0xFF, ledgrid::stagger_phase_lanes(0, 1, 0xFF));
  TEST_ASSERT_EQUAL_HEX8(0x00, ledgrid::stagger_phase_lanes(1, 1, 0xFF));
  TEST_ASSERT_EQUAL_HEX8(0x00, ledgrid::stagger_phase_lanes(2, 1, 0xFF));

  // Three phases split the eight lanes into disjoint sets that cover them all.
  const std::uint8_t phase0 = ledgrid::stagger_phase_lanes(0, 3, 0xFF);
  const std::uint8_t phase1 = ledgrid::stagger_phase_lanes(1, 3, 0xFF);
  const std::uint8_t phase2 = ledgrid::stagger_phase_lanes(2, 3, 0xFF);
  TEST_ASSERT_EQUAL_HEX8(0x49, phase0);  // lanes 0, 3, 6
  TEST_ASSERT_EQUAL_HEX8(0x92, phase1);  // lanes 1, 4, 7
  TEST_ASSERT_EQUAL_HEX8(0x24, phase2);  // lanes 2, 5
  TEST_ASSERT_EQUAL_HEX8(0x00, phase0 & phase1);
  TEST_ASSERT_EQUAL_HEX8(0x00, phase0 & phase2);
  TEST_ASSERT_EQUAL_HEX8(0x00, phase1 & phase2);
  TEST_ASSERT_EQUAL_HEX8(0xFF, phase0 | phase1 | phase2);

  // Masked lanes stay out of every phase.
  TEST_ASSERT_EQUAL_HEX8(0x09, ledgrid::stagger_phase_lanes(0, 3, 0x0F));
  TEST_ASSERT_EQUAL_HEX8(0x02, ledgrid::stagger_phase_lanes(1, 3, 0x0F));

  // Out-of-range phase counts fall back to no staggering rather than
  // silently producing a partition the initializer did not build.
  TEST_ASSERT_EQUAL_HEX8(0xFF, ledgrid::stagger_phase_lanes(0, 0, 0xFF));
  TEST_ASSERT_EQUAL_HEX8(0xFF, ledgrid::stagger_phase_lanes(0, 4, 0xFF));
  TEST_ASSERT_EQUAL_HEX8(0x00, ledgrid::stagger_phase_lanes(1, 4, 0xFF));
}

void test_stagger_off_reproduces_the_original_waveform() {
  // Byte-for-byte guard on the fallback path: if staggering does not fix the
  // wall, this is what ships, so it must not have moved at all.
  constexpr std::uint16_t kLeds = 3;
  std::array<std::uint8_t, 8U * kLeds * 3U> rgb{};
  for (std::size_t index = 0; index < rgb.size(); ++index) {
    rgb[index] = static_cast<std::uint8_t>(index * 7U + 11U);
  }
  const auto expected = reference_waveform(rgb.data(), 8, kLeds);

  std::vector<std::uint8_t> output(ledgrid::ws2812_encoded_size(kLeds), 0xA5);
  const auto result = ledgrid::encode_parallel_grb(
      rgb.data(), rgb.size(), 8, kLeds, 255, output.data(), output.size(),
      ledgrid::kWs2812ResetUs, ledgrid::kWs2812SampleRateHz,
      ledgrid::kAllLanesMask, ledgrid::kStaggerOff);

  TEST_ASSERT_TRUE(result.ok);
  TEST_ASSERT_EQUAL_UINT32(expected.size(), output.size());
  TEST_ASSERT_EQUAL_HEX8_ARRAY(expected.data(), output.data(), expected.size());
}

void test_stagger_caps_coincident_rising_edges_at_three() {
  constexpr std::uint16_t kLeds = 2;
  // Every bit a one, so each lane rises as often as the waveform allows.
  std::array<std::uint8_t, 8U * kLeds * 3U> rgb{};
  rgb.fill(0xFF);

  std::vector<std::uint8_t> together(ledgrid::ws2812_encoded_size(kLeds), 0xA5);
  TEST_ASSERT_TRUE(ledgrid::encode_parallel_grb(
      rgb.data(), rgb.size(), 8, kLeds, 255, together.data(), together.size(),
      ledgrid::kWs2812ResetUs, ledgrid::kWs2812SampleRateHz,
      ledgrid::kAllLanesMask, ledgrid::kStaggerOff).ok);

  std::vector<std::uint8_t> spread(ledgrid::ws2812_encoded_size(kLeds), 0xA5);
  TEST_ASSERT_TRUE(ledgrid::encode_parallel_grb(
      rgb.data(), rgb.size(), 8, kLeds, 255, spread.data(), spread.size(),
      ledgrid::kWs2812ResetUs, ledgrid::kWs2812SampleRateHz,
      ledgrid::kAllLanesMask, 3).ok);

  std::uint8_t peak_together = 0;
  std::uint8_t peak_spread = 0;
  for (std::size_t sample = 0; sample < spread.size(); ++sample) {
    peak_together = std::max(peak_together, popcount8(rising_lanes(together, sample)));
    const std::uint8_t rises = rising_lanes(spread, sample);
    peak_spread = std::max(peak_spread, popcount8(rises));
    // Only the lanes sitting at this sample's phase may rise here.
    TEST_ASSERT_EQUAL_HEX8(
        0,
        rises & ~ledgrid::stagger_phase_lanes(
                    static_cast<std::uint8_t>(sample % 3U), 3, 0xFF));
  }

  TEST_ASSERT_EQUAL_UINT8(8, peak_together);
  TEST_ASSERT_EQUAL_UINT8(3, peak_spread);
}

void test_stagger_round_trips_every_lane() {
  constexpr std::uint16_t kLeds = 3;
  constexpr std::uint8_t kPhases = 3;
  constexpr std::uint8_t kGrbOffsets[3] = {1, 0, 2};
  std::array<std::uint8_t, 8U * kLeds * 3U> rgb{};
  for (std::size_t index = 0; index < rgb.size(); ++index) {
    rgb[index] = static_cast<std::uint8_t>(index * 13U + 5U);
  }
  // The last pixel's blue LSB is the final bit of the stream, and for a lane
  // at the trailing phase it lands in the first byte of the reset region.
  rgb[rgb.size() - 1U] = 0xFF;

  std::vector<std::uint8_t> output(ledgrid::ws2812_encoded_size(kLeds), 0xA5);
  TEST_ASSERT_TRUE(ledgrid::encode_parallel_grb(
      rgb.data(), rgb.size(), 8, kLeds, 255, output.data(), output.size(),
      ledgrid::kWs2812ResetUs, ledgrid::kWs2812SampleRateHz,
      ledgrid::kAllLanesMask, kPhases).ok);

  const std::size_t lane_stride = static_cast<std::size_t>(kLeds) * 3U;
  for (std::uint8_t lane = 0; lane < 8; ++lane) {
    std::size_t bit_index = 0;
    for (std::uint16_t pixel = 0; pixel < kLeds; ++pixel) {
      for (std::uint8_t channel = 0; channel < 3; ++channel) {
        const std::size_t offset = static_cast<std::size_t>(pixel) * 3U +
                                   kGrbOffsets[channel] + lane_stride * lane;
        std::uint8_t decoded = 0;
        for (std::uint8_t bit = 0; bit < 8; ++bit) {
          decoded = static_cast<std::uint8_t>(
              (decoded << 1U) |
              staggered_bit(output, bit_index, lane, kPhases));
          ++bit_index;
        }
        TEST_ASSERT_EQUAL_HEX8(rgb[offset], decoded);
      }
    }
  }
}

void test_stagger_writes_stay_inside_the_encoded_buffer() {
  constexpr std::uint16_t kLeds = 138;
  constexpr std::uint8_t kPhases = 3;
  constexpr std::size_t kGuard = 32;
  constexpr std::uint8_t kGuardValue = 0x5A;
  const std::size_t required = ledgrid::ws2812_encoded_size(kLeds);
  const std::size_t data_samples = static_cast<std::size_t>(kLeds) * 72U;
  std::array<std::uint8_t, 8U * kLeds * 3U> rgb{};
  rgb.fill(0xFF);

  std::vector<std::uint8_t> output(required + kGuard, kGuardValue);
  TEST_ASSERT_TRUE(ledgrid::encode_parallel_grb(
      rgb.data(), rgb.size(), 8, kLeds, 255, output.data(), output.size(),
      ledgrid::kWs2812ResetUs, ledgrid::kWs2812SampleRateHz,
      ledgrid::kAllLanesMask, kPhases).ok);

  // The trailing phase writes as far as data_samples and no further, and that
  // byte is inside the reset region rather than past the encoded frame. It
  // legitimately carries the trailing phase's final data bit, but nothing
  // else: a leading high edge here would start a symbol that never completes.
  TEST_ASSERT_TRUE(data_samples < required);
  TEST_ASSERT_EQUAL_HEX8(
      ledgrid::stagger_phase_lanes(2, kPhases, 0xFF), output[data_samples]);
  for (std::size_t sample = data_samples + 1U; sample < required; ++sample) {
    TEST_ASSERT_EQUAL_HEX8(0, output[sample]);
  }
  for (std::size_t sample = required; sample < output.size(); ++sample) {
    TEST_ASSERT_EQUAL_HEX8(kGuardValue, output[sample]);
  }

  // The fifth installed receiver maps one compact logical strip to a selected
  // physical output. Lane 7 belongs to phase 1, so its final bit lands one
  // sample before the reset region and the entire reset tail must stay low.
  constexpr std::uint8_t kLane7 = 0x80;
  std::array<std::uint8_t, kLeds * 3U> compact_rgb{};
  compact_rgb.fill(0xFF);
  std::vector<std::uint8_t> compact_output(
      required + kGuard, kGuardValue);
  TEST_ASSERT_TRUE(ledgrid::initialize_parallel_grb_waveform(
      8, kLeds, compact_output.data(), compact_output.size(),
      ledgrid::kWs2812ResetUs, ledgrid::kWs2812SampleRateHz, kLane7,
      kPhases));
  TEST_ASSERT_TRUE(ledgrid::encode_parallel_grb_pixels(
      compact_rgb.data(), compact_rgb.size(), 1, kLeds, 255,
      compact_output.data(), compact_output.size(), ledgrid::kWs2812ResetUs,
      ledgrid::kWs2812SampleRateHz, kLane7, kPhases, true).ok);

  TEST_ASSERT_EQUAL_HEX8(kLane7, compact_output[data_samples - 1U]);
  for (std::size_t sample = data_samples; sample < required; ++sample) {
    TEST_ASSERT_EQUAL_HEX8(0, compact_output[sample]);
  }
  for (std::size_t sample = required; sample < compact_output.size();
       ++sample) {
    TEST_ASSERT_EQUAL_HEX8(kGuardValue, compact_output[sample]);
  }
}

void test_encoder_appends_300us_reset_and_rejects_bad_bounds() {
  TEST_ASSERT_EQUAL_UINT32(720, ledgrid::ws2812_reset_samples());
  TEST_ASSERT_EQUAL_UINT32(792, ledgrid::ws2812_encoded_size(1));

  const std::uint8_t rgb[] = {1, 2, 3};
  std::vector<std::uint8_t> output(ledgrid::ws2812_encoded_size(1));
  auto result = ledgrid::encode_parallel_grb(
      rgb, sizeof(rgb), 1, 1, 255, output.data(), output.size());
  TEST_ASSERT_TRUE(result.ok);
  for (std::size_t i = 72; i < output.size(); ++i) {
    TEST_ASSERT_EQUAL_HEX8(0, output[i]);
  }

  TEST_ASSERT_FALSE(ledgrid::encode_parallel_grb(
      rgb, sizeof(rgb), 0, 1, 255, output.data(), output.size()).ok);
  TEST_ASSERT_FALSE(ledgrid::encode_parallel_grb(
      rgb, sizeof(rgb), 9, 1, 255, output.data(), output.size()).ok);
  TEST_ASSERT_FALSE(ledgrid::encode_parallel_grb(
      rgb, sizeof(rgb) - 1, 1, 1, 255, output.data(), output.size()).ok);
  TEST_ASSERT_FALSE(ledgrid::encode_parallel_grb(
      rgb, sizeof(rgb), 1, 1, 255, output.data(), output.size() - 1).ok);
}

void test_startup_rainbow_is_45_degrees_and_moves_up_right() {
  constexpr std::uint8_t strips = 3;
  constexpr std::uint16_t leds = 4;
  std::array<std::uint8_t, strips * leds * 3U> initial{};
  std::array<std::uint8_t, strips * leds * 3U> advanced{};
  const std::uint32_t one_diagonal_step_us =
      (2U * ledgrid::kStartupRainbowCycleUs) /
      ledgrid::kStartupRainbowPeriodPixels;

  TEST_ASSERT_TRUE(ledgrid::render_startup_rainbow(
      0, strips, leds, initial.data(), initial.size()));
  TEST_ASSERT_TRUE(ledgrid::render_startup_rainbow(
      one_diagonal_step_us,
      strips,
      leds,
      advanced.data(),
      advanced.size()));

  // Equal phase along x and y produces a 45-degree field. After 1/16 second,
  // each color has moved one coordinate toward both positive axes.
  for (std::uint8_t strip = 0; strip + 1 < strips; ++strip) {
    for (std::uint16_t led = 0; led + 1 < leds; ++led) {
      const std::size_t source =
          (static_cast<std::size_t>(strip) * leds + led) * 3U;
      const std::size_t right =
          (static_cast<std::size_t>(strip + 1U) * leds + led) * 3U;
      const std::size_t up =
          (static_cast<std::size_t>(strip) * leds + led + 1U) * 3U;
      const std::size_t up_right =
          (static_cast<std::size_t>(strip + 1U) * leds + led + 1U) * 3U;
      TEST_ASSERT_EQUAL_MEMORY(initial.data() + right,
                               initial.data() + up, 3);
      TEST_ASSERT_EQUAL_MEMORY(initial.data() + source,
                               advanced.data() + up_right, 3);
    }
  }
}

void test_startup_rainbow_cycles_once_per_second_and_checks_bounds() {
  constexpr std::uint8_t strips = 8;
  constexpr std::uint16_t leds = 32;
  std::array<std::uint8_t, strips * leds * 3U> initial{};
  std::array<std::uint8_t, strips * leds * 3U> looped{};

  TEST_ASSERT_TRUE(ledgrid::render_startup_rainbow(
      0, strips, leds, initial.data(), initial.size()));
  TEST_ASSERT_TRUE(ledgrid::render_startup_rainbow(
      ledgrid::kStartupRainbowCycleUs,
      strips,
      leds,
      looped.data(),
      looped.size()));
  TEST_ASSERT_EQUAL_MEMORY(initial.data(), looped.data(), initial.size());
  TEST_ASSERT_EQUAL_HEX8(0xFF, initial[0]);
  TEST_ASSERT_EQUAL_HEX8(0x00, initial[1]);
  TEST_ASSERT_EQUAL_HEX8(0x00, initial[2]);

  TEST_ASSERT_FALSE(ledgrid::render_startup_rainbow(
      0, strips, leds, nullptr, initial.size()));
  TEST_ASSERT_FALSE(ledgrid::render_startup_rainbow(
      0, strips, leds, initial.data(), initial.size() - 1U));
}

void test_mailbox_replaces_only_unread_ready_frames() {
  ledgrid::LatestFrameMailbox mailbox;
  ledgrid::FrameMetadata metadata{};

  int slot0 = mailbox.begin_write();
  TEST_ASSERT_GREATER_OR_EQUAL(0, slot0);
  metadata.sequence = 1;
  TEST_ASSERT_TRUE(mailbox.commit_write(slot0, metadata));

  ledgrid::FrameMetadata reading{};
  TEST_ASSERT_EQUAL_INT(slot0, mailbox.begin_read(&reading));
  TEST_ASSERT_EQUAL_UINT32(1, reading.sequence);

  int slot1 = mailbox.begin_write();
  metadata.sequence = 2;
  TEST_ASSERT_TRUE(mailbox.commit_write(slot1, metadata));
  int slot2 = mailbox.begin_write();
  metadata.sequence = 3;
  TEST_ASSERT_TRUE(mailbox.commit_write(slot2, metadata));

  // One slot is being read, one is ready, and the third remains free. A fourth
  // publish replaces a ready frame but never the frame being displayed.
  int replacement = mailbox.begin_write();
  metadata.sequence = 4;
  TEST_ASSERT_TRUE(mailbox.commit_write(replacement, metadata));
  TEST_ASSERT_EQUAL(ledgrid::LatestFrameMailbox::SlotState::Reading,
                    mailbox.state(slot0));
  TEST_ASSERT_EQUAL_UINT32(1, mailbox.counters().superseded);

  TEST_ASSERT_TRUE(mailbox.finish_read(slot0));
  TEST_ASSERT_EQUAL_UINT32(1, mailbox.counters().displayed);
  TEST_ASSERT_EQUAL_UINT32(4, mailbox.counters().accepted);

  int newest = mailbox.begin_read(&reading);
  TEST_ASSERT_GREATER_OR_EQUAL(0, newest);
  TEST_ASSERT_EQUAL_UINT32(4, reading.sequence);
}

void test_status_v2_layout_is_stable() {
  ledgrid::ReceiverStatusV2 status{};
  status.flags = 3;
  status.active_strips = 8;
  status.lane_mask = 0x5A;
  status.leds_per_strip = 140;
  status.queued_transactions = 2;
  status.packets = 11;
  status.crc_errors = 12;
  status.crc_ok_packets = 13;
  status.frames_accepted = 14;
  status.frames_displayed = 15;
  status.frames_superseded = 16;
  status.publish_drops = 17;
  status.spi_queue_errors = 18;
  status.last_crc_us = 19;
  status.last_copy_us = 20;
  status.last_encode_us = 21;
  status.last_show_us = 22;
  status.last_accepted_sequence = 23;
  status.last_displayed_sequence = 24;
  status.stagger_phases = 3;
  std::array<std::uint8_t, ledgrid::kStatusBytesV2> encoded{};

  TEST_ASSERT_TRUE(ledgrid::encode_receiver_status_v2(
      status, encoded.data(), encoded.size()));
  TEST_ASSERT_EQUAL_MEMORY("LGS2", encoded.data(), 4);
  TEST_ASSERT_EQUAL_UINT8(2, encoded[4]);
  TEST_ASSERT_EQUAL_UINT8(3, encoded[5]);
  TEST_ASSERT_EQUAL_UINT8(8, encoded[6]);
  TEST_ASSERT_EQUAL_HEX8(0x5A, encoded[7]);
  TEST_ASSERT_EQUAL_UINT16(140, read_u16(encoded.data() + 8));
  TEST_ASSERT_EQUAL_UINT16(2, read_u16(encoded.data() + 10));
  TEST_ASSERT_EQUAL_UINT32(14, read_u32(encoded.data() + 24));
  TEST_ASSERT_EQUAL_UINT32(16, read_u32(encoded.data() + 32));
  TEST_ASSERT_EQUAL_UINT16(21, read_u16(encoded.data() + 48));
  TEST_ASSERT_EQUAL_UINT32(24, read_u32(encoded.data() + 56));
  TEST_ASSERT_EQUAL_UINT8(3, encoded[64]);

  TEST_ASSERT_FALSE(ledgrid::encode_receiver_status_v2(
      status, encoded.data(), ledgrid::kStatusBytesV2 - 1));
}

void test_aligned_envelope_decodes_exact_semantic_payload_and_legacy_packets() {
  const std::vector<std::uint8_t> semantic = {
      static_cast<std::uint8_t>(ledgrid::ReceiverCommand::SetRange),
      0x00, 0x02, 0x01, 0x11, 0x22, 0x33};
  const auto envelope = aligned_packet(semantic);
  TEST_ASSERT_EQUAL_UINT32(0, envelope.size() % 4U);
  TEST_ASSERT_EQUAL_UINT32(16, envelope.size());
  TEST_ASSERT_EQUAL_HEX8(0, envelope[11]);

  ledgrid::ReceiverPacketPayload decoded{};
  TEST_ASSERT_TRUE(ledgrid::decode_receiver_packet_payload(
      envelope.data(), envelope.size(), &decoded));
  TEST_ASSERT_TRUE(decoded.aligned_envelope);
  TEST_ASSERT_EQUAL_UINT32(semantic.size(), decoded.size);
  TEST_ASSERT_EQUAL_MEMORY(semantic.data(), decoded.data, semantic.size());

  // A valid v1 transaction may collide with a v2 codeword-aligned wire size;
  // the two-bit marker/version separation keeps it on the v1 path.
  std::vector<std::uint8_t> colliding(514, 0x31);
  colliding[0] = static_cast<std::uint8_t>(ledgrid::ReceiverCommand::SetAll);
  const auto colliding_v1 = aligned_packet(colliding);
  TEST_ASSERT_EQUAL_UINT32(520, colliding_v1.size());
  ledgrid::ReceiverPacketDecodeReport colliding_report{};
  TEST_ASSERT_TRUE(ledgrid::decode_receiver_packet_payload(
      colliding_v1.data(), colliding_v1.size(), &decoded, &colliding_report));
  TEST_ASSERT_FALSE(colliding_report.fec_envelope_attempted);
  TEST_ASSERT_EQUAL_MEMORY(colliding.data(), decoded.data, colliding.size());

  const auto legacy = legacy_packet(semantic);
  TEST_ASSERT_TRUE(ledgrid::decode_receiver_packet_payload(
      legacy.data(), legacy.size(), &decoded));
  TEST_ASSERT_FALSE(decoded.aligned_envelope);
  TEST_ASSERT_EQUAL_UINT32(semantic.size(), decoded.size);
  TEST_ASSERT_EQUAL_MEMORY(semantic.data(), decoded.data, semantic.size());
}

void test_aligned_envelope_accepts_frame_and_exact_maximum_semantic_sizes() {
  std::vector<std::uint8_t> frame(1U + 8U * 138U * 3U, 0x5A);
  frame[0] = static_cast<std::uint8_t>(ledgrid::ReceiverCommand::SetAll);
  const auto frame_packet = aligned_packet(frame);
  TEST_ASSERT_EQUAL_UINT32(3320, frame_packet.size());
  TEST_ASSERT_EQUAL_UINT32(0, frame_packet.size() % 4U);

  ledgrid::ReceiverPacketPayload decoded{};
  TEST_ASSERT_TRUE(ledgrid::decode_receiver_packet_payload(
      frame_packet.data(), frame_packet.size(), &decoded));
  TEST_ASSERT_EQUAL_UINT32(frame.size(), decoded.size);
  TEST_ASSERT_EQUAL_MEMORY(frame.data(), decoded.data, frame.size());

  std::vector<std::uint8_t> maximum(
      ledgrid::kAlignedEnvelopeMaxSemanticBytes, 0xA6);
  maximum[0] = static_cast<std::uint8_t>(
      ledgrid::ReceiverCommand::NativeModuleChunk);
  const auto maximum_packet = aligned_packet(maximum);
  TEST_ASSERT_EQUAL_UINT32(
      ledgrid::kAnimationPipelineMaxTransactionBytes, maximum_packet.size());
  TEST_ASSERT_TRUE(ledgrid::decode_receiver_packet_payload(
      maximum_packet.data(), maximum_packet.size(), &decoded));
  TEST_ASSERT_EQUAL_UINT32(maximum.size(), decoded.size);
}

void test_aligned_envelope_preserves_full_status_query_semantics() {
  struct Case {
    std::size_t semantic_size;
    std::size_t wire_size;
    bool sparse_overlay;
    bool installation_profile;
    bool native_modules;
  };
  constexpr std::array<Case, 4> kCases = {{
      {ledgrid::kStatusBytesV3, 328, false, false, false},
      {ledgrid::kStatusBytesV4, 424, true, false, false},
      {ledgrid::kStatusBytesV5, 776, true, true, false},
      {ledgrid::kStatusBytesV6, 1224, true, true, true},
  }};

  for (const auto& test_case : kCases) {
    std::vector<std::uint8_t> semantic(test_case.semantic_size, 0);
    semantic[0] = static_cast<std::uint8_t>(
        ledgrid::ReceiverCommand::StatusQuery);
    const auto packet = aligned_packet(semantic);
    TEST_ASSERT_EQUAL_UINT32(test_case.wire_size, packet.size());

    ledgrid::ReceiverPacketPayload decoded{};
    TEST_ASSERT_TRUE(ledgrid::decode_receiver_packet_payload(
        packet.data(), packet.size(), &decoded));
    TEST_ASSERT_EQUAL_UINT32(test_case.semantic_size, decoded.size);
    TEST_ASSERT_TRUE(ledgrid::valid_status_query(
        decoded.data, decoded.size, test_case.sparse_overlay,
        test_case.installation_profile, test_case.native_modules));
  }
}

void test_aligned_envelope_rejects_bad_crc_version_length_padding_and_alignment() {
  const std::vector<std::uint8_t> semantic = {
      static_cast<std::uint8_t>(ledgrid::ReceiverCommand::Show)};
  const auto canonical = aligned_packet(semantic);
  ledgrid::ReceiverPacketPayload decoded{};

  auto bad_crc = canonical;
  bad_crc[4] ^= 0x01;
  TEST_ASSERT_FALSE(ledgrid::decode_receiver_packet_payload(
      bad_crc.data(), bad_crc.size(), &decoded));

  auto bad_version = canonical;
  bad_version[1] = ledgrid::kAlignedEnvelopeVersion + 1U;
  write_packet_crc(&bad_version);
  TEST_ASSERT_FALSE(ledgrid::decode_receiver_packet_payload(
      bad_version.data(), bad_version.size(), &decoded));

  auto bad_length = canonical;
  bad_length[3] = 3;
  write_packet_crc(&bad_length);
  TEST_ASSERT_FALSE(ledgrid::decode_receiver_packet_payload(
      bad_length.data(), bad_length.size(), &decoded));

  auto bad_padding = canonical;
  bad_padding[5] = 0x7E;
  write_packet_crc(&bad_padding);
  TEST_ASSERT_FALSE(ledgrid::decode_receiver_packet_payload(
      bad_padding.data(), bad_padding.size(), &decoded));

  std::vector<std::uint8_t> unaligned = {
      static_cast<std::uint8_t>(ledgrid::ReceiverCommand::AlignedEnvelope),
      ledgrid::kAlignedEnvelopeVersion, 0, 1,
      static_cast<std::uint8_t>(ledgrid::ReceiverCommand::Show), 0, 0};
  write_packet_crc(&unaligned);
  TEST_ASSERT_TRUE(ledgrid::receiver_packet_crc_valid(
      unaligned.data(), unaligned.size()));
  TEST_ASSERT_FALSE(ledgrid::decode_receiver_packet_payload(
      unaligned.data(), unaligned.size(), &decoded));
}

void test_fec_envelope_golden_layout_and_exact_installed_sizes() {
  const std::vector<std::uint8_t> show = {
      static_cast<std::uint8_t>(ledgrid::ReceiverCommand::Show)};
  const auto golden = fec_packet(show);
  TEST_ASSERT_EQUAL_UINT32(248, golden.size());
  const std::uint8_t expected_prefix[] = {
      0x0B, 0x07, 0x00, 0x08};
  TEST_ASSERT_EQUAL_MEMORY(expected_prefix, golden.data(), sizeof(expected_prefix));
  TEST_ASSERT_EQUAL_MEMORY(
      expected_prefix, golden.data() + golden.size() - sizeof(expected_prefix),
      sizeof(expected_prefix));
  const auto legacy_v2 = fec_v2_packet(show);
  std::array<std::uint8_t, ledgrid::kFecScratchBytes> legacy_scratch{};
  ledgrid::ReceiverPacketPayload legacy_decoded{};
  ledgrid::ReceiverPacketDecodeReport legacy_report{};
  TEST_ASSERT_TRUE(ledgrid::decode_receiver_packet_payload(
      legacy_v2.data(), legacy_v2.size(), &legacy_decoded, &legacy_report,
      legacy_scratch.data(), legacy_scratch.size()));
  TEST_ASSERT_TRUE(legacy_report.fec_envelope_attempted);
  TEST_ASSERT_EQUAL_MEMORY(show.data(), legacy_decoded.data, show.size());

  const auto legacy_v5 = fec_v5_packet(show);
  TEST_ASSERT_TRUE(ledgrid::decode_receiver_packet_payload(
      legacy_v5.data(), legacy_v5.size(), &legacy_decoded, &legacy_report,
      legacy_scratch.data(), legacy_scratch.size()));
  TEST_ASSERT_TRUE(legacy_report.fec_envelope_attempted);
  TEST_ASSERT_EQUAL_MEMORY(show.data(), legacy_decoded.data, show.size());

  const auto legacy_v6 = fec_v6_packet(show);
  TEST_ASSERT_TRUE(ledgrid::decode_receiver_packet_payload(
      legacy_v6.data(), legacy_v6.size(), &legacy_decoded, &legacy_report,
      legacy_scratch.data(), legacy_scratch.size()));
  TEST_ASSERT_TRUE(legacy_report.fec_envelope_attempted);
  TEST_ASSERT_EQUAL_MEMORY(show.data(), legacy_decoded.data, show.size());

  const auto legacy_v4 = fec_v4_packet(show);
  TEST_ASSERT_TRUE(ledgrid::decode_receiver_packet_payload(
      legacy_v4.data(), legacy_v4.size(), &legacy_decoded, &legacy_report,
      legacy_scratch.data(), legacy_scratch.size()));
  TEST_ASSERT_TRUE(legacy_report.fec_envelope_attempted);
  TEST_ASSERT_EQUAL_MEMORY(show.data(), legacy_decoded.data, show.size());

  const auto legacy_v3 = fec_v3_packet(show);
  TEST_ASSERT_TRUE(ledgrid::decode_receiver_packet_payload(
      legacy_v3.data(), legacy_v3.size(), &legacy_decoded, &legacy_report,
      legacy_scratch.data(), legacy_scratch.size()));
  TEST_ASSERT_TRUE(legacy_report.fec_envelope_attempted);
  TEST_ASSERT_EQUAL_MEMORY(show.data(), legacy_decoded.data, show.size());

  std::vector<std::uint8_t> broad(1U + 8U * 138U * 3U, 0x5A);
  broad[0] = static_cast<std::uint8_t>(ledgrid::ReceiverCommand::SetAll);
  TEST_ASSERT_EQUAL_UINT32(4088, fec_packet(broad).size());
  std::vector<std::uint8_t> tail(1U + 138U * 3U, 0xA5);
  tail[0] = static_cast<std::uint8_t>(ledgrid::ReceiverCommand::SetAll);
  TEST_ASSERT_EQUAL_UINT32(728, fec_packet(tail).size());

  std::vector<std::uint8_t> maximum(
      ledgrid::kFecEnvelopeMaxSemanticBytes, 0x33);
  maximum[0] = static_cast<std::uint8_t>(
      ledgrid::ReceiverCommand::NativeModuleChunk);
  const auto maximum_packet = fec_packet(maximum);
  TEST_ASSERT_EQUAL_UINT32(4088, maximum_packet.size());
  TEST_ASSERT_LESS_OR_EQUAL_UINT32(
      ledgrid::kAnimationPipelineMaxTransactionBytes,
      maximum_packet.size());
  std::array<std::uint8_t, ledgrid::kFecScratchBytes> scratch{};
  ledgrid::ReceiverPacketPayload decoded{};
  ledgrid::ReceiverPacketDecodeReport report{};
  TEST_ASSERT_TRUE(ledgrid::decode_receiver_packet_payload(
      maximum_packet.data(), maximum_packet.size(), &decoded, &report,
      scratch.data(), scratch.size()));
  TEST_ASSERT_EQUAL_UINT32(maximum.size(), decoded.size);
}

void test_fec_corrects_header_payload_crc_and_distinct_codeword_bits() {
  std::vector<std::uint8_t> semantic(260, 0);
  semantic[0] = static_cast<std::uint8_t>(ledgrid::ReceiverCommand::SetAll);
  for (std::size_t index = 1; index < semantic.size(); ++index) {
    semantic[index] = static_cast<std::uint8_t>(index * 37U);
  }
  const auto canonical = fec_packet(semantic);
  std::array<std::uint8_t, ledgrid::kFecScratchBytes> scratch{};
  const std::size_t codewords =
      (canonical.size() - ledgrid::kFecWireHeaderBytes) /
      ledgrid::kFecCodewordBytes;
  const std::size_t matrix = ledgrid::kFecEnvelopeHeaderBytes;
  for (const std::pair<std::size_t, std::size_t> location : {
           std::pair<std::size_t, std::size_t>{0U, 0U},
           {1U, 0U}, {4U, 3U}, {8U, 9U}, {15U, codewords - 1U}}) {
    auto damaged = canonical;
    damaged[fec_rs_wire_offset(
        matrix, location.first, location.second, codewords)] ^= 0xA5U;
    ledgrid::ReceiverPacketPayload decoded{};
    ledgrid::ReceiverPacketDecodeReport report{};
    TEST_ASSERT_TRUE(ledgrid::decode_receiver_packet_payload(
        damaged.data(), damaged.size(), &decoded, &report,
        scratch.data(), scratch.size()));
    TEST_ASSERT_TRUE(report.fec_envelope_attempted);
    TEST_ASSERT_EQUAL_UINT16(1, report.corrected_codewords);
    TEST_ASSERT_EQUAL_UINT16(4, report.corrected_bits);
    TEST_ASSERT_TRUE(decoded.fec_envelope);
    TEST_ASSERT_EQUAL_UINT32(semantic.size(), decoded.size);
    TEST_ASSERT_EQUAL_MEMORY(semantic.data(), decoded.data, semantic.size());
  }

  // The inner-v1 CRC occupies protected offsets 268/269 for this semantic
  // length and remains correctable after the byte interleave.
  auto crc_damaged = canonical;
  const std::size_t crc_protected_offset = 268U;
  crc_damaged[fec_rs_wire_offset(
      matrix,
      crc_protected_offset % ledgrid::kFecDataBytes,
      crc_protected_offset / ledgrid::kFecDataBytes,
      codewords)] ^= 0x3CU;
  ledgrid::ReceiverPacketPayload crc_decoded{};
  ledgrid::ReceiverPacketDecodeReport crc_report{};
  TEST_ASSERT_TRUE(ledgrid::decode_receiver_packet_payload(
      crc_damaged.data(), crc_damaged.size(), &crc_decoded, &crc_report,
      scratch.data(), scratch.size()));
  TEST_ASSERT_EQUAL_UINT16(1, crc_report.corrected_codewords);

  auto two_blocks = canonical;
  two_blocks[fec_rs_wire_offset(matrix, 2U, 1U, codewords)] ^= 0x01U;
  two_blocks[fec_rs_wire_offset(matrix, 7U, 2U, codewords)] ^= 0x02U;
  ledgrid::ReceiverPacketPayload two_decoded{};
  ledgrid::ReceiverPacketDecodeReport two_report{};
  TEST_ASSERT_TRUE(ledgrid::decode_receiver_packet_payload(
      two_blocks.data(), two_blocks.size(), &two_decoded, &two_report,
      scratch.data(), scratch.size()));
  TEST_ASSERT_EQUAL_UINT16(2, two_report.corrected_codewords);
  TEST_ASSERT_EQUAL_MEMORY(semantic.data(), two_decoded.data, semantic.size());

  // Every parity-symbol class can be damaged without changing the protected
  // systematic payload. The semantic-CRC fast path accepts these without
  // spending time on an unnecessary Reed-Solomon repair.
  for (std::size_t parity_symbol = 0;
       parity_symbol < ledgrid::kFecParityBytes; ++parity_symbol) {
    auto parity_damaged = canonical;
    parity_damaged[fec_rs_wire_offset(
        matrix, ledgrid::kFecDataBytes + parity_symbol, 3U, codewords)] ^=
        0xD3U;
    ledgrid::ReceiverPacketPayload parity_decoded{};
    ledgrid::ReceiverPacketDecodeReport parity_report{};
    TEST_ASSERT_TRUE(ledgrid::decode_receiver_packet_payload(
        parity_damaged.data(), parity_damaged.size(), &parity_decoded,
        &parity_report, scratch.data(), scratch.size()));
    TEST_ASSERT_EQUAL_UINT16(0, parity_report.corrected_codewords);
    TEST_ASSERT_EQUAL_MEMORY(
        semantic.data(), parity_decoded.data, semantic.size());
  }

  // A contiguous installed-link burst spanning nine complete interleave
  // rows maps to nine consecutive bytes in every codeword. The bounded
  // erasure fallback validates the remaining syndrome before repair.
  auto burst = canonical;
  const std::size_t burst_start = matrix;
  for (std::size_t offset = 0; offset < 9U * codewords; ++offset) {
    burst[burst_start + offset] ^= 0xA5U;
  }
  ledgrid::ReceiverPacketPayload burst_decoded{};
  ledgrid::ReceiverPacketDecodeReport burst_report{};
  TEST_ASSERT_TRUE(ledgrid::decode_receiver_packet_payload(
      burst.data(), burst.size(), &burst_decoded, &burst_report,
      scratch.data(), scratch.size()));
  TEST_ASSERT_EQUAL_UINT16(codewords, burst_report.corrected_codewords);
  TEST_ASSERT_EQUAL_UINT16(codewords * 36U, burst_report.corrected_bits);
  TEST_ASSERT_EQUAL_MEMORY(semantic.data(), burst_decoded.data, semantic.size());

  // A longer contiguous burst gives three codewords ten errors and leaves
  // the rest independently validated at nine. The ten-error run is accepted
  // only because exactly one span restores the canonical whole-frame CRC.
  auto maximum_burst = canonical;
  constexpr std::size_t kMaximumExtraBytes = 3U;
  for (std::size_t offset = 0;
       offset < 9U * codewords + kMaximumExtraBytes; ++offset) {
    maximum_burst[burst_start + offset] ^= 0xA5U;
  }
  ledgrid::ReceiverPacketPayload maximum_burst_decoded{};
  ledgrid::ReceiverPacketDecodeReport maximum_burst_report{};
  TEST_ASSERT_TRUE(ledgrid::decode_receiver_packet_payload(
      maximum_burst.data(), maximum_burst.size(),
      &maximum_burst_decoded, &maximum_burst_report,
      scratch.data(), scratch.size()));
  TEST_ASSERT_EQUAL_UINT16(codewords, maximum_burst_report.corrected_codewords);
  TEST_ASSERT_EQUAL_UINT16(
      (9U * codewords + kMaximumExtraBytes) * 4U,
      maximum_burst_report.corrected_bits);
  TEST_ASSERT_EQUAL_MEMORY(
      semantic.data(), maximum_burst_decoded.data, semantic.size());

  // A longer repeated-bit electrical burst has only three unknowns: start,
  // length, and XOR value. All ten syndromes uniquely identify this interval.
  auto uniform_long_burst = canonical;
  for (std::size_t offset = 0; offset < 11U * codewords; ++offset) {
    uniform_long_burst[matrix + offset] ^= 0xA5U;
  }
  ledgrid::ReceiverPacketPayload uniform_long_decoded{};
  ledgrid::ReceiverPacketDecodeReport uniform_long_report{};
  TEST_ASSERT_TRUE(ledgrid::decode_receiver_packet_payload(
      uniform_long_burst.data(), uniform_long_burst.size(),
      &uniform_long_decoded, &uniform_long_report,
      scratch.data(), scratch.size()));
  TEST_ASSERT_EQUAL_UINT16(codewords, uniform_long_report.corrected_codewords);
  TEST_ASSERT_EQUAL_UINT16(
      11U * codewords * 4U, uniform_long_report.corrected_bits);
  TEST_ASSERT_EQUAL_MEMORY(
      semantic.data(), uniform_long_decoded.data, semantic.size());

  // The same eleven-column span with varying error magnitudes exceeds every
  // bounded recovery shape and remains terminal.
  auto over_radius_burst = canonical;
  for (std::size_t symbol = 0; symbol < 11U; ++symbol) {
    const std::uint8_t error = static_cast<std::uint8_t>(
        0x11U + symbol * 13U);
    for (std::size_t block = 0; block < codewords; ++block) {
      over_radius_burst[
          fec_rs_wire_offset(matrix, symbol, block, codewords)] ^= error;
    }
  }
  ledgrid::ReceiverPacketPayload over_radius_decoded{};
  ledgrid::ReceiverPacketDecodeReport over_radius_report{};
  TEST_ASSERT_FALSE(ledgrid::decode_receiver_packet_payload(
      over_radius_burst.data(), over_radius_burst.size(),
      &over_radius_decoded, &over_radius_report,
      scratch.data(), scratch.size()));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(
          ledgrid::ReceiverPacketDecodeResult::FecUncorrectable),
      static_cast<std::uint8_t>(over_radius_report.result));

  // Either separated raw discriminator can carry attribution by itself.
  const std::array<std::size_t, 2> marker_offsets = {
      0U, canonical.size() - 4U};
  for (const std::size_t marker : marker_offsets) {
    auto marker_damaged = canonical;
    marker_damaged[marker] ^= 0x07U;
    ledgrid::ReceiverPacketPayload marker_decoded{};
    ledgrid::ReceiverPacketDecodeReport marker_report{};
    TEST_ASSERT_TRUE(ledgrid::decode_receiver_packet_payload(
        marker_damaged.data(), marker_damaged.size(), &marker_decoded,
        &marker_report, scratch.data(), scratch.size()));
    TEST_ASSERT_TRUE(marker_report.fec_envelope_attempted);
    TEST_ASSERT_EQUAL_MEMORY(
        semantic.data(), marker_decoded.data, semantic.size());
  }
}

void test_fec_outer_parity_recovers_one_terminal_codeword_and_two_fail_closed() {
  std::vector<std::uint8_t> semantic(1U + 128U * 3U, 0x5A);
  semantic[0] = static_cast<std::uint8_t>(ledgrid::ReceiverCommand::SetAll);
  const auto canonical = fec_packet(semantic);
  std::array<std::uint8_t, ledgrid::kFecScratchBytes> scratch{};
  std::vector<std::uint8_t> working(semantic.size() - 1U, 0xA7);
  const auto prior = working;
  const std::size_t codewords =
      (canonical.size() - ledgrid::kFecWireHeaderBytes) /
      ledgrid::kFecCodewordBytes;
  const std::size_t matrix = ledgrid::kFecEnvelopeHeaderBytes;

  auto single = canonical;
  single[matrix] ^= 0xA5U;
  ledgrid::ReceiverPacketPayload decoded{};
  ledgrid::ReceiverPacketDecodeReport report{};
  TEST_ASSERT_TRUE(ledgrid::decode_receiver_packet_payload(
      single.data(), single.size(), &decoded, &report,
      scratch.data(), scratch.size()));
  const auto decision = ledgrid::classify_receiver_dispatch(
      decoded.data, decoded.size, working.size(), ledgrid::BaseMode::HostFullScene,
      false);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::ReceiverDispatchRoute::HostFullFrame),
      static_cast<std::uint8_t>(decision.route));
  std::copy(decoded.data + 1U, decoded.data + decoded.size, working.begin());
  TEST_ASSERT_EQUAL_MEMORY(semantic.data() + 1U, working.data(), working.size());

  working = prior;
  auto beyond_radius = canonical;
  const std::uint8_t errors[] = {0xA5U, 0x3CU, 0x81U, 0x5AU, 0xC3U, 0x7EU};
  const std::size_t symbols[] = {0U, 10U, 20U, 30U, 40U, 50U};
  for (std::size_t index = 0; index < std::size(errors); ++index) {
    beyond_radius[
        fec_rs_wire_offset(matrix, symbols[index], 0U, codewords)] ^=
        errors[index];
  }
  TEST_ASSERT_TRUE(ledgrid::decode_receiver_packet_payload(
      beyond_radius.data(), beyond_radius.size(), &decoded, &report,
      scratch.data(), scratch.size()));
  TEST_ASSERT_TRUE(report.fec_envelope_attempted);
  TEST_ASSERT_EQUAL_UINT16(1, report.corrected_codewords);
  TEST_ASSERT_EQUAL_MEMORY(semantic.data(), decoded.data, semantic.size());

  const auto legacy_v6 = fec_v6_packet(semantic);
  auto legacy_beyond_radius = legacy_v6;
  const std::size_t legacy_codewords =
      (legacy_v6.size() - ledgrid::kFecWireHeaderBytes) /
      ledgrid::kFecCodewordBytes;
  for (std::size_t index = 0; index < std::size(errors); ++index) {
    legacy_beyond_radius[
        fec_rs_wire_offset(matrix, symbols[index], 0U, legacy_codewords)] ^=
        errors[index];
  }
  TEST_ASSERT_FALSE(ledgrid::decode_receiver_packet_payload(
      legacy_beyond_radius.data(), legacy_beyond_radius.size(), &decoded,
      &report, scratch.data(), scratch.size()));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(
          ledgrid::ReceiverPacketDecodeResult::FecUncorrectable),
      static_cast<std::uint8_t>(report.result));

  auto two_terminal_blocks = canonical;
  for (const std::size_t block : {0U, 1U}) {
    for (std::size_t index = 0; index < std::size(errors); ++index) {
      two_terminal_blocks[
          fec_rs_wire_offset(matrix, symbols[index], block, codewords)] ^=
          errors[index];
    }
  }
  TEST_ASSERT_FALSE(ledgrid::decode_receiver_packet_payload(
      two_terminal_blocks.data(), two_terminal_blocks.size(), &decoded,
      &report, scratch.data(), scratch.size()));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(
          ledgrid::ReceiverPacketDecodeResult::FecUncorrectable),
      static_cast<std::uint8_t>(report.result));
  TEST_ASSERT_EQUAL_MEMORY(prior.data(), working.data(), working.size());
}

void test_fec_malformed_multisymbol_crc_padding_and_shape_fail_closed() {
  std::vector<std::uint8_t> semantic(260, 0x6C);
  semantic[0] = static_cast<std::uint8_t>(ledgrid::ReceiverCommand::SetAll);
  const auto canonical = fec_packet(semantic);
  std::array<std::uint8_t, ledgrid::kFecScratchBytes> scratch{};
  ledgrid::ReceiverPacketPayload decoded{};
  ledgrid::ReceiverPacketDecodeReport report{};
  const std::size_t codewords =
      (canonical.size() - ledgrid::kFecWireHeaderBytes) /
      ledgrid::kFecCodewordBytes;
  const std::size_t matrix = ledgrid::kFecEnvelopeHeaderBytes;

  auto beyond_radius = canonical;
  const std::uint8_t errors[] = {0xA5U, 0x3CU, 0x81U, 0x5AU, 0xC3U, 0x7EU};
  const std::size_t symbols[] = {0U, 10U, 20U, 30U, 40U, 50U};
  for (const std::size_t block : {0U, 1U}) {
    for (std::size_t index = 0; index < std::size(errors); ++index) {
      beyond_radius[
          fec_rs_wire_offset(matrix, symbols[index], block, codewords)] ^=
          errors[index];
    }
  }
  TEST_ASSERT_FALSE(ledgrid::decode_receiver_packet_payload(
      beyond_radius.data(), beyond_radius.size(), &decoded, &report,
      scratch.data(), scratch.size()));
  TEST_ASSERT_TRUE(report.fec_envelope_attempted);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(
          ledgrid::ReceiverPacketDecodeResult::FecUncorrectable),
      static_cast<std::uint8_t>(report.result));

  // A coordinated minimum-distance mutation can form another valid codeword,
  // but the canonical inner CRC remains the end-to-end semantic authority.
  auto crc_corrupt = canonical;
  const std::size_t block = 1U;
  auto alternate_semantic = semantic;
  alternate_semantic[71] ^= 0x7BU;
  const auto alternate_packet = fec_packet(alternate_semantic);
  for (std::size_t symbol = 0;
       symbol < ledgrid::kFecCodewordBytes; ++symbol) {
    const std::size_t offset =
        fec_rs_wire_offset(matrix, symbol, block, codewords);
    crc_corrupt[offset] = alternate_packet[offset];
  }
  TEST_ASSERT_FALSE(ledgrid::decode_receiver_packet_payload(
      crc_corrupt.data(), crc_corrupt.size(), &decoded, &report,
      scratch.data(), scratch.size()));
  TEST_ASSERT_TRUE(report.fec_envelope_attempted);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(
          ledgrid::ReceiverPacketDecodeResult::FecSemanticCrcError),
      static_cast<std::uint8_t>(report.result));

  // The same internally consistent mutation in canonical zero padding is a
  // framing failure and never reaches the inner command decoder.
  auto padding_corrupt = canonical;
  std::vector<std::uint8_t> padding_semantic(300U, 0x7BU);
  padding_semantic[0] = static_cast<std::uint8_t>(
      ledgrid::ReceiverCommand::SetAll);
  const auto padding_source = fec_packet(padding_semantic);
  const std::size_t padding_block = codewords - 2U;
  for (std::size_t symbol = 0;
       symbol < ledgrid::kFecCodewordBytes; ++symbol) {
    const std::size_t offset =
        fec_rs_wire_offset(matrix, symbol, padding_block, codewords);
    padding_corrupt[offset] = padding_source[offset];
  }
  TEST_ASSERT_FALSE(ledgrid::decode_receiver_packet_payload(
      padding_corrupt.data(), padding_corrupt.size(), &decoded, &report,
      scratch.data(), scratch.size()));
  TEST_ASSERT_TRUE(report.fec_envelope_attempted);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(
          ledgrid::ReceiverPacketDecodeResult::InvalidFraming),
      static_cast<std::uint8_t>(report.result));

  auto unattributed_marker = canonical;
  unattributed_marker[0] ^= 0x03U;
  unattributed_marker[unattributed_marker.size() - 4U] ^= 0x03U;
  TEST_ASSERT_FALSE(ledgrid::decode_receiver_packet_payload(
      unattributed_marker.data(), unattributed_marker.size(), &decoded, &report,
      scratch.data(), scratch.size()));
  TEST_ASSERT_FALSE(report.fec_envelope_attempted);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::ReceiverPacketDecodeResult::CrcError),
      static_cast<std::uint8_t>(report.result));

  auto truncated = canonical;
  truncated.resize(truncated.size() - ledgrid::kFecCodewordBytes);
  TEST_ASSERT_FALSE(ledgrid::decode_receiver_packet_payload(
      truncated.data(), truncated.size(), &decoded, &report,
      scratch.data(), scratch.size()));
  TEST_ASSERT_FALSE(report.fec_envelope_attempted);
}

void test_status_v7_preserves_v6_and_encodes_exact_fec_counters() {
  ledgrid::ReceiverStatusV7 status{};
  status.capabilities = ledgrid::kCapabilityAlignedEnvelopeV1 |
                        ledgrid::kCapabilityFecEnvelopeV2 |
                        ledgrid::kCapabilityFecEnvelopeV3 |
                        ledgrid::kCapabilityFecEnvelopeV4 |
                        ledgrid::kCapabilityFecEnvelopeV5 |
                        ledgrid::kCapabilityFecEnvelopeV6 |
                        ledgrid::kCapabilityFecEnvelopeV7;
  status.fec_packets_received = 11;
  status.fec_packets_accepted = 7;
  status.fec_corrected_packets = 3;
  status.fec_corrected_codewords = 4;
  status.fec_uncorrectable_packets = 1;
  status.fec_semantic_crc_errors = 2;
  status.fec_framing_errors = 1;
  status.fec_last_decode_us = 83;
  status.fec_max_decode_us = 109;
  std::array<std::uint8_t, ledgrid::kStatusBytesV7> encoded{};
  TEST_ASSERT_TRUE(ledgrid::encode_receiver_status_v7(
      status, encoded.data(), encoded.size()));
  TEST_ASSERT_EQUAL_MEMORY("LGS7", encoded.data(), 4);
  TEST_ASSERT_EQUAL_UINT8(7, encoded[4]);
  TEST_ASSERT_EQUAL_UINT32(11, read_u32(encoded.data() + 1216));
  TEST_ASSERT_EQUAL_UINT32(7, read_u32(encoded.data() + 1220));
  TEST_ASSERT_EQUAL_UINT32(3, read_u32(encoded.data() + 1224));
  TEST_ASSERT_EQUAL_UINT32(4, read_u32(encoded.data() + 1228));
  TEST_ASSERT_EQUAL_UINT32(1, read_u32(encoded.data() + 1232));
  TEST_ASSERT_EQUAL_UINT32(2, read_u32(encoded.data() + 1236));
  TEST_ASSERT_EQUAL_UINT32(1, read_u32(encoded.data() + 1240));
  TEST_ASSERT_EQUAL_UINT16(83, read_u16(encoded.data() + 1244));
  TEST_ASSERT_EQUAL_UINT16(109, read_u16(encoded.data() + 1246));
  TEST_ASSERT_FALSE(ledgrid::encode_receiver_status_v7(
      status, encoded.data(), encoded.size() - 1U));
}

void test_fec_runtime_outcome_partition_is_total_and_exclusive() {
  ledgrid::ReceiverPacketDecodeReport report{};
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::ReceiverFecPacketOutcome::NotFec),
      static_cast<std::uint8_t>(
          ledgrid::receiver_fec_packet_outcome(false, report)));
  report.fec_envelope_attempted = true;
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::ReceiverFecPacketOutcome::Accepted),
      static_cast<std::uint8_t>(
          ledgrid::receiver_fec_packet_outcome(true, report)));
  const std::array<std::pair<ledgrid::ReceiverPacketDecodeResult,
                             ledgrid::ReceiverFecPacketOutcome>, 3> cases = {{
      {ledgrid::ReceiverPacketDecodeResult::FecUncorrectable,
       ledgrid::ReceiverFecPacketOutcome::Uncorrectable},
      {ledgrid::ReceiverPacketDecodeResult::FecSemanticCrcError,
       ledgrid::ReceiverFecPacketOutcome::SemanticCrcError},
      {ledgrid::ReceiverPacketDecodeResult::InvalidFraming,
       ledgrid::ReceiverFecPacketOutcome::FramingError},
  }};
  for (const auto& test_case : cases) {
    report.result = test_case.first;
    TEST_ASSERT_EQUAL_UINT8(
        static_cast<std::uint8_t>(test_case.second),
        static_cast<std::uint8_t>(
            ledgrid::receiver_fec_packet_outcome(false, report)));
  }
}

void test_fec_native_decode_benchmark_installed_frame() {
  std::vector<std::uint8_t> semantic(1U + 8U * 138U * 3U, 0x5A);
  semantic[0] = static_cast<std::uint8_t>(ledgrid::ReceiverCommand::SetAll);
  const auto packet = fec_packet(semantic);
  std::array<std::uint8_t, ledgrid::kFecScratchBytes> scratch{};
  constexpr std::size_t kIterations = 2000;
  volatile std::size_t checksum = 0;
  bool all_decoded = true;
  const auto started = std::chrono::steady_clock::now();
  for (std::size_t iteration = 0; iteration < kIterations; ++iteration) {
    ledgrid::ReceiverPacketPayload decoded{};
    ledgrid::ReceiverPacketDecodeReport report{};
    all_decoded = ledgrid::decode_receiver_packet_payload(
        packet.data(), packet.size(), &decoded, &report,
        scratch.data(), scratch.size()) && all_decoded;
    checksum += decoded.size + report.corrected_bits;
  }
  const auto elapsed = std::chrono::duration_cast<std::chrono::nanoseconds>(
      std::chrono::steady_clock::now() - started).count();
  const double mean_us = static_cast<double>(elapsed) /
      static_cast<double>(kIterations) / 1000.0;
  std::printf("[FEC_BENCH] native 4088-byte decode %.3f us/frame (%zu frames)\n",
              mean_us, kIterations);
  TEST_ASSERT_TRUE(all_decoded);
  TEST_ASSERT_EQUAL_UINT32(semantic.size() * kIterations, checksum);
}

}  // namespace

void setUp() {}
void tearDown() {}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_encoder_emits_parallel_grb_waveform);
  RUN_TEST(test_encoder_scales_brightness_before_bit_expansion);
  RUN_TEST(test_encoder_refreshes_cached_expansion_when_brightness_changes);
  RUN_TEST(test_optimized_encoder_updates_all_eight_lanes);
  RUN_TEST(test_lane_mask_silences_unselected_lanes);
  RUN_TEST(test_lane_mask_defaults_to_every_lane);
  RUN_TEST(test_compact_strip_can_target_an_independent_physical_lane);
  RUN_TEST(test_stagger_phase_lanes_partitions_every_lane);
  RUN_TEST(test_compact_single_strip_round_trips_on_every_staggered_lane);
  RUN_TEST(test_compact_single_strip_broadcasts_to_every_selected_lane);
  RUN_TEST(test_stagger_off_reproduces_the_original_waveform);
  RUN_TEST(test_stagger_caps_coincident_rising_edges_at_three);
  RUN_TEST(test_stagger_round_trips_every_lane);
  RUN_TEST(test_stagger_writes_stay_inside_the_encoded_buffer);
  RUN_TEST(test_encoder_appends_300us_reset_and_rejects_bad_bounds);
  RUN_TEST(test_startup_rainbow_is_45_degrees_and_moves_up_right);
  RUN_TEST(test_startup_rainbow_cycles_once_per_second_and_checks_bounds);
  RUN_TEST(test_mailbox_replaces_only_unread_ready_frames);
  RUN_TEST(test_status_v2_layout_is_stable);
  RUN_TEST(test_aligned_envelope_decodes_exact_semantic_payload_and_legacy_packets);
  RUN_TEST(test_aligned_envelope_accepts_frame_and_exact_maximum_semantic_sizes);
  RUN_TEST(test_aligned_envelope_preserves_full_status_query_semantics);
  RUN_TEST(test_aligned_envelope_rejects_bad_crc_version_length_padding_and_alignment);
  RUN_TEST(test_fec_envelope_golden_layout_and_exact_installed_sizes);
  RUN_TEST(test_fec_corrects_header_payload_crc_and_distinct_codeword_bits);
  RUN_TEST(
      test_fec_outer_parity_recovers_one_terminal_codeword_and_two_fail_closed);
  RUN_TEST(test_fec_malformed_multisymbol_crc_padding_and_shape_fail_closed);
  RUN_TEST(test_status_v7_preserves_v6_and_encodes_exact_fec_counters);
  RUN_TEST(test_fec_runtime_outcome_partition_is_total_and_exclusive);
  RUN_TEST(test_fec_native_decode_benchmark_installed_frame);
  return UNITY_END();
}
