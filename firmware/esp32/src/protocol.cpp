#include "ledgrid/protocol.hpp"

#include <cstring>

namespace ledgrid {
namespace {

void write_u16(std::uint8_t* output, std::uint16_t value) {
  output[0] = static_cast<std::uint8_t>(value >> 8);
  output[1] = static_cast<std::uint8_t>(value);
}

void write_u32(std::uint8_t* output, std::uint32_t value) {
  output[0] = static_cast<std::uint8_t>(value >> 24);
  output[1] = static_cast<std::uint8_t>(value >> 16);
  output[2] = static_cast<std::uint8_t>(value >> 8);
  output[3] = static_cast<std::uint8_t>(value);
}

}  // namespace

bool encode_receiver_status_v2(
    const ReceiverStatusV2& status,
    std::uint8_t* output,
    std::size_t output_size) {
  if (output == nullptr || output_size < kStatusBytesV2) return false;
  std::memset(output, 0, kStatusBytesV2);
  output[0] = 'L';
  output[1] = 'G';
  output[2] = 'S';
  output[3] = '2';
  output[4] = kStatusProtocolVersion;
  output[5] = status.flags;
  output[6] = status.active_strips;
  write_u16(output + 8, status.leds_per_strip);
  write_u16(output + 10, status.queued_transactions);
  write_u32(output + 12, status.packets);
  write_u32(output + 16, status.crc_errors);
  write_u32(output + 20, status.crc_ok_packets);
  write_u32(output + 24, status.frames_accepted);
  write_u32(output + 28, status.frames_displayed);
  write_u32(output + 32, status.frames_superseded);
  write_u32(output + 36, status.publish_drops);
  write_u32(output + 40, status.spi_queue_errors);
  write_u16(output + 44, status.last_crc_us);
  write_u16(output + 46, status.last_copy_us);
  write_u16(output + 48, status.last_encode_us);
  write_u16(output + 50, status.last_show_us);
  write_u32(output + 52, status.last_accepted_sequence);
  write_u32(output + 56, status.last_displayed_sequence);
  write_u32(output + 60, status.display_errors);
  return true;
}

}  // namespace ledgrid
