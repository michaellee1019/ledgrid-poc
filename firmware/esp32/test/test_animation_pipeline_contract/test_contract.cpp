#include <unity.h>

#include <array>
#include <cstdint>
#include <vector>

#include "ledgrid/animation_pipeline_contract.hpp"
#include "../fixtures/animation_pipeline_v1.hpp"

namespace {

std::uint16_t read_u16(const std::uint8_t* input) {
  return static_cast<std::uint16_t>(
      (static_cast<std::uint16_t>(input[0]) << 8U) | input[1]);
}

std::uint64_t read_u64(const std::uint8_t* input) {
  std::uint64_t value = 0;
  for (std::size_t index = 0; index < 8U; ++index) {
    value = (value << 8U) | input[index];
  }
  return value;
}

void assert_rgba(
    const ledgrid::PremultipliedRgba8& expected,
    const ledgrid::PremultipliedRgba8& actual) {
  TEST_ASSERT_EQUAL_UINT8(expected.red, actual.red);
  TEST_ASSERT_EQUAL_UINT8(expected.green, actual.green);
  TEST_ASSERT_EQUAL_UINT8(expected.blue, actual.blue);
  TEST_ASSERT_EQUAL_UINT8(expected.alpha, actual.alpha);
}

ledgrid::PremultipliedRgba8 rgba_from(const std::uint8_t input[4]) {
  return {input[0], input[1], input[2], input[3]};
}

ledgrid::Digest256 digest(std::uint8_t seed) {
  ledgrid::Digest256 value{};
  for (std::size_t index = 0; index < ledgrid::kSnapshotDigestBytes; ++index) {
    value.bytes[index] = static_cast<std::uint8_t>(seed + index);
  }
  return value;
}

bool union_fixture_dirty_ranges(
    const ledgrid::golden_v1::DirtyRange* previous,
    std::size_t previous_count,
    const ledgrid::golden_v1::DirtyRange* next,
    std::size_t next_count,
    std::uint32_t pixel_count,
    ledgrid::golden_v1::DirtyRange* output,
    std::size_t output_capacity,
    std::size_t* output_count) {
  using ledgrid::golden_v1::DirtyRange;
  constexpr std::size_t kInputCapacity =
      ledgrid::golden_v1::kMaxDirtyUnionRanges;
  if (output_count == nullptr || previous_count + next_count > kInputCapacity ||
      (previous_count != 0 && previous == nullptr) ||
      (next_count != 0 && next == nullptr) ||
      (output_capacity != 0 && output == nullptr)) {
    return false;
  }
  *output_count = 0;

  DirtyRange sorted[kInputCapacity] = {};
  const std::size_t input_count = previous_count + next_count;
  for (std::size_t index = 0; index < input_count; ++index) {
    const DirtyRange range =
        index < previous_count ? previous[index] : next[index - previous_count];
    if (range.start >= range.end || range.end > pixel_count) return false;
    sorted[index] = range;
  }

  for (std::size_t index = 1; index < input_count; ++index) {
    const DirtyRange candidate = sorted[index];
    std::size_t position = index;
    while (position > 0 &&
           (candidate.start < sorted[position - 1U].start ||
            (candidate.start == sorted[position - 1U].start &&
             candidate.end < sorted[position - 1U].end))) {
      sorted[position] = sorted[position - 1U];
      --position;
    }
    sorted[position] = candidate;
  }

  DirtyRange merged[kInputCapacity] = {};
  std::size_t merged_count = 0;
  for (std::size_t index = 0; index < input_count; ++index) {
    if (merged_count != 0 &&
        sorted[index].start <= merged[merged_count - 1U].end) {
      if (sorted[index].end > merged[merged_count - 1U].end) {
        merged[merged_count - 1U].end = sorted[index].end;
      }
    } else {
      merged[merged_count++] = sorted[index];
    }
  }
  if (merged_count > output_capacity) return false;
  for (std::size_t index = 0; index < merged_count; ++index) {
    output[index] = merged[index];
  }
  *output_count = merged_count;
  return true;
}

void test_generated_json_golden_vectors_match_firmware_exactly() {
  using namespace ledgrid::golden_v1;

  TEST_ASSERT_EQUAL_UINT8(kProtocolVersion,
                          ledgrid::kAnimationPipelineProtocolVersion);
  TEST_ASSERT_EQUAL_UINT32(kMaxTransactionBytes,
                           ledgrid::kAnimationPipelineMaxTransactionBytes);
  TEST_ASSERT_EQUAL_UINT32(kCrcBytes, ledgrid::kAnimationPipelineCrcBytes);
  TEST_ASSERT_EQUAL_UINT32(kMaxRgbaPixelsPerPatch,
                           ledgrid::kMaxRgbaPixelsPerPatch);
  TEST_ASSERT_EQUAL_UINT32(kLocalPixels, ledgrid::kContractLocalPixels);
  TEST_ASSERT_EQUAL_UINT8(
      kControllerSessionBeginCommand,
      static_cast<std::uint8_t>(
          ledgrid::AnimationPipelineCommand::ControllerSessionBegin));
  TEST_ASSERT_EQUAL_UINT8(
      kOverlayBeginCommand,
      static_cast<std::uint8_t>(ledgrid::AnimationPipelineCommand::OverlayBegin));
  TEST_ASSERT_EQUAL_UINT8(
      kOverlayPatchCommand,
      static_cast<std::uint8_t>(ledgrid::AnimationPipelineCommand::OverlayPatch));
  TEST_ASSERT_EQUAL_UINT8(
      kOverlayCommitCommand,
      static_cast<std::uint8_t>(ledgrid::AnimationPipelineCommand::OverlayCommit));
  TEST_ASSERT_EQUAL_UINT8(
      kOverlayClearCommand,
      static_cast<std::uint8_t>(ledgrid::AnimationPipelineCommand::OverlayClear));
  TEST_ASSERT_EQUAL_UINT8(
      kOverlayRenewCommand,
      static_cast<std::uint8_t>(ledgrid::AnimationPipelineCommand::OverlayRenew));
  TEST_ASSERT_EQUAL_UINT32(kControllerSessionBeginHeaderBytes,
                           ledgrid::kControllerSessionBeginHeaderBytes);
  TEST_ASSERT_EQUAL_UINT32(kOverlayBeginHeaderBytes,
                           ledgrid::kOverlayBeginHeaderBytes);
  TEST_ASSERT_EQUAL_UINT32(kOverlayPatchHeaderBytes,
                           ledgrid::kOverlayPatchHeaderBytes);
  TEST_ASSERT_EQUAL_UINT32(kOverlayCommitHeaderBytes,
                           ledgrid::kOverlayCommitHeaderBytes);
  TEST_ASSERT_EQUAL_UINT32(kOverlayClearHeaderBytes,
                           ledgrid::kOverlayClearHeaderBytes);
  TEST_ASSERT_EQUAL_UINT32(kOverlayRenewHeaderBytes,
                           ledgrid::kOverlayRenewHeaderBytes);

  for (const auto& vector : kBlendVectors) {
    std::uint8_t actual[3] = {};
    ledgrid::source_over_opaque_rgb8(
        vector.base_rgb, rgba_from(vector.overlay_rgba), actual);
    TEST_ASSERT_EQUAL_UINT8_ARRAY_MESSAGE(
        vector.expected_rgb, actual, 3, vector.id);
  }
  for (const auto& vector : kOpacityVectors) {
    const auto actual = ledgrid::scale_premultiplied_rgba8(
        rgba_from(vector.input_rgba), vector.opacity);
    const std::uint8_t actual_bytes[4] = {
        actual.red, actual.green, actual.blue, actual.alpha};
    TEST_ASSERT_EQUAL_UINT8_ARRAY_MESSAGE(
        vector.expected_rgba, actual_bytes, 4, vector.id);
  }
  for (const auto& vector : kOverlayFoldVectors) {
    const auto actual = ledgrid::source_over_premultiplied_rgba8(
        rgba_from(vector.bottom_rgba), rgba_from(vector.top_rgba));
    const std::uint8_t actual_bytes[4] = {
        actual.red, actual.green, actual.blue, actual.alpha};
    TEST_ASSERT_EQUAL_UINT8_ARRAY_MESSAGE(
        vector.expected_rgba, actual_bytes, 4, vector.id);
  }
  TEST_ASSERT_EQUAL_UINT32(
      4, sizeof(kDirtyRangeVectors) / sizeof(kDirtyRangeVectors[0]));
  for (const auto& vector : kDirtyRangeVectors) {
    DirtyRange actual[kMaxDirtyUnionRanges] = {};
    std::size_t actual_count = UINT32_MAX;
    TEST_ASSERT_TRUE_MESSAGE(
        union_fixture_dirty_ranges(
            vector.previous_coverage,
            vector.previous_count,
            vector.next_coverage,
            vector.next_count,
            vector.pixel_count,
            actual,
            kMaxDirtyUnionRanges,
            &actual_count),
        vector.id);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(
        vector.expected_count, actual_count, vector.id);
    for (std::size_t index = 0; index < actual_count; ++index) {
      TEST_ASSERT_EQUAL_UINT32_MESSAGE(
          vector.expected_union[index].start, actual[index].start, vector.id);
      TEST_ASSERT_EQUAL_UINT32_MESSAGE(
          vector.expected_union[index].end, actual[index].end, vector.id);
    }
  }
  for (const auto& vector : kCoordinateVectors) {
    std::uint32_t actual_global = UINT32_MAX;
    const bool global_valid = ledgrid::logical_to_global_pixel(
        vector.global_strip,
        vector.led,
        vector.global_strips,
        vector.leds_per_strip,
        &actual_global);
    TEST_ASSERT_EQUAL_MESSAGE(vector.global_valid, global_valid, vector.id);
    if (global_valid) {
      TEST_ASSERT_EQUAL_UINT32_MESSAGE(
          vector.expected_global_index, actual_global, vector.id);
    }

    std::uint32_t actual_local = UINT32_MAX;
    const bool valid = ledgrid::logical_to_local_pixel(
        vector.global_strip,
        vector.led,
        vector.global_strip_offset,
        vector.local_strips,
        vector.leds_per_strip,
        &actual_local);
    TEST_ASSERT_EQUAL_MESSAGE(vector.valid, valid, vector.id);
    if (valid) {
      TEST_ASSERT_EQUAL_UINT32_MESSAGE(
          vector.expected_local_index, actual_local, vector.id);
    }
  }
  for (const auto& vector : kBoardSlices) {
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(
        vector.global_strip_offset * ledgrid::kContractLedsPerStrip,
        vector.start_flat_index,
        "board start");
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(
        vector.start_flat_index + vector.pixel_count,
        vector.end_flat_index,
        "board end");
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(
        ledgrid::kContractLocalPixels, vector.pixel_count, "board pixels");
  }
  TEST_ASSERT_EQUAL_UINT32(
      ledgrid::kContractFullSnapshotPatchCount,
      sizeof(kFullSnapshotPatches) / sizeof(kFullSnapshotPatches[0]));
  for (std::size_t index = 0;
       index < sizeof(kFullSnapshotPatches) / sizeof(kFullSnapshotPatches[0]);
       ++index) {
    TEST_ASSERT_EQUAL_UINT16(
        kFullSnapshotPatches[index].start,
        ledgrid::full_snapshot_patch_start(index));
    TEST_ASSERT_EQUAL_UINT16(
        kFullSnapshotPatches[index].count,
        ledgrid::full_snapshot_patch_pixels(index));
  }
}

void test_state_and_command_values_are_frozen_without_runtime_wiring() {
  TEST_ASSERT_EQUAL_UINT8(
      0, static_cast<std::uint8_t>(ledgrid::BaseMode::StartupFallback));
  TEST_ASSERT_EQUAL_UINT8(
      1, static_cast<std::uint8_t>(ledgrid::BaseMode::LocalBackground));
  TEST_ASSERT_EQUAL_UINT8(
      2, static_cast<std::uint8_t>(ledgrid::BaseMode::HostFullScene));
  TEST_ASSERT_EQUAL_UINT8(
      1, static_cast<std::uint8_t>(ledgrid::ForegroundState::Staging));
  TEST_ASSERT_EQUAL_UINT8(
      2, static_cast<std::uint8_t>(ledgrid::ForegroundState::Active));
  TEST_ASSERT_EQUAL_UINT8(
      2, static_cast<std::uint8_t>(ledgrid::MaintenanceState::CalibrationTransfer));
  TEST_ASSERT_EQUAL_UINT8(
      6, static_cast<std::uint8_t>(ledgrid::ReceiverFailureState::NativeWatchdogReset));
  TEST_ASSERT_EQUAL_HEX8(
      0x20,
      static_cast<std::uint8_t>(
          ledgrid::AnimationPipelineCommand::ControllerSessionBegin));
  TEST_ASSERT_EQUAL_HEX8(
      0x30,
      static_cast<std::uint8_t>(ledgrid::AnimationPipelineCommand::OverlayBegin));
  TEST_ASSERT_EQUAL_HEX8(
      0x34,
      static_cast<std::uint8_t>(ledgrid::AnimationPipelineCommand::OverlayRenew));
}

void test_wire_widths_and_exact_maximum_patch_are_frozen() {
  TEST_ASSERT_EQUAL_UINT32(4096, ledgrid::kAnimationPipelineMaxTransactionBytes);
  TEST_ASSERT_EQUAL_UINT32(2, ledgrid::kAnimationPipelineCrcBytes);
  TEST_ASSERT_EQUAL_UINT32(58, ledgrid::kControllerSessionBeginHeaderBytes);
  TEST_ASSERT_EQUAL_UINT32(66, ledgrid::kOverlayBeginHeaderBytes);
  TEST_ASSERT_EQUAL_UINT32(30, ledgrid::kOverlayPatchHeaderBytes);
  TEST_ASSERT_EQUAL_UINT32(50, ledgrid::kOverlayCommitHeaderBytes);
  TEST_ASSERT_EQUAL_UINT32(34, ledgrid::kOverlayClearHeaderBytes);
  TEST_ASSERT_EQUAL_UINT32(30, ledgrid::kOverlayRenewHeaderBytes);
  TEST_ASSERT_EQUAL_UINT32(1016, ledgrid::kMaxRgbaPixelsPerPatch);

  const std::size_t maximum_packet =
      ledgrid::kOverlayPatchHeaderBytes +
      ledgrid::kMaxRgbaPixelsPerPatch *
          ledgrid::kPremultipliedRgbaBytesPerPixel +
      ledgrid::kAnimationPipelineCrcBytes;
  TEST_ASSERT_EQUAL_UINT32(4096, maximum_packet);
}

void test_full_receiver_snapshot_has_canonical_two_patch_fixture() {
  TEST_ASSERT_EQUAL_UINT32(1104, ledgrid::kContractLocalPixels);
  TEST_ASSERT_EQUAL_UINT32(4416, ledgrid::kContractLocalRgbaBytes);
  TEST_ASSERT_EQUAL_UINT32(2, ledgrid::kContractFullSnapshotPatchCount);
  TEST_ASSERT_EQUAL_UINT16(0, ledgrid::full_snapshot_patch_start(0));
  TEST_ASSERT_EQUAL_UINT16(1016, ledgrid::full_snapshot_patch_pixels(0));
  TEST_ASSERT_EQUAL_UINT16(1016, ledgrid::full_snapshot_patch_start(1));
  TEST_ASSERT_EQUAL_UINT16(88, ledgrid::full_snapshot_patch_pixels(1));
  TEST_ASSERT_EQUAL_UINT16(0, ledgrid::full_snapshot_patch_pixels(2));
}

void test_patch_header_encoding_is_big_endian_and_bounds_checked() {
  ledgrid::OverlayPatchHeader header{};
  for (std::size_t index = 0; index < ledgrid::kControllerSessionBytes; ++index) {
    header.controller_session[index] = static_cast<std::uint8_t>(0xA0U + index);
  }
  header.generation = UINT64_C(0x0102030405060708);
  header.start = 1016;
  header.count = 88;
  std::array<std::uint8_t, ledgrid::kOverlayPatchHeaderBytes> encoded{};

  TEST_ASSERT_TRUE(ledgrid::encode_overlay_patch_header(
      header, encoded.data(), encoded.size()));
  TEST_ASSERT_EQUAL_HEX8(0x31, encoded[0]);
  TEST_ASSERT_EQUAL_UINT8(1, encoded[1]);
  TEST_ASSERT_EQUAL_HEX8(0xA0, encoded[2]);
  TEST_ASSERT_EQUAL_HEX8(0xAF, encoded[17]);
  TEST_ASSERT_EQUAL_UINT64(header.generation, read_u64(encoded.data() + 18));
  TEST_ASSERT_EQUAL_UINT16(header.start, read_u16(encoded.data() + 26));
  TEST_ASSERT_EQUAL_UINT16(header.count, read_u16(encoded.data() + 28));

  TEST_ASSERT_FALSE(ledgrid::encode_overlay_patch_header(
      header, encoded.data(), encoded.size() - 1U));
  header.count = 0;
  TEST_ASSERT_FALSE(ledgrid::encode_overlay_patch_header(
      header, encoded.data(), encoded.size()));
  header.count = 89;
  TEST_ASSERT_FALSE(ledgrid::encode_overlay_patch_header(
      header, encoded.data(), encoded.size()));
}

void test_crc_contract_matches_ccitt_false_and_exact_packet() {
  constexpr std::uint8_t check[] = {'1', '2', '3', '4', '5',
                                    '6', '7', '8', '9'};
  TEST_ASSERT_EQUAL_HEX16(
      0x29B1,
      ledgrid::animation_pipeline_crc16_ccitt(check, sizeof(check)));

  std::vector<std::uint8_t> packet(
      ledgrid::kAnimationPipelineMaxTransactionBytes, 0x5A);
  ledgrid::OverlayPatchHeader header{};
  header.generation = 1;
  header.count = ledgrid::kMaxRgbaPixelsPerPatch;
  TEST_ASSERT_TRUE(ledgrid::encode_overlay_patch_header(
      header, packet.data(), ledgrid::kOverlayPatchHeaderBytes));
  const std::size_t crc_offset = packet.size() - ledgrid::kAnimationPipelineCrcBytes;
  const std::uint16_t crc =
      ledgrid::animation_pipeline_crc16_ccitt(packet.data(), crc_offset);
  packet[crc_offset] = static_cast<std::uint8_t>(crc >> 8U);
  packet[crc_offset + 1U] = static_cast<std::uint8_t>(crc);
  TEST_ASSERT_EQUAL_UINT16(crc, read_u16(packet.data() + crc_offset));
}

void test_version_session_and_counter_order_reject_stale_inputs() {
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::Ok),
      static_cast<std::uint8_t>(ledgrid::validate_overlay_version_format(
          1, ledgrid::OverlayFormat::PremultipliedRgba8)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(
          ledgrid::OverlayOperationResult::UnsupportedVersion),
      static_cast<std::uint8_t>(ledgrid::validate_overlay_version_format(
          2, ledgrid::OverlayFormat::PremultipliedRgba8)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(
          ledgrid::OverlayOperationResult::UnsupportedFormat),
      static_cast<std::uint8_t>(ledgrid::validate_overlay_version_format(
          1, static_cast<ledgrid::OverlayFormat>(99))));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::StaleSession),
      static_cast<std::uint8_t>(
          ledgrid::validate_overlay_session_revision(false, 9, 9)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::StaleRevision),
      static_cast<std::uint8_t>(
          ledgrid::validate_overlay_session_revision(true, 8, 9)));
  TEST_ASSERT_EQUAL_INT8(
      static_cast<std::int8_t>(ledgrid::CounterRelation::Stale),
      static_cast<std::int8_t>(ledgrid::compare_monotonic_counter(4, 5)));
  TEST_ASSERT_EQUAL_INT8(
      static_cast<std::int8_t>(ledgrid::CounterRelation::Equal),
      static_cast<std::int8_t>(ledgrid::compare_monotonic_counter(5, 5)));
  TEST_ASSERT_EQUAL_INT8(
      static_cast<std::int8_t>(ledgrid::CounterRelation::Newer),
      static_cast<std::int8_t>(ledgrid::compare_monotonic_counter(6, 5)));
}

