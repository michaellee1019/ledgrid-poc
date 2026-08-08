#include <unity.h>

#include <array>
#include <cstring>
#include <cstdint>
#include <map>
#include <string>
#include <vector>

#include "ledgrid/asset_upload.hpp"
#include "ledgrid/asset_verifier.hpp"
#include "ledgrid/display_mode.hpp"
#include "ledgrid/frame_mailbox.hpp"
#include "ledgrid/frame_track.hpp"
#include "ledgrid/native_examples.hpp"
#include "ledgrid/protocol.hpp"
#include "ledgrid/receiver_control.hpp"
#include "ledgrid/sha256.hpp"
#include "ledgrid/startup_animation.hpp"
#include "ledgrid/typed_parameters.hpp"
#include "ledgrid/ws2812_encoder.hpp"
#include "native_examples/native_time.hpp"

namespace {

std::uint16_t read_u16(const std::uint8_t* input) {
  return static_cast<std::uint16_t>(
      (static_cast<std::uint16_t>(input[0]) << 8) | input[1]);
}

std::uint32_t read_u32(const std::uint8_t* input) {
  return (static_cast<std::uint32_t>(input[0]) << 24) |
         (static_cast<std::uint32_t>(input[1]) << 16) |
         (static_cast<std::uint32_t>(input[2]) << 8) |
         input[3];
}

void append_u16(std::vector<std::uint8_t>* output, std::uint16_t value) {
  output->push_back(static_cast<std::uint8_t>(value >> 8));
  output->push_back(static_cast<std::uint8_t>(value));
}

void append_u32(std::vector<std::uint8_t>* output, std::uint32_t value) {
  output->push_back(static_cast<std::uint8_t>(value >> 24));
  output->push_back(static_cast<std::uint8_t>(value >> 16));
  output->push_back(static_cast<std::uint8_t>(value >> 8));
  output->push_back(static_cast<std::uint8_t>(value));
}

void set_u32(std::vector<std::uint8_t>* output, std::size_t at,
             std::uint32_t value) {
  (*output)[at] = static_cast<std::uint8_t>(value >> 24);
  (*output)[at + 1] = static_cast<std::uint8_t>(value >> 16);
  (*output)[at + 2] = static_cast<std::uint8_t>(value >> 8);
  (*output)[at + 3] = static_cast<std::uint8_t>(value);
}

std::vector<std::uint8_t> make_track() {
  std::vector<std::uint8_t> result{'L', 'G', 'T', '1', 1,
                                   ledgrid::kFrameTrackLoop, 1, 2};
  append_u16(&result, 3);
  append_u16(&result, 2);
  append_u32(&result, 0);  // patched data size
  append_u32(&result, 0);

  // Frame zero: keyframe of red, green, blue.
  append_u32(&result, 100);
  append_u32(&result, 9);
  result.insert(result.end(), {ledgrid::kFrameRecordKeyframe, 0, 0, 0});
  result.push_back(0);
  append_u16(&result, 3);
  append_u16(&result, 0xF800);
  append_u16(&result, 0x07E0);
  append_u16(&result, 0x001F);

  // Frame one: preserve red, replace the other two with white.
  append_u32(&result, 200);
  append_u32(&result, 8);
  result.insert(result.end(), {0, 0, 0, 0});
  result.push_back(1);
  append_u16(&result, 1);
  result.push_back(2);
  append_u16(&result, 2);
  append_u16(&result, 0xFFFF);
  set_u32(&result, 12,
          static_cast<std::uint32_t>(result.size() -
                                     ledgrid::kFrameTrackHeaderBytes));
  return result;
}

std::array<std::uint8_t, 32> digest_of(
    const std::uint8_t* data, std::size_t size) {
  ledgrid::Sha256 sha;
  sha.update(data, size);
  std::array<std::uint8_t, 32> digest{};
  sha.finish(digest.data());
  return digest;
}

std::vector<std::uint8_t> make_signed_index(
    ledgrid::AssetKind kind,
    const std::array<std::uint8_t, 32>& selected_digest,
    std::uint16_t abi = ledgrid::kAnimationAbiV1,
    std::uint16_t target = ledgrid::kEsp32S3ElfLoaderTargetV1,
    std::uint8_t local_strips = ledgrid::kLocalStrips,
    std::uint16_t leds_per_strip = ledgrid::kInstalledLedsPerStrip) {
  std::vector<std::uint8_t> index{'L', 'G', 'I', 'X', 1,
                                  static_cast<std::uint8_t>(kind)};
  append_u16(&index, abi);
  append_u16(&index, target);
  index.push_back(ledgrid::kReceiverCount);
  index.push_back(local_strips);
  append_u16(&index, ledgrid::kWallStrips);
  append_u16(&index, leds_per_strip);
  index.insert(index.end(), 32, 0xA5);  // signed manifest digest
  for (std::size_t device = 0; device < ledgrid::kReceiverCount; ++device)
    index.insert(index.end(), selected_digest.begin(), selected_digest.end());
  return index;
}

std::vector<std::uint8_t> make_asset_begin(
    const ledgrid::AssetDescriptor& descriptor,
    const std::vector<std::uint8_t>& index,
    const std::array<std::uint8_t, 64>& signature,
    const char* key_id = "key-0123456789abcdef") {
  std::vector<std::uint8_t> command{
      static_cast<std::uint8_t>(ledgrid::Command::AssetBegin),
      ledgrid::kAssetVerificationEnvelopeVersion, 0, 0};
  append_u32(&command, descriptor.total_size);
  command.insert(command.end(), descriptor.digest, descriptor.digest + 32);
  command.push_back(static_cast<std::uint8_t>(descriptor.kind));
  append_u16(&command, descriptor.abi);
  append_u16(&command, descriptor.target);
  command.push_back(descriptor.strip_count);
  append_u16(&command, descriptor.leds_per_strip);
  command.push_back(descriptor.logical_device);
  command.push_back(ledgrid::kSigningKeyIdBytes);
  command.insert(command.end(), key_id, key_id + ledgrid::kSigningKeyIdBytes);
  append_u16(&command, static_cast<std::uint16_t>(index.size()));
  command.insert(command.end(), index.begin(), index.end());
  command.push_back(signature.size());
  command.insert(command.end(), signature.begin(), signature.end());
  const std::uint16_t envelope_size =
      static_cast<std::uint16_t>(command.size() - 4U);
  command[2] = static_cast<std::uint8_t>(envelope_size >> 8U);
  command[3] = static_cast<std::uint8_t>(envelope_size);
  return command;
}

class FakeSignatureVerifier : public ledgrid::AssetSignatureVerifier {
 public:
  ledgrid::OperationResult verify(
      const ledgrid::AssetVerificationEnvelope& envelope) const override {
    static constexpr char kKey[] = "key-0123456789abcdef";
    if (envelope.key_id_size != ledgrid::kSigningKeyIdBytes ||
        std::memcmp(envelope.key_id, kKey, ledgrid::kSigningKeyIdBytes) != 0)
      return ledgrid::OperationResult::UnknownKey;
    if (envelope.signed_index_size != expected_index.size() ||
        std::memcmp(envelope.signed_index, expected_index.data(),
                    expected_index.size()) != 0 ||
        envelope.signature_size != expected_signature.size() ||
        std::memcmp(envelope.signature, expected_signature.data(),
                    expected_signature.size()) != 0)
      return ledgrid::OperationResult::BadSignature;
    return ledgrid::OperationResult::Ok;
  }
  std::vector<std::uint8_t> expected_index;
  std::array<std::uint8_t, 64> expected_signature{};
};

class FakeStore : public ledgrid::AssetStore {
 public:
  bool probe(const std::uint8_t digest[32]) const override {
    return committed && std::memcmp(digest, descriptor.digest, 32) == 0;
  }
  bool describe(const std::uint8_t digest[32],
                ledgrid::AssetDescriptor* output) const override {
    if (!probe(digest) || output == nullptr) return false;
    *output = descriptor;
    return true;
  }
  bool begin_part(const ledgrid::AssetDescriptor& value) override {
    ++begin_calls;
    descriptor = value;
    part.clear();
    part.resize(value.total_size);
    part_visible = false;
    return allow_storage;
  }
  bool write_part(std::uint32_t offset, const std::uint8_t* data,
                  std::size_t size) override {
    if (!allow_storage || offset + size > part.size()) return false;
    std::memcpy(part.data() + offset, data, size);
    return true;
  }
  bool read_part(std::uint32_t offset, std::uint8_t* data,
                 std::size_t size) const override {
    if (offset + size > part.size()) return false;
    std::memcpy(data, part.data() + offset, size);
    return true;
  }
  ledgrid::OperationResult validate_part(
      const ledgrid::AssetDescriptor&) override { return validation_result; }
  bool commit_part(const std::uint8_t digest[32]) override {
    if (!allow_commit || std::memcmp(digest, descriptor.digest, 32) != 0)
      return false;
    committed = true;
    committed_bytes = part;
    return true;
  }
  void discard_part() override { part.clear(); }
  bool remove(const std::uint8_t digest[32]) override {
    if (probe(digest)) committed = false;
    return true;
  }
  std::uint32_t free_bytes() const override { return free_capacity; }
  std::uint32_t used_bytes() const override {
    return static_cast<std::uint32_t>(committed_bytes.size());
  }

