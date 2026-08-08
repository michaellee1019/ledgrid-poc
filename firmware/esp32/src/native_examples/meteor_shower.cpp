// Deterministic sparse meteors with bounded density*trail work.
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

uint32_t mix(uint32_t value) {
  value ^= value >> 16U;
  value *= 0x7FEB352DU;
  value ^= value >> 15U;
  value *= 0x846CA68BU;
  return value ^ (value >> 16U);
}

void add_scaled(uint8_t* pixel, const uint8_t color[3], uint8_t strength) {
  for (uint8_t channel = 0; channel < 3U; ++channel) {
    const uint16_t addition =
        (static_cast<uint16_t>(color[channel]) * strength) / 255U;
    const uint16_t sum = static_cast<uint16_t>(pixel[channel]) + addition;
    pixel[channel] = static_cast<uint8_t>(sum > 255U ? 255U : sum);
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
  if (context->rgb_output_size < required) return LEDGRID_ANIMATION_ERROR;
  for (size_t i = 0; i < required; ++i) context->rgb_output[i] = 0U;

  float speed = 1.0F;
  int density = 7;
  int trail_length = 24;
  bool sparkle = true;
  bool upward = false;
  uint8_t color[3] = {158U, 220U, 255U};
  if (const auto* value = find_parameter(
          context, "speed", LEDGRID_PARAMETER_FLOAT32))
    speed = value->value.real;
  if (const auto* value = find_parameter(
          context, "density", LEDGRID_PARAMETER_INT32))
    density = value->value.integer;
  if (const auto* value = find_parameter(
          context, "trail_length", LEDGRID_PARAMETER_INT32))
    trail_length = value->value.integer;
  if (const auto* value = find_parameter(
          context, "sparkle", LEDGRID_PARAMETER_BOOL))
    sparkle = value->value.boolean != 0U;
  if (const auto* value = find_parameter(
          context, "direction", LEDGRID_PARAMETER_ENUM))
    upward = text_equal(value->value.enum_value, "up");
  if (const auto* value = find_parameter(
          context, "color", LEDGRID_PARAMETER_COLOR_RGB)) {
    color[0] = value->value.color[0];
    color[1] = value->value.color[1];
    color[2] = value->value.color[2];
  }
  if (!(speed >= 0.1F && speed <= 4.0F)) speed = 1.0F;
  const uint32_t speed_permille =
      static_cast<uint32_t>(speed * 1000.0F + 0.5F);
  if (density < 1) density = 1;
  if (density > 16) density = 16;
  if (trail_length < 4) trail_length = 4;
  if (trail_length > 48) trail_length = 48;

  const int32_t span = static_cast<int32_t>(context->leds_per_strip) +
                       trail_length * 2;
  const int32_t motion = static_cast<int32_t>(
      ledgrid_native_example::phase_from_elapsed(
          context->scaled_elapsed_us, speed_permille, 55U,
          static_cast<uint32_t>(span)));
  for (int meteor = 0; meteor < density; ++meteor) {
    const uint32_t seed = mix(static_cast<uint32_t>(meteor) + 0xC001D00DU);
    const uint16_t global_strip = static_cast<uint16_t>(seed % 32U);
    if (global_strip < context->global_strip_offset ||
        global_strip >= context->global_strip_offset + context->local_strips)
      continue;
    const uint8_t local_strip = static_cast<uint8_t>(
        global_strip - context->global_strip_offset);
    const int32_t base = static_cast<int32_t>((seed >> 8U) % span);
    int32_t head = (base + motion + meteor * 19) % span - trail_length;
    if (upward) head = static_cast<int32_t>(context->leds_per_strip - 1U) - head;
    for (int trail = 0; trail < trail_length; ++trail) {
      const int32_t led = upward ? head + trail : head - trail;
      if (led < 0 || led >= context->leds_per_strip) continue;
      const int remaining = trail_length - trail;
      const uint8_t strength = static_cast<uint8_t>(
          (remaining * remaining * 255) / (trail_length * trail_length));
      const size_t at =
          (static_cast<size_t>(local_strip) * context->leds_per_strip +
           static_cast<uint16_t>(led)) * 3U;
      add_scaled(context->rgb_output + at, color, strength);
      if (sparkle && trail == 0) {
        context->rgb_output[at] = 255U;
        context->rgb_output[at + 1U] = 255U;
        context->rgb_output[at + 2U] = 255U;
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
ledgrid_builtin_meteor_shower_v1(void) {
  return &kCallbacks;
}
#else
extern "C" const ledgrid_animation_callbacks_v1* ledgrid_animation_v1(void) {
  return &kCallbacks;
}
#endif
