#include <unity.h>

#include <array>
#include <cstdint>
#include <cstring>
#include <utility>
#include <vector>

#include "fixtures/animation_pipeline_v1.hpp"
#include "fixtures/receiver_presentation_v1.hpp"
#include "ledgrid/protocol.hpp"
#include "ledgrid/receiver_task_policy.hpp"
#include "ledgrid/receiver_runtime.hpp"
#include "ledgrid/sha256.hpp"

namespace {

std::uint8_t hex_nibble(char value) {
  if (value >= '0' && value <= '9') return value - '0';
  return static_cast<std::uint8_t>(10 + value - 'a');
}

std::vector<std::uint8_t> from_hex(const char* input) {
  std::vector<std::uint8_t> result;
  const std::size_t length = std::strlen(input);
  TEST_ASSERT_EQUAL_UINT32(0, length % 2);
  result.reserve(length / 2);
  for (std::size_t index = 0; index < length; index += 2) {
    result.push_back(static_cast<std::uint8_t>(
        (hex_nibble(input[index]) << 4U) | hex_nibble(input[index + 1])));
  }
  return result;
}

void append_u16(std::vector<std::uint8_t>* output, std::uint16_t value) {
  output->push_back(value >> 8U);
  output->push_back(value);
}

void append_u32(std::vector<std::uint8_t>* output, std::uint32_t value) {
  output->push_back(value >> 24U); output->push_back(value >> 16U);
  output->push_back(value >> 8U); output->push_back(value);
}

void append_u64(std::vector<std::uint8_t>* output, std::uint64_t value) {
  for (unsigned shift = 56; shift <= 56; shift -= 8) output->push_back(value >> shift);
}

std::uint64_t read_u64(const std::uint8_t* input) {
  std::uint64_t value = 0;
  for (std::size_t index = 0; index < 8U; ++index) {
    value = (value << 8U) | input[index];
  }
  return value;
}

std::uint16_t read_u16(const std::uint8_t* input) {
  return static_cast<std::uint16_t>(
      (static_cast<std::uint16_t>(input[0]) << 8U) | input[1]);
}

std::vector<std::uint8_t> start_command(
    std::uint16_t cadence = 30, std::uint32_t offset = 8,
    std::uint32_t seed = 42,
    std::uint64_t epoch = 0x0102030405060708ULL) {
  std::vector<std::uint8_t> result{0x10};
  append_u16(&result, 1); append_u16(&result, cadence);
  append_u32(&result, offset); append_u32(&result, seed); append_u64(&result, epoch);
  return result;
}

std::vector<std::uint8_t> session_command(
    const ledgrid::ReceiverRuntime& runtime, std::uint64_t revision = 1,
    std::uint8_t digest_byte = 0xA5) {
  std::vector<std::uint8_t> result{0x20, 1};
  result.insert(result.end(), runtime.active_context().session,
                runtime.active_context().session + 16);
  append_u64(&result, revision);
  result.insert(result.end(), 32, digest_byte);
  return result;
}

std::vector<std::uint8_t> overlay_begin_command(
    const ledgrid::ReceiverRuntime& runtime,
    std::uint64_t generation,
    std::uint64_t prior_generation,
    ledgrid::OverlayUpdateKind kind,
    std::uint16_t expected_patches,
    std::uint32_t lease_ms) {
  std::vector<std::uint8_t> result{0x30, 1};
  result.insert(result.end(), runtime.active_context().session,
                runtime.active_context().session + 16);
  append_u64(&result, generation);
  append_u64(&result, prior_generation);
  append_u64(&result, runtime.active_context().scene_revision);
  append_u64(&result, runtime.active_context().scene_epoch);
  append_u64(&result, runtime.active_context().scene_revision);
  result.push_back(1);
  result.push_back(static_cast<std::uint8_t>(kind));
  append_u16(&result, expected_patches);
  append_u32(&result, lease_ms);
  return result;
}

std::vector<std::uint8_t> overlay_patch_command(
    const ledgrid::ReceiverRuntime& runtime,
    std::uint64_t generation,
    std::uint16_t start,
    const std::vector<std::uint8_t>& rgba) {
  std::vector<std::uint8_t> result{0x31, 1};
  result.insert(result.end(), runtime.active_context().session,
                runtime.active_context().session + 16);
  append_u64(&result, generation);
  append_u16(&result, start);
  append_u16(&result, static_cast<std::uint16_t>(rgba.size() / 4U));
  result.insert(result.end(), rgba.begin(), rgba.end());
  return result;
}

using BatchSpan = std::pair<std::uint16_t, std::vector<std::uint8_t>>;

std::vector<std::uint8_t> overlay_patch_batch_command(
    const ledgrid::ReceiverRuntime& runtime,
    std::uint64_t generation,
    const std::vector<BatchSpan>& spans) {
  std::vector<std::uint8_t> result{0x35, 1};
  result.insert(result.end(), runtime.active_context().session,
                runtime.active_context().session + 16);
  append_u64(&result, generation);
  append_u16(&result, static_cast<std::uint16_t>(spans.size()));
  for (const auto& span : spans) {
    append_u16(&result, span.first);
    append_u16(&result,
               static_cast<std::uint16_t>(span.second.size() / 4U));
    result.insert(result.end(), span.second.begin(), span.second.end());
  }
  return result;
}

std::vector<std::uint8_t> overlay_commit_command(
    const ledgrid::ReceiverRuntime& runtime,
    std::uint64_t generation,
    std::uint64_t present_at_scene_time_us) {
  std::vector<std::uint8_t> result{0x32, 1};
  result.insert(result.end(), runtime.active_context().session,
                runtime.active_context().session + 16);
  append_u64(&result, generation);
  append_u64(&result, runtime.active_context().scene_epoch);
  append_u64(&result, runtime.active_context().scene_revision);
  append_u64(&result, present_at_scene_time_us);
  return result;
}

std::vector<std::uint8_t> overlay_clear_command(
    const ledgrid::ReceiverRuntime& runtime,
    std::uint64_t generation) {
  std::vector<std::uint8_t> result{0x33, 1};
  result.insert(result.end(), runtime.active_context().session,
                runtime.active_context().session + 16);
  append_u64(&result, generation);
  append_u64(&result, runtime.active_context().scene_revision);
  return result;
}

std::vector<std::uint8_t> overlay_renew_command(
    const ledgrid::ReceiverRuntime& runtime,
    std::uint64_t generation,
    std::uint32_t lease_ms) {
  std::vector<std::uint8_t> result{0x34, 1};
  result.insert(result.end(), runtime.active_context().session,
                runtime.active_context().session + 16);
  append_u64(&result, generation);
  append_u32(&result, lease_ms);
  return result;
}

void activate_neutral_context(ledgrid::ReceiverRuntime* runtime);

void activate_local_hybrid(
    ledgrid::ReceiverRuntime* runtime,
    std::uint64_t local_monotonic_us = 1000000) {
  activate_neutral_context(runtime);
  auto start = start_command(
      60, 0, 42, runtime->active_context().scene_epoch);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      runtime->process_command(start.data(), start.size(), local_monotonic_us)));
  auto session = session_command(*runtime);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime->process_command(
      session.data(), session.size(), local_monotonic_us)));
}

void publish_full_snapshot(
    ledgrid::ReceiverRuntime* runtime,
    std::uint64_t generation,
    std::uint64_t prior_generation,
    std::uint32_t lease_ms,
    std::uint64_t local_monotonic_us) {
  auto begin = overlay_begin_command(
      *runtime, generation, prior_generation,
      ledgrid::OverlayUpdateKind::FullSnapshot, 2, lease_ms);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      runtime->process_command(begin.data(), begin.size(), local_monotonic_us)));
  std::vector<std::uint8_t> first(1016U * 4U, 0);
  first[3] = 255;
  auto first_patch = overlay_patch_command(*runtime, generation, 0, first);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime->process_command(
      first_patch.data(), first_patch.size(), local_monotonic_us)));
  std::vector<std::uint8_t> tail(88U * 4U, 0);
  auto tail_patch = overlay_patch_command(*runtime, generation, 1016, tail);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime->process_command(
      tail_patch.data(), tail_patch.size(), local_monotonic_us)));
  auto commit = overlay_commit_command(
      *runtime, generation, runtime->scene_time_us(local_monotonic_us));
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime->process_command(
      commit.data(), commit.size(), local_monotonic_us)));
}

std::vector<std::uint8_t> canonical_dispatch_command(std::uint8_t command) {
  std::size_t size = 1;
  switch (static_cast<ledgrid::ReceiverCommand>(command)) {
    case ledgrid::ReceiverCommand::SetPixel: size = 6; break;
    case ledgrid::ReceiverCommand::SetBrightness: size = 2; break;
    case ledgrid::ReceiverCommand::SetRange: size = 4; break;
    case ledgrid::ReceiverCommand::SetAll: size = 13; break;
    case ledgrid::ReceiverCommand::Config: size = 4; break;
    case ledgrid::ReceiverCommand::StatusQuery:
      size = ledgrid::kStatusBytesV3;
      break;
    case ledgrid::ReceiverCommand::LocalBackgroundStart: size = 21; break;
    case ledgrid::ReceiverCommand::LocalBackgroundParameters: size = 11; break;
    case ledgrid::ReceiverCommand::ControllerSessionBegin:
    case ledgrid::ReceiverCommand::PresentationContextBegin: size = 58; break;
    case ledgrid::ReceiverCommand::PresentationContextSet: size = 145; break;
    case ledgrid::ReceiverCommand::PresentationContextCommit: size = 74; break;
    case ledgrid::ReceiverCommand::OverlayBegin: size = 66; break;
    case ledgrid::ReceiverCommand::OverlayPatch: size = 30; break;
    case ledgrid::ReceiverCommand::OverlayCommit: size = 50; break;
    case ledgrid::ReceiverCommand::OverlayClear: size = 34; break;
    case ledgrid::ReceiverCommand::OverlayRenew: size = 30; break;
    case ledgrid::ReceiverCommand::OverlayPatchBatch: size = 36; break;
    default: break;
  }
  std::vector<std::uint8_t> result(size, 0);
  result[0] = command;
  if (command == static_cast<std::uint8_t>(
                     ledgrid::ReceiverCommand::OverlayPatchBatch)) {
    result[1] = ledgrid::kAnimationPipelineProtocolVersion;
    result[27] = 1;  // one span
    result[31] = 1;  // one pixel
  }
  return result;
}

struct FakePhysicalSubmitter {
  unsigned calls = 0;
  ledgrid::ReceiverOutputConfiguration last_output{};
  bool result = true;
};

bool fake_physical_submit(
    void* raw_context,
    const ledgrid::ReceiverOutputConfiguration& output) {
  auto* context = static_cast<FakePhysicalSubmitter*>(raw_context);
  if (context == nullptr) return false;
  ++context->calls;
  context->last_output = output;
  return context->result;
}

