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

// The receiver has one display owner and brightness changes only at a control
// boundary, while every submitted frame otherwise repeats the same 256-entry
// brightness expansion. Retaining that derived table removes 256 scales and
// 64-bit copies from the production frame hot path. A changed brightness value
// refreshes the whole table before any pixel uses it.
struct BrightnessExpandCache {
  std::array<std::uint64_t, 256> table{};
  std::uint8_t brightness = 0;
  bool valid = false;
};

BrightnessExpandCache brightness_expand_cache;

const std::array<std::uint64_t, 256>& brightness_expand_table(
    std::uint8_t brightness) {
  if (brightness_expand_cache.valid &&
      brightness_expand_cache.brightness == brightness) {
    return brightness_expand_cache.table;
  }
  for (std::size_t value = 0;
       value < brightness_expand_cache.table.size(); ++value) {
    const auto channel = static_cast<std::uint8_t>(value);
    const auto scaled = brightness == 255
                            ? channel
                            : scale_channel(channel, brightness);
    brightness_expand_cache.table[value] = kExpandTable[scaled];
  }
  brightness_expand_cache.brightness = brightness;
  brightness_expand_cache.valid = true;
  return brightness_expand_cache.table;
}

}  // namespace

std::uint8_t stagger_phase_lanes(
    std::uint8_t phase,
    std::uint8_t stagger_phases,
    std::uint8_t active_mask) {
  const std::uint8_t phases =
      (stagger_phases == 0 || stagger_phases > kMaxStaggerPhases)
          ? kStaggerOff
          : stagger_phases;
  if (phase >= phases) return 0;
  std::uint8_t lanes = 0;
  for (std::uint8_t lane = 0; lane < kMaxParallelStrips; ++lane) {
    if (lane % phases == phase) {
      lanes = static_cast<std::uint8_t>(lanes | (1U << lane));
    }
  }
  return static_cast<std::uint8_t>(lanes & active_mask);
}

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
    std::uint32_t sample_rate_hz,
    std::uint8_t lane_mask,
    std::uint8_t stagger_phases) {
  if (!initialize_parallel_grb_waveform(
          strip_count,
          leds_per_strip,
          output,
          output_capacity,
          reset_us,
          sample_rate_hz,
          lane_mask,
          stagger_phases)) {
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
      sample_rate_hz,
      lane_mask,
      stagger_phases);
}

