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

bool encode_receiver_status(
    const ReceiverStatus& status,
    std::uint8_t* output,
    std::size_t output_size) {
  if (output == nullptr || output_size < kStatusBytes) return false;
  std::memset(output, 0, kStatusBytes);
  output[0] = 'L';
  output[1] = 'G';
  output[2] = 'S';
  output[3] = '3';
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
  write_u32(output + 64, status.capabilities);
  output[68] = static_cast<std::uint8_t>(status.display_mode);
  output[69] = static_cast<std::uint8_t>(status.asset_kind);
  output[70] = static_cast<std::uint8_t>(status.upload_state);
  output[71] = static_cast<std::uint8_t>(status.last_result);
  std::memcpy(output + 72, status.active_digest, sizeof(status.active_digest));
  write_u32(output + 104, status.cache_free_bytes);
  write_u32(output + 108, status.cache_used_bytes);
  write_u32(output + 112, status.upload_received_bytes);
  write_u32(output + 116, status.upload_total_bytes);
  write_u16(output + 120, status.last_render_us);
  write_u16(output + 122, status.max_render_us);
  write_u16(output + 124, status.missed_deadlines);
  output[126] = status.watchdog_events;
  output[127] = status.quarantine_state;
  return true;
}

bool valid_spi_transaction_size(std::size_t command_bytes) {
  return command_bytes > 0 && command_bytes <= kMaxCommandBytes;
}

bool command_takes_display_ownership(Command command) {
  return command == Command::Show || command == Command::Clear ||
         command == Command::SetAll;
}

}  // namespace ledgrid