void activate_neutral_context(ledgrid::ReceiverRuntime* runtime) {
  const auto& vector = ledgrid::golden_presentation_v1::kPresentationVectors[0];
  auto begin = from_hex(vector.begin_hex);
  auto set = from_hex(vector.set_hex);
  auto commit = from_hex(vector.commit_hex);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      runtime->process_command(begin.data(), begin.size())));
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      runtime->process_command(set.data(), set.size())));
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      runtime->process_command(commit.data(), commit.size())));
}

void test_command_ids_ownership_and_disabled_behavior_are_explicit() {
  TEST_ASSERT_EQUAL_HEX8(0x08, static_cast<std::uint8_t>(ledgrid::ReceiverCommand::StatusQuery));
  TEST_ASSERT_EQUAL_HEX8(0x10, static_cast<std::uint8_t>(ledgrid::ReceiverCommand::LocalBackgroundStart));
  TEST_ASSERT_EQUAL_HEX8(0x23, static_cast<std::uint8_t>(ledgrid::ReceiverCommand::PresentationContextCommit));
  for (unsigned value = 0; value <= 0xFF; ++value) {
    const bool expected = value == 0x06 || value == 0x10;
    TEST_ASSERT_EQUAL(expected, ledgrid::command_may_claim_base(
                                    static_cast<ledgrid::ReceiverCommand>(value)));
  }
  ledgrid::ReceiverRuntime disabled(false);
  const auto start = start_command();
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::ReceiverOperationResult::Unsupported),
      static_cast<std::uint8_t>(disabled.process_command(start.data(), start.size())));
  TEST_ASSERT_EQUAL_UINT8(0, static_cast<std::uint8_t>(disabled.base_mode()));
  disabled.complete_host_frame();
  TEST_ASSERT_EQUAL_UINT8(2, static_cast<std::uint8_t>(disabled.base_mode()));
}

void test_start_parameter_stop_takeover_restart_and_failure_transitions() {
  ledgrid::ReceiverRuntime runtime(true);
  runtime.set_reverse_local_strip_order(true);
  activate_neutral_context(&runtime);
  auto start = start_command(40, 16, 0x10203040, 0x0102030405060708ULL);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      runtime.process_command(start.data(), start.size())));
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.base_mode()));
  TEST_ASSERT_EQUAL_UINT16(40, runtime.local_parameters().preferred_cadence_hz);
  TEST_ASSERT_EQUAL_UINT32(16, runtime.local_parameters().global_strip_offset);
  TEST_ASSERT_EQUAL_UINT64(0x0102030405060708ULL, runtime.local_parameters().scene_epoch);
  TEST_ASSERT_TRUE(runtime.local_parameters().reverse_local_strip_order);

  std::vector<std::uint8_t> parameters{0x12};
  append_u16(&parameters, 20); append_u32(&parameters, 24); append_u32(&parameters, 7);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      runtime.process_command(parameters.data(), parameters.size())));
  TEST_ASSERT_EQUAL_UINT64(0x0102030405060708ULL, runtime.local_parameters().scene_epoch);
  TEST_ASSERT_TRUE(runtime.local_parameters().reverse_local_strip_order);
  runtime.complete_host_frame();
  TEST_ASSERT_EQUAL_UINT8(2, static_cast<std::uint8_t>(runtime.base_mode()));
  TEST_ASSERT_EQUAL_UINT8(3, static_cast<std::uint8_t>(runtime.transition_reason()));
  const std::uint8_t stop[] = {0x11};
  runtime.process_command(stop, sizeof(stop));
  TEST_ASSERT_EQUAL_UINT8(0, static_cast<std::uint8_t>(runtime.base_mode()));
  runtime.process_command(start.data(), start.size());
  TEST_ASSERT_TRUE(runtime.local_render_failed_if_current(
      runtime.render_generation()));
  TEST_ASSERT_EQUAL_UINT8(0, static_cast<std::uint8_t>(runtime.base_mode()));
  TEST_ASSERT_EQUAL_UINT8(5, static_cast<std::uint8_t>(runtime.transition_reason()));
  runtime.receiver_restart();
  TEST_ASSERT_EQUAL_UINT8(4, static_cast<std::uint8_t>(runtime.transition_reason()));
}

void test_invalid_commands_are_atomic_and_partial_commands_never_claim() {
  ledgrid::ReceiverRuntime runtime(true);
  auto invalid = start_command(0);
  TEST_ASSERT_EQUAL_UINT8(4, static_cast<std::uint8_t>(
      runtime.process_command(invalid.data(), invalid.size())));
  TEST_ASSERT_EQUAL_UINT8(0, static_cast<std::uint8_t>(runtime.base_mode()));
  auto start = start_command();
  TEST_ASSERT_EQUAL_UINT8(5, static_cast<std::uint8_t>(
      runtime.process_command(start.data(), start.size())));
  activate_neutral_context(&runtime);
  runtime.process_command(start.data(), start.size());
  const auto before = runtime.local_parameters();
  std::vector<std::uint8_t> bad_parameters{0x12, 0, 30};
  TEST_ASSERT_EQUAL_UINT8(3, static_cast<std::uint8_t>(
      runtime.process_command(bad_parameters.data(), bad_parameters.size())));
  TEST_ASSERT_EQUAL_UINT32(before.common_seed, runtime.local_parameters().common_seed);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.base_mode()));
}

void test_cadence_is_deadline_driven_and_counts_misses() {
  ledgrid::ReceiverRuntime runtime(true);
  activate_neutral_context(&runtime);
  auto start = start_command(40);
  runtime.process_command(start.data(), start.size());
  TEST_ASSERT_TRUE(runtime.local_frame_due(1000000));
  runtime.local_frame_rendered_if_current(
      runtime.render_generation(), 1000000, 5000000, 321);
  TEST_ASSERT_FALSE(runtime.local_frame_due(1024999));
  TEST_ASSERT_TRUE(runtime.local_frame_due(1025000));
  runtime.local_frame_rendered_if_current(
      runtime.render_generation(), 1025000, 5025000, 123);
  TEST_ASSERT_TRUE(runtime.local_frame_due(1100000));
  TEST_ASSERT_EQUAL_UINT32(2, runtime.render_stats().missed_cadence);
  runtime.local_frame_rendered_if_current(
      runtime.render_generation(), 1100000, 5100000, 70000);
  TEST_ASSERT_EQUAL_UINT32(3, runtime.render_stats().rendered_frames);
  TEST_ASSERT_EQUAL_UINT16(UINT16_MAX, runtime.render_stats().last_render_us);
  TEST_ASSERT_EQUAL_UINT16(UINT16_MAX, runtime.render_stats().max_render_us);
  TEST_ASSERT_EQUAL_UINT64(5100000, runtime.render_stats().last_frame_scene_time_us);
}

void test_rainbow_uses_global_coordinates_seed_and_luminance_once() {
  ledgrid::LocalBackgroundParameters full_params{};
  full_params.common_seed = 9;
  std::array<std::uint8_t, 16U * 4U * 3U> full{};
  std::array<std::uint8_t, 8U * 4U * 3U> board{};
  TEST_ASSERT_TRUE(ledgrid::render_compiled_rainbow(
      12345, full_params, 256, 16, 4, full.data(), full.size()));
  auto board_params = full_params;
  board_params.global_strip_offset = 8;
  TEST_ASSERT_TRUE(ledgrid::render_compiled_rainbow(
      12345, board_params, 256, 8, 4, board.data(), board.size()));
  TEST_ASSERT_EQUAL_MEMORY(full.data() + 8U * 4U * 3U, board.data(), board.size());
  auto reversed_params = board_params;
  reversed_params.reverse_local_strip_order = true;
  std::array<std::uint8_t, 8U * 4U * 3U> reversed{};
  TEST_ASSERT_TRUE(ledgrid::render_compiled_rainbow(
      12345, reversed_params, 256, 8, 4, reversed.data(), reversed.size()));
  constexpr std::size_t kTestStripBytes = 4U * 3U;
  for (std::size_t strip = 0; strip < 8; ++strip) {
    TEST_ASSERT_EQUAL_MEMORY(
        board.data() + (7U - strip) * kTestStripBytes,
        reversed.data() + strip * kTestStripBytes,
        kTestStripBytes);
  }
  auto changed_seed = board_params;
  changed_seed.common_seed++;
  std::array<std::uint8_t, 8U * 4U * 3U> changed{};
  ledgrid::render_compiled_rainbow(12345, changed_seed, 256, 8, 4,
                                   changed.data(), changed.size());
  TEST_ASSERT_NOT_EQUAL(0, std::memcmp(board.data(), changed.data(), board.size()));
  for (const auto& vector : ledgrid::golden_presentation_v1::kLuminanceVectors) {
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(
        vector.expected,
        ledgrid::apply_luminance_q8_8(vector.channel, vector.factor), vector.id);
  }
  TEST_ASSERT_EQUAL_UINT8(128, ledgrid::apply_luminance_q8_8(255, 128));
  TEST_ASSERT_EQUAL_UINT8(64, ledgrid::apply_luminance_q8_8(
                                    ledgrid::apply_luminance_q8_8(255, 128), 128));
}

void test_generated_cross_language_context_packets_stage_and_commit() {
  for (const auto& vector : ledgrid::golden_presentation_v1::kPresentationVectors) {
    ledgrid::ReceiverRuntime runtime(true);
    auto begin = from_hex(vector.begin_hex);
    auto set = from_hex(vector.set_hex);
    auto commit = from_hex(vector.commit_hex);
    TEST_ASSERT_EQUAL_UINT32(58, begin.size());
    TEST_ASSERT_TRUE(set.size() >= 145 && set.size() <= 187);
    TEST_ASSERT_EQUAL_UINT32(74, commit.size());
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(1, static_cast<std::uint8_t>(
        runtime.process_command(begin.data(), begin.size())), vector.id);
    TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.context_state()));
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(1, static_cast<std::uint8_t>(
        runtime.process_command(set.data(), set.size())), vector.id);
    TEST_ASSERT_EQUAL_UINT8(2, static_cast<std::uint8_t>(runtime.context_state()));
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(1, static_cast<std::uint8_t>(
        runtime.process_command(commit.data(), commit.size())), vector.id);
    TEST_ASSERT_EQUAL_UINT8(3, static_cast<std::uint8_t>(runtime.context_state()));
    TEST_ASSERT_EQUAL_MEMORY(begin.data() + 2,
                             runtime.active_context().session, 16);
    TEST_ASSERT_EQUAL_MEMORY(begin.data() + 26,
                             runtime.active_context().context_digest, 32);
  }
}

