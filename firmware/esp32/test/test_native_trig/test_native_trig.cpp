#include <unity.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <numeric>
#include <vector>

#include "ledgrid/native_trig.hpp"

// Exercise the unchanged production payload against both helper tables. This
// only compiles it for the portable test; the shipped native bundle is untouched.
#define LG_HOST_PREVIEW 1
#include "../../../../animation/plugins/native_aurora/native/background.cpp"

namespace {

// The receiver helper before the lookup optimization. Keep its expression and
// double precision intact: every ABI input must retain the same rounded value.
std::int16_t reference_sin(std::uint16_t phase) {
  constexpr double kTau = 6.283185307179586476925286766559;
  return static_cast<std::int16_t>(
      std::nearbyint(std::sin(static_cast<double>(phase) * kTau / 65536.0) *
                    32767.0));
}

void test_every_phase_matches_original_receiver_helpers() {
  for (std::uint32_t phase = 0; phase < 65536U; ++phase) {
    const auto input = static_cast<std::uint16_t>(phase);
    TEST_ASSERT_EQUAL_INT16(reference_sin(input), ledgrid::native_sin_q15(input));
    TEST_ASSERT_EQUAL_INT16(
        reference_sin(static_cast<std::uint16_t>(phase + 16384U)),
        ledgrid::native_cos_q15(input));
  }
}

void test_cardinals_and_wrap_preserve_abi_range() {
  TEST_ASSERT_EQUAL_INT16(0, ledgrid::native_sin_q15(0));
  TEST_ASSERT_EQUAL_INT16(32767, ledgrid::native_sin_q15(16384));
  TEST_ASSERT_EQUAL_INT16(0, ledgrid::native_sin_q15(32768));
  TEST_ASSERT_EQUAL_INT16(-32767, ledgrid::native_sin_q15(49152));
  TEST_ASSERT_EQUAL_INT16(-3, ledgrid::native_sin_q15(65535));
  TEST_ASSERT_EQUAL_INT16(32767, ledgrid::native_cos_q15(65535));
}

void test_production_aurora_frames_cache_and_state_match_original_helper() {
  const bool benchmark = std::getenv("LEDGRID_BENCH_NATIVE_TRIG") != nullptr;
  const auto* api = ledgrid_native_background_v2();
  const ledgrid_native_helpers_v2 helpers[] = {
      {LEDGRID_NATIVE_BACKGROUND_ABI_VERSION, sizeof(ledgrid_native_helpers_v2),
       nullptr, nullptr, reference_sin, nullptr},
      {LEDGRID_NATIVE_BACKGROUND_ABI_VERSION, sizeof(ledgrid_native_helpers_v2),
       nullptr, nullptr, ledgrid::native_sin_q15, ledgrid::native_cos_q15}};
  for (unsigned receiver = 0; receiver < 5; ++receiver) {
    const std::uint16_t strips = receiver == 4 ? 1 : 8;
    const std::uint16_t offset = receiver * 8;
    const std::uint8_t reverse = receiver == 2 || receiver == 3;
    AuroraState states[2] = {};
    std::array<std::uint8_t, 8 * 138 * 3> frames[2] = {};
    ledgrid_native_init_v2 init = {};
    init.abi_version = LEDGRID_NATIVE_BACKGROUND_ABI_VERSION;
    init.struct_size = sizeof(init);
    init.global_strips = 33;
    init.local_strips = strips;
    init.leds_per_strip = 138;
    init.global_strip_offset = offset;
    init.reverse_local_strip_order = reverse;
    init.pixel_count = strips * 138;
    for (unsigned variant = 0; variant < 2; ++variant) {
      init.helpers = &helpers[variant];
      TEST_ASSERT_EQUAL(LEDGRID_NATIVE_BACKGROUND_OK,
                        api->initialize(&states[variant], &init));
    }
    ledgrid_native_parameter_v2 parameters[3] = {};
    parameters[0].id = 0;
    parameters[0].type = LEDGRID_NATIVE_PARAMETER_FLOAT32;
    parameters[0].value.real = 0.62F;
    parameters[1].id = 1;
    parameters[1].type = LEDGRID_NATIVE_PARAMETER_INT32;
    parameters[1].value.integer = 4201;
    parameters[2].id = 2;
    parameters[2].type = LEDGRID_NATIVE_PARAMETER_FLOAT32;
    parameters[2].value.real = 30.0F;
    ledgrid_native_vibe_v2 vibe = {};
    vibe.struct_size = sizeof(vibe);
    const std::uint8_t ember[3][3] = {{18, 3, 2}, {156, 42, 14}, {255, 202, 92}};
    std::memcpy(vibe.palette[0], ember[0], 3);
    std::memcpy(vibe.palette[3], ember[1], 3);
    std::memcpy(vibe.palette[5], ember[2], 3);
    ledgrid_native_profile_view_v2 profile = {};
    profile.struct_size = sizeof(profile);
    profile.global_strips = 33;
    profile.local_strips = strips;
    profile.leds_per_strip = 138;
    profile.global_strip_offset = offset;
    profile.reverse_local_strip_order = reverse;
    ledgrid_native_context_v2 context = {};
    context.abi_version = LEDGRID_NATIVE_BACKGROUND_ABI_VERSION;
    context.struct_size = sizeof(context);
    context.parameters = parameters;
    context.parameter_count = 3;
    context.vibe = &vibe;
    context.profile = &profile;
    unsigned changed = 0;
    std::vector<double> samples[2][2];
    for (unsigned frame = 0; frame < 300; ++frame) {
      if (frame == 150) {
        parameters[0].value.real = 1.0F;
        parameters[1].value.integer = 2147483647;
        parameters[2].value.real = 60.0F;
        vibe.palette[3][1] = 255;
      }
      ledgrid_native_render_result_v2 results[2] = {};
      for (unsigned variant = 0; variant < 2; ++variant) {
        if (frame == 0 || frame == 150) {
          TEST_ASSERT_EQUAL(LEDGRID_NATIVE_BACKGROUND_OK,
                            api->update_context(&states[variant], &context));
        }
        ledgrid_native_render_request_v2 request = {};
        request.abi_version = LEDGRID_NATIVE_BACKGROUND_ABI_VERSION;
        request.struct_size = sizeof(request);
        request.unscaled_scene_time_us = frame * 1000000ULL / 150;
        request.scaled_scene_time_us = request.unscaled_scene_time_us * 5 / 4;
        request.frame_index = frame;
        request.rgb_output = frames[variant].data();
        request.rgb_output_size = strips * 138 * 3;
        results[variant].struct_size = sizeof(results[variant]);
        const auto start = std::chrono::steady_clock::now();
        const int status = api->render(&states[variant], &request, &results[variant]);
        const auto end = std::chrono::steady_clock::now();
        TEST_ASSERT_EQUAL(LEDGRID_NATIVE_BACKGROUND_OK, status);
        if (benchmark) {
          samples[variant][results[variant].changed].push_back(
              std::chrono::duration<double, std::micro>(end - start).count());
        }
      }
      TEST_ASSERT_EQUAL_MEMORY(frames[0].data(), frames[1].data(), strips * 138 * 3);
      TEST_ASSERT_EQUAL_MEMORY(&results[0], &results[1], sizeof(results[0]));
      // Only the callback-table addresses differ between the two module states.
      AuroraState comparable = states[1];
      comparable.helpers = states[0].helpers;
      TEST_ASSERT_EQUAL_MEMORY(&states[0], &comparable, sizeof(comparable));
      changed += results[1].changed;
    }
    TEST_ASSERT_GREATER_THAN_UINT(0, changed);
    TEST_ASSERT_LESS_THAN_UINT(300, changed);
    if (benchmark) {
      for (unsigned variant = 0; variant < 2; ++variant) {
        for (unsigned tick = 0; tick < 2; ++tick) {
          auto& times = samples[variant][tick];
          std::sort(times.begin(), times.end());
          const double mean = std::accumulate(times.begin(), times.end(), 0.0) / times.size();
          std::printf("HOST_ONLY receiver=%u helper=%s changed=%u samples=%zu "
                      "changed_ratio=%.3f mean_us=%.3f p95_us=%.3f p99_us=%.3f max_us=%.3f\n",
                      receiver, variant == 0 ? "libm" : "lookup", tick, times.size(),
                      changed / 300.0, mean, times[times.size() * 95 / 100],
                      times[times.size() * 99 / 100], times.back());
        }
      }
    }
    for (auto& state : states) {
      TEST_ASSERT_EQUAL(LEDGRID_NATIVE_BACKGROUND_OK, api->cleanup(&state));
    }
  }
}

}  // namespace

void setUp() {}
void tearDown() {}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_every_phase_matches_original_receiver_helpers);
  RUN_TEST(test_cardinals_and_wrap_preserve_abi_range);
  RUN_TEST(test_production_aurora_frames_cache_and_state_match_original_helper);
  return UNITY_END();
}