  ledgrid::AssetDescriptor descriptor{};
  std::vector<std::uint8_t> part;
  std::vector<std::uint8_t> committed_bytes;
  ledgrid::OperationResult validation_result = ledgrid::OperationResult::Ok;
  bool allow_storage = true;
  bool allow_commit = true;
  bool committed = false;
  bool part_visible = false;
  int begin_calls = 0;
  std::uint32_t free_capacity = 4U * 1024U * 1024U;
};

class FakeAnimationBackend : public ledgrid::AnimationBackend {
 public:
  std::uint32_t capabilities() const override { return capability_bits; }
  bool available(ledgrid::AssetKind kind) const override {
    return kind == ledgrid::AssetKind::Native ||
           kind == ledgrid::AssetKind::FrameTrack;
  }
  ledgrid::OperationResult start(
      const ledgrid::AssetDescriptor&, std::uint16_t,
      const std::uint8_t*, std::size_t) override {
    ++starts;
    active = start_result == ledgrid::OperationResult::Ok;
    return start_result;
  }
  void stop() override { ++stops; active = false; }
  ledgrid::OperationResult restart() override {
    ++restarts;
    return active ? ledgrid::OperationResult::Ok
                  : ledgrid::OperationResult::InvalidState;
  }
  ledgrid::OperationResult update_parameters(
      const std::uint8_t*, std::size_t) override {
    ++updates;
    return active ? ledgrid::OperationResult::Ok
                  : ledgrid::OperationResult::InvalidState;
  }
  bool render(std::uint64_t, std::uint8_t*, std::size_t, bool* changed) override {
    if (changed != nullptr) *changed = active;
    return active;
  }
  std::uint32_t capability_bits = ledgrid::kCapabilityNative |
                                  ledgrid::kCapabilityFrameTrack |
                                  ledgrid::kCapabilityPsramExecution;
  ledgrid::OperationResult start_result = ledgrid::OperationResult::Ok;
  int starts = 0;
  int stops = 0;
  int restarts = 0;
  int updates = 0;
  bool active = false;
};

class FakePersistence : public ledgrid::ReceiverPersistence {
 public:
  bool mark_active(const std::uint8_t digest[32]) override {
    ++active_writes;
    std::memcpy(active, digest, 32);
    return allow_write;
  }
  void clear_active() override { ++active_clears; std::memset(active, 0, 32); }
  void mark_quarantined(const std::uint8_t digest[32]) override {
    ++quarantine_writes;
    std::memcpy(quarantined, digest, 32);
  }
  void clear_quarantined(const std::uint8_t[32]) override {
    ++quarantine_clears;
    std::memset(quarantined, 0, 32);
  }
  std::uint8_t active[32] = {};
  std::uint8_t quarantined[32] = {};
  bool allow_write = true;
  int active_writes = 0;
  int active_clears = 0;
  int quarantine_writes = 0;
  int quarantine_clears = 0;
};

void test_encoder_emits_parallel_grb_waveform() {
  // Two strips, one RGB pixel each. Only strip 0 green bit 7 is set.
  const std::uint8_t rgb[] = {0x00, 0x80, 0x00, 0x00, 0x00, 0x00};
  std::vector<std::uint8_t> output(ledgrid::ws2812_encoded_size(1));

  const auto result = ledgrid::encode_parallel_grb(
      rgb, sizeof(rgb), 2, 1, 255, output.data(), output.size());

  TEST_ASSERT_TRUE(result.ok);
  TEST_ASSERT_EQUAL_UINT32(output.size(), result.bytes_written);
  TEST_ASSERT_EQUAL_HEX8(0x03, output[0]);
  TEST_ASSERT_EQUAL_HEX8(0x01, output[1]);
  TEST_ASSERT_EQUAL_HEX8(0x00, output[2]);
  TEST_ASSERT_EQUAL_HEX8(0x03, output[3]);
  TEST_ASSERT_EQUAL_HEX8(0x00, output[4]);
  TEST_ASSERT_EQUAL_HEX8(0x00, output[5]);

  // Red begins after the eight green bits and is zero on both lanes.
  TEST_ASSERT_EQUAL_HEX8(0x03, output[24]);
  TEST_ASSERT_EQUAL_HEX8(0x00, output[25]);
  TEST_ASSERT_EQUAL_HEX8(0x00, output[26]);
}

void test_encoder_scales_brightness_before_bit_expansion() {
  const std::uint8_t rgb[] = {0x00, 0xFF, 0x00};
  std::vector<std::uint8_t> output(ledgrid::ws2812_encoded_size(1));

  auto result = ledgrid::encode_parallel_grb(
      rgb, sizeof(rgb), 1, 1, 128, output.data(), output.size());
  TEST_ASSERT_TRUE(result.ok);
  TEST_ASSERT_EQUAL_HEX8(0x01, output[1]);   // Scaled value 128: bit 7 set.
  TEST_ASSERT_EQUAL_HEX8(0x00, output[4]);   // Bit 6 clear.

  result = ledgrid::encode_parallel_grb(
      rgb, sizeof(rgb), 1, 1, 0, output.data(), output.size());
  TEST_ASSERT_TRUE(result.ok);
  for (std::size_t bit = 0; bit < 8; ++bit) {
    TEST_ASSERT_EQUAL_HEX8(0x00, output[bit * 3 + 1]);
  }
}

void test_optimized_encoder_updates_all_eight_lanes() {
  std::array<std::uint8_t, 8U * 3U> rgb{};
  for (std::size_t lane = 0; lane < 8; ++lane) {
    rgb[lane * 3U + 1U] = static_cast<std::uint8_t>(0x80U >> lane);
  }
  std::vector<std::uint8_t> output(ledgrid::ws2812_encoded_size(1), 0xA5);

  TEST_ASSERT_TRUE(ledgrid::initialize_parallel_grb_waveform(
      8, 1, output.data(), output.size()));
  const auto result = ledgrid::encode_parallel_grb_pixels(
      rgb.data(), rgb.size(), 8, 1, 255, output.data(), output.size());

  TEST_ASSERT_TRUE(result.ok);
  for (std::size_t bit = 0; bit < 8; ++bit) {
    TEST_ASSERT_EQUAL_HEX8(0xFF, output[bit * 3U]);
    TEST_ASSERT_EQUAL_HEX8(
        static_cast<std::uint8_t>(1U << bit), output[bit * 3U + 1U]);
    TEST_ASSERT_EQUAL_HEX8(0, output[bit * 3U + 2U]);
  }
}

void test_encoder_appends_300us_reset_and_rejects_bad_bounds() {
  TEST_ASSERT_EQUAL_UINT32(720, ledgrid::ws2812_reset_samples());
  TEST_ASSERT_EQUAL_UINT32(792, ledgrid::ws2812_encoded_size(1));

  const std::uint8_t rgb[] = {1, 2, 3};
  std::vector<std::uint8_t> output(ledgrid::ws2812_encoded_size(1));
  auto result = ledgrid::encode_parallel_grb(
      rgb, sizeof(rgb), 1, 1, 255, output.data(), output.size());
  TEST_ASSERT_TRUE(result.ok);
  for (std::size_t i = 72; i < output.size(); ++i) {
    TEST_ASSERT_EQUAL_HEX8(0, output[i]);
  }

  TEST_ASSERT_FALSE(ledgrid::encode_parallel_grb(
      rgb, sizeof(rgb), 0, 1, 255, output.data(), output.size()).ok);
  TEST_ASSERT_FALSE(ledgrid::encode_parallel_grb(
      rgb, sizeof(rgb), 9, 1, 255, output.data(), output.size()).ok);
  TEST_ASSERT_FALSE(ledgrid::encode_parallel_grb(
      rgb, sizeof(rgb) - 1, 1, 1, 255, output.data(), output.size()).ok);
  TEST_ASSERT_FALSE(ledgrid::encode_parallel_grb(
      rgb, sizeof(rgb), 1, 1, 255, output.data(), output.size() - 1).ok);
}

void test_startup_rainbow_is_45_degrees_and_moves_up_right() {
  constexpr std::uint8_t strips = 3;
  constexpr std::uint16_t leds = 4;
  std::array<std::uint8_t, strips * leds * 3U> initial{};
  std::array<std::uint8_t, strips * leds * 3U> advanced{};
  const std::uint32_t one_diagonal_step_us =
      (2U * ledgrid::kStartupRainbowCycleUs) /
      ledgrid::kStartupRainbowPeriodPixels;

  TEST_ASSERT_TRUE(ledgrid::render_startup_rainbow(
      0, strips, leds, initial.data(), initial.size()));
  TEST_ASSERT_TRUE(ledgrid::render_startup_rainbow(
      one_diagonal_step_us,
      strips,
      leds,
      advanced.data(),
      advanced.size()));

  // Equal phase along x and y produces a 45-degree field. After 1/16 second,
  // each color has moved one coordinate toward both positive axes.
  for (std::uint8_t strip = 0; strip + 1 < strips; ++strip) {
    for (std::uint16_t led = 0; led + 1 < leds; ++led) {
      const std::size_t source =
          (static_cast<std::size_t>(strip) * leds + led) * 3U;
      const std::size_t right =
          (static_cast<std::size_t>(strip + 1U) * leds + led) * 3U;
      const std::size_t up =
          (static_cast<std::size_t>(strip) * leds + led + 1U) * 3U;
      const std::size_t up_right =
          (static_cast<std::size_t>(strip + 1U) * leds + led + 1U) * 3U;
      TEST_ASSERT_EQUAL_MEMORY(initial.data() + right,
                               initial.data() + up, 3);
      TEST_ASSERT_EQUAL_MEMORY(initial.data() + source,
                               advanced.data() + up_right, 3);
    }
  }
}

void test_startup_rainbow_cycles_once_per_second_and_checks_bounds() {
  constexpr std::uint8_t strips = 8;
  constexpr std::uint16_t leds = 32;
  std::array<std::uint8_t, strips * leds * 3U> initial{};
  std::array<std::uint8_t, strips * leds * 3U> looped{};

  TEST_ASSERT_TRUE(ledgrid::render_startup_rainbow(
      0, strips, leds, initial.data(), initial.size()));
  TEST_ASSERT_TRUE(ledgrid::render_startup_rainbow(
      ledgrid::kStartupRainbowCycleUs,
      strips,
      leds,
      looped.data(),
      looped.size()));
  TEST_ASSERT_EQUAL_MEMORY(initial.data(), looped.data(), initial.size());
  TEST_ASSERT_EQUAL_HEX8(0xFF, initial[0]);
  TEST_ASSERT_EQUAL_HEX8(0x00, initial[1]);
  TEST_ASSERT_EQUAL_HEX8(0x00, initial[2]);

  TEST_ASSERT_FALSE(ledgrid::render_startup_rainbow(
      0, strips, leds, nullptr, initial.size()));
  TEST_ASSERT_FALSE(ledgrid::render_startup_rainbow(
      0, strips, leds, initial.data(), initial.size() - 1U));
}

void test_startup_fallback_invokes_the_native_module_byte_for_byte() {
  constexpr std::uint8_t strips = 8;
  constexpr std::uint16_t leds = 138;
  constexpr std::size_t bytes = strips * leds * 3U;
  std::array<std::uint8_t, bytes> fallback{};
  std::array<std::uint8_t, bytes + 8U> native{};
  native.fill(0xA5U);
  constexpr std::uint64_t elapsed_us = 345678U;
  TEST_ASSERT_TRUE(ledgrid::render_startup_rainbow(
      elapsed_us, strips, leds, fallback.data(), fallback.size()));

  ledgrid_render_context_v1 context{};
  context.abi_version = LEDGRID_ANIMATION_ABI_V1;
  context.local_strips = strips;
  context.leds_per_strip = leds;
  context.elapsed_us = elapsed_us;
  context.scaled_elapsed_us = elapsed_us;
  context.rgb_output = native.data();
  context.rgb_output_size = bytes;
  const auto* api = ledgrid_builtin_startup_rainbow_v1();
  void* state = reinterpret_cast<void*>(1U);
  TEST_ASSERT_EQUAL_INT(LEDGRID_ANIMATION_OK,
                        api->initialize(&context, nullptr, &state));
  TEST_ASSERT_NULL(state);
  TEST_ASSERT_EQUAL_INT(LEDGRID_ANIMATION_OK, api->render(state, &context));
  TEST_ASSERT_EQUAL_MEMORY(fallback.data(), native.data(), bytes);
  for (std::size_t i = bytes; i < native.size(); ++i)
    TEST_ASSERT_EQUAL_HEX8(0xA5U, native[i]);

  context.rgb_output_size = bytes - 1U;
  TEST_ASSERT_NOT_EQUAL(LEDGRID_ANIMATION_OK, api->render(state, &context));
  context.rgb_output_size = bytes;
  context.abi_version = 99U;
  TEST_ASSERT_NOT_EQUAL(LEDGRID_ANIMATION_OK, api->render(state, &context));
}

void test_startup_native_fractional_speed_is_continuous_across_one_second() {
  constexpr std::uint8_t strips = 1;
  constexpr std::uint16_t leds = 32;
  constexpr std::size_t bytes = strips * leds * 3U;
  std::array<std::uint8_t, bytes> initial{};
  std::array<std::uint8_t, bytes> after_one_second{};
  std::array<std::uint8_t, bytes> after_slow_cycle{};
  ledgrid_parameter_v1 speed{};
  speed.name = "speed";
  speed.type = LEDGRID_PARAMETER_FLOAT32;
  speed.value.real = 0.1F;
  ledgrid_render_context_v1 context{};
  context.abi_version = LEDGRID_ANIMATION_ABI_V1;
  context.local_strips = strips;
  context.leds_per_strip = leds;
  context.parameters = &speed;
  context.parameter_count = 1U;
  context.rgb_output_size = bytes;
  const auto* api = ledgrid_builtin_startup_rainbow_v1();

  context.rgb_output = initial.data();
  TEST_ASSERT_EQUAL_INT(LEDGRID_ANIMATION_OK, api->render(nullptr, &context));
  context.elapsed_us = context.scaled_elapsed_us = 1000000U;
  context.rgb_output = after_one_second.data();
  TEST_ASSERT_EQUAL_INT(LEDGRID_ANIMATION_OK, api->render(nullptr, &context));
  context.elapsed_us = context.scaled_elapsed_us = 10000000U;
  context.rgb_output = after_slow_cycle.data();
  TEST_ASSERT_EQUAL_INT(LEDGRID_ANIMATION_OK, api->render(nullptr, &context));

  TEST_ASSERT_NOT_EQUAL(
      0, std::memcmp(initial.data(), after_one_second.data(), bytes));
  TEST_ASSERT_EQUAL_MEMORY(initial.data(), after_slow_cycle.data(), bytes);
}

void test_native_time_matches_reference_across_the_32_bit_wrap() {
  struct TimingCase {
    std::uint64_t elapsed_us;
    std::uint32_t speed_permille;
    std::uint32_t units_per_second;
    std::uint32_t phase_steps;
  };
  const TimingCase cases[] = {
      {0U, 1000U, 1536U, 1536U},
      {1000000U, 100U, 1536U, 1536U},
      {4294967295ULL, 4000U, 1536U, 1536U},
      {4294967296ULL, 2750U, 45U, 256U},
      {98765432101ULL, 4000U, 55U, 234U},
  };
  for (const TimingCase& value : cases) {
    const std::uint64_t numerator =
        value.elapsed_us * value.speed_permille * value.units_per_second;
    const std::uint32_t expected = static_cast<std::uint32_t>(
        (numerator / 1000000000ULL) % value.phase_steps);
    TEST_ASSERT_EQUAL_UINT32(
        expected,
        ledgrid_native_example::phase_from_elapsed(
            value.elapsed_us, value.speed_permille,
            value.units_per_second, value.phase_steps));
  }
}

void test_checked_in_native_examples_render_distinct_bounded_frames() {
  constexpr std::uint8_t strips = 8;
  constexpr std::uint16_t leds = 138;
  constexpr std::size_t bytes = strips * leds * 3U;
  std::array<std::uint8_t, bytes> startup{};
  std::array<std::uint8_t, bytes> aurora{};
  std::array<std::uint8_t, bytes> meteors{};
  std::uint8_t* outputs[] = {startup.data(), aurora.data(), meteors.data()};
  const ledgrid_animation_callbacks_v1* apis[] = {
      ledgrid_builtin_startup_rainbow_v1(),
      ledgrid_builtin_aurora_ribbons_v1(),
      ledgrid_builtin_meteor_shower_v1()};
  for (std::size_t index = 0; index < 3U; ++index) {
    ledgrid_render_context_v1 context{};
    context.abi_version = LEDGRID_ANIMATION_ABI_V1;
    context.local_strips = strips;
    context.leds_per_strip = leds;
    context.global_strip_offset = 8U;
    context.elapsed_us = 750000U;
    context.scaled_elapsed_us = 750000U;
    context.frame_index = 45U;
    context.rgb_output = outputs[index];
    context.rgb_output_size = bytes;
    void* state = nullptr;
    TEST_ASSERT_EQUAL_INT(
        LEDGRID_ANIMATION_OK, apis[index]->initialize(&context, nullptr, &state));
    TEST_ASSERT_EQUAL_INT(LEDGRID_ANIMATION_OK,
                          apis[index]->render(state, &context));
    apis[index]->cleanup(state);
    bool nonzero = false;
    for (std::size_t byte = 0; byte < bytes; ++byte)
      nonzero = nonzero || outputs[index][byte] != 0U;
    TEST_ASSERT_TRUE(nonzero);
  }
  TEST_ASSERT_NOT_EQUAL(0, std::memcmp(startup.data(), aurora.data(), bytes));
  TEST_ASSERT_NOT_EQUAL(0, std::memcmp(startup.data(), meteors.data(), bytes));
  TEST_ASSERT_NOT_EQUAL(0, std::memcmp(aurora.data(), meteors.data(), bytes));
}

void test_mailbox_replaces_only_unread_ready_frames() {
  ledgrid::LatestFrameMailbox mailbox;
  ledgrid::FrameMetadata metadata{};

  int slot0 = mailbox.begin_write();
  TEST_ASSERT_GREATER_OR_EQUAL(0, slot0);
  metadata.sequence = 1;
  TEST_ASSERT_TRUE(mailbox.commit_write(slot0, metadata));

  ledgrid::FrameMetadata reading{};
  TEST_ASSERT_EQUAL_INT(slot0, mailbox.begin_read(&reading));
  TEST_ASSERT_EQUAL_UINT32(1, reading.sequence);

  int slot1 = mailbox.begin_write();
  metadata.sequence = 2;
  TEST_ASSERT_TRUE(mailbox.commit_write(slot1, metadata));
  int slot2 = mailbox.begin_write();
  metadata.sequence = 3;
  TEST_ASSERT_TRUE(mailbox.commit_write(slot2, metadata));

  // One slot is being read, one is ready, and the third remains free. A fourth
  // publish replaces a ready frame but never the frame being displayed.
  int replacement = mailbox.begin_write();
  metadata.sequence = 4;
  TEST_ASSERT_TRUE(mailbox.commit_write(replacement, metadata));
  TEST_ASSERT_EQUAL(ledgrid::LatestFrameMailbox::SlotState::Reading,
                    mailbox.state(slot0));
  TEST_ASSERT_EQUAL_UINT32(1, mailbox.counters().superseded);

  TEST_ASSERT_TRUE(mailbox.finish_read(slot0));
  TEST_ASSERT_EQUAL_UINT32(1, mailbox.counters().displayed);
  TEST_ASSERT_EQUAL_UINT32(4, mailbox.counters().accepted);

  int newest = mailbox.begin_read(&reading);
  TEST_ASSERT_GREATER_OR_EQUAL(0, newest);
  TEST_ASSERT_EQUAL_UINT32(4, reading.sequence);
}

void test_status_layout_is_stable() {
  ledgrid::ReceiverStatus status{};
  status.flags = 3;
  status.active_strips = 8;
  status.leds_per_strip = 138;
  status.queued_transactions = 2;
  status.packets = 11;
  status.crc_errors = 12;
  status.crc_ok_packets = 13;
  status.frames_accepted = 14;
  status.frames_displayed = 15;
  status.frames_superseded = 16;
  status.publish_drops = 17;
  status.spi_queue_errors = 18;
  status.last_crc_us = 19;
  status.last_copy_us = 20;
  status.last_encode_us = 21;
  status.last_show_us = 22;
  status.last_accepted_sequence = 23;
  status.last_displayed_sequence = 24;
  status.display_errors = 25;
  status.capabilities = 0x10203040;
  status.display_mode = ledgrid::DisplayMode::FirmwareAnimation;
  status.asset_kind = ledgrid::AssetKind::FrameTrack;
  status.upload_state = ledgrid::UploadState::Verifying;
  status.last_result = ledgrid::OperationResult::BadSignature;
  for (std::size_t i = 0; i < 32; ++i)
    status.active_digest[i] = static_cast<std::uint8_t>(i);
  status.cache_free_bytes = 0x11223344;
  status.cache_used_bytes = 0x55667788;
  status.upload_received_bytes = 0x01020304;
  status.upload_total_bytes = 0x05060708;
  status.last_render_us = 0x0910;
  status.max_render_us = 0x1112;
  status.missed_deadlines = 0x1314;
  status.watchdog_events = 7;
  status.quarantine_state = 1;

  std::array<std::uint8_t, ledgrid::kStatusBytes> encoded{};
  TEST_ASSERT_TRUE(ledgrid::encode_receiver_status(
      status, encoded.data(), encoded.size()));
  TEST_ASSERT_EQUAL_MEMORY("LGS3", encoded.data(), 4);
  TEST_ASSERT_EQUAL_UINT8(3, encoded[4]);
  TEST_ASSERT_EQUAL_UINT32(11, read_u32(encoded.data() + 12));
  TEST_ASSERT_EQUAL_UINT32(12, read_u32(encoded.data() + 16));
  TEST_ASSERT_EQUAL_UINT32(13, read_u32(encoded.data() + 20));
  TEST_ASSERT_EQUAL_UINT32(14, read_u32(encoded.data() + 24));
  TEST_ASSERT_EQUAL_UINT32(15, read_u32(encoded.data() + 28));
  TEST_ASSERT_EQUAL_UINT32(16, read_u32(encoded.data() + 32));
  TEST_ASSERT_EQUAL_UINT32(17, read_u32(encoded.data() + 36));
  TEST_ASSERT_EQUAL_UINT32(18, read_u32(encoded.data() + 40));
  TEST_ASSERT_EQUAL_UINT16(19, read_u16(encoded.data() + 44));
  TEST_ASSERT_EQUAL_UINT16(20, read_u16(encoded.data() + 46));
  TEST_ASSERT_EQUAL_UINT16(21, read_u16(encoded.data() + 48));
  TEST_ASSERT_EQUAL_UINT16(22, read_u16(encoded.data() + 50));
  TEST_ASSERT_EQUAL_UINT32(23, read_u32(encoded.data() + 52));
  TEST_ASSERT_EQUAL_UINT32(24, read_u32(encoded.data() + 56));
  TEST_ASSERT_EQUAL_UINT32(25, read_u32(encoded.data() + 60));
  TEST_ASSERT_EQUAL_HEX32(0x10203040, read_u32(encoded.data() + 64));
  TEST_ASSERT_EQUAL_UINT8(2, encoded[68]);
  TEST_ASSERT_EQUAL_UINT8(2, encoded[69]);
  TEST_ASSERT_EQUAL_UINT8(2, encoded[70]);
  TEST_ASSERT_EQUAL_UINT8(6, encoded[71]);
  TEST_ASSERT_EQUAL_MEMORY(status.active_digest, encoded.data() + 72, 32);
  TEST_ASSERT_EQUAL_HEX32(0x11223344, read_u32(encoded.data() + 104));
  TEST_ASSERT_EQUAL_HEX32(0x55667788, read_u32(encoded.data() + 108));
  TEST_ASSERT_EQUAL_HEX32(0x01020304, read_u32(encoded.data() + 112));
  TEST_ASSERT_EQUAL_HEX32(0x05060708, read_u32(encoded.data() + 116));
  TEST_ASSERT_EQUAL_HEX16(0x0910, read_u16(encoded.data() + 120));
  TEST_ASSERT_EQUAL_HEX16(0x1112, read_u16(encoded.data() + 122));
  TEST_ASSERT_EQUAL_HEX16(0x1314, read_u16(encoded.data() + 124));
  TEST_ASSERT_EQUAL_UINT8(7, encoded[126]);
  TEST_ASSERT_EQUAL_UINT8(1, encoded[127]);
  TEST_ASSERT_FALSE(ledgrid::encode_receiver_status(
      status, encoded.data(), encoded.size() - 1));
}

void test_spi_ceiling_and_display_ownership_contract() {
  TEST_ASSERT_TRUE(
      ledgrid::valid_spi_transaction_size(ledgrid::kMaxCommandBytes));
  TEST_ASSERT_FALSE(
      ledgrid::valid_spi_transaction_size(ledgrid::kMaxCommandBytes + 1));
  TEST_ASSERT_EQUAL_UINT32(4096, ledgrid::kMaxSpiTransactionBytes);
  TEST_ASSERT_EQUAL_UINT32(4089, ledgrid::kMaxAssetChunkBytes);
  TEST_ASSERT_TRUE(ledgrid::command_takes_display_ownership(
      ledgrid::Command::SetAll));
  TEST_ASSERT_TRUE(ledgrid::command_takes_display_ownership(
      ledgrid::Command::Show));
  TEST_ASSERT_TRUE(ledgrid::command_takes_display_ownership(
      ledgrid::Command::Clear));
  TEST_ASSERT_FALSE(ledgrid::command_takes_display_ownership(
      ledgrid::Command::Ping));
  TEST_ASSERT_FALSE(ledgrid::command_takes_display_ownership(
      ledgrid::Command::SetBrightness));
  TEST_ASSERT_FALSE(ledgrid::command_takes_display_ownership(
      ledgrid::Command::AssetChunk));
}

void test_mode_machine_host_ownership_maintenance_and_quarantine() {
  ledgrid::DisplayModeStateMachine modes;
  std::array<std::uint8_t, 32> digest{};
  digest[0] = 42;
  TEST_ASSERT_EQUAL_UINT8(0, static_cast<std::uint8_t>(modes.mode()));
  TEST_ASSERT_TRUE(modes.start_firmware(digest.data(),
                                        ledgrid::AssetKind::Native));
  TEST_ASSERT_EQUAL_UINT8(2, static_cast<std::uint8_t>(modes.mode()));
  TEST_ASSERT_TRUE(modes.begin_maintenance());
  TEST_ASSERT_EQUAL_UINT8(3, static_cast<std::uint8_t>(modes.mode()));
  modes.end_maintenance();
  TEST_ASSERT_EQUAL_UINT8(2, static_cast<std::uint8_t>(modes.mode()));
  modes.host_frame_received();
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(modes.mode()));

