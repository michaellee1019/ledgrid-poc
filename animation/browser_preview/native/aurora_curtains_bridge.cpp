#include "ledgrid/native_background_abi_v2.h"

#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>

#include <emscripten/emscripten.h>

extern "C" const ledgrid_native_background_api_v2*
ledgrid_native_background_v2(void);

namespace {

constexpr uint16_t kGlobalStrips = 33;
constexpr uint16_t kLedsPerStrip = 138;
constexpr uint16_t kMaxLocalStrips = 8;
constexpr uint8_t kReceiverCount = 5;
constexpr uint64_t kCadencePeriodUs = 16667U;
constexpr uint32_t kWallBytes =
    static_cast<uint32_t>(kGlobalStrips) * kLedsPerStrip * 3U;
constexpr uint32_t kMaxLocalBytes =
    static_cast<uint32_t>(kMaxLocalStrips) * kLedsPerStrip * 3U;

struct ReceiverView {
  uint16_t global_strip_offset;
  uint16_t local_strips;
  bool reverse_local_strip_order;
};

// This is the installed receiver-native coordinate contract. Keep logical
// receiver identity separate from this physical/global-coordinate view.
constexpr ReceiverView kReceiverViews[kReceiverCount] = {
    {0, 8, false},
    {8, 8, false},
    {16, 8, true},
    {24, 8, true},
    {32, 1, false},
};

constexpr uint8_t kNeutralPalette[LEDGRID_NATIVE_BACKGROUND_PALETTE_ROLES][3] = {
    {8, 10, 16},
    {32, 38, 52},
    {92, 104, 128},
    {224, 228, 236},
    {152, 164, 184},
    {255, 184, 72},
    {240, 244, 252},
    {255, 72, 64},
};

enum BrowserError : int32_t {
  kBrowserOk = 0,
  kBrowserBadGeometry = 1,
  kBrowserBadApi = 2,
  kBrowserBadParameters = 3,
  kBrowserInitializeFailed = 4,
  kBrowserContextFailed = 5,
  kBrowserRenderFailed = 6,
  kBrowserInvalidResult = 7,
};

alignas(LEDGRID_NATIVE_BACKGROUND_MAX_STATE_ALIGNMENT)
uint8_t g_states[kReceiverCount][LEDGRID_NATIVE_BACKGROUND_MAX_STATE_BYTES];
uint8_t g_local_frames[kReceiverCount][kMaxLocalBytes];
uint8_t g_wall_frame[kWallBytes];
ledgrid_native_profile_view_v2 g_profiles[kReceiverCount];
uint64_t g_previous_deadlines[kReceiverCount];
bool g_local_frame_ready[kReceiverCount];
uint8_t g_initialized_receivers = 0;
uint8_t g_changed = 0;
int32_t g_last_error = kBrowserOk;

ledgrid_native_parameter_v2 g_parameters[5];
ledgrid_native_vibe_v2 g_vibe;
ledgrid_native_modifier_view_v2 g_modifiers;
ledgrid_native_helpers_v2 g_helpers;
const ledgrid_native_background_api_v2* g_api = nullptr;

uint32_t random_u32(uint32_t* state) {
  if (state == nullptr) {
    return 0U;
  }
  uint32_t value = *state;
  value ^= value << 13U;
  value ^= value >> 17U;
  value ^= value << 5U;
  *state = value;
  return value;
}

int32_t round_to_nearest_even(double value) {
  const double lower_double = std::floor(value);
  const int32_t lower = static_cast<int32_t>(lower_double);
  const double fraction = value - lower_double;
  if (fraction < 0.5) {
    return lower;
  }
  if (fraction > 0.5) {
    return lower + 1;
  }
  return (lower & 1) == 0 ? lower : lower + 1;
}

void hsv_to_rgb(uint16_t hue, uint8_t saturation, uint8_t value,
                uint8_t rgb[3]) {
  if (rgb == nullptr) {
    return;
  }
  const double h = static_cast<double>(hue) / 65536.0;
  const double s = static_cast<double>(saturation) / 255.0;
  const double v = static_cast<double>(value) / 255.0;
  const double sector = h * 6.0;
  const int32_t index = static_cast<int32_t>(std::floor(sector));
  const double fraction = sector - std::floor(sector);
  const double p = v * (1.0 - s);
  const double q = v * (1.0 - s * fraction);
  const double t = v * (1.0 - s * (1.0 - fraction));
  double red = v;
  double green = t;
  double blue = p;
  switch (index % 6) {
    case 1:
      red = q;
      green = v;
      blue = p;
      break;
    case 2:
      red = p;
      green = v;
      blue = t;
      break;
    case 3:
      red = p;
      green = q;
      blue = v;
      break;
    case 4:
      red = t;
      green = p;
      blue = v;
      break;
    case 5:
      red = v;
      green = p;
      blue = q;
      break;
    default:
      break;
  }
  rgb[0] = static_cast<uint8_t>(round_to_nearest_even(red * 255.0));
  rgb[1] = static_cast<uint8_t>(round_to_nearest_even(green * 255.0));
  rgb[2] = static_cast<uint8_t>(round_to_nearest_even(blue * 255.0));
}

int16_t sin_q15(uint16_t phase) {
  switch (phase) {
    case 0:
    case 32768:
      return 0;
    case 16384:
      return 32767;
    case 49152:
      return -32767;
    default:
      break;
  }
  constexpr double kTau = 0x1.921fb54442d18p+2;
  const double radians = static_cast<double>(phase) * kTau / 65536.0;
  return static_cast<int16_t>(
      round_to_nearest_even(std::sin(radians) * 32767.0));
}

int16_t cos_q15(uint16_t phase) {
  return sin_q15(static_cast<uint16_t>(phase + 16384U));
}

void reset_context_values() {
  std::memset(g_parameters, 0, sizeof(g_parameters));
  g_parameters[0].id = 0;
  g_parameters[0].type = LEDGRID_NATIVE_PARAMETER_FLOAT32;
  g_parameters[0].value.real = 0.42F;
  g_parameters[1].id = 1;
  g_parameters[1].type = LEDGRID_NATIVE_PARAMETER_INT32;
  g_parameters[1].value.integer = 7;
  g_parameters[2].id = 2;
  g_parameters[2].type = LEDGRID_NATIVE_PARAMETER_INT32;
  g_parameters[2].value.integer = 3;
  g_parameters[3].id = 3;
  g_parameters[3].type = LEDGRID_NATIVE_PARAMETER_FLOAT32;
  g_parameters[3].value.real = 0.34F;
  g_parameters[4].id = 4;
  g_parameters[4].type = LEDGRID_NATIVE_PARAMETER_BOOL;
  g_parameters[4].value.boolean = 1;

  std::memset(&g_vibe, 0, sizeof(g_vibe));
  g_vibe.struct_size = sizeof(g_vibe);
  g_vibe.profile_version = 1;
  g_vibe.revision = 0;
  std::memcpy(g_vibe.palette, kNeutralPalette, sizeof(kNeutralPalette));
  g_vibe.tempo_q8_8 = 256;
  g_vibe.luminance_q8_8 = 256;
  g_vibe.chroma_q8_8 = 256;
  g_vibe.energy_q8_8 = 128;

  std::memset(&g_modifiers, 0, sizeof(g_modifiers));
  g_modifiers.struct_size = sizeof(g_modifiers);
}

bool valid_api(const ledgrid_native_background_api_v2* api) {
  return api != nullptr &&
         api->abi_version == LEDGRID_NATIVE_BACKGROUND_ABI_VERSION &&
         api->struct_size == sizeof(ledgrid_native_background_api_v2) &&
         api->state_size >= 1U &&
         api->state_size <= LEDGRID_NATIVE_BACKGROUND_MAX_STATE_BYTES &&
         api->state_alignment >= 1U &&
         api->state_alignment <= LEDGRID_NATIVE_BACKGROUND_MAX_STATE_ALIGNMENT &&
         (api->state_alignment & (api->state_alignment - 1U)) == 0U &&
         api->initialize != nullptr && api->update_context != nullptr &&
         api->render != nullptr && api->cleanup != nullptr;
}

int update_all_contexts() {
  for (uint8_t receiver = 0; receiver < g_initialized_receivers; ++receiver) {
    ledgrid_native_context_v2 context{};
    context.abi_version = LEDGRID_NATIVE_BACKGROUND_ABI_VERSION;
    context.struct_size = sizeof(context);
    context.parameters = g_parameters;
    context.parameter_count = 5;
    context.vibe = &g_vibe;
    context.modifiers = &g_modifiers;
    context.profile = &g_profiles[receiver];
    if (g_api->update_context(g_states[receiver], &context) !=
        LEDGRID_NATIVE_BACKGROUND_OK) {
      g_last_error = kBrowserContextFailed;
      return -1;
    }
  }
  g_last_error = kBrowserOk;
  return 0;
}

void cleanup_initialized() {
  if (g_api != nullptr && g_api->cleanup != nullptr) {
    for (uint8_t receiver = 0; receiver < g_initialized_receivers; ++receiver) {
      g_api->cleanup(g_states[receiver]);
    }
  }
  g_initialized_receivers = 0;
}

}  // namespace

