#include "ledgrid/protocol.hpp"

#include <algorithm>
#include <array>
#include <cstring>
#include <iterator>

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

bool is_power_of_two(std::uint16_t value) {
  return value != 0U && (value & (value - 1U)) == 0U;
}

bool fec_data_bit_index(std::uint16_t position, std::size_t* data_bit) {
  if (data_bit == nullptr || position == 0U || is_power_of_two(position)) {
    return false;
  }
  std::size_t index = 0;
  for (std::uint16_t candidate = 1; candidate <= position; ++candidate) {
    if (is_power_of_two(candidate)) continue;
    if (candidate == position) {
      *data_bit = index;
      return index < kFecV2DataBytes * 8U;
    }
    ++index;
  }
  return false;
}

struct FecGfTables {
  std::array<std::uint8_t, 510> exponent{};
  std::array<std::uint8_t, 256> logarithm{};
};

constexpr FecGfTables make_fec_gf_tables() {
  FecGfTables tables{};
  std::uint16_t value = 1U;
  for (std::size_t exponent = 0; exponent < 255U; ++exponent) {
    tables.exponent[exponent] = static_cast<std::uint8_t>(value);
    tables.logarithm[value] = static_cast<std::uint8_t>(exponent);
    value <<= 1U;
    if ((value & 0x100U) != 0U) value ^= 0x11DU;
  }
  for (std::size_t exponent = 255U;
       exponent < tables.exponent.size(); ++exponent) {
    tables.exponent[exponent] = tables.exponent[exponent - 255U];
  }
  return tables;
}

constexpr FecGfTables kFecGfTables = make_fec_gf_tables();

constexpr std::uint8_t fec_gf_multiply(
    std::uint8_t left, std::uint8_t right) {
  if (left == 0U || right == 0U) return 0U;
  return kFecGfTables.exponent[
      static_cast<std::size_t>(kFecGfTables.logarithm[left]) +
      kFecGfTables.logarithm[right]];
}

constexpr std::uint8_t fec_gf_power(
    std::uint8_t value, std::uint8_t exponent) {
  if (exponent == 0U) return 1U;
  if (value == 0U) return 0U;
  return kFecGfTables.exponent[
      (static_cast<std::size_t>(kFecGfTables.logarithm[value]) * exponent) %
      255U];
}

constexpr std::uint8_t fec_gf_inverse(std::uint8_t value) {
  return value == 0U ? 0U : kFecGfTables.exponent[
      255U - kFecGfTables.logarithm[value]];
}

constexpr std::size_t kFecV5BurstSpanSymbols = kFecParityBytes - 1U;
constexpr std::size_t kFecV5BurstSpanStarts =
    kFecCodewordBytes - kFecV5BurstSpanSymbols + 1U;
constexpr std::size_t kFecV5MaximumBurstSpanSymbols = kFecParityBytes;
constexpr std::size_t kFecV5MaximumBurstSpanStarts =
    kFecCodewordBytes - kFecV5MaximumBurstSpanSymbols + 1U;
constexpr std::size_t kFecV5ConstantBurstMinimumSymbols =
    kFecV5MaximumBurstSpanSymbols + 1U;

template <std::size_t Span>
using FecV5BurstInverse =
    std::array<std::array<std::uint8_t, Span>, Span>;

template <std::size_t Span, std::size_t First, std::size_t Count>
constexpr std::array<FecV5BurstInverse<Span>, Count>
make_fec_v5_burst_inverses() {
  std::array<FecV5BurstInverse<Span>, Count> output{};
  for (std::size_t local = 0; local < Count; ++local) {
    const std::size_t first = First + local;
    std::array<
        std::array<std::uint8_t, 2U * Span>, Span> augmented{};
    for (std::size_t row = 0; row < Span; ++row) {
      for (std::size_t column = 0; column < Span; ++column) {
        augmented[row][column] = fec_gf_power(
            static_cast<std::uint8_t>(first + column + 1U),
            static_cast<std::uint8_t>(row));
      }
      augmented[row][Span + row] = 1U;
    }
    for (std::size_t pivot = 0; pivot < Span; ++pivot) {
      std::size_t pivot_row = pivot;
      while (pivot_row < Span &&
             augmented[pivot_row][pivot] == 0U) {
        ++pivot_row;
      }
      if (pivot_row != pivot) {
        const auto saved = augmented[pivot];
        augmented[pivot] = augmented[pivot_row];
        augmented[pivot_row] = saved;
      }
      const std::uint8_t inverse =
          fec_gf_inverse(augmented[pivot][pivot]);
      for (std::size_t column = 0; column < 2U * Span; ++column) {
        augmented[pivot][column] =
            fec_gf_multiply(augmented[pivot][column], inverse);
      }
      for (std::size_t row = 0; row < Span; ++row) {
        if (row == pivot || augmented[row][pivot] == 0U) continue;
        const std::uint8_t scale = augmented[row][pivot];
        for (std::size_t column = 0; column < 2U * Span; ++column) {
          augmented[row][column] ^=
              fec_gf_multiply(scale, augmented[pivot][column]);
        }
      }
    }
    for (std::size_t row = 0; row < Span; ++row) {
      for (std::size_t column = 0; column < Span; ++column) {
        output[local][row][column] =
            augmented[row][Span + column];
      }
    }
  }
  return output;
}

constexpr auto kFecV5BurstInverses0 =
    make_fec_v5_burst_inverses<kFecV5BurstSpanSymbols, 0U, 7U>();
constexpr auto kFecV5BurstInverses1 =
    make_fec_v5_burst_inverses<kFecV5BurstSpanSymbols, 7U, 7U>();
constexpr auto kFecV5BurstInverses2 =
    make_fec_v5_burst_inverses<kFecV5BurstSpanSymbols, 14U, 7U>();
constexpr auto kFecV5BurstInverses3 =
    make_fec_v5_burst_inverses<kFecV5BurstSpanSymbols, 21U, 7U>();
constexpr auto kFecV5BurstInverses4 =
    make_fec_v5_burst_inverses<kFecV5BurstSpanSymbols, 28U, 7U>();
constexpr auto kFecV5BurstInverses5 =
    make_fec_v5_burst_inverses<kFecV5BurstSpanSymbols, 35U, 7U>();
constexpr auto kFecV5BurstInverses6 =
    make_fec_v5_burst_inverses<kFecV5BurstSpanSymbols, 42U, 7U>();
constexpr auto kFecV5BurstInverses7 =
    make_fec_v5_burst_inverses<kFecV5BurstSpanSymbols, 49U, 3U>();

constexpr auto kFecV5MaximumBurstInverses0 =
    make_fec_v5_burst_inverses<kFecV5MaximumBurstSpanSymbols, 0U, 7U>();
constexpr auto kFecV5MaximumBurstInverses1 =
    make_fec_v5_burst_inverses<kFecV5MaximumBurstSpanSymbols, 7U, 7U>();
constexpr auto kFecV5MaximumBurstInverses2 =
    make_fec_v5_burst_inverses<kFecV5MaximumBurstSpanSymbols, 14U, 7U>();
constexpr auto kFecV5MaximumBurstInverses3 =
    make_fec_v5_burst_inverses<kFecV5MaximumBurstSpanSymbols, 21U, 7U>();
constexpr auto kFecV5MaximumBurstInverses4 =
    make_fec_v5_burst_inverses<kFecV5MaximumBurstSpanSymbols, 28U, 7U>();
constexpr auto kFecV5MaximumBurstInverses5 =
    make_fec_v5_burst_inverses<kFecV5MaximumBurstSpanSymbols, 35U, 7U>();