  TEST_ASSERT_TRUE(modes.start_firmware(digest.data(),
                                        ledgrid::AssetKind::Native));
  modes.render_failed(true);
  TEST_ASSERT_EQUAL_UINT8(0, static_cast<std::uint8_t>(modes.mode()));
  TEST_ASSERT_TRUE(modes.quarantined());
  TEST_ASSERT_EQUAL_UINT8(1, modes.watchdog_events());
  TEST_ASSERT_FALSE(modes.start_firmware(digest.data(),
                                         ledgrid::AssetKind::Native));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OperationResult::Quarantined),
      static_cast<std::uint8_t>(modes.last_result()));
  modes.mark_reinstalled(digest.data());
  TEST_ASSERT_FALSE(modes.quarantined());
  TEST_ASSERT_TRUE(modes.start_firmware(digest.data(),
                                        ledgrid::AssetKind::Native));
}

void test_frame_track_decodes_keyframe_delta_brightness_and_timing() {
  auto track = make_track();
  ledgrid::FrameTrackDecoder decoder;
  TEST_ASSERT_TRUE(decoder.open(track.data(), track.size(), 1, 3, 2));
  std::array<std::uint8_t, 9> frame{};
  std::uint32_t duration = 0;
  TEST_ASSERT_TRUE(decoder.decode_frame(0, 255, frame.data(), frame.size(),
                                        &duration));
  TEST_ASSERT_EQUAL_UINT32(100, duration);
  const std::uint8_t expected[] = {255, 0, 0, 0, 255, 0, 0, 0, 255};
  TEST_ASSERT_EQUAL_MEMORY(expected, frame.data(), sizeof(expected));
  TEST_ASSERT_TRUE(decoder.decode_frame(1, 128, frame.data(), frame.size(),
                                        &duration));
  TEST_ASSERT_EQUAL_UINT32(200, duration);
  // Direct delta seek rebuilds from its keyframe so brightness applies to all
  // pixels, including unchanged runs.
  TEST_ASSERT_EQUAL_UINT8(128, frame[0]);
  TEST_ASSERT_EQUAL_UINT8(128, frame[3]);
  TEST_ASSERT_EQUAL_UINT8(128, frame[4]);
  TEST_ASSERT_EQUAL_UINT8(128, frame[5]);

  ledgrid::FrameTrackPlayer player(&decoder);
  ledgrid::FramePlaybackControls controls{};
  controls.speed_permille = 2000;
  TEST_ASSERT_TRUE(player.set_controls(controls));
  bool changed = false;
  TEST_ASSERT_TRUE(player.render(1000, frame.data(), frame.size(), &changed));
  TEST_ASSERT_TRUE(changed);
  TEST_ASSERT_TRUE(player.render(1049, frame.data(), frame.size(), &changed));
  TEST_ASSERT_FALSE(changed);
  TEST_ASSERT_TRUE(player.render(1050, frame.data(), frame.size(), &changed));
  TEST_ASSERT_TRUE(changed);
  TEST_ASSERT_EQUAL_UINT16(1, player.frame_index());
  controls.paused = true;
  TEST_ASSERT_TRUE(player.set_controls(controls));
  TEST_ASSERT_TRUE(player.render(5000, frame.data(), frame.size(), &changed));
  TEST_ASSERT_EQUAL_UINT16(1, player.frame_index());
  controls.paused = false;
  controls.loop = true;
  TEST_ASSERT_TRUE(player.set_controls(controls));
  TEST_ASSERT_TRUE(player.render(5100, frame.data(), frame.size(), &changed));
  TEST_ASSERT_EQUAL_UINT16(0, player.frame_index());
  controls.speed_permille = 99;
  TEST_ASSERT_FALSE(player.set_controls(controls));
}

