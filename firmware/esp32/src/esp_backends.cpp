#include "ledgrid/esp_backends.hpp"

#include <algorithm>
#include <cerrno>
#include <cmath>
#include <cstring>
#include <dirent.h>
#include <sys/stat.h>
#include <unistd.h>

#include "esp_dlfcn.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_spiffs.h"
#include "mbedtls/ecp.h"
#include "mbedtls/ecdsa.h"
#include "mbedtls/sha256.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "ledgrid/typed_parameters.hpp"

#ifndef CONFIG_LEDGRID_TRUSTED_KEY_ID
#define CONFIG_LEDGRID_TRUSTED_KEY_ID ""
#endif
#ifndef CONFIG_LEDGRID_TRUSTED_P256_PUBLIC_KEY_HEX
#define CONFIG_LEDGRID_TRUSTED_P256_PUBLIC_KEY_HEX ""
#endif
#ifndef CONFIG_LEDGRID_ALLOW_UNSIGNED_DEVELOPMENT
#define CONFIG_LEDGRID_ALLOW_UNSIGNED_DEVELOPMENT 0
#endif

namespace ledgrid {
namespace {

constexpr char kBasePath[] = "/animcache";
constexpr char kPartPath[] = "/animcache/upload.part";
constexpr char kPartMetaPath[] = "/animcache/upload.meta.part";
constexpr char kStoreTag[] = "anim-store";
constexpr std::size_t kMetaBytes = 64;

void write_u16(std::uint8_t* p, std::uint16_t v) {
  p[0] = static_cast<std::uint8_t>(v >> 8U);
  p[1] = static_cast<std::uint8_t>(v);
}

void write_u32(std::uint8_t* p, std::uint32_t v) {
  p[0] = static_cast<std::uint8_t>(v >> 24U);
  p[1] = static_cast<std::uint8_t>(v >> 16U);
  p[2] = static_cast<std::uint8_t>(v >> 8U);
  p[3] = static_cast<std::uint8_t>(v);
}

std::uint16_t read_u16(const std::uint8_t* p) {
  return static_cast<std::uint16_t>((static_cast<std::uint16_t>(p[0]) << 8U) |
                                    p[1]);
}

std::uint32_t read_u32(const std::uint8_t* p) {
  return (static_cast<std::uint32_t>(p[0]) << 24U) |
         (static_cast<std::uint32_t>(p[1]) << 16U) |
         (static_cast<std::uint32_t>(p[2]) << 8U) | p[3];
}

std::uint32_t metadata_checksum(const std::uint8_t* data, std::size_t size) {
  std::uint32_t hash = 2166136261U;
  for (std::size_t i = 0; i < size; ++i) hash = (hash ^ data[i]) * 16777619U;
  return hash;
}

char hex_digit(std::uint8_t value) {
  return value < 10 ? static_cast<char>('0' + value)
                    : static_cast<char>('a' + value - 10);
}

int hex_value(char value) {
  if (value >= '0' && value <= '9') return value - '0';
  if (value >= 'a' && value <= 'f') return value - 'a' + 10;
  if (value >= 'A' && value <= 'F') return value - 'A' + 10;
  return -1;
}

bool digest_from_filename(const char* name, std::uint8_t digest[32]) {
  if (name == nullptr || std::strlen(name) != 69 ||
      std::strcmp(name + 64, ".meta") != 0) return false;
  for (std::size_t i = 0; i < 32; ++i) {
    const int high = hex_value(name[i * 2]);
    const int low = hex_value(name[i * 2 + 1]);
    if (high < 0 || low < 0) return false;
    digest[i] = static_cast<std::uint8_t>((high << 4) | low);
  }
  return true;
}

bool digest_path(const std::uint8_t digest[32], const char* suffix,
                 char* output, std::size_t output_size) {
  if (digest == nullptr || suffix == nullptr || output == nullptr ||
      output_size < std::strlen(kBasePath) + 1 + 64 + std::strlen(suffix) + 1)
    return false;
  std::size_t cursor = static_cast<std::size_t>(
      std::snprintf(output, output_size, "%s/", kBasePath));
  for (std::size_t i = 0; i < 32; ++i) {
    output[cursor++] = hex_digit(digest[i] >> 4U);
    output[cursor++] = hex_digit(digest[i] & 0x0FU);
  }
  std::strcpy(output + cursor, suffix);
  return true;
}

bool all_zero(const std::uint8_t digest[32]) {
  if (digest == nullptr) return true;
  std::uint8_t combined = 0;
  for (std::size_t i = 0; i < 32; ++i) combined |= digest[i];
  return combined == 0;
}

bool valid_key_id(const char* key_id) {
  if (key_id == nullptr || std::strlen(key_id) != kSigningKeyIdBytes ||
      std::memcmp(key_id, "key-", 4) != 0) return false;
  for (std::size_t i = 4; i < kSigningKeyIdBytes; ++i) {
    if (!((key_id[i] >= '0' && key_id[i] <= '9') ||
          (key_id[i] >= 'a' && key_id[i] <= 'f'))) return false;
  }
  return true;
}

bool write_meta_file(const char* path, const AssetDescriptor& descriptor,
                     std::uint32_t access) {
  std::uint8_t bytes[kMetaBytes] = {};
  std::memcpy(bytes, "LGM1", 4);
  write_u32(bytes + 4, descriptor.total_size);
  bytes[8] = static_cast<std::uint8_t>(descriptor.kind);
  write_u16(bytes + 9, descriptor.abi);
  write_u16(bytes + 11, descriptor.target);
  bytes[13] = descriptor.strip_count;
  write_u16(bytes + 14, descriptor.leds_per_strip);
  bytes[16] = descriptor.logical_device;
  write_u32(bytes + 17, access);
  std::memcpy(bytes + 21, descriptor.digest, 32);
  write_u32(bytes + 60, metadata_checksum(bytes, 60));
  std::FILE* file = std::fopen(path, "wb");
  if (file == nullptr) return false;
  const bool ok = std::fwrite(bytes, 1, sizeof(bytes), file) == sizeof(bytes) &&
                  std::fflush(file) == 0 && fsync(fileno(file)) == 0;
  std::fclose(file);
  return ok;
}

bool nvs_write_digest(const char* key, const std::uint8_t* digest) {
  nvs_handle_t handle = 0;
  if (nvs_open("ledgrid", NVS_READWRITE, &handle) != ESP_OK) return false;
  esp_err_t result = digest == nullptr ? nvs_erase_key(handle, key)
                                       : nvs_set_blob(handle, key, digest, 32);
  if (result == ESP_ERR_NVS_NOT_FOUND && digest == nullptr) result = ESP_OK;
  if (result == ESP_OK) result = nvs_commit(handle);
  nvs_close(handle);
  return result == ESP_OK;
}

bool nvs_read_digest(const char* key, std::uint8_t digest[32]) {
  nvs_handle_t handle = 0;
  if (nvs_open("ledgrid", NVS_READONLY, &handle) != ESP_OK) return false;
  std::size_t size = 32;
  const esp_err_t result = nvs_get_blob(handle, key, digest, &size);
  nvs_close(handle);
  return result == ESP_OK && size == 32;
}

std::uint32_t random_u32(std::uint32_t* state) {
  std::uint32_t value = state == nullptr ? 0x6D2B79F5U : *state;
  value ^= value << 13U;
  value ^= value >> 17U;
  value ^= value << 5U;
  if (state != nullptr) *state = value;
  return value;
}

void hsv_to_rgb(std::uint16_t hue, std::uint8_t saturation, std::uint8_t value,
                std::uint8_t rgb[3]) {
  const std::uint8_t region = static_cast<std::uint8_t>((hue / 10923U) % 6U);
  const std::uint16_t remainder = static_cast<std::uint16_t>(hue % 10923U);
  const std::uint8_t p = static_cast<std::uint8_t>(
      (static_cast<std::uint16_t>(value) * (255U - saturation)) / 255U);
  const std::uint8_t q = static_cast<std::uint8_t>(
      (static_cast<std::uint32_t>(value) *
       (255U - (static_cast<std::uint32_t>(saturation) * remainder) / 10923U)) /
      255U);
  const std::uint8_t t = static_cast<std::uint8_t>(
      (static_cast<std::uint32_t>(value) *
       (255U - (static_cast<std::uint32_t>(saturation) *
                (10923U - remainder)) / 10923U)) /
      255U);
  const std::uint8_t values[6][3] = {
      {value, t, p}, {q, value, p}, {p, value, t},
      {p, q, value}, {t, p, value}, {value, p, q}};
  std::memcpy(rgb, values[region], 3);
}

std::uint16_t rgb_to_565(std::uint8_t red, std::uint8_t green,
                         std::uint8_t blue) {
  return static_cast<std::uint16_t>(
      ((static_cast<std::uint16_t>(red) & 0xF8U) << 8U) |
      ((static_cast<std::uint16_t>(green) & 0xFCU) << 3U) |
      (static_cast<std::uint16_t>(blue) >> 3U));
}

float sin_f32(float radians) { return std::sin(radians); }
float cos_f32(float radians) { return std::cos(radians); }

const ledgrid_host_helpers_v1 kHostHelpers = {
    LEDGRID_ANIMATION_ABI_V1, random_u32, hsv_to_rgb,
    rgb_to_565, sin_f32, cos_f32};

}  // namespace

SpiffsAssetStore::~SpiffsAssetStore() {
  discard_part();
  if (ready_) esp_vfs_spiffs_unregister("animcache");
}

bool SpiffsAssetStore::begin() {
  esp_vfs_spiffs_conf_t config{};
  config.base_path = kBasePath;
  config.partition_label = "animcache";
  config.max_files = 32;
  config.format_if_mount_failed = true;
  ready_ = esp_vfs_spiffs_register(&config) == ESP_OK;
  if (!ready_) return false;
  discard_part();
  DIR* directory = opendir(kBasePath);
  if (directory != nullptr) {
    while (const dirent* entry = readdir(directory)) {
      std::uint8_t digest[32] = {};
      if (!digest_from_filename(entry->d_name, digest)) continue;
      char path[96] = {};
      if (!metadata_path(digest, path, sizeof(path))) continue;
      AssetDescriptor descriptor{};
      std::uint32_t access = 0;
      if (read_metadata(path, &descriptor, &access))
        access_counter_ = std::max(access_counter_, access + 1U);
    }
    closedir(directory);
  }
  return true;
}

bool SpiffsAssetStore::metadata_path(const std::uint8_t digest[32], char* output,
                                     std::size_t output_size) const {
  return digest_path(digest, ".meta", output, output_size);
}

bool SpiffsAssetStore::committed_path(const std::uint8_t digest[32], char* output,
                                      std::size_t output_size) const {
  return digest_path(digest, ".bin", output, output_size);
}

bool SpiffsAssetStore::read_metadata(const char* path, AssetDescriptor* descriptor,
                                     std::uint32_t* access) const {
  if (!ready_ || path == nullptr || descriptor == nullptr) return false;
  std::uint8_t bytes[kMetaBytes] = {};
  std::FILE* file = std::fopen(path, "rb");
  if (file == nullptr) return false;
  const bool read = std::fread(bytes, 1, sizeof(bytes), file) == sizeof(bytes);
  std::fclose(file);
  if (!read || std::memcmp(bytes, "LGM1", 4) != 0 ||
      read_u32(bytes + 60) != metadata_checksum(bytes, 60)) return false;
  descriptor->total_size = read_u32(bytes + 4);
  descriptor->kind = static_cast<AssetKind>(bytes[8]);
  descriptor->abi = read_u16(bytes + 9);
  descriptor->target = read_u16(bytes + 11);
  descriptor->strip_count = bytes[13];
  descriptor->leds_per_strip = read_u16(bytes + 14);
  descriptor->logical_device = bytes[16];
  std::memcpy(descriptor->digest, bytes + 21, 32);
  if (access != nullptr) *access = read_u32(bytes + 17);
  return true;
}

bool SpiffsAssetStore::probe(const std::uint8_t digest[32]) const {
  AssetDescriptor descriptor{};
  char meta[96] = {}, data[96] = {};
  struct stat info{};
  return metadata_path(digest, meta, sizeof(meta)) &&
         committed_path(digest, data, sizeof(data)) &&
         read_metadata(meta, &descriptor, nullptr) &&
         std::memcmp(descriptor.digest, digest, 32) == 0 &&
         stat(data, &info) == 0 && info.st_size == descriptor.total_size;
}

bool SpiffsAssetStore::touch(const std::uint8_t digest[32],
                             const AssetDescriptor& descriptor) const {
  char meta[96] = {};
  return metadata_path(digest, meta, sizeof(meta)) &&
         write_meta_file(meta, descriptor, access_counter_++);
}

bool SpiffsAssetStore::describe(const std::uint8_t digest[32],
                                AssetDescriptor* out) const {
  char meta[96] = {};
  if (!probe(digest) || !metadata_path(digest, meta, sizeof(meta)) ||
      !read_metadata(meta, out, nullptr)) return false;
  touch(digest, *out);
  return true;
}

std::uint32_t SpiffsAssetStore::used_bytes() const {
  std::size_t total = 0, used = 0;
  return ready_ && esp_spiffs_info("animcache", &total, &used) == ESP_OK
             ? static_cast<std::uint32_t>(std::min<std::size_t>(used, UINT32_MAX))
             : 0;
}

std::uint32_t SpiffsAssetStore::free_bytes() const {
  std::size_t total = 0, used = 0;
  return ready_ && esp_spiffs_info("animcache", &total, &used) == ESP_OK
             ? static_cast<std::uint32_t>(
                   std::min<std::size_t>(total - used, UINT32_MAX))
             : 0;
}

bool SpiffsAssetStore::ensure_space(std::uint32_t required) {
  while (free_bytes() < required) {
    DIR* directory = opendir(kBasePath);
    if (directory == nullptr) return false;
    std::uint8_t digests[32][32] = {};
    CacheEntryView entries[32] = {};
    std::size_t entry_count = 0;
    while (const dirent* entry = readdir(directory)) {
      std::uint8_t digest[32] = {};
      if (entry_count >= 32 || !digest_from_filename(entry->d_name, digest))
        continue;
      char meta[96] = {};
      AssetDescriptor descriptor{};
      std::uint32_t access = 0;
      if (metadata_path(digest, meta, sizeof(meta)) &&
          read_metadata(meta, &descriptor, &access)) {
        std::memcpy(digests[entry_count], digest, 32);
        entries[entry_count] = {digests[entry_count], access};
        ++entry_count;
      }
    }
    closedir(directory);
    const int selected = select_inactive_lru(
        entries, entry_count, all_zero(active_digest_) ? nullptr : active_digest_);
    if (selected < 0 || !remove(digests[selected])) return false;
  }
  return true;
}

bool SpiffsAssetStore::write_part_metadata(const AssetDescriptor& descriptor) {
  return write_meta_file(kPartMetaPath, descriptor, access_counter_++);
}

bool SpiffsAssetStore::begin_part(const AssetDescriptor& descriptor) {
  if (!ready_) return false;
  discard_part();
  if (!ensure_space(descriptor.total_size + kMinimumFilesystemReserveBytes +
                    4096U))
    return false;
  part_file_ = std::fopen(kPartPath, "wb+");
  if (part_file_ == nullptr || !write_part_metadata(descriptor)) {
    discard_part();
    return false;
  }
  part_descriptor_ = descriptor;
  return true;
}

bool SpiffsAssetStore::write_part(std::uint32_t offset, const std::uint8_t* data,
                                  std::size_t size) {
  return part_file_ != nullptr && data != nullptr &&
         std::fseek(part_file_, static_cast<long>(offset), SEEK_SET) == 0 &&
         std::fwrite(data, 1, size, part_file_) == size &&
         std::fflush(part_file_) == 0;
}

bool SpiffsAssetStore::read_part(std::uint32_t offset, std::uint8_t* data,
                                 std::size_t size) const {
  return part_file_ != nullptr && data != nullptr &&
         std::fflush(part_file_) == 0 &&
         std::fseek(part_file_, static_cast<long>(offset), SEEK_SET) == 0 &&
         std::fread(data, 1, size, part_file_) == size;
}

OperationResult SpiffsAssetStore::validate_part(
    const AssetDescriptor& descriptor) {
  if (part_file_ == nullptr || std::fflush(part_file_) != 0) return OperationResult::StorageError;
  if (free_bytes() < kMinimumFilesystemReserveBytes)
    return OperationResult::StorageError;
  if (descriptor.kind == AssetKind::Native) {
    void* handle = dlopen(kPartPath, RTLD_NOW);
    if (handle == nullptr) return OperationResult::Unsupported;
    auto entry = reinterpret_cast<ledgrid_animation_entrypoint_v1>(
        dlsym(handle, LEDGRID_ANIMATION_ENTRYPOINT_V1));
    const ledgrid_animation_callbacks_v1* callbacks = entry == nullptr ? nullptr : entry();
    const bool valid = callbacks != nullptr &&
                       callbacks->abi_version == LEDGRID_ANIMATION_ABI_V1 &&
                       callbacks->initialize != nullptr && callbacks->render != nullptr &&
                       callbacks->cleanup != nullptr;
    dlclose(handle);
    return valid ? OperationResult::Ok : OperationResult::WrongAbi;
  }
  std::uint8_t* bytes = static_cast<std::uint8_t*>(
      heap_caps_malloc(descriptor.total_size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (bytes == nullptr) return OperationResult::StorageError;
  const bool read = read_part(0, bytes, descriptor.total_size);
  FrameTrackDecoder decoder;
  const bool valid = read && decoder.open(bytes, descriptor.total_size,
                                           descriptor.strip_count,
                                           descriptor.leds_per_strip,
                                           descriptor.logical_device);
  heap_caps_free(bytes);
  return valid ? OperationResult::Ok : OperationResult::InvalidCommand;
}

bool SpiffsAssetStore::commit_part(const std::uint8_t digest[32]) {
  if (part_file_ == nullptr || std::memcmp(digest, part_descriptor_.digest, 32) != 0)
    return false;
  const bool synced = std::fflush(part_file_) == 0 && fsync(fileno(part_file_)) == 0;
  std::fclose(part_file_);
  part_file_ = nullptr;
  if (!synced) { discard_part(); return false; }
  char data[96] = {}, meta[96] = {};
  if (!committed_path(digest, data, sizeof(data)) ||
      !metadata_path(digest, meta, sizeof(meta))) return false;
  unlink(data);
  unlink(meta);
  if (rename(kPartPath, data) != 0) { discard_part(); return false; }
  if (rename(kPartMetaPath, meta) != 0) {
    unlink(data);
    discard_part();
    return false;
  }
  part_descriptor_ = {};
  return true;
}

void SpiffsAssetStore::discard_part() {
  if (part_file_ != nullptr) {
    std::fclose(part_file_);
    part_file_ = nullptr;
  }
  unlink(kPartPath);
  unlink(kPartMetaPath);
  part_descriptor_ = {};
}

bool SpiffsAssetStore::remove(const std::uint8_t digest[32]) {
  if (!all_zero(active_digest_) && std::memcmp(digest, active_digest_, 32) == 0)
    return false;
  char data[96] = {}, meta[96] = {};
  if (!committed_path(digest, data, sizeof(data)) ||
      !metadata_path(digest, meta, sizeof(meta))) return false;
  const bool absent = !probe(digest);
  const bool meta_ok = unlink(meta) == 0 || errno == ENOENT;
  const bool data_ok = unlink(data) == 0 || errno == ENOENT;
  return absent || (meta_ok && data_ok);
}

bool SpiffsAssetStore::read_committed(const std::uint8_t digest[32],
                                      std::uint32_t offset, std::uint8_t* data,
                                      std::size_t size) const {
  char path[96] = {};
  if (!committed_path(digest, path, sizeof(path))) return false;
  std::FILE* file = std::fopen(path, "rb");
  if (file == nullptr) return false;
  const bool ok = std::fseek(file, static_cast<long>(offset), SEEK_SET) == 0 &&
                  std::fread(data, 1, size, file) == size;
  std::fclose(file);
  return ok;
}

void SpiffsAssetStore::set_active_digest(const std::uint8_t digest[32]) {
  if (digest == nullptr) std::memset(active_digest_, 0, sizeof(active_digest_));
  else std::memcpy(active_digest_, digest, sizeof(active_digest_));
}

bool MbedtlsAssetVerifier::begin() {
  unsigned_development_ = CONFIG_LEDGRID_ALLOW_UNSIGNED_DEVELOPMENT != 0;
  const char* key_hex = CONFIG_LEDGRID_TRUSTED_P256_PUBLIC_KEY_HEX;
  const char* key_id = CONFIG_LEDGRID_TRUSTED_KEY_ID;
  if (std::strlen(key_hex) == 130 && valid_key_id(key_id) &&
      key_hex[0] == '0' && key_hex[1] == '4') {
    key_ready_ = true;
    for (std::size_t i = 0; i < sizeof(public_key_); ++i) {
      const int high = hex_value(key_hex[i * 2]);
      const int low = hex_value(key_hex[i * 2 + 1]);
      if (high < 0 || low < 0) { key_ready_ = false; break; }
      public_key_[i] = static_cast<std::uint8_t>((high << 4) | low);
    }
    if (key_ready_) {
      mbedtls_ecp_group group;
      mbedtls_ecp_point point;
      mbedtls_ecp_group_init(&group);
      mbedtls_ecp_point_init(&point);
      int result = mbedtls_ecp_group_load(&group, MBEDTLS_ECP_DP_SECP256R1);
      if (result == 0) result = mbedtls_ecp_point_read_binary(
          &group, &point, public_key_, sizeof(public_key_));
      if (result == 0) result = mbedtls_ecp_check_pubkey(&group, &point);
      key_ready_ = result == 0;
      mbedtls_ecp_point_free(&point);
      mbedtls_ecp_group_free(&group);
    }
    if (key_ready_) std::memcpy(key_id_, key_id, kSigningKeyIdBytes);
  }
  if (unsigned_development_)
    ESP_LOGE("anim-trust", "UNSIGNED DEVELOPMENT MODE ENABLED; DO NOT DEPLOY");
  else if (!key_ready_)
    ESP_LOGW("anim-trust", "No trusted P-256 key configured; uploads disabled");
  return available();
}

OperationResult MbedtlsAssetVerifier::verify(
    const AssetVerificationEnvelope& envelope) const {
  if (unsigned_development_) return OperationResult::Ok;
  if (!key_ready_) return OperationResult::Unsupported;
  if (envelope.key_id_size != kSigningKeyIdBytes ||
      std::memcmp(envelope.key_id, key_id_, kSigningKeyIdBytes) != 0)
    return OperationResult::UnknownKey;
  std::uint8_t hash[32] = {};
  if (mbedtls_sha256(envelope.signed_index, envelope.signed_index_size, hash, 0) != 0)
    return OperationResult::BadSignature;
  mbedtls_ecp_group group;
  mbedtls_ecp_point point;
  mbedtls_mpi r, s, half_order;
  mbedtls_ecp_group_init(&group);
  mbedtls_ecp_point_init(&point);
  mbedtls_mpi_init(&r); mbedtls_mpi_init(&s); mbedtls_mpi_init(&half_order);
  int result = mbedtls_ecp_group_load(&group, MBEDTLS_ECP_DP_SECP256R1);
  if (result == 0) result = mbedtls_ecp_point_read_binary(
      &group, &point, public_key_, sizeof(public_key_));
  if (result == 0) result = mbedtls_mpi_read_binary(&r, envelope.signature, 32);
  if (result == 0) result = mbedtls_mpi_read_binary(&s, envelope.signature + 32, 32);
  if (result == 0) result = mbedtls_mpi_copy(&half_order, &group.N);
  if (result == 0) result = mbedtls_mpi_shift_r(&half_order, 1);
  if (result == 0 && (mbedtls_mpi_cmp_int(&r, 1) < 0 ||
                      mbedtls_mpi_cmp_int(&s, 1) < 0 ||
                      mbedtls_mpi_cmp_mpi(&s, &half_order) > 0)) result = -1;
  if (result == 0) result = mbedtls_ecdsa_verify(&group, hash, sizeof(hash),
                                                 &point, &r, &s);
  mbedtls_mpi_free(&half_order); mbedtls_mpi_free(&s); mbedtls_mpi_free(&r);
  mbedtls_ecp_point_free(&point); mbedtls_ecp_group_free(&group);
  return result == 0 ? OperationResult::Ok : OperationResult::BadSignature;
}

bool NvsReceiverPersistence::begin() {
  esp_err_t result = nvs_flash_init();
  if (result == ESP_ERR_NVS_NO_FREE_PAGES || result == ESP_ERR_NVS_NEW_VERSION_FOUND) {
    if (nvs_flash_erase() == ESP_OK) result = nvs_flash_init();
  }
  ready_ = result == ESP_OK;
  return ready_;
}

bool NvsReceiverPersistence::mark_active(const std::uint8_t digest[32]) {
  return ready_ && nvs_write_digest("active", digest);
}
void NvsReceiverPersistence::clear_active() { if (ready_) nvs_write_digest("active", nullptr); }
void NvsReceiverPersistence::mark_quarantined(const std::uint8_t digest[32]) {
  if (ready_) nvs_write_digest("quarantine", digest);
}
void NvsReceiverPersistence::clear_quarantined(const std::uint8_t digest[32]) {
  if (!ready_) return;
  std::uint8_t current[32] = {};
  if (!nvs_read_digest("quarantine", current) || digest == nullptr ||
      std::memcmp(current, digest, 32) == 0) nvs_write_digest("quarantine", nullptr);
}
bool NvsReceiverPersistence::active_digest(std::uint8_t digest[32]) const {
  return ready_ && nvs_read_digest("active", digest);
}
bool NvsReceiverPersistence::quarantined_digest(std::uint8_t digest[32]) const {
  return ready_ && nvs_read_digest("quarantine", digest);
}
void NvsReceiverPersistence::record_reset_reason(std::uint32_t reason) {
  if (!ready_) return;
  nvs_handle_t handle = 0;
  if (nvs_open("ledgrid", NVS_READWRITE, &handle) == ESP_OK) {
    if (nvs_set_u32(handle, "reset_reason", reason) == ESP_OK) nvs_commit(handle);
    nvs_close(handle);
  }
}

EspAnimationBackend::~EspAnimationBackend() { stop(); }

bool EspAnimationBackend::begin() {
  ready_ = store_ != nullptr && store_->ready() &&
           heap_caps_get_total_size(MALLOC_CAP_SPIRAM) > 0;
  return ready_;
}

std::uint32_t EspAnimationBackend::capabilities() const {
  if (!ready_) return 0;
  std::uint32_t capabilities = kCapabilityFrameTrack;
  if (native_watchdog_ready_)
    capabilities |= kCapabilityNative | kCapabilityPsramExecution;
  return capabilities;
}

bool EspAnimationBackend::available(AssetKind kind) const {
  return ready_ && (kind == AssetKind::FrameTrack ||
                    (kind == AssetKind::Native && native_watchdog_ready_));
}

OperationResult EspAnimationBackend::load_frame_track(
    const AssetDescriptor& descriptor) {
  track_data_ = static_cast<std::uint8_t*>(
      heap_caps_malloc(descriptor.total_size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (track_data_ == nullptr) return OperationResult::StorageError;
  if (!store_->read_committed(descriptor.digest, 0, track_data_, descriptor.total_size) ||
      !decoder_.open(track_data_, descriptor.total_size, descriptor.strip_count,
                     descriptor.leds_per_strip, descriptor.logical_device)) {
    heap_caps_free(track_data_); track_data_ = nullptr;
    return OperationResult::InvalidCommand;
  }
  player_.set_decoder(&decoder_);
  apply_frame_controls();
  return OperationResult::Ok;
}

OperationResult EspAnimationBackend::load_native(
    const AssetDescriptor& descriptor) {
  char path[96] = {};
  if (!store_->committed_path(descriptor.digest, path, sizeof(path)))
    return OperationResult::StorageError;
  module_handle_ = dlopen(path, RTLD_NOW);
  if (module_handle_ == nullptr) return OperationResult::Unsupported;
  auto entry = reinterpret_cast<ledgrid_animation_entrypoint_v1>(
      dlsym(module_handle_, LEDGRID_ANIMATION_ENTRYPOINT_V1));
  callbacks_ = entry == nullptr ? nullptr : entry();
  if (callbacks_ == nullptr || callbacks_->abi_version != LEDGRID_ANIMATION_ABI_V1 ||
      callbacks_->initialize == nullptr || callbacks_->render == nullptr ||
      callbacks_->cleanup == nullptr) {
    callbacks_ = nullptr;
    dlclose(module_handle_);
    module_handle_ = nullptr;
    return OperationResult::WrongAbi;
  }
  ledgrid_render_context_v1 context{};
  context.abi_version = LEDGRID_ANIMATION_ABI_V1;
  context.local_strips = descriptor.strip_count;
  context.leds_per_strip = descriptor.leds_per_strip;
  context.global_strip_offset = global_strip_offset_;
  context.parameters = parameters_;
  context.parameter_count = parameter_count_;
  return callbacks_->initialize(&context, &kHostHelpers, &module_state_) ==
                 LEDGRID_ANIMATION_OK
             ? OperationResult::Ok : OperationResult::RenderFailed;
}

bool EspAnimationBackend::decode_parameters(const std::uint8_t* data,
                                            std::size_t size) {
  if (!validate_typed_parameter_blob(data, size)) return false;
  parameter_count_ = data[1];
  std::size_t cursor = 2;
  for (std::uint8_t i = 0; i < parameter_count_; ++i) {
    const std::uint8_t name_size = data[cursor++];
    std::memcpy(parameter_names_[i], data + cursor, name_size);
    parameter_names_[i][name_size] = '\0'; cursor += name_size;
    parameters_[i] = {};
    parameters_[i].name = parameter_names_[i];
    parameters_[i].type = data[cursor++];
    switch (static_cast<WireParameterType>(parameters_[i].type)) {
      case WireParameterType::Int32:
        parameters_[i].value.integer = static_cast<std::int32_t>(read_u32(data + cursor));
        cursor += 4; break;
      case WireParameterType::Float32: {
        const std::uint32_t bits = read_u32(data + cursor);
        std::memcpy(&parameters_[i].value.real, &bits, sizeof(bits));
        cursor += 4; break;
      }
      case WireParameterType::Bool:
        parameters_[i].value.boolean = data[cursor++]; break;
      case WireParameterType::Color:
        std::memcpy(parameters_[i].value.color, data + cursor, 3); cursor += 3; break;
      case WireParameterType::Enum: {
        const std::uint8_t value_size = data[cursor++];
        std::memcpy(enum_values_[i], data + cursor, value_size);
        enum_values_[i][value_size] = '\0'; cursor += value_size;
        parameters_[i].value.enum_value = enum_values_[i]; break;
      }
    }
  }
  return true;
}

void EspAnimationBackend::apply_frame_controls() {
  FramePlaybackControls controls{};
  controls.paused = runtime_controls_.paused;
  controls.loop = runtime_controls_.loop;
  controls.speed_permille = compose_frame_speed_permille(runtime_controls_);
  controls.brightness = asset_brightness_u8(runtime_controls_);
  player_.set_controls(controls);
}

OperationResult EspAnimationBackend::start(
    const AssetDescriptor& descriptor, std::uint16_t global_strip_offset,
    const std::uint8_t* parameters, std::size_t parameter_size) {
  stop();
  runtime_controls_ = {};
  if (!available(descriptor.kind) || !decode_parameters(parameters, parameter_size) ||
      !decode_runtime_playback_controls(
          parameters, parameter_size, &runtime_controls_))
    return OperationResult::Unsupported;
  descriptor_ = descriptor;
  kind_ = descriptor.kind;
  global_strip_offset_ = global_strip_offset;
  started_us_ = 0;
  frame_index_ = 0;
  const OperationResult result = kind_ == AssetKind::FrameTrack
                                     ? load_frame_track(descriptor)
                                     : load_native(descriptor);
  if (result != OperationResult::Ok) stop();
  return result;
}

void EspAnimationBackend::stop() {
  if (callbacks_ != nullptr && callbacks_->cleanup != nullptr && module_state_ != nullptr)
    callbacks_->cleanup(module_state_);
  module_state_ = nullptr; callbacks_ = nullptr;
  if (module_handle_ != nullptr) dlclose(module_handle_);
  module_handle_ = nullptr;
  if (track_data_ != nullptr) heap_caps_free(track_data_);
  track_data_ = nullptr; decoder_ = {}; player_.set_decoder(nullptr);
  descriptor_ = {}; kind_ = AssetKind::None; parameter_count_ = 0;
}

OperationResult EspAnimationBackend::restart() {
  if (kind_ == AssetKind::None) return OperationResult::InvalidState;
  started_us_ = 0; frame_index_ = 0;
  if (kind_ == AssetKind::FrameTrack) { player_.restart(); return OperationResult::Ok; }
  if (callbacks_ != nullptr && callbacks_->cleanup != nullptr && module_state_ != nullptr)
    callbacks_->cleanup(module_state_);
  module_state_ = nullptr;
  ledgrid_render_context_v1 context{};
  context.abi_version = LEDGRID_ANIMATION_ABI_V1;
  context.local_strips = descriptor_.strip_count;
  context.leds_per_strip = descriptor_.leds_per_strip;
  context.global_strip_offset = global_strip_offset_;
  context.parameters = parameters_; context.parameter_count = parameter_count_;
  return callbacks_ != nullptr && callbacks_->initialize(
             &context, &kHostHelpers, &module_state_) == LEDGRID_ANIMATION_OK
             ? OperationResult::Ok : OperationResult::RenderFailed;
}

OperationResult EspAnimationBackend::update_parameters(
    const std::uint8_t* parameters, std::size_t parameter_size) {
  if (!decode_parameters(parameters, parameter_size)) return OperationResult::BadSize;
  runtime_controls_ = {};
  if (!decode_runtime_playback_controls(
          parameters, parameter_size, &runtime_controls_))
    return OperationResult::BadSize;
  if (kind_ == AssetKind::FrameTrack) apply_frame_controls();
  return OperationResult::Ok;
}

bool EspAnimationBackend::render(std::uint64_t now_us, std::uint8_t* rgb_output,
                                 std::size_t rgb_output_size, bool* changed) {
  if (changed != nullptr) *changed = false;
  if (kind_ == AssetKind::FrameTrack)
    return player_.render(now_us / 1000U, rgb_output, rgb_output_size, changed);
  if (kind_ != AssetKind::Native || callbacks_ == nullptr)
    return false;
  if (started_us_ == 0) started_us_ = now_us;
  ledgrid_render_context_v1 context{};
  context.abi_version = LEDGRID_ANIMATION_ABI_V1;
  context.local_strips = descriptor_.strip_count;
  context.leds_per_strip = descriptor_.leds_per_strip;
  context.global_strip_offset = global_strip_offset_;
  context.elapsed_us = now_us - started_us_;
  context.scaled_elapsed_us = scale_animation_elapsed_us(
      context.elapsed_us, runtime_controls_.time_scale);
  context.frame_index = frame_index_++;
  context.parameters = parameters_; context.parameter_count = parameter_count_;
  context.rgb_output = rgb_output; context.rgb_output_size = rgb_output_size;
  const bool ok = callbacks_->render(module_state_, &context) ==
                  LEDGRID_ANIMATION_OK;
  if (changed != nullptr) *changed = ok;
  return ok;
}

}  // namespace ledgrid
