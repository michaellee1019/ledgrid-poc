#include <unity.h>

#include <array>
#include <cstdint>
#include <vector>

#include "ledgrid/frame_mailbox.hpp"
#include "ledgrid/protocol.hpp"
#include "ledgrid/startup_animation.hpp"
#include "ledgrid/ws2812_encoder.hpp"

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

void test_status_v2_layout_is_stable() {
  ledgrid::ReceiverStatusV2 status{};
  status.flags = 3;
  status.active_strips = 8;
  status.leds_per_strip = 140;
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
  std::array<std::uint8_t, ledgrid::kStatusBytesV2> encoded{};

  TEST_ASSERT_TRUE(ledgrid::encode_receiver_status_v2(
      status, encoded.data(), encoded.size()));
  TEST_ASSERT_EQUAL_MEMORY("LGS2", encoded.data(), 4);
  TEST_ASSERT_EQUAL_UINT8(2, encoded[4]);
  TEST_ASSERT_EQUAL_UINT8(3, encoded[5]);
  TEST_ASSERT_EQUAL_UINT8(8, encoded[6]);
  TEST_ASSERT_EQUAL_UINT16(140, read_u16(encoded.data() + 8));
  TEST_ASSERT_EQUAL_UINT16(2, read_u16(encoded.data() + 10));
  TEST_ASSERT_EQUAL_UINT32(14, read_u32(encoded.data() + 24));
  TEST_ASSERT_EQUAL_UINT32(16, read_u32(encoded.data() + 32));
  TEST_ASSERT_EQUAL_UINT16(21, read_u16(encoded.data() + 48));
  TEST_ASSERT_EQUAL_UINT32(24, read_u32(encoded.data() + 56));
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
  RUN_TEST(test_mailbox_replaces_only_unread_ready_frames);
  RUN_TEST(test_status_v2_layout_is_stable);
  return UNITY_END();
}