void test_frame_track_rejects_all_truncation_and_run_overflow_before_writes() {
  auto good = make_track();
  ledgrid::FrameTrackDecoder decoder;
  for (std::size_t size = 0; size < ledgrid::kFrameTrackHeaderBytes; ++size) {
    TEST_ASSERT_FALSE(decoder.open(good.data(), size, 1, 3, 2));
  }
  auto bad_size = good;
  set_u32(&bad_size, 12, 999);
  TEST_ASSERT_FALSE(decoder.open(bad_size.data(), bad_size.size(), 1, 3, 2));
  auto finite_loop = good;
  finite_loop[5] = 0;
  set_u32(&finite_loop, 16, 1);
  TEST_ASSERT_FALSE(decoder.open(
      finite_loop.data(), finite_loop.size(), 1, 3, 2));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::FrameTrackError::BadLoopCount),
      static_cast<std::uint8_t>(decoder.error()));
  auto no_keyframe = good;
  no_keyframe[28] = 0;
  TEST_ASSERT_FALSE(decoder.open(no_keyframe.data(), no_keyframe.size(), 1, 3, 2));
  auto bad_opcode = good;
  bad_opcode[32] = 9;
  TEST_ASSERT_FALSE(decoder.open(bad_opcode.data(), bad_opcode.size(), 1, 3, 2));
  auto overflow = good;
  overflow[33] = 0;
  overflow[34] = 4;
  TEST_ASSERT_FALSE(decoder.open(overflow.data(), overflow.size(), 1, 3, 2));
  auto truncated = good;
  truncated.pop_back();
  set_u32(&truncated, 12,
          static_cast<std::uint32_t>(truncated.size() - 20));
  TEST_ASSERT_FALSE(decoder.open(truncated.data(), truncated.size(), 1, 3, 2));

  auto mutable_track = make_track();
  TEST_ASSERT_TRUE(decoder.open(mutable_track.data(), mutable_track.size(), 1, 3, 2));
  mutable_track[33] = 0;
  mutable_track[34] = 4;
  std::array<std::uint8_t, 9> untouched{};
  untouched.fill(0xA5);
  TEST_ASSERT_FALSE(
      decoder.decode_frame(0, 255, untouched.data(), untouched.size()));
  for (auto value : untouched) TEST_ASSERT_EQUAL_HEX8(0xA5, value);
  TEST_ASSERT_FALSE(decoder.decode_frame(
      0, 255, untouched.data(), untouched.size() - 1));
}

