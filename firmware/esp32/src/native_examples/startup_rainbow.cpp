// The compiled recovery animation and the uploadable startup-rainbow module
// share this exact implementation. Package builds omit
// LEDGRID_BUILTIN_NATIVE_EXAMPLES and export the standard module entrypoint.
#if defined(LEDGRID_BUILTIN_NATIVE_EXAMPLES)
#include "ledgrid/animation_abi.h"
#else
#include "lga/animation_v1.h"
#endif

#include <stddef.h>
#include <stdint.h>

#include "native_time.hpp"

namespace {

constexpr uint16_t kPeriodPixels = 32U;
constexpr uint16_t kHueCycle = 1536U;
constexpr uint16_t kSpatialStep = kHueCycle / kPeriodPixels;
constexpr uint32_t kCycleUs = 1000000U;

bool text_equal(const char* left, const char* right) {
  if (left == nullptr || right == nullptr) return left == right;
  while (*left != '\0' && *left == *right) {
    ++left;
    ++right;
  }
  return *left == *right;
}

float float_parameter(const ledgrid_render_context_v1* context,
                      const char* name, float fallback) {
  if (context->parameters == nullptr) return fallback;
  for (uint8_t i = 0; i < context->parameter_count; ++i) {
    const ledgrid_parameter_v1& parameter = context->parameters[i];
    if (parameter.type == LEDGRID_PARAMETER_FLOAT32 &&
        text_equal(parameter.name, name))
      return parameter.value.real;
  }
  return fallback;
}

const char* enum_parameter(const ledgrid_render_context_v1* context,
                           const char* name, const char* fallback) {
  if (context->parameters == nullptr) return fallback;
  for (uint8_t i = 0; i < context->parameter_count; ++i) {
    const ledgrid_parameter_v1& parameter = context->parameters[i];
    if (parameter.type == LEDGRID_PARAMETER_ENUM &&
        text_equal(parameter.name, name))
      return parameter.value.enum_value;
  }
  return fallback;
}

void hue_to_rgb(uint16_t hue, bool pastel, uint8_t rgb[3]) {
  const uint8_t sector = static_cast<uint8_t>((hue >> 8U) % 6U);
  const uint8_t ramp = static_cast<uint8_t>(hue);
  const uint8_t fall = static_cast<uint8_t>(255U - ramp);
  switch (sector) {
    case 0: rgb[0] = 255U; rgb[1] = ramp; rgb[2] = 0U; break;
    case 1: rgb[0] = fall; rgb[1] = 255U; rgb[2] = 0U; break;
    case 2: rgb[0] = 0U; rgb[1] = 255U; rgb[2] = ramp; break;
    case 3: rgb[0] = 0U; rgb[1] = fall; rgb[2] = 255U; break;
    case 4: rgb[0] = ramp; rgb[1] = 0U; rgb[2] = 255U; break;
    default: rgb[0] = 255U; rgb[1] = 0U; rgb[2] = fall; break;
  }
  if (pastel) {
    rgb[0] = static_cast<uint8_t>(64U + (static_cast<uint16_t>(rgb[0]) * 3U) / 4U);
    rgb[1] = static_cast<uint8_t>(64U + (static_cast<uint16_t>(rgb[1]) * 3U) / 4U);
    rgb[2] = static_cast<uint8_t>(64U + (static_cast<uint16_t>(rgb[2]) * 3U) / 4U);
  }
}

int initialize(const ledgrid_render_context_v1* context,
               const ledgrid_host_helpers_v1*, void** state) {
  if (context == nullptr || state == nullptr ||
      context->abi_version != LEDGRID_ANIMATION_ABI_V1)
    return LEDGRID_ANIMATION_ERROR;
  *state = nullptr;
  return LEDGRID_ANIMATION_OK;
}

int render(void*, const ledgrid_render_context_v1* context) {
  if (context == nullptr || context->abi_version != LEDGRID_ANIMATION_ABI_V1 ||
      context->local_strips == 0U || context->leds_per_strip == 0U ||
      context->rgb_output == nullptr)
    return LEDGRID_ANIMATION_ERROR;
  const size_t required = static_cast<size_t>(context->local_strips) *
                          context->leds_per_strip * 3U;
  if (context->rgb_output_size < required)
    return LEDGRID_ANIMATION_ERROR;

  float speed = float_parameter(context, "speed", 1.0F);
  if (!(speed >= 0.1F && speed <= 4.0F)) speed = 1.0F;
  const uint32_t speed_permille =
      static_cast<uint32_t>(speed * 1000.0F + 0.5F);
  const bool reverse = text_equal(
      enum_parameter(context, "direction", "up-right"), "down-left");
  const bool pastel = text_equal(
      enum_parameter(context, "palette", "spectrum"), "pastel");
  uint16_t motion = static_cast<uint16_t>(
      ledgrid_native_example::phase_from_elapsed(
          context->scaled_elapsed_us, speed_permille,
          kHueCycle, kHueCycle));
  if (reverse && motion != 0U) motion = static_cast<uint16_t>(kHueCycle - motion);

  uint8_t palette[kPeriodPixels][3];
  for (uint16_t phase = 0; phase < kPeriodPixels; ++phase) {
    const uint16_t hue = static_cast<uint16_t>(
        (phase * kSpatialStep + kHueCycle - motion) % kHueCycle);
    hue_to_rgb(hue, pastel, palette[phase]);
  }
  for (uint8_t strip = 0; strip < context->local_strips; ++strip) {
    const uint16_t global_strip = context->global_strip_offset + strip;
    for (uint16_t led = 0; led < context->leds_per_strip; ++led) {
      const uint16_t phase = static_cast<uint16_t>(
          (static_cast<uint32_t>(global_strip) + led) & (kPeriodPixels - 1U));
      const size_t at =
          (static_cast<size_t>(strip) * context->leds_per_strip + led) * 3U;
      context->rgb_output[at] = palette[phase][0];
      context->rgb_output[at + 1U] = palette[phase][1];
      context->rgb_output[at + 2U] = palette[phase][2];
    }
  }
  return LEDGRID_ANIMATION_OK;
}

void cleanup(void*) {}

const ledgrid_animation_callbacks_v1 kCallbacks = {
    LEDGRID_ANIMATION_ABI_V1, initialize, render, cleanup};

}  // namespace

#if defined(LEDGRID_BUILTIN_NATIVE_EXAMPLES)
extern "C" const ledgrid_animation_callbacks_v1*
ledgrid_builtin_startup_rainbow_v1(void) {
  return &kCallbacks;
}
#else
extern "C" const ledgrid_animation_callbacks_v1* ledgrid_animation_v1(void) {
  return &kCallbacks;
}
#endif