#define LG_BROWSER_EXPORT extern "C" EMSCRIPTEN_KEEPALIVE

LG_BROWSER_EXPORT int lg_browser_init(uint32_t global_strips,
                                      uint32_t leds_per_strip) {
  cleanup_initialized();
  g_last_error = kBrowserOk;
  g_changed = 0;
  std::memset(g_states, 0, sizeof(g_states));
  std::memset(g_local_frames, 0, sizeof(g_local_frames));
  std::memset(g_wall_frame, 0, sizeof(g_wall_frame));
  std::memset(g_profiles, 0, sizeof(g_profiles));
  std::memset(g_previous_deadlines, 0, sizeof(g_previous_deadlines));
  std::memset(g_local_frame_ready, 0, sizeof(g_local_frame_ready));

  if (global_strips != kGlobalStrips || leds_per_strip != kLedsPerStrip) {
    g_last_error = kBrowserBadGeometry;
    return -1;
  }

  g_api = ledgrid_native_background_v2();
  if (!valid_api(g_api)) {
    g_last_error = kBrowserBadApi;
    return -1;
  }

  g_helpers = {};
  g_helpers.abi_version = LEDGRID_NATIVE_BACKGROUND_ABI_VERSION;
  g_helpers.struct_size = sizeof(g_helpers);
  g_helpers.random_u32 = random_u32;
  g_helpers.hsv_to_rgb = hsv_to_rgb;
  g_helpers.sin_q15 = sin_q15;
  g_helpers.cos_q15 = cos_q15;
  reset_context_values();

  for (uint8_t receiver = 0; receiver < kReceiverCount; ++receiver) {
    const ReceiverView& view = kReceiverViews[receiver];
    ledgrid_native_profile_view_v2& profile = g_profiles[receiver];
    profile = {};
    profile.struct_size = sizeof(profile);
    profile.global_strips = kGlobalStrips;
    profile.leds_per_strip = kLedsPerStrip;
    profile.global_strip_offset = view.global_strip_offset;
    profile.local_strips = view.local_strips;
    profile.reverse_local_strip_order = view.reverse_local_strip_order ? 1U : 0U;

    ledgrid_native_init_v2 init{};
    init.abi_version = LEDGRID_NATIVE_BACKGROUND_ABI_VERSION;
    init.struct_size = sizeof(init);
    init.global_strips = kGlobalStrips;
    init.local_strips = view.local_strips;
    init.leds_per_strip = kLedsPerStrip;
    init.global_strip_offset = view.global_strip_offset;
    init.reverse_local_strip_order = view.reverse_local_strip_order ? 1U : 0U;
    init.pixel_count = static_cast<uint32_t>(view.local_strips) * kLedsPerStrip;
    init.deterministic_seed = 0x0A17C0A5U;
    init.scene_epoch_ns = 0x123456789ABCDEF0ULL;
    init.helpers = &g_helpers;
    if (g_api->initialize(g_states[receiver], &init) !=
        LEDGRID_NATIVE_BACKGROUND_OK) {
      g_last_error = kBrowserInitializeFailed;
      cleanup_initialized();
      return -1;
    }
    ++g_initialized_receivers;
  }

  return update_all_contexts();
}

