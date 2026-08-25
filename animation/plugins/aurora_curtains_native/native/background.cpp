#include "ledgrid/native_background_abi_v2.h"

#ifndef LG_HOST_PREVIEW
// GNU ld synthesizes these boundary symbols for a bare shared object. Marking
// them hidden in the input unit keeps the dynamic export surface to the ABI
// entrypoint without introducing a second linker-script source input.
asm(".hidden __bss_start\n.hidden _edata\n.hidden _end");
#endif

namespace {

static_assert(LEDGRID_NATIVE_MODIFIER_ILLUMINATE == 1);
static_assert(LEDGRID_NATIVE_MODIFIER_EMITTER == 14);
static_assert(LEDGRID_NATIVE_PROFILE_CATEGORY == 1);
static_assert(LEDGRID_NATIVE_PROFILE_NORMAL_Y == 9);
static_assert(LEDGRID_NATIVE_PROFILE_UNSIGNED_ENUM == 1);
static_assert(LEDGRID_NATIVE_PROFILE_SIGNED_BYTE == 4);
static_assert(LEDGRID_NATIVE_CATEGORY_OPEN == 0);
static_assert(LEDGRID_NATIVE_CATEGORY_GLOBE == 2);
static_assert(LEDGRID_NATIVE_GLOBE_REGION_NONE == 0);
static_assert(LEDGRID_NATIVE_GLOBE_REGION_LOWER_RIGHT == 7);
static_assert(LEDGRID_NATIVE_BACKGROUND_MAX_STATE_BYTES == 64U * 1024U);
static_assert(LEDGRID_NATIVE_BACKGROUND_MAX_STATE_ALIGNMENT == 64U);

constexpr uint16_t kGlobalStrips = 33;
constexpr uint16_t kMaxLocalStrips = 8;
constexpr uint16_t kLedsPerStrip = 138;
constexpr uint64_t kFramePeriodUs = 16667U;

struct alignas(8) AuroraState {
  const ledgrid_native_helpers_v2* helpers;
  uint16_t global_strip_offset;
  uint16_t local_strips;
  uint16_t brightness_q8;
  uint16_t motion_q8;
  uint8_t curtain_width;
  uint8_t layers;
  uint8_t shimmer;
  uint8_t initialized;
  uint8_t reverse_local_strip_order;
  uint8_t palette[LEDGRID_NATIVE_BACKGROUND_PALETTE_ROLES][3];
};

static_assert(sizeof(AuroraState) <= LEDGRID_NATIVE_BACKGROUND_MAX_STATE_BYTES);
static_assert(alignof(AuroraState) <=
              LEDGRID_NATIVE_BACKGROUND_MAX_STATE_ALIGNMENT);

uint32_t modulo_u64(uint64_t value, uint32_t divisor) {
  // Both frozen peers are little-endian. Walking bytes avoids compiler-runtime
  // helpers for 64-bit shifts on Xtensa while retaining the full time range.
  const auto* bytes = reinterpret_cast<const uint8_t*>(&value);
  uint32_t remainder = 0U;
  for (int byte = 7; byte >= 0; --byte) {
    for (int bit = 7; bit >= 0; --bit) {
      remainder = (remainder << 1U) |
                  ((bytes[byte] >> static_cast<unsigned>(bit)) & 1U);
      if (remainder >= divisor) {
        remainder -= divisor;
      }
    }
  }
  return remainder;
}

uint32_t float_bits(float value) {
  uint32_t result;
  __builtin_memcpy(&result, &value, sizeof(result));
  return result;
}

uint16_t positive_float_to_q8(uint32_t bits) {
  const uint32_t exponent = (bits >> 23U) & 0xffU;
  if (exponent == 0U) {
    return 0U;
  }
  const uint32_t significand = (bits & 0x7fffffU) | 0x800000U;
  const int shift = static_cast<int>(exponent) - 142;
  const uint32_t converted =
      shift >= 0 ? significand << static_cast<unsigned>(shift)
                 : significand >> static_cast<unsigned>(-shift);
  return static_cast<uint16_t>(converted > 65535U ? 65535U : converted);
}

bool reserved_is_zero(const uint8_t* value, uint8_t count) {
  for (uint8_t index = 0; index < count; ++index) {
    if (value[index] != 0U) {
      return false;
    }
  }
  return true;
}

uint8_t scale_channel(uint8_t value, uint16_t brightness_q8) {
  return static_cast<uint8_t>((static_cast<uint32_t>(value) * brightness_q8) >> 8U);
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
      init->helpers == nullptr ||
      init->reverse_local_strip_order > 1U ||
      !reserved_is_zero(init->reserved_zero, 7U) ||
      init->helpers->abi_version != LEDGRID_NATIVE_BACKGROUND_ABI_VERSION ||
      init->helpers->struct_size != sizeof(ledgrid_native_helpers_v2) ||
      init->helpers->sin_q15 == nullptr) {
    return LEDGRID_NATIVE_BACKGROUND_ERROR;
  }
  auto* state = static_cast<AuroraState*>(opaque);
  state->helpers = init->helpers;
  state->global_strip_offset = init->global_strip_offset;
  state->local_strips = init->local_strips;
  state->brightness_q8 = 108U;
  state->motion_q8 = 87U;
  state->curtain_width = 7U;
  state->layers = 3U;
  state->shimmer = 1U;
  state->initialized = 1U;
  state->reverse_local_strip_order = init->reverse_local_strip_order;
  for (uint8_t role = 0; role < LEDGRID_NATIVE_BACKGROUND_PALETTE_ROLES; ++role) {
    for (uint8_t channel = 0; channel < 3U; ++channel) {
      state->palette[role][channel] = 0U;
    }
  }
  return LEDGRID_NATIVE_BACKGROUND_OK;
}

