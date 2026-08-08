#include "ledgrid/receiver_control.hpp"

#include <cstring>

#include "ledgrid/typed_parameters.hpp"

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

}  // namespace

ReceiverController::ReceiverController(
    AssetStore* store, std::uint8_t strip_count, std::uint16_t leds_per_strip,
    std::uint8_t logical_device, const AssetSignatureVerifier* verifier,
    AnimationBackend* backend, ReceiverPersistence* persistence)
    : store_(store), verifier_(verifier), upload_(store), strip_count_(strip_count),
      leds_per_strip_(leds_per_strip), logical_device_(logical_device),
      backend_(backend), persistence_(persistence) {}

void ReceiverController::host_frame_received() {
  if (backend_ != nullptr) backend_->stop();
  if (store_ != nullptr) store_->set_active_digest(nullptr);
  if (persistence_ != nullptr) persistence_->clear_active();
  modes_.host_frame_received();
}

void ReceiverController::configure_geometry(
    std::uint8_t strip_count, std::uint16_t leds_per_strip) {
  if (strip_count == strip_count_ && leds_per_strip == leds_per_strip_) return;
  if (modes_.mode() == DisplayMode::FirmwareAnimation) {
    if (backend_ != nullptr) backend_->stop();
    if (store_ != nullptr) store_->set_active_digest(nullptr);
    if (persistence_ != nullptr) persistence_->clear_active();
    modes_.stop_firmware();
  }
  strip_count_ = strip_count;
  leds_per_strip_ = leds_per_strip;
}

OperationResult ReceiverController::finish(OperationResult result) {
  modes_.set_result(result);
  return result;
}

OperationResult ReceiverController::process(
    const std::uint8_t* command, std::size_t size) {
  if (command == nullptr || !valid_spi_transaction_size(size)) {
    return finish(OperationResult::BadSize);
  }
  const auto code = static_cast<Command>(command[0]);
  switch (code) {
    case Command::CapabilitiesQuery:
      return finish(size == 1 ? OperationResult::Ok
                              : OperationResult::InvalidCommand);
    case Command::AssetProbe:
      if (size != 33) return finish(OperationResult::InvalidCommand);
      return finish(upload_.probe(command + 1) ? OperationResult::Ok
                                                : OperationResult::NotFound);
    case Command::AssetBegin: {
      AssetVerificationEnvelope envelope{};
      const OperationResult parsed =
          parse_asset_verification_envelope(command, size, &envelope);
      if (parsed != OperationResult::Ok) return finish(parsed);
      const AssetDescriptor& descriptor = envelope.descriptor;
      if (descriptor.strip_count != strip_count_ ||
          descriptor.leds_per_strip != leds_per_strip_) {
        return finish(OperationResult::WrongGeometry);
      }
      if (descriptor.logical_device != logical_device_) {
        return finish(OperationResult::WrongDevice);
      }
      // A missing trust implementation is never a development-mode bypass.
      if (verifier_ == nullptr || !verifier_->available())
        return finish(OperationResult::Unsupported);
      const OperationResult verified = verifier_->verify(envelope);
      if (verified != OperationResult::Ok) return finish(verified);
      if (backend_ != nullptr && !backend_->available(descriptor.kind))
        return finish(OperationResult::Unsupported);
      modes_.begin_maintenance();
      const OperationResult result = upload_.begin(descriptor);
      if (result != OperationResult::Ok ||
          upload_.state() == UploadState::Committed) modes_.end_maintenance();
      return finish(result);
    }
    case Command::AssetChunk:
      if (size <= 5) return finish(OperationResult::InvalidCommand);
      return finish(upload_.chunk(read_u32(command + 1), command + 5, size - 5));
    case Command::AssetCommit: {
      if (size != 33) return finish(OperationResult::InvalidCommand);
      const OperationResult result = upload_.commit(command + 1);
      if (result == OperationResult::Ok) {
        modes_.mark_reinstalled(command + 1);
        if (persistence_ != nullptr) persistence_->clear_quarantined(command + 1);
      }
      modes_.end_maintenance();
      return finish(result);
    }
    case Command::AssetAbort:
      if (size != 1) return finish(OperationResult::InvalidCommand);
      upload_.abort();
      modes_.end_maintenance();
      return finish(OperationResult::Ok);
    case Command::AssetRemove:
      if (size != 33) return finish(OperationResult::InvalidCommand);
      return finish(upload_.remove(
          command + 1, modes_.active_digest(),
          modes_.mode() == DisplayMode::FirmwareAnimation));
    case Command::AnimationStart: {
      if (size < 37) return finish(OperationResult::InvalidCommand);
      const std::uint16_t blob_size = read_u16(command + 35);
      if (blob_size != size - 37 || blob_size > sizeof(parameters_) ||
          !validate_typed_parameter_blob(command + 37, blob_size)) {
        return finish(OperationResult::BadSize);
      }
      AssetDescriptor descriptor{};
      if (store_ == nullptr || !store_->describe(command + 1, &descriptor)) {
        return finish(OperationResult::NotFound);
      }
      if (descriptor.strip_count != strip_count_ ||
          descriptor.leds_per_strip != leds_per_strip_) {
        return finish(OperationResult::WrongGeometry);
      }
      if (descriptor.logical_device != logical_device_)
        return finish(OperationResult::WrongDevice);
      if (backend_ == nullptr || !backend_->available(descriptor.kind))
        return finish(OperationResult::Unsupported);
      global_strip_offset_ = read_u16(command + 33);
      parameter_size_ = blob_size;
      std::memcpy(parameters_, command + 37, blob_size);
      if (!modes_.start_firmware(descriptor.digest, descriptor.kind))
        return finish(modes_.last_result());
      if (persistence_ != nullptr &&
          !persistence_->mark_active(descriptor.digest)) {
        modes_.render_failed(false);
        return finish(OperationResult::StorageError);
      }
      const OperationResult started = backend_->start(
          descriptor, global_strip_offset_, parameters_, parameter_size_);
      if (started != OperationResult::Ok) {
        if (persistence_ != nullptr) {
          persistence_->mark_quarantined(descriptor.digest);
          persistence_->clear_active();
        }
        modes_.render_failed(false);
        backend_->stop();
        if (store_ != nullptr) store_->set_active_digest(nullptr);
        return finish(started);
      }
      if (store_ != nullptr) store_->set_active_digest(descriptor.digest);
      return finish(OperationResult::Ok);
    }
    case Command::AnimationStop:
      if (size != 1) return finish(OperationResult::InvalidCommand);
      if (backend_ != nullptr) backend_->stop();
      if (store_ != nullptr) store_->set_active_digest(nullptr);
      if (persistence_ != nullptr) persistence_->clear_active();
      modes_.stop_firmware();
      return finish(OperationResult::Ok);
    case Command::AnimationRestart: {
      if (size != 1) return finish(OperationResult::InvalidCommand);
      if (modes_.mode() != DisplayMode::FirmwareAnimation) {
        return finish(OperationResult::InvalidState);
      }
      if (backend_ == nullptr) return finish(OperationResult::Unsupported);
      const OperationResult result = backend_->restart();
      if (result != OperationResult::Ok) {
        std::uint8_t digest[32] = {};
        std::memcpy(digest, modes_.active_digest(), sizeof(digest));
        modes_.render_failed(false);
        if (persistence_ != nullptr) {
          persistence_->mark_quarantined(digest);
          persistence_->clear_active();
        }
        backend_->stop();
        if (store_ != nullptr) store_->set_active_digest(nullptr);
      }
      return finish(result);
    }
    case Command::AnimationParameters: {
      if (size < 3 || modes_.mode() != DisplayMode::FirmwareAnimation) {
        return finish(OperationResult::InvalidState);
      }
      const std::uint16_t blob_size = read_u16(command + 1);
      if (blob_size != size - 3 || blob_size > sizeof(parameters_) ||
          !validate_typed_parameter_blob(command + 3, blob_size)) {
        return finish(OperationResult::BadSize);
      }
      parameter_size_ = blob_size;
      std::memcpy(parameters_, command + 3, blob_size);
      return finish(backend_ == nullptr
                        ? OperationResult::Unsupported
                        : backend_->update_parameters(parameters_, parameter_size_));
    }
    default:
      return finish(OperationResult::Unsupported);
  }
}

