#pragma once

#include <cstddef>
#include <cstdint>

namespace ledgrid {

constexpr std::uint8_t kStatusProtocolVersion = 2;
// The original 64 bytes were fully assigned, so stagger_phases starts a new
// word past them. Hosts read the snapshot by offset and treat a zero here as
// "firmware predates the field" rather than as a legal phase count.
constexpr std::size_t kStatusBytesV2 = 68;

struct ReceiverStatusV2 {
  std::uint8_t flags = 0;
  std::uint8_t active_strips = 0;
  std::uint8_t lane_mask = 0xFF;
  std::uint16_t leds_per_strip = 0;
  std::uint16_t queued_transactions = 0;
  std::uint32_t packets = 0;
  std::uint32_t crc_errors = 0;
  std::uint32_t crc_ok_packets = 0;
  std::uint32_t frames_accepted = 0;
  std::uint32_t frames_displayed = 0;
  std::uint32_t frames_superseded = 0;
  std::uint32_t publish_drops = 0;
  std::uint32_t spi_queue_errors = 0;
  std::uint16_t last_crc_us = 0;
  std::uint16_t last_copy_us = 0;
  std::uint16_t last_encode_us = 0;
  std::uint16_t last_show_us = 0;
  std::uint32_t last_accepted_sequence = 0;
  std::uint32_t last_displayed_sequence = 0;
  std::uint32_t display_errors = 0;
  std::uint8_t stagger_phases = 1;
};

bool encode_receiver_status_v2(
    const ReceiverStatusV2& status,
    std::uint8_t* output,
    std::size_t output_size);

}  // namespace ledgrid
