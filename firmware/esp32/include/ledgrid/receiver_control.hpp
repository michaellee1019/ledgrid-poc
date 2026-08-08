#pragma once

#include <cstddef>
#include <cstdint>

#include "ledgrid/asset_upload.hpp"
#include "ledgrid/asset_verifier.hpp"
#include "ledgrid/animation_backend.hpp"
#include "ledgrid/display_mode.hpp"

namespace ledgrid {

constexpr std::uint32_t kAnimationRenderWatchdogUs = 25000;

class ReceiverController {
 public:
  ReceiverController(
      AssetStore* store, std::uint8_t strip_count, std::uint16_t leds_per_strip,
      std::uint8_t logical_device,
      const AssetSignatureVerifier* verifier = nullptr,
      AnimationBackend* backend = nullptr,
      ReceiverPersistence* persistence = nullptr);

  OperationResult process(const std::uint8_t* command, std::size_t size);
  void host_frame_received();
  void configure_geometry(std::uint8_t strip_count,
                          std::uint16_t leds_per_strip);
  void render_failed(bool watchdog) { modes_.render_failed(watchdog); }
  void render_completed(bool callback_ok, std::uint32_t duration_us);
  void populate_status(ReceiverStatus* status) const;
  void restore_quarantine(const std::uint8_t digest[32]);
  const DisplayModeStateMachine& modes() const { return modes_; }
  const UploadManager& upload() const { return upload_; }

 private:
  OperationResult finish(OperationResult result);
  AssetStore* store_ = nullptr;
  const AssetSignatureVerifier* verifier_ = nullptr;
  AnimationBackend* backend_ = nullptr;
  ReceiverPersistence* persistence_ = nullptr;
  UploadManager upload_;
  DisplayModeStateMachine modes_;
  std::uint8_t strip_count_ = 0;
  std::uint16_t leds_per_strip_ = 0;
  std::uint8_t logical_device_ = 0;
  std::uint8_t parameters_[1024] = {};
  std::uint16_t parameter_size_ = 0;
  std::uint16_t global_strip_offset_ = 0;
  std::uint16_t last_render_us_ = 0;
  std::uint16_t max_render_us_ = 0;
  std::uint16_t missed_deadlines_ = 0;
};

}  // namespace ledgrid
