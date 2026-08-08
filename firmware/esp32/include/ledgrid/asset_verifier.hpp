#pragma once

#include <cstddef>
#include <cstdint>

#include "ledgrid/asset_upload.hpp"

namespace ledgrid {

constexpr std::uint8_t kAssetVerificationEnvelopeVersion = 1;
constexpr std::size_t kAssetBeginEnvelopeMaxBytes = 1024;
constexpr std::size_t kSigningKeyIdBytes = 20;
constexpr std::size_t kSignedPackageIndexBytes = 176;
constexpr std::size_t kP256SignatureBytes = 64;
constexpr std::size_t kCanonicalAssetBeginBytes = 313;
constexpr std::uint16_t kAnimationAbiV1 = 1;
constexpr std::uint16_t kEsp32S3ElfLoaderTargetV1 = 1;
constexpr std::uint8_t kReceiverCount = 4;
constexpr std::uint8_t kLocalStrips = 8;
constexpr std::uint16_t kWallStrips = 32;
constexpr std::uint16_t kInstalledLedsPerStrip = 138;

struct AssetVerificationEnvelope {
  AssetDescriptor descriptor{};
  const std::uint8_t* key_id = nullptr;
  std::size_t key_id_size = 0;
  const std::uint8_t* signed_index = nullptr;
  std::size_t signed_index_size = 0;
  const std::uint8_t* signature = nullptr;
  std::size_t signature_size = 0;
};

class AssetSignatureVerifier {
 public:
  virtual ~AssetSignatureVerifier() = default;
  virtual bool available() const { return true; }
  virtual bool unsigned_development() const { return false; }
  // Returns UnknownKey when key_id is not installed and BadSignature for any
  // malformed/non-canonical/altered P-256 signature. Production must not
  // substitute an accept-all verifier.
  virtual OperationResult verify(
      const AssetVerificationEnvelope& envelope) const = 0;
};

// Parses ASSET_BEGIN including its command byte, validates the exact LGIX v1
// layout, and binds descriptor digest/kind/ABI/target/geometry/device to signed
// index fields before returning a view suitable for trust verification.
OperationResult parse_asset_verification_envelope(
    const std::uint8_t* command,
    std::size_t command_size,
    AssetVerificationEnvelope* envelope);

}  // namespace ledgrid
