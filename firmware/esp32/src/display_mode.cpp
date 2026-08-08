#include "ledgrid/display_mode.hpp"

#include <cstring>

namespace ledgrid {

void DisplayModeStateMachine::host_frame_received() {
  mode_ = DisplayMode::HostFrames;
  active_kind_ = AssetKind::None;
  std::memset(active_digest_, 0, sizeof(active_digest_));
  last_result_ = OperationResult::Ok;
}

bool DisplayModeStateMachine::begin_maintenance() {
  if (mode_ == DisplayMode::Maintenance) return true;
  mode_before_maintenance_ = mode_;
  mode_ = DisplayMode::Maintenance;
  return true;
}

void DisplayModeStateMachine::end_maintenance() {
  if (mode_ == DisplayMode::Maintenance) mode_ = mode_before_maintenance_;
}

bool DisplayModeStateMachine::start_firmware(
    const std::uint8_t digest[32], AssetKind kind) {
  if (digest == nullptr || kind == AssetKind::None ||
      (quarantined_ &&
       std::memcmp(digest, quarantined_digest_, sizeof(active_digest_)) == 0)) {
    last_result_ = quarantined_ ? OperationResult::Quarantined
                                : OperationResult::InvalidCommand;
    return false;
  }
  std::memcpy(active_digest_, digest, sizeof(active_digest_));
  active_kind_ = kind;
  mode_ = DisplayMode::FirmwareAnimation;
  last_result_ = OperationResult::Ok;
  return true;
}

void DisplayModeStateMachine::stop_firmware() {
  mode_ = DisplayMode::StartupFallback;
  active_kind_ = AssetKind::None;
  std::memset(active_digest_, 0, sizeof(active_digest_));
  last_result_ = OperationResult::Ok;
}

void DisplayModeStateMachine::render_failed(bool watchdog) {
  if (mode_ == DisplayMode::FirmwareAnimation) {
    std::memcpy(quarantined_digest_, active_digest_, sizeof(active_digest_));
    quarantined_ = true;
  }
  if (watchdog && watchdog_events_ != UINT8_MAX) ++watchdog_events_;
  mode_ = DisplayMode::StartupFallback;
  active_kind_ = AssetKind::None;
  std::memset(active_digest_, 0, sizeof(active_digest_));
  last_result_ = watchdog ? OperationResult::Watchdog
                          : OperationResult::RenderFailed;
}

void DisplayModeStateMachine::mark_reinstalled(const std::uint8_t digest[32]) {
  if (digest != nullptr && quarantined_ &&
      std::memcmp(digest, quarantined_digest_, sizeof(quarantined_digest_)) == 0) {
    quarantined_ = false;
    std::memset(quarantined_digest_, 0, sizeof(quarantined_digest_));
  }
}

void DisplayModeStateMachine::restore_quarantine(
    const std::uint8_t digest[32]) {
  if (digest == nullptr) return;
  std::memcpy(quarantined_digest_, digest, sizeof(quarantined_digest_));
  quarantined_ = true;
  mode_ = DisplayMode::StartupFallback;
  active_kind_ = AssetKind::None;
  std::memset(active_digest_, 0, sizeof(active_digest_));
  last_result_ = OperationResult::Quarantined;
}

}  // namespace ledgrid