void test_upload_is_ordered_retryable_atomic_and_fails_closed() {
  FakeStore store;
  ledgrid::UploadManager upload(&store);
  const std::uint8_t payload[] = {'a', 'b', 'c'};
  const auto digest = digest_of(payload, sizeof(payload));
  ledgrid::AssetDescriptor descriptor{};
  std::memcpy(descriptor.digest, digest.data(), digest.size());
  descriptor.total_size = sizeof(payload);
  descriptor.kind = ledgrid::AssetKind::FrameTrack;
  descriptor.abi = 1;
  descriptor.strip_count = 8;
  descriptor.leds_per_strip = 138;
  descriptor.logical_device = 2;

  TEST_ASSERT_EQUAL_UINT8(
      1, static_cast<std::uint8_t>(upload.begin(descriptor)));
  TEST_ASSERT_EQUAL_UINT8(
      1, static_cast<std::uint8_t>(upload.begin(descriptor)));
  TEST_ASSERT_FALSE(upload.probe(digest.data()));  // .part is never active.
  TEST_ASSERT_EQUAL_UINT8(
      1, static_cast<std::uint8_t>(upload.chunk(0, payload, 1)));
  TEST_ASSERT_EQUAL_UINT8(
      3, static_cast<std::uint8_t>(upload.chunk(2, payload + 2, 1)));
  TEST_ASSERT_EQUAL_UINT8(
      1, static_cast<std::uint8_t>(upload.chunk(1, payload + 1, 2)));
  TEST_ASSERT_EQUAL_UINT8(
      1, static_cast<std::uint8_t>(upload.chunk(1, payload + 1, 2)));
  const std::uint8_t wrong = 'x';
  TEST_ASSERT_EQUAL_UINT8(
      3, static_cast<std::uint8_t>(upload.chunk(1, &wrong, 1)));
  TEST_ASSERT_EQUAL_UINT8(
      1, static_cast<std::uint8_t>(upload.commit(digest.data())));

  std::array<std::uint8_t, 32> missing{};
  missing[0] = 0xFF;
  TEST_ASSERT_EQUAL_UINT8(
      1, static_cast<std::uint8_t>(upload.remove(
             missing.data(), digest.data(), false)));
  TEST_ASSERT_EQUAL_UINT8(
      3, static_cast<std::uint8_t>(upload.remove(
             digest.data(), digest.data(), true)));
  TEST_ASSERT_TRUE(upload.probe(digest.data()));
  TEST_ASSERT_EQUAL_UINT8(
      1, static_cast<std::uint8_t>(upload.commit(digest.data())));

  FakeStore rejected;
  rejected.validation_result = ledgrid::OperationResult::BadSignature;
  ledgrid::UploadManager failed(&rejected);
  TEST_ASSERT_EQUAL_UINT8(1,
      static_cast<std::uint8_t>(failed.begin(descriptor)));
  TEST_ASSERT_EQUAL_UINT8(1,
      static_cast<std::uint8_t>(failed.chunk(0, payload, sizeof(payload))));
  TEST_ASSERT_EQUAL_UINT8(6,
      static_cast<std::uint8_t>(failed.commit(digest.data())));
  TEST_ASSERT_FALSE(failed.probe(digest.data()));
  TEST_ASSERT_FALSE(rejected.committed);
}

