#include "ledgrid/protocol.hpp"

#include <algorithm>
#include <array>
#include <cstring>
#include <iterator>

namespace ledgrid {
namespace {

constexpr std::array<std::uint32_t, 256> status_crc32_table() {
  std::array<std::uint32_t, 256> table{};
  for (std::size_t i = 0; i < table.size(); ++i) {
    std::uint32_t value = static_cast<std::uint32_t>(i);
    for (unsigned bit = 0; bit < 8; ++bit) {
      value = (value >> 1U) ^ ((value & 1U) ? 0xEDB88320U : 0U);
    }
    table[i] = value;
  }
  return table;
}
constexpr auto kStatusCrc32Table = status_crc32_table();

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

bool valid_installed_config(const std::uint8_t* data, std::size_t length) {
 if (!data || length != 8 || data[0] != 7 || data[5] >= 5) return false;
 const std::uint16_t leds=(data[2]<<8)|data[3];
 const std::uint16_t offset=(data[6]<<8)|data[7];
 return leds == 138 && offset == data[5]*8 && data[1] == (data[5]==4?1:8) && (data[4] & ~1U) == 0;
}
bool configure_host_output(const std::uint8_t* data, std::size_t length,
 OutputConfiguration* output, std::uint8_t* pixels, std::size_t capacity) {
 if (!output || !pixels || !valid_installed_config(data,length) || capacity < data[1]*138U*3U) return false;
 if (output->strip_count != data[1] || output->leds_per_strip != 138) {
  std::memset(pixels,0,capacity);
  output->strip_count=data[1]; output->leds_per_strip=138;
 }
 return true;
}
bool encode_receiver_status_v8(const ReceiverStatusV7& status, std::uint8_t* output, std::size_t output_size) {
 if (!output || output_size < kStatusBytesV8) return false;
 std::memset(output, 0, kStatusBytesV8);
 std::memcpy(output, "LGS8", 4); output[4] = 8;
 encode_v2_fields(status, output);
 write_u32(output + 64, status.capabilities);
 output[72] = status.last_result;
 output[68] = 2; // Host full-frame output; no receiver scene engine.
 write_u32(output + 80, status.global_strip_offset);
 output[312] = status.logical_receiver_id;
 output[313] = status.last_processed_command;
 output[314] = status.stagger_phases;
 write_u32(output + 316, status.operation_sequence);
  write_u32(output + 1216, status.fec_packets_received);
  write_u32(output + 1220, status.fec_packets_accepted);
  write_u32(output + 1224, status.fec_corrected_packets);
  write_u32(output + 1228, status.fec_corrected_codewords);
  write_u32(output + 1232, status.fec_uncorrectable_packets);
  write_u32(output + 1236, status.fec_semantic_crc_errors);
  write_u32(output + 1240, status.fec_framing_errors);
  write_u16(output + 1244, status.fec_last_decode_us);
  write_u16(output + 1246, status.fec_max_decode_us);
  std::uint32_t checksum = 0xFFFFFFFFU;
  for (std::size_t i = 0; i < kStatusBytesV7; ++i) {
    checksum = (checksum >> 8U) ^ kStatusCrc32Table[(checksum ^ output[i]) & 0xFFU];
  }
  write_u32(output + kStatusBytesV7, checksum ^ 0xFFFFFFFFU);
  return true;
}

std::uint16_t animation_pipeline_crc16_ccitt(const std::uint8_t* data, std::size_t size) {
 std::uint16_t crc=0xFFFF;
 for (std::size_t i=0;i<size;++i) { crc ^= static_cast<std::uint16_t>(data[i])<<8; for(unsigned b=0;b<8;++b) crc=(crc&0x8000)?(crc<<1)^0x1021:crc<<1; }
 return crc;
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
  if (!fec_v7_candidate) {
    if (fec_v2_candidate || fec_v3_candidate || fec_v4_candidate ||
        fec_v5_candidate || fec_v6_candidate) {
      if (report != nullptr) {
        report->fec_envelope_attempted = true;
        report->result = ReceiverPacketDecodeResult::InvalidFraming;
      }
      return false;
    }
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
  const std::size_t codewords =
      (packet_size - kFecWireHeaderBytes) / kFecCodewordBytes;
  constexpr bool diagonal = true;
  constexpr bool outer_parity = true;
  const std::size_t decoded_capacity = codewords * kFecDataBytes;
  if (scratch == nullptr || scratch_size < decoded_capacity ||
      codewords > kFecMaxCodewords || codewords % 4U != 0U) {
    return false;
  }
  std::uint16_t corrected_codewords = 0;
  std::uint16_t corrected_bits = 0;
  bool outer_parity_unavailable = false;
  // This fills scratch and verifies the complete inner CRC and outer parity.
  // A successful check skips every repair below, so scratch stays unchanged.
  const bool systematic_payload_valid = fec_v5_systematic_payload_valid(
      packet, codewords, scratch, kFecEnvelopeVersion, true, true);
  {
    const std::size_t matrix_offset = kFecEnvelopeHeaderBytes;
    constexpr std::size_t kMaximumCorrections = kFecParityBytes / 2U;
    // Clean installed frames are overwhelmingly the common case. Deinterleave
    // the systematic bytes and validate the complete canonical inner packet
    // before paying for 40,800 GF(256) syndrome operations. Any data, length,
    // padding, or semantic-CRC damage still enters the bounded RS decoder.
    // Parity-only damage is safe to ignore because it cannot change the
    // authenticated semantic payload.
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
            scratch, codewords, kFecEnvelopeVersion, true, true);
        if (recovered) {
          corrected_bits = static_cast<std::uint16_t>(
              corrected_bits + reconstructed_bits);
        }
      } else if (failed_block == data_codewords) {
        // Damage confined to the redundant outer shard cannot change the
        // canonical inner packet. Its CRC remains the semantic authority.
        recovered = fec_v5_decoded_payload_valid(
            scratch, codewords, kFecEnvelopeVersion, true, false);
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
                scratch, codewords, kFecEnvelopeVersion, true, true)) {
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
  }

  const std::size_t semantic_decoded_capacity =
      decoded_capacity - kFecOuterParityBytes;
  if (semantic_decoded_capacity < kFecEnvelopeHeaderBytes +
                             kAlignedEnvelopeHeaderBytes + 1U +
                             kAnimationPipelineCrcBytes ||
      scratch[0] != static_cast<std::uint8_t>(ReceiverCommand::AlignedEnvelope) ||
      scratch[1] != kFecEnvelopeVersion) {
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
  const std::size_t maximum_semantic_size = kFecEnvelopeMaxSemanticBytes;
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
      (kFecEnvelopeHeaderBytes + inner_wire_size + kFecDataBytes - 1U) /
      kFecDataBytes;
  ++required_codewords;
  required_codewords += (4U - required_codewords % 4U) % 4U;
  if (required_codewords != codewords) {
    return false;
  }
  for (std::size_t index = kFecEnvelopeHeaderBytes + inner_wire_size;
       index < semantic_decoded_capacity; ++index) {
    if (scratch[index] != 0U) return false;
  }
  if (!systematic_payload_valid &&
      !receiver_packet_crc_valid(inner, inner_wire_size)) {
    if (report != nullptr) {
      report->result = ReceiverPacketDecodeResult::FecSemanticCrcError;
    }
    return false;
  }
  if (!systematic_payload_valid && !outer_parity_unavailable &&
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
    return false;
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


} // namespace ledgrid