constexpr auto kFecV5MaximumBurstInverses6 =
    make_fec_v5_burst_inverses<kFecV5MaximumBurstSpanSymbols, 42U, 7U>();
constexpr auto kFecV5MaximumBurstInverses7 =
    make_fec_v5_burst_inverses<kFecV5MaximumBurstSpanSymbols, 49U, 2U>();

const FecV5BurstInverse<kFecV5BurstSpanSymbols>&
fec_v5_burst_inverse(std::size_t first) {
  if (first < 7U) return kFecV5BurstInverses0[first];
  if (first < 14U) return kFecV5BurstInverses1[first - 7U];
  if (first < 21U) return kFecV5BurstInverses2[first - 14U];
  if (first < 28U) return kFecV5BurstInverses3[first - 21U];
  if (first < 35U) return kFecV5BurstInverses4[first - 28U];
  if (first < 42U) return kFecV5BurstInverses5[first - 35U];
  if (first < 49U) return kFecV5BurstInverses6[first - 42U];
  return kFecV5BurstInverses7[first - 49U];
}

const FecV5BurstInverse<kFecV5MaximumBurstSpanSymbols>&
fec_v5_maximum_burst_inverse(std::size_t first) {
  if (first < 7U) return kFecV5MaximumBurstInverses0[first];
  if (first < 14U) return kFecV5MaximumBurstInverses1[first - 7U];
  if (first < 21U) return kFecV5MaximumBurstInverses2[first - 14U];
  if (first < 28U) return kFecV5MaximumBurstInverses3[first - 21U];
  if (first < 35U) return kFecV5MaximumBurstInverses4[first - 28U];
  if (first < 42U) return kFecV5MaximumBurstInverses5[first - 35U];
  if (first < 49U) return kFecV5MaximumBurstInverses6[first - 42U];
  return kFecV5MaximumBurstInverses7[first - 49U];
}

constexpr auto make_fec_v5_power_prefixes() {
  std::array<
      std::array<std::uint8_t, kFecCodewordBytes + 1U>,
      kFecParityBytes> prefixes{};
  for (std::size_t check = 0; check < kFecParityBytes; ++check) {
    for (std::size_t symbol = 0; symbol < kFecCodewordBytes; ++symbol) {
      prefixes[check][symbol + 1U] =
          prefixes[check][symbol] ^ fec_gf_power(
              static_cast<std::uint8_t>(symbol + 1U),
              static_cast<std::uint8_t>(check));
    }
  }
  return prefixes;
}

constexpr auto kFecV5PowerPrefixes = make_fec_v5_power_prefixes();

std::uint8_t parity8(std::uint8_t value) {
  value ^= static_cast<std::uint8_t>(value >> 4U);
  value ^= static_cast<std::uint8_t>(value >> 2U);
  value ^= static_cast<std::uint8_t>(value >> 1U);
  return value & 1U;
}

