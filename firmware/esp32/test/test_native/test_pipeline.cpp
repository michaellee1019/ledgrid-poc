#include <unity.h>

#include <algorithm>
#include <array>
#include <cstdint>
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

}  // namespace

void setUp() {}
void tearDown() {}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_encoder_emits_parallel_grb_waveform);
  RUN_TEST(test_encoder_scales_brightness_before_bit_expansion);
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
  return UNITY_END();
}
