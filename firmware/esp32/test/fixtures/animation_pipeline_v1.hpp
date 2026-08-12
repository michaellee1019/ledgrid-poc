// Generated from tests/fixtures/animation_pipeline_v1.json.
// Run tools/generate_firmware_animation_pipeline_golden.py after changing it.
#pragma once

#include <cstddef>
#include <cstdint>

namespace ledgrid {
namespace golden_v1 {

struct BlendVector {
  const char* id;
  std::uint8_t base_rgb[3];
  std::uint8_t overlay_rgba[4];
  std::uint8_t expected_rgb[3];
};

struct OpacityVector {
  const char* id;
  std::uint8_t input_rgba[4];
  std::uint8_t opacity;
  std::uint8_t expected_rgba[4];
};

struct OverlayFoldVector {
  const char* id;
  std::uint8_t bottom_rgba[4];
  std::uint8_t top_rgba[4];
  std::uint8_t expected_rgba[4];
};

constexpr std::size_t kMaxDirtyRangesPerGroup = 8;
constexpr std::size_t kMaxDirtyUnionRanges = 16;

struct DirtyRange {
  std::uint32_t start;
  std::uint32_t end;
};

struct DirtyRangeVector {
  const char* id;
  std::uint32_t pixel_count;
  std::size_t previous_count;
  DirtyRange previous_coverage[kMaxDirtyRangesPerGroup];
  std::size_t next_count;
  DirtyRange next_coverage[kMaxDirtyRangesPerGroup];
  std::size_t expected_count;
  DirtyRange expected_union[kMaxDirtyUnionRanges];
};

struct CoordinateVector {
  const char* id;
  std::uint16_t global_strip;
  std::uint16_t led;
  std::uint16_t global_strip_offset;
  std::uint16_t global_strips;
  std::uint16_t local_strips;
  std::uint16_t leds_per_strip;
  bool global_valid;
  bool valid;
  std::uint32_t expected_global_index;
  std::uint32_t expected_local_index;
};

struct BoardSliceVector {
  std::uint8_t board_index;
  std::uint16_t global_strip_offset;
  std::uint32_t start_flat_index;
  std::uint32_t end_flat_index;
  std::uint32_t pixel_count;
};

struct SnapshotPatchVector {
  std::uint16_t start;
  std::uint16_t count;
};

constexpr std::uint8_t kProtocolVersion = 1;
constexpr std::size_t kMaxTransactionBytes = 4096;
constexpr std::size_t kCrcBytes = 2;
constexpr std::size_t kMaxRgbaPixelsPerPatch = 1016;
constexpr std::size_t kLocalPixels = 1104;

constexpr std::uint8_t kControllerSessionBeginCommand = 32;
constexpr std::uint8_t kOverlayBeginCommand = 48;
constexpr std::uint8_t kOverlayPatchCommand = 49;
constexpr std::uint8_t kOverlayCommitCommand = 50;
constexpr std::uint8_t kOverlayClearCommand = 51;
constexpr std::uint8_t kOverlayRenewCommand = 52;

constexpr std::size_t kControllerSessionBeginHeaderBytes = 58;
constexpr std::size_t kOverlayBeginHeaderBytes = 66;
constexpr std::size_t kOverlayPatchHeaderBytes = 30;
constexpr std::size_t kOverlayCommitHeaderBytes = 50;
constexpr std::size_t kOverlayClearHeaderBytes = 34;
constexpr std::size_t kOverlayRenewHeaderBytes = 30;

constexpr BlendVector kBlendVectors[] = {
    {"transparent_black", {12, 34, 56}, {0, 0, 0, 0}, {12, 34, 56}},
    {"opaque_black", {12, 34, 56}, {0, 0, 0, 255}, {0, 0, 0}},
    {"opaque_color", {250, 200, 150}, {17, 31, 47, 255}, {17, 31, 47}},
    {"half_up_rounding", {1, 2, 3}, {0, 0, 0, 127}, {1, 1, 2}},
    {"channel_saturation", {255, 255, 255}, {254, 253, 252, 254}, {255, 254, 253}},
    {"mixed_channels", {241, 17, 99}, {32, 64, 16, 128}, {152, 72, 65}},
};

constexpr OpacityVector kOpacityVectors[] = {
    {"zero_endpoint", {99, 66, 33, 128}, 0, {0, 0, 0, 0}},
    {"opaque_endpoint", {99, 66, 33, 128}, 255, {99, 66, 33, 128}},
    {"half_up_to_one", {1, 1, 1, 1}, 128, {1, 1, 1, 1}},
    {"half_down_to_zero", {1, 1, 1, 1}, 127, {0, 0, 0, 0}},
    {"mixed_opacity", {120, 60, 30, 128}, 200, {94, 47, 24, 100}},
};

constexpr OverlayFoldVector kOverlayFoldVectors[] = {
    {"transparent_top", {80, 40, 20, 128}, {0, 0, 0, 0}, {80, 40, 20, 128}},
    {"opaque_top", {80, 40, 20, 128}, {5, 7, 9, 255}, {5, 7, 9, 255}},
    {"ordered_overlap", {80, 20, 10, 128}, {5, 60, 15, 96}, {55, 72, 21, 176}},
    {"round_each_fold", {1, 1, 1, 1}, {1, 0, 0, 128}, {1, 0, 0, 128}},
};

constexpr DirtyRangeVector kDirtyRangeVectors[] = {
    {"movement", 64, 1, {{10, 13}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}}, 1, {{20, 23}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}}, 2, {{10, 13}, {20, 23}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}}},
    {"overlap_and_adjacency", 64, 2, {{10, 14}, {30, 34}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}}, 2, {{13, 18}, {34, 36}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}}, 2, {{10, 18}, {30, 36}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}}},
    {"complete_clear", 64, 2, {{4, 9}, {40, 44}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}}, 0, {{0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}}, 2, {{4, 9}, {40, 44}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}}},
    {"empty", 64, 0, {{0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}}, 0, {{0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}}, 0, {{0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}}},
};

