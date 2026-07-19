#pragma once

#include <cstddef>
#include <cstdint>

namespace ledgrid {

constexpr std::uint32_t kWs2812SampleRateHz = 2400000;
constexpr std::uint16_t kWs2812ResetUs = 300;
constexpr std::uint8_t kMaxParallelStrips = 8;

struct EncodeResult {
  bool ok = false;
  std::size_t bytes_written = 0;
};

std::size_t ws2812_reset_samples(
    std::uint16_t reset_us = kWs2812ResetUs,
    std::uint32_t sample_rate_hz = kWs2812SampleRateHz);

std::size_t ws2812_encoded_size(
    std::uint16_t leds_per_strip,
    std::uint16_t reset_us = kWs2812ResetUs,
    std::uint32_t sample_rate_hz = kWs2812SampleRateHz);

bool initialize_parallel_grb_waveform(
    std::uint8_t strip_count,
    std::uint16_t leds_per_strip,
    std::uint8_t* output,
    std::size_t output_capacity,
    std::uint16_t reset_us = kWs2812ResetUs,
    std::uint32_t sample_rate_hz = kWs2812SampleRateHz);

// Updates only the middle sample of each preinitialized 100/110 symbol. This
// is the hot path used by the persistent DMA buffers in ParallelLedDriver.
EncodeResult encode_parallel_grb_pixels(
    const std::uint8_t* rgb,
    std::size_t rgb_bytes,
    std::uint8_t strip_count,
    std::uint16_t leds_per_strip,
    std::uint8_t brightness,
    std::uint8_t* output,
    std::size_t output_capacity,
    std::uint16_t reset_us = kWs2812ResetUs,
    std::uint32_t sample_rate_hz = kWs2812SampleRateHz);

// Convenience full encoder for callers that do not retain an initialized
// output buffer. The receiver's display path uses the split functions above.
EncodeResult encode_parallel_grb(
    const std::uint8_t* rgb,
    std::size_t rgb_bytes,
    std::uint8_t strip_count,
    std::uint16_t leds_per_strip,
    std::uint8_t brightness,
    std::uint8_t* output,
    std::size_t output_capacity,
    std::uint16_t reset_us = kWs2812ResetUs,
    std::uint32_t sample_rate_hz = kWs2812SampleRateHz);

}  // namespace ledgrid
