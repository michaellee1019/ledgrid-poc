#include "ledgrid/protocol.hpp"

#include <array>
#include <cstring>

namespace ledgrid {
namespace {

constexpr std::array<std::uint32_t, 256> status_crc32_table() {
  std::array<std::uint32_t, 256> table{};
  for (std::size_t i = 0; i < table.size(); ++i) {
    std::uint32_t value = static_cast<std::uint32_t>(i);
    for (unsigned bit = 0; bit < 8; ++bit) {
      value = (value >> 1U) ^ ((value & 1U) ? 0xEDB88320U : 0U);
    }
    table[i] = value;
  }
  return table;
}
constexpr auto kStatusCrc32Table = status_crc32_table();

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

void encode_v2_fields(
    const ReceiverStatusV2& status, std::uint8_t* output) {
  output[5] = status.flags;
  output[6] = status.active_strips;
  output[7] = status.lane_mask;
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
}

}  // namespace

bool valid_installed_config(const std::uint8_t* data, std::size_t length) {
 if (!data || length != 8 || data[0] != 7 || data[5] >= 5) return false;
 const std::uint16_t leds=(data[2]<<8)|data[3];
 const std::uint16_t offset=(data[6]<<8)|data[7];
 return leds == 138 && offset == data[5]*8 && data[1] == (data[5]==4?1:8) && (data[4] & ~1U) == 0;
}
bool configure_host_output(const std::uint8_t* data, std::size_t length,
 OutputConfiguration* output, std::uint8_t* pixels, std::size_t capacity) {
 if (!output || !pixels || !valid_installed_config(data,length) || capacity < data[1]*138U*3U) return false;
 if (output->strip_count != data[1] || output->leds_per_strip != 138) {
  std::memset(pixels,0,capacity);
  output->strip_count=data[1]; output->leds_per_strip=138;
 }
 return true;
}
bool encode_receiver_status_v8(const ReceiverStatusV7& status, std::uint8_t* output, std::size_t output_size) {
 if (!output || output_size < kStatusBytesV8) return false;
 std::memset(output, 0, kStatusBytesV8);
 std::memcpy(output, "LGS8", 4); output[4] = 8;
 encode_v2_fields(status, output);
 write_u32(output + 64, status.capabilities);
 output[72] = status.last_result;
 output[68] = 2; // Host full-frame output; no receiver scene engine.
 write_u32(output + 80, status.global_strip_offset);
 output[312] = status.logical_receiver_id;
 output[313] = status.last_processed_command;
 output[314] = status.stagger_phases;
 write_u32(output + 316, status.operation_sequence);
  // Retired transport fields stay reserved and CRC-covered for LGS8 peers.
  std::uint32_t checksum = 0xFFFFFFFFU;
  for (std::size_t i = 0; i < kStatusBytesV7; ++i) {
    checksum = (checksum >> 8U) ^ kStatusCrc32Table[(checksum ^ output[i]) & 0xFFU];
  }
  write_u32(output + kStatusBytesV7, checksum ^ 0xFFFFFFFFU);
  return true;
}

std::uint16_t animation_pipeline_crc16_ccitt(const std::uint8_t* data, std::size_t size) {
 std::uint16_t crc=0xFFFF;
 for (std::size_t i=0;i<size;++i) { crc ^= static_cast<std::uint16_t>(data[i])<<8; for(unsigned b=0;b<8;++b) crc=(crc&0x8000)?(crc<<1)^0x1021:crc<<1; }
 return crc;
}
bool receiver_packet_crc_valid(
    const std::uint8_t* packet,
    std::size_t packet_size,
    std::uint16_t* computed_crc) {
  if (packet == nullptr || packet_size < 1U + kAnimationPipelineCrcBytes ||
      packet_size > kAnimationPipelineMaxTransactionBytes) {
    return false;
  }
  const std::size_t payload_size =
      packet_size - kAnimationPipelineCrcBytes;
  const std::uint16_t calculated =
      animation_pipeline_crc16_ccitt(packet, payload_size);
  if (computed_crc != nullptr) *computed_crc = calculated;
  const std::uint16_t received = static_cast<std::uint16_t>(
      (static_cast<std::uint16_t>(packet[payload_size]) << 8U) |
      packet[payload_size + 1U]);
  return received == calculated;
}

bool decode_receiver_packet_payload(
    const std::uint8_t* packet,
    std::size_t packet_size,
    ReceiverPacketPayload* payload) {
  if (payload != nullptr) *payload = {};
  return receiver_packet_crc_valid(packet, packet_size) &&
      decode_crc_valid_receiver_packet_payload(packet, packet_size, payload);
}

bool decode_crc_valid_receiver_packet_payload(
    const std::uint8_t* packet,
    std::size_t packet_size,
    ReceiverPacketPayload* payload) {
  if (payload == nullptr) return false;
  *payload = {};
  if (packet == nullptr ||
      packet_size < 1U + kAnimationPipelineCrcBytes ||
      packet_size > kAnimationPipelineMaxTransactionBytes) {
    return false;
  }

  const std::size_t outer_payload_size =
      packet_size - kAnimationPipelineCrcBytes;
  if (packet[0] !=
      static_cast<std::uint8_t>(ReceiverCommand::AlignedEnvelope)) {
    return false;
  }

  if (packet_size % kSpiDmaAlignmentBytes != 0U ||
      outer_payload_size < kAlignedEnvelopeHeaderBytes + 1U ||
      packet[1] != kAlignedEnvelopeVersion) {
    return false;
  }
  const std::size_t semantic_size =
      (static_cast<std::size_t>(packet[2]) << 8U) | packet[3];
  if (semantic_size == 0U ||
      semantic_size > kAlignedEnvelopeMaxSemanticBytes) {
    return false;
  }
  const std::size_t unpadded_size =
      kAlignedEnvelopeHeaderBytes + semantic_size +
      kAnimationPipelineCrcBytes;
  const std::size_t padding_size =
      (kSpiDmaAlignmentBytes - unpadded_size % kSpiDmaAlignmentBytes) %
      kSpiDmaAlignmentBytes;
  if (packet_size != unpadded_size + padding_size) return false;
  const std::size_t padding_offset =
      kAlignedEnvelopeHeaderBytes + semantic_size;
  for (std::size_t index = 0; index < padding_size; ++index) {
    if (packet[padding_offset + index] != 0U) return false;
  }
  payload->data = packet + kAlignedEnvelopeHeaderBytes;
  payload->size = semantic_size;
  payload->aligned_envelope = true;
  return true;
}


} // namespace ledgrid
