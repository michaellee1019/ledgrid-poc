#include "ledgrid/asset_verifier.hpp"

#include <cstring>

namespace ledgrid {
namespace {

std::uint16_t read_u16(const std::uint8_t* value) {
  return static_cast<std::uint16_t>(
      (static_cast<std::uint16_t>(value[0]) << 8U) | value[1]);
}

std::uint32_t read_u32(const std::uint8_t* value) {
  return (static_cast<std::uint32_t>(value[0]) << 24U) |
         (static_cast<std::uint32_t>(value[1]) << 16U) |
         (static_cast<std::uint32_t>(value[2]) << 8U) | value[3];
}

bool valid_key_id(const std::uint8_t* key_id, std::size_t size) {
  if (size != kSigningKeyIdBytes || std::memcmp(key_id, "key-", 4) != 0)
    return false;
  for (std::size_t i = 4; i < size; ++i) {
    if (!((key_id[i] >= '0' && key_id[i] <= '9') ||
          (key_id[i] >= 'a' && key_id[i] <= 'f')))
      return false;
  }
  return true;
}

}  // namespace

OperationResult parse_asset_verification_envelope(
    const std::uint8_t* command,
    std::size_t command_size,
    AssetVerificationEnvelope* envelope) {
  if (command == nullptr || envelope == nullptr || command_size < 50 ||
      command_size > kAssetBeginEnvelopeMaxBytes ||
      command[0] != static_cast<std::uint8_t>(Command::AssetBegin))
    return OperationResult::BadSize;
  if (command[1] != kAssetVerificationEnvelopeVersion)
    return OperationResult::BadEnvelope;
  if (read_u16(command + 2) != command_size - 4U)
    return OperationResult::BadSize;

  AssetVerificationEnvelope parsed{};
  parsed.descriptor.total_size = read_u32(command + 4);
  std::memcpy(parsed.descriptor.digest, command + 8, 32);
  parsed.descriptor.kind = static_cast<AssetKind>(command[40]);
  parsed.descriptor.abi = read_u16(command + 41);
  parsed.descriptor.target = read_u16(command + 43);
  parsed.descriptor.strip_count = command[45];
  parsed.descriptor.leds_per_strip = read_u16(command + 46);
  parsed.descriptor.logical_device = command[48];

  std::size_t cursor = 49;
  const std::size_t key_size = command[cursor++];
  if (key_size != kSigningKeyIdBytes || key_size > command_size - cursor)
    return OperationResult::BadEnvelope;
  parsed.key_id = command + cursor;
  parsed.key_id_size = key_size;
  if (!valid_key_id(parsed.key_id, parsed.key_id_size))
    return OperationResult::UnknownKey;
  cursor += key_size;
  if (command_size - cursor < 2) return OperationResult::BadEnvelope;
  const std::size_t index_size = read_u16(command + cursor);
  cursor += 2;
  if (index_size != kSignedPackageIndexBytes ||
      index_size > command_size - cursor)
    return OperationResult::BadEnvelope;
  parsed.signed_index = command + cursor;
  parsed.signed_index_size = index_size;
  cursor += index_size;
  if (cursor >= command_size) return OperationResult::BadEnvelope;
  const std::size_t signature_size = command[cursor++];
  if (signature_size != kP256SignatureBytes ||
      signature_size > command_size - cursor ||
      cursor + signature_size != command_size)
    return OperationResult::BadEnvelope;
  parsed.signature = command + cursor;
  parsed.signature_size = signature_size;

  const std::uint8_t* index = parsed.signed_index;
  if (std::memcmp(index, "LGIX", 4) != 0 || index[4] != 1)
    return OperationResult::BadEnvelope;
  const AssetKind signed_kind = static_cast<AssetKind>(index[5]);
  const std::uint16_t signed_abi = read_u16(index + 6);
  const std::uint16_t signed_target = read_u16(index + 8);
  const std::uint8_t receivers = index[10];
  const std::uint8_t local_strips = index[11];
  const std::uint16_t wall_strips = read_u16(index + 12);
  const std::uint16_t leds_per_strip = read_u16(index + 14);
  if (signed_kind != AssetKind::Native &&
      signed_kind != AssetKind::FrameTrack)
    return OperationResult::BadEnvelope;
  if (parsed.descriptor.kind != signed_kind)
    return OperationResult::BadEnvelope;
  if (signed_abi != kAnimationAbiV1 ||
      parsed.descriptor.abi != signed_abi)
    return OperationResult::WrongAbi;
  if (signed_target != kEsp32S3ElfLoaderTargetV1 ||
      parsed.descriptor.target != signed_target)
    return OperationResult::WrongTarget;
  if (receivers != kReceiverCount || local_strips != kLocalStrips ||
      wall_strips != kWallStrips ||
      parsed.descriptor.strip_count != local_strips ||
      parsed.descriptor.leds_per_strip != leds_per_strip ||
      leds_per_strip != kInstalledLedsPerStrip)
    return OperationResult::WrongGeometry;
  if (parsed.descriptor.logical_device >= receivers)
    return OperationResult::WrongDevice;
  const std::uint8_t* selected_digest =
      index + 48U + static_cast<std::size_t>(parsed.descriptor.logical_device) * 32U;
  if (std::memcmp(parsed.descriptor.digest, selected_digest, 32) != 0)
    return OperationResult::BadDigest;
  *envelope = parsed;
  return OperationResult::Ok;
}

}  // namespace ledgrid