void test_generation_begin_enforces_cas_and_exact_idempotency() {
  ledgrid::OverlayGenerationOrderState state{};
  state.committed_generation = 8;
  const auto first_digest = digest(0x12);
  const auto conflicting_digest = digest(0x56);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::Ok),
      static_cast<std::uint8_t>(
          ledgrid::validate_overlay_generation_begin(state, 9, 8, first_digest)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(
          ledgrid::OverlayOperationResult::PriorGenerationMismatch),
      static_cast<std::uint8_t>(
          ledgrid::validate_overlay_generation_begin(state, 10, 7, first_digest)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(
          ledgrid::OverlayOperationResult::StaleGeneration),
      static_cast<std::uint8_t>(
          ledgrid::validate_overlay_generation_begin(state, 8, 8, first_digest)));

  state.has_staged_generation = true;
  state.staged_generation = 9;
  state.staged_operation_digest = first_digest;
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::Idempotent),
      static_cast<std::uint8_t>(
          ledgrid::validate_overlay_generation_begin(state, 9, 8, first_digest)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(
          ledgrid::OverlayOperationResult::GenerationConflict),
      static_cast<std::uint8_t>(
          ledgrid::validate_overlay_generation_begin(
              state, 9, 8, conflicting_digest)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::InvalidState),
      static_cast<std::uint8_t>(
          ledgrid::validate_overlay_generation_begin(state, 10, 8, first_digest)));
}