void test_context_digest_and_order_failures_preserve_active_context() {
  const auto& vector = ledgrid::golden_presentation_v1::kPresentationVectors[0];
  ledgrid::ReceiverRuntime runtime(true);
  auto begin = from_hex(vector.begin_hex);
  auto set = from_hex(vector.set_hex);
  auto commit = from_hex(vector.commit_hex);
  runtime.process_command(begin.data(), begin.size());
  auto corrupt = set;
  corrupt[94] ^= 1;
  TEST_ASSERT_EQUAL_UINT8(7, static_cast<std::uint8_t>(
      runtime.process_command(corrupt.data(), corrupt.size())));
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.context_state()));
  runtime.process_command(set.data(), set.size());
  auto wrong_commit = commit;
  wrong_commit[42] ^= 1;
  TEST_ASSERT_EQUAL_UINT8(5, static_cast<std::uint8_t>(
      runtime.process_command(wrong_commit.data(), wrong_commit.size())));
  TEST_ASSERT_EQUAL_UINT8(2, static_cast<std::uint8_t>(runtime.context_state()));
  runtime.process_command(commit.data(), commit.size());
  TEST_ASSERT_EQUAL_UINT8(3, static_cast<std::uint8_t>(runtime.context_state()));
}

void test_zero_tempo_is_valid_presentation_state_not_a_render_failure() {
  const auto& vector = ledgrid::golden_presentation_v1::kPresentationVectors[0];
  auto begin = from_hex(vector.begin_hex);
  auto set = from_hex(vector.set_hex);
  auto commit = from_hex(vector.commit_hex);
  set[95] = 0;
  set[96] = 0;
  std::uint8_t digest[32] = {};
  ledgrid::sha256(set.data() + 18, set.size() - 18, digest);
  std::memcpy(begin.data() + 26, digest, 32);
  std::memcpy(commit.data() + 42, digest, 32);
  ledgrid::ReceiverRuntime runtime(true);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      runtime.process_command(begin.data(), begin.size())));
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      runtime.process_command(set.data(), set.size())));
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      runtime.process_command(commit.data(), commit.size())));
}

void test_context_retries_revision_order_and_conflicts_are_safe() {
  const auto& vector = ledgrid::golden_presentation_v1::kPresentationVectors[0];
  ledgrid::ReceiverRuntime runtime(true);
  auto begin = from_hex(vector.begin_hex);
  auto set = from_hex(vector.set_hex);
  auto commit = from_hex(vector.commit_hex);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.process_command(begin.data(), begin.size())));
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.process_command(begin.data(), begin.size())));
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.process_command(set.data(), set.size())));
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.process_command(set.data(), set.size())));
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.process_command(commit.data(), commit.size())));
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.process_command(commit.data(), commit.size())));
  auto conflict = begin;
  conflict[57] ^= 1;
  TEST_ASSERT_EQUAL_UINT8(10, static_cast<std::uint8_t>(runtime.process_command(conflict.data(), conflict.size())));
  auto newer = from_hex(ledgrid::golden_presentation_v1::kPresentationVectors[1].begin_hex);
  std::memcpy(newer.data() + 2, begin.data() + 2, 16);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.process_command(newer.data(), newer.size())));
  TEST_ASSERT_EQUAL_UINT8(9, static_cast<std::uint8_t>(runtime.process_command(begin.data(), begin.size())));
}

void test_sha256_standard_and_fixture_vectors() {
  const std::uint8_t abc[] = {'a', 'b', 'c'};
  std::uint8_t digest[32] = {};
  ledgrid::sha256(nullptr, 0, digest);
  const auto empty = from_hex("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
  TEST_ASSERT_EQUAL_MEMORY(empty.data(), digest, 32);
  ledgrid::sha256(abc, sizeof(abc), digest);
  const auto abc_expected = from_hex("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  TEST_ASSERT_EQUAL_MEMORY(abc_expected.data(), digest, 32);
  std::memset(digest, 0xAA, sizeof(digest));
  ledgrid::sha256(nullptr, 1, digest);
  const std::uint8_t zero[32] = {};
  TEST_ASSERT_EQUAL_MEMORY(zero, digest, 32);
}

void test_status_v3_preserves_v2_prefix_and_extended_offsets() {
  ledgrid::ReceiverStatusV3 status{};
  status.flags = 3; status.active_strips = 8; status.leds_per_strip = 138;
  status.frames_accepted = 0x10203040; status.capabilities = 0x01020304;
  status.base_mode = 1; status.component_id = 0x1122;
  status.scene_epoch = 0x0102030405060708ULL;
  status.active_modifier_digest[31] = 0xAA;
  status.staged_context_digest[31] = 0xBB;
  status.active_controller_session[15] = 0xCC;
  status.staged_controller_session[15] = 0xDD;
  status.logical_receiver_id = 3;
  status.last_processed_command = 0x10;
  status.operation_sequence = 0x01020304;
  std::array<std::uint8_t, ledgrid::kStatusBytesV3> encoded{};
  TEST_ASSERT_TRUE(ledgrid::encode_receiver_status_v3(
      status, encoded.data(), encoded.size()));
  TEST_ASSERT_EQUAL_MEMORY("LGS3", encoded.data(), 4);
  TEST_ASSERT_EQUAL_UINT8(3, encoded[4]);
  TEST_ASSERT_EQUAL_HEX8(0x10, encoded[24]);
  TEST_ASSERT_EQUAL_HEX8(0x04, encoded[67]);
  TEST_ASSERT_EQUAL_HEX8(0x11, encoded[74]);
  TEST_ASSERT_EQUAL_HEX8(0x22, encoded[75]);
  TEST_ASSERT_EQUAL_HEX8(0x01, encoded[88]);
  TEST_ASSERT_EQUAL_HEX8(0xAA, encoded[239]);
  TEST_ASSERT_EQUAL_HEX8(0xBB, encoded[279]);
  TEST_ASSERT_EQUAL_HEX8(0xCC, encoded[295]);
  TEST_ASSERT_EQUAL_HEX8(0xDD, encoded[311]);
  TEST_ASSERT_EQUAL_UINT8(3, encoded[312]);
  TEST_ASSERT_EQUAL_HEX8(0x10, encoded[313]);
  TEST_ASSERT_EQUAL_HEX8(0, encoded[314]);
  TEST_ASSERT_EQUAL_HEX8(0, encoded[315]);
  TEST_ASSERT_EQUAL_HEX8(1, encoded[316]);
  TEST_ASSERT_EQUAL_HEX8(4, encoded[319]);
}

void test_status_query_is_exact_zero_padded_and_non_owning() {
  std::array<std::uint8_t, ledgrid::kStatusBytesV3> query{};
  query[0] = 0x08;
  TEST_ASSERT_TRUE(ledgrid::valid_status_query(query.data(), query.size()));
  query.back() = 1;
  TEST_ASSERT_FALSE(ledgrid::valid_status_query(query.data(), query.size()));
  query.back() = 0;
  TEST_ASSERT_FALSE(ledgrid::valid_status_query(query.data(), query.size() - 1));
  TEST_ASSERT_FALSE(ledgrid::command_may_claim_base(ledgrid::ReceiverCommand::StatusQuery));
}

void test_config_identity_is_backward_compatible_and_fail_closed() {
  std::uint8_t logical_id = 0xFF;
  const std::uint8_t legacy4[] = {0x07, 8, 0, 138};
  const std::uint8_t legacy5[] = {0x07, 8, 0, 138, 1};
  const std::uint8_t provisioned[] = {0x07, 8, 0, 138, 0, 3};
  const std::uint8_t bad[] = {0x07, 8, 0, 138, 0, 4};
  TEST_ASSERT_TRUE(ledgrid::parse_logical_receiver_id(
      legacy4, sizeof(legacy4), logical_id, &logical_id));
  TEST_ASSERT_EQUAL_HEX8(0xFF, logical_id);
  TEST_ASSERT_TRUE(ledgrid::parse_logical_receiver_id(
      legacy5, sizeof(legacy5), logical_id, &logical_id));
  TEST_ASSERT_EQUAL_HEX8(0xFF, logical_id);
  TEST_ASSERT_TRUE(ledgrid::parse_logical_receiver_id(
      provisioned, sizeof(provisioned), logical_id, &logical_id));
  TEST_ASSERT_EQUAL_UINT8(3, logical_id);
  TEST_ASSERT_FALSE(ledgrid::parse_logical_receiver_id(
      bad, sizeof(bad), logical_id, &logical_id));
}

void test_scene_time_is_common_across_different_receiver_boot_clocks() {
  const auto& vector = ledgrid::golden_presentation_v1::kPresentationVectors[0];
  auto begin = from_hex(vector.begin_hex);
  auto set = from_hex(vector.set_hex);
  auto commit = from_hex(vector.commit_hex);
  ledgrid::ReceiverRuntime first(true), second(true);
  first.process_command(begin.data(), begin.size());
  first.process_command(set.data(), set.size());
  first.process_command(commit.data(), commit.size(), 1000000);
  second.process_command(begin.data(), begin.size());
  second.process_command(set.data(), set.size());
  second.process_command(commit.data(), commit.size(), 9000000);
  TEST_ASSERT_EQUAL_UINT64(first.scene_time_us(1500000),
                           second.scene_time_us(9500000));
  const std::uint64_t before_retry = first.scene_time_us(1500000);
  first.process_command(commit.data(), commit.size(), 1400000);
  TEST_ASSERT_EQUAL_UINT64(before_retry, first.scene_time_us(1500000));
  std::array<std::uint8_t, 8 * 4 * 3> a{}, b{};
  ledgrid::LocalBackgroundParameters parameters{};
  TEST_ASSERT_TRUE(ledgrid::render_compiled_rainbow(
      first.scene_time_us(1500000), parameters, 256, 8, 4, a.data(), a.size()));
  TEST_ASSERT_TRUE(ledgrid::render_compiled_rainbow(
      second.scene_time_us(9500000), parameters, 256, 8, 4, b.data(), b.size()));
  TEST_ASSERT_EQUAL_MEMORY(a.data(), b.data(), a.size());
}

void test_staging_replacement_context_preserves_active_scene_time_until_commit() {
  const auto& original =
      ledgrid::golden_presentation_v1::kPresentationVectors[0];
  auto original_begin = from_hex(original.begin_hex);
  auto original_set = from_hex(original.set_hex);
  auto original_commit = from_hex(original.commit_hex);
  ledgrid::ReceiverRuntime runtime(true);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      runtime.process_command(original_begin.data(), original_begin.size())));
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      runtime.process_command(original_set.data(), original_set.size())));
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.process_command(
      original_commit.data(), original_commit.size(), 1000000)));
  auto start = start_command(
      30, 8, 42, runtime.active_context().scene_epoch);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      runtime.process_command(start.data(), start.size())));

  const std::uint64_t before_staging = runtime.scene_time_us(1500000);
  const auto& replacement =
      ledgrid::golden_presentation_v1::kPresentationVectors[1];
  auto replacement_begin = from_hex(replacement.begin_hex);
  auto replacement_set = from_hex(replacement.set_hex);
  auto replacement_commit = from_hex(replacement.commit_hex);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.process_command(
      replacement_begin.data(), replacement_begin.size(), 1500000)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::PresentationContextState::Staging),
      static_cast<std::uint8_t>(runtime.context_state()));
  TEST_ASSERT_EQUAL_MEMORY(
      original_commit.data() + 42,
      runtime.active_context().context_digest,
      32);
  TEST_ASSERT_EQUAL_UINT64(
      before_staging + 250000,
      runtime.scene_time_us(1750000));
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.process_command(
      replacement_set.data(), replacement_set.size(), 1750000)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::PresentationContextState::Ready),
      static_cast<std::uint8_t>(runtime.context_state()));
  TEST_ASSERT_EQUAL_UINT64(
      before_staging + 500000,
      runtime.scene_time_us(2000000));

  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.process_command(
      replacement_commit.data(), replacement_commit.size(), 2000000)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::PresentationContextState::Active),
      static_cast<std::uint8_t>(runtime.context_state()));
  const std::uint64_t replacement_scene_time =
      runtime.active_context().present_at_scene_time_us;
  TEST_ASSERT_EQUAL_UINT64(
      replacement_scene_time,
      runtime.scene_time_us(2000000));
  TEST_ASSERT_EQUAL_UINT64(
      replacement_scene_time + 250000,
      runtime.scene_time_us(2250000));
}