void test_typed_parameters_are_bounded_typed_and_unique() {
  const std::uint8_t valid[] = {
      1, 3,
      5, 's', 'p', 'e', 'e', 'd', 2, 0x3f, 0x80, 0, 0,
      7, 'r', 'e', 'v', 'e', 'r', 's', 'e', 3, 1,
      7, 'p', 'a', 'l', 'e', 't', 't', 'e', 4, 4, 'w', 'a', 'r', 'm'};
  TEST_ASSERT_TRUE(ledgrid::validate_typed_parameter_blob(valid, sizeof(valid)));
  auto duplicate = std::vector<std::uint8_t>(valid, valid + sizeof(valid));
  duplicate[13] = 5;
  duplicate.erase(duplicate.begin() + 19, duplicate.begin() + 21);
  // Easier canonical duplicate-name fixture.
  const std::uint8_t dup[] = {1, 2, 1, 'x', 3, 0, 1, 'x', 3, 1};
  TEST_ASSERT_FALSE(ledgrid::validate_typed_parameter_blob(dup, sizeof(dup)));
  const std::uint8_t bad_bool[] = {1, 1, 1, 'x', 3, 2};
  TEST_ASSERT_FALSE(
      ledgrid::validate_typed_parameter_blob(bad_bool, sizeof(bad_bool)));
  TEST_ASSERT_FALSE(
      ledgrid::validate_typed_parameter_blob(valid, sizeof(valid) - 1));
}

void test_sdk_playback_controls_compose_time_scale_and_brightness() {
  std::vector<std::uint8_t> blob{1, 5};
  auto append_name = [&](const char* name, std::uint8_t type) {
    const std::size_t size = std::strlen(name);
    blob.push_back(static_cast<std::uint8_t>(size));
    blob.insert(blob.end(), name, name + size);
    blob.push_back(type);
  };
  auto append_float = [&](float value) {
    std::uint32_t bits = 0;
    std::memcpy(&bits, &value, sizeof(bits));
    append_u32(&blob, bits);
  };
  append_name("pause", 3); blob.push_back(1);
  append_name("loop", 3); blob.push_back(0);
  append_name("playback_speed", 2); append_float(1.5F);
  append_name("time_scale", 2); append_float(2.0F);
  append_name("asset_brightness", 2); append_float(0.5F);
  ledgrid::RuntimePlaybackControls controls{};
  TEST_ASSERT_TRUE(ledgrid::decode_runtime_playback_controls(
      blob.data(), blob.size(), &controls));
  TEST_ASSERT_TRUE(controls.paused);
  TEST_ASSERT_FALSE(controls.loop);
  TEST_ASSERT_FLOAT_WITHIN(0.001F, 1.5F, controls.playback_speed);
  TEST_ASSERT_FLOAT_WITHIN(0.001F, 2.0F, controls.time_scale);
  TEST_ASSERT_EQUAL_UINT16(3000,
      ledgrid::compose_frame_speed_permille(controls));
  TEST_ASSERT_EQUAL_UINT8(128, ledgrid::asset_brightness_u8(controls));
  TEST_ASSERT_EQUAL_UINT64(2500,
      ledgrid::scale_animation_elapsed_us(1000, 2.5F));

  const std::uint8_t invalid_late_float[] = {
      1, 2, 5, 'p', 'a', 'u', 's', 'e', 3, 1,
      10, 't', 'i', 'm', 'e', '_', 's', 'c', 'a', 'l', 'e',
      2, 0x7f, 0xc0, 0, 0};
  ledgrid::RuntimePlaybackControls unchanged{};
  TEST_ASSERT_FALSE(ledgrid::decode_runtime_playback_controls(
      invalid_late_float, sizeof(invalid_late_float), &unchanged));
  TEST_ASSERT_FALSE(unchanged.paused);

  controls.playback_speed = 10.0F;
  controls.time_scale = 10.0F;
  controls.asset_brightness = 2.0F;
  TEST_ASSERT_EQUAL_UINT16(4000,
      ledgrid::compose_frame_speed_permille(controls));
  TEST_ASSERT_EQUAL_UINT8(255, ledgrid::asset_brightness_u8(controls));
  TEST_ASSERT_EQUAL_UINT64(4000,
      ledgrid::scale_animation_elapsed_us(1000, controls.time_scale));
}

void test_cache_policy_evicts_oldest_inactive_and_store_owns_reserve() {
  std::array<std::array<std::uint8_t, 32>, 3> digests{};
  digests[0][0] = 1; digests[1][0] = 2; digests[2][0] = 3;
  const ledgrid::CacheEntryView entries[] = {
      {digests[0].data(), 30}, {digests[1].data(), 20},
      {digests[2].data(), 10}};
  TEST_ASSERT_EQUAL_INT(2,
      ledgrid::select_inactive_lru(entries, 3, nullptr));
  TEST_ASSERT_EQUAL_INT(1,
      ledgrid::select_inactive_lru(entries, 3, digests[2].data()));

  FakeStore store;
  store.free_capacity = 1;  // Store may evict before it creates .part.
  ledgrid::UploadManager upload(&store);
  ledgrid::AssetDescriptor descriptor{};
  descriptor.digest[0] = 9;
  descriptor.total_size = 1024;
  descriptor.kind = ledgrid::AssetKind::FrameTrack;
  descriptor.abi = 1;
  descriptor.strip_count = 8;
  descriptor.leds_per_strip = 138;
  TEST_ASSERT_EQUAL_UINT8(1,
      static_cast<std::uint8_t>(upload.begin(descriptor)));
  TEST_ASSERT_EQUAL_INT(1, store.begin_calls);

  ledgrid::AssetDescriptor replacement = descriptor;
  replacement.digest[0] = 10;
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OperationResult::InvalidState),
      static_cast<std::uint8_t>(upload.begin(replacement)));
  upload.abort();
  TEST_ASSERT_TRUE(store.part.empty());
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::UploadState::Idle),
      static_cast<std::uint8_t>(upload.state()));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OperationResult::Ok),
      static_cast<std::uint8_t>(upload.begin(replacement)));
}