void test_full_snapshot_patch_order_retry_and_commit_are_transactional() {
  ledgrid::OverlayPatchOrderState state{};
  state.expected_patches = 2;
  state.update_kind = ledgrid::OverlayUpdateKind::FullSnapshot;
  const auto first_patch = digest(0x11);
  const auto second_patch = digest(0x22);

  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::Ok),
      static_cast<std::uint8_t>(
          ledgrid::accept_overlay_patch(&state, 0, 1016, first_patch)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::Idempotent),
      static_cast<std::uint8_t>(
          ledgrid::accept_overlay_patch(&state, 0, 1016, first_patch)));
  TEST_ASSERT_EQUAL_UINT16(1, state.accepted_patches);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::PatchConflict),
      static_cast<std::uint8_t>(
          ledgrid::accept_overlay_patch(&state, 0, 1016, second_patch)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::Incomplete),
      static_cast<std::uint8_t>(
          ledgrid::validate_overlay_commit(state, true, false)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::PatchOverlap),
      static_cast<std::uint8_t>(
          ledgrid::accept_overlay_patch(&state, 1000, 88, second_patch)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::PatchOrder),
      static_cast<std::uint8_t>(
          ledgrid::accept_overlay_patch(&state, 1017, 87, second_patch)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::Ok),
      static_cast<std::uint8_t>(
          ledgrid::accept_overlay_patch(&state, 1016, 88, second_patch)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(
          ledgrid::OverlayOperationResult::BaseBindingMismatch),
      static_cast<std::uint8_t>(
          ledgrid::validate_overlay_commit(state, false, false)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::LeaseExpired),
      static_cast<std::uint8_t>(
          ledgrid::validate_overlay_commit(state, true, true)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::Ok),
      static_cast<std::uint8_t>(
          ledgrid::validate_overlay_commit(state, true, false)));
}