void ReceiverController::render_completed(
    bool callback_ok, std::uint32_t duration_us) {
  last_render_us_ = duration_us > UINT16_MAX
                        ? UINT16_MAX
                        : static_cast<std::uint16_t>(duration_us);
  if (last_render_us_ > max_render_us_) max_render_us_ = last_render_us_;
  if (duration_us > kAnimationRenderWatchdogUs) {
    if (missed_deadlines_ != UINT16_MAX) ++missed_deadlines_;
    modes_.render_failed(true);
  } else if (!callback_ok) {
    modes_.render_failed(false);
  }
  if (modes_.mode() == DisplayMode::StartupFallback && modes_.quarantined()) {
    if (persistence_ != nullptr) {
      persistence_->mark_quarantined(modes_.quarantined_digest());
      persistence_->clear_active();
    }
    if (backend_ != nullptr) backend_->stop();
    if (store_ != nullptr) store_->set_active_digest(nullptr);
  }
}

void ReceiverController::populate_status(ReceiverStatus* status) const {
  if (status == nullptr) return;
  status->capabilities = kCapabilityTypedParameters | kCapabilityQuarantine;
  status->capabilities |= kCapabilityLogicalDeviceIdentity |
      (static_cast<std::uint32_t>(logical_device_) <<
       kCapabilityLogicalDeviceShift);
  const bool store_ready = store_ != nullptr && store_->ready();
  const bool verifier_ready = verifier_ != nullptr && verifier_->available();
  if (backend_ != nullptr) status->capabilities |= backend_->capabilities();
  if (verifier_ready && verifier_->unsigned_development())
    status->capabilities |= kCapabilityUnsignedDevelopment;
  if (store_ready && verifier_ready) {
    status->capabilities |= kCapabilityAssetUpload;
    if (!verifier_->unsigned_development())
      status->capabilities |= kCapabilitySignedPackages;
  }
  status->display_mode = modes_.mode();
  status->asset_kind = modes_.active_kind();
  status->upload_state = upload_.state();
  status->last_result = modes_.last_result();
  std::memcpy(status->active_digest, modes_.active_digest(), 32);
  if (store_ready) {
    status->cache_free_bytes = store_->free_bytes();
    status->cache_used_bytes = store_->used_bytes();
  }
  status->upload_received_bytes = upload_.received_bytes();
  status->upload_total_bytes = upload_.total_bytes();
  status->last_render_us = last_render_us_;
  status->max_render_us = max_render_us_;
  status->missed_deadlines = missed_deadlines_;
  status->watchdog_events = modes_.watchdog_events();
  status->quarantine_state = modes_.quarantined() ? 1 : 0;
}

void ReceiverController::restore_quarantine(const std::uint8_t digest[32]) {
  modes_.restore_quarantine(digest);
  if (persistence_ != nullptr) persistence_->mark_quarantined(digest);
  if (store_ != nullptr) store_->set_active_digest(nullptr);
}

}  // namespace ledgrid