void test_cadence_has_no_integer_period_drift_over_thirty_minutes() {
  ledgrid::ReceiverRuntime runtime(true);
  activate_neutral_context(&runtime);
  auto start = start_command(60);
  runtime.process_command(start.data(), start.size());
  runtime.local_frame_rendered_if_current(
      runtime.render_generation(), 1000000, 0, 1);
  const std::uint64_t after_thirty_minutes = 1000000ULL + 1800000000ULL;
  TEST_ASSERT_TRUE(runtime.local_frame_due(after_thirty_minutes));
  TEST_ASSERT_EQUAL_UINT32(107999, runtime.render_stats().missed_cadence);
  runtime.local_frame_rendered_if_current(
      runtime.render_generation(), after_thirty_minutes, 1800000000, 1);
  TEST_ASSERT_FALSE(runtime.local_frame_due(after_thirty_minutes));
}

void test_local_render_generation_prevents_stale_frame_submission() {
  ledgrid::ReceiverRuntime runtime(true);
  activate_neutral_context(&runtime);
  auto start = start_command();
  runtime.process_command(start.data(), start.size());
  const std::uint32_t generation = runtime.render_generation();
  TEST_ASSERT_TRUE(runtime.local_render_still_valid(generation));
  runtime.complete_host_frame();
  TEST_ASSERT_FALSE(runtime.local_render_still_valid(generation));
  activate_neutral_context(&runtime);
  runtime.process_command(start.data(), start.size());
  const std::uint32_t restarted = runtime.render_generation();
  TEST_ASSERT_NOT_EQUAL(generation, restarted);
  const std::uint8_t stop[] = {0x11};
  runtime.process_command(stop, sizeof(stop));
  TEST_ASSERT_FALSE(runtime.local_render_still_valid(restarted));
}

void test_stale_render_and_dma_completions_cannot_mutate_newer_ownership() {
  ledgrid::ReceiverRuntime runtime(true);
  activate_neutral_context(&runtime);
  auto start = start_command();
  runtime.process_command(start.data(), start.size());
  const std::uint32_t local_generation = runtime.render_generation();
  const auto stats_before = runtime.render_stats();

  runtime.complete_host_frame();
  TEST_ASSERT_FALSE(runtime.local_render_failed_if_current(local_generation));
  TEST_ASSERT_FALSE(runtime.local_frame_rendered_if_current(
      local_generation, 1000000, 2000000, 100));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::BaseMode::HostFullScene),
      static_cast<std::uint8_t>(runtime.base_mode()));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::BaseTransitionReason::HostTakeover),
      static_cast<std::uint8_t>(runtime.transition_reason()));
  TEST_ASSERT_EQUAL_UINT32(stats_before.rendered_frames,
                           runtime.render_stats().rendered_frames);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::PresentationContextState::Active),
      static_cast<std::uint8_t>(runtime.context_state()));

  runtime.process_command(start.data(), start.size());
  const std::uint32_t stopped_generation = runtime.render_generation();
  const std::uint8_t stop[] = {0x11};
  runtime.process_command(stop, sizeof(stop));
  TEST_ASSERT_FALSE(runtime.local_render_failed_if_current(stopped_generation));
  TEST_ASSERT_FALSE(runtime.local_frame_rendered_if_current(
      stopped_generation, 3000000, 4000000, 100));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::BaseMode::StartupFallback),
      static_cast<std::uint8_t>(runtime.base_mode()));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::BaseTransitionReason::LocalStop),
      static_cast<std::uint8_t>(runtime.transition_reason()));
}

void test_parameter_and_context_updates_invalidate_inflight_local_frames() {
  ledgrid::ReceiverRuntime runtime(true);
  activate_neutral_context(&runtime);
  auto start = start_command();
  runtime.process_command(start.data(), start.size());
  const std::uint32_t before_parameters = runtime.render_generation();

  std::vector<std::uint8_t> parameters{0x12};
  append_u16(&parameters, 60);
  append_u32(&parameters, 24);
  append_u32(&parameters, 99);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::ReceiverOperationResult::Ok),
      static_cast<std::uint8_t>(
          runtime.process_command(parameters.data(), parameters.size())));
  TEST_ASSERT_FALSE(runtime.local_render_still_valid(before_parameters));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::BaseMode::LocalBackground),
      static_cast<std::uint8_t>(runtime.base_mode()));

  const std::uint32_t before_context = runtime.render_generation();
  const auto& replacement =
      ledgrid::golden_presentation_v1::kPresentationVectors[1];
  auto begin = from_hex(replacement.begin_hex);
  auto set = from_hex(replacement.set_hex);
  auto commit = from_hex(replacement.commit_hex);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      runtime.process_command(begin.data(), begin.size(), 5000000)));
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      runtime.process_command(set.data(), set.size(), 5000000)));
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      runtime.process_command(commit.data(), commit.size(), 5000000)));
  TEST_ASSERT_FALSE(runtime.local_render_still_valid(before_context));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::BaseMode::LocalBackground),
      static_cast<std::uint8_t>(runtime.base_mode()));
  TEST_ASSERT_EQUAL_UINT64(runtime.active_context().scene_epoch,
                           runtime.local_parameters().scene_epoch);
  const std::uint32_t after_context = runtime.render_generation();
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      runtime.process_command(commit.data(), commit.size(), 9000000)));
  TEST_ASSERT_EQUAL_UINT32(after_context, runtime.render_generation());
}

void test_live_dispatch_policy_is_exhaustive_and_feature_gated() {
  const std::array<ledgrid::BaseMode, 3> modes = {
      ledgrid::BaseMode::StartupFallback,
      ledgrid::BaseMode::LocalBackground,
      ledgrid::BaseMode::HostFullScene,
  };
  for (const auto mode : modes) {
    for (unsigned value = 0; value <= 0xFF; ++value) {
      const auto command = canonical_dispatch_command(value);
      const auto decision = ledgrid::classify_receiver_dispatch(
          command.data(), command.size(), 12, mode, true);
      ledgrid::ReceiverDispatchRoute expected =
          ledgrid::ReceiverDispatchRoute::Reject;
      switch (static_cast<ledgrid::ReceiverCommand>(value)) {
        case ledgrid::ReceiverCommand::Ping:
        case ledgrid::ReceiverCommand::SetPixel:
        case ledgrid::ReceiverCommand::SetBrightness:
        case ledgrid::ReceiverCommand::Show:
        case ledgrid::ReceiverCommand::Clear:
        case ledgrid::ReceiverCommand::SetRange:
        case ledgrid::ReceiverCommand::Config:
          expected = ledgrid::ReceiverDispatchRoute::Operational;
          break;
        case ledgrid::ReceiverCommand::SetAll:
          expected = ledgrid::ReceiverDispatchRoute::HostFullFrame;
          break;
        case ledgrid::ReceiverCommand::StatusQuery:
          expected = ledgrid::ReceiverDispatchRoute::StatusQuery;
          break;
        case ledgrid::ReceiverCommand::LocalBackgroundStart:
        case ledgrid::ReceiverCommand::LocalBackgroundStop:
        case ledgrid::ReceiverCommand::LocalBackgroundParameters:
        case ledgrid::ReceiverCommand::PresentationContextBegin:
        case ledgrid::ReceiverCommand::PresentationContextSet:
        case ledgrid::ReceiverCommand::PresentationContextCommit:
        case ledgrid::ReceiverCommand::ControllerSessionBegin:
        case ledgrid::ReceiverCommand::OverlayBegin:
        case ledgrid::ReceiverCommand::OverlayPatch:
        case ledgrid::ReceiverCommand::OverlayCommit:
        case ledgrid::ReceiverCommand::OverlayClear:
        case ledgrid::ReceiverCommand::OverlayRenew:
        case ledgrid::ReceiverCommand::OverlayPatchBatch:
          expected = ledgrid::ReceiverDispatchRoute::Runtime;
          break;
        default: break;
      }
      TEST_ASSERT_EQUAL_UINT8(
          static_cast<std::uint8_t>(expected),
          static_cast<std::uint8_t>(decision.route));
      const bool set_all =
          value == static_cast<unsigned>(ledgrid::ReceiverCommand::SetAll);
      const bool start = value == static_cast<unsigned>(
          ledgrid::ReceiverCommand::LocalBackgroundStart);
      const bool host_refresh = mode == ledgrid::BaseMode::HostFullScene &&
          (value == 0x02 || value == 0x03 || value == 0x04);
      TEST_ASSERT_EQUAL(set_all || start, decision.may_claim_base);
      TEST_ASSERT_EQUAL(set_all || host_refresh,
                        decision.publishes_host_frame);

      const auto disabled = ledgrid::classify_receiver_dispatch(
          command.data(), command.size(), 12, mode, false);
      if (expected == ledgrid::ReceiverDispatchRoute::Runtime) {
        TEST_ASSERT_EQUAL_UINT8(
            static_cast<std::uint8_t>(ledgrid::ReceiverDispatchRoute::Reject),
            static_cast<std::uint8_t>(disabled.route));
        TEST_ASSERT_EQUAL_UINT8(
            static_cast<std::uint8_t>(ledgrid::ReceiverOperationResult::Unsupported),
            static_cast<std::uint8_t>(disabled.result));
      } else {
        TEST_ASSERT_EQUAL_UINT8(
            static_cast<std::uint8_t>(expected),
            static_cast<std::uint8_t>(disabled.route));
      }
    }
  }

  const std::uint8_t short_set_all[] = {0x06};
  const auto invalid = ledgrid::classify_receiver_dispatch(
      short_set_all, sizeof(short_set_all), 12,
      ledgrid::BaseMode::LocalBackground, true);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::ReceiverDispatchRoute::Reject),
      static_cast<std::uint8_t>(invalid.route));
  TEST_ASSERT_FALSE(invalid.publishes_host_frame);
  TEST_ASSERT_FALSE(invalid.may_claim_base);
}

