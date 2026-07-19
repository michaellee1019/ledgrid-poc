#include "ledgrid/ws2812_encoder.hpp"

#include <array>
#include <cstring>

namespace ledgrid {
namespace {

std::uint8_t scale_channel(std::uint8_t value, std::uint8_t brightness) {
  return static_cast<std::uint8_t>(
      (static_cast<std::uint16_t>(value) * brightness + 127U) / 255U);
}

constexpr std::uint64_t expand_byte(std::uint8_t value) {
  std::uint64_t expanded = 0;
  for (std::uint8_t output_bit = 0; output_bit < 8; ++output_bit) {
    if ((value & (1U << (7U - output_bit))) != 0) {
      expanded |= std::uint64_t{1} << (output_bit * 8U);
    }
  }
  return expanded;
}

constexpr std::array<std::uint64_t, 256> make_expand_table() {
  std::array<std::uint64_t, 256> table{};
  for (std::size_t value = 0; value < table.size(); ++value) {
    table[value] = expand_byte(static_cast<std::uint8_t>(value));
  }
  return table;
}

constexpr auto kExpandTable = make_expand_table();

}  // namespace

std::size_t ws2812_reset_samples(
    std::uint16_t reset_us,
    std::uint32_t sample_rate_hz) {
  if (sample_rate_hz == 0) return 0;
  const std::uint64_t scaled =
      static_cast<std::uint64_t>(reset_us) * sample_rate_hz;
  return static_cast<std::size_t>((scaled + 999999ULL) / 1000000ULL);
}

std::size_t ws2812_encoded_size(
    std::uint16_t leds_per_strip,
    std::uint16_t reset_us,
    std::uint32_t sample_rate_hz) {
  constexpr std::size_t kSamplesPerRgbPixel = 3U * 8U * 3U;
  return static_cast<std::size_t>(leds_per_strip) * kSamplesPerRgbPixel +
         ws2812_reset_samples(reset_us, sample_rate_hz);
}

EncodeResult encode_parallel_grb(
    const std::uint8_t* rgb,
    std::size_t rgb_bytes,
    std::uint8_t strip_count,
    std::uint16_t leds_per_strip,
    std::uint8_t brightness,
    std::uint8_t* output,
    std::size_t output_capacity,
    std::uint16_t reset_us,
    std::uint32_t sample_rate_hz) {
  if (!initialize_parallel_grb_waveform(
          strip_count,
          leds_per_strip,
          output,
          output_capacity,
          reset_us,
          sample_rate_hz)) {
    return {};
  }
  return encode_parallel_grb_pixels(
      rgb,
      rgb_bytes,
      strip_count,
      leds_per_strip,
      brightness,
      output,
      output_capacity,
      reset_us,
      sample_rate_hz);
}

bool initialize_parallel_grb_waveform(
    std::uint8_t strip_count,
    std::uint16_t leds_per_strip,
    std::uint8_t* output,
    std::size_t output_capacity,
    std::uint16_t reset_us,
    std::uint32_t sample_rate_hz) {
  if (output == nullptr || strip_count == 0 ||
      strip_count > kMaxParallelStrips || leds_per_strip == 0 ||
      sample_rate_hz == 0) {
    return false;
  }
  const std::size_t required_output =
      ws2812_encoded_size(leds_per_strip, reset_us, sample_rate_hz);
  if (required_output == 0 || output_capacity < required_output) return false;

  const std::uint8_t active_mask =
      strip_count == 8 ? 0xFFU
                       : static_cast<std::uint8_t>((1U << strip_count) - 1U);
  const std::size_t data_samples =
      static_cast<std::size_t>(leds_per_strip) * 3U * 8U * 3U;
  for (std::size_t sample = 0; sample < data_samples; sample += 3U) {
    output[sample] = active_mask;
    output[sample + 1U] = 0;
    output[sample + 2U] = 0;
  }
  std::memset(output + data_samples, 0, required_output - data_samples);
  return true;
}

EncodeResult encode_parallel_grb_pixels(
    const std::uint8_t* rgb,
    std::size_t rgb_bytes,
    std::uint8_t strip_count,
    std::uint16_t leds_per_strip,
    std::uint8_t brightness,
    std::uint8_t* output,
    std::size_t output_capacity,
    std::uint16_t reset_us,
    std::uint32_t sample_rate_hz) {
  if (rgb == nullptr || output == nullptr || strip_count == 0 ||
      strip_count > kMaxParallelStrips || leds_per_strip == 0 ||
      sample_rate_hz == 0) {
    return {};
  }

  const std::size_t required_rgb =
      static_cast<std::size_t>(strip_count) * leds_per_strip * 3U;
  const std::size_t required_output =
      ws2812_encoded_size(leds_per_strip, reset_us, sample_rate_hz);
  if (rgb_bytes < required_rgb || required_output == 0 ||
      output_capacity < required_output) {
    return {};
  }

  constexpr std::uint8_t kGrbOffsets[3] = {1, 0, 2};
  // Materialize the brightness-adjusted expansion table in internal RAM once
  // per frame. The inner loop then needs one fast lookup per lane rather than
  // a brightness lookup followed by a flash-resident 64-bit lookup.
  std::array<std::uint64_t, 256> frame_expand_table{};
  for (std::size_t value = 0; value < frame_expand_table.size(); ++value) {
    const auto channel = static_cast<std::uint8_t>(value);
    const auto scaled = brightness == 255
                            ? channel
                            : scale_channel(channel, brightness);
    frame_expand_table[value] = kExpandTable[scaled];
  }

  const std::size_t lane_stride = static_cast<std::size_t>(leds_per_strip) * 3U;
  std::uint8_t* dynamic_sample = output + 1U;

  for (std::uint16_t pixel = 0; pixel < leds_per_strip; ++pixel) {
    for (std::uint8_t channel = 0; channel < 3; ++channel) {
      const std::size_t offset =
          static_cast<std::size_t>(pixel) * 3U + kGrbOffsets[channel];
      std::uint64_t parallel_bits = frame_expand_table[rgb[offset]];
      if (strip_count == 8) {
        parallel_bits |= frame_expand_table[rgb[offset + lane_stride]] << 1U;
        parallel_bits |= frame_expand_table[rgb[offset + lane_stride * 2U]] << 2U;
        parallel_bits |= frame_expand_table[rgb[offset + lane_stride * 3U]] << 3U;
        parallel_bits |= frame_expand_table[rgb[offset + lane_stride * 4U]] << 4U;
        parallel_bits |= frame_expand_table[rgb[offset + lane_stride * 5U]] << 5U;
        parallel_bits |= frame_expand_table[rgb[offset + lane_stride * 6U]] << 6U;
        parallel_bits |= frame_expand_table[rgb[offset + lane_stride * 7U]] << 7U;
      } else {
        for (std::uint8_t lane = 1; lane < strip_count; ++lane) {
          parallel_bits |=
              frame_expand_table[rgb[offset + lane_stride * lane]] << lane;
        }
      }

      dynamic_sample[0] = static_cast<std::uint8_t>(parallel_bits);
      dynamic_sample[3] = static_cast<std::uint8_t>(parallel_bits >> 8U);
      dynamic_sample[6] = static_cast<std::uint8_t>(parallel_bits >> 16U);
      dynamic_sample[9] = static_cast<std::uint8_t>(parallel_bits >> 24U);
      dynamic_sample[12] = static_cast<std::uint8_t>(parallel_bits >> 32U);
      dynamic_sample[15] = static_cast<std::uint8_t>(parallel_bits >> 40U);
      dynamic_sample[18] = static_cast<std::uint8_t>(parallel_bits >> 48U);
      dynamic_sample[21] = static_cast<std::uint8_t>(parallel_bits >> 56U);
      dynamic_sample += 24U;
    }
  }

  return {true, required_output};
}

}  // namespace ledgrid
