#include <unity.h>

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <vector>

#include "../fixtures/installation_profile_receiver_v1.hpp"
#include "ledgrid/installation_profile.hpp"
#include "ledgrid/sha256.hpp"

namespace {

constexpr std::size_t kFixedHeaderBytes = 112;
constexpr std::size_t kSectionEntryBytes = 24;
constexpr std::size_t kSectionPayloadOffset = 112 + 9 * 24;
constexpr std::size_t kPixels = 8 * 138;

void write_u16(std::vector<std::uint8_t>* bytes, std::size_t offset,
               std::uint16_t value) {
  (*bytes)[offset] = static_cast<std::uint8_t>(value >> 8U);
  (*bytes)[offset + 1] = static_cast<std::uint8_t>(value);
}

void write_u32(std::vector<std::uint8_t>* bytes, std::size_t offset,
               std::uint32_t value) {
  (*bytes)[offset] = static_cast<std::uint8_t>(value >> 24U);
  (*bytes)[offset + 1] = static_cast<std::uint8_t>(value >> 16U);
  (*bytes)[offset + 2] = static_cast<std::uint8_t>(value >> 8U);
  (*bytes)[offset + 3] = static_cast<std::uint8_t>(value);
}

std::uint32_t crc32(const std::uint8_t* data, std::size_t size) {
  std::uint32_t crc = 0xFFFFFFFFU;
  for (std::size_t index = 0; index < size; ++index) {
    crc ^= data[index];
    for (unsigned bit = 0; bit < 8; ++bit) {
      crc = (crc >> 1U) ^ (0xEDB88320U & (0U - (crc & 1U)));
    }
  }
  return ~crc;
}

void rehash_content(std::vector<std::uint8_t>* bytes) {
  std::fill(bytes->begin() + 68, bytes->begin() + 100, 0);
  std::uint8_t digest[32] = {};
  ledgrid::sha256(bytes->data(), bytes->size(), digest);
  std::copy(digest, digest + 32, bytes->begin() + 68);
}

void refresh_section(std::vector<std::uint8_t>* bytes, std::size_t section) {
  const std::size_t payload = kSectionPayloadOffset + section * kPixels;
  const std::size_t entry = kFixedHeaderBytes + section * kSectionEntryBytes;
  write_u32(bytes, entry + 16, crc32(bytes->data() + payload, kPixels));
  rehash_content(bytes);
}

std::vector<std::uint8_t> fixture(std::size_t logical_id = 0) {
  const auto& source =
      ledgrid::installation_profile_fixture::kInstalledReceivers[logical_id];
  return std::vector<std::uint8_t>(source.bytes, source.bytes + source.size);
}

ledgrid::InstallationProfileReceiverExpectationV1 expectation(
    std::size_t logical_id = 0) {
  const auto& source =
      ledgrid::installation_profile_fixture::kInstalledReceivers[logical_id];
  return {source.strip_origin, source.reversed_strip_order,
          source.global_strip_count, source.strip_count,
          source.leds_per_strip};
}

void assert_rejected(
    const std::vector<std::uint8_t>& bytes,
    ledgrid::InstallationProfileError expected,
    ledgrid::InstallationProfileReceiverExpectationV1 expected_view =
        expectation()) {
  ledgrid::InstallationProfileViewV1 view{};
  view.encoded = reinterpret_cast<const std::uint8_t*>(1);
  ledgrid::InstallationProfileError error =
      ledgrid::InstallationProfileError::None;
  TEST_ASSERT_FALSE(ledgrid::decode_installation_profile_receiver_v1(
      bytes.data(), bytes.size(), expected_view, &view, &error));
  TEST_ASSERT_EQUAL_UINT8(static_cast<std::uint8_t>(expected),
                          static_cast<std::uint8_t>(error));
  TEST_ASSERT_NULL(view.encoded);
  TEST_ASSERT_NULL(view.category);
}

void test_all_installed_receiver_views_decode_with_exact_identity() {
  for (std::size_t logical_id = 0;
       logical_id < std::size(
           ledgrid::installation_profile_fixture::kInstalledReceivers);
       ++logical_id) {
    const auto& source =
        ledgrid::installation_profile_fixture::kInstalledReceivers[logical_id];
    ledgrid::InstallationProfileViewV1 view{};
    ledgrid::InstallationProfileError error =
        ledgrid::InstallationProfileError::NullArgument;
    TEST_ASSERT_TRUE(ledgrid::decode_installation_profile_receiver_v1(
        source.bytes, source.size, expectation(logical_id), &view, &error));
    TEST_ASSERT_EQUAL_UINT8(
        static_cast<std::uint8_t>(ledgrid::InstallationProfileError::None),
        static_cast<std::uint8_t>(error));
    TEST_ASSERT_EQUAL_PTR(source.bytes, view.encoded);
    TEST_ASSERT_EQUAL_UINT32(source.size, view.encoded_size);
    TEST_ASSERT_EQUAL_UINT16(source.global_strip_count, view.global_strip_count);
    TEST_ASSERT_EQUAL_UINT16(source.leds_per_strip, view.leds_per_strip);
    TEST_ASSERT_EQUAL_UINT16(source.strip_origin, view.strip_origin);
    TEST_ASSERT_EQUAL_UINT16(source.strip_count, view.strip_count);
    const std::size_t pixels =
        static_cast<std::size_t>(source.strip_count) * source.leds_per_strip;
    TEST_ASSERT_EQUAL_UINT32(pixels, view.pixel_count);
    TEST_ASSERT_EQUAL_UINT8(1, view.clearance_radius);
    TEST_ASSERT_EQUAL(source.reversed_strip_order, view.reversed_strip_order);
    TEST_ASSERT_EQUAL_PTR(source.bytes + kSectionPayloadOffset, view.category);
    TEST_ASSERT_EQUAL_PTR(view.category + pixels, view.clearance);
    TEST_ASSERT_EQUAL_PTR(view.clearance + pixels, view.foliage_edge);
    TEST_ASSERT_EQUAL_PTR(view.foliage_edge + pixels, view.globe_edge);
    TEST_ASSERT_EQUAL_PTR(view.globe_edge + pixels, view.obstacle_edge);
    TEST_ASSERT_EQUAL_PTR(view.obstacle_edge + pixels, view.globe_region);
    TEST_ASSERT_EQUAL_PTR(view.globe_region + pixels, view.distance);
    TEST_ASSERT_EQUAL_PTR(view.distance + pixels,
                          reinterpret_cast<const std::uint8_t*>(view.normal_x));
    TEST_ASSERT_EQUAL_PTR(
        reinterpret_cast<const std::uint8_t*>(view.normal_x) + pixels,
        reinterpret_cast<const std::uint8_t*>(view.normal_y));
    TEST_ASSERT_EQUAL_MEMORY(source.bytes + 36, view.calibration_digest, 32);
    TEST_ASSERT_EQUAL_MEMORY(source.bytes + 68, view.content_digest, 32);
  }
}

void test_receiver_identity_and_exact_size_fail_closed() {
  const auto bytes = fixture(2);
  assert_rejected(bytes, ledgrid::InstallationProfileError::WrongOrigin,
                  expectation(3));
  auto wrong_direction = expectation(2);
  wrong_direction.reversed_strip_order = false;
  assert_rejected(bytes, ledgrid::InstallationProfileError::WrongDirection,
                  wrong_direction);
  for (std::size_t size : {std::size_t{0}, std::size_t{327},
                           bytes.size() - 1, bytes.size() + 1}) {
    std::vector<std::uint8_t> resized(size, 0);
    const std::size_t copied = std::min(size, bytes.size());
    std::copy(bytes.begin(), bytes.begin() + copied, resized.begin());
    assert_rejected(resized, ledgrid::InstallationProfileError::InvalidSize,
                    expectation(2));
  }
}

void test_header_table_reserved_and_digest_corruption_are_distinct() {
  struct Mutation {
    std::size_t offset;
    std::uint8_t value;
    ledgrid::InstallationProfileError expected;
    bool rehash;
  };
  const Mutation cases[] = {
      {0, 'X', ledgrid::InstallationProfileError::InvalidMagic, false},
      {5, 2, ledgrid::InstallationProfileError::UnsupportedVersion, false},
      {7, 111, ledgrid::InstallationProfileError::InvalidHeader, false},
      {11, 2, ledgrid::InstallationProfileError::InvalidFlags, false},
      {13, 31, ledgrid::InstallationProfileError::WrongGeometry, false},
      {17, 8, ledgrid::InstallationProfileError::WrongOrigin, false},
      {25, 6, ledgrid::InstallationProfileError::InvalidHeader, false},
      {27, 8, ledgrid::InstallationProfileError::InvalidSectionTable, false},
      {31, 1, ledgrid::InstallationProfileError::InvalidReservedBytes, false},
      {100, 1, ledgrid::InstallationProfileError::InvalidReservedBytes, false},
      {113, 0, ledgrid::InstallationProfileError::InvalidSectionMetadata, true},
  };
  for (const auto& value : cases) {
    auto bytes = fixture();
    bytes[value.offset] = value.value;
    if (value.rehash) rehash_content(&bytes);
    assert_rejected(bytes, value.expected);
  }
  auto digest = fixture();
  digest[68] ^= 1;
  assert_rejected(digest,
                  ledgrid::InstallationProfileError::ContentDigestMismatch);
}

void test_section_crc_and_semantic_values_reject_after_valid_rehash() {
  auto crc = fixture();
  crc[kSectionPayloadOffset] ^= 1;
  rehash_content(&crc);
  assert_rejected(crc, ledgrid::InstallationProfileError::SectionCrcMismatch);

  auto category = fixture();
  category[kSectionPayloadOffset] = 3;
  refresh_section(&category, 0);
  assert_rejected(category, ledgrid::InstallationProfileError::InvalidCategory);

  auto boolean = fixture();
  boolean[kSectionPayloadOffset + kPixels] = 2;
  refresh_section(&boolean, 1);
  assert_rejected(boolean, ledgrid::InstallationProfileError::InvalidBoolean);

  auto region = fixture();
  region[kSectionPayloadOffset + 5 * kPixels] = 8;
  refresh_section(&region, 5);
  assert_rejected(region,
                  ledgrid::InstallationProfileError::InvalidGlobeRegion);

  auto mismatch = fixture();
  const auto category_begin = mismatch.begin() + kSectionPayloadOffset;
  const auto globe = std::find(category_begin, category_begin + kPixels, 2);
  TEST_ASSERT_TRUE(globe != category_begin + kPixels);
  const std::size_t pixel = static_cast<std::size_t>(globe - category_begin);
  mismatch[kSectionPayloadOffset + 5 * kPixels + pixel] = 0;
  refresh_section(&mismatch, 5);
  assert_rejected(mismatch,
                  ledgrid::InstallationProfileError::CategoryRegionMismatch);

  auto normal = fixture();
  normal[kSectionPayloadOffset + 7 * kPixels] = 0x80;
  refresh_section(&normal, 7);
  assert_rejected(normal, ledgrid::InstallationProfileError::InvalidNormal);
}

void test_cross_section_semantic_invariants_reject_after_valid_rehash() {
  const auto source = fixture();
  const auto category_begin = source.begin() + kSectionPayloadOffset;

  const auto obstacle =
      std::find_if(category_begin, category_begin + kPixels,
                   [](std::uint8_t value) { return value != 0; });
  TEST_ASSERT_TRUE(obstacle != category_begin + kPixels);
  const std::size_t obstacle_pixel =
      static_cast<std::size_t>(obstacle - category_begin);

  const auto background =
      std::find(category_begin, category_begin + kPixels, 0);
  TEST_ASSERT_TRUE(background != category_begin + kPixels);
  const std::size_t background_pixel =
      static_cast<std::size_t>(background - category_begin);

  const auto non_foliage =
      std::find_if(category_begin, category_begin + kPixels,
                   [](std::uint8_t value) { return value != 1; });
  TEST_ASSERT_TRUE(non_foliage != category_begin + kPixels);
  const std::size_t non_foliage_pixel =
      static_cast<std::size_t>(non_foliage - category_begin);

  const auto non_globe =
      std::find_if(category_begin, category_begin + kPixels,
                   [](std::uint8_t value) { return value != 2; });
  TEST_ASSERT_TRUE(non_globe != category_begin + kPixels);
  const std::size_t non_globe_pixel =
      static_cast<std::size_t>(non_globe - category_begin);

  auto clearance = source;
  clearance[kSectionPayloadOffset + kPixels + obstacle_pixel] = 0;
  refresh_section(&clearance, 1);
  assert_rejected(
      clearance,
      ledgrid::InstallationProfileError::ClearanceObstacleMismatch);

  auto foliage_edge = source;
  foliage_edge[kSectionPayloadOffset + 2 * kPixels + non_foliage_pixel] = 1;
  refresh_section(&foliage_edge, 2);
  assert_rejected(foliage_edge,
                  ledgrid::InstallationProfileError::FoliageEdgeMismatch);

  auto globe_edge = source;
  globe_edge[kSectionPayloadOffset + 3 * kPixels + non_globe_pixel] = 1;
  refresh_section(&globe_edge, 3);
  assert_rejected(globe_edge,
                  ledgrid::InstallationProfileError::GlobeEdgeMismatch);

  auto obstacle_edge = source;
  obstacle_edge[kSectionPayloadOffset + 4 * kPixels + background_pixel] = 1;
  refresh_section(&obstacle_edge, 4);
  assert_rejected(obstacle_edge,
                  ledgrid::InstallationProfileError::ObstacleEdgeMismatch);

  auto nonzero_obstacle_distance = source;
  nonzero_obstacle_distance[kSectionPayloadOffset + 6 * kPixels +
                            obstacle_pixel] = 1;
  refresh_section(&nonzero_obstacle_distance, 6);
  assert_rejected(
      nonzero_obstacle_distance,
      ledgrid::InstallationProfileError::DistanceObstacleMismatch);

  auto zero_background_distance = source;
  zero_background_distance[kSectionPayloadOffset + 6 * kPixels +
                           background_pixel] = 0;
  refresh_section(&zero_background_distance, 6);
  assert_rejected(zero_background_distance,
                  ledgrid::InstallationProfileError::DistanceObstacleMismatch);
}

void test_null_arguments_never_expose_a_partial_view() {
  auto bytes = fixture();
  ledgrid::InstallationProfileError error =
      ledgrid::InstallationProfileError::None;
  ledgrid::InstallationProfileViewV1 view{};
  TEST_ASSERT_FALSE(ledgrid::decode_installation_profile_receiver_v1(
      nullptr, bytes.size(), expectation(), &view, &error));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::InstallationProfileError::NullArgument),
      static_cast<std::uint8_t>(error));
  TEST_ASSERT_FALSE(ledgrid::decode_installation_profile_receiver_v1(
      bytes.data(), bytes.size(), expectation(), nullptr, &error));
}

}  // namespace

void setUp() {}
void tearDown() {}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_all_installed_receiver_views_decode_with_exact_identity);
  RUN_TEST(test_receiver_identity_and_exact_size_fail_closed);
  RUN_TEST(test_header_table_reserved_and_digest_corruption_are_distinct);
  RUN_TEST(test_section_crc_and_semantic_values_reject_after_valid_rehash);
  RUN_TEST(test_cross_section_semantic_invariants_reject_after_valid_rehash);
  RUN_TEST(test_null_arguments_never_expose_a_partial_view);
  return UNITY_END();
}