void test_delta_patches_allow_gaps_but_reject_reverse_order_and_bounds() {
  ledgrid::OverlayPatchOrderState state{};
  state.expected_patches = 2;
  state.update_kind = ledgrid::OverlayUpdateKind::Delta;
  const auto first_patch = digest(1);
  const auto second_patch = digest(2);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::Ok),
      static_cast<std::uint8_t>(
          ledgrid::accept_overlay_patch(&state, 10, 2, first_patch)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::Ok),
      static_cast<std::uint8_t>(
          ledgrid::accept_overlay_patch(&state, 1000, 4, second_patch)));

  ledgrid::OverlayPatchOrderState reversed{};
  reversed.expected_patches = 2;
  reversed.update_kind = ledgrid::OverlayUpdateKind::Delta;
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::Ok),
      static_cast<std::uint8_t>(
          ledgrid::accept_overlay_patch(&reversed, 100, 2, first_patch)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::PatchOrder),
      static_cast<std::uint8_t>(
          ledgrid::accept_overlay_patch(&reversed, 20, 2, second_patch)));
  ledgrid::OverlayPatchOrderState bounds{};
  bounds.expected_patches = 1;
  bounds.update_kind = ledgrid::OverlayUpdateKind::Delta;
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::OutOfBounds),
      static_cast<std::uint8_t>(
          ledgrid::accept_overlay_patch(&bounds, 1100, 5, first_patch)));
}