void test_crc_gate_rejects_corruption_before_runtime_mutation() {
  std::array<std::uint8_t, 3> packet = {0xFF, 0, 0};
  const std::uint16_t crc =
      ledgrid::animation_pipeline_crc16_ccitt(packet.data(), 1);
  packet[1] = static_cast<std::uint8_t>(crc >> 8U);
  packet[2] = static_cast<std::uint8_t>(crc);
  TEST_ASSERT_TRUE(ledgrid::receiver_packet_crc_valid(
      packet.data(), packet.size()));
  packet[2] ^= 1U;

  ledgrid::ReceiverRuntime runtime(true);
  const auto initial_mode = runtime.base_mode();
  const std::uint32_t initial_generation = runtime.render_generation();
  if (ledgrid::receiver_packet_crc_valid(packet.data(), packet.size())) {
    runtime.complete_host_frame();
  }
  TEST_ASSERT_EQUAL_UINT8(static_cast<std::uint8_t>(initial_mode),
                          static_cast<std::uint8_t>(runtime.base_mode()));
  TEST_ASSERT_EQUAL_UINT32(initial_generation, runtime.render_generation());
}

void test_receiver_control_and_display_tasks_use_separate_cores() {
  TEST_ASSERT_EQUAL_INT(0, ledgrid::kReceiverSpiTaskCore);
  TEST_ASSERT_EQUAL_INT(1, ledgrid::kReceiverDisplayTaskCore);
  TEST_ASSERT_EQUAL_UINT(3, ledgrid::kReceiverDisplayTaskPriority);
  TEST_ASSERT_NOT_EQUAL(ledgrid::kReceiverSpiTaskCore,
                        ledgrid::kReceiverDisplayTaskCore);
}

void test_render_set_all_submit_interleaving_has_one_linearization_point() {
  ledgrid::ReceiverRuntime runtime(true);
  ledgrid::ReceiverOutputState output(8, 138, 50);
  activate_neutral_context(&runtime);
  auto start = start_command();
  runtime.process_command(start.data(), start.size());

  // The display task has completed rendering but has not entered the shared
  // submit critical section. A SET_ALL takeover linearizes first.
  const auto rendered = ledgrid::capture_render_ticket(runtime, output);
  runtime.complete_host_frame();
  FakePhysicalSubmitter submitter{};
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::PhysicalSubmitResult::Stale),
      static_cast<std::uint8_t>(ledgrid::submit_rendered_frame_if_current(
          runtime, output, rendered, fake_physical_submit, &submitter)));
  TEST_ASSERT_EQUAL_UINT32(0, submitter.calls);

  // In the inverse ordering, submission linearizes before the takeover. The
  // production callback only queues DMA and never waits while holding the lock.
  ledgrid::ReceiverRuntime submit_first(true);
  activate_neutral_context(&submit_first);
  submit_first.process_command(start.data(), start.size());
  const auto current = ledgrid::capture_render_ticket(submit_first, output);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::PhysicalSubmitResult::Submitted),
      static_cast<std::uint8_t>(ledgrid::submit_rendered_frame_if_current(
          submit_first, output, current, fake_physical_submit, &submitter)));
  submit_first.complete_host_frame();
  TEST_ASSERT_EQUAL_UINT32(1, submitter.calls);
}

void test_config_and_brightness_interleavings_invalidate_render_tickets() {
  ledgrid::ReceiverOutputState output(8, 138, 50);
  ledgrid::ReceiverRuntime startup(false);
  FakePhysicalSubmitter submitter{};
  const auto old_geometry = ledgrid::capture_render_ticket(startup, output);
  TEST_ASSERT_TRUE(output.configure(8, 140));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::PhysicalSubmitResult::Stale),
      static_cast<std::uint8_t>(ledgrid::submit_rendered_frame_if_current(
          startup, output, old_geometry, fake_physical_submit, &submitter)));
  TEST_ASSERT_EQUAL_UINT32(0, submitter.calls);

  const auto old_brightness = ledgrid::capture_render_ticket(startup, output);
  TEST_ASSERT_TRUE(output.set_brightness(200));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::PhysicalSubmitResult::Stale),
      static_cast<std::uint8_t>(ledgrid::submit_rendered_frame_if_current(
          startup, output, old_brightness, fake_physical_submit, &submitter)));
  const auto current = ledgrid::capture_render_ticket(startup, output);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::PhysicalSubmitResult::Submitted),
      static_cast<std::uint8_t>(ledgrid::submit_rendered_frame_if_current(
          startup, output, current, fake_physical_submit, &submitter)));
  TEST_ASSERT_EQUAL_UINT8(8, submitter.last_output.strip_count);
  TEST_ASSERT_EQUAL_UINT16(140, submitter.last_output.leds_per_strip);
  TEST_ASSERT_EQUAL_UINT8(200, submitter.last_output.brightness);

  ledgrid::ReceiverRuntime local(true);
  activate_neutral_context(&local);
  auto start = start_command();
  local.process_command(start.data(), start.size());
  const auto local_old_geometry =
      ledgrid::capture_render_ticket(local, output);
  TEST_ASSERT_TRUE(output.configure(8, 139));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::PhysicalSubmitResult::Stale),
      static_cast<std::uint8_t>(ledgrid::submit_rendered_frame_if_current(
          local, output, local_old_geometry, fake_physical_submit,
          &submitter)));
  const auto local_old_brightness =
      ledgrid::capture_render_ticket(local, output);
  TEST_ASSERT_TRUE(output.set_brightness(201));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::PhysicalSubmitResult::Stale),
      static_cast<std::uint8_t>(ledgrid::submit_rendered_frame_if_current(
          local, output, local_old_brightness, fake_physical_submit,
          &submitter)));
}

void test_operation_sequence_saturates_and_fails_closed_at_boundary() {
  ledgrid::ReceiverOperationTracker tracker(UINT32_MAX - 1U, 0x05);
  TEST_ASSERT_TRUE(tracker.begin(0x06));
  TEST_ASSERT_EQUAL_UINT32(UINT32_MAX, tracker.sequence());
  TEST_ASSERT_EQUAL_HEX8(0x06, tracker.last_processed_command());
  TEST_ASSERT_TRUE(tracker.exhausted());
  TEST_ASSERT_FALSE(tracker.begin(0x10));
  TEST_ASSERT_EQUAL_UINT32(UINT32_MAX, tracker.sequence());
  TEST_ASSERT_EQUAL_HEX8(0x06, tracker.last_processed_command());
}

