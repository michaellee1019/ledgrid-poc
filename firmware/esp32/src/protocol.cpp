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
    bool installation_profiles_enabled) {
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
      if (size != 4 && size != 5 && size != 6) {
        return reject(ReceiverOperationResult::InvalidSize);
      }
      return ReceiverDispatchDecision{ReceiverDispatchRoute::Operational,
                                      ReceiverOperationResult::None, false,
                                      false};
    case ReceiverCommand::StatusQuery:
      if (size == kStatusBytesV3 ||
          (local_background_enabled && size == kStatusBytesV4) ||
          (installation_profiles_enabled && size == kStatusBytesV5)) {
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
  return valid_status_query(command, size, false, false);
}

bool valid_status_query(
    const std::uint8_t* command,
    std::size_t size,
    bool sparse_overlay_enabled) {
  return valid_status_query(
      command, size, sparse_overlay_enabled, false);
}

bool valid_status_query(
    const std::uint8_t* command,
    std::size_t size,
    bool sparse_overlay_enabled,
    bool installation_profiles_enabled) {
  if (command == nullptr ||
      (size != kStatusBytesV3 &&
       !(sparse_overlay_enabled && size == kStatusBytesV4) &&
       !(installation_profiles_enabled && size == kStatusBytesV5)) ||
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
  if (command == nullptr || logical_receiver_id == nullptr ||
      (size != 4 && size != 5 && size != 6) ||
      command[0] != static_cast<std::uint8_t>(ReceiverCommand::Config)) {
    return false;
  }
  if (size == 6 && command[5] > 3) return false;
  *logical_receiver_id = size == 6 ? command[5] : current_id;
  return true;
}

}  // namespace ledgrid
