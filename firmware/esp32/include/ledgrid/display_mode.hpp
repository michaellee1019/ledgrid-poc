#pragma once

#include <cstddef>
#include <cstdint>

#include "ledgrid/protocol.hpp"

namespace ledgrid {

class DisplayModeStateMachine {
 public:
  DisplayMode mode() const { return mode_; }
  AssetKind active_kind() const { return active_kind_; }
  const std::uint8_t* active_digest() const { return active_digest_; }
  bool quarantined() const { return quarantined_; }
  const std::uint8_t* quarantined_digest() const { return quarantined_digest_; }
  std::uint8_t watchdog_events() const { return watchdog_events_; }
  OperationResult last_result() const { return last_result_; }

  void host_frame_received();
  bool begin_maintenance();
  void end_maintenance();
  bool start_firmware(const std::uint8_t digest[32], AssetKind kind);
  void stop_firmware();
  void render_failed(bool watchdog);
  void mark_reinstalled(const std::uint8_t digest[32]);
  void restore_quarantine(const std::uint8_t digest[32]);
  void set_result(OperationResult result) { last_result_ = result; }

 private:
  DisplayMode mode_ = DisplayMode::StartupFallback;
  DisplayMode mode_before_maintenance_ = DisplayMode::StartupFallback;
  AssetKind active_kind_ = AssetKind::None;
  std::uint8_t active_digest_[32] = {};
  std::uint8_t quarantined_digest_[32] = {};
  bool quarantined_ = false;
  std::uint8_t watchdog_events_ = 0;
  OperationResult last_result_ = OperationResult::None;
};

}  // namespace ledgrid
