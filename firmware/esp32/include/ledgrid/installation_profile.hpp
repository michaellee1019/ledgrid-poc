#pragma once

#include <cstddef>
#include <cstdint>

namespace ledgrid {

constexpr std::uint16_t kInstallationProfileFormatV1 = 1;
constexpr std::uint16_t kInstallationProfileGlobalStripsV1 = 33;
constexpr std::uint16_t kInstallationProfileLedsPerStripV1 = 138;
constexpr std::uint16_t kInstallationProfileReceiverStripsV1 = 8;
constexpr std::uint32_t kInstallationProfileReceiverPixelsV1 =
    kInstallationProfileReceiverStripsV1 *
    kInstallationProfileLedsPerStripV1;
constexpr std::size_t kInstallationProfileSectionCountV1 = 9;
constexpr std::size_t kInstallationProfileReceiverBytesV1 =
    112U + 24U * kInstallationProfileSectionCountV1 +
    kInstallationProfileSectionCountV1 *
        kInstallationProfileReceiverPixelsV1;
constexpr std::size_t kInstallationProfileMaximumBytesV1 = 65535U;
constexpr std::size_t installation_profile_receiver_bytes_v1(
    std::uint16_t strip_count, std::uint16_t leds_per_strip) {
  return 112U + 24U * kInstallationProfileSectionCountV1 +
         kInstallationProfileSectionCountV1 *
             static_cast<std::size_t>(strip_count) * leds_per_strip;
}

enum class InstallationProfileError : std::uint8_t {
  None = 0,
  NullArgument,
  InvalidSize,
  InvalidMagic,
  UnsupportedVersion,
  InvalidHeader,
  InvalidFlags,
  WrongGeometry,
  WrongOrigin,
  WrongDirection,
  InvalidSectionTable,
  InvalidReservedBytes,
  ContentDigestMismatch,
  InvalidSectionMetadata,
  SectionOutOfBounds,
  SectionCrcMismatch,
  InvalidCategory,
  InvalidBoolean,
  InvalidGlobeRegion,
  CategoryRegionMismatch,
  InvalidNormal,
  ClearanceObstacleMismatch,
  FoliageEdgeMismatch,
  GlobeEdgeMismatch,
  ObstacleEdgeMismatch,
  DistanceObstacleMismatch,
};

struct InstallationProfileReceiverExpectationV1 {
  std::uint16_t strip_origin = 0;
  bool reversed_strip_order = false;
  std::uint16_t global_strip_count = kInstallationProfileGlobalStripsV1;
  std::uint16_t strip_count = kInstallationProfileReceiverStripsV1;
  std::uint16_t leds_per_strip = kInstallationProfileLedsPerStripV1;
};

// Non-owning, read-only view of validated profile bytes. The caller must keep
// the complete encoded profile alive and unchanged for the lifetime of this
// view. This is the reserved seam for a later receiver runtime/native ABI; it
// deliberately exposes no storage, transport, or activation behavior.
struct InstallationProfileViewV1 {
  const std::uint8_t* encoded = nullptr;
  std::size_t encoded_size = 0;
  std::uint16_t global_strip_count = 0;
  std::uint16_t leds_per_strip = 0;
  std::uint16_t strip_origin = 0;
  std::uint16_t strip_count = 0;
  std::uint32_t pixel_count = 0;
  std::uint8_t clearance_radius = 0;
  bool reversed_strip_order = false;
  std::uint8_t calibration_digest[32] = {};
  std::uint8_t content_digest[32] = {};
  const std::uint8_t* category = nullptr;
  const std::uint8_t* clearance = nullptr;
  const std::uint8_t* foliage_edge = nullptr;
  const std::uint8_t* globe_edge = nullptr;
  const std::uint8_t* obstacle_edge = nullptr;
  const std::uint8_t* globe_region = nullptr;
  const std::uint8_t* distance = nullptr;
  const std::int8_t* normal_x = nullptr;
  const std::int8_t* normal_y = nullptr;
};

bool decode_installation_profile_receiver_v1(
    const std::uint8_t* encoded,
    std::size_t encoded_size,
    const InstallationProfileReceiverExpectationV1& expectation,
    InstallationProfileViewV1* view,
    InstallationProfileError* error = nullptr);

}  // namespace ledgrid