constexpr CoordinateVector kCoordinateVectors[] = {
    {"wall_origin", 0, 0, 0, 32, 8, 138, true, true, 0, 0},
    {"board_0_last", 7, 137, 0, 32, 8, 138, true, true, 1103, 1103},
    {"board_1_first", 8, 0, 8, 32, 8, 138, true, true, 1104, 0},
    {"board_1_last", 15, 137, 8, 32, 8, 138, true, true, 2207, 1103},
    {"board_2_first", 16, 0, 16, 32, 8, 138, true, true, 2208, 0},
    {"board_2_last", 23, 137, 16, 32, 8, 138, true, true, 3311, 1103},
    {"board_3_first", 24, 0, 24, 32, 8, 138, true, true, 3312, 0},
    {"wall_last", 31, 137, 24, 32, 8, 138, true, true, 4415, 1103},
    {"before_local_offset", 7, 137, 8, 32, 8, 138, true, false, 1103, 0},
    {"after_local_range", 16, 0, 8, 32, 8, 138, true, false, 2208, 0},
    {"led_out_of_range", 24, 138, 24, 32, 8, 138, false, false, 0, 0},
    {"strip_out_of_range", 32, 0, 24, 32, 8, 138, false, false, 0, 0},
};

constexpr BoardSliceVector kBoardSlices[] = {
    {0, 0, 0, 1104, 1104},
    {1, 8, 1104, 2208, 1104},
    {2, 16, 2208, 3312, 1104},
    {3, 24, 3312, 4416, 1104},
};

constexpr SnapshotPatchVector kFullSnapshotPatches[] = {
    {0, 1016},
    {1016, 88},
};

}  // namespace golden_v1
}  // namespace ledgrid