void test_verified_asset_begin_binds_signed_index_before_staging() {
  const std::uint8_t payload[] = {'v', 'e', 'r', 'i', 'f', 'y'};
  const auto digest = digest_of(payload, sizeof(payload));
  ledgrid::AssetDescriptor descriptor{};
  std::memcpy(descriptor.digest, digest.data(), digest.size());
  descriptor.total_size = sizeof(payload);
  descriptor.kind = ledgrid::AssetKind::FrameTrack;
  descriptor.abi = ledgrid::kAnimationAbiV1;
  descriptor.target = ledgrid::kEsp32S3ElfLoaderTargetV1;
  descriptor.strip_count = ledgrid::kLocalStrips;
  descriptor.leds_per_strip = ledgrid::kInstalledLedsPerStrip;
  descriptor.logical_device = 2;
  const auto index = make_signed_index(descriptor.kind, digest);
  std::array<std::uint8_t, 64> signature{};
  signature.fill(0x5A);
  auto begin = make_asset_begin(descriptor, index, signature);
  TEST_ASSERT_EQUAL_UINT32(ledgrid::kCanonicalAssetBeginBytes, begin.size());

  FakeSignatureVerifier verifier;
  verifier.expected_index = index;
  verifier.expected_signature = signature;
  FakeStore store;
  ledgrid::ReceiverController controller(
      &store, ledgrid::kLocalStrips, ledgrid::kInstalledLedsPerStrip, 2,
      &verifier);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OperationResult::Ok),
      static_cast<std::uint8_t>(controller.process(begin.data(), begin.size())));
  TEST_ASSERT_EQUAL_INT(1, store.begin_calls);
  TEST_ASSERT_FALSE(store.probe(digest.data()));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::DisplayMode::Maintenance),
      static_cast<std::uint8_t>(controller.modes().mode()));

  // A failed/partial transfer is explicitly cancellable without reboot, and
  // cancellation restores the mode that maintenance displaced.
  std::vector<std::uint8_t> partial{
      static_cast<std::uint8_t>(ledgrid::Command::AssetChunk), 0, 0, 0, 0,
      payload[0], payload[1]};
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OperationResult::Ok),
      static_cast<std::uint8_t>(controller.process(partial.data(), partial.size())));
  std::vector<std::uint8_t> wrong_commit{
      static_cast<std::uint8_t>(ledgrid::Command::AssetCommit)};
  wrong_commit.insert(wrong_commit.end(), 32, 0xFF);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OperationResult::BadDigest),
      static_cast<std::uint8_t>(
          controller.process(wrong_commit.data(), wrong_commit.size())));
  const std::uint8_t abort[] = {
      static_cast<std::uint8_t>(ledgrid::Command::AssetAbort)};
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OperationResult::Ok),
      static_cast<std::uint8_t>(controller.process(abort, sizeof(abort))));
  TEST_ASSERT_TRUE(store.part.empty());
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::UploadState::Idle),
      static_cast<std::uint8_t>(controller.upload().state()));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::DisplayMode::StartupFallback),
      static_cast<std::uint8_t>(controller.modes().mode()));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OperationResult::Ok),
      static_cast<std::uint8_t>(controller.process(begin.data(), begin.size())));

  std::vector<std::uint8_t> chunk{
      static_cast<std::uint8_t>(ledgrid::Command::AssetChunk), 0, 0, 0, 0};
  chunk.insert(chunk.end(), payload, payload + sizeof(payload));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OperationResult::Ok),
      static_cast<std::uint8_t>(controller.process(chunk.data(), chunk.size())));
  std::vector<std::uint8_t> commit{
      static_cast<std::uint8_t>(ledgrid::Command::AssetCommit)};
  commit.insert(commit.end(), digest.begin(), digest.end());
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OperationResult::Ok),
      static_cast<std::uint8_t>(controller.process(commit.data(), commit.size())));
  TEST_ASSERT_TRUE(store.probe(digest.data()));
  // Install-only commit restores the prior mode; it never activates itself.
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::DisplayMode::StartupFallback),
      static_cast<std::uint8_t>(controller.modes().mode()));
}

void test_verified_asset_begin_rejects_trust_and_binding_failures_atomically() {
  const std::uint8_t payload[] = {'s', 'i', 'g'};
  const auto digest = digest_of(payload, sizeof(payload));
  ledgrid::AssetDescriptor descriptor{};
  std::memcpy(descriptor.digest, digest.data(), digest.size());
  descriptor.total_size = sizeof(payload);
  descriptor.kind = ledgrid::AssetKind::FrameTrack;
  descriptor.abi = 1;
  descriptor.target = 1;
  descriptor.strip_count = 8;
  descriptor.leds_per_strip = 138;
  descriptor.logical_device = 1;
  const auto index = make_signed_index(descriptor.kind, digest);
  std::array<std::uint8_t, 64> signature{};
  signature.fill(0x33);
  const auto canonical = make_asset_begin(descriptor, index, signature);

  FakeSignatureVerifier verifier;
  verifier.expected_index = index;
  verifier.expected_signature = signature;
  auto reject = [&](std::vector<std::uint8_t> command,
                    ledgrid::OperationResult expected,
                    const ledgrid::AssetSignatureVerifier* selected_verifier = nullptr) {
    FakeStore store;
    ledgrid::ReceiverController controller(
        &store, 8, 138, 1,
        selected_verifier == nullptr ? &verifier : selected_verifier);
    TEST_ASSERT_EQUAL_UINT8(
        static_cast<std::uint8_t>(expected),
        static_cast<std::uint8_t>(controller.process(command.data(), command.size())));
    TEST_ASSERT_EQUAL_INT(0, store.begin_calls);
    TEST_ASSERT_TRUE(store.part.empty());
    TEST_ASSERT_FALSE(store.committed);
    TEST_ASSERT_EQUAL_UINT8(
        static_cast<std::uint8_t>(ledgrid::DisplayMode::StartupFallback),
        static_cast<std::uint8_t>(controller.modes().mode()));
  };

  auto unknown_key = canonical;
  unknown_key[69] = '0';
  reject(unknown_key, ledgrid::OperationResult::UnknownKey);

  auto altered_index = canonical;
  altered_index[72 + 16] ^= 1U;  // signed manifest digest, not descriptor fields
  reject(altered_index, ledgrid::OperationResult::BadSignature);

  auto altered_signature = canonical;
  altered_signature[249] ^= 1U;
  reject(altered_signature, ledgrid::OperationResult::BadSignature);

  auto selected_digest = canonical;
  selected_digest[8] ^= 1U;
  reject(selected_digest, ledgrid::OperationResult::BadDigest);

  auto wrong_abi = canonical;
  wrong_abi[42] = 2;
  wrong_abi[72 + 7] = 2;
  reject(wrong_abi, ledgrid::OperationResult::WrongAbi);

  auto wrong_target = canonical;
  wrong_target[44] = 2;
  wrong_target[72 + 9] = 2;
  reject(wrong_target, ledgrid::OperationResult::WrongTarget);

  auto wrong_geometry = canonical;
  wrong_geometry[45] = 7;
  wrong_geometry[72 + 11] = 7;
  reject(wrong_geometry, ledgrid::OperationResult::WrongGeometry);

  auto wrong_device = canonical;
  wrong_device[48] = 4;
  reject(wrong_device, ledgrid::OperationResult::WrongDevice);

  auto wrong_kind = canonical;
  wrong_kind[40] = static_cast<std::uint8_t>(ledgrid::AssetKind::Native);
  reject(wrong_kind, ledgrid::OperationResult::BadEnvelope);

  auto truncated = canonical;
  truncated.pop_back();
  reject(truncated, ledgrid::OperationResult::BadSize);

  auto oversized = canonical;
  oversized.resize(ledgrid::kAssetBeginEnvelopeMaxBytes + 1U, 0);
  reject(oversized, ledgrid::OperationResult::BadSize);

  FakeStore no_trust_store;
  ledgrid::ReceiverController no_trust(&no_trust_store, 8, 138, 1, nullptr);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OperationResult::Unsupported),
      static_cast<std::uint8_t>(
          no_trust.process(canonical.data(), canonical.size())));
  TEST_ASSERT_EQUAL_INT(0, no_trust_store.begin_calls);
}