LG_BROWSER_EXPORT int lg_browser_set_parameters(float brightness,
                                                int32_t curtain_width,
                                                int32_t layers,
                                                float motion,
                                                int32_t shimmer) {
  if (g_initialized_receivers != kReceiverCount ||
      !std::isfinite(brightness) || brightness < 0.04F || brightness > 1.0F ||
      curtain_width < 2 || curtain_width > 14 || layers < 1 || layers > 5 ||
      !std::isfinite(motion) || motion < 0.02F || motion > 1.0F ||
      (shimmer != 0 && shimmer != 1)) {
    g_last_error = kBrowserBadParameters;
    return -1;
  }
  g_parameters[0].value.real = brightness;
  g_parameters[1].value.integer = curtain_width;
  g_parameters[2].value.integer = layers;
  g_parameters[3].value.real = motion;
  g_parameters[4].value.boolean = static_cast<uint8_t>(shimmer);
  return update_all_contexts();
}

LG_BROWSER_EXPORT int lg_browser_render(uint32_t scene_time_low,
                                        uint32_t scene_time_high,
                                        uint32_t frame_index_low,
                                        uint32_t frame_index_high) {
  if (g_initialized_receivers != kReceiverCount) {
    g_last_error = kBrowserInitializeFailed;
    return -1;
  }
  const uint64_t scene_time_us =
      (static_cast<uint64_t>(scene_time_high) << 32U) | scene_time_low;
  const uint64_t frame_index =
      (static_cast<uint64_t>(frame_index_high) << 32U) | frame_index_low;
  bool wall_changed = false;

  for (uint8_t receiver = 0; receiver < kReceiverCount; ++receiver) {
    const ReceiverView& view = kReceiverViews[receiver];
    const uint32_t local_bytes =
        static_cast<uint32_t>(view.local_strips) * kLedsPerStrip * 3U;
    ledgrid_native_render_request_v2 request{};
    request.abi_version = LEDGRID_NATIVE_BACKGROUND_ABI_VERSION;
    request.struct_size = sizeof(request);
    request.unscaled_scene_time_us = scene_time_us;
    request.scaled_scene_time_us = scene_time_us;
    request.frame_index = frame_index;
    request.rgb_output = g_local_frames[receiver];
    request.rgb_output_size = local_bytes;

    ledgrid_native_render_result_v2 result{};
    result.struct_size = sizeof(result);
    result.status = LEDGRID_NATIVE_BACKGROUND_ERROR;
    result.changed = 0xFFU;
    const int callback_status =
        g_api->render(g_states[receiver], &request, &result);
    if (callback_status != LEDGRID_NATIVE_BACKGROUND_OK ||
        result.status != LEDGRID_NATIVE_BACKGROUND_OK) {
      g_last_error = kBrowserRenderFailed;
      return -1;
    }
    bool reserved_zero = true;
    for (uint8_t value : result.reserved_zero) {
      reserved_zero = reserved_zero && value == 0U;
    }
    if (result.struct_size != sizeof(result) || result.changed > 1U ||
        !reserved_zero || result.next_deadline_scene_time_us <= scene_time_us ||
        result.next_deadline_scene_time_us > scene_time_us + kCadencePeriodUs ||
        result.next_deadline_scene_time_us < g_previous_deadlines[receiver] ||
        (result.changed == 0U && !g_local_frame_ready[receiver])) {
      g_last_error = kBrowserInvalidResult;
      return -1;
    }
    g_previous_deadlines[receiver] = result.next_deadline_scene_time_us;
    if (result.changed != 0U) {
      g_local_frame_ready[receiver] = true;
      wall_changed = true;
      const uint32_t strip_bytes = kLedsPerStrip * 3U;
      for (uint16_t local_strip = 0; local_strip < view.local_strips;
           ++local_strip) {
        const uint16_t global_strip = view.global_strip_offset +
            (view.reverse_local_strip_order
                 ? view.local_strips - 1U - local_strip
                 : local_strip);
        std::memcpy(
            g_wall_frame + static_cast<uint32_t>(global_strip) * strip_bytes,
            g_local_frames[receiver] +
                static_cast<uint32_t>(local_strip) * strip_bytes,
            strip_bytes);
      }
    }
  }

  g_changed = wall_changed ? 1U : 0U;
  g_last_error = kBrowserOk;
  return 0;
}

LG_BROWSER_EXPORT uintptr_t lg_browser_pixels() {
  return reinterpret_cast<uintptr_t>(g_wall_frame);
}

LG_BROWSER_EXPORT uint32_t lg_browser_pixels_size() { return kWallBytes; }

LG_BROWSER_EXPORT uint32_t lg_browser_width() { return kGlobalStrips; }

LG_BROWSER_EXPORT uint32_t lg_browser_height() { return kLedsPerStrip; }

LG_BROWSER_EXPORT uint32_t lg_browser_changed() { return g_changed; }

LG_BROWSER_EXPORT int32_t lg_browser_last_error() { return g_last_error; }

LG_BROWSER_EXPORT void lg_browser_cleanup() { cleanup_initialized(); }
