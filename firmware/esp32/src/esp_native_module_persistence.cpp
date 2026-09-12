#include "ledgrid/esp_native_module.hpp"

#if LEDGRID_ENABLE_RECEIVER_NATIVE_MODULES

#include <atomic>
#include <cstring>

#include "esp_attr.h"
#include "esp_system.h"
#include "nvs.h"
#include "nvs_flash.h"

namespace ledgrid {
namespace {

constexpr std::size_t kLedgerBytes = 344;

// Only this transient execution marker belongs in retained RAM. NVS remains
// authoritative for the ledger and quarantine. Committing/erasing NVS on every
// render forces flash page maintenance into the 25 ms module watchdog window.
// RTC_NOINIT survives esp_restart (our watchdog), panic, and watchdog resets;
// it is deliberately discarded after power loss/brownout, which is not proof
// that the module crashed. No constructor may initialize this storage at boot.
struct RetainedNativePhase {
  std::uint32_t committed;
  std::uint8_t payload_and_phase[33];
  std::uint32_t checksum;
};
constexpr std::uint32_t kRetainedPhaseMagic = 0x4c475031U;  // LGP1
RTC_NOINIT_ATTR volatile RetainedNativePhase retained_phase;

void clear_retained_phase() {
  retained_phase.committed = 0;
  std::atomic_thread_fence(std::memory_order_seq_cst);
}

void write_u16(std::uint8_t* output, std::uint16_t value) {
  output[0] = static_cast<std::uint8_t>(value >> 8U);
  output[1] = static_cast<std::uint8_t>(value);
}

void write_u32(std::uint8_t* output, std::uint32_t value) {
  output[0] = static_cast<std::uint8_t>(value >> 24U);
  output[1] = static_cast<std::uint8_t>(value >> 16U);
  output[2] = static_cast<std::uint8_t>(value >> 8U);
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

std::uint32_t read_u32(const std::uint8_t* input) {
  return (static_cast<std::uint32_t>(input[0]) << 24U) |
         (static_cast<std::uint32_t>(input[1]) << 16U) |
         (static_cast<std::uint32_t>(input[2]) << 8U) | input[3];
}

std::uint64_t read_u64(const std::uint8_t* input) {
  std::uint64_t value = 0;
  for (std::size_t index = 0; index < 8; ++index) value = (value << 8U) | input[index];
  return value;
}

std::uint32_t checksum(const std::uint8_t* bytes, std::size_t size) {
  std::uint32_t value = 2166136261U;
  for (std::size_t index = 0; index < size; ++index)
    value = (value ^ bytes[index]) * 16777619U;
  return value;
}

bool load_retained_phase(std::uint8_t payload[32], NativeModulePhase* phase) {
  if (retained_phase.committed != kRetainedPhaseMagic) return true;
  std::atomic_thread_fence(std::memory_order_seq_cst);
  std::uint8_t bytes[33];
  for (std::size_t i = 0; i < sizeof(bytes); ++i)
    bytes[i] = retained_phase.payload_and_phase[i];
  if (checksum(bytes, sizeof(bytes)) != retained_phase.checksum ||
      bytes[32] == static_cast<std::uint8_t>(NativeModulePhase::None) ||
      bytes[32] > static_cast<std::uint8_t>(NativeModulePhase::Unload))
    return false;
  std::memcpy(payload, bytes, 32);
  *phase = static_cast<NativeModulePhase>(bytes[32]);
  return true;
}

void encode_descriptor(std::uint8_t* output,
                       const NativeModuleDescriptor& descriptor) {
  std::memcpy(output, descriptor.bundle_digest, 32);
  std::memcpy(output + 32, descriptor.payload_digest, 32);
  write_u32(output + 64, descriptor.payload_size);
  write_u16(output + 68, descriptor.abi);
  output[70] = descriptor.target;
  write_u16(output + 71, descriptor.global_strips);
  output[73] = descriptor.local_strips;
  write_u16(output + 74, descriptor.leds_per_strip);
  write_u16(output + 76, descriptor.global_strip_offset);
  write_u16(output + 78, descriptor.cadence_hz);
  write_u32(output + 80, descriptor.parameter_schema_revision);
  output[84] = descriptor.flags;
}

void decode_descriptor(const std::uint8_t* input,
                       NativeModuleDescriptor* descriptor) {
  std::memcpy(descriptor->bundle_digest, input, 32);
  std::memcpy(descriptor->payload_digest, input + 32, 32);
  descriptor->payload_size = read_u32(input + 64);
  descriptor->abi = read_u16(input + 68);
  descriptor->target = input[70];
  descriptor->global_strips = read_u16(input + 71);
  descriptor->local_strips = input[73];
  descriptor->leds_per_strip = read_u16(input + 74);
  descriptor->global_strip_offset = read_u16(input + 76);
  descriptor->cadence_hz = read_u16(input + 78);
  descriptor->parameter_schema_revision = read_u32(input + 80);
  descriptor->flags = input[84];
}

void encode_binding(std::uint8_t* output,
                    const NativeModuleBinding& binding) {
  output[0] = binding.present ? 1 : 0;
  if (binding.present) encode_descriptor(output + 1, binding.descriptor);
}

bool decode_binding(const std::uint8_t* input,
                    NativeModuleBinding* binding) {
  if (binding == nullptr || input[0] > 1) return false;
  *binding = {};
  binding->present = input[0] == 1;
  if (binding->present) {
    decode_descriptor(input + 1, &binding->descriptor);
  } else {
    std::uint8_t combined = 0;
    for (std::size_t index = 1; index < 86; ++index) combined |= input[index];
    if (combined != 0) return false;
  }
  return true;
}

}  // namespace

bool NvsNativeModulePersistence::begin() {
  const esp_reset_reason_t reason = esp_reset_reason();
  if (reason == ESP_RST_POWERON || reason == ESP_RST_BROWNOUT ||
      reason == ESP_RST_PWR_GLITCH) clear_retained_phase();
  ready_ = nvs_flash_init() == ESP_OK;
  return ready_;
}

bool NvsNativeModulePersistence::load(
    NativeModuleLedger* ledger, std::uint8_t quarantined_payload[32],
    std::uint8_t attributed_payload[32],
    NativeModulePhase* attributed_phase) {
  if (!ready_ || ledger == nullptr || quarantined_payload == nullptr ||
      attributed_payload == nullptr || attributed_phase == nullptr) return false;
  *ledger = {};
  std::memset(quarantined_payload, 0, 32);
  std::memset(attributed_payload, 0, 32);
  *attributed_phase = NativeModulePhase::None;
  nvs_handle_t handle = 0;
  const esp_err_t opened = nvs_open("ledgrid_native", NVS_READONLY, &handle);
  if (opened == ESP_ERR_NVS_NOT_FOUND)
    return load_retained_phase(attributed_payload, attributed_phase);
  if (opened != ESP_OK) return false;
  std::uint8_t bytes[kLedgerBytes] = {};
  std::size_t size = sizeof(bytes);
  const esp_err_t loaded = nvs_get_blob(handle, "ledger", bytes, &size);
  if (loaded != ESP_ERR_NVS_NOT_FOUND) {
    if (loaded != ESP_OK || size != sizeof(bytes) ||
        std::memcmp(bytes, "LGNS", 4) != 0 || bytes[4] != 1 ||
        checksum(bytes, 340) != read_u32(bytes + 340)) {
      nvs_close(handle);
      return false;
    }
    ledger->generation = read_u64(bytes + 8);
    if (!decode_binding(bytes + 16, &ledger->active) ||
        !decode_binding(bytes + 102, &ledger->staged) ||
        !decode_binding(bytes + 188, &ledger->rollback)) {
      nvs_close(handle);
      return false;
    }
    std::memcpy(quarantined_payload, bytes + 274, 32);
  }
  std::size_t payload_size = 32;
  if (nvs_get_blob(handle, "phase_payload", attributed_payload,
                   &payload_size) == ESP_OK) {
    std::uint8_t phase = 0;
    if (payload_size != 32 || nvs_get_u8(handle, "phase", &phase) != ESP_OK ||
        phase > static_cast<std::uint8_t>(NativeModulePhase::Unload)) {
      nvs_close(handle);
      return false;
    }
    *attributed_phase = static_cast<NativeModulePhase>(phase);
    legacy_phase_present_ = true;
  }
  nvs_close(handle);
  // Consume old firmware's NVS marker through the normal quarantine path before
  // clearing its keys once. New callbacks never write these keys again.
  return legacy_phase_present_ ||
         load_retained_phase(attributed_payload, attributed_phase);
}

bool NvsNativeModulePersistence::save(
    const NativeModuleLedger& ledger,
    const std::uint8_t quarantined_payload[32]) {
  if (!ready_) return false;
  std::uint8_t bytes[kLedgerBytes] = {};
  std::memcpy(bytes, "LGNS", 4);
  bytes[4] = 1;
  write_u64(bytes + 8, ledger.generation);
  encode_binding(bytes + 16, ledger.active);
  encode_binding(bytes + 102, ledger.staged);
  encode_binding(bytes + 188, ledger.rollback);
  if (quarantined_payload != nullptr)
    std::memcpy(bytes + 274, quarantined_payload, 32);
  write_u32(bytes + 340, checksum(bytes, 340));
  nvs_handle_t handle = 0;
  if (nvs_open("ledgrid_native", NVS_READWRITE, &handle) != ESP_OK) return false;
  esp_err_t result = nvs_set_blob(handle, "ledger", bytes, sizeof(bytes));
  if (result == ESP_OK) result = nvs_commit(handle);
  nvs_close(handle);
  return result == ESP_OK;
}

bool NvsNativeModulePersistence::mark_phase(
    const std::uint8_t payload[32], NativeModulePhase phase) {
  if (!ready_ || payload == nullptr || phase == NativeModulePhase::None ||
      phase > NativeModulePhase::Unload) return false;
  std::uint8_t bytes[33];
  std::memcpy(bytes, payload, 32);
  bytes[32] = static_cast<std::uint8_t>(phase);
  clear_retained_phase();
  for (std::size_t i = 0; i < sizeof(bytes); ++i)
    retained_phase.payload_and_phase[i] = bytes[i];
  retained_phase.checksum = checksum(bytes, sizeof(bytes));
  // Publish last, before native code or its watchdog can execute. A reset in
  // the preceding preparation cannot attribute code that has not been called.
  std::atomic_thread_fence(std::memory_order_seq_cst);
  retained_phase.committed = kRetainedPhaseMagic;
  std::atomic_thread_fence(std::memory_order_seq_cst);
  return true;
}

bool NvsNativeModulePersistence::clear_phase() {
  if (!ready_) return false;
  clear_retained_phase();
  if (!legacy_phase_present_) return true;
  nvs_handle_t handle = 0;
  if (nvs_open("ledgrid_native", NVS_READWRITE, &handle) != ESP_OK) return false;
  esp_err_t first = nvs_erase_key(handle, "phase_payload");
  esp_err_t second = nvs_erase_key(handle, "phase");
  const bool keys_ok = (first == ESP_OK || first == ESP_ERR_NVS_NOT_FOUND) &&
                       (second == ESP_OK || second == ESP_ERR_NVS_NOT_FOUND);
  const esp_err_t committed = keys_ok ? nvs_commit(handle) : ESP_FAIL;
  nvs_close(handle);
  if (!keys_ok || committed != ESP_OK) return false;
  legacy_phase_present_ = false;
  return true;
}

}  // namespace ledgrid

#endif
