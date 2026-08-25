#include "ledgrid/protocol.hpp"

#include <algorithm>
#include <cstring>

namespace ledgrid {
namespace {

void write_u16(std::uint8_t* output, std::uint16_t value) {
  output[0] = static_cast<std::uint8_t>(value >> 8);
  output[1] = static_cast<std::uint8_t>(value);
}

void write_u32(std::uint8_t* output, std::uint32_t value) {
  output[0] = static_cast<std::uint8_t>(value >> 24);
  output[1] = static_cast<std::uint8_t>(value >> 16);
  output[2] = static_cast<std::uint8_t>(value >> 8);
  output[3] = static_cast<std::uint8_t>(value);
}

void write_u64(std::uint8_t* output, std::uint64_t value) {
  for (std::size_t index = 0; index < 8; ++index) {
    output[index] = static_cast<std::uint8_t>(value >> (56U - index * 8U));
  }
}

std::uint16_t read_u16(const std::uint8_t* input) {
  return static_cast<std::uint16_t>(
      (static_cast<std::uint16_t>(input[0]) << 8U) | input[1]);
}

void encode_v2_fields(
    const ReceiverStatusV2& status, std::uint8_t* output) {
  output[5] = status.flags;
  output[6] = status.active_strips;
  output[7] = status.lane_mask;
  write_u16(output + 8, status.leds_per_strip);
  write_u16(output + 10, status.queued_transactions);
  write_u32(output + 12, status.packets);
  write_u32(output + 16, status.crc_errors);
  write_u32(output + 20, status.crc_ok_packets);
  write_u32(output + 24, status.frames_accepted);
  write_u32(output + 28, status.frames_displayed);
  write_u32(output + 32, status.frames_superseded);
  write_u32(output + 36, status.publish_drops);
  write_u32(output + 40, status.spi_queue_errors);
  write_u16(output + 44, status.last_crc_us);
  write_u16(output + 46, status.last_copy_us);
  write_u16(output + 48, status.last_encode_us);
  write_u16(output + 50, status.last_show_us);
  write_u32(output + 52, status.last_accepted_sequence);
  write_u32(output + 56, status.last_displayed_sequence);
  write_u32(output + 60, status.display_errors);
}

}  // namespace

bool ReceiverOperationTracker::begin(std::uint8_t command) {
  if (sequence_ == UINT32_MAX) return false;
  ++sequence_;
  last_processed_command_ = command;
  return true;
}

bool encode_receiver_status_v2(
    const ReceiverStatusV2& status,
    std::uint8_t* output,
    std::size_t output_size) {
  if (output == nullptr || output_size < kStatusBytesV2) return false;
  std::memset(output, 0, kStatusBytesV2);
  output[0] = 'L';
  output[1] = 'G';
  output[2] = 'S';
  output[3] = '2';
  output[4] = kStatusProtocolVersion;
  encode_v2_fields(status, output);
  output[64] = status.stagger_phases;
  return true;
}

bool encode_receiver_status_v3(
    const ReceiverStatusV3& status,
    std::uint8_t* output,
    std::size_t output_size) {
  if (output == nullptr || output_size < kStatusBytesV3) return false;
  std::memset(output, 0, kStatusBytesV3);
  std::memcpy(output, "LGS3", 4);
  output[4] = kStatusProtocolVersionV3;
  encode_v2_fields(status, output);
  write_u32(output + 64, status.capabilities);
  output[68] = status.base_mode;
  output[69] = status.foreground_state;
  output[70] = status.maintenance_state;
  output[71] = static_cast<std::uint8_t>(status.transition_reason);
  output[72] = static_cast<std::uint8_t>(status.last_result);
  output[73] = static_cast<std::uint8_t>(status.context_state);
  write_u16(output + 74, status.component_id);
  write_u16(output + 76, status.preferred_cadence_hz);
  write_u16(output + 78, status.luminance_q8_8);
  write_u32(output + 80, status.global_strip_offset);
  write_u32(output + 84, status.common_seed);
  write_u64(output + 88, status.scene_epoch);
  write_u64(output + 96, status.active_context_scene_revision);
  write_u64(output + 104, status.active_vibe_revision);
  write_u64(output + 112, status.active_modifier_revision);
  write_u32(output + 120, status.cadence_deadlines);
  write_u32(output + 124, status.rendered_frames);
  write_u32(output + 128, status.missed_cadence);
  write_u16(output + 132, status.last_render_us);
  write_u16(output + 134, status.max_render_us);
  write_u64(output + 136, status.last_frame_scene_time_us);
  std::memcpy(output + 144, status.active_context_digest, 32);
  std::memcpy(output + 176, status.active_vibe_digest, 32);
  std::memcpy(output + 208, status.active_modifier_digest, 32);
  write_u64(output + 240, status.staged_context_scene_revision);
  std::memcpy(output + 248, status.staged_context_digest, 32);
  std::memcpy(output + 280, status.active_controller_session, 16);
  std::memcpy(output + 296, status.staged_controller_session, 16);
  output[312] = status.logical_receiver_id;
  output[313] = status.last_processed_command;
  output[314] = status.stagger_phases;
  write_u32(output + 316, status.operation_sequence);
  return true;
}

bool encode_receiver_status_v4(
    const ReceiverStatusV4& status,
    std::uint8_t* output,
    std::size_t output_size) {
  if (output == nullptr || output_size < kStatusBytesV4) return false;
  if (!encode_receiver_status_v3(status, output, output_size)) return false;
  std::memcpy(output, "LGS4", 4);
  output[4] = kStatusProtocolVersionV4;
  output[320] = static_cast<std::uint8_t>(status.overlay_result);
  output[321] = static_cast<std::uint8_t>(status.overlay_update_kind);
  write_u16(output + 322, status.overlay_expected_patches);
  write_u16(output + 324, status.overlay_accepted_patches);
  write_u16(output + 326, status.overlay_committed_coverage_pixels);
  write_u64(output + 328, status.overlay_committed_generation);
  write_u64(output + 336, status.overlay_staged_generation);
  write_u64(output + 344, status.foreground_scene_revision);
  write_u64(output + 352, status.foreground_scene_epoch);
  write_u64(output + 360, status.foreground_base_revision);
  write_u64(output + 368, status.foreground_present_at_scene_time_us);
  write_u32(output + 376, status.overlay_lease_ms);
  write_u32(output + 380, status.overlay_lease_remaining_ms);
  std::memcpy(output + 384, status.overlay_session, kControllerSessionBytes);
  write_u32(output + 400, status.overlay_composite_frames);
  write_u16(output + 404, status.overlay_last_composite_us);
  write_u16(output + 406, status.overlay_max_composite_us);
  write_u32(output + 408, status.overlay_commits);
  write_u32(output + 412, status.overlay_expirations);
  return true;
}

bool encode_receiver_status_v5(
    const ReceiverStatusV5& status,
    std::uint8_t* output,
    std::size_t output_size) {
  if (output == nullptr || output_size < kStatusBytesV5) return false;
  if (!encode_receiver_status_v4(status, output, output_size)) return false;
  std::memcpy(output, "LGS5", 4);
  output[4] = kStatusProtocolVersionV5;
  const auto& profile = status.installation_profile;
  output[416] = static_cast<std::uint8_t>(profile.result);
  output[417] = static_cast<std::uint8_t>(profile.transfer_state);
  output[418] = profile.decoder_error;
  output[419] = profile.flags;
  write_u32(output + 420, profile.capacity_bytes);
  write_u32(output + 424, profile.used_bytes);
  write_u32(output + 428, profile.free_bytes);
  write_u32(output + 432, profile.reserve_bytes);
  write_u32(output + 436, profile.reclaimable_bytes);
  write_u32(output + 440, profile.received_bytes);
  write_u32(output + 444, profile.total_bytes);
  write_u64(output + 448, profile.state_generation);
  write_u64(output + 456, profile.preflight_token);
  std::memcpy(output + 464, profile.last_probe_payload_digest, 32);
  std::memcpy(output + 496, profile.transfer_global_id, 32);
  std::memcpy(output + 528, profile.transfer_payload_digest, 32);
  std::memcpy(output + 560, profile.active_global_id, 32);
  std::memcpy(output + 592, profile.active_payload_digest, 32);
  std::memcpy(output + 624, profile.staged_global_id, 32);
  std::memcpy(output + 656, profile.staged_payload_digest, 32);
  std::memcpy(output + 688, profile.rollback_global_id, 32);
  std::memcpy(output + 720, profile.rollback_payload_digest, 32);
  write_u32(output + 752, profile.writes);
  write_u32(output + 756, profile.evictions);
  write_u16(output + 760, profile.stages);
  write_u16(output + 762, profile.verifies);
  write_u16(output + 764, profile.activations);
  write_u16(output + 766, profile.restores);
  return true;
}

bool encode_receiver_status_v6(
    const ReceiverStatusV6& status,
    std::uint8_t* output,
    std::size_t output_size) {
  if (output == nullptr || output_size < kStatusBytesV6) return false;
  if (!encode_receiver_status_v5(status, output, output_size)) return false;
  std::memcpy(output, "LGS6", 4);
  output[4] = kStatusProtocolVersionV6;
  const auto& native = status.native_module;
  output[768] = static_cast<std::uint8_t>(native.result);
  output[769] = static_cast<std::uint8_t>(native.transfer_state);
  output[770] = static_cast<std::uint8_t>(native.watchdog_phase);
  output[771] = native.flags;
  write_u32(output + 772, native.capacity_bytes);
  write_u32(output + 776, native.used_bytes);
  write_u32(output + 780, native.free_bytes);
  write_u32(output + 784, native.reserve_bytes);
  write_u32(output + 788, native.reclaimable_bytes);
  write_u32(output + 792, native.received_bytes);
  write_u32(output + 796, native.total_bytes);
  write_u64(output + 800, native.state_generation);
  write_u64(output + 808, native.preflight_token);
  std::memcpy(output + 816, native.last_probe_payload_digest, 32);
  std::memcpy(output + 848, native.transfer_bundle_digest, 32);
  std::memcpy(output + 880, native.transfer_payload_digest, 32);
  std::memcpy(output + 912, native.active_bundle_digest, 32);
  std::memcpy(output + 944, native.active_payload_digest, 32);
  std::memcpy(output + 976, native.staged_bundle_digest, 32);
  std::memcpy(output + 1008, native.staged_payload_digest, 32);
  std::memcpy(output + 1040, native.rollback_bundle_digest, 32);
  std::memcpy(output + 1072, native.rollback_payload_digest, 32);
  std::memcpy(output + 1104, native.quarantine_payload_digest, 32);
  write_u32(output + 1136, native.active_parameter_schema_revision);
  write_u16(output + 1140, native.active_cadence_hz);
  output[1142] = native.active_local_strips;
  output[1143] = native.active_target;
  write_u16(output + 1144, native.active_global_strips);
  write_u16(output + 1146, native.active_leds_per_strip);
  write_u16(output + 1148, native.active_global_strip_offset);
  write_u16(output + 1150, native.active_parameter_size);
  std::memcpy(output + 1152, native.active_parameter_digest, 32);
  write_u16(output + 1184, native.last_load_us);
  write_u16(output + 1186, native.last_initialize_us);
  write_u16(output + 1188, native.last_context_us);
  write_u16(output + 1190, native.last_render_us);
  write_u16(output + 1192, native.max_phase_us);
  write_u16(output + 1194, native.watchdog_events);
  write_u32(output + 1196, native.writes);
  write_u32(output + 1200, native.evictions);
  write_u16(output + 1204, native.stages);
  write_u16(output + 1206, native.verifies);
  write_u16(output + 1208, native.activations);
  write_u16(output + 1210, native.restores);
  write_u16(output + 1212, native.quarantines);
  return true;
}

bool command_may_claim_base(ReceiverCommand command) {
  return command == ReceiverCommand::SetAll ||
         command == ReceiverCommand::LocalBackgroundStart;
}

ReceiverDispatchDecision classify_receiver_dispatch(
    const std::uint8_t* command,
    std::size_t size,
    std::size_t active_rgb_bytes,
    BaseMode base_mode,
    bool local_background_enabled,
    bool installation_profiles_enabled,
    bool receiver_native_modules_enabled) {
  const auto reject = [](ReceiverOperationResult result) {
    return ReceiverDispatchDecision{
        ReceiverDispatchRoute::Reject, result, false, false};
  };
  if (command == nullptr || size == 0) {
    return reject(ReceiverOperationResult::InvalidSize);
  }
  const ReceiverCommand id = static_cast<ReceiverCommand>(command[0]);
  const auto exact = [&](std::size_t expected, ReceiverDispatchRoute route,
                         bool publishes, bool claims) {
    if (size != expected) {
      return reject(ReceiverOperationResult::InvalidSize);
    }
    return ReceiverDispatchDecision{
        route, ReceiverOperationResult::None, publishes, claims};
  };

  switch (id) {
    case ReceiverCommand::Ping:
      return exact(1, ReceiverDispatchRoute::Operational, false, false);
    case ReceiverCommand::SetPixel:
      return exact(6, ReceiverDispatchRoute::Operational, false, false);
    case ReceiverCommand::SetBrightness:
      return exact(2, ReceiverDispatchRoute::Operational,
                   base_mode == BaseMode::HostFullScene, false);
    case ReceiverCommand::Show:
    case ReceiverCommand::Clear:
      return exact(1, ReceiverDispatchRoute::Operational,
                   base_mode == BaseMode::HostFullScene, false);
    case ReceiverCommand::SetRange: {
      if (size < 4 || active_rgb_bytes % 3U != 0) {
        return reject(ReceiverOperationResult::InvalidSize);
      }
      const std::size_t pixels = active_rgb_bytes / 3U;
      const std::size_t start =
          (static_cast<std::size_t>(command[1]) << 8U) | command[2];
      if (start >= pixels) {
        return reject(ReceiverOperationResult::InvalidCommand);
      }
      const std::size_t count =
          std::min<std::size_t>(command[3], pixels - start);
      return exact(4U + count * 3U, ReceiverDispatchRoute::Operational,
                   false, false);
    }
    case ReceiverCommand::SetAll:
      return exact(1U + active_rgb_bytes,
                   ReceiverDispatchRoute::HostFullFrame, true, true);
    case ReceiverCommand::Config:
      if (size != 4 && size != 5 && size != 6 && size != 8) {
        return reject(ReceiverOperationResult::InvalidSize);
      }
      return ReceiverDispatchDecision{ReceiverDispatchRoute::Operational,
                                      ReceiverOperationResult::None, false,
                                      false};
    case ReceiverCommand::StatusQuery:
      if (size == kStatusBytesV3 ||
          (local_background_enabled && size == kStatusBytesV4) ||
          (installation_profiles_enabled && size == kStatusBytesV5) ||
          (receiver_native_modules_enabled && size == kStatusBytesV6)) {
        return ReceiverDispatchDecision{ReceiverDispatchRoute::StatusQuery,
                                        ReceiverOperationResult::None, false,
                                        false};
      }
      return reject(ReceiverOperationResult::InvalidSize);
    case ReceiverCommand::SetLaneMask:
    case ReceiverCommand::SetStagger:
      return exact(2, ReceiverDispatchRoute::Operational, false, false);
    case ReceiverCommand::InstallationProfilePreflight:
    case ReceiverCommand::InstallationProfileBegin:
    case ReceiverCommand::InstallationProfileChunk:
    case ReceiverCommand::InstallationProfileFinalize:
    case ReceiverCommand::InstallationProfileVerify:
    case ReceiverCommand::InstallationProfileActivate:
    case ReceiverCommand::InstallationProfileRestore:
    case ReceiverCommand::InstallationProfileAbort:
      if (!installation_profiles_enabled) {
        return reject(ReceiverOperationResult::Unsupported);
      }
      break;
    case ReceiverCommand::NativeModuleProbe:
    case ReceiverCommand::NativeModulePreflight:
    case ReceiverCommand::NativeModuleBegin:
    case ReceiverCommand::NativeModuleChunk:
    case ReceiverCommand::NativeModuleFinalize:
    case ReceiverCommand::NativeModuleVerify:
    case ReceiverCommand::NativeModuleActivate:
    case ReceiverCommand::NativeModuleStop:
    case ReceiverCommand::NativeModuleParameters:
    case ReceiverCommand::NativeModuleRemove:
    case ReceiverCommand::NativeModuleAbort:
    case ReceiverCommand::NativeModuleRestore:
    case ReceiverCommand::NativeModuleQuarantineClear:
      if (!receiver_native_modules_enabled) {
        return reject(ReceiverOperationResult::Unsupported);
      }
      break;
    case ReceiverCommand::LocalBackgroundStart:
    case ReceiverCommand::LocalBackgroundStop:
    case ReceiverCommand::LocalBackgroundParameters:
    case ReceiverCommand::PresentationContextBegin:
    case ReceiverCommand::PresentationContextSet:
    case ReceiverCommand::PresentationContextCommit:
    case ReceiverCommand::ControllerSessionBegin:
    case ReceiverCommand::OverlayBegin:
    case ReceiverCommand::OverlayPatch:
    case ReceiverCommand::OverlayCommit:
    case ReceiverCommand::OverlayClear:
    case ReceiverCommand::OverlayRenew:
    case ReceiverCommand::OverlayPatchBatch:
      break;
    default:
      return reject(ReceiverOperationResult::InvalidCommand);
  }

  if (id >= ReceiverCommand::InstallationProfilePreflight &&
      id <= ReceiverCommand::InstallationProfileAbort) {
    std::size_t expected = 0;
    switch (id) {
      case ReceiverCommand::InstallationProfilePreflight:
        expected = kInstallationProfilePreflightBytes; break;
      case ReceiverCommand::InstallationProfileBegin:
        expected = kInstallationProfileBeginBytes; break;
      case ReceiverCommand::InstallationProfileChunk:
        if (size < 6 ||
            size > kAnimationPipelineMaxTransactionBytes -
                       kAnimationPipelineCrcBytes) {
          return reject(ReceiverOperationResult::InvalidSize);
        }
        expected = size;
        break;
      case ReceiverCommand::InstallationProfileFinalize:
        expected = kInstallationProfileFinalizeBytes; break;
      case ReceiverCommand::InstallationProfileVerify:
        expected = kInstallationProfileVerifyBytes; break;
      case ReceiverCommand::InstallationProfileActivate:
        expected = kInstallationProfileActivateBytes; break;
      case ReceiverCommand::InstallationProfileRestore:
        expected = kInstallationProfileRestoreBytes; break;
      case ReceiverCommand::InstallationProfileAbort:
        expected = kInstallationProfileAbortBytes; break;
      default: break;
    }
    return exact(expected, ReceiverDispatchRoute::InstallationProfile,
                 false, false);
  }

  if (id >= ReceiverCommand::NativeModuleProbe &&
      id <= ReceiverCommand::NativeModuleQuarantineClear) {
    std::size_t expected = 0;
    switch (id) {
      case ReceiverCommand::NativeModuleProbe:
        expected = kNativeModuleProbeBytes; break;
      case ReceiverCommand::NativeModulePreflight:
        expected = kNativeModulePreflightBytes; break;
      case ReceiverCommand::NativeModuleBegin:
        expected = kNativeModuleBeginBytes; break;
      case ReceiverCommand::NativeModuleChunk:
        if (size < 6 || size > kAnimationPipelineMaxTransactionBytes -
                                   kAnimationPipelineCrcBytes) {
          return reject(ReceiverOperationResult::InvalidSize);
        }
        expected = size;
        break;
      case ReceiverCommand::NativeModuleFinalize:
        expected = kNativeModuleFinalizeBytes; break;
      case ReceiverCommand::NativeModuleVerify:
        expected = kNativeModuleVerifyBytes; break;
      case ReceiverCommand::NativeModuleActivate:
        if (size < kNativeModuleActivateHeaderBytes ||
            size > kNativeModuleActivateHeaderBytes +
                       kNativeModuleMaxParameterBytes) {
          return reject(ReceiverOperationResult::InvalidSize);
        }
        expected = kNativeModuleActivateHeaderBytes +
            static_cast<std::size_t>(read_u16(command + 85));
        break;
      case ReceiverCommand::NativeModuleStop:
        expected = kNativeModuleStopBytes; break;
      case ReceiverCommand::NativeModuleParameters:
        if (size < kNativeModuleParametersHeaderBytes ||
            size > kNativeModuleParametersHeaderBytes +
                       kNativeModuleMaxParameterBytes) {
          return reject(ReceiverOperationResult::InvalidSize);
        }
        expected = kNativeModuleParametersHeaderBytes +
            static_cast<std::size_t>(read_u16(command + 69));
        break;
      case ReceiverCommand::NativeModuleRemove:
        expected = kNativeModuleRemoveBytes; break;
      case ReceiverCommand::NativeModuleAbort:
        expected = kNativeModuleAbortBytes; break;
      case ReceiverCommand::NativeModuleRestore:
        expected = kNativeModuleRestoreBytes; break;
      case ReceiverCommand::NativeModuleQuarantineClear:
        expected = kNativeModuleQuarantineClearBytes; break;
      default: break;
    }
    return exact(expected, ReceiverDispatchRoute::NativeModule, false, false);
  }

  if (!local_background_enabled) {
    return reject(ReceiverOperationResult::Unsupported);
  }
  std::size_t expected = 0;
  switch (id) {
    case ReceiverCommand::LocalBackgroundStart: expected = 21; break;
    case ReceiverCommand::LocalBackgroundStop: expected = 1; break;
    case ReceiverCommand::LocalBackgroundParameters: expected = 11; break;
    case ReceiverCommand::PresentationContextBegin: expected = 58; break;
    case ReceiverCommand::PresentationContextCommit: expected = 74; break;
    case ReceiverCommand::PresentationContextSet:
      if (size < 145 || size > 187) {
        return reject(ReceiverOperationResult::InvalidSize);
      }
      expected = 145U + static_cast<std::size_t>(command[144]) * 3U;
      break;
    case ReceiverCommand::ControllerSessionBegin:
      expected = kControllerSessionBeginHeaderBytes;
      break;
    case ReceiverCommand::OverlayBegin:
      expected = kOverlayBeginHeaderBytes;
      break;
    case ReceiverCommand::OverlayPatch:
      if (size < kOverlayPatchHeaderBytes) {
        return reject(ReceiverOperationResult::InvalidSize);
      }
      expected = kOverlayPatchHeaderBytes +
          static_cast<std::size_t>(
              (static_cast<std::uint16_t>(command[28]) << 8U) | command[29]) *
              kPremultipliedRgbaBytesPerPixel;
      break;
    case ReceiverCommand::OverlayCommit:
      expected = kOverlayCommitHeaderBytes;
      break;
    case ReceiverCommand::OverlayClear:
      expected = kOverlayClearHeaderBytes;
      break;
    case ReceiverCommand::OverlayRenew:
      expected = kOverlayRenewHeaderBytes;
      break;
    case ReceiverCommand::OverlayPatchBatch: {
      // The runtime owns detailed span/result validation so every CRC-valid,
      // bounded batch reports the exact OverlayOperationResult in status-v4.
      // It validates the whole packet before mutating its staging plane.
      if (size < kOverlayPatchBatchHeaderBytes ||
          size > kAnimationPipelineMaxTransactionBytes -
                     kAnimationPipelineCrcBytes) {
        return reject(ReceiverOperationResult::InvalidSize);
      }
      expected = size;
      break;
    }
    default: break;
  }
  return exact(expected, ReceiverDispatchRoute::Runtime, false,
               id == ReceiverCommand::LocalBackgroundStart);
}

bool receiver_packet_crc_valid(
    const std::uint8_t* packet,
    std::size_t packet_size,
    std::uint16_t* computed_crc) {
  if (packet == nullptr || packet_size < 1U + kAnimationPipelineCrcBytes ||
      packet_size > kAnimationPipelineMaxTransactionBytes) {
    return false;
  }
  const std::size_t payload_size =
      packet_size - kAnimationPipelineCrcBytes;
  const std::uint16_t calculated =
      animation_pipeline_crc16_ccitt(packet, payload_size);
  if (computed_crc != nullptr) *computed_crc = calculated;
  const std::uint16_t received = static_cast<std::uint16_t>(
      (static_cast<std::uint16_t>(packet[payload_size]) << 8U) |
      packet[payload_size + 1U]);
  return received == calculated;
}

bool valid_status_query(const std::uint8_t* command, std::size_t size) {
  return valid_status_query(command, size, false, false, false);
}

bool valid_status_query(
    const std::uint8_t* command,
    std::size_t size,
    bool sparse_overlay_enabled) {
  return valid_status_query(
      command, size, sparse_overlay_enabled, false, false);
}

bool valid_status_query(
    const std::uint8_t* command,
    std::size_t size,
    bool sparse_overlay_enabled,
    bool installation_profiles_enabled) {
  return valid_status_query(command, size, sparse_overlay_enabled,
                            installation_profiles_enabled, false);
}

bool valid_status_query(
    const std::uint8_t* command,
    std::size_t size,
    bool sparse_overlay_enabled,
    bool installation_profiles_enabled,
    bool receiver_native_modules_enabled) {
  if (command == nullptr ||
      (size != kStatusBytesV3 &&
       !(sparse_overlay_enabled && size == kStatusBytesV4) &&
       !(installation_profiles_enabled && size == kStatusBytesV5) &&
       !(receiver_native_modules_enabled && size == kStatusBytesV6)) ||
      command[0] != static_cast<std::uint8_t>(ReceiverCommand::StatusQuery)) {
    return false;
  }
  for (std::size_t index = 1; index < size; ++index) {
    if (command[index] != 0) return false;
  }
  return true;
}

bool parse_logical_receiver_id(
    const std::uint8_t* command,
    std::size_t size,
    std::uint8_t current_id,
    std::uint8_t* logical_receiver_id) {
  std::uint16_t ignored_offset = 0;
  return parse_receiver_topology(command, size, current_id, 0,
                                 logical_receiver_id, &ignored_offset);
}

bool parse_receiver_topology(
    const std::uint8_t* command,
    std::size_t size,
    std::uint8_t current_id,
    std::uint16_t current_global_offset,
    std::uint8_t* logical_receiver_id,
    std::uint16_t* global_strip_offset) {
  if (command == nullptr || logical_receiver_id == nullptr ||
      global_strip_offset == nullptr ||
      (size != 4 && size != 5 && size != 6 && size != 8) ||
      command[0] != static_cast<std::uint8_t>(ReceiverCommand::Config)) {
    return false;
  }
  if (size == 6 && command[5] > 3) return false;
  if (size == 8 && command[5] == 0xFF) return false;
  *logical_receiver_id = size >= 6 ? command[5] : current_id;
  *global_strip_offset = size == 8 ? read_u16(command + 6)
                                    : current_global_offset;
  return true;
}

}  // namespace ledgrid
