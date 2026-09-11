#include "ledgrid/native_background_abi_v2.h"

#ifndef LG_HOST_PREVIEW
asm(".hidden __bss_start\n.hidden _edata\n.hidden _end");
#endif

namespace {

constexpr uint16_t kGlobalStrips = 33;
constexpr uint16_t kMaxLocalStrips = 8;
constexpr uint16_t kLedsPerStrip = 138;

struct alignas(8) AuroraState {
  const ledgrid_native_helpers_v2* helpers;
  uint16_t global_strip_offset;
  uint16_t local_strips;
  uint16_t gain_q8;
  uint16_t source_fps_q8;
  uint32_t seed;
  uint64_t last_source_tick;
  uint8_t reverse_local_strip_order;
  uint8_t initialized;
  uint8_t context_dirty;
  uint8_t palette[LEDGRID_NATIVE_BACKGROUND_PALETTE_ROLES][3];
};

static_assert(sizeof(AuroraState) <= LEDGRID_NATIVE_BACKGROUND_MAX_STATE_BYTES);
static_assert(alignof(AuroraState) <= LEDGRID_NATIVE_BACKGROUND_MAX_STATE_ALIGNMENT);

bool reserved_is_zero(const uint8_t* value, uint8_t count) {
  for (uint8_t index = 0; index < count; ++index) {
    if (value[index] != 0U) return false;
  }
  return true;
}

uint32_t modulo_u64(uint64_t value, uint32_t divisor) {
  const auto* bytes = reinterpret_cast<const uint8_t*>(&value);
  uint32_t remainder = 0U;
  for (int byte = 7; byte >= 0; --byte) {
    for (int bit = 7; bit >= 0; --bit) {
      remainder = (remainder << 1U) |
                  ((bytes[byte] >> static_cast<unsigned>(bit)) & 1U);
      if (remainder >= divisor) remainder -= divisor;
    }
  }
  return remainder;
}

uint64_t divide_u64_u32(uint64_t value, uint32_t divisor) {
  const auto* value_bytes = reinterpret_cast<const uint8_t*>(&value);
  uint64_t quotient = 0U;
  auto* quotient_bytes = reinterpret_cast<uint8_t*>(&quotient);
  uint32_t remainder = 0U;
  for (int byte = 7; byte >= 0; --byte) {
    for (int bit = 7; bit >= 0; --bit) {
      remainder = (remainder << 1U) |
                  ((value_bytes[byte] >> static_cast<unsigned>(bit)) & 1U);
      if (remainder >= divisor) {
        remainder -= divisor;
        quotient_bytes[byte] |= static_cast<uint8_t>(1U << static_cast<unsigned>(bit));
      }
    }
  }
  return quotient;
}

uint32_t float_bits(float value) {
  uint32_t result;
  __builtin_memcpy(&result, &value, sizeof(result));
  return result;
}

uint16_t positive_float_to_q8(uint32_t bits) {
  const uint32_t exponent = (bits >> 23U) & 0xffU;
  if (exponent == 0U) return 0U;
  const uint32_t significand = (bits & 0x7fffffU) | 0x800000U;
  const int shift = static_cast<int>(exponent) - 142;
  const uint32_t converted = shift >= 0
      ? significand << static_cast<unsigned>(shift)
      : significand >> static_cast<unsigned>(-shift);
  return static_cast<uint16_t>(converted > 65535U ? 65535U : converted);
}

uint8_t clamp_channel(int32_t value) {
  if (value < 0) return 0U;
  if (value > 255) return 255U;
  return static_cast<uint8_t>(value);
}

int initialize(void* opaque, const ledgrid_native_init_v2* init) {
  if (opaque == nullptr || init == nullptr ||
      init->abi_version != LEDGRID_NATIVE_BACKGROUND_ABI_VERSION ||
      init->struct_size != sizeof(ledgrid_native_init_v2) ||
      init->global_strips != kGlobalStrips || init->local_strips == 0U ||
      init->local_strips > kMaxLocalStrips ||
      init->leds_per_strip != kLedsPerStrip ||
      init->pixel_count != init->local_strips * kLedsPerStrip ||
      init->global_strip_offset > kGlobalStrips - init->local_strips ||
      init->reverse_local_strip_order > 1U ||
      !reserved_is_zero(init->reserved_zero, 7U) || init->helpers == nullptr ||
      init->helpers->abi_version != LEDGRID_NATIVE_BACKGROUND_ABI_VERSION ||
      init->helpers->struct_size != sizeof(ledgrid_native_helpers_v2) ||
      init->helpers->sin_q15 == nullptr) {
    return LEDGRID_NATIVE_BACKGROUND_ERROR;
  }
  auto* state = static_cast<AuroraState*>(opaque);
  state->helpers = init->helpers;
  state->global_strip_offset = init->global_strip_offset;
  state->local_strips = init->local_strips;
  state->gain_q8 = 184U;
  state->source_fps_q8 = 7680U;
  state->seed = 8012U;
  state->last_source_tick = 0U;
  state->reverse_local_strip_order = init->reverse_local_strip_order;
  state->initialized = 1U;
  state->context_dirty = 1U;
  for (uint8_t role = 0; role < LEDGRID_NATIVE_BACKGROUND_PALETTE_ROLES; ++role) {
    for (uint8_t channel = 0; channel < 3U; ++channel) state->palette[role][channel] = 0U;
  }
  return LEDGRID_NATIVE_BACKGROUND_OK;
}

int update_context(void* opaque, const ledgrid_native_context_v2* context) {
  if (opaque == nullptr || context == nullptr ||
      context->abi_version != LEDGRID_NATIVE_BACKGROUND_ABI_VERSION ||
      context->struct_size != sizeof(ledgrid_native_context_v2) ||
      context->parameter_count != 3U || context->parameters == nullptr ||
      !reserved_is_zero(context->reserved_zero, 7U) || context->vibe == nullptr ||
      context->vibe->struct_size != sizeof(ledgrid_native_vibe_v2) ||
      context->profile == nullptr ||
      context->profile->struct_size != sizeof(ledgrid_native_profile_view_v2)) {
    return LEDGRID_NATIVE_BACKGROUND_ERROR;
  }
  auto* state = static_cast<AuroraState*>(opaque);
  if (state->initialized != 1U ||
      context->profile->global_strips != kGlobalStrips ||
      context->profile->local_strips != state->local_strips ||
      context->profile->leds_per_strip != kLedsPerStrip ||
      context->profile->global_strip_offset != state->global_strip_offset ||
      context->profile->reverse_local_strip_order != state->reverse_local_strip_order ||
      !reserved_is_zero(context->profile->reserved_zero, 5U)) {
    return LEDGRID_NATIVE_BACKGROUND_ERROR;
  }
  const auto* parameters = context->parameters;
  if (parameters[0].id != 0U || parameters[0].type != LEDGRID_NATIVE_PARAMETER_FLOAT32 ||
      parameters[1].id != 1U || parameters[1].type != LEDGRID_NATIVE_PARAMETER_INT32 ||
      parameters[2].id != 2U || parameters[2].type != LEDGRID_NATIVE_PARAMETER_FLOAT32) {
    return LEDGRID_NATIVE_BACKGROUND_ERROR;
  }
  for (uint8_t index = 0; index < 3U; ++index) {
    if (parameters[index].reserved_zero != 0U) return LEDGRID_NATIVE_BACKGROUND_ERROR;
  }
  const uint32_t gain_bits = float_bits(parameters[0].value.real);
  const int32_t seed = parameters[1].value.integer;
  const uint32_t fps_bits = float_bits(parameters[2].value.real);
  if (gain_bits > 0x3f800000U || seed < 0 ||
      fps_bits < 0x3f800000U || fps_bits > 0x42700000U) {
    return LEDGRID_NATIVE_BACKGROUND_ERROR;
  }
  state->gain_q8 = positive_float_to_q8(gain_bits);
  state->source_fps_q8 = positive_float_to_q8(fps_bits);
  state->seed = static_cast<uint32_t>(seed);
  for (uint8_t role = 0; role < LEDGRID_NATIVE_BACKGROUND_PALETTE_ROLES; ++role) {
    for (uint8_t channel = 0; channel < 3U; ++channel) {
      state->palette[role][channel] = context->vibe->palette[role][channel];
    }
  }
  state->context_dirty = 1U;
  return LEDGRID_NATIVE_BACKGROUND_OK;
}

int render(void* opaque, const ledgrid_native_render_request_v2* request,
           ledgrid_native_render_result_v2* result) {
  if (opaque == nullptr || request == nullptr || result == nullptr) return LEDGRID_NATIVE_BACKGROUND_ERROR;
  auto* state = static_cast<AuroraState*>(opaque);
  if (state->initialized != 1U ||
      request->abi_version != LEDGRID_NATIVE_BACKGROUND_ABI_VERSION ||
      request->struct_size != sizeof(ledgrid_native_render_request_v2) ||
      request->rgb_output == nullptr ||
      request->rgb_output_size != state->local_strips * kLedsPerStrip * 3U ||
      request->reserved_zero != 0U ||
      result->struct_size != sizeof(ledgrid_native_render_result_v2)) {
    return LEDGRID_NATIVE_BACKGROUND_ERROR;
  }
  const uint64_t source_tick = divide_u64_u32(
      request->scaled_scene_time_us * state->source_fps_q8, 256000000U);
  const uint32_t period_us =
      (256000000U + state->source_fps_q8 - 1U) / state->source_fps_q8;
  if (state->context_dirty == 0U && source_tick == state->last_source_tick) {
    result->status = LEDGRID_NATIVE_BACKGROUND_OK;
    result->changed = 0U;
  } else {
    const uint64_t tick_q8 = source_tick * 256U;
    const uint64_t tick_seconds = divide_u64_u32(tick_q8, state->source_fps_q8);
    const uint32_t tick_fraction = modulo_u64(tick_q8, state->source_fps_q8);
    const uint16_t time_phase = static_cast<uint16_t>(
        modulo_u64(tick_seconds, 65536U) * 4381U +
        (tick_fraction * 4381U) / state->source_fps_q8);
    const uint32_t x_coefficient = 38588U + (state->seed % 5U) * 1147U;
    for (uint16_t local_strip = 0; local_strip < state->local_strips; ++local_strip) {
      const uint16_t global_strip = state->global_strip_offset +
          (state->reverse_local_strip_order != 0U
               ? state->local_strips - 1U - local_strip
               : local_strip);
      for (uint16_t led = 0; led < kLedsPerStrip; ++led) {
        const uint16_t phase = static_cast<uint16_t>(
            time_phase + (static_cast<uint32_t>(global_strip) * x_coefficient) / 32U +
            (static_cast<uint32_t>(led) * 21900U) / 137U);
        const int32_t wave = state->helpers->sin_q15(phase);
        const uint32_t unit = static_cast<uint32_t>(wave + 32768) * state->gain_q8;
        const uint32_t field_q16 = unit > 16776960U ? 65535U : unit / 256U;
        const uint32_t field_squared_q16 = (field_q16 * field_q16) >> 16U;
        const uint8_t* low = state->palette[0];
        const uint8_t* primary = state->palette[3];
        const uint8_t* accent = state->palette[5];
        const uint32_t output_index =
            (static_cast<uint32_t>(local_strip) * kLedsPerStrip + led) * 3U;
        for (uint8_t channel = 0; channel < 3U; ++channel) {
          int32_t value = low[channel];
          value += ((static_cast<int32_t>(primary[channel]) - low[channel]) *
                    static_cast<int32_t>(field_q16)) / 65536;
          value += ((static_cast<int32_t>(accent[channel]) - primary[channel]) *
                    static_cast<int32_t>(field_squared_q16) * 34 + 3276800) / 6553600;
          request->rgb_output[output_index + channel] = clamp_channel(value);
        }
      }
    }
    state->last_source_tick = source_tick;
    state->context_dirty = 0U;
    result->status = LEDGRID_NATIVE_BACKGROUND_OK;
    result->changed = 1U;
  }
  for (uint8_t index = 0; index < 7U; ++index) result->reserved_zero[index] = 0U;
  const uint32_t remainder = modulo_u64(request->unscaled_scene_time_us, period_us);
  result->next_deadline_scene_time_us =
      request->unscaled_scene_time_us + period_us - remainder;
  return LEDGRID_NATIVE_BACKGROUND_OK;
}

int cleanup(void* opaque) {
  if (opaque == nullptr) return LEDGRID_NATIVE_BACKGROUND_ERROR;
  auto* state = static_cast<AuroraState*>(opaque);
  state->initialized = 0U;
  state->helpers = nullptr;
  return LEDGRID_NATIVE_BACKGROUND_OK;
}

const ledgrid_native_background_api_v2 kApi = {
    LEDGRID_NATIVE_BACKGROUND_ABI_VERSION,
    sizeof(ledgrid_native_background_api_v2),
    sizeof(AuroraState),
    alignof(AuroraState),
    initialize,
    update_context,
    render,
    cleanup,
};

}  // namespace

extern "C" __attribute__((visibility("default"), used))
const ledgrid_native_background_api_v2* ledgrid_native_background_v2(void) {
  return &kApi;
}
