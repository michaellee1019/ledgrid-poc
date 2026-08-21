#include "ledgrid/esp_installation_profile_store.hpp"

#include <algorithm>
#include <cerrno>
#include <cstring>
#include <dirent.h>
#include <sys/stat.h>
#include <unistd.h>

#include "esp_spiffs.h"
#include "nvs.h"
#include "nvs_flash.h"

namespace ledgrid {
namespace {

constexpr char kPartitionLabel[] = "profilecache";
constexpr char kBasePath[] = "/profilecache";
constexpr char kPartPath[] = "/profilecache/upload.part";
constexpr char kPartMetaPath[] = "/profilecache/upload.meta.part";
constexpr std::size_t kMetadataBytes = 48;
constexpr std::size_t kLedgerBytes = 224;

char hex_digit(std::uint8_t value) {
  return value < 10 ? static_cast<char>('0' + value)
                    : static_cast<char>('a' + value - 10);
}
int hex_value(char value) {
  if (value >= '0' && value <= '9') return value - '0';
  if (value >= 'a' && value <= 'f') return value - 'a' + 10;
  return -1;
}
bool digest_from_meta_name(const char* name, std::uint8_t digest[32]) {
  if (name == nullptr || std::strlen(name) != 69 ||
      std::strcmp(name + 64, ".meta") != 0) return false;
  for (std::size_t index = 0; index < 32; ++index) {
    const int high = hex_value(name[index * 2]);
    const int low = hex_value(name[index * 2 + 1]);
    if (high < 0 || low < 0) return false;
    digest[index] = static_cast<std::uint8_t>((high << 4) | low);
  }
  return true;
}
void write_u32(std::uint8_t* output, std::uint32_t value) {
  output[0] = static_cast<std::uint8_t>(value >> 24U);
  output[1] = static_cast<std::uint8_t>(value >> 16U);
  output[2] = static_cast<std::uint8_t>(value >> 8U);
  output[3] = static_cast<std::uint8_t>(value);
}
void write_u64(std::uint8_t* output, std::uint64_t value) {
  for (std::size_t index = 0; index < 8; ++index)
    output[index] = static_cast<std::uint8_t>(value >> (56U - index * 8U));
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
bool equal_digest(const std::uint8_t* left, const std::uint8_t* right) {
  return std::memcmp(left, right, 32) == 0;
}
bool is_pinned(
    const std::uint8_t digest[32], const InstallationProfileLedger& pins) {
  for (const auto* binding : {&pins.active, &pins.staged, &pins.rollback}) {
    if (binding->present && equal_digest(digest, binding->payload_digest)) return true;
  }
  return false;
}

void encode_binding(
    std::uint8_t* output, const InstallationProfileBinding& binding) {
  output[0] = binding.present ? 1 : 0;
  if (binding.present) {
    std::memcpy(output + 1, binding.global_id, 32);
    std::memcpy(output + 33, binding.payload_digest, 32);
  }
}
bool decode_binding(
    const std::uint8_t* input, InstallationProfileBinding* binding) {
  if (input[0] > 1 || binding == nullptr) return false;
  *binding = {};
  binding->present = input[0] == 1;
  if (binding->present) {
    std::memcpy(binding->global_id, input + 1, 32);
    std::memcpy(binding->payload_digest, input + 33, 32);
  } else {
    std::uint8_t combined = 0;
    for (std::size_t index = 1; index < 65; ++index) combined |= input[index];
    if (combined != 0) return false;
  }
  return true;
}

}  // namespace

EspInstallationProfileStore::~EspInstallationProfileStore() {
  abort_part();
  if (ready_) esp_vfs_spiffs_unregister(kPartitionLabel);
}

bool EspInstallationProfileStore::begin() {
  esp_vfs_spiffs_conf_t config{};
  config.base_path = kBasePath;
  config.partition_label = kPartitionLabel;
  config.max_files = 12;
  config.format_if_mount_failed = true;
  ready_ = esp_vfs_spiffs_register(&config) == ESP_OK;
  if (!ready_) return false;
  abort_part();
  DIR* directory = opendir(kBasePath);
  if (directory != nullptr) {
    while (const dirent* entry = readdir(directory)) {
      std::uint8_t digest[32] = {};
      std::uint32_t size = 0, access = 0;
      if (digest_from_meta_name(entry->d_name, digest) &&
          read_metadata(digest, &size, &access)) {
        access_counter_ = std::max(access_counter_, access + 1U);
      }
    }
    closedir(directory);
  }
  return true;
}

std::uint32_t EspInstallationProfileStore::capacity_bytes() const {
  std::size_t total = 0, used = 0;
  return ready_ && esp_spiffs_info(kPartitionLabel, &total, &used) == ESP_OK
      ? static_cast<std::uint32_t>(std::min<std::size_t>(total, UINT32_MAX)) : 0;
}

std::uint32_t EspInstallationProfileStore::used_bytes() const {
  std::size_t total = 0, used = 0;
  return ready_ && esp_spiffs_info(kPartitionLabel, &total, &used) == ESP_OK
      ? static_cast<std::uint32_t>(std::min<std::size_t>(used, UINT32_MAX)) : 0;
}

bool EspInstallationProfileStore::path_for(
    const std::uint8_t digest[32], const char* suffix,
    char* output, std::size_t output_size) const {
  if (digest == nullptr || suffix == nullptr || output == nullptr ||
      output_size < std::strlen(kBasePath) + 1U + 64U +
                        std::strlen(suffix) + 1U) return false;
  std::size_t cursor = static_cast<std::size_t>(
      std::snprintf(output, output_size, "%s/", kBasePath));
  for (std::size_t index = 0; index < 32; ++index) {
    output[cursor++] = hex_digit(digest[index] >> 4U);
    output[cursor++] = hex_digit(digest[index] & 0x0FU);
  }
  std::strcpy(output + cursor, suffix);
  return true;
}

bool EspInstallationProfileStore::write_metadata(
    const std::uint8_t digest[32], std::uint32_t size,
    std::uint32_t access, const char* path) const {
  std::uint8_t bytes[kMetadataBytes] = {};
  std::memcpy(bytes, "LGPM", 4);
  write_u32(bytes + 4, size);
  write_u32(bytes + 8, access);
  std::memcpy(bytes + 12, digest, 32);
  write_u32(bytes + 44, checksum(bytes, 44));
  std::FILE* file = std::fopen(path, "wb");
  if (file == nullptr) return false;
  const bool ok = std::fwrite(bytes, 1, sizeof(bytes), file) == sizeof(bytes) &&
                  std::fflush(file) == 0 && fsync(fileno(file)) == 0;
  std::fclose(file);
  return ok;
}

bool EspInstallationProfileStore::read_metadata(
    const std::uint8_t digest[32], std::uint32_t* size,
    std::uint32_t* access) const {
  char path[112] = {};
  if (!path_for(digest, ".meta", path, sizeof(path))) return false;
  std::uint8_t bytes[kMetadataBytes] = {};
  std::FILE* file = std::fopen(path, "rb");
  if (file == nullptr) return false;
  const bool read = std::fread(bytes, 1, sizeof(bytes), file) == sizeof(bytes);
  std::fclose(file);
  if (!read || std::memcmp(bytes, "LGPM", 4) != 0 ||
      checksum(bytes, 44) != read_u32(bytes + 44) ||
      !equal_digest(bytes + 12, digest)) return false;
  if (size != nullptr) *size = read_u32(bytes + 4);
  if (access != nullptr) *access = read_u32(bytes + 8);
  return true;
}

bool EspInstallationProfileStore::probe(
    const std::uint8_t digest[32], std::uint32_t* size) const {
  std::uint32_t metadata_size = 0;
  char path[112] = {};
  struct stat info{};
  if (!ready_ || !read_metadata(digest, &metadata_size, nullptr) ||
      !path_for(digest, ".bin", path, sizeof(path)) || stat(path, &info) != 0 ||
      info.st_size != static_cast<off_t>(metadata_size)) return false;
  if (size != nullptr) *size = metadata_size;
  return true;
}

bool EspInstallationProfileStore::touch(const std::uint8_t digest[32]) {
  std::uint32_t metadata_size = 0;
  char metadata_path[112] = {};
  if (!ready_ || !read_metadata(digest, &metadata_size, nullptr) ||
      !path_for(digest, ".meta", metadata_path, sizeof(metadata_path)) ||
      !write_metadata(digest, metadata_size, access_counter_++, metadata_path))
    return false;
  ++mutation_generation_;
  return true;
}

bool EspInstallationProfileStore::can_stage(
    std::uint32_t size, const InstallationProfileLedger& pins,
    std::uint32_t* reclaimable) const {
  if (!ready_) return false;
  std::uint32_t reclaim = 0;
  DIR* directory = opendir(kBasePath);
  if (directory != nullptr) {
    while (const dirent* entry = readdir(directory)) {
      std::uint8_t digest[32] = {};
      std::uint32_t entry_size = 0;
      if (digest_from_meta_name(entry->d_name, digest) &&
          !is_pinned(digest, pins) &&
          read_metadata(digest, &entry_size, nullptr)) {
        reclaim = UINT32_MAX - reclaim < entry_size ? UINT32_MAX
                                                     : reclaim + entry_size;
      }
    }
    closedir(directory);
  }
  if (reclaimable != nullptr) *reclaimable = reclaim;
  const std::uint32_t capacity = capacity_bytes();
  const std::uint32_t used = used_bytes();
  const std::uint32_t usable = capacity > reserve_bytes()
      ? capacity - reserve_bytes() : 0;
  return size <= usable && used - std::min(used, reclaim) <= usable - size;
}

bool EspInstallationProfileStore::begin_part(
    const std::uint8_t digest[32], std::uint32_t size,
    const InstallationProfileLedger& pins, std::uint32_t* evicted) {
  std::uint32_t reclaimable = 0;
  if (!can_stage(size, pins, &reclaimable)) return false;
  abort_part();
  std::uint32_t count = 0;
  while (used_bytes() + size > capacity_bytes() - reserve_bytes()) {
    std::uint8_t selected[32] = {};
    std::uint32_t oldest = UINT32_MAX;
    bool found = false;
    DIR* directory = opendir(kBasePath);
    if (directory != nullptr) {
      while (const dirent* entry = readdir(directory)) {
        std::uint8_t candidate[32] = {};
        std::uint32_t entry_size = 0, access = 0;
        if (digest_from_meta_name(entry->d_name, candidate) &&
            !is_pinned(candidate, pins) &&
            read_metadata(candidate, &entry_size, &access) && access < oldest) {
          oldest = access;
          std::memcpy(selected, candidate, 32);
          found = true;
        }
      }
      closedir(directory);
    }
    if (!found || !remove(selected)) return false;
    ++count;
  }
  part_file_ = std::fopen(kPartPath, "wb+");
  if (part_file_ == nullptr) return false;
  std::memcpy(part_digest_, digest, 32);
  part_size_ = size;
  part_received_ = 0;
  if (evicted != nullptr) *evicted = count;
  ++mutation_generation_;
  return true;
}

bool EspInstallationProfileStore::write_part(
    std::uint32_t offset, const std::uint8_t* data, std::size_t size) {
  if (part_file_ == nullptr || data == nullptr || offset != part_received_ ||
      size > part_size_ - offset ||
      std::fseek(part_file_, static_cast<long>(offset), SEEK_SET) != 0 ||
      std::fwrite(data, 1, size, part_file_) != size) return false;
  part_received_ += static_cast<std::uint32_t>(size);
  return true;
}

bool EspInstallationProfileStore::read_part(
    std::uint32_t offset, std::uint8_t* data, std::size_t size) const {
  if (part_file_ == nullptr || data == nullptr || offset > part_received_ ||
      size > part_received_ - offset || std::fflush(part_file_) != 0 ||
      std::fseek(part_file_, static_cast<long>(offset), SEEK_SET) != 0)
    return false;
  return std::fread(data, 1, size, part_file_) == size;
}

bool EspInstallationProfileStore::commit_part(const std::uint8_t digest[32]) {
  if (part_file_ == nullptr || part_received_ != part_size_ ||
      !equal_digest(digest, part_digest_) || std::fflush(part_file_) != 0 ||
      fsync(fileno(part_file_)) != 0) return false;
  std::fclose(part_file_);
  part_file_ = nullptr;
  if (!write_metadata(digest, part_size_, access_counter_++, kPartMetaPath)) {
    abort_part();
    return false;
  }
  char data[112] = {}, meta[112] = {};
  if (!path_for(digest, ".bin", data, sizeof(data)) ||
      !path_for(digest, ".meta", meta, sizeof(meta))) return false;
  unlink(data);
  unlink(meta);
  if (rename(kPartPath, data) != 0 || rename(kPartMetaPath, meta) != 0) {
    unlink(data);
    unlink(kPartPath);
    unlink(kPartMetaPath);
    return false;
  }
  part_size_ = part_received_ = 0;
  std::memset(part_digest_, 0, 32);
  ++mutation_generation_;
  return true;
}

void EspInstallationProfileStore::abort_part() {
  if (part_file_ != nullptr) {
    std::fclose(part_file_);
    part_file_ = nullptr;
  }
  unlink(kPartPath);
  unlink(kPartMetaPath);
  part_size_ = part_received_ = 0;
  std::memset(part_digest_, 0, 32);
  ++mutation_generation_;
}

bool EspInstallationProfileStore::read_committed(
    const std::uint8_t digest[32], std::uint32_t offset,
    std::uint8_t* data, std::size_t size) const {
  std::uint32_t total = 0;
  char path[112] = {};
  if (data == nullptr || !probe(digest, &total) || offset > total ||
      size > total - offset || !path_for(digest, ".bin", path, sizeof(path)))
    return false;
  std::FILE* file = std::fopen(path, "rb");
  if (file == nullptr) return false;
  const bool ok = std::fseek(file, static_cast<long>(offset), SEEK_SET) == 0 &&
                  std::fread(data, 1, size, file) == size;
  std::fclose(file);
  return ok;
}

bool EspInstallationProfileStore::remove(const std::uint8_t digest[32]) {
  char data[112] = {}, meta[112] = {};
  if (!path_for(digest, ".bin", data, sizeof(data)) ||
      !path_for(digest, ".meta", meta, sizeof(meta))) return false;
  const bool data_ok = unlink(data) == 0 || errno == ENOENT;
  const bool meta_ok = unlink(meta) == 0 || errno == ENOENT;
  if (data_ok && meta_ok) ++mutation_generation_;
  return data_ok && meta_ok;
}

bool NvsInstallationProfilePersistence::begin() {
  esp_err_t result = nvs_flash_init();
  if (result == ESP_ERR_NVS_NO_FREE_PAGES || result == ESP_ERR_NVS_NEW_VERSION_FOUND) {
    result = nvs_flash_erase();
    if (result == ESP_OK) result = nvs_flash_init();
  }
  ready_ = result == ESP_OK;
  return ready_;
}

bool NvsInstallationProfilePersistence::load(InstallationProfileLedger* ledger) {
  if (!ready_ || ledger == nullptr) return false;
  *ledger = {};
  nvs_handle_t handle = 0;
  const esp_err_t open_result =
      nvs_open("ledgrid_profile", NVS_READONLY, &handle);
  if (open_result == ESP_ERR_NVS_NOT_FOUND) return true;
  if (open_result != ESP_OK) return false;
  std::uint8_t bytes[kLedgerBytes] = {};
  std::size_t size = sizeof(bytes);
  const esp_err_t result = nvs_get_blob(handle, "ledger", bytes, &size);
  nvs_close(handle);
  if (result == ESP_ERR_NVS_NOT_FOUND) return true;
  if (result != ESP_OK || size != sizeof(bytes) ||
      std::memcmp(bytes, "LGPS", 4) != 0 || bytes[4] != 1 ||
      checksum(bytes, 220) != read_u32(bytes + 220)) return false;
  ledger->generation = read_u64(bytes + 8);
  return decode_binding(bytes + 16, &ledger->active) &&
         decode_binding(bytes + 81, &ledger->staged) &&
         decode_binding(bytes + 146, &ledger->rollback);
}

bool NvsInstallationProfilePersistence::save(
    const InstallationProfileLedger& ledger) {
  if (!ready_) return false;
  std::uint8_t bytes[kLedgerBytes] = {};
  std::memcpy(bytes, "LGPS", 4);
  bytes[4] = 1;
  write_u64(bytes + 8, ledger.generation);
  encode_binding(bytes + 16, ledger.active);
  encode_binding(bytes + 81, ledger.staged);
  encode_binding(bytes + 146, ledger.rollback);
  write_u32(bytes + 220, checksum(bytes, 220));
  nvs_handle_t handle = 0;
  if (nvs_open("ledgrid_profile", NVS_READWRITE, &handle) != ESP_OK) return false;
  esp_err_t result = nvs_set_blob(handle, "ledger", bytes, sizeof(bytes));
  if (result == ESP_OK) result = nvs_commit(handle);
  nvs_close(handle);
  return result == ESP_OK;
}

}  // namespace ledgrid
