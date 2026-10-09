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
// Aligned-envelope v2 protects its four-byte discriminator/inner-length header
// and the complete canonical v1 wire packet (header, semantic payload, zero
// alignment, and CRC) in fixed 128-byte systematic data codewords. Eleven
// Hamming check bits plus one overall even-parity bit make each codeword 130
// wire bytes. The codeword count is rounded up to an even number so the
// complete SPI transaction remains 32-bit DMA aligned.
constexpr std::uint8_t kFecEnvelopeVersionV2 = 2;
constexpr std::size_t kFecEnvelopeHeaderBytes = 4;
constexpr std::size_t kFecV2DataBytes = 128;
constexpr std::size_t kFecV2ParityBytes = 2;
constexpr std::size_t kFecV2CodewordBytes =
    kFecV2DataBytes + kFecV2ParityBytes;
constexpr std::size_t kFecV2ParityBits = 11;
constexpr std::uint16_t kFecV2ParityMask = (1U << kFecV2ParityBits) - 1U;
constexpr std::uint16_t kFecV2OverallParityMask = 1U << kFecV2ParityBits;
constexpr std::size_t kFecV2MaxCodewords = 30;
constexpr std::size_t kFecV2EnvelopeMaxSemanticBytes =
    kFecV2MaxCodewords * kFecV2DataBytes - kFecEnvelopeHeaderBytes -
    kAlignedEnvelopeHeaderBytes - kAnimationPipelineCrcBytes;

// V3 retains the canonical v1 inner packet but uses 16 data symbols plus
// three GF(256) parity symbols. Codewords are byte-interleaved across the wire,
// so any contiguous burst no longer than the codeword count changes at most
// one symbol per codeword. Three independent syndromes correct one arbitrary
// byte and detect two; separated prefix/suffix discriminators keep framing
// attributable when one end of the transaction is damaged.
constexpr std::uint8_t kFecEnvelopeVersionV3 = 3;
constexpr std::size_t kFecV3DataBytes = 16;
constexpr std::size_t kFecV3ParityBytes = 3;
constexpr std::size_t kFecV3CodewordBytes =
    kFecV3DataBytes + kFecV3ParityBytes;
constexpr std::size_t kFecWireHeaderBytes = 2U * kFecEnvelopeHeaderBytes;
constexpr std::size_t kFecV3MaxCodewords = 212;
constexpr std::size_t kFecV3EnvelopeMaxSemanticBytes =
    kFecV3MaxCodewords * kFecV3DataBytes - kFecEnvelopeHeaderBytes -
    kAlignedEnvelopeHeaderBytes - kAnimationPipelineCrcBytes;

// V4 is a shortened systematic Reed-Solomon code. Its 25 data and five parity
// symbols have distinct GF(256) evaluation points, giving minimum distance six:
// two arbitrary byte errors per codeword are corrected and three are detected.
// Interleaving 136 codewords extends contiguous-burst correction to 272 bytes
// while keeping the full receiver-3 packet within the 4,096-byte SPI limit.
constexpr std::uint8_t kFecEnvelopeVersionV4 = 4;
constexpr std::size_t kFecV4DataBytes = 25;
constexpr std::size_t kFecV4ParityBytes = 5;
constexpr std::size_t kFecV4CodewordBytes =
    kFecV4DataBytes + kFecV4ParityBytes;
constexpr std::size_t kFecV4MaxCodewords = 136;
constexpr std::size_t kFecV4EnvelopeMaxSemanticBytes =
    kFecV4MaxCodewords * kFecV4DataBytes - kFecEnvelopeHeaderBytes -
    kAlignedEnvelopeHeaderBytes - kAnimationPipelineCrcBytes;

