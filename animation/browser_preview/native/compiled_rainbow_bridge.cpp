#include "ledgrid/receiver_runtime.hpp"

#include <cstddef>
#include <cstdint>
#include <cstring>

#include <emscripten/emscripten.h>

namespace {

constexpr std::uint16_t kGlobalStrips = 33;
constexpr std::uint16_t kLedsPerStrip = 138;
constexpr std::uint32_t kWallBytes =
    static_cast<std::uint32_t>(kGlobalStrips) * kLedsPerStrip * 3U;

enum BrowserError : std::int32_t {
  kBrowserOk = 0,
  kBrowserBadGeometry = 1,
  kBrowserBadParameters = 2,
  kBrowserRenderFailed = 3,
};

std::uint8_t g_wall_frame[kWallBytes];
ledgrid::LocalBackgroundParameters g_parameters{};
std::uint8_t g_changed = 0;
std::int32_t g_last_error = kBrowserOk;
bool g_initialized = false;
bool g_frame_ready = false;

}  // namespace

#define LG_BROWSER_EXPORT extern "C" EMSCRIPTEN_KEEPALIVE

LG_BROWSER_EXPORT int lg_browser_init(std::uint32_t global_strips,
                                      std::uint32_t leds_per_strip) {
  if (global_strips != kGlobalStrips || leds_per_strip != kLedsPerStrip) {
    g_last_error = kBrowserBadGeometry;
    return -1;
  }
  std::memset(g_wall_frame, 0, sizeof(g_wall_frame));
  g_parameters = ledgrid::LocalBackgroundParameters{};
  g_parameters.global_strip_offset = 0;
  g_parameters.reverse_local_strip_order = false;
  g_changed = 0;
  g_frame_ready = false;
  g_initialized = true;
  g_last_error = kBrowserOk;
  return 0;
}

LG_BROWSER_EXPORT int lg_browser_set_parameters(
    std::uint32_t preferred_cadence_hz, std::uint32_t common_seed) {
  if (!g_initialized || preferred_cadence_hz < ledgrid::kMinLocalCadenceHz ||
      preferred_cadence_hz > ledgrid::kMaxLocalCadenceHz) {
    g_last_error = kBrowserBadParameters;
    return -1;
  }
  g_parameters.preferred_cadence_hz =
      static_cast<std::uint16_t>(preferred_cadence_hz);
  g_parameters.common_seed = common_seed;
  g_last_error = kBrowserOk;
  return 0;
}

LG_BROWSER_EXPORT int lg_browser_render(std::uint32_t scene_time_low,
                                        std::uint32_t scene_time_high,
                                        std::uint32_t /*frame_index_low*/,
                                        std::uint32_t /*frame_index_high*/) {
  if (!g_initialized) {
    g_last_error = kBrowserRenderFailed;
    return -1;
  }
  const std::uint64_t scene_time_us =
      (static_cast<std::uint64_t>(scene_time_high) << 32U) | scene_time_low;
  std::uint8_t next[kWallBytes];
  if (!ledgrid::render_compiled_rainbow(
          scene_time_us, g_parameters, ledgrid::kQ8_8One, kGlobalStrips,
          kLedsPerStrip, next, sizeof(next))) {
    g_last_error = kBrowserRenderFailed;
    return -1;
  }
  g_changed = !g_frame_ready || std::memcmp(g_wall_frame, next, sizeof(next)) != 0;
  if (g_changed != 0U) {
    std::memcpy(g_wall_frame, next, sizeof(next));
    g_frame_ready = true;
  }
  g_last_error = kBrowserOk;
  return 0;
}

LG_BROWSER_EXPORT std::uintptr_t lg_browser_pixels() {
  return reinterpret_cast<std::uintptr_t>(g_wall_frame);
}

LG_BROWSER_EXPORT std::uint32_t lg_browser_pixels_size() { return kWallBytes; }

LG_BROWSER_EXPORT std::uint32_t lg_browser_width() { return kGlobalStrips; }

LG_BROWSER_EXPORT std::uint32_t lg_browser_height() { return kLedsPerStrip; }

LG_BROWSER_EXPORT std::uint32_t lg_browser_changed() { return g_changed; }

LG_BROWSER_EXPORT std::int32_t lg_browser_last_error() { return g_last_error; }

LG_BROWSER_EXPORT void lg_browser_cleanup() {
  g_initialized = false;
  g_frame_ready = false;
  g_changed = 0;
}