void test_receiver_watchdog_transitions_to_fallback_and_reports_timing() {
  FakeStore store;
  FakeAnimationBackend backend;
  FakePersistence persistence;
  ledgrid::ReceiverController controller(
      &store, 8, 138, 0, nullptr, &backend, &persistence);
  const std::uint8_t payload[] = {'o', 'k'};
  const auto digest = digest_of(payload, sizeof(payload));
  store.committed = true;
  std::memcpy(store.descriptor.digest, digest.data(), digest.size());
  store.descriptor.total_size = sizeof(payload);
  store.descriptor.kind = ledgrid::AssetKind::Native;
  store.descriptor.abi = 1;
  store.descriptor.strip_count = 8;
  store.descriptor.leds_per_strip = 138;
  store.descriptor.logical_device = 0;
  const std::uint8_t empty_parameters[] = {1, 0};
  std::vector<std::uint8_t> start{0x26};
  start.insert(start.end(), digest.begin(), digest.end());
  append_u16(&start, 0);
  append_u16(&start, sizeof(empty_parameters));
  start.insert(start.end(), empty_parameters,
               empty_parameters + sizeof(empty_parameters));
  TEST_ASSERT_EQUAL_UINT8(
      1, static_cast<std::uint8_t>(controller.process(start.data(), start.size())));
  TEST_ASSERT_EQUAL_UINT8(
      2, static_cast<std::uint8_t>(controller.modes().mode()));
  controller.render_completed(true, ledgrid::kAnimationRenderWatchdogUs + 1);
  TEST_ASSERT_EQUAL_UINT8(
      0, static_cast<std::uint8_t>(controller.modes().mode()));
  TEST_ASSERT_TRUE(controller.modes().quarantined());
  TEST_ASSERT_EQUAL_INT(1, backend.starts);
  TEST_ASSERT_TRUE(backend.stops >= 1);
  TEST_ASSERT_EQUAL_INT(1, persistence.active_writes);
  TEST_ASSERT_EQUAL_INT(1, persistence.quarantine_writes);
  ledgrid::ReceiverStatus status{};
  controller.populate_status(&status);
  TEST_ASSERT_EQUAL_HEX32(
      ledgrid::kCapabilityTypedParameters | ledgrid::kCapabilityQuarantine |
          ledgrid::kCapabilityNative | ledgrid::kCapabilityFrameTrack |
          ledgrid::kCapabilityPsramExecution |
          ledgrid::kCapabilityLogicalDeviceIdentity,
      status.capabilities);
  TEST_ASSERT_EQUAL_UINT16(25001, status.last_render_us);
  TEST_ASSERT_EQUAL_UINT16(25001, status.max_render_us);
  TEST_ASSERT_EQUAL_UINT16(1, status.missed_deadlines);
  TEST_ASSERT_EQUAL_UINT8(1, status.watchdog_events);
  TEST_ASSERT_EQUAL_UINT8(1, status.quarantine_state);
}

void test_controller_routes_runtime_lifecycle_and_reports_initialized_caps() {
  FakeStore store;
  FakeAnimationBackend backend;
  FakePersistence persistence;
  FakeSignatureVerifier verifier;
  ledgrid::ReceiverController controller(
      &store, 8, 138, 0, &verifier, &backend, &persistence);
  const std::uint8_t payload[] = {'r', 'u', 'n'};
  const auto digest = digest_of(payload, sizeof(payload));
  store.committed = true;
  std::memcpy(store.descriptor.digest, digest.data(), 32);
  store.descriptor.total_size = sizeof(payload);
  store.descriptor.kind = ledgrid::AssetKind::FrameTrack;
  store.descriptor.abi = 1;
  store.descriptor.target = 1;
  store.descriptor.strip_count = 8;
  store.descriptor.leds_per_strip = 138;
  store.descriptor.logical_device = 0;
  const std::uint8_t empty[] = {1, 0};
  std::vector<std::uint8_t> start{0x26};
  start.insert(start.end(), digest.begin(), digest.end());
  append_u16(&start, 0);
  append_u16(&start, sizeof(empty));
  start.insert(start.end(), empty, empty + sizeof(empty));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::OperationResult::Ok),
      static_cast<std::uint8_t>(controller.process(start.data(), start.size())));
  TEST_ASSERT_EQUAL_INT(1, backend.starts);
  TEST_ASSERT_EQUAL_INT(1, persistence.active_writes);

  const std::uint8_t restart[] = {0x28};
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      controller.process(restart, sizeof(restart))));
  const std::uint8_t parameters[] = {0x29, 0, 2, 1, 0};
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      controller.process(parameters, sizeof(parameters))));
  TEST_ASSERT_EQUAL_INT(1, backend.restarts);
  TEST_ASSERT_EQUAL_INT(1, backend.updates);

  ledgrid::ReceiverStatus status{};
  controller.populate_status(&status);
  const std::uint32_t expected = ledgrid::kCapabilityTypedParameters |
      ledgrid::kCapabilityQuarantine | ledgrid::kCapabilityNative |
      ledgrid::kCapabilityFrameTrack | ledgrid::kCapabilityPsramExecution |
      ledgrid::kCapabilitySignedPackages | ledgrid::kCapabilityAssetUpload |
      ledgrid::kCapabilityLogicalDeviceIdentity;
  TEST_ASSERT_EQUAL_HEX32(expected, status.capabilities);

  controller.host_frame_received();
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::DisplayMode::HostFrames),
      static_cast<std::uint8_t>(controller.modes().mode()));
  TEST_ASSERT_TRUE(backend.stops >= 1);
  TEST_ASSERT_TRUE(persistence.active_clears >= 1);
}

void test_controller_reports_nonzero_provisioned_logical_identity() {
  FakeStore store;
  FakeAnimationBackend backend;
  FakePersistence persistence;
  FakeSignatureVerifier verifier;
  ledgrid::ReceiverController controller(
      &store, 8, 138, 3, &verifier, &backend, &persistence);
  ledgrid::ReceiverStatus status{};
  controller.populate_status(&status);
  TEST_ASSERT_TRUE(
      status.capabilities & ledgrid::kCapabilityLogicalDeviceIdentity);
  TEST_ASSERT_EQUAL_UINT32(
      3U,
      (status.capabilities & ledgrid::kCapabilityLogicalDeviceMask) >>
          ledgrid::kCapabilityLogicalDeviceShift);
}

}  // namespace

void setUp() {}
void tearDown() {}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_encoder_emits_parallel_grb_waveform);
  RUN_TEST(test_encoder_scales_brightness_before_bit_expansion);
  RUN_TEST(test_optimized_encoder_updates_all_eight_lanes);
  RUN_TEST(test_encoder_appends_300us_reset_and_rejects_bad_bounds);
  RUN_TEST(test_startup_rainbow_is_45_degrees_and_moves_up_right);
  RUN_TEST(test_startup_rainbow_cycles_once_per_second_and_checks_bounds);
  RUN_TEST(test_startup_fallback_invokes_the_native_module_byte_for_byte);
  RUN_TEST(test_startup_native_fractional_speed_is_continuous_across_one_second);
  RUN_TEST(test_native_time_matches_reference_across_the_32_bit_wrap);
  RUN_TEST(test_checked_in_native_examples_render_distinct_bounded_frames);
  RUN_TEST(test_mailbox_replaces_only_unread_ready_frames);
  RUN_TEST(test_status_layout_is_stable);
  RUN_TEST(test_spi_ceiling_and_display_ownership_contract);
  RUN_TEST(test_mode_machine_host_ownership_maintenance_and_quarantine);
  RUN_TEST(test_frame_track_decodes_keyframe_delta_brightness_and_timing);
  RUN_TEST(test_frame_track_rejects_all_truncation_and_run_overflow_before_writes);
  RUN_TEST(test_upload_is_ordered_retryable_atomic_and_fails_closed);
  RUN_TEST(test_typed_parameters_are_bounded_typed_and_unique);
  RUN_TEST(test_sdk_playback_controls_compose_time_scale_and_brightness);
  RUN_TEST(test_cache_policy_evicts_oldest_inactive_and_store_owns_reserve);
  RUN_TEST(test_verified_asset_begin_binds_signed_index_before_staging);
  RUN_TEST(test_verified_asset_begin_rejects_trust_and_binding_failures_atomically);
  RUN_TEST(test_receiver_watchdog_transitions_to_fallback_and_reports_timing);
  RUN_TEST(test_controller_routes_runtime_lifecycle_and_reports_initialized_caps);
  RUN_TEST(test_controller_reports_nonzero_provisioned_logical_identity);
  return UNITY_END();
}
