#include "ledgrid/startup_animation.hpp"

namespace ledgrid {
namespace {

constexpr std::uint16_t kHueSectors = 6;
constexpr std::uint16_t kHueSectorSteps = 256;
constexpr std::uint16_t kHueCycleSteps = kHueSectors * kHueSectorSteps;
constexpr std::uint16_t kSpatialStep =
    kHueCycleSteps / kStartupRainbowPeriodPixels;
static_assert(
    (kStartupRainbowPeriodPixels & (kStartupRainbowPeriodPixels - 1U)) == 0,
    "startup rainbow period must remain a power of two");

void hue_to_rgb(std::uint16_t hue, std::uint8_t* rgb) {
  const std::uint8_t sector = hue >> 8U;
  const std::uint8_t ramp = hue & 0xFFU;
  const std::uint8_t falling = 0xFFU - ramp;

  switch (sector) {
    case 0:
      rgb[0] = 0xFFU;
      rgb[1] = ramp;
      rgb[2] = 0;
      break;
    case 1:
      rgb[0] = falling;
      rgb[1] = 0xFFU;
      rgb[2] = 0;
      break;
    case 2:
      rgb[0] = 0;
      rgb[1] = 0xFFU;
      rgb[2] = ramp;
      break;
    case 3:
      rgb[0] = 0;
      rgb[1] = falling;
      rgb[2] = 0xFFU;
      break;
    case 4:
      rgb[0] = ramp;
      rgb[1] = 0;
      rgb[2] = 0xFFU;
      break;
    default:
      rgb[0] = 0xFFU;
      rgb[1] = 0;
      rgb[2] = falling;
      break;
  }
}

}  // namespace

bool render_startup_rainbow(
    std::uint64_t elapsed_us,
    std::uint8_t strip_count,
    std::uint16_t leds_per_strip,
    std::uint8_t* output,
    std::size_t output_size) {
  if (output == nullptr || strip_count == 0 || leds_per_strip == 0) {
    return false;
  }

  const std::size_t required_size =
      static_cast<std::size_t>(strip_count) * leds_per_strip * 3U;
  if (output_size < required_size) return false;

  const std::uint16_t motion_phase = static_cast<std::uint16_t>(
      ((elapsed_us % kStartupRainbowCycleUs) * kHueCycleSteps) /
      kStartupRainbowCycleUs);

  // Only 32 distinct colors exist in a frame. Build that moving palette once
  // rather than repeating HSV-sector math for every physical LED.
  std::uint8_t palette[kStartupRainbowPeriodPixels][3] = {};
  for (std::uint16_t phase = 0; phase < kStartupRainbowPeriodPixels; ++phase) {
    const std::uint16_t hue = static_cast<std::uint16_t>(
        (phase * kSpatialStep + kHueCycleSteps - motion_phase) %
        kHueCycleSteps);
    hue_to_rgb(hue, palette[phase]);
  }

  for (std::uint8_t strip = 0; strip < strip_count; ++strip) {
    for (std::uint16_t led = 0; led < leds_per_strip; ++led) {
      const std::uint16_t phase = static_cast<std::uint16_t>(
          (static_cast<std::uint32_t>(strip) + led) &
          (kStartupRainbowPeriodPixels - 1U));
      const std::size_t offset =
          (static_cast<std::size_t>(strip) * leds_per_strip + led) * 3U;
      output[offset] = palette[phase][0];
      output[offset + 1U] = palette[phase][1];
      output[offset + 2U] = palette[phase][2];
    }
  }
  return true;
}

}  // namespace ledgrid
