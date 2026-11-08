#pragma once
#include <cstddef>
#include <cstdint>
namespace ledgrid {
constexpr std::size_t kAnimationPipelineMaxTransactionBytes = 4096;
constexpr std::size_t kAnimationPipelineCrcBytes = 2;
constexpr std::uint8_t kStatusProtocolVersion = 2;
// The original 64 bytes were fully assigned, so stagger_phases starts a new
// word past them. Hosts read the snapshot by offset and treat a zero here as
// "firmware predates the field" rather than as a legal phase count.
constexpr std::size_t kStatusBytesV2 = 68;
constexpr std::uint8_t kStatusProtocolVersionV3 = 3;
constexpr std::size_t kStatusBytesV3 = 320;
constexpr std::uint8_t kStatusProtocolVersionV4 = 4;
constexpr std::size_t kStatusBytesV4 = 416;
constexpr std::uint8_t kStatusProtocolVersionV5 = 5;
constexpr std::size_t kStatusBytesV5 = 768;
constexpr std::uint8_t kStatusProtocolVersionV6 = 6;
constexpr std::size_t kStatusBytesV6 = 1216;
constexpr std::uint8_t kStatusProtocolVersionV7 = 7;
constexpr std::size_t kStatusBytesV7 = 1248;
constexpr std::uint8_t kStatusProtocolVersionV8 = 8;
constexpr std::size_t kStatusBytesV8 = kStatusBytesV7 + 4;
// ESP32-S3 SPI slave DMA requires every Host write to be a multiple of one
// 32-bit word.  The transport envelope carries an exact semantic length and
// CRC-covered zero padding so command parsers never mistake DMA padding for
// command data. The matched current host and firmware always use this framing.
constexpr std::uint8_t kAlignedEnvelopeVersion = 1;
constexpr std::size_t kAlignedEnvelopeHeaderBytes = 4;
constexpr std::size_t kSpiDmaAlignmentBytes = 4;
constexpr std::size_t kAlignedEnvelopeMaxSemanticBytes =
    kAnimationPipelineMaxTransactionBytes - kAlignedEnvelopeHeaderBytes -
    kAnimationPipelineCrcBytes;
enum class ReceiverCommand : std::uint8_t {
  SetPixel = 0x01,
  SetBrightness = 0x02,
  Show = 0x03,
  Clear = 0x04,
  SetRange = 0x05,
  SetAll = 0x06,
  Config = 0x07,
  StatusQuery = 0x08,
  SetLaneMask = 0x09,
  SetStagger = 0x0A,
  AlignedEnvelope = 0x0B,
  Ping = 0xFF,
};
struct ReceiverPacketPayload {
  const std::uint8_t* data = nullptr;
  std::size_t size = 0;
  bool aligned_envelope = false;
};

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

struct ReceiverStatusV7 : ReceiverStatusV2 {
  std::uint8_t last_result = 0;
  std::uint32_t capabilities = 0;
  std::uint32_t global_strip_offset = 0;
  std::uint8_t logical_receiver_id = 0xFF;
  std::uint8_t last_processed_command = 0;
  std::uint32_t operation_sequence = 0;
};

bool receiver_packet_crc_valid(
    const std::uint8_t* packet,
    std::size_t packet_size,
    std::uint16_t* computed_crc = nullptr);
bool decode_receiver_packet_payload(
    const std::uint8_t* packet,
    std::size_t packet_size,
    ReceiverPacketPayload* payload);
// Decode framing after receiver_packet_crc_valid() has already accepted this
// exact packet.  Keeping this separate avoids hashing every live frame twice.
bool decode_crc_valid_receiver_packet_payload(
    const std::uint8_t* packet,
    std::size_t packet_size,
    ReceiverPacketPayload* payload);

std::uint16_t animation_pipeline_crc16_ccitt(const std::uint8_t*, std::size_t);
struct OutputConfiguration {
 std::uint8_t strip_count=8; std::uint16_t leds_per_strip=138; std::uint8_t brightness=0;
 std::size_t total_leds() const {return strip_count*leds_per_strip;}
 std::size_t rgb_bytes() const {return total_leds()*3;}
};
bool configure_host_output(const std::uint8_t*, std::size_t, OutputConfiguration*, std::uint8_t*, std::size_t);
bool valid_installed_config(const std::uint8_t*, std::size_t);
bool encode_receiver_status_v8(const ReceiverStatusV7&, std::uint8_t*, std::size_t);
} // namespace ledgrid