int update_context(void* opaque, const ledgrid_native_context_v2* context) {
  if (opaque == nullptr || context == nullptr ||
      context->abi_version != LEDGRID_NATIVE_BACKGROUND_ABI_VERSION ||
      context->struct_size != sizeof(ledgrid_native_context_v2) ||
      context->parameter_count != 5U || context->parameters == nullptr ||
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
      context->profile->section_count > LEDGRID_NATIVE_BACKGROUND_MAX_PROFILE_SECTIONS ||
      !reserved_is_zero(context->profile->reserved_zero, 5U)) {
    return LEDGRID_NATIVE_BACKGROUND_ERROR;
  }
  const auto* parameters = context->parameters;
  if (parameters[0].id != 0U || parameters[0].type != LEDGRID_NATIVE_PARAMETER_FLOAT32 ||
      parameters[1].id != 1U || parameters[1].type != LEDGRID_NATIVE_PARAMETER_INT32 ||
      parameters[2].id != 2U || parameters[2].type != LEDGRID_NATIVE_PARAMETER_INT32 ||
      parameters[3].id != 3U || parameters[3].type != LEDGRID_NATIVE_PARAMETER_FLOAT32 ||
      parameters[4].id != 4U || parameters[4].type != LEDGRID_NATIVE_PARAMETER_BOOL) {
    return LEDGRID_NATIVE_BACKGROUND_ERROR;
  }
  for (uint8_t index = 0; index < 5U; ++index) {
    if (parameters[index].reserved_zero != 0U) {
      return LEDGRID_NATIVE_BACKGROUND_ERROR;
    }
  }
  const uint32_t brightness_bits = float_bits(parameters[0].value.real);
  const int32_t curtain_width = parameters[1].value.integer;
  const int32_t layers = parameters[2].value.integer;
  const uint32_t motion_bits = float_bits(parameters[3].value.real);
  if (brightness_bits < 0x3d23d70aU || brightness_bits > 0x3f800000U ||
      curtain_width < 2 || curtain_width > 14 || layers < 1 || layers > 5 ||
      motion_bits < 0x3ca3d70aU || motion_bits > 0x3f800000U ||
      parameters[4].value.boolean > 1U) {
    return LEDGRID_NATIVE_BACKGROUND_ERROR;
  }
  // Receiver framework owns the one-and-only vibe luminance pass after this
  // module renders; applying it here would double-scale native backgrounds.
  state->brightness_q8 = positive_float_to_q8(brightness_bits);
  state->motion_q8 = positive_float_to_q8(motion_bits);
  state->curtain_width = static_cast<uint8_t>(curtain_width);
  state->layers = static_cast<uint8_t>(layers);
  state->shimmer = parameters[4].value.boolean;
  for (uint8_t role = 0; role < LEDGRID_NATIVE_BACKGROUND_PALETTE_ROLES; ++role) {
    for (uint8_t channel = 0; channel < 3U; ++channel) {
      state->palette[role][channel] = context->vibe->palette[role][channel];
    }
  }
  return LEDGRID_NATIVE_BACKGROUND_OK;
}

