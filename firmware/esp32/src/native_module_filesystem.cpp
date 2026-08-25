#include "ledgrid/native_module_filesystem.hpp"

#include <cerrno>
#include <cstddef>
#include <cstdio>
#include <cstring>
#include <dirent.h>
#include <sys/stat.h>
#include <unistd.h>

#include "ledgrid/sha256.hpp"

namespace ledgrid {
namespace {

constexpr std::size_t kDigestBytes = 32;
constexpr std::size_t kMetadataBytes = 48;

int hex_value(char value) {
  if (value >= '0' && value <= '9') return value - '0';
  if (value >= 'a' && value <= 'f') return value - 'a' + 10;
  return -1;
}

bool decode_name(const char* name, const char* suffix,
                 std::uint8_t digest[kDigestBytes]) {
  if (name == nullptr || suffix == nullptr || name[0] != 'n' ||
      std::strlen(name) != 1U + kDigestBytes * 2U + std::strlen(suffix) ||
      std::strcmp(name + 1U + kDigestBytes * 2U, suffix) != 0) {
    return false;
  }
  for (std::size_t index = 0; index < kDigestBytes; ++index) {
    const int high = hex_value(name[1U + index * 2U]);
    const int low = hex_value(name[2U + index * 2U]);
    if (high < 0 || low < 0) return false;
    digest[index] = static_cast<std::uint8_t>((high << 4) | low);
  }
  return true;
}

std::uint32_t read_u32(const std::uint8_t* input) {
  return (static_cast<std::uint32_t>(input[0]) << 24U) |
         (static_cast<std::uint32_t>(input[1]) << 16U) |
         (static_cast<std::uint32_t>(input[2]) << 8U) | input[3];
}

std::uint32_t checksum(const std::uint8_t* bytes, std::size_t size) {
  std::uint32_t value = 2166136261U;
  for (std::size_t index = 0; index < size; ++index) {
    value = (value ^ bytes[index]) * 16777619U;
  }
  return value;
}

bool join_path(const char* base, const char* name, char* output,
               std::size_t output_size) {
  if (base == nullptr || name == nullptr || output == nullptr) return false;
  const int written = std::snprintf(output, output_size, "%s/%s", base, name);
  return written >= 0 && static_cast<std::size_t>(written) < output_size;
}

bool pair_valid(const char* base_path, const std::uint8_t expected_digest[32],
                const char* data_name, const char* metadata_name) {
  char data_path[192] = {};
  char metadata_path[192] = {};
  if (!join_path(base_path, data_name, data_path, sizeof(data_path)) ||
      !join_path(base_path, metadata_name, metadata_path,
                 sizeof(metadata_path))) {
    return false;
  }
  std::uint8_t metadata[kMetadataBytes] = {};
  std::FILE* file = std::fopen(metadata_path, "rb");
  if (file == nullptr) return false;
  const bool metadata_read =
      std::fread(metadata, 1, sizeof(metadata), file) == sizeof(metadata);
  const bool no_trailing_bytes = std::fgetc(file) == EOF;
  std::fclose(file);
  struct stat data_info {};
  const bool metadata_valid = metadata_read && no_trailing_bytes &&
      std::memcmp(metadata, "LGNM", 4) == 0 &&
      checksum(metadata, 44) == read_u32(metadata + 44) &&
      std::memcmp(metadata + 12, expected_digest, 32) == 0 &&
      stat(data_path, &data_info) == 0 && data_info.st_size >= 0 &&
      static_cast<std::uint64_t>(data_info.st_size) == read_u32(metadata + 4);
  if (!metadata_valid) return false;

  file = std::fopen(data_path, "rb");
  if (file == nullptr) return false;
  Sha256 hasher;
  std::uint8_t buffer[512] = {};
  bool read_ok = true;
  while (true) {
    const std::size_t amount = std::fread(buffer, 1, sizeof(buffer), file);
    if (amount != 0) hasher.update(buffer, amount);
    if (amount < sizeof(buffer)) {
      read_ok = std::feof(file) != 0 && std::ferror(file) == 0;
      break;
    }
  }
  std::fclose(file);
  std::uint8_t actual_digest[32] = {};
  hasher.finish(actual_digest);
  return read_ok &&
      std::memcmp(actual_digest, expected_digest, sizeof(actual_digest)) == 0;
}

bool remove_if_present(const char* path, std::uint32_t* count) {
  if (unlink(path) == 0) {
    if (count != nullptr && *count != UINT32_MAX) ++*count;
    return true;
  }
  return errno == ENOENT;
}

}  // namespace

NativeModuleCacheReconcileResult reconcile_native_module_cache(
    const char* base_path) {
  NativeModuleCacheReconcileResult result{};
  if (base_path == nullptr) return result;
  char part_path[192] = {};
  char part_meta_path[192] = {};
  if (!join_path(base_path, "native-upload.part", part_path,
                 sizeof(part_path)) ||
      !join_path(base_path, "native-upload.meta.part", part_meta_path,
                 sizeof(part_meta_path)) ||
      !remove_if_present(part_path, &result.removed_partial_files) ||
      !remove_if_present(part_meta_path, &result.removed_partial_files)) {
    return result;
  }

  DIR* directory = opendir(base_path);
  if (directory == nullptr) return result;
  while (const dirent* entry = readdir(directory)) {
    std::uint8_t digest[32] = {};
    const bool data = decode_name(entry->d_name, ".bin", digest);
    const bool metadata = !data && decode_name(entry->d_name, ".meta", digest);
    if (!data && !metadata) continue;
    char data_name[70] = {};
    char metadata_name[71] = {};
    std::memcpy(data_name, entry->d_name, 65);
    std::memcpy(metadata_name, entry->d_name, 65);
    std::strcpy(data_name + 65, ".bin");
    std::strcpy(metadata_name + 65, ".meta");
    if (pair_valid(base_path, digest, data_name, metadata_name)) continue;
    char path[192] = {};
    if (!join_path(base_path, entry->d_name, path, sizeof(path)) ||
        !remove_if_present(path, data ? &result.removed_data_files
                                     : &result.removed_metadata_files)) {
      closedir(directory);
      return result;
    }
  }
  closedir(directory);

  // A corrupt pair can be encountered as data first and metadata second (or
  // the reverse). A second pass removes whichever half remained after the
  // directory mutation in the first pass.
  directory = opendir(base_path);
  if (directory == nullptr) return result;
  while (const dirent* entry = readdir(directory)) {
    std::uint8_t digest[32] = {};
    const bool data = decode_name(entry->d_name, ".bin", digest);
    const bool metadata = !data && decode_name(entry->d_name, ".meta", digest);
    if (!data && !metadata) continue;
    char data_name[70] = {};
    char metadata_name[71] = {};
    std::memcpy(data_name, entry->d_name, 65);
    std::memcpy(metadata_name, entry->d_name, 65);
    std::strcpy(data_name + 65, ".bin");
    std::strcpy(metadata_name + 65, ".meta");
    if (pair_valid(base_path, digest, data_name, metadata_name)) continue;
    char path[192] = {};
    if (!join_path(base_path, entry->d_name, path, sizeof(path)) ||
        !remove_if_present(path, data ? &result.removed_data_files
                                     : &result.removed_metadata_files)) {
      closedir(directory);
      return result;
    }
  }
  closedir(directory);
  result.ok = true;
  return result;
}

}  // namespace ledgrid
