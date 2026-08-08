// Low-cost integer ribbon field sized for one 8x138 receiver frame.
#if defined(LEDGRID_BUILTIN_NATIVE_EXAMPLES)
#include "ledgrid/animation_abi.h"
#else
#include "lga/animation_v1.h"
#endif

#include <stddef.h>
#include <stdint.h>

#include "native_time.hpp"

namespace {

bool text_equal(const char* left, const char* right) {
  if (left == nullptr || right == nullptr) return left == right;
  while (*left != '\0' && *left == *right) { ++left; ++right; }
  return *left == *right;
}

const ledgrid_parameter_v1* find_parameter(
    const ledgrid_render_context_v1* context, const char* name,
    uint8_t type) {
  if (context->parameters == nullptr) return nullptr;
  for (uint8_t i = 0; i < context->parameter_count; ++i) {
    const ledgrid_parameter_v1& parameter = context->parameters[i];
    if (parameter.type == type && text_equal(parameter.name, name))
      return &parameter;
  }
  return nullptr;
}

uint8_t triangle(uint8_t value) {
  return value < 128U ? static_cast<uint8_t>(value * 2U)
                      : static_cast<uint8_t>((255U - value) * 2U);
}

uint8_t ribbon_intensity(int distance, int width) {
  if (distance < 0) distance = -distance;
  if (distance >= width) return 0U;
  return static_cast<uint8_t>(((width - distance) * 255) / width);
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
  if (context->rgb_output_size < required) return LEDGRID_ANIMATION_ERROR;

  float speed = 1.0F;
  int width = 22;
  bool shimmer = true;
  bool sunset = false;
  if (const auto* value = find_parameter(
          context, "speed", LEDGRID_PARAMETER_FLOAT32))
    speed = value->value.real;
  if (const auto* value = find_parameter(
          context, "band_width", LEDGRID_PARAMETER_INT32))
    width = value->value.integer;
  if (const auto* value = find_parameter(
          context, "shimmer", LEDGRID_PARAMETER_BOOL))
    shimmer = value->value.boolean != 0U;
  if (const auto* value = find_parameter(
          context, "palette", LEDGRID_PARAMETER_ENUM))
    sunset = text_equal(value->value.enum_value, "sunset");
  if (!(speed >= 0.1F && speed <= 4.0F)) speed = 1.0F;
  const uint32_t speed_permille =
      static_cast<uint32_t>(speed * 1000.0F + 0.5F);
  if (width < 4) width = 4;
  if (width > 48) width = 48;

  const uint32_t motion = ledgrid_native_example::phase_from_elapsed(
      context->scaled_elapsed_us, speed_permille, 45U, 256U);
  for (uint8_t strip = 0; strip < context->local_strips; ++strip) {
    const uint16_t global_strip = context->global_strip_offset + strip;
    const uint8_t wave_a = triangle(static_cast<uint8_t>(
        global_strip * 19U + motion));
    const uint8_t wave_b = triangle(static_cast<uint8_t>(
        global_strip * 29U - motion / 2U + 73U));
    const int center_a = 22 + (wave_a * 76) / 255;
    const int center_b = 48 + (wave_b * 68) / 255;
    for (uint16_t led = 0; led < context->leds_per_strip; ++led) {
      uint8_t strength_a = ribbon_intensity(static_cast<int>(led) - center_a, width);
      uint8_t strength_b = ribbon_intensity(
          static_cast<int>(led) - center_b, width * 3 / 4);
      if (shimmer) {
        const uint8_t grain = static_cast<uint8_t>(
            global_strip * 37U + led * 17U + context->frame_index * 11U);
        strength_a = static_cast<uint8_t>(
            (static_cast<uint16_t>(strength_a) * (208U + (grain & 47U))) / 255U);
      }
      const uint16_t total = static_cast<uint16_t>(strength_a) + strength_b;
      const uint8_t glow = static_cast<uint8_t>(total > 255U ? 255U : total);
      const size_t at =
          (static_cast<size_t>(strip) * context->leds_per_strip + led) * 3U;
      if (sunset) {
        context->rgb_output[at] = static_cast<uint8_t>(4U + (glow * 251U) / 255U);
        context->rgb_output[at + 1U] = static_cast<uint8_t>(2U + (glow * 82U) / 255U);
        context->rgb_output[at + 2U] = static_cast<uint8_t>(10U + (strength_b * 150U) / 255U);
      } else {
        context->rgb_output[at] = static_cast<uint8_t>(1U + (strength_b * 50U) / 255U);
        context->rgb_output[at + 1U] = static_cast<uint8_t>(4U + (glow * 230U) / 255U);
        context->rgb_output[at + 2U] = static_cast<uint8_t>(10U + (strength_a * 245U) / 255U);
      }
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
ledgrid_builtin_aurora_ribbons_v1(void) {
  return &kCallbacks;
}
#else
extern "C" const ledgrid_animation_callbacks_v1* ledgrid_animation_v1(void) {
  return &kCallbacks;
}
#endif