void test_fixed_point_alpha_endpoints_rounding_saturation_and_fold_order() {
  TEST_ASSERT_EQUAL_UINT8(0, ledgrid::scale_u8_fixed(255, 0));
  TEST_ASSERT_EQUAL_UINT8(255, ledgrid::scale_u8_fixed(255, 255));
  TEST_ASSERT_EQUAL_UINT8(1, ledgrid::scale_u8_fixed(1, 128));
  TEST_ASSERT_EQUAL_UINT8(0, ledgrid::scale_u8_fixed(1, 127));

  const ledgrid::PremultipliedRgba8 scaled =
      ledgrid::scale_premultiplied_rgba8({100, 50, 1, 128}, 127);
  assert_rgba({50, 25, 0, 64}, scaled);

  std::uint8_t output[3] = {};
  const std::uint8_t white[3] = {255, 255, 255};
  ledgrid::source_over_opaque_rgb8(white, {0, 0, 0, 0}, output);
  TEST_ASSERT_EQUAL_UINT8_ARRAY(white, output, 3);
  ledgrid::source_over_opaque_rgb8(white, {0, 0, 0, 255}, output);
  const std::uint8_t black[3] = {0, 0, 0};
  TEST_ASSERT_EQUAL_UINT8_ARRAY(black, output, 3);
  ledgrid::source_over_opaque_rgb8(white, {255, 255, 255, 128}, output);
  const std::uint8_t saturated[3] = {255, 255, 255};
  TEST_ASSERT_EQUAL_UINT8_ARRAY(saturated, output, 3);

  const auto bottom_then_top = ledgrid::source_over_premultiplied_rgba8(
      {80, 20, 0, 128}, {0, 60, 30, 96});
  const auto top_then_bottom = ledgrid::source_over_premultiplied_rgba8(
      {0, 60, 30, 96}, {80, 20, 0, 128});
  assert_rgba({50, 72, 30, 176}, bottom_then_top);
  TEST_ASSERT_FALSE(
      bottom_then_top.red == top_then_bottom.red &&
      bottom_then_top.green == top_then_bottom.green &&
      bottom_then_top.blue == top_then_bottom.blue &&
      bottom_then_top.alpha == top_then_bottom.alpha);
}