int render(void* opaque, const ledgrid_native_render_request_v2* request,
           ledgrid_native_render_result_v2* result) {
  if (opaque == nullptr || request == nullptr || result == nullptr) {
    return LEDGRID_NATIVE_BACKGROUND_ERROR;
  }
  auto* state = static_cast<AuroraState*>(opaque);
  if (state->initialized != 1U) {
    return LEDGRID_NATIVE_BACKGROUND_ERROR;
  }
  if (request->abi_version != LEDGRID_NATIVE_BACKGROUND_ABI_VERSION ||
      request->struct_size != sizeof(ledgrid_native_render_request_v2) ||
      request->rgb_output == nullptr ||
      request->rgb_output_size != state->local_strips * kLedsPerStrip * 3U ||
      request->reserved_zero != 0U ||
      result->struct_size != sizeof(ledgrid_native_render_result_v2)) {
    return LEDGRID_NATIVE_BACKGROUND_ERROR;
  }
  const uint32_t motion_time =
      modulo_u64(request->scaled_scene_time_us, 8000000U) * state->motion_q8;
  for (uint16_t local_strip = 0; local_strip < state->local_strips; ++local_strip) {
    const uint16_t global_strip = state->global_strip_offset +
        (state->reverse_local_strip_order != 0U
             ? state->local_strips - 1U - local_strip
             : local_strip);
    for (uint16_t led = 0; led < kLedsPerStrip; ++led) {
      uint16_t glow = 0U;
      for (uint8_t layer = 0; layer < state->layers; ++layer) {
        const uint16_t phase = static_cast<uint16_t>(
            (motion_time >> (10U + layer)) + led * (137U + layer * 41U) +
            layer * 13107U);
        const int32_t wave = state->helpers->sin_q15(phase);
        // Signed integer division truncates toward zero in C++17 on both peers;
        // shifting a negative signed value would be implementation-defined.
        const int32_t center = 16 + (wave * (10 - layer)) / 32768;
        int32_t distance = static_cast<int32_t>(global_strip) - center;
        if (distance < 0) {
          distance = -distance;
        }
        const int32_t reach = state->curtain_width + layer;
        if (distance < reach) {
          glow += static_cast<uint16_t>((reach - distance) * (180 / reach));
        }
      }
      if (glow > 255U) {
        glow = 255U;
      }
      if (state->shimmer != 0U) {
        const uint16_t sparkle = static_cast<uint16_t>(
            (global_strip * 83U + led * 47U + (motion_time >> 12U)) & 255U);
        if (sparkle > 246U) {
          glow = static_cast<uint16_t>(glow + 52U > 255U ? 255U : glow + 52U);
        }
      }
      const uint8_t* low = state->palette[0];
      const uint8_t* primary = state->palette[3];
      const uint8_t* accent = state->palette[5];
      const uint32_t output_index =
          (static_cast<uint32_t>(local_strip) * kLedsPerStrip + led) * 3U;
      for (uint8_t channel = 0; channel < 3U; ++channel) {
        const uint16_t aurora = static_cast<uint16_t>(
            (static_cast<uint16_t>(primary[channel]) * glow +
             static_cast<uint16_t>(accent[channel]) * (glow >> 2U) +
             static_cast<uint16_t>(low[channel]) * (255U - glow)) /
            319U);
        request->rgb_output[output_index + channel] =
            scale_channel(static_cast<uint8_t>(aurora), state->brightness_q8);
      }
    }
  }
  result->status = LEDGRID_NATIVE_BACKGROUND_OK;
  result->changed = 1U;
  for (uint8_t index = 0; index < 7U; ++index) {
    result->reserved_zero[index] = 0U;
  }
  const uint32_t remainder = modulo_u64(request->unscaled_scene_time_us, kFramePeriodUs);
  result->next_deadline_scene_time_us =
      request->unscaled_scene_time_us + kFramePeriodUs - remainder;
  return LEDGRID_NATIVE_BACKGROUND_OK;
}

int cleanup(void* opaque) {
  if (opaque == nullptr) {
    return LEDGRID_NATIVE_BACKGROUND_ERROR;
  }
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
