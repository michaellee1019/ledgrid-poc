#pragma once

#include <cstddef>
#include <cstdint>

namespace ledgrid {

// One complete rainbow spans this many logical LED coordinates. Equal phase
// change in strip (x) and LED (y) coordinates makes the gradient 45 degrees.
constexpr std::uint16_t kStartupRainbowPeriodPixels = 32;
constexpr std::uint32_t kStartupRainbowCycleUs = 1000000;

// Renders a full-saturation rainbow into the strip-major RGB frame used by the
// receiver. Time advances the field toward increasing strip and LED indices.
// The frame repeats exactly once per second.
bool render_startup_rainbow(
    std::uint64_t elapsed_us,
    std::uint8_t strip_count,
    std::uint16_t leds_per_strip,
    std::uint8_t* output,
    std::size_t output_size);

}  // namespace ledgrid