void test_sparse_full_snapshot_schedules_composites_and_delta_clears() {
  ledgrid::ReceiverRuntime runtime(true);
  activate_local_hybrid(&runtime);
  const std::uint64_t now = 1000000;
  const std::uint64_t present = runtime.scene_time_us(now) + 1000;
  auto begin = overlay_begin_command(
      runtime, 1, 0, ledgrid::OverlayUpdateKind::FullSnapshot, 2, 3000);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      runtime.process_command(begin.data(), begin.size(), now)));
  // Staging is not visible when there is no prior committed foreground.
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::ForegroundState::Staging),
      static_cast<std::uint8_t>(runtime.foreground_state()));
  const auto staged_status = runtime.overlay_status(now);
  TEST_ASSERT_EQUAL_UINT64(0, staged_status.committed_generation);
  TEST_ASSERT_EQUAL_UINT64(1, staged_status.staged_generation);
  TEST_ASSERT_EQUAL_UINT64(runtime.active_context().scene_revision,
                           staged_status.scene_revision);
  TEST_ASSERT_EQUAL_UINT64(runtime.active_context().scene_epoch,
                           staged_status.scene_epoch);
  TEST_ASSERT_EQUAL_UINT64(runtime.active_context().scene_revision,
                           staged_status.base_revision);
  TEST_ASSERT_EQUAL_UINT64(0, staged_status.present_at_scene_time_us);
  TEST_ASSERT_EQUAL_UINT32(3000, staged_status.lease_ms);
  TEST_ASSERT_EQUAL_UINT32(3000, staged_status.lease_remaining_ms);
  TEST_ASSERT_EQUAL_UINT16(0,
                           staged_status.committed_coverage_pixels);

  std::vector<std::uint8_t> first(1016U * 4U, 0);
  first[0] = 0; first[1] = 0; first[2] = 0; first[3] = 255;  // black
  first[4] = 64; first[5] = 0; first[6] = 0; first[7] = 128;
  auto patch0 = overlay_patch_command(runtime, 1, 0, first);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.process_command(
      patch0.data(), patch0.size(), now)));
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.process_command(
      patch0.data(), patch0.size(), now)));
  TEST_ASSERT_EQUAL_UINT8(2, static_cast<std::uint8_t>(
      runtime.last_overlay_result()));
  auto conflicting = patch0;
  conflicting[30] = 1;
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::ReceiverOperationResult::Conflict),
      static_cast<std::uint8_t>(runtime.process_command(
          conflicting.data(), conflicting.size(), now)));

  std::vector<std::uint8_t> tail(88U * 4U, 0);
  tail[tail.size() - 4] = 0;
  tail[tail.size() - 3] = 255;
  tail[tail.size() - 2] = 0;
  tail[tail.size() - 1] = 255;
  auto patch1 = overlay_patch_command(runtime, 1, 1016, tail);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.process_command(
      patch1.data(), patch1.size(), now)));
  auto commit = overlay_commit_command(runtime, 1, present);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.process_command(
      commit.data(), commit.size(), now)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::ForegroundState::Staging),
      static_cast<std::uint8_t>(runtime.foreground_state()));
  const auto pending_status = runtime.overlay_status(now);
  TEST_ASSERT_EQUAL_UINT64(1, pending_status.staged_generation);
  TEST_ASSERT_EQUAL_UINT64(runtime.active_context().scene_revision,
                           pending_status.scene_revision);
  TEST_ASSERT_EQUAL_UINT64(runtime.active_context().scene_epoch,
                           pending_status.scene_epoch);
  TEST_ASSERT_EQUAL_UINT64(runtime.active_context().scene_revision,
                           pending_status.base_revision);
  TEST_ASSERT_EQUAL_UINT64(present,
                           pending_status.present_at_scene_time_us);
  TEST_ASSERT_EQUAL_UINT32(3000, pending_status.lease_ms);
  TEST_ASSERT_EQUAL_UINT32(3000, pending_status.lease_remaining_ms);
  TEST_ASSERT_EQUAL_UINT16(0,
                           pending_status.committed_coverage_pixels);
  TEST_ASSERT_FALSE(runtime.service_foreground(now + 999));
  TEST_ASSERT_TRUE(runtime.service_foreground(now + 1000));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::ForegroundState::Active),
      static_cast<std::uint8_t>(runtime.foreground_state()));
  TEST_ASSERT_EQUAL_UINT16(3,
      runtime.overlay_status(now + 1000).committed_coverage_pixels);
  const auto active_status = runtime.overlay_status(now + 1000);
  TEST_ASSERT_EQUAL_UINT64(0, active_status.staged_generation);
  TEST_ASSERT_EQUAL_UINT64(present,
                           active_status.present_at_scene_time_us);
  TEST_ASSERT_EQUAL_UINT32(3000, active_status.lease_ms);

  std::vector<std::uint8_t> base(ledgrid::kContractLocalPixels * 3U, 100);
  std::vector<std::uint8_t> composite(base.size(), 0);
  TEST_ASSERT_TRUE(runtime.composite_foreground(
      base.data(), ledgrid::kContractLocalPixels,
      composite.data(), composite.size()));
  TEST_ASSERT_EQUAL_UINT8(0, composite[0]);
  TEST_ASSERT_EQUAL_UINT8(0, composite[1]);
  TEST_ASSERT_EQUAL_UINT8(0, composite[2]);
  TEST_ASSERT_EQUAL_UINT8(114, composite[3]);
  TEST_ASSERT_EQUAL_UINT8(50, composite[4]);
  TEST_ASSERT_EQUAL_UINT8(50, composite[5]);
  TEST_ASSERT_EQUAL_UINT8(0, composite[composite.size() - 3]);
  TEST_ASSERT_EQUAL_UINT8(255, composite[composite.size() - 2]);
  TEST_ASSERT_EQUAL_UINT8(0, composite[composite.size() - 1]);

  const auto base_stats = runtime.render_stats();
  auto noop = overlay_begin_command(
      runtime, 2, 1, ledgrid::OverlayUpdateKind::Delta, 0, 3000);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.process_command(
      noop.data(), noop.size(), now + 1100)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::ForegroundState::Staging),
      static_cast<std::uint8_t>(runtime.foreground_state()));
  const auto replacement_status = runtime.overlay_status(now + 1100);
  TEST_ASSERT_EQUAL_UINT64(2, replacement_status.staged_generation);
  TEST_ASSERT_EQUAL_UINT16(3,
                           replacement_status.committed_coverage_pixels);
  // Staging is observable while the prior committed plane remains visible.
  runtime.composite_foreground(base.data(), ledgrid::kContractLocalPixels,
                               composite.data(), composite.size());
  TEST_ASSERT_EQUAL_UINT8(0, composite[0]);
  auto noop_commit = overlay_commit_command(runtime, 2,
      runtime.scene_time_us(now + 1100));
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.process_command(
      noop_commit.data(), noop_commit.size(), now + 1100)));
  TEST_ASSERT_EQUAL_UINT64(2,
      runtime.overlay_status(now + 1100).committed_generation);
  TEST_ASSERT_EQUAL_UINT32(base_stats.rendered_frames,
                           runtime.render_stats().rendered_frames);

  auto delta = overlay_begin_command(
      runtime, 3, 2, ledgrid::OverlayUpdateKind::Delta, 1, 3000);
  runtime.process_command(delta.data(), delta.size(), now + 1200);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::ForegroundState::Staging),
      static_cast<std::uint8_t>(runtime.foreground_state()));
  std::vector<std::uint8_t> moved(5U * 4U, 0);
  moved[16] = 0; moved[17] = 0; moved[18] = 0; moved[19] = 255;
  auto moved_patch = overlay_patch_command(runtime, 3, 0, moved);
  runtime.process_command(moved_patch.data(), moved_patch.size(), now + 1200);
  auto moved_commit = overlay_commit_command(
      runtime, 3, runtime.scene_time_us(now + 1200));
  runtime.process_command(moved_commit.data(), moved_commit.size(), now + 1200);
  runtime.composite_foreground(base.data(), ledgrid::kContractLocalPixels,
                               composite.data(), composite.size());
  TEST_ASSERT_EQUAL_UINT8(100, composite[0]);
  TEST_ASSERT_EQUAL_UINT8(0, composite[12]);
  TEST_ASSERT_EQUAL_UINT16(2,
      runtime.overlay_status(now + 1200).committed_coverage_pixels);
  TEST_ASSERT_EQUAL_UINT32(base_stats.rendered_frames,
                           runtime.render_stats().rendered_frames);
}

void test_sparse_order_interruption_session_lease_restart_and_takeover() {
  ledgrid::ReceiverRuntime runtime(true);
  activate_local_hybrid(&runtime);
  const std::uint64_t now = 5000000;
  auto begin = overlay_begin_command(
      runtime, 1, 0, ledgrid::OverlayUpdateKind::FullSnapshot, 2, 10);
  runtime.process_command(begin.data(), begin.size(), now);
  std::vector<std::uint8_t> tail(88U * 4U, 0);
  auto out_of_order = overlay_patch_command(runtime, 1, 1016, tail);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::ReceiverOperationResult::InvalidState),
      static_cast<std::uint8_t>(runtime.process_command(
          out_of_order.data(), out_of_order.size(), now)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::PatchOrder),
      static_cast<std::uint8_t>(runtime.last_overlay_result()));

  // Clear aborts interrupted staging without ever revealing it.
  auto clear = overlay_clear_command(runtime, 2);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.process_command(
      clear.data(), clear.size(), now)));
  TEST_ASSERT_EQUAL_UINT8(0, static_cast<std::uint8_t>(runtime.foreground_state()));
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.process_command(
      clear.data(), clear.size(), now)));
  TEST_ASSERT_EQUAL_UINT8(2, static_cast<std::uint8_t>(
      runtime.last_overlay_result()));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::StaleGeneration),
      static_cast<std::uint8_t>(runtime.process_command(
          begin.data(), begin.size(), now)));

  auto delta = overlay_begin_command(
      runtime, 3, 2, ledgrid::OverlayUpdateKind::Delta, 1, 2);
  runtime.process_command(delta.data(), delta.size(), now);
  std::vector<std::uint8_t> black{0, 0, 0, 255};
  auto black_patch = overlay_patch_command(runtime, 3, 7, black);
  runtime.process_command(black_patch.data(), black_patch.size(), now);
  auto commit = overlay_commit_command(runtime, 3, runtime.scene_time_us(now));
  runtime.process_command(commit.data(), commit.size(), now);
  auto renew = overlay_renew_command(runtime, 3, 4);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.process_command(
      renew.data(), renew.size(), now + 1000)));
  TEST_ASSERT_FALSE(runtime.service_foreground(now + 4999));
  TEST_ASSERT_TRUE(runtime.service_foreground(now + 5000));
  TEST_ASSERT_EQUAL_UINT8(0, static_cast<std::uint8_t>(runtime.foreground_state()));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::LeaseExpired),
      static_cast<std::uint8_t>(runtime.last_overlay_result()));

  auto post_expiry_clear = overlay_clear_command(runtime, 4);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.process_command(
      post_expiry_clear.data(), post_expiry_clear.size(), now + 5500)));
  auto held = overlay_begin_command(
      runtime, 5, 4, ledgrid::OverlayUpdateKind::Delta, 1, 0);
  runtime.process_command(held.data(), held.size(), now + 6000);
  auto held_patch = overlay_patch_command(runtime, 5, 8, black);
  runtime.process_command(held_patch.data(), held_patch.size(), now + 6000);
  auto held_commit = overlay_commit_command(
      runtime, 5, runtime.scene_time_us(now + 6000));
  runtime.process_command(held_commit.data(), held_commit.size(), now + 6000);
  TEST_ASSERT_FALSE(runtime.service_foreground(now + 1000000));

  // Compensation uses a new generation. Reusing a committed content
  // generation for a different CLEAR operation is a conflict.
  auto same_generation_clear = overlay_clear_command(runtime, 5);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::ReceiverOperationResult::Conflict),
      static_cast<std::uint8_t>(runtime.process_command(
          same_generation_clear.data(), same_generation_clear.size(),
          now + 6500)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::GenerationConflict),
      static_cast<std::uint8_t>(runtime.last_overlay_result()));
  auto compensation = overlay_clear_command(runtime, 6);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.process_command(
      compensation.data(), compensation.size(), now + 6500)));
  TEST_ASSERT_EQUAL_UINT8(0, static_cast<std::uint8_t>(runtime.foreground_state()));

  auto replacement_foreground = overlay_begin_command(
      runtime, 7, 6, ledgrid::OverlayUpdateKind::Delta, 1, 0);
  runtime.process_command(replacement_foreground.data(),
                          replacement_foreground.size(), now + 6600);
  auto replacement_patch = overlay_patch_command(runtime, 7, 8, black);
  runtime.process_command(replacement_patch.data(), replacement_patch.size(),
                          now + 6600);
  auto replacement_commit = overlay_commit_command(
      runtime, 7, runtime.scene_time_us(now + 6600));
  runtime.process_command(replacement_commit.data(), replacement_commit.size(),
                          now + 6600);

  auto replacement = session_command(runtime, 2, 0xBB);
  replacement[2] ^= 0x80;
  runtime.process_command(replacement.data(), replacement.size(), now + 7000);
  TEST_ASSERT_EQUAL_UINT8(2, static_cast<std::uint8_t>(runtime.foreground_state()));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::ReceiverOperationResult::InvalidState),
      static_cast<std::uint8_t>(runtime.process_command(
          renew.data(), renew.size(), now + 7000)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::StaleSession),
      static_cast<std::uint8_t>(runtime.last_overlay_result()));
  runtime.complete_host_frame();
  TEST_ASSERT_EQUAL_UINT8(0, static_cast<std::uint8_t>(runtime.foreground_state()));
  TEST_ASSERT_EQUAL_UINT8(2, static_cast<std::uint8_t>(runtime.base_mode()));
  runtime.receiver_restart();
  TEST_ASSERT_EQUAL_UINT8(0, static_cast<std::uint8_t>(runtime.base_mode()));
  TEST_ASSERT_EQUAL_UINT64(0,
      runtime.overlay_status(now + 7000).committed_generation);
}

