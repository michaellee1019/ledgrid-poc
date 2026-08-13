#include <unity.h>

#include <array>
#include <cstdint>
#include <cstring>
#include <vector>

#include "fixtures/receiver_presentation_v1.hpp"
#include "ledgrid/protocol.hpp"
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

std::vector<std::uint8_t> start_command(
    std::uint16_t cadence = 30, std::uint32_t offset = 8,
    std::uint32_t seed = 42,
    std::uint64_t epoch = 0x0102030405060708ULL) {
  std::vector<std::uint8_t> result{0x10};
  append_u16(&result, 1); append_u16(&result, cadence);
  append_u32(&result, offset); append_u32(&result, seed); append_u64(&result, epoch);
  return result;
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
    default: break;
  }
  std::vector<std::uint8_t> result(size, 0);
  result[0] = command;
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
  activate_neutral_context(&runtime);
  auto start = start_command(40, 16, 0x10203040, 0x0102030405060708ULL);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      runtime.process_command(start.data(), start.size())));
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(runtime.base_mode()));
  TEST_ASSERT_EQUAL_UINT16(40, runtime.local_parameters().preferred_cadence_hz);
  TEST_ASSERT_EQUAL_UINT32(16, runtime.local_parameters().global_strip_offset);
  TEST_ASSERT_EQUAL_UINT64(0x0102030405060708ULL, runtime.local_parameters().scene_epoch);

  std::vector<std::uint8_t> parameters{0x12};
  append_u16(&parameters, 20); append_u32(&parameters, 24); append_u32(&parameters, 7);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      runtime.process_command(parameters.data(), parameters.size())));
  TEST_ASSERT_EQUAL_UINT64(0x0102030405060708ULL, runtime.local_parameters().scene_epoch);
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
  RUN_TEST(test_cadence_has_no_integer_period_drift_over_thirty_minutes);
  RUN_TEST(test_local_render_generation_prevents_stale_frame_submission);
  RUN_TEST(test_stale_render_and_dma_completions_cannot_mutate_newer_ownership);
  RUN_TEST(test_parameter_and_context_updates_invalidate_inflight_local_frames);
  RUN_TEST(test_live_dispatch_policy_is_exhaustive_and_feature_gated);
  RUN_TEST(test_crc_gate_rejects_corruption_before_runtime_mutation);
  RUN_TEST(test_render_set_all_submit_interleaving_has_one_linearization_point);
  RUN_TEST(test_config_and_brightness_interleavings_invalidate_render_tickets);
  RUN_TEST(test_operation_sequence_saturates_and_fails_closed_at_boundary);
  return UNITY_END();
}