void test_logical_to_local_coordinates_cover_all_board_boundaries() {
  std::uint32_t index = UINT32_MAX;
  TEST_ASSERT_TRUE(
      ledgrid::logical_to_local_pixel(0, 0, 0, 8, 138, &index));
  TEST_ASSERT_EQUAL_UINT32(0, index);
  TEST_ASSERT_TRUE(
      ledgrid::logical_to_local_pixel(7, 137, 0, 8, 138, &index));
  TEST_ASSERT_EQUAL_UINT32(1103, index);

  constexpr std::uint16_t offsets[] = {0, 8, 16, 24};
  for (std::size_t board = 0; board < 4; ++board) {
    TEST_ASSERT_TRUE(ledgrid::logical_to_local_pixel(
        offsets[board], 0, offsets[board], 8, 138, &index));
    TEST_ASSERT_EQUAL_UINT32(0, index);
    TEST_ASSERT_TRUE(ledgrid::logical_to_local_pixel(
        static_cast<std::uint16_t>(offsets[board] + 7U),
        137,
        offsets[board],
        8,
        138,
        &index));
    TEST_ASSERT_EQUAL_UINT32(1103, index);
  }

  TEST_ASSERT_FALSE(
      ledgrid::logical_to_local_pixel(7, 0, 8, 8, 138, &index));
  TEST_ASSERT_FALSE(
      ledgrid::logical_to_local_pixel(16, 0, 8, 8, 138, &index));
  TEST_ASSERT_FALSE(
      ledgrid::logical_to_local_pixel(8, 138, 8, 8, 138, &index));
  TEST_ASSERT_FALSE(
      ledgrid::logical_to_local_pixel(8, 0, 8, 8, 138, nullptr));
}

}  // namespace

void setUp() {}
void tearDown() {}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_generated_json_golden_vectors_match_firmware_exactly);
  RUN_TEST(test_state_and_command_values_are_frozen_without_runtime_wiring);
  RUN_TEST(test_wire_widths_and_exact_maximum_patch_are_frozen);
  RUN_TEST(test_full_receiver_snapshot_has_canonical_two_patch_fixture);
  RUN_TEST(test_patch_header_encoding_is_big_endian_and_bounds_checked);
  RUN_TEST(test_crc_contract_matches_ccitt_false_and_exact_packet);
  RUN_TEST(test_version_session_and_counter_order_reject_stale_inputs);
  RUN_TEST(test_generation_begin_enforces_cas_and_exact_idempotency);
  RUN_TEST(test_full_snapshot_patch_order_retry_and_commit_are_transactional);
  RUN_TEST(test_delta_patches_allow_gaps_but_reject_reverse_order_and_bounds);
  RUN_TEST(test_fixed_point_alpha_endpoints_rounding_saturation_and_fold_order);
  RUN_TEST(test_logical_to_local_coordinates_cover_all_board_boundaries);
  return UNITY_END();
}
