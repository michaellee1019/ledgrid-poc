#include "ledgrid/receiver_optics.hpp"

#include <cstddef>
#include <cstdint>
#include <limits>

#include "ledgrid/receiver_optics_coefficients_v1.hpp"

namespace ledgrid {
namespace {

constexpr std::size_t kRgbChannels = 3;

bool valid_receiver_geometry(const InstallationProfileViewV1& profile) {
  return profile.global_strip_count == kInstallationProfileGlobalStripsV1 &&
         profile.leds_per_strip == kInstallationProfileLedsPerStripV1 &&
         profile.strip_count == kInstallationProfileReceiverStripsV1 &&
         profile.pixel_count == kInstallationProfileReceiverPixelsV1 &&
         profile.strip_origin % kInstallationProfileReceiverStripsV1 == 0 &&
         profile.strip_origin + profile.strip_count <=
             profile.global_strip_count;
}

bool valid_output(const InstallationProfileViewV1& profile,
                  const std::uint8_t* rgb, std::size_t rgb_bytes) {
  // Exact receiver geometry fixes pixel_count at 1,104 before multiplication,
  // so the RGB byte count is representable on every supported target.
  if (rgb == nullptr || !valid_receiver_geometry(profile)) {
    return false;
  }
  return rgb_bytes ==
         static_cast<std::size_t>(profile.pixel_count) * kRgbChannels;
}

bool ranges_overlap(const void* left, std::size_t left_size, const void* right,
                    std::size_t right_size) {
  if (left == nullptr || right == nullptr || left_size == 0 || right_size == 0) {
    return false;
  }
  const auto left_begin = reinterpret_cast<std::uintptr_t>(left);
  const auto right_begin = reinterpret_cast<std::uintptr_t>(right);
  if (left_begin > std::numeric_limits<std::uintptr_t>::max() - left_size ||
      right_begin > std::numeric_limits<std::uintptr_t>::max() - right_size) {
    return true;
  }
  const auto left_end = left_begin + left_size;
  const auto right_end = right_begin + right_size;
  return left_begin < right_end && right_begin < left_end;
}

bool output_is_separate(const InstallationProfileViewV1& profile,
                        const std::uint8_t* rgb, std::size_t rgb_bytes) {
  const std::size_t pixels = profile.pixel_count;
  if (ranges_overlap(rgb, rgb_bytes, profile.encoded, profile.encoded_size)) {
    return false;
  }
  const void* const sections[] = {
      profile.category,      profile.clearance,   profile.foliage_edge,
      profile.globe_edge,    profile.obstacle_edge,
      profile.globe_region,  profile.distance,    profile.normal_x,
      profile.normal_y,
  };
  for (const void* section : sections) {
    if (ranges_overlap(rgb, rgb_bytes, section, pixels)) {
      return false;
    }
  }
  return true;
}

std::uint8_t quantize_q14(std::int64_t sum) {
  constexpr std::int64_t kScale = std::int64_t{1}
                                  << receiver_optics_v1::kMatrixShift;
  const std::int64_t adjusted =
      sum + static_cast<std::int64_t>(receiver_optics_v1::kMatrixRound);
  std::int64_t value = 0;
  if (adjusted >= 0) {
    value = adjusted / kScale;
  } else {
    // C++ division truncates toward zero; compensate to implement floor for
    // negative values without depending on implementation-defined shifts.
    value = -((-adjusted + kScale - 1) / kScale);
  }
  if (value <= 0) {
    return 0;
  }
  if (value >= 255) {
    return 255;
  }
  return static_cast<std::uint8_t>(value);
}

}  // namespace

bool apply_hue_shift_q8_8(std::uint8_t* rgb, std::size_t rgb_bytes,
                         const InstallationProfileViewV1& profile,
                         std::uint16_t strength_q8_8) {
  if (strength_q8_8 == 0) {
    return true;
  }
  if (strength_q8_8 > receiver_optics_v1::kHueStrengthMax ||
      !valid_output(profile, rgb, rgb_bytes) || profile.category == nullptr ||
      !output_is_separate(profile, rgb, rgb_bytes)) {
    return false;
  }

  // Validate the entire category plane before the first output mutation.
  for (std::size_t pixel = 0; pixel < profile.pixel_count; ++pixel) {
    if (profile.category[pixel] > 2) {
      return false;
    }
  }

  const auto& matrix =
      receiver_optics_v1::kHueShiftMatricesQ14[strength_q8_8];
  for (std::size_t pixel = 0; pixel < profile.pixel_count; ++pixel) {
    if (profile.category[pixel] == 0) {
      continue;
    }
    std::uint8_t* const channels = rgb + pixel * kRgbChannels;
    const std::uint8_t red = channels[0];
    const std::uint8_t green = channels[1];
    const std::uint8_t blue = channels[2];
    for (std::size_t output = 0; output < kRgbChannels; ++output) {
      const std::int64_t sum =
          static_cast<std::int64_t>(matrix[output][0]) * red +
          static_cast<std::int64_t>(matrix[output][1]) * green +
          static_cast<std::int64_t>(matrix[output][2]) * blue;
      channels[output] = quantize_q14(sum);
    }
  }
  return true;
}

bool render_installation_geometry_canary(
    const InstallationProfileViewV1& profile, std::uint8_t* rgb,
    std::size_t rgb_bytes) {
  if (!valid_output(profile, rgb, rgb_bytes) || profile.category == nullptr ||
      profile.clearance == nullptr || profile.obstacle_edge == nullptr ||
      profile.globe_region == nullptr ||
      !output_is_separate(profile, rgb, rgb_bytes)) {
    return false;
  }

  // Classify all pixels first so malformed views cannot partially overwrite
  // the output. A decoded LGIP view already satisfies these invariants; the
  // checks preserve the standalone primitive's atomic failure contract.
  for (std::size_t pixel = 0; pixel < profile.pixel_count; ++pixel) {
    if (installation_geometry_canary_class_v1(
            profile.category[pixel], profile.clearance[pixel],
            profile.obstacle_edge[pixel], profile.globe_region[pixel]) ==
        InstallationGeometryCanaryClassV1::Invalid) {
      return false;
    }
  }

  for (std::size_t pixel = 0; pixel < profile.pixel_count; ++pixel) {
    const auto semantic_class = installation_geometry_canary_class_v1(
        profile.category[pixel], profile.clearance[pixel],
        profile.obstacle_edge[pixel], profile.globe_region[pixel]);
    const ReceiverOpticsRgbV1& color =
        kInstallationGeometryCanaryPaletteV1[static_cast<std::uint8_t>(
            semantic_class)];
    std::uint8_t* const output = rgb + pixel * kRgbChannels;
    output[0] = color.red;
    output[1] = color.green;
    output[2] = color.blue;
  }
  return true;
}

}  // namespace ledgrid