std::uint8_t parity16(std::uint16_t value) {
  return parity8(static_cast<std::uint8_t>(value)) ^
         parity8(static_cast<std::uint8_t>(value >> 8U));
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

bool encode_receiver_status_v7(
    const ReceiverStatusV7& status,
    std::uint8_t* output,
    std::size_t output_size) {
  if (output == nullptr || output_size < kStatusBytesV7) return false;
  if (!encode_receiver_status_v6(status, output, output_size)) return false;
  std::memcpy(output, "LGS7", 4);
  output[4] = kStatusProtocolVersionV7;
  write_u32(output + 1216, status.fec_packets_received);
  write_u32(output + 1220, status.fec_packets_accepted);
  write_u32(output + 1224, status.fec_corrected_packets);
  write_u32(output + 1228, status.fec_corrected_codewords);
  write_u32(output + 1232, status.fec_uncorrectable_packets);
  write_u32(output + 1236, status.fec_semantic_crc_errors);
  write_u32(output + 1240, status.fec_framing_errors);
  write_u16(output + 1244, status.fec_last_decode_us);
  write_u16(output + 1246, status.fec_max_decode_us);
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
      if (size == kStatusBytesV3 || size == kStatusBytesV7 ||
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

bool fec_v5_solution_valid(
    const std::uint8_t* syndromes,
    const std::size_t* correction_symbols,
    const std::uint8_t* correction_values,
    std::size_t correction_count) {
  for (std::size_t check = 0; check < kFecParityBytes; ++check) {
    std::uint8_t reconstructed = 0U;
    for (std::size_t correction = 0;
         correction < correction_count; ++correction) {
      reconstructed ^= fec_gf_multiply(
          correction_values[correction],
          fec_gf_power(
              static_cast<std::uint8_t>(
                  correction_symbols[correction] + 1U),
              static_cast<std::uint8_t>(check)));
    }
    if (reconstructed != syndromes[check]) return false;
  }
  return true;
}

bool fec_v5_solve_and_validate(
    const std::uint8_t* syndromes,
    const std::size_t* correction_symbols,
    std::size_t correction_count,
    std::uint8_t* correction_values,
    bool require_nonzero) {
  if (syndromes == nullptr || correction_symbols == nullptr ||
      correction_values == nullptr || correction_count == 0U ||
      correction_count > kFecParityBytes) {
    return false;
  }
  std::uint8_t system[kFecParityBytes][kFecParityBytes + 1U] = {};
  for (std::size_t row = 0; row < correction_count; ++row) {
    for (std::size_t column = 0; column < correction_count; ++column) {
      system[row][column] = fec_gf_power(
          static_cast<std::uint8_t>(correction_symbols[column] + 1U),
          static_cast<std::uint8_t>(row));
    }
    system[row][correction_count] = syndromes[row];
  }
  for (std::size_t pivot = 0; pivot < correction_count; ++pivot) {
    std::size_t pivot_row = pivot;
    while (pivot_row < correction_count &&
           system[pivot_row][pivot] == 0U) {
      ++pivot_row;
    }
    if (pivot_row == correction_count) return false;
    if (pivot_row != pivot) {
      for (std::size_t column = pivot;
           column <= correction_count; ++column) {
        std::swap(system[pivot][column], system[pivot_row][column]);
      }
    }
    const std::uint8_t inverse_pivot =
        fec_gf_inverse(system[pivot][pivot]);
    for (std::size_t column = pivot;
         column <= correction_count; ++column) {
      system[pivot][column] =
          fec_gf_multiply(system[pivot][column], inverse_pivot);
    }
    for (std::size_t row = 0; row < correction_count; ++row) {
      if (row == pivot || system[row][pivot] == 0U) continue;
      const std::uint8_t scale = system[row][pivot];
      for (std::size_t column = pivot;
           column <= correction_count; ++column) {
        system[row][column] ^=
            fec_gf_multiply(scale, system[pivot][column]);
      }
    }
  }
  for (std::size_t correction = 0;
       correction < correction_count; ++correction) {
    correction_values[correction] =
        system[correction][correction_count];
    if (require_nonzero && correction_values[correction] == 0U) {
      return false;
    }
  }

  return fec_v5_solution_valid(
      syndromes, correction_symbols, correction_values, correction_count);
}

bool fec_v5_solve_contiguous_span(
    const std::uint8_t* syndromes,
    std::size_t first,
    std::uint8_t* correction_values) {
  if (syndromes == nullptr || correction_values == nullptr ||
      first >= kFecV5BurstSpanStarts) {
    return false;
  }
  const auto& inverse = fec_v5_burst_inverse(first);
  for (std::size_t row = 0; row < kFecV5BurstSpanSymbols; ++row) {
    correction_values[row] = 0U;
    for (std::size_t column = 0;
         column < kFecV5BurstSpanSymbols; ++column) {
      correction_values[row] ^=
          fec_gf_multiply(inverse[row][column], syndromes[column]);
    }
  }
  std::size_t correction_symbols[kFecV5BurstSpanSymbols] = {};
  for (std::size_t index = 0; index < kFecV5BurstSpanSymbols; ++index) {
    correction_symbols[index] = first + index;
  }
  return fec_v5_solution_valid(
      syndromes, correction_symbols, correction_values,
      kFecV5BurstSpanSymbols);
}

bool fec_v5_solve_maximum_contiguous_span(
    const std::uint8_t* syndromes,
    std::size_t first,
    std::uint8_t* correction_values) {
  if (syndromes == nullptr || correction_values == nullptr ||
      first >= kFecV5MaximumBurstSpanStarts) {
    return false;
  }
  const auto& inverse = fec_v5_maximum_burst_inverse(first);
  for (std::size_t row = 0;
       row < kFecV5MaximumBurstSpanSymbols; ++row) {
    correction_values[row] = 0U;
    for (std::size_t column = 0;
         column < kFecV5MaximumBurstSpanSymbols; ++column) {
      correction_values[row] ^=
          fec_gf_multiply(inverse[row][column], syndromes[column]);
    }
  }
  return true;
}

bool fec_v5_solve_constant_contiguous_span(
    const std::uint8_t* syndromes,
    std::size_t* correction_first,
    std::size_t* correction_count,
    std::uint8_t* correction_value) {
  if (syndromes == nullptr || correction_first == nullptr ||
      correction_count == nullptr || correction_value == nullptr) {
    return false;
  }
  std::size_t matching_first = kFecCodewordBytes;
  std::size_t matching_count = 0U;
  std::uint8_t matching_value = 0U;
  std::size_t matches = 0U;
  for (std::size_t first = 0; first < kFecCodewordBytes; ++first) {
    for (std::size_t count = kFecV5ConstantBurstMinimumSymbols;
         first + count <= kFecCodewordBytes; ++count) {
      std::uint8_t value = 0U;
      bool value_established = false;
      for (std::size_t check = 0; check < kFecParityBytes; ++check) {
        const std::uint8_t coefficient =
            kFecV5PowerPrefixes[check][first + count] ^
            kFecV5PowerPrefixes[check][first];
        if (coefficient == 0U) {
          if (syndromes[check] != 0U) {
            value_established = false;
            break;
          }
          continue;
        }
        const std::uint8_t candidate = fec_gf_multiply(
            syndromes[check], fec_gf_inverse(coefficient));
        if (!value_established) {
          value = candidate;
          value_established = true;
        } else if (candidate != value) {
          value_established = false;
          break;
        }
      }
      if (!value_established || value == 0U) continue;
      matching_first = first;
      matching_count = count;
      matching_value = value;
      ++matches;
      if (matches > 1U) return false;
    }
  }
  if (matches != 1U) return false;
  *correction_first = matching_first;
  *correction_count = matching_count;
  *correction_value = matching_value;
  return true;
}

std::size_t fec_rs_wire_offset(
    std::size_t matrix_offset,
    std::size_t symbol,
    std::size_t logical_block,
    std::size_t codewords,
    bool diagonal) {
  std::size_t wire_block = logical_block;
  if (diagonal) {
    wire_block += symbol;
    while (wire_block >= codewords) wire_block -= codewords;
  }
  return matrix_offset + symbol * codewords + wire_block;
}

bool fec_outer_parity_valid(
    const std::uint8_t* decoded,
    std::size_t codewords) {
  if (decoded == nullptr || codewords < 2U) return false;
  const std::size_t data_codewords = codewords - 1U;
  const std::size_t outer_offset = data_codewords * kFecDataBytes;
  for (std::size_t symbol = 0; symbol < kFecDataBytes; ++symbol) {
    std::uint8_t expected = 0U;
    for (std::size_t block = 0; block < data_codewords; ++block) {
      expected ^= decoded[block * kFecDataBytes + symbol];
    }
    if (decoded[outer_offset + symbol] != expected) return false;
  }
  return true;
}

bool fec_v5_decoded_payload_valid(
    const std::uint8_t* decoded,
    std::size_t codewords,
    std::uint8_t expected_version,
    bool outer_parity,
    bool require_outer_parity) {
  if (decoded == nullptr) return false;
  if (outer_parity && codewords < 2U) return false;
  const std::size_t data_codewords = codewords - (outer_parity ? 1U : 0U);
  const std::size_t decoded_capacity = data_codewords * kFecDataBytes;
  if (decoded_capacity < kFecEnvelopeHeaderBytes +
                             kAlignedEnvelopeHeaderBytes + 1U +
                             kAnimationPipelineCrcBytes ||
      decoded[0] != static_cast<std::uint8_t>(
          ReceiverCommand::AlignedEnvelope) ||
      decoded[1] != expected_version) {
    return false;
  }
  const std::size_t inner_wire_size =
      (static_cast<std::size_t>(decoded[2]) << 8U) | decoded[3];
  const std::uint8_t* inner = decoded + kFecEnvelopeHeaderBytes;
  if (inner_wire_size < kAlignedEnvelopeHeaderBytes + 1U +
                            kAnimationPipelineCrcBytes ||
      inner_wire_size > decoded_capacity - kFecEnvelopeHeaderBytes ||
      inner_wire_size % kSpiDmaAlignmentBytes != 0U ||
      inner[0] != static_cast<std::uint8_t>(
          ReceiverCommand::AlignedEnvelope) ||
      inner[1] != kAlignedEnvelopeVersion) {
    return false;
  }
  const std::size_t semantic_size =
      (static_cast<std::size_t>(inner[2]) << 8U) | inner[3];
  const std::size_t maximum_semantic_size = outer_parity
      ? kFecEnvelopeMaxSemanticBytes
      : kFecV6EnvelopeMaxSemanticBytes;
  if (semantic_size == 0U || semantic_size > maximum_semantic_size) {
    return false;
  }
  const std::size_t inner_unpadded = kAlignedEnvelopeHeaderBytes +
      semantic_size + kAnimationPipelineCrcBytes;
  const std::size_t inner_padding =
      (kSpiDmaAlignmentBytes - inner_unpadded % kSpiDmaAlignmentBytes) %
      kSpiDmaAlignmentBytes;
  if (inner_wire_size != inner_unpadded + inner_padding) return false;
  std::size_t required_codewords =
      (kFecEnvelopeHeaderBytes + inner_wire_size + kFecDataBytes - 1U) /
      kFecDataBytes;
  if (outer_parity) ++required_codewords;
  required_codewords += (4U - required_codewords % 4U) % 4U;
  if (required_codewords != codewords) return false;
  for (std::size_t index = kFecEnvelopeHeaderBytes + inner_wire_size;
       index < decoded_capacity; ++index) {
    if (decoded[index] != 0U) return false;
  }
  if (outer_parity && require_outer_parity &&
      !fec_outer_parity_valid(decoded, codewords)) {
    return false;
  }
  return receiver_packet_crc_valid(inner, inner_wire_size);
}

bool fec_v5_systematic_payload_valid(
    const std::uint8_t* packet,
    std::size_t codewords,
    std::uint8_t* scratch,
    std::uint8_t expected_version,
    bool diagonal,
    bool outer_parity) {
  const std::size_t matrix_offset = kFecEnvelopeHeaderBytes;
  for (std::size_t block = 0; block < codewords; ++block) {
    for (std::size_t symbol = 0; symbol < kFecDataBytes; ++symbol) {
      scratch[block * kFecDataBytes + symbol] =
          packet[fec_rs_wire_offset(
              matrix_offset, symbol, block, codewords, diagonal)];
    }
  }
  return fec_v5_decoded_payload_valid(
      scratch, codewords, expected_version, outer_parity, true);
}

ReceiverFecPacketOutcome receiver_fec_packet_outcome(
    bool decoded_ok, const ReceiverPacketDecodeReport& report) {
  if (!report.fec_envelope_attempted) {
    return ReceiverFecPacketOutcome::NotFec;
  }
  if (decoded_ok) return ReceiverFecPacketOutcome::Accepted;
  switch (report.result) {
    case ReceiverPacketDecodeResult::FecUncorrectable:
      return ReceiverFecPacketOutcome::Uncorrectable;
    case ReceiverPacketDecodeResult::FecSemanticCrcError:
      return ReceiverFecPacketOutcome::SemanticCrcError;
    default:
      return ReceiverFecPacketOutcome::FramingError;
  }
}

bool decode_receiver_packet_payload(
    const std::uint8_t* packet,
    std::size_t packet_size,
    ReceiverPacketPayload* payload,
    ReceiverPacketDecodeReport* report,
    std::uint8_t* scratch,
    std::size_t scratch_size) {
  if (payload != nullptr) *payload = {};
  if (report != nullptr) *report = {};
  if (packet == nullptr || payload == nullptr ||
      packet_size < 1U + kAnimationPipelineCrcBytes ||
      packet_size > kAnimationPipelineMaxTransactionBytes) {
    return false;
  }

  const bool fec_v2_shape = packet_size >= 2U * kFecV2CodewordBytes &&
      packet_size % (2U * kFecV2CodewordBytes) == 0U;
  const std::uint8_t fec_v2_marker_distance = static_cast<std::uint8_t>(
      __builtin_popcount(static_cast<unsigned int>(
          packet[0] ^ static_cast<std::uint8_t>(ReceiverCommand::AlignedEnvelope))) +
      __builtin_popcount(static_cast<unsigned int>(
          packet[1] ^ kFecEnvelopeVersionV2)));
  const bool fec_v2_candidate =
      fec_v2_shape && fec_v2_marker_distance <= 1U;
  const bool fec_v3_shape = packet_size >=
          kFecWireHeaderBytes + 4U * kFecV3CodewordBytes &&
      (packet_size - kFecWireHeaderBytes) %
          (4U * kFecV3CodewordBytes) == 0U;
  const bool fec_v4_shape = packet_size >=
          kFecWireHeaderBytes + 4U * kFecV4CodewordBytes &&
      (packet_size - kFecWireHeaderBytes) %
          (4U * kFecV4CodewordBytes) == 0U;
  const bool fec_v5_shape = packet_size >=
          kFecWireHeaderBytes + 4U * kFecCodewordBytes &&
      (packet_size - kFecWireHeaderBytes) %
          (4U * kFecCodewordBytes) == 0U;
  const auto duplicated_marker_matches = [&](std::uint8_t version) {
    const std::size_t suffix = packet_size - kFecEnvelopeHeaderBytes;
    return (packet[0] == static_cast<std::uint8_t>(
                ReceiverCommand::AlignedEnvelope) &&
            packet[1] == version) ||
           (packet[suffix] == static_cast<std::uint8_t>(
                ReceiverCommand::AlignedEnvelope) &&
            packet[suffix + 1U] == version);
  };
  const bool fec_v3_candidate =
      fec_v3_shape && duplicated_marker_matches(kFecEnvelopeVersionV3);
  const bool fec_v4_candidate =
      fec_v4_shape && duplicated_marker_matches(kFecEnvelopeVersionV4);
  const bool fec_v7_candidate =
      fec_v5_shape && duplicated_marker_matches(kFecEnvelopeVersion);
  const bool fec_v6_candidate =
      fec_v5_shape && duplicated_marker_matches(kFecEnvelopeVersionV6);
  const bool fec_v5_candidate =
      fec_v5_shape && duplicated_marker_matches(kFecEnvelopeVersionV5);
  if (!fec_v2_candidate && !fec_v3_candidate &&
      !fec_v4_candidate && !fec_v5_candidate && !fec_v6_candidate &&
      !fec_v7_candidate) {
    if (receiver_packet_crc_valid(packet, packet_size) &&
        decode_crc_valid_receiver_packet_payload(
            packet, packet_size, payload)) {
      if (report != nullptr) report->result = ReceiverPacketDecodeResult::Ok;
      return true;
    }
    if (report != nullptr) report->result = ReceiverPacketDecodeResult::CrcError;
    return false;
  }
  if (report != nullptr) report->fec_envelope_attempted = true;
  const bool fec_v7 = fec_v7_candidate;
  const bool fec_v6 = !fec_v7 && fec_v6_candidate;
  const bool fec_v5 = !fec_v7 && !fec_v6 && fec_v5_candidate;
  const bool fec_rs = fec_v7 || fec_v6 || fec_v5;
  const bool diagonal = fec_v7 || fec_v6;
  const bool outer_parity = fec_v7;
  const std::uint8_t fec_rs_version = fec_v7
      ? kFecEnvelopeVersion
      : fec_v6 ? kFecEnvelopeVersionV6 : kFecEnvelopeVersionV5;
  const bool fec_v4 = !fec_rs && fec_v4_candidate;
  const bool fec_v3 = !fec_rs && !fec_v4 && fec_v3_candidate;
  const std::size_t codewords = fec_rs
      ? (packet_size - kFecWireHeaderBytes) / kFecCodewordBytes
      : fec_v4
          ? (packet_size - kFecWireHeaderBytes) / kFecV4CodewordBytes
          : fec_v3
              ? (packet_size - kFecWireHeaderBytes) / kFecV3CodewordBytes
              : packet_size / kFecV2CodewordBytes;
  const std::size_t data_bytes = fec_rs
      ? kFecDataBytes
      : fec_v4
          ? kFecV4DataBytes
          : fec_v3 ? kFecV3DataBytes : kFecV2DataBytes;
  const std::size_t decoded_capacity = codewords * data_bytes;
  if (scratch == nullptr || scratch_size < decoded_capacity ||
      (fec_rs && (codewords > kFecMaxCodewords || codewords % 4U != 0U)) ||
      (fec_v4 && (codewords > kFecV4MaxCodewords || codewords % 4U != 0U)) ||
      (fec_v3 && (codewords > kFecV3MaxCodewords || codewords % 4U != 0U)) ||
      (!fec_rs && !fec_v4 && !fec_v3 && codewords > kFecV2MaxCodewords)) {
    return false;
  }
  std::uint16_t corrected_codewords = 0;
  std::uint16_t corrected_bits = 0;
  bool outer_parity_unavailable = false;
  if (fec_rs) {
    const std::size_t matrix_offset = kFecEnvelopeHeaderBytes;
    constexpr std::size_t kMaximumCorrections = kFecParityBytes / 2U;
    // Clean installed frames are overwhelmingly the common case. Deinterleave
    // the systematic bytes and validate the complete canonical inner packet
    // before paying for 40,800 GF(256) syndrome operations. Any data, length,
    // padding, or semantic-CRC damage still enters the bounded RS decoder.
    // Parity-only damage is safe to ignore because it cannot change the
    // authenticated semantic payload.
    const bool systematic_payload_valid = fec_v5_systematic_payload_valid(
        packet, codewords, scratch, fec_rs_version, diagonal, outer_parity);
    std::size_t contiguous_burst_hint = kFecCodewordBytes;
    bool maximum_burst_blocks[kFecMaxCodewords] = {};
    std::uint8_t maximum_burst_syndromes
        [kFecMaxCodewords][kFecParityBytes] = {};
    std::size_t maximum_burst_block_count = 0U;
    for (std::size_t block = 0;
         !systematic_payload_valid && block < codewords; ++block) {
      std::uint8_t syndromes[kFecParityBytes] = {};
      for (std::size_t symbol = 0; symbol < kFecCodewordBytes; ++symbol) {
        const std::uint8_t value = packet[fec_rs_wire_offset(
            matrix_offset, symbol, block, codewords, diagonal)];
        const std::uint8_t evaluation =
            static_cast<std::uint8_t>(symbol + 1U);
        std::uint8_t power = 1U;
        for (std::size_t check = 0; check < kFecParityBytes; ++check) {
          syndromes[check] ^= fec_gf_multiply(value, power);
          power = fec_gf_multiply(power, evaluation);
        }
      }

      const bool canonical = std::all_of(
          std::begin(syndromes), std::end(syndromes),
          [](std::uint8_t value) { return value == 0U; });
      std::size_t correction_symbols[kFecParityBytes] = {};
      std::uint8_t correction_values[kFecParityBytes] = {};
      std::size_t correction_count = 0;
      std::size_t constant_correction_first = kFecCodewordBytes;
      std::size_t constant_correction_count = 0U;
      std::uint8_t constant_correction_value = 0U;
      if (!canonical) {
        const bool bounded_recovery = [&]() {
          // Berlekamp-Massey finds the shortest recurrence for the ten
          // syndromes. With evaluation points X, its locator is
          // Lambda(z) = product(1 + X*z), so roots occur at inverse(X).
          std::uint8_t locator[kFecParityBytes + 1U] = {1U};
          std::uint8_t previous[kFecParityBytes + 1U] = {1U};
          std::size_t locator_degree = 0;
          std::size_t shift = 1;
          std::uint8_t previous_discrepancy = 1U;
          for (std::size_t index = 0; index < kFecParityBytes; ++index) {
            std::uint8_t discrepancy = syndromes[index];
            for (std::size_t term = 1; term <= locator_degree; ++term) {
              discrepancy ^= fec_gf_multiply(
                  locator[term], syndromes[index - term]);
            }
            if (discrepancy == 0U) {
              ++shift;
              continue;
            }
            std::uint8_t saved[kFecParityBytes + 1U] = {};
            std::copy(std::begin(locator), std::end(locator), saved);
            const std::uint8_t scale = fec_gf_multiply(
                discrepancy, fec_gf_inverse(previous_discrepancy));
            for (std::size_t term = 0;
                 term + shift <= kFecParityBytes; ++term) {
              locator[term + shift] ^=
                  fec_gf_multiply(scale, previous[term]);
            }
            if (2U * locator_degree <= index) {
              locator_degree = index + 1U - locator_degree;
              std::copy(std::begin(saved), std::end(saved), previous);
              previous_discrepancy = discrepancy;
              shift = 1U;
            } else {
              ++shift;
            }
          }
          if (locator_degree == 0U ||
              locator_degree > kMaximumCorrections) {
            return false;
          }
          correction_count = 0;
          for (std::size_t symbol = 0;
               symbol < kFecCodewordBytes; ++symbol) {
            const std::uint8_t inverse_evaluation = fec_gf_inverse(
                static_cast<std::uint8_t>(symbol + 1U));
            std::uint8_t value = locator[locator_degree];
            for (std::size_t term = locator_degree; term > 0U; --term) {
              value = fec_gf_multiply(value, inverse_evaluation) ^
                  locator[term - 1U];
            }
            if (value == 0U) {
              if (correction_count >= locator_degree) return false;
              correction_symbols[correction_count++] = symbol;
            }
          }
          return correction_count == locator_degree &&
              fec_v5_solve_and_validate(
                  syndromes, correction_symbols, correction_count,
                  correction_values, true);
        }();

        const bool contiguous_burst_recovery = bounded_recovery || [&]() {
          // The installed SPI fault is a contiguous wire burst. Interleaving
          // maps that burst to a short consecutive symbol span in each
          // codeword. Nine syndromes solve as many as nine erasures in that
          // known-shape span and the tenth validates the result, while
          // arbitrary six-symbol errors remain outside the bounded correction
          // contract.
          const auto try_span = [&](std::size_t first) {
            std::uint8_t candidate_values[kFecParityBytes] = {};
            if (!fec_v5_solve_contiguous_span(
                    syndromes, first, candidate_values)) {
              return false;
            }
            correction_count = 0;
            for (std::size_t index = 0;
                 index < kFecV5BurstSpanSymbols; ++index) {
              if (candidate_values[index] == 0U) continue;
              correction_symbols[correction_count] =
                  first + index;
              correction_values[correction_count] = candidate_values[index];
              ++correction_count;
            }
            if (correction_count == 0U) return false;
            contiguous_burst_hint = first;
            return true;
          };
          if (contiguous_burst_hint + kFecV5BurstSpanSymbols <=
                  kFecCodewordBytes &&
              try_span(contiguous_burst_hint)) {
            return true;
          }
          for (std::size_t first = 0;
               first + kFecV5BurstSpanSymbols <= kFecCodewordBytes; ++first) {
            if (first == contiguous_burst_hint) continue;
            if (try_span(first)) return true;
          }
          return false;
        }();
        const bool constant_burst_recovery =
            !contiguous_burst_recovery &&
            fec_v5_solve_constant_contiguous_span(
                syndromes, &constant_correction_first,
                &constant_correction_count, &constant_correction_value);
        if (!contiguous_burst_recovery && !constant_burst_recovery) {
          maximum_burst_blocks[block] = true;
          std::copy(
              std::begin(syndromes), std::end(syndromes),
              maximum_burst_syndromes[block]);
          ++maximum_burst_block_count;
          correction_count = 0U;
        } else {
          ++corrected_codewords;
          if (constant_burst_recovery) {
            corrected_bits = static_cast<std::uint16_t>(
                corrected_bits + constant_correction_count *
                    __builtin_popcount(static_cast<unsigned int>(
                        constant_correction_value)));
          } else {
            for (std::size_t correction = 0;
                 correction < correction_count; ++correction) {
              corrected_bits = static_cast<std::uint16_t>(
                  corrected_bits + __builtin_popcount(
                      static_cast<unsigned int>(
                          correction_values[correction])));
            }
          }
        }
      }
      for (std::size_t symbol = 0; symbol < kFecDataBytes; ++symbol) {
        std::uint8_t value = packet[fec_rs_wire_offset(
            matrix_offset, symbol, block, codewords, diagonal)];
        for (std::size_t correction = 0;
             correction < correction_count; ++correction) {
          if (symbol == correction_symbols[correction]) {
            value ^= correction_values[correction];
          }
        }
        if (symbol >= constant_correction_first &&
            symbol < constant_correction_first + constant_correction_count) {
          value ^= constant_correction_value;
        }
        scratch[block * kFecDataBytes + symbol] = value;
      }
    }
    if (!systematic_payload_valid && outer_parity &&
        maximum_burst_block_count == 1U) {
      std::size_t failed_block = 0U;
      while (failed_block < codewords &&
             !maximum_burst_blocks[failed_block]) {
        ++failed_block;
      }
      const std::size_t data_codewords = codewords - 1U;
      bool recovered = false;
      if (failed_block < data_codewords) {
        const std::size_t outer_offset = data_codewords * kFecDataBytes;
        std::uint16_t reconstructed_bits = 0U;
        for (std::size_t symbol = 0; symbol < kFecDataBytes; ++symbol) {
          std::uint8_t value = scratch[outer_offset + symbol];
          for (std::size_t block = 0; block < data_codewords; ++block) {
            if (block == failed_block) continue;
            value ^= scratch[block * kFecDataBytes + symbol];
          }
          const std::size_t offset = failed_block * kFecDataBytes + symbol;
          reconstructed_bits = static_cast<std::uint16_t>(
              reconstructed_bits + __builtin_popcount(
                  static_cast<unsigned int>(scratch[offset] ^ value)));
          scratch[offset] = value;
        }
        recovered = fec_v5_decoded_payload_valid(
            scratch, codewords, fec_rs_version, true, true);
        if (recovered) {
          corrected_bits = static_cast<std::uint16_t>(
              corrected_bits + reconstructed_bits);
        }
      } else if (failed_block == data_codewords) {
        // Damage confined to the redundant outer shard cannot change the
        // canonical inner packet. Its CRC remains the semantic authority.
        recovered = fec_v5_decoded_payload_valid(
            scratch, codewords, fec_rs_version, true, false);
        outer_parity_unavailable = recovered;
      }
      if (recovered) {
        ++corrected_codewords;
        maximum_burst_blocks[failed_block] = false;
        maximum_burst_block_count = 0U;
      }
    }
    if (!systematic_payload_valid && maximum_burst_block_count != 0U) {
      // A burst longer than nine interleave columns leaves a contiguous run
      // of codewords with ten damaged symbols. Ten equations solve those
      // erasures but cannot independently validate their unknown location, so
      // accept only one location that reconstructs the complete canonical
      // inner frame and its end-to-end CRC. A burst wrapping the interleave row
      // boundary uses adjacent symbol spans on its prefix and suffix blocks.
      std::size_t linear_runs = 0U;
      for (std::size_t block = 0; block < codewords; ++block) {
        if (maximum_burst_blocks[block] &&
            (block == 0U || !maximum_burst_blocks[block - 1U])) {
          ++linear_runs;
        }
      }
      const bool wrapped = maximum_burst_block_count < codewords &&
          maximum_burst_blocks[0] &&
          maximum_burst_blocks[codewords - 1U];
      const bool valid_shape = wrapped ? linear_runs == 2U : linear_runs == 1U;
      if (!valid_shape) {
        if (report != nullptr) {
          report->result = ReceiverPacketDecodeResult::FecUncorrectable;
        }
        return false;
      }
      std::size_t wrapped_prefix_blocks = 0U;
      while (wrapped_prefix_blocks < codewords &&
             maximum_burst_blocks[wrapped_prefix_blocks]) {
        ++wrapped_prefix_blocks;
      }

      const auto apply_maximum_burst_candidate = [&](
          std::size_t first,
          std::uint16_t* candidate_codewords,
          std::uint16_t* candidate_bits) {
        std::uint16_t local_codewords = 0U;
        std::uint16_t local_bits = 0U;
        for (std::size_t block = 0; block < codewords; ++block) {
          if (!maximum_burst_blocks[block]) continue;
          const std::size_t block_first =
              !diagonal && wrapped && block < wrapped_prefix_blocks
                  ? first + 1U
                  : first;
          if (block_first >= kFecV5MaximumBurstSpanStarts) return false;
          std::uint8_t values[kFecV5MaximumBurstSpanSymbols] = {};
          if (!fec_v5_solve_maximum_contiguous_span(
                  maximum_burst_syndromes[block], block_first, values)) {
            return false;
          }
          ++local_codewords;
          for (std::size_t index = 0;
               index < kFecV5MaximumBurstSpanSymbols; ++index) {
            local_bits = static_cast<std::uint16_t>(
                local_bits + __builtin_popcount(
                    static_cast<unsigned int>(values[index])));
          }
          for (std::size_t symbol = 0; symbol < kFecDataBytes; ++symbol) {
            std::uint8_t value = packet[fec_rs_wire_offset(
                matrix_offset, symbol, block, codewords, diagonal)];
            if (symbol >= block_first &&
                symbol < block_first + kFecV5MaximumBurstSpanSymbols) {
              value ^= values[symbol - block_first];
            }
            scratch[block * kFecDataBytes + symbol] = value;
          }
        }
        if (!fec_v5_decoded_payload_valid(
                scratch, codewords, fec_rs_version, outer_parity, true)) {
          return false;
        }
        if (candidate_codewords != nullptr) {
          *candidate_codewords = local_codewords;
        }
        if (candidate_bits != nullptr) *candidate_bits = local_bits;
        return true;
      };

      std::size_t matching_first = kFecCodewordBytes;
      std::size_t matching_count = 0U;
      const std::size_t candidate_starts = wrapped
          ? kFecV5MaximumBurstSpanStarts - 1U
          : kFecV5MaximumBurstSpanStarts;
      for (std::size_t first = 0; first < candidate_starts; ++first) {
        if (apply_maximum_burst_candidate(first, nullptr, nullptr)) {
          matching_first = first;
          ++matching_count;
          if (matching_count > 1U) break;
        }
      }
      std::uint16_t maximum_corrected_codewords = 0U;
      std::uint16_t maximum_corrected_bits = 0U;
      if (matching_count != 1U ||
          !apply_maximum_burst_candidate(
              matching_first, &maximum_corrected_codewords,
              &maximum_corrected_bits)) {
        if (report != nullptr) {
          report->result = ReceiverPacketDecodeResult::FecUncorrectable;
        }
        return false;
      }
      corrected_codewords = static_cast<std::uint16_t>(
          corrected_codewords + maximum_corrected_codewords);
      corrected_bits = static_cast<std::uint16_t>(
          corrected_bits + maximum_corrected_bits);
    }
  } else if (fec_v4) {
    const std::size_t matrix_offset = kFecEnvelopeHeaderBytes;
    for (std::size_t block = 0; block < codewords; ++block) {
      std::uint8_t syndromes[kFecV4ParityBytes] = {};
      for (std::size_t symbol = 0; symbol < kFecV4CodewordBytes; ++symbol) {
        const std::uint8_t value =
            packet[matrix_offset + symbol * codewords + block];
        const std::uint8_t evaluation =
            static_cast<std::uint8_t>(symbol + 1U);
        std::uint8_t power = 1U;
        for (std::size_t check = 0; check < kFecV4ParityBytes; ++check) {
          syndromes[check] ^= fec_gf_multiply(value, power);
          power = fec_gf_multiply(power, evaluation);
        }
      }

      std::size_t correction_symbols[2] = {
          kFecV4CodewordBytes, kFecV4CodewordBytes};
      std::uint8_t correction_values[2] = {};
      std::size_t correction_count = 0;
      const bool canonical = std::all_of(
          std::begin(syndromes), std::end(syndromes),
          [](std::uint8_t value) { return value == 0U; });
      if (!canonical && syndromes[0] != 0U) {
        for (std::size_t symbol = 0;
             symbol < kFecV4CodewordBytes; ++symbol) {
          const std::uint8_t evaluation =
              static_cast<std::uint8_t>(symbol + 1U);
          std::uint8_t power = 1U;
          bool matches = true;
          for (std::size_t check = 0;
               check < kFecV4ParityBytes; ++check) {
            if (syndromes[check] !=
                fec_gf_multiply(syndromes[0], power)) {
              matches = false;
              break;
            }
            power = fec_gf_multiply(power, evaluation);
          }
          if (matches) {
            correction_symbols[0] = symbol;
            correction_values[0] = syndromes[0];
            correction_count = 1U;
            break;
          }
        }
      }
      if (!canonical && correction_count == 0U) {
        for (std::size_t first = 0;
             first + 1U < kFecV4CodewordBytes && correction_count == 0U;
             ++first) {
          const std::uint8_t first_evaluation =
              static_cast<std::uint8_t>(first + 1U);
          for (std::size_t second = first + 1U;
               second < kFecV4CodewordBytes; ++second) {
            const std::uint8_t second_evaluation =
                static_cast<std::uint8_t>(second + 1U);
            const std::uint8_t denominator =
                first_evaluation ^ second_evaluation;
            const std::uint8_t first_error = fec_gf_multiply(
                syndromes[1] ^
                    fec_gf_multiply(second_evaluation, syndromes[0]),
                fec_gf_inverse(denominator));
            const std::uint8_t second_error = syndromes[0] ^ first_error;
            if (first_error == 0U || second_error == 0U) continue;
            std::uint8_t first_power = 1U;
            std::uint8_t second_power = 1U;
            bool matches = true;
            for (std::size_t check = 0;
                 check < kFecV4ParityBytes; ++check) {
              if (syndromes[check] !=
                  (fec_gf_multiply(first_error, first_power) ^
                   fec_gf_multiply(second_error, second_power))) {
                matches = false;
                break;
              }
              first_power =
                  fec_gf_multiply(first_power, first_evaluation);
              second_power =
                  fec_gf_multiply(second_power, second_evaluation);
            }
            if (matches) {
              correction_symbols[0] = first;
              correction_symbols[1] = second;
              correction_values[0] = first_error;
              correction_values[1] = second_error;
              correction_count = 2U;
              break;
            }
          }
        }
      }
      if (!canonical && correction_count == 0U) {
        if (report != nullptr) {
          report->result = ReceiverPacketDecodeResult::FecUncorrectable;
        }
        return false;
      }
      if (correction_count != 0U) {
        ++corrected_codewords;
        for (std::size_t correction = 0;
             correction < correction_count; ++correction) {
          corrected_bits = static_cast<std::uint16_t>(
              corrected_bits + __builtin_popcount(static_cast<unsigned int>(
                  correction_values[correction])));
        }
      }
      for (std::size_t symbol = 0; symbol < kFecV4DataBytes; ++symbol) {
        std::uint8_t value =
            packet[matrix_offset + symbol * codewords + block];
        for (std::size_t correction = 0;
             correction < correction_count; ++correction) {
          if (symbol == correction_symbols[correction]) {
            value ^= correction_values[correction];
          }
        }
        scratch[block * kFecV4DataBytes + symbol] = value;
      }
    }
  } else if (fec_v3) {
    const std::size_t matrix_offset = kFecEnvelopeHeaderBytes;
    for (std::size_t block = 0; block < codewords; ++block) {
      std::uint8_t syndrome0 = 0;
      std::uint8_t syndrome1 = 0;
      std::uint8_t syndrome2 = 0;
      for (std::size_t symbol = 0; symbol < kFecV3DataBytes; ++symbol) {
        const std::uint8_t value =
            packet[matrix_offset + symbol * codewords + block];
        const std::uint8_t coefficient =
            static_cast<std::uint8_t>(symbol + 1U);
        syndrome0 ^= value;
        syndrome1 ^= fec_gf_multiply(value, coefficient);
        syndrome2 ^= fec_gf_multiply(
            value, fec_gf_multiply(coefficient, coefficient));
      }
      syndrome0 ^= packet[
          matrix_offset + kFecV3DataBytes * codewords + block];
      syndrome1 ^= packet[
          matrix_offset + (kFecV3DataBytes + 1U) * codewords + block];
      syndrome2 ^= packet[
          matrix_offset + (kFecV3DataBytes + 2U) * codewords + block];

      std::size_t corrected_symbol = kFecV3DataBytes;
      std::uint8_t corrected_value = 0;
      bool corrected = false;
      if (syndrome0 == 0U && syndrome1 == 0U && syndrome2 == 0U) {
        // Canonical codeword.
      } else if (syndrome0 != 0U && syndrome1 == 0U && syndrome2 == 0U) {
        corrected = true;  // First parity symbol only.
        corrected_value = syndrome0;
      } else if (syndrome0 == 0U && syndrome1 != 0U && syndrome2 == 0U) {
        corrected = true;  // Second parity symbol only.
        corrected_value = syndrome1;
      } else if (syndrome0 == 0U && syndrome1 == 0U && syndrome2 != 0U) {
        corrected = true;  // Third parity symbol only.
        corrected_value = syndrome2;
      } else if (syndrome0 != 0U) {
        for (std::size_t symbol = 0; symbol < kFecV3DataBytes; ++symbol) {
          const std::uint8_t coefficient =
              static_cast<std::uint8_t>(symbol + 1U);
          if (syndrome1 == fec_gf_multiply(syndrome0, coefficient) &&
              syndrome2 == fec_gf_multiply(
                  syndrome0,
                  fec_gf_multiply(coefficient, coefficient))) {
            corrected = true;
            corrected_symbol = symbol;
            corrected_value = syndrome0;
            break;
          }
        }
      }
      if (!corrected &&
          (syndrome0 != 0U || syndrome1 != 0U || syndrome2 != 0U)) {
        if (report != nullptr) {
          report->result = ReceiverPacketDecodeResult::FecUncorrectable;
        }
        return false;
      }
      if (corrected) {
        ++corrected_codewords;
        corrected_bits = static_cast<std::uint16_t>(
            corrected_bits + __builtin_popcount(
                static_cast<unsigned int>(corrected_value)));
      }
      for (std::size_t symbol = 0; symbol < kFecV3DataBytes; ++symbol) {
        std::uint8_t value =
            packet[matrix_offset + symbol * codewords + block];
        if (symbol == corrected_symbol) value ^= corrected_value;
        scratch[block * kFecV3DataBytes + symbol] = value;
      }
    }
  } else {
    for (std::size_t block = 0; block < codewords; ++block) {
      const std::size_t wire_offset = block * kFecV2CodewordBytes;
      const std::uint8_t* data = packet + wire_offset;
      const std::uint16_t stored = static_cast<std::uint16_t>(
          (static_cast<std::uint16_t>(data[kFecV2DataBytes]) << 8U) |
          data[kFecV2DataBytes + 1U]);
      if ((stored & ~(kFecV2ParityMask | kFecV2OverallParityMask)) != 0U) {
        return false;
      }
      const std::uint16_t stored_hamming = stored & kFecV2ParityMask;
      std::uint16_t data_syndrome = 0;
      std::uint8_t data_parity = 0;
      std::uint16_t codeword_position = 1;
      for (std::size_t byte_index = 0;
           byte_index < kFecV2DataBytes; ++byte_index) {
        const std::uint8_t value = data[byte_index];
        data_parity ^= parity8(value);
        for (std::uint8_t bit = 0; bit < 8U; ++bit) {
          while (is_power_of_two(codeword_position)) ++codeword_position;
          if ((value & (1U << bit)) != 0U) {
            data_syndrome ^= codeword_position;
          }
          ++codeword_position;
        }
      }
      const std::uint16_t syndrome = data_syndrome ^ stored_hamming;
      const bool overall_mismatch =
          (data_parity ^ parity16(stored_hamming) ^
           ((stored & kFecV2OverallParityMask) != 0U)) != 0U;
      std::size_t corrected_data_bit = kFecV2DataBytes * 8U;
      bool corrected = false;
      if (syndrome != 0U && !overall_mismatch) {
        if (report != nullptr) {
          report->result = ReceiverPacketDecodeResult::FecUncorrectable;
        }
        return false;
      }
      if (syndrome != 0U && overall_mismatch) {
        if (!is_power_of_two(syndrome) &&
            !fec_data_bit_index(syndrome, &corrected_data_bit)) {
          if (report != nullptr) {
            report->result = ReceiverPacketDecodeResult::FecUncorrectable;
          }
          return false;
        }
        corrected = true;
      } else if (overall_mismatch) {
        corrected = true;
      }
      if (corrected) {
        ++corrected_codewords;
        ++corrected_bits;
      }
      for (std::size_t byte_index = 0;
           byte_index < kFecV2DataBytes; ++byte_index) {
        std::uint8_t value = data[byte_index];
        if (corrected_data_bit / 8U == byte_index) {
          value ^= static_cast<std::uint8_t>(
              1U << (corrected_data_bit % 8U));
        }
        scratch[block * kFecV2DataBytes + byte_index] = value;
      }
    }
  }

  const std::size_t semantic_decoded_capacity = fec_v7
      ? decoded_capacity - kFecOuterParityBytes
      : decoded_capacity;
  if (semantic_decoded_capacity < kFecEnvelopeHeaderBytes +
                             kAlignedEnvelopeHeaderBytes + 1U +
                             kAnimationPipelineCrcBytes ||
      scratch[0] != static_cast<std::uint8_t>(ReceiverCommand::AlignedEnvelope) ||
      scratch[1] != (fec_rs
          ? fec_rs_version
          : fec_v4
              ? kFecEnvelopeVersionV4
              : fec_v3 ? kFecEnvelopeVersionV3 : kFecEnvelopeVersionV2)) {
    return false;
  }
  const std::size_t inner_wire_size =
      (static_cast<std::size_t>(scratch[2]) << 8U) | scratch[3];
  const std::uint8_t* inner = scratch + kFecEnvelopeHeaderBytes;
  if (inner_wire_size < kAlignedEnvelopeHeaderBytes + 1U +
                            kAnimationPipelineCrcBytes ||
      inner_wire_size > semantic_decoded_capacity - kFecEnvelopeHeaderBytes ||
      inner_wire_size % kSpiDmaAlignmentBytes != 0U ||
      inner[0] != static_cast<std::uint8_t>(ReceiverCommand::AlignedEnvelope) ||
      inner[1] != kAlignedEnvelopeVersion) {
    return false;
  }
  const std::size_t semantic_size =
      (static_cast<std::size_t>(inner[2]) << 8U) | inner[3];
  const std::size_t maximum_semantic_size = fec_v7
      ? kFecEnvelopeMaxSemanticBytes
      : fec_rs
      ? kFecV6EnvelopeMaxSemanticBytes
      : fec_v4
          ? kFecV4EnvelopeMaxSemanticBytes
          : fec_v3
              ? kFecV3EnvelopeMaxSemanticBytes
              : kFecV2EnvelopeMaxSemanticBytes;
  if (semantic_size == 0U || semantic_size > maximum_semantic_size) {
    return false;
  }
  const std::size_t inner_unpadded = kAlignedEnvelopeHeaderBytes +
      semantic_size + kAnimationPipelineCrcBytes;
  const std::size_t inner_padding =
      (kSpiDmaAlignmentBytes - inner_unpadded % kSpiDmaAlignmentBytes) %
      kSpiDmaAlignmentBytes;
  const std::size_t canonical_inner_wire_size = inner_unpadded + inner_padding;
  if (inner_wire_size != canonical_inner_wire_size) return false;
  std::size_t required_codewords =
      (kFecEnvelopeHeaderBytes + inner_wire_size + data_bytes - 1U) /
      data_bytes;
  if (fec_v7) ++required_codewords;
  if (fec_rs || fec_v4 || fec_v3) {
    required_codewords += (4U - required_codewords % 4U) % 4U;
  } else if (required_codewords % 2U != 0U) {
    ++required_codewords;
  }
  if (required_codewords != codewords) {
    return false;
  }
  for (std::size_t index = kFecEnvelopeHeaderBytes + inner_wire_size;
       index < semantic_decoded_capacity; ++index) {
    if (scratch[index] != 0U) return false;
  }
  if (!receiver_packet_crc_valid(inner, inner_wire_size)) {
    if (report != nullptr) {
      report->result = ReceiverPacketDecodeResult::FecSemanticCrcError;
    }
    return false;
  }
  if (fec_v7 && !outer_parity_unavailable &&
      !fec_outer_parity_valid(scratch, codewords)) {
    // Every ordinary repair must converge on both the canonical inner packet
    // and its outer shard. The sole exception is an uncorrectable outer shard:
    // it is redundant, so a canonical inner packet remains safe to consume.
    if (report != nullptr) {
      report->result = ReceiverPacketDecodeResult::FecUncorrectable;
    }
    return false;
  }
  if (!decode_crc_valid_receiver_packet_payload(
          inner, inner_wire_size, payload)) {
    return false;
  }
  payload->fec_envelope = true;
  if (report != nullptr) {
    report->result = ReceiverPacketDecodeResult::Ok;
    report->corrected_codewords = corrected_codewords;
    report->corrected_bits = corrected_bits;
  }
  return true;
}

bool decode_crc_valid_receiver_packet_payload(
    const std::uint8_t* packet,
    std::size_t packet_size,
    ReceiverPacketPayload* payload) {
  if (payload == nullptr) return false;
  *payload = {};
  if (packet == nullptr ||
      packet_size < 1U + kAnimationPipelineCrcBytes ||
      packet_size > kAnimationPipelineMaxTransactionBytes) {
    return false;
  }

  const std::size_t outer_payload_size =
      packet_size - kAnimationPipelineCrcBytes;
  if (packet[0] !=
      static_cast<std::uint8_t>(ReceiverCommand::AlignedEnvelope)) {
    payload->data = packet;
    payload->size = outer_payload_size;
    return true;
  }

  if (packet_size % kSpiDmaAlignmentBytes != 0U ||
      outer_payload_size < kAlignedEnvelopeHeaderBytes + 1U ||
      packet[1] != kAlignedEnvelopeVersion) {
    return false;
  }
  const std::size_t semantic_size =
      (static_cast<std::size_t>(packet[2]) << 8U) | packet[3];
  if (semantic_size == 0U ||
      semantic_size > kAlignedEnvelopeMaxSemanticBytes) {
    return false;
  }
  const std::size_t unpadded_size =
      kAlignedEnvelopeHeaderBytes + semantic_size +
      kAnimationPipelineCrcBytes;
  const std::size_t padding_size =
      (kSpiDmaAlignmentBytes - unpadded_size % kSpiDmaAlignmentBytes) %
      kSpiDmaAlignmentBytes;
  if (packet_size != unpadded_size + padding_size) return false;
  const std::size_t padding_offset =
      kAlignedEnvelopeHeaderBytes + semantic_size;
  for (std::size_t index = 0; index < padding_size; ++index) {
    if (packet[padding_offset + index] != 0U) return false;
  }
  payload->data = packet + kAlignedEnvelopeHeaderBytes;
  payload->size = semantic_size;
  payload->aligned_envelope = true;
  return true;
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
      (size != kStatusBytesV3 && size != kStatusBytesV7 &&
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