void test_sparse_generation_counter_exhaustion_is_fail_closed() {
  ledgrid::ReceiverRuntime runtime(true);
  activate_local_hybrid(&runtime);
  auto maximum_clear = overlay_clear_command(runtime, UINT64_MAX);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.process_command(
      maximum_clear.data(), maximum_clear.size(), 1000000)));
  auto exhausted = overlay_begin_command(
      runtime, UINT64_MAX, UINT64_MAX,
      ledgrid::OverlayUpdateKind::Delta, 0, 0);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::ReceiverOperationResult::InvalidState),
      static_cast<std::uint8_t>(runtime.process_command(
          exhausted.data(), exhausted.size(), 1000000)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::CounterExhausted),
      static_cast<std::uint8_t>(runtime.last_overlay_result()));
  TEST_ASSERT_EQUAL_UINT64(UINT64_MAX,
      runtime.overlay_status(1000000).committed_generation);
}

void test_sparse_batch_applies_multiple_spans_atomically_and_retries_exactly() {
  ledgrid::ReceiverRuntime runtime(true);
  activate_local_hybrid(&runtime);
  const std::uint64_t now = 7000000;
  auto begin = overlay_begin_command(
      runtime, 1, 0, ledgrid::OverlayUpdateKind::FullSnapshot, 2, 3000);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      runtime.process_command(begin.data(), begin.size(), now)));

  std::vector<std::uint8_t> first(1015U * 4U, 0);
  first[0] = 12;
  first[3] = 12;
  std::vector<std::uint8_t> tail(89U * 4U, 0);
  tail[tail.size() - 3U] = 255;
  tail[tail.size() - 1U] = 255;
  auto first_batch = overlay_patch_batch_command(
      runtime, 1, {{0, first}});
  auto tail_batch = overlay_patch_batch_command(
      runtime, 1, {{1015, tail}});
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      runtime.process_command(first_batch.data(), first_batch.size(), now)));
  TEST_ASSERT_EQUAL_UINT16(1, runtime.overlay_status(now).accepted_patches);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      runtime.process_command(tail_batch.data(), tail_batch.size(), now)));
  TEST_ASSERT_EQUAL_UINT16(2, runtime.overlay_status(now).accepted_patches);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      runtime.process_command(tail_batch.data(), tail_batch.size(), now)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::Idempotent),
      static_cast<std::uint8_t>(runtime.last_overlay_result()));
  TEST_ASSERT_EQUAL_UINT16(2, runtime.overlay_status(now).accepted_patches);

  auto conflicting_retry = tail_batch;
  conflicting_retry[conflicting_retry.size() - 3U] ^= 1U;
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::ReceiverOperationResult::Conflict),
      static_cast<std::uint8_t>(runtime.process_command(
          conflicting_retry.data(), conflicting_retry.size(), now)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::PatchConflict),
      static_cast<std::uint8_t>(runtime.last_overlay_result()));
  TEST_ASSERT_EQUAL_UINT16(2, runtime.overlay_status(now).accepted_patches);

  auto commit = overlay_commit_command(
      runtime, 1, runtime.scene_time_us(now));
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      runtime.process_command(commit.data(), commit.size(), now)));
  std::vector<std::uint8_t> base(ledgrid::kContractLocalPixels * 3U, 100);
  std::vector<std::uint8_t> composite(base.size(), 0);
  TEST_ASSERT_TRUE(runtime.composite_foreground(
      base.data(), ledgrid::kContractLocalPixels,
      composite.data(), composite.size()));
  TEST_ASSERT_EQUAL_UINT8(107, composite[0]);
  TEST_ASSERT_EQUAL_UINT8(95, composite[1]);
  TEST_ASSERT_EQUAL_UINT8(95, composite[2]);
  TEST_ASSERT_EQUAL_UINT8(0, composite[composite.size() - 3U]);
  TEST_ASSERT_EQUAL_UINT8(255, composite[composite.size() - 2U]);
  TEST_ASSERT_EQUAL_UINT8(0, composite[composite.size() - 1U]);
}

void test_sparse_batch_rejects_malformed_unsorted_overlap_and_premul_atomically() {
  ledgrid::ReceiverRuntime runtime(true);
  activate_local_hybrid(&runtime);
  const std::uint64_t now = 8000000;
  auto begin = overlay_begin_command(
      runtime, 1, 0, ledgrid::OverlayUpdateKind::Delta, 2, 3000);
  // A new session requires a repair snapshot first.
  publish_full_snapshot(&runtime, 1, 0, 3000, now);
  begin = overlay_begin_command(
      runtime, 2, 1, ledgrid::OverlayUpdateKind::Delta, 2, 3000);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      runtime.process_command(begin.data(), begin.size(), now + 100)));

  const auto assert_atomic_reject = [&](std::vector<std::uint8_t> batch,
                                        ledgrid::OverlayOperationResult expected) {
    TEST_ASSERT_NOT_EQUAL(1, static_cast<std::uint8_t>(
        runtime.process_command(batch.data(), batch.size(), now + 100)));
    TEST_ASSERT_EQUAL_UINT8(static_cast<std::uint8_t>(expected),
                            static_cast<std::uint8_t>(
                                runtime.last_overlay_result()));
    TEST_ASSERT_EQUAL_UINT16(0,
                             runtime.overlay_status(now + 100).accepted_patches);
  };

  auto unsorted = overlay_patch_batch_command(
      runtime, 2, {{20, {0, 0, 0, 255}}, {10, {0, 0, 0, 255}}});
  assert_atomic_reject(unsorted, ledgrid::OverlayOperationResult::PatchOrder);
  auto overlap = overlay_patch_batch_command(
      runtime, 2, {{10, {0, 0, 0, 255, 0, 0, 0, 0}},
                   {11, {0, 0, 0, 255}}});
  assert_atomic_reject(overlap, ledgrid::OverlayOperationResult::PatchOverlap);
  auto bad_premul = overlay_patch_batch_command(
      runtime, 2, {{10, {2, 0, 0, 1}}, {20, {0, 0, 0, 255}}});
  assert_atomic_reject(bad_premul,
                       ledgrid::OverlayOperationResult::UnsupportedFormat);
  auto truncated = overlay_patch_batch_command(
      runtime, 2, {{10, {0, 0, 0, 255}}, {20, {0, 0, 0, 255}}});
  truncated.pop_back();
  assert_atomic_reject(truncated, ledgrid::OverlayOperationResult::InvalidSize);
  auto zero_spans = overlay_patch_batch_command(
      runtime, 2, {{10, {0, 0, 0, 255}}});
  zero_spans.resize(ledgrid::kOverlayPatchBatchHeaderBytes);
  zero_spans[26] = 0;
  zero_spans[27] = 0;
  assert_atomic_reject(zero_spans,
                       ledgrid::OverlayOperationResult::InvalidSize);
  auto out_of_bounds = overlay_patch_batch_command(
      runtime, 2, {{1103, {0, 0, 0, 255, 0, 0, 0, 0}},
                   {20, {0, 0, 0, 255}}});
  assert_atomic_reject(out_of_bounds,
                       ledgrid::OverlayOperationResult::OutOfBounds);

  auto valid = overlay_patch_batch_command(
      runtime, 2, {{10, {0, 0, 0, 255}}, {20, {0, 0, 0, 255}}});
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      runtime.process_command(valid.data(), valid.size(), now + 100)));
  TEST_ASSERT_EQUAL_UINT16(2, runtime.overlay_status(now + 100).accepted_patches);
  const auto render_before = runtime.render_stats().rendered_frames;
  auto commit = overlay_commit_command(
      runtime, 2, runtime.scene_time_us(now + 100));
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      runtime.process_command(commit.data(), commit.size(), now + 100)));
  TEST_ASSERT_EQUAL_UINT32(render_before, runtime.render_stats().rendered_frames);
}

void test_sparse_batch_accepts_exact_maximum_span_capacity() {
  ledgrid::ReceiverRuntime runtime(true);
  activate_local_hybrid(&runtime);
  const std::uint64_t now = 9000000;
  publish_full_snapshot(&runtime, 1, 0, 3000, now);
  auto begin = overlay_begin_command(
      runtime, 2, 1, ledgrid::OverlayUpdateKind::Delta,
      static_cast<std::uint16_t>(ledgrid::kMaxSinglePixelSpansPerBatch), 3000);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      runtime.process_command(begin.data(), begin.size(), now + 100)));
  std::vector<BatchSpan> spans;
  spans.reserve(ledgrid::kMaxSinglePixelSpansPerBatch);
  for (std::size_t span = 0;
       span < ledgrid::kMaxSinglePixelSpansPerBatch; ++span) {
    spans.push_back({static_cast<std::uint16_t>(span * 2U),
                     {0, 0, 0, 255}});
  }
  auto batch = overlay_patch_batch_command(runtime, 2, spans);
  TEST_ASSERT_EQUAL_UINT32(
      ledgrid::kOverlayPatchBatchHeaderBytes +
          ledgrid::kMaxSinglePixelSpansPerBatch *
              (ledgrid::kOverlayPatchBatchSpanHeaderBytes +
               ledgrid::kPremultipliedRgbaBytesPerPixel),
      batch.size());
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      runtime.process_command(batch.data(), batch.size(), now + 100)));
  TEST_ASSERT_EQUAL_UINT16(
      ledgrid::kMaxSinglePixelSpansPerBatch,
      runtime.overlay_status(now + 100).accepted_patches);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      runtime.process_command(batch.data(), batch.size(), now + 100)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::Idempotent),
      static_cast<std::uint8_t>(runtime.last_overlay_result()));
  TEST_ASSERT_EQUAL_UINT16(
      ledgrid::kMaxSinglePixelSpansPerBatch,
      runtime.overlay_status(now + 100).accepted_patches);
}

void test_generated_malformed_batches_reject_with_exact_runtime_results() {
  using namespace ledgrid::golden_v1;
  for (const auto& vector : kMalformedBatchPacketVectors) {
    ledgrid::ReceiverRuntime runtime(true);
    activate_neutral_context(&runtime);
    auto start = start_command(
        60, 0, 42, runtime.active_context().scene_epoch);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(
        1, static_cast<std::uint8_t>(
               runtime.process_command(start.data(), start.size(), 1000000)),
        vector.id);

    std::vector<std::uint8_t> session{0x20, 1};
    session.insert(session.end(), vector.packet + 2U, vector.packet + 18U);
    append_u64(&session, 1);
    session.insert(session.end(), 32, 0xA5);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(
        1, static_cast<std::uint8_t>(runtime.process_command(
               session.data(), session.size(), 1000000)),
        vector.id);

    // Establish authoritative empty generation 1 so malformed sparse deltas
    // exercise their own span validation instead of the session-repair gate.
    auto clear = overlay_clear_command(runtime, 1);
    std::memcpy(clear.data() + 2U, vector.packet + 2U,
                ledgrid::kControllerSessionBytes);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(
        1, static_cast<std::uint8_t>(runtime.process_command(
               clear.data(), clear.size(), 1000000)),
        vector.id);

    const std::uint64_t generation = read_u64(vector.packet + 18U);
    auto begin = overlay_begin_command(
        runtime, generation, 1, ledgrid::OverlayUpdateKind::Delta,
        static_cast<std::uint16_t>(
            std::max<std::uint16_t>(1U, read_u16(vector.packet + 26U))),
        3000);
    std::memcpy(begin.data() + 2U, vector.packet + 2U,
                ledgrid::kControllerSessionBytes);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(
        1, static_cast<std::uint8_t>(runtime.process_command(
               begin.data(), begin.size(), 1000000)),
        vector.id);

    // The generated packet includes its trailing transport CRC. The runtime
    // sees the CRC-validated payload only; even the 4,098-byte over-capacity
    // vector remains safely bounded by its generated array.
    TEST_ASSERT_TRUE_MESSAGE(
        vector.packet_bytes >= ledgrid::kAnimationPipelineCrcBytes, vector.id);
    const std::size_t runtime_bytes =
        vector.packet_bytes - ledgrid::kAnimationPipelineCrcBytes;
    runtime.process_command(vector.packet, runtime_bytes, 1000000);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(
        vector.expected_result,
        static_cast<std::uint8_t>(runtime.last_overlay_result()), vector.id);
    TEST_ASSERT_EQUAL_UINT16_MESSAGE(
        0, runtime.overlay_status(1000000).accepted_patches, vector.id);
  }
}

