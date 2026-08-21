#include <unity.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>

#include "../fixtures/installation_profile_receiver_v1.hpp"
#include "../fixtures/receiver_optics_v1.hpp"
#include "ledgrid/installation_profile.hpp"
#include "ledgrid/receiver_optics.hpp"
#include "ledgrid/receiver_optics_coefficients_v1.hpp"
#include "ledgrid/sha256.hpp"

namespace {

constexpr std::size_t kPixels =
    ledgrid::kInstallationProfileReceiverPixelsV1;
constexpr std::size_t kRgbBytes = kPixels * 3;

struct OwnedProfile {
  std::array<std::uint8_t, kPixels> category{};
  std::array<std::uint8_t, kPixels> clearance{};
  std::array<std::uint8_t, kPixels> edge{};
  std::array<std::uint8_t, kPixels> region{};
  ledgrid::InstallationProfileViewV1 view{};

  OwnedProfile() {
    view.global_strip_count = ledgrid::kInstallationProfileGlobalStripsV1;
    view.leds_per_strip = ledgrid::kInstallationProfileLedsPerStripV1;
    view.strip_count = ledgrid::kInstallationProfileReceiverStripsV1;
    view.pixel_count = ledgrid::kInstallationProfileReceiverPixelsV1;
    view.category = category.data();
    view.clearance = clearance.data();
    view.obstacle_edge = edge.data();
    view.globe_region = region.data();
  }
};

ledgrid::InstallationProfileViewV1 installed_view(std::size_t logical_id) {
  const auto& source =
      ledgrid::installation_profile_fixture::kInstalledReceivers[logical_id];
  ledgrid::InstallationProfileViewV1 view{};
  const ledgrid::InstallationProfileReceiverExpectationV1 expectation{
      source.strip_origin, source.reversed_strip_order};
  TEST_ASSERT_TRUE(ledgrid::decode_installation_profile_receiver_v1(
      source.bytes, source.size, expectation, &view));
  return view;
}

std::array<std::uint8_t, kRgbBytes> solid_rgb(std::uint8_t red,
                                              std::uint8_t green,
                                              std::uint8_t blue) {
  std::array<std::uint8_t, kRgbBytes> output{};
  for (std::size_t pixel = 0; pixel < kPixels; ++pixel) {
    output[pixel * 3] = red;
    output[pixel * 3 + 1] = green;
    output[pixel * 3 + 2] = blue;
  }
  return output;
}

void assert_rgb(std::uint8_t red, std::uint8_t green, std::uint8_t blue,
                const std::uint8_t* actual) {
  TEST_ASSERT_EQUAL_UINT8(red, actual[0]);
  TEST_ASSERT_EQUAL_UINT8(green, actual[1]);
  TEST_ASSERT_EQUAL_UINT8(blue, actual[2]);
}

std::int64_t reference_unclamped(
    const std::int16_t row[3], const std::array<std::uint8_t, 3>& input) {
  const std::int64_t sum = static_cast<std::int64_t>(row[0]) * input[0] +
                           static_cast<std::int64_t>(row[1]) * input[1] +
                           static_cast<std::int64_t>(row[2]) * input[2];
  constexpr std::int64_t scale =
      std::int64_t{1} << ledgrid::receiver_optics_v1::kMatrixShift;
  const std::int64_t adjusted =
      sum + ledgrid::receiver_optics_v1::kMatrixRound;
  return adjusted >= 0 ? adjusted / scale
                       : -((-adjusted + scale - 1) / scale);
}

std::uint8_t reference_channel(
    const std::int16_t row[3], const std::array<std::uint8_t, 3>& input) {
  const std::int64_t rounded = reference_unclamped(row, input);
  return rounded <= 0 ? 0
                      : rounded >= 255 ? 255
                                       : static_cast<std::uint8_t>(rounded);
}

std::array<char, 65> sha256_hex(const std::uint8_t* bytes, std::size_t size) {
  std::uint8_t digest[32] = {};
  ledgrid::sha256(bytes, size, digest);
  constexpr char digits[] = "0123456789abcdef";
  std::array<char, 65> output{};
  for (std::size_t index = 0; index < 32; ++index) {
    output[index * 2] = digits[digest[index] >> 4U];
    output[index * 2 + 1] = digits[digest[index] & 0x0fU];
  }
  output[64] = '\0';
  return output;
}

void test_zero_strength_is_exact_unconditional_noop_and_invalid_is_atomic() {
  ledgrid::InstallationProfileViewV1 deliberately_invalid{};
  auto rgb = solid_rgb(7, 19, 231);
  const auto before = rgb;

  TEST_ASSERT_TRUE(ledgrid::apply_hue_shift_q8_8(
      rgb.data(), 1, deliberately_invalid, 0));
  TEST_ASSERT_TRUE(ledgrid::apply_hue_shift_q8_8(
      nullptr, 0, deliberately_invalid, 0));
  TEST_ASSERT_EQUAL_MEMORY(before.data(), rgb.data(), rgb.size());

  TEST_ASSERT_FALSE(ledgrid::apply_hue_shift_q8_8(
      rgb.data(), rgb.size(), deliberately_invalid, 257));
  TEST_ASSERT_EQUAL_MEMORY(before.data(), rgb.data(), rgb.size());

  OwnedProfile owned;
  owned.category[9] = 1;
  owned.clearance[9] = 1;
  TEST_ASSERT_FALSE(ledgrid::apply_hue_shift_q8_8(
      nullptr, rgb.size(), owned.view, 1));
  TEST_ASSERT_FALSE(ledgrid::apply_hue_shift_q8_8(
      rgb.data(), rgb.size() - 1, owned.view, 1));
  owned.view.global_strip_count = 31;
  TEST_ASSERT_FALSE(ledgrid::apply_hue_shift_q8_8(
      rgb.data(), rgb.size(), owned.view, 1));
  TEST_ASSERT_EQUAL_MEMORY(before.data(), rgb.data(), rgb.size());

  owned.view.global_strip_count = ledgrid::kInstallationProfileGlobalStripsV1;
  owned.category[kPixels - 1] = 3;
  TEST_ASSERT_FALSE(ledgrid::apply_hue_shift_q8_8(
      rgb.data(), rgb.size(), owned.view, 1));
  TEST_ASSERT_EQUAL_MEMORY(before.data(), rgb.data(), rgb.size());

  owned.category[kPixels - 1] = 0;
  const auto category_before = owned.category;
  TEST_ASSERT_FALSE(ledgrid::apply_hue_shift_q8_8(
      owned.category.data(), kRgbBytes, owned.view, 1));
  TEST_ASSERT_EQUAL_MEMORY(category_before.data(), owned.category.data(),
                           owned.category.size());
}

void test_hue_transform_targets_exact_obstacles_and_matches_matrix_rounding() {
  const auto view = installed_view(0);
  auto rgb = solid_rgb(255, 0, 0);
  const auto before = rgb;
  TEST_ASSERT_TRUE(ledgrid::apply_hue_shift_q8_8(
      rgb.data(), rgb.size(), view, 256));

  const std::array<std::uint8_t, 3> input{{255, 0, 0}};
  const auto& matrix = ledgrid::receiver_optics_v1::kHueShiftMatricesQ14[256];
  const std::array<std::uint8_t, 3> expected{{
      reference_channel(matrix[0], input), reference_channel(matrix[1], input),
      reference_channel(matrix[2], input)}};
  TEST_ASSERT_FALSE(expected == input);

  std::size_t changed = 0;
  std::size_t obstacles = 0;
  for (std::size_t pixel = 0; pixel < kPixels; ++pixel) {
    const std::uint8_t* const actual = rgb.data() + pixel * 3;
    if (view.category[pixel] == 0) {
      TEST_ASSERT_EQUAL_MEMORY(before.data() + pixel * 3, actual, 3);
    } else {
      ++obstacles;
      assert_rgb(expected[0], expected[1], expected[2], actual);
      if (std::memcmp(before.data() + pixel * 3, actual, 3) != 0) {
        ++changed;
      }
    }
  }
  TEST_ASSERT_GREATER_THAN(0, obstacles);
  TEST_ASSERT_EQUAL_UINT32(obstacles, changed);
}

void test_hue_rounding_clipping_and_strength_table_endpoints() {
  OwnedProfile owned;
  owned.category[0] = 1;
  owned.clearance[0] = 1;
  const std::array<std::array<std::uint8_t, 3>, 8> colors{{
      {{0, 0, 0}},       {{255, 255, 255}}, {{255, 0, 0}},
      {{0, 255, 0}},     {{0, 0, 255}},     {{1, 127, 254}},
      {{17, 91, 203}},   {{254, 3, 129}},
  }};
  constexpr std::array<std::uint16_t, 5> strengths{{1, 64, 128, 192, 256}};

  for (const auto strength : strengths) {
    const auto& matrix =
        ledgrid::receiver_optics_v1::kHueShiftMatricesQ14[strength];
    for (const auto& color : colors) {
      auto rgb = solid_rgb(11, 22, 33);
      rgb[0] = color[0];
      rgb[1] = color[1];
      rgb[2] = color[2];
      TEST_ASSERT_TRUE(ledgrid::apply_hue_shift_q8_8(
          rgb.data(), rgb.size(), owned.view, strength));
      for (std::size_t channel = 0; channel < 3; ++channel) {
        TEST_ASSERT_EQUAL_UINT8(reference_channel(matrix[channel], color),
                                rgb[channel]);
      }
      // A non-obstacle neighbor remains byte-exact for every strength.
      assert_rgb(11, 22, 33, rgb.data() + 3);
    }
  }

  const auto& identity = ledgrid::receiver_optics_v1::kHueShiftMatricesQ14[0];
  TEST_ASSERT_EQUAL_INT16(16384, identity[0][0]);
  TEST_ASSERT_EQUAL_INT16(16384, identity[1][1]);
  TEST_ASSERT_EQUAL_INT16(16384, identity[2][2]);
  TEST_ASSERT_EQUAL_INT16(0, identity[0][1]);
  TEST_ASSERT_EQUAL_INT16(0, identity[2][1]);
}

void test_generated_cross_language_rgb_vectors_match_exactly() {
  OwnedProfile owned;
  owned.category[0] = 1;
  owned.clearance[0] = 1;
  for (const auto& vector :
       ledgrid::golden_receiver_optics_v1::kRgbVectors) {
    auto rgb = solid_rgb(5, 7, 11);
    std::copy(vector.input_rgb, vector.input_rgb + 3, rgb.begin());
    TEST_ASSERT_TRUE(ledgrid::apply_hue_shift_q8_8(
        rgb.data(), rgb.size(), owned.view, vector.strength_q8_8));
    TEST_ASSERT_EQUAL_MEMORY(vector.expected_rgb, rgb.data(), 3);
    // Fixture generation also freezes the unclamped result. Recompute it from
    // the production coefficient row to detect table/fixture drift distinctly
    // from final clamping drift.
    const auto& matrix = ledgrid::receiver_optics_v1::kHueShiftMatricesQ14[
        vector.strength_q8_8];
    const std::array<std::uint8_t, 3> input{{
        vector.input_rgb[0], vector.input_rgb[1], vector.input_rgb[2]}};
    for (std::size_t channel = 0; channel < 3; ++channel) {
      TEST_ASSERT_EQUAL_INT16(vector.unclamped_rgb[channel],
                              reference_unclamped(matrix[channel], input));
      TEST_ASSERT_EQUAL_UINT8(reference_channel(matrix[channel], input),
                              vector.expected_rgb[channel]);
    }
    assert_rgb(5, 7, 11, rgb.data() + 3);
  }
}

void test_generated_installed_topology_digests_match_and_stitch_without_seams() {
  constexpr std::size_t global_pixels =
      ledgrid::kInstallationProfileGlobalStripsV1 *
      ledgrid::kInstallationProfileLedsPerStripV1;
  std::array<std::uint8_t, global_pixels * 3> stitched{};
  std::array<std::uint8_t, ledgrid::kInstallationProfileGlobalStripsV1> seen{};

  for (const auto& vector :
       ledgrid::golden_receiver_optics_v1::kInstalledTopologyVectors) {
    stitched.fill(0);
    seen.fill(0);
    for (std::size_t logical_id = 0; logical_id < 4; ++logical_id) {
      const auto view = installed_view(logical_id);
      std::array<std::uint8_t, kRgbBytes> rgb{};
      for (std::uint16_t local_strip = 0; local_strip < view.strip_count;
           ++local_strip) {
        const std::uint16_t global_strip = static_cast<std::uint16_t>(
            view.strip_origin +
            (view.reversed_strip_order ? view.strip_count - 1U - local_strip
                                       : local_strip));
        TEST_ASSERT_LESS_THAN(ledgrid::kInstallationProfileGlobalStripsV1,
                              global_strip);
        ++seen[global_strip];
        for (std::uint16_t led = 0; led < view.leds_per_strip; ++led) {
          const std::size_t local_pixel =
              static_cast<std::size_t>(local_strip) * view.leds_per_strip + led;
          std::uint8_t* const pixel = rgb.data() + local_pixel * 3;
          pixel[0] = static_cast<std::uint8_t>(global_strip * 37U + led * 11U +
                                               17U);
          pixel[1] = static_cast<std::uint8_t>(global_strip * 7U + led * 29U +
                                               73U);
          pixel[2] = static_cast<std::uint8_t>(global_strip * 53U + led * 3U +
                                               151U);
        }
      }
      TEST_ASSERT_TRUE(ledgrid::apply_hue_shift_q8_8(
          rgb.data(), rgb.size(), view, vector.strength_q8_8));
      const auto receiver_digest = sha256_hex(rgb.data(), rgb.size());
      TEST_ASSERT_EQUAL_STRING(vector.receiver_sha256[logical_id],
                               receiver_digest.data());

      for (std::uint16_t local_strip = 0; local_strip < view.strip_count;
           ++local_strip) {
        const std::uint16_t global_strip = static_cast<std::uint16_t>(
            view.strip_origin +
            (view.reversed_strip_order ? view.strip_count - 1U - local_strip
                                       : local_strip));
        const std::uint8_t* const local =
            rgb.data() + static_cast<std::size_t>(local_strip) *
                             view.leds_per_strip * 3;
        std::uint8_t* const global =
            stitched.data() + static_cast<std::size_t>(global_strip) *
                                  view.leds_per_strip * 3;
        std::copy(local, local + static_cast<std::size_t>(view.leds_per_strip) * 3,
                  global);
      }
    }
    for (const std::uint8_t count : seen) {
      TEST_ASSERT_EQUAL_UINT8(1, count);
    }
    const auto stitched_digest = sha256_hex(stitched.data(), stitched.size());
    TEST_ASSERT_EQUAL_STRING(vector.stitched_global_sha256,
                             stitched_digest.data());
  }
}

void test_canary_palette_and_class_helper_are_mechanically_distinct() {
  using Class = ledgrid::InstallationGeometryCanaryClassV1;
  std::array<bool, ledgrid::kInstallationGeometryCanaryClassCountV1> seen{};
  for (std::size_t index = 0;
       index < ledgrid::kInstallationGeometryCanaryPaletteV1.size(); ++index) {
    const auto& color = ledgrid::kInstallationGeometryCanaryPaletteV1[index];
    for (std::size_t prior = 0; prior < index; ++prior) {
      const auto& previous =
          ledgrid::kInstallationGeometryCanaryPaletteV1[prior];
      TEST_ASSERT_FALSE(color.red == previous.red &&
                        color.green == previous.green &&
                        color.blue == previous.blue);
    }
    seen[index] = true;
  }
  TEST_ASSERT_EQUAL_UINT8(0,
      ledgrid::kInstallationGeometryCanaryPaletteV1[0].red);
  TEST_ASSERT_EQUAL_UINT8(0,
      ledgrid::kInstallationGeometryCanaryPaletteV1[0].green);
  TEST_ASSERT_EQUAL_UINT8(0,
      ledgrid::kInstallationGeometryCanaryPaletteV1[0].blue);

  TEST_ASSERT_EQUAL_UINT8(static_cast<std::uint8_t>(Class::Empty),
      static_cast<std::uint8_t>(ledgrid::installation_geometry_canary_class_v1(
          0, 0, 0, 0)));
  TEST_ASSERT_EQUAL_UINT8(static_cast<std::uint8_t>(Class::ClearanceOnly),
      static_cast<std::uint8_t>(ledgrid::installation_geometry_canary_class_v1(
          0, 1, 0, 0)));
  TEST_ASSERT_EQUAL_UINT8(static_cast<std::uint8_t>(Class::FoliageInterior),
      static_cast<std::uint8_t>(ledgrid::installation_geometry_canary_class_v1(
          1, 1, 0, 0)));
  TEST_ASSERT_EQUAL_UINT8(static_cast<std::uint8_t>(Class::FoliageEdge),
      static_cast<std::uint8_t>(ledgrid::installation_geometry_canary_class_v1(
          1, 1, 1, 0)));
  for (std::uint8_t region = 1; region <= 7; ++region) {
    const auto interior = ledgrid::installation_geometry_canary_class_v1(
        2, 1, 0, region);
    const auto edge = ledgrid::installation_geometry_canary_class_v1(
        2, 1, 1, region);
    const auto* interior_color =
        ledgrid::installation_geometry_canary_color_v1(interior);
    const auto* edge_color =
        ledgrid::installation_geometry_canary_color_v1(edge);
    TEST_ASSERT_NOT_NULL(interior_color);
    TEST_ASSERT_NOT_NULL(edge_color);
    TEST_ASSERT_EQUAL_UINT8(interior_color->red, edge_color->red);
    TEST_ASSERT_EQUAL_UINT8(interior_color->green, edge_color->green);
    TEST_ASSERT_EQUAL_UINT8(
        ledgrid::kInstallationGeometryCanaryInteriorBlueV1,
        interior_color->blue);
    TEST_ASSERT_EQUAL_UINT8(ledgrid::kInstallationGeometryCanaryEdgeBlueV1,
                            edge_color->blue);
  }
  TEST_ASSERT_EQUAL_UINT8(static_cast<std::uint8_t>(Class::Invalid),
      static_cast<std::uint8_t>(ledgrid::installation_geometry_canary_class_v1(
          0, 1, 1, 0)));
  TEST_ASSERT_EQUAL_UINT8(static_cast<std::uint8_t>(Class::Invalid),
      static_cast<std::uint8_t>(ledgrid::installation_geometry_canary_class_v1(
          2, 1, 0, 0)));
  TEST_ASSERT_NULL(ledgrid::installation_geometry_canary_color_v1(
      Class::Invalid));
  TEST_ASSERT_TRUE(std::all_of(seen.begin(), seen.end(), [](bool value) {
    return value;
  }));
}

void test_canary_covers_every_semantic_class_in_all_installed_views() {
  std::array<bool, ledgrid::kInstallationGeometryCanaryClassCountV1> seen{};
  for (std::size_t logical_id = 0; logical_id < 4; ++logical_id) {
    const auto view = installed_view(logical_id);
    std::array<std::uint8_t, kRgbBytes> rgb{};
    rgb.fill(0xa5);
    TEST_ASSERT_TRUE(ledgrid::render_installation_geometry_canary(
        view, rgb.data(), rgb.size()));
    for (std::size_t pixel = 0; pixel < kPixels; ++pixel) {
      const auto semantic_class = ledgrid::installation_geometry_canary_class_v1(
          view.category[pixel], view.clearance[pixel],
          view.obstacle_edge[pixel], view.globe_region[pixel]);
      const auto index = static_cast<std::uint8_t>(semantic_class);
      TEST_ASSERT_LESS_THAN(ledgrid::kInstallationGeometryCanaryClassCountV1,
                            index);
      seen[index] = true;
      const auto& expected =
          ledgrid::kInstallationGeometryCanaryPaletteV1[index];
      assert_rgb(expected.red, expected.green, expected.blue,
                 rgb.data() + pixel * 3);
    }
  }
  for (std::size_t index = 0; index < seen.size(); ++index) {
    TEST_ASSERT_TRUE_MESSAGE(seen[index],
                             "canonical geometry must cover every canary class");
  }
}

void test_canary_is_read_only_atomic_and_rejects_output_aliases() {
  const auto& fixture =
      ledgrid::installation_profile_fixture::kInstalledReceivers[2];
  std::vector<std::uint8_t> encoded(fixture.bytes,
                                    fixture.bytes + fixture.size);
  const auto encoded_before = encoded;
  ledgrid::InstallationProfileViewV1 view{};
  const ledgrid::InstallationProfileReceiverExpectationV1 expectation{
      fixture.strip_origin, fixture.reversed_strip_order};
  TEST_ASSERT_TRUE(ledgrid::decode_installation_profile_receiver_v1(
      encoded.data(), encoded.size(), expectation, &view));
  std::array<std::uint8_t, kRgbBytes> rgb{};
  rgb.fill(0x6d);
  TEST_ASSERT_TRUE(ledgrid::render_installation_geometry_canary(
      view, rgb.data(), rgb.size()));
  TEST_ASSERT_EQUAL_MEMORY(encoded_before.data(), encoded.data(), encoded.size());

  OwnedProfile malformed;
  auto untouched = solid_rgb(9, 8, 7);
  const auto before = untouched;
  malformed.clearance[kPixels - 1] = 2;
  TEST_ASSERT_FALSE(ledgrid::render_installation_geometry_canary(
      malformed.view, untouched.data(), untouched.size()));
  TEST_ASSERT_EQUAL_MEMORY(before.data(), untouched.data(), untouched.size());
  TEST_ASSERT_FALSE(ledgrid::render_installation_geometry_canary(
      malformed.view, untouched.data(), untouched.size() - 1));
  TEST_ASSERT_EQUAL_MEMORY(before.data(), untouched.data(), untouched.size());

  // The advertised span overlaps the category plane and must reject before a
  // byte is written; callers may never use the profile as their output plane.
  malformed.clearance[kPixels - 1] = 0;
  TEST_ASSERT_FALSE(ledgrid::render_installation_geometry_canary(
      malformed.view, malformed.category.data(), kRgbBytes));
}

void test_installed_orientation_mapping_stitches_each_global_strip_once() {
  std::array<std::uint8_t, 32> owner{};
  std::array<std::uint8_t, 32> local_index{};
  owner.fill(0xff);
  for (std::size_t logical_id = 0; logical_id < 4; ++logical_id) {
    const auto view = installed_view(logical_id);
    for (std::uint16_t local_strip = 0; local_strip < view.strip_count;
         ++local_strip) {
      const std::uint16_t global_strip = static_cast<std::uint16_t>(
          view.strip_origin +
          (view.reversed_strip_order ? view.strip_count - 1U - local_strip
                                     : local_strip));
      TEST_ASSERT_LESS_THAN(32, global_strip);
      TEST_ASSERT_EQUAL_UINT8(0xff, owner[global_strip]);
      owner[global_strip] = static_cast<std::uint8_t>(logical_id);
      local_index[global_strip] = static_cast<std::uint8_t>(local_strip);
    }
  }
  for (std::size_t strip = 0; strip < 32; ++strip) {
    TEST_ASSERT_NOT_EQUAL(0xff, owner[strip]);
  }

  // Exact installed boundary evidence: the right-side logical receivers are
  // native-reversed without changing their independent logical identities.
  TEST_ASSERT_EQUAL_UINT8(0, owner[7]);
  TEST_ASSERT_EQUAL_UINT8(1, owner[8]);
  TEST_ASSERT_EQUAL_UINT8(1, owner[15]);
  TEST_ASSERT_EQUAL_UINT8(3, owner[16]);
  TEST_ASSERT_EQUAL_UINT8(3, owner[23]);
  TEST_ASSERT_EQUAL_UINT8(2, owner[24]);
  TEST_ASSERT_EQUAL_UINT8(7, local_index[7]);
  TEST_ASSERT_EQUAL_UINT8(0, local_index[8]);
  TEST_ASSERT_EQUAL_UINT8(7, local_index[16]);
  TEST_ASSERT_EQUAL_UINT8(0, local_index[23]);
  TEST_ASSERT_EQUAL_UINT8(7, local_index[24]);
}

struct TimingSummary {
  bool success;
  double mean_us;
  double p95_us;
  double p99_us;
  double max_us;
};

template <typename Operation>
TimingSummary benchmark(std::size_t samples, Operation operation) {
  std::vector<double> timings;
  timings.reserve(samples);
  bool success = true;
  for (std::size_t sample = 0; sample < samples; ++sample) {
    const auto begin = std::chrono::steady_clock::now();
    success = operation() && success;
    const auto end = std::chrono::steady_clock::now();
    timings.push_back(
        std::chrono::duration<double, std::micro>(end - begin).count());
  }
  std::sort(timings.begin(), timings.end());
  double total = 0.0;
  for (const double value : timings) {
    total += value;
  }
  const auto percentile = [&](double value) {
    const std::size_t index = static_cast<std::size_t>(
        value * static_cast<double>(timings.size() - 1));
    return timings[index];
  };
  return {success, total / timings.size(), percentile(0.95), percentile(0.99),
          timings.back()};
}

void test_workstation_proxy_default_and_maximum_transform_timing() {
  const auto view = installed_view(0);
  auto rgb = solid_rgb(201, 73, 19);
  constexpr std::size_t samples = 1000;
  const auto zero = benchmark(samples, [&]() {
    return ledgrid::apply_hue_shift_q8_8(rgb.data(), rgb.size(), view, 0);
  });
  const auto maximum = benchmark(samples, [&]() {
    return ledgrid::apply_hue_shift_q8_8(rgb.data(), rgb.size(), view, 256);
  });
  TEST_ASSERT_TRUE(zero.success);
  TEST_ASSERT_TRUE(maximum.success);
  std::printf(
      "receiver optics workstation proxy (8x138, us): "
      "zero mean=%.3f p95=%.3f p99=%.3f max=%.3f; "
      "max mean=%.3f p95=%.3f p99=%.3f max=%.3f\n",
      zero.mean_us, zero.p95_us, zero.p99_us, zero.max_us, maximum.mean_us,
      maximum.p95_us, maximum.p99_us, maximum.max_us);
  // This is a deliberately loose desktop-native structural regression bound,
  // not ESP32 timing acceptance. Physical receiver cadence remains a separate
  // gate.
  TEST_ASSERT_LESS_THAN_FLOAT(4000.0f,
                              static_cast<float>(maximum.p95_us));
}

}  // namespace

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_zero_strength_is_exact_unconditional_noop_and_invalid_is_atomic);
  RUN_TEST(test_hue_transform_targets_exact_obstacles_and_matches_matrix_rounding);
  RUN_TEST(test_hue_rounding_clipping_and_strength_table_endpoints);
  RUN_TEST(test_generated_cross_language_rgb_vectors_match_exactly);
  RUN_TEST(test_generated_installed_topology_digests_match_and_stitch_without_seams);
  RUN_TEST(test_canary_palette_and_class_helper_are_mechanically_distinct);
  RUN_TEST(test_canary_covers_every_semantic_class_in_all_installed_views);
  RUN_TEST(test_canary_is_read_only_atomic_and_rejects_output_aliases);
  RUN_TEST(test_installed_orientation_mapping_stitches_each_global_strip_once);
  RUN_TEST(test_workstation_proxy_default_and_maximum_transform_timing);
  return UNITY_END();
}
