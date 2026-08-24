#pragma once

#include <cstddef>
#include <cstdint>

namespace ledgrid {

constexpr std::uint32_t kWs2812SampleRateHz = 2400000;
constexpr std::uint16_t kWs2812ResetUs = 300;
constexpr std::uint8_t kMaxParallelStrips = 8;

// Lanes cleared in a lane mask emit no edges at all: their bit stays low in
// every sample of every symbol. This exists to isolate per-lane signal
// integrity faults from faults caused by all eight lanes switching together.
constexpr std::uint8_t kAllLanesMask = 0xFF;

// Every bit symbol is three samples: a leading high, the data sample, then a
// low. Without staggering all lanes share sample 0, so all eight outputs rise
// on the same edge 800k times a second and the buffer's supply pins carry the
// whole surge at once.
constexpr std::uint8_t kSamplesPerBit = 3;

// Lane L is delayed by (L % phases) samples, which spreads the rising edges
// across the symbol instead of stacking them. Delaying a whole lane leaves
// T0H, T1H and the bit period untouched, so it costs nothing in WS2812
// timing; it only moves when that lane's edges land. Three is the ceiling at
// the current sample rate because a symbol is only three samples wide.
constexpr std::uint8_t kStaggerOff = 1;
constexpr std::uint8_t kMaxStaggerPhases = kSamplesPerBit;

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

// Lanes carrying phase p, restricted to the lanes that are active. Exposed so
// the driver and tests can reason about which lanes share a rising edge.
std::uint8_t stagger_phase_lanes(
    std::uint8_t phase,
    std::uint8_t stagger_phases,
    std::uint8_t active_mask);

bool initialize_parallel_grb_waveform(
    std::uint8_t strip_count,
    std::uint16_t leds_per_strip,
    std::uint8_t* output,
    std::size_t output_capacity,
    std::uint16_t reset_us = kWs2812ResetUs,
    std::uint32_t sample_rate_hz = kWs2812SampleRateHz,
    std::uint8_t lane_mask = kAllLanesMask,
    std::uint8_t stagger_phases = kStaggerOff);

// Rewrites the data-bearing samples of each preinitialized 100/110 symbol.
// This is the hot path used by the persistent DMA buffers in
// ParallelLedDriver. The lane mask and stagger phases must match the ones
// used to initialize the waveform.
EncodeResult encode_parallel_grb_pixels(
    const std::uint8_t* rgb,
    std::size_t rgb_bytes,
    std::uint8_t strip_count,
    std::uint16_t leds_per_strip,
    std::uint8_t brightness,
    std::uint8_t* output,
    std::size_t output_capacity,
    std::uint16_t reset_us = kWs2812ResetUs,
    std::uint32_t sample_rate_hz = kWs2812SampleRateHz,
    std::uint8_t lane_mask = kAllLanesMask,
    std::uint8_t stagger_phases = kStaggerOff);

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
    std::uint32_t sample_rate_hz = kWs2812SampleRateHz,
    std::uint8_t lane_mask = kAllLanesMask,
    std::uint8_t stagger_phases = kStaggerOff);

}  // namespace ledgrid
