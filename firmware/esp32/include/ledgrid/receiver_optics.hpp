#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

#include "ledgrid/installation_profile.hpp"

namespace ledgrid {

// Stable semantic classes for the installation-geometry diagnostic. Edge
// variants retain their class identity in red/green and differ only in blue.
enum class InstallationGeometryCanaryClassV1 : std::uint8_t {
  Empty = 0,
  ClearanceOnly = 1,
  FoliageInterior = 2,
  FoliageEdge = 3,
  Globe1Interior = 4,
  Globe1Edge = 5,
  Globe2Interior = 6,
  Globe2Edge = 7,
  Globe3Interior = 8,
  Globe3Edge = 9,
  Globe4Interior = 10,
  Globe4Edge = 11,
  Globe5Interior = 12,
  Globe5Edge = 13,
  Globe6Interior = 14,
  Globe6Edge = 15,
  Globe7Interior = 16,
  Globe7Edge = 17,
  Invalid = 0xff,
};

struct ReceiverOpticsRgbV1 {
  std::uint8_t red;
  std::uint8_t green;
  std::uint8_t blue;
};

inline constexpr std::size_t kInstallationGeometryCanaryClassCountV1 = 18;
inline constexpr std::uint8_t kInstallationGeometryCanaryInteriorBlueV1 = 32;
inline constexpr std::uint8_t kInstallationGeometryCanaryEdgeBlueV1 = 255;

// Clearance is deliberately dimmer than every semantic core. Semantic
// interior/edge pairs have identical red/green identity and the predictable
// blue-channel rule declared above. All eighteen RGB triples are unique.
inline constexpr std::array<ReceiverOpticsRgbV1,
                            kInstallationGeometryCanaryClassCountV1>
    kInstallationGeometryCanaryPaletteV1{{
        {0, 0, 0},       // Empty.
        {8, 12, 24},     // ClearanceOnly.
        {32, 180, 32},   // FoliageInterior.
        {32, 180, 255},  // FoliageEdge.
        {224, 48, 32},   // Globe1Interior.
        {224, 48, 255},  // Globe1Edge.
        {224, 112, 32},  // Globe2Interior.
        {224, 112, 255}, // Globe2Edge.
        {208, 176, 32},  // Globe3Interior.
        {208, 176, 255}, // Globe3Edge.
        {48, 192, 32},   // Globe4Interior.
        {48, 192, 255},  // Globe4Edge.
        {48, 144, 32},   // Globe5Interior.
        {48, 144, 255},  // Globe5Edge.
        {96, 80, 32},    // Globe6Interior.
        {96, 80, 255},   // Globe6Edge.
        {176, 48, 32},   // Globe7Interior.
        {176, 48, 255},  // Globe7Edge.
    }};

constexpr InstallationGeometryCanaryClassV1
installation_geometry_canary_class_v1(std::uint8_t category,
                                      std::uint8_t clearance,
                                      std::uint8_t obstacle_edge,
                                      std::uint8_t globe_region) {
  if (clearance > 1 || obstacle_edge > 1) {
    return InstallationGeometryCanaryClassV1::Invalid;
  }
  if (category == 0) {
    if (obstacle_edge != 0 || globe_region != 0) {
      return InstallationGeometryCanaryClassV1::Invalid;
    }
    return clearance != 0
               ? InstallationGeometryCanaryClassV1::ClearanceOnly
               : InstallationGeometryCanaryClassV1::Empty;
  }
  if (clearance == 0) {
    return InstallationGeometryCanaryClassV1::Invalid;
  }
  if (category == 1) {
    if (globe_region != 0) {
      return InstallationGeometryCanaryClassV1::Invalid;
    }
    return obstacle_edge != 0
               ? InstallationGeometryCanaryClassV1::FoliageEdge
               : InstallationGeometryCanaryClassV1::FoliageInterior;
  }
  if (category != 2 || globe_region < 1 || globe_region > 7) {
    return InstallationGeometryCanaryClassV1::Invalid;
  }
  const auto index = static_cast<std::uint8_t>(
      4U + 2U * (globe_region - 1U) + (obstacle_edge != 0 ? 1U : 0U));
  return static_cast<InstallationGeometryCanaryClassV1>(index);
}

constexpr const ReceiverOpticsRgbV1* installation_geometry_canary_color_v1(
    InstallationGeometryCanaryClassV1 semantic_class) {
  const auto index = static_cast<std::uint8_t>(semantic_class);
  return index < kInstallationGeometryCanaryPaletteV1.size()
             ? &kInstallationGeometryCanaryPaletteV1[index]
             : nullptr;
}

// Applies the frozen YIQ hue rotation selected by unsigned Q8.8 strength to
// exact obstacle pixels only. Strength zero succeeds as an exact no-op before
// inspecting either the output or profile. All other invalid inputs fail
// before mutation.
bool apply_hue_shift_q8_8(std::uint8_t* rgb, std::size_t rgb_bytes,
                         const InstallationProfileViewV1& profile,
                         std::uint16_t strength_q8_8);

// Replaces the caller-owned receiver RGB slice with the deterministic palette
// above. The validated profile remains read-only. Invalid inputs fail before
// any output byte is changed.
bool render_installation_geometry_canary(
    const InstallationProfileViewV1& profile, std::uint8_t* rgb,
    std::size_t rgb_bytes);

}  // namespace ledgrid
