#pragma once

#include <cstddef>
#include <cstdint>

namespace ledgrid {

constexpr std::uint8_t kStatusProtocolVersion = 3;
constexpr std::size_t kStatusBytes = 128;

// The controller and receiver both cap the complete SPI transaction, not just
// its command body. Every transfer ends in a two-byte CRC-16/CCITT-FALSE.
constexpr std::size_t kMaxSpiTransactionBytes = 4096;
constexpr std::size_t kSpiCrcBytes = 2;
constexpr std::size_t kMaxCommandBytes =
    kMaxSpiTransactionBytes - kSpiCrcBytes;
constexpr std::size_t kAssetChunkHeaderBytes = 5;
constexpr std::size_t kMaxAssetChunkBytes =
    kMaxCommandBytes - kAssetChunkHeaderBytes;

enum class Command : std::uint8_t {
  SetPixel = 0x01,
  SetBrightness = 0x02,
  Show = 0x03,
  Clear = 0x04,
  SetRange = 0x05,
  SetAll = 0x06,
  Config = 0x07,
  CapabilitiesQuery = 0x20,
  AssetProbe = 0x21,
  AssetBegin = 0x22,
  AssetChunk = 0x23,
  AssetCommit = 0x24,
  AssetRemove = 0x25,
  AnimationStart = 0x26,
  AnimationStop = 0x27,
  AnimationRestart = 0x28,
  AnimationParameters = 0x29,
  AssetAbort = 0x2A,
  Ping = 0xFF,
};

enum class DisplayMode : std::uint8_t {
  StartupFallback = 0,
  HostFrames = 1,
  FirmwareAnimation = 2,
  Maintenance = 3,
};

enum class AssetKind : std::uint8_t { None = 0, Native = 1, FrameTrack = 2 };

enum class UploadState : std::uint8_t {
  Idle = 0,
  Receiving = 1,
  Verifying = 2,
  Committed = 3,
  Failed = 4,
};

enum class OperationResult : std::uint8_t {
  None = 0,
  Ok = 1,
  InvalidCommand = 2,
  InvalidState = 3,
  BadSize = 4,
  BadDigest = 5,
  BadSignature = 6,
  WrongAbi = 7,
  WrongGeometry = 8,
  WrongDevice = 9,
  StorageError = 10,
  Unsupported = 11,
  Quarantined = 12,
  RenderFailed = 13,
  Watchdog = 14,
  NotFound = 15,
  UnknownKey = 16,
  WrongTarget = 17,
  BadEnvelope = 18,
};

enum Capability : std::uint32_t {
  kCapabilityNative = 1U << 0U,
  kCapabilityFrameTrack = 1U << 1U,
  kCapabilitySignedPackages = 1U << 2U,
  kCapabilityAssetUpload = 1U << 3U,
  kCapabilityPsramExecution = 1U << 4U,
  kCapabilityUnsignedDevelopment = 1U << 5U,
  kCapabilityTypedParameters = 1U << 6U,
  kCapabilityQuarantine = 1U << 7U,
  // Bits 16-17 carry the provisioned logical receiver index. The validity
  // bit prevents an unprovisioned/older logical-0 image from looking valid.
  kCapabilityLogicalDeviceShift = 16U,
  kCapabilityLogicalDeviceMask = 3U << kCapabilityLogicalDeviceShift,
  kCapabilityLogicalDeviceIdentity = 1U << 18U,
};

struct ReceiverStatus {
  std::uint8_t flags = 0;
  std::uint8_t active_strips = 0;
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
  std::uint32_t capabilities = 0;
  DisplayMode display_mode = DisplayMode::StartupFallback;
  AssetKind asset_kind = AssetKind::None;
  UploadState upload_state = UploadState::Idle;
  OperationResult last_result = OperationResult::None;
  std::uint8_t active_digest[32] = {};
  std::uint32_t cache_free_bytes = 0;
  std::uint32_t cache_used_bytes = 0;
  std::uint32_t upload_received_bytes = 0;
  std::uint32_t upload_total_bytes = 0;
  std::uint16_t last_render_us = 0;
  std::uint16_t max_render_us = 0;
  std::uint16_t missed_deadlines = 0;
  std::uint8_t watchdog_events = 0;
  std::uint8_t quarantine_state = 0;
};

bool encode_receiver_status(
    const ReceiverStatus& status,
    std::uint8_t* output,
    std::size_t output_size);

bool valid_spi_transaction_size(std::size_t command_bytes);
bool command_takes_display_ownership(Command command);

}  // namespace ledgrid