bool initialize_parallel_grb_waveform(
    std::uint8_t strip_count,
    std::uint16_t leds_per_strip,
    std::uint8_t* output,
    std::size_t output_capacity,
    std::uint16_t reset_us,
    std::uint32_t sample_rate_hz,
    std::uint8_t lane_mask,
    std::uint8_t stagger_phases) {
  if (output == nullptr || strip_count == 0 ||
      strip_count > kMaxParallelStrips || leds_per_strip == 0 ||
      sample_rate_hz == 0) {
    return false;
  }
  const std::size_t required_output =
      ws2812_encoded_size(leds_per_strip, reset_us, sample_rate_hz);
  if (required_output == 0 || output_capacity < required_output) return false;

  const std::uint8_t strip_mask =
      strip_count == 8 ? 0xFFU
                       : static_cast<std::uint8_t>((1U << strip_count) - 1U);
  const std::uint8_t active_mask =
      static_cast<std::uint8_t>(strip_mask & lane_mask);
  const std::size_t data_samples =
      static_cast<std::size_t>(leds_per_strip) * 3U * 8U * 3U;

  // Sample s carries the leading high edge for whichever lanes sit at phase
  // (s % kSamplesPerBit). With staggering off that is every active lane on
  // every third sample, which is the original all-lanes-together waveform.
  std::uint8_t phase_lanes[kSamplesPerBit];
  for (std::uint8_t phase = 0; phase < kSamplesPerBit; ++phase) {
    phase_lanes[phase] =
        stagger_phase_lanes(phase, stagger_phases, active_mask);
  }
  for (std::size_t sample = 0; sample < data_samples; sample += kSamplesPerBit) {
    output[sample] = phase_lanes[0];
    output[sample + 1U] = phase_lanes[1];
    output[sample + 2U] = phase_lanes[2];
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
    std::uint32_t sample_rate_hz,
    std::uint8_t lane_mask,
    std::uint8_t stagger_phases,
    bool map_compact_strips_to_selected_lanes) {
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
  // The inner loop needs one internal-RAM lookup per lane rather than a
  // brightness scale followed by a flash-resident 64-bit lookup.
  const auto& frame_expand_table = brightness_expand_table(brightness);

  const std::size_t lane_stride = static_cast<std::size_t>(leds_per_strip) * 3U;
  // Each of the eight bytes packed into parallel_bits holds one lane bit per
  // position, so replicating the mask into every byte silences a lane across
  // all eight bits of the channel at once.
  const std::uint64_t lane_bits = 0x0101010101010101ULL * lane_mask;

  std::uint8_t selected_lane_count = 0;
  for (std::uint8_t bits = lane_mask; bits != 0; bits >>= 1U) {
    selected_lane_count += bits & 1U;
  }
  // A compact one-strip receiver has only one semantic RGB source. Allow its
  // physical mask to name one or many candidate outputs so installations with
  // an unresolved tail-lane wire can safely broadcast that same strip without
  // pretending the receiver owns additional logical/global strips.
  const bool broadcast_single_strip = map_compact_strips_to_selected_lanes &&
      strip_count == 1 && selected_lane_count != 0;
  const bool compact_mapping = map_compact_strips_to_selected_lanes &&
      strip_count < kMaxParallelStrips && selected_lane_count == strip_count;

  const std::uint8_t strip_mask =
      strip_count == 8 ? 0xFFU
                       : static_cast<std::uint8_t>((1U << strip_count) - 1U);
  const std::uint8_t active_mask = broadcast_single_strip || compact_mapping
      ? lane_mask : static_cast<std::uint8_t>(strip_mask & lane_mask);
  const std::uint8_t phases =
      (stagger_phases == 0 || stagger_phases > kMaxStaggerPhases)
          ? kStaggerOff
          : stagger_phases;
  // A lane at phase p puts its data sample at p+1 within the symbol, so the
  // byte at that offset also carries the leading high edge of the lanes one
  // phase later. Both halves are disjoint lane sets, so the write stays a
  // plain store rather than a read-modify-write.
  std::uint8_t phase_lanes[kSamplesPerBit];
  for (std::uint8_t phase = 0; phase < kSamplesPerBit; ++phase) {
    phase_lanes[phase] = stagger_phase_lanes(phase, phases, active_mask);
  }

  std::uint8_t* dynamic_sample = output + 1U;

  for (std::uint16_t pixel = 0; pixel < leds_per_strip; ++pixel) {
    for (std::uint8_t channel = 0; channel < 3; ++channel) {
      const std::size_t offset =
          static_cast<std::size_t>(pixel) * 3U + kGrbOffsets[channel];
      std::uint64_t parallel_bits = 0;
      if (broadcast_single_strip) {
        // Each expansion byte is zero or one. Multiplication by the byte-sized
        // lane mask duplicates that bit into every selected lane without
        // carries between bytes, retaining the compact one-lane fast path too.
        parallel_bits = frame_expand_table[rgb[offset]] * lane_mask;
      } else if (compact_mapping) {
        std::uint8_t logical_lane = 0;
        for (std::uint8_t physical_lane = 0; physical_lane < 8;
             ++physical_lane) {
          if ((lane_mask & (1U << physical_lane)) == 0) continue;
          parallel_bits |=
              frame_expand_table[rgb[offset + lane_stride * logical_lane]]
              << physical_lane;
          ++logical_lane;
        }
      } else if (strip_count == 8) {
        parallel_bits = frame_expand_table[rgb[offset]];
        parallel_bits |= frame_expand_table[rgb[offset + lane_stride]] << 1U;
        parallel_bits |= frame_expand_table[rgb[offset + lane_stride * 2U]] << 2U;
        parallel_bits |= frame_expand_table[rgb[offset + lane_stride * 3U]] << 3U;
        parallel_bits |= frame_expand_table[rgb[offset + lane_stride * 4U]] << 4U;
        parallel_bits |= frame_expand_table[rgb[offset + lane_stride * 5U]] << 5U;
        parallel_bits |= frame_expand_table[rgb[offset + lane_stride * 6U]] << 6U;
        parallel_bits |= frame_expand_table[rgb[offset + lane_stride * 7U]] << 7U;
      } else {
        parallel_bits = frame_expand_table[rgb[offset]];
        for (std::uint8_t lane = 1; lane < strip_count; ++lane) {
          parallel_bits |=
              frame_expand_table[rgb[offset + lane_stride * lane]] << lane;
        }
      }

      parallel_bits &= lane_bits;

      if (phases == kMaxStaggerPhases) {
        // Production always uses all three samples as stagger phases. Writing
        // one complete symbol at a time removes the generic phase loop and its
        // modulo from every one of the 414 parallel color channels.
        std::uint8_t* symbol = dynamic_sample;
        std::uint32_t four_bits = static_cast<std::uint32_t>(parallel_bits);
        for (std::uint8_t bit = 0; bit < 4; ++bit) {
          const auto data = static_cast<std::uint8_t>(four_bits);
          symbol[0] = static_cast<std::uint8_t>(
              phase_lanes[1] | (data & phase_lanes[0]));
          symbol[1] = static_cast<std::uint8_t>(
              phase_lanes[2] | (data & phase_lanes[1]));
          symbol[2] = static_cast<std::uint8_t>(
              phase_lanes[0] | (data & phase_lanes[2]));
          four_bits >>= 8U;
          symbol += kSamplesPerBit;
        }
        four_bits = static_cast<std::uint32_t>(parallel_bits >> 32U);
        for (std::uint8_t bit = 0; bit < 4; ++bit) {
          const auto data = static_cast<std::uint8_t>(four_bits);
          symbol[0] = static_cast<std::uint8_t>(
              phase_lanes[1] | (data & phase_lanes[0]));
          symbol[1] = static_cast<std::uint8_t>(
              phase_lanes[2] | (data & phase_lanes[1]));
          symbol[2] = static_cast<std::uint8_t>(
              phase_lanes[0] | (data & phase_lanes[2]));
          four_bits >>= 8U;
          symbol += kSamplesPerBit;
        }
      } else {
        // Extract the eight encoded data bits once per color channel. Shifting
        // inside the phase loop repeats every extraction for the generic path.
        const std::uint8_t bit0 = static_cast<std::uint8_t>(parallel_bits);
        const std::uint8_t bit1 =
            static_cast<std::uint8_t>(parallel_bits >> 8U);
        const std::uint8_t bit2 =
            static_cast<std::uint8_t>(parallel_bits >> 16U);
        const std::uint8_t bit3 =
            static_cast<std::uint8_t>(parallel_bits >> 24U);
        const std::uint8_t bit4 =
            static_cast<std::uint8_t>(parallel_bits >> 32U);
        const std::uint8_t bit5 =
            static_cast<std::uint8_t>(parallel_bits >> 40U);
        const std::uint8_t bit6 =
            static_cast<std::uint8_t>(parallel_bits >> 48U);
        const std::uint8_t bit7 =
            static_cast<std::uint8_t>(parallel_bits >> 56U);
        for (std::uint8_t phase = 0; phase < phases; ++phase) {
          const std::uint8_t data_lanes = phase_lanes[phase];
          const std::uint8_t high_lanes =
              phase_lanes[(phase + 1U) % kSamplesPerBit];
          std::uint8_t* symbol = dynamic_sample + phase;
          symbol[0] =
              static_cast<std::uint8_t>(high_lanes | (bit0 & data_lanes));
          symbol[3] =
              static_cast<std::uint8_t>(high_lanes | (bit1 & data_lanes));
          symbol[6] =
              static_cast<std::uint8_t>(high_lanes | (bit2 & data_lanes));
          symbol[9] =
              static_cast<std::uint8_t>(high_lanes | (bit3 & data_lanes));
          symbol[12] =
              static_cast<std::uint8_t>(high_lanes | (bit4 & data_lanes));
          symbol[15] =
              static_cast<std::uint8_t>(high_lanes | (bit5 & data_lanes));
          symbol[18] =
              static_cast<std::uint8_t>(high_lanes | (bit6 & data_lanes));
          symbol[21] =
              static_cast<std::uint8_t>(high_lanes | (bit7 & data_lanes));
        }
      }
      dynamic_sample += 24U;
    }
  }

  // The trailing phase's last write lands on the first sample of the reset
  // region and carries the leading high edge of a symbol that never gets a
  // data sample. Left alone that is a spurious 416ns pulse on the phase-0
  // lanes; clearing it lets the reset window start idle. Only the data bits
  // of the trailing phase's lanes belong in that byte, and the two lane sets
  // are disjoint, so the mask cannot disturb them.
  if (phases > kStaggerOff) {
    const std::size_t data_samples =
        static_cast<std::size_t>(leds_per_strip) * 3U * 8U * kSamplesPerBit;
    output[data_samples] = static_cast<std::uint8_t>(
        output[data_samples] & ~phase_lanes[phases % kSamplesPerBit]);
  }

  return {true, required_output};
}

}  // namespace ledgrid
