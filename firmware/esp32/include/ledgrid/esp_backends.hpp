#pragma once

#include <cstddef>
#include <cstdint>
#include <cstdio>

#include "ledgrid/animation_abi.h"
#include "ledgrid/animation_backend.hpp"
#include "ledgrid/asset_verifier.hpp"
#include "ledgrid/frame_track.hpp"
#include "ledgrid/typed_parameters.hpp"

namespace ledgrid {

class SpiffsAssetStore final : public AssetStore {
 public:
  ~SpiffsAssetStore() override;
  bool begin();
  bool ready() const override { return ready_; }
  bool probe(const std::uint8_t digest[32]) const override;
  bool describe(const std::uint8_t digest[32], AssetDescriptor* out) const override;
  bool begin_part(const AssetDescriptor& descriptor) override;
  bool write_part(std::uint32_t offset, const std::uint8_t* data,
                  std::size_t size) override;
  bool read_part(std::uint32_t offset, std::uint8_t* data,
                 std::size_t size) const override;
  OperationResult validate_part(const AssetDescriptor& descriptor) override;
  bool commit_part(const std::uint8_t digest[32]) override;
  void discard_part() override;
  bool remove(const std::uint8_t digest[32]) override;
  std::uint32_t free_bytes() const override;
  std::uint32_t used_bytes() const override;
  bool committed_path(const std::uint8_t digest[32], char* output,
                      std::size_t output_size) const override;
  bool read_committed(const std::uint8_t digest[32], std::uint32_t offset,
                      std::uint8_t* data, std::size_t size) const override;
  void set_active_digest(const std::uint8_t digest[32]) override;

 private:
  bool ensure_space(std::uint32_t required);
  bool write_part_metadata(const AssetDescriptor& descriptor);
  bool metadata_path(const std::uint8_t digest[32], char* output,
                     std::size_t output_size) const;
  bool read_metadata(const char* path, AssetDescriptor* descriptor,
                     std::uint32_t* access) const;
  bool touch(const std::uint8_t digest[32], const AssetDescriptor& descriptor) const;

  mutable std::uint32_t access_counter_ = 1;
  std::uint8_t active_digest_[32] = {};
  AssetDescriptor part_descriptor_{};
  mutable std::FILE* part_file_ = nullptr;
  bool ready_ = false;
};

class MbedtlsAssetVerifier final : public AssetSignatureVerifier {
 public:
  bool begin();
  bool available() const override { return key_ready_ || unsigned_development_; }
  bool unsigned_development() const override { return unsigned_development_; }
  OperationResult verify(const AssetVerificationEnvelope& envelope) const override;

 private:
  std::uint8_t public_key_[65] = {};
  char key_id_[kSigningKeyIdBytes + 1] = {};
  bool key_ready_ = false;
  bool unsigned_development_ = false;
};

class NvsReceiverPersistence final : public ReceiverPersistence {
 public:
  bool begin();
  bool mark_active(const std::uint8_t digest[32]) override;
  void clear_active() override;
  void mark_quarantined(const std::uint8_t digest[32]) override;
  void clear_quarantined(const std::uint8_t digest[32]) override;
  bool active_digest(std::uint8_t digest[32]) const;
  bool quarantined_digest(std::uint8_t digest[32]) const;
  void record_reset_reason(std::uint32_t reason);

 private:
  bool ready_ = false;
};

class EspAnimationBackend final : public AnimationBackend {
 public:
  explicit EspAnimationBackend(AssetStore* store) : store_(store) {}
  ~EspAnimationBackend() override;
  bool begin();
  void set_native_watchdog_ready(bool ready) { native_watchdog_ready_ = ready; }
  std::uint32_t capabilities() const override;
  bool available(AssetKind kind) const override;
  OperationResult start(const AssetDescriptor& descriptor,
                        std::uint16_t global_strip_offset,
                        const std::uint8_t* parameters,
                        std::size_t parameter_size) override;
  void stop() override;
  OperationResult restart() override;
  OperationResult update_parameters(const std::uint8_t* parameters,
                                    std::size_t parameter_size) override;
  bool render(std::uint64_t now_us, std::uint8_t* rgb_output,
              std::size_t rgb_output_size, bool* changed) override;

 private:
  OperationResult load_frame_track(const AssetDescriptor& descriptor);
  OperationResult load_native(const AssetDescriptor& descriptor);
  bool decode_parameters(const std::uint8_t* data, std::size_t size);
  void apply_frame_controls();

  AssetStore* store_ = nullptr;
  AssetDescriptor descriptor_{};
  AssetKind kind_ = AssetKind::None;
  std::uint16_t global_strip_offset_ = 0;
  std::uint64_t started_us_ = 0;
  std::uint32_t frame_index_ = 0;
  std::uint8_t* track_data_ = nullptr;
  FrameTrackDecoder decoder_{};
  FrameTrackPlayer player_{};
  void* module_handle_ = nullptr;
  const ledgrid_animation_callbacks_v1* callbacks_ = nullptr;
  void* module_state_ = nullptr;
  ledgrid_parameter_v1 parameters_[LEDGRID_ANIMATION_MAX_PARAMETERS] = {};
  char parameter_names_[LEDGRID_ANIMATION_MAX_PARAMETERS][64] = {};
  char enum_values_[LEDGRID_ANIMATION_MAX_PARAMETERS][64] = {};
  std::uint8_t parameter_count_ = 0;
  RuntimePlaybackControls runtime_controls_{};
  bool ready_ = false;
  bool native_watchdog_ready_ = false;
};

}  // namespace ledgrid