void test_sparse_lease_expiry_requires_full_snapshot_repair() {
  ledgrid::ReceiverRuntime runtime(true);
  const std::uint64_t now = 5000000;
  activate_local_hybrid(&runtime, now);
  publish_full_snapshot(&runtime, 1, 0, 1, now);
  TEST_ASSERT_FALSE(runtime.service_foreground(now + 999));
  TEST_ASSERT_TRUE(runtime.service_foreground(now + 1000));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::LeaseExpired),
      static_cast<std::uint8_t>(runtime.last_overlay_result()));

  auto invalid_delta = overlay_begin_command(
      runtime, 2, 1, ledgrid::OverlayUpdateKind::Delta, 0, 0);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::ReceiverOperationResult::InvalidState),
      static_cast<std::uint8_t>(runtime.process_command(
          invalid_delta.data(), invalid_delta.size(), now + 1000)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::InvalidState),
      static_cast<std::uint8_t>(runtime.last_overlay_result()));

  auto repair = overlay_begin_command(
      runtime, 2, 1, ledgrid::OverlayUpdateKind::FullSnapshot, 2, 0);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.process_command(
      repair.data(), repair.size(), now + 1000)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::ForegroundState::Staging),
      static_cast<std::uint8_t>(runtime.foreground_state()));
}

void test_host_takeover_invalidates_sparse_controller_authority() {
  ledgrid::ReceiverRuntime runtime(true);
  const std::uint64_t now = 7000000;
  activate_local_hybrid(&runtime, now);
  const auto original_session = session_command(runtime);
  auto authoritative_clear = overlay_clear_command(runtime, 9);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.process_command(
      authoritative_clear.data(), authoritative_clear.size(), now)));
  TEST_ASSERT_EQUAL_UINT64(9,
      runtime.overlay_status(now).committed_generation);

  runtime.complete_host_frame();
  const auto after_takeover = runtime.overlay_status(now);
  TEST_ASSERT_EQUAL_UINT64(0, after_takeover.committed_generation);
  TEST_ASSERT_EQUAL_UINT64(0, after_takeover.scene_revision);
  TEST_ASSERT_EQUAL_UINT64(0, after_takeover.scene_epoch);
  TEST_ASSERT_EQUAL_UINT64(0, after_takeover.base_revision);
  TEST_ASSERT_EQUAL_UINT16(0, after_takeover.committed_coverage_pixels);
  const std::uint8_t zero_session[ledgrid::kControllerSessionBytes] = {};
  TEST_ASSERT_EQUAL_MEMORY(zero_session, after_takeover.session,
                           ledgrid::kControllerSessionBytes);

  auto restart_local = start_command(
      60, 0, 42, runtime.active_context().scene_epoch);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.process_command(
      restart_local.data(), restart_local.size(), now + 1000)));
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.process_command(
      original_session.data(), original_session.size(), now + 1000)));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OverlayOperationResult::Ok),
      static_cast<std::uint8_t>(runtime.last_overlay_result()));
  auto delta_before_repair = overlay_begin_command(
      runtime, 1, 0, ledgrid::OverlayUpdateKind::Delta, 0, 0);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::ReceiverOperationResult::InvalidState),
      static_cast<std::uint8_t>(runtime.process_command(
          delta_before_repair.data(), delta_before_repair.size(), now + 1000)));
}

void test_status_v4_negotiates_after_exact_v3_prefix() {
  ledgrid::ReceiverStatusV4 status{};
  status.capabilities = ledgrid::kCapabilityStatusV3 |
                        ledgrid::kCapabilitySparseOverlayV1;
  status.overlay_result = ledgrid::OverlayOperationResult::PatchConflict;
  status.overlay_update_kind = ledgrid::OverlayUpdateKind::Delta;
  status.overlay_expected_patches = 3;
  status.overlay_accepted_patches = 2;
  status.overlay_committed_coverage_pixels = 17;
  status.overlay_committed_generation = 0x0102030405060708ULL;
  status.overlay_staged_generation = 9;
  status.foreground_scene_revision = 10;
  status.foreground_scene_epoch = 11;
  status.foreground_base_revision = 10;
  status.foreground_present_at_scene_time_us = 12;
  status.overlay_lease_ms = 3000;
  status.overlay_lease_remaining_ms = 999;
  status.overlay_session[0] = 0xAA;
  status.overlay_composite_frames = 13;
  status.overlay_last_composite_us = 14;
  status.overlay_max_composite_us = 15;
  status.overlay_commits = 16;
  status.overlay_expirations = 17;
  std::array<std::uint8_t, ledgrid::kStatusBytesV4> encoded{};
  TEST_ASSERT_TRUE(ledgrid::encode_receiver_status_v4(
      status, encoded.data(), encoded.size()));
  TEST_ASSERT_EQUAL_STRING_LEN("LGS4", reinterpret_cast<char*>(encoded.data()), 4);
  TEST_ASSERT_EQUAL_UINT8(4, encoded[4]);
  TEST_ASSERT_EQUAL_UINT8(14, encoded[320]);
  TEST_ASSERT_EQUAL_UINT8(2, encoded[321]);
  TEST_ASSERT_EQUAL_UINT8(17, encoded[327]);
  TEST_ASSERT_EQUAL_UINT8(0x01, encoded[328]);
  TEST_ASSERT_EQUAL_UINT8(0x08, encoded[335]);
  TEST_ASSERT_EQUAL_UINT8(0xAA, encoded[384]);
  TEST_ASSERT_EQUAL_UINT8(17, encoded[415]);
  std::array<std::uint8_t, ledgrid::kStatusBytesV3> legacy{};
  TEST_ASSERT_TRUE(ledgrid::encode_receiver_status_v3(
      status, legacy.data(), legacy.size()));
  TEST_ASSERT_EQUAL_STRING_LEN("LGS3", reinterpret_cast<char*>(legacy.data()), 4);
  std::array<std::uint8_t, ledgrid::kStatusBytesV4> query{};
  query[0] = 0x08;
  TEST_ASSERT_TRUE(ledgrid::valid_status_query(
      query.data(), ledgrid::kStatusBytesV3, false));
  TEST_ASSERT_FALSE(ledgrid::valid_status_query(
      query.data(), query.size(), false));
  TEST_ASSERT_TRUE(ledgrid::valid_status_query(
      query.data(), query.size(), true));
  query[400] = 1;
  TEST_ASSERT_FALSE(ledgrid::valid_status_query(
      query.data(), query.size(), true));
}

}  // namespace

void setUp() {}
void tearDown() {}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_command_ids_ownership_and_disabled_behavior_are_explicit);
  RUN_TEST(test_start_parameter_stop_takeover_restart_and_failure_transitions);
  RUN_TEST(test_invalid_commands_are_atomic_and_partial_commands_never_claim);
  RUN_TEST(test_cadence_is_deadline_driven_and_counts_misses);
  RUN_TEST(test_rainbow_uses_global_coordinates_seed_and_luminance_once);
  RUN_TEST(test_generated_cross_language_context_packets_stage_and_commit);
  RUN_TEST(test_context_digest_and_order_failures_preserve_active_context);
  RUN_TEST(test_zero_tempo_is_valid_presentation_state_not_a_render_failure);
  RUN_TEST(test_context_retries_revision_order_and_conflicts_are_safe);
  RUN_TEST(test_sha256_standard_and_fixture_vectors);
  RUN_TEST(test_status_v3_preserves_v2_prefix_and_extended_offsets);
  RUN_TEST(test_status_query_is_exact_zero_padded_and_non_owning);
  RUN_TEST(test_config_identity_is_backward_compatible_and_fail_closed);
  RUN_TEST(test_scene_time_is_common_across_different_receiver_boot_clocks);
  RUN_TEST(test_staging_replacement_context_preserves_active_scene_time_until_commit);
  RUN_TEST(test_cadence_has_no_integer_period_drift_over_thirty_minutes);
  RUN_TEST(test_local_render_generation_prevents_stale_frame_submission);
  RUN_TEST(test_stale_render_and_dma_completions_cannot_mutate_newer_ownership);
  RUN_TEST(test_parameter_and_context_updates_invalidate_inflight_local_frames);
  RUN_TEST(test_live_dispatch_policy_is_exhaustive_and_feature_gated);
  RUN_TEST(test_crc_gate_rejects_corruption_before_runtime_mutation);
  RUN_TEST(test_receiver_control_and_display_tasks_use_separate_cores);
  RUN_TEST(test_render_set_all_submit_interleaving_has_one_linearization_point);
  RUN_TEST(test_config_and_brightness_interleavings_invalidate_render_tickets);
  RUN_TEST(test_operation_sequence_saturates_and_fails_closed_at_boundary);
  RUN_TEST(test_sparse_full_snapshot_schedules_composites_and_delta_clears);
  RUN_TEST(test_sparse_order_interruption_session_lease_restart_and_takeover);
  RUN_TEST(test_sparse_generation_counter_exhaustion_is_fail_closed);
  RUN_TEST(test_sparse_batch_applies_multiple_spans_atomically_and_retries_exactly);
  RUN_TEST(test_sparse_batch_rejects_malformed_unsorted_overlap_and_premul_atomically);
  RUN_TEST(test_sparse_batch_accepts_exact_maximum_span_capacity);
  RUN_TEST(test_generated_malformed_batches_reject_with_exact_runtime_results);
  RUN_TEST(test_sparse_lease_expiry_requires_full_snapshot_repair);
  RUN_TEST(test_host_takeover_invalidates_sparse_controller_authority);
  RUN_TEST(test_status_v4_negotiates_after_exact_v3_prefix);
  return UNITY_END();
}