// V5 increases the shortened systematic Reed-Solomon distance to eleven.
// Ten parity symbols correct five arbitrary byte errors in each 50-byte data
// codeword. Interleaving 68 codewords corrects 340 bytes without assuming an
// error shape; the bounded contiguous-erasure fallback uses nine syndromes to
// repair 612 bytes and the tenth to validate that candidate. Bursts through
// 679 bytes may consume all ten syndromes in a contiguous run of codewords and
// are accepted only after unique canonical whole-frame CRC validation. Longer
// repeated-bit spans are repaired only when all ten syndromes identify one
// unique constant-XOR interval.
// The installed broad-frame wire size remains 4,088 bytes.
constexpr std::uint8_t kFecEnvelopeVersionV5 = 5;
// V6 retains the exact v5 code and 4,088-byte installed wire bound, but rotates
// logical codeword positions by the symbol row before writing each interleave
// row. Fixed-position/periodic link interference is therefore distributed
// across codewords instead of repeatedly consuming one codeword's ten
// syndromes. Receivers retain ordinary v5 deinterleaving for rollback hosts.
constexpr std::uint8_t kFecEnvelopeVersionV6 = 6;
// V7 reserves the final systematic codeword as the XOR of all preceding data
// codewords. After ordinary RS correction, one otherwise-uncorrectable data
// codeword can be reconstructed from that protected outer parity shard. The
// installed frame remains exactly 68 codewords/4,088 wire bytes.
constexpr std::uint8_t kFecEnvelopeVersion = 7;
constexpr std::size_t kFecDataBytes = 50;
constexpr std::size_t kFecParityBytes = 10;
constexpr std::size_t kFecCodewordBytes =
    kFecDataBytes + kFecParityBytes;
constexpr std::size_t kFecMaxCodewords = 68;
constexpr std::size_t kFecOuterParityBytes = kFecDataBytes;
constexpr std::size_t kFecV6EnvelopeMaxSemanticBytes =
    kFecMaxCodewords * kFecDataBytes - kFecEnvelopeHeaderBytes -
    kAlignedEnvelopeHeaderBytes - kAnimationPipelineCrcBytes;
constexpr std::size_t kFecEnvelopeMaxSemanticBytes = 3338;
constexpr std::size_t kFecScratchBytes =
    kFecV2MaxCodewords * kFecV2DataBytes;
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
  bool fec_envelope = false;
};

enum class ReceiverPacketDecodeResult : std::uint8_t {
  Ok = 0,
  InvalidFraming = 1,
  CrcError = 2,
  FecUncorrectable = 3,
  FecSemanticCrcError = 4,
};

struct ReceiverPacketDecodeReport {
  ReceiverPacketDecodeResult result = ReceiverPacketDecodeResult::InvalidFraming;
  bool fec_envelope_attempted = false;
  std::uint16_t corrected_codewords = 0;
  std::uint16_t corrected_bits = 0;
};

enum class ReceiverFecPacketOutcome : std::uint8_t {
  NotFec = 0,
  Accepted = 1,
  Uncorrectable = 2,
  SemanticCrcError = 3,
  FramingError = 4,
};

ReceiverFecPacketOutcome receiver_fec_packet_outcome(
    bool decoded_ok, const ReceiverPacketDecodeReport& report);

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
  std::uint32_t fec_packets_received = 0;
  std::uint32_t fec_packets_accepted = 0;
  std::uint32_t fec_corrected_packets = 0;
  std::uint32_t fec_corrected_codewords = 0;
  std::uint32_t fec_uncorrectable_packets = 0;
  std::uint32_t fec_semantic_crc_errors = 0;
  std::uint32_t fec_framing_errors = 0;
  std::uint16_t fec_last_decode_us = 0;
  std::uint16_t fec_max_decode_us = 0;
};

bool receiver_packet_crc_valid(
    const std::uint8_t* packet,
    std::size_t packet_size,
    std::uint16_t* computed_crc = nullptr);
bool decode_receiver_packet_payload(
    const std::uint8_t* packet,
    std::size_t packet_size,
    ReceiverPacketPayload* payload,
    ReceiverPacketDecodeReport* report = nullptr,
    std::uint8_t* scratch = nullptr,
    std::size_t scratch_size = 0);
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
