#include "ledgrid/esp_native_module.hpp"
#include "ledgrid/native_module_filesystem.hpp"

#if LEDGRID_ENABLE_RECEIVER_NATIVE_MODULES

#include "ledgrid/native_trig.hpp"

#include <algorithm>
#include <cerrno>
#include <cmath>
#include <cstring>
#include <dirent.h>
#include <sys/stat.h>
#include <unistd.h>

#include "esp_heap_caps.h"
#include "esp_spiffs.h"
#include "esp_system.h"
#include "esp_timer.h"

namespace ledgrid {
namespace {

constexpr char kPartitionLabel[] = "profilecache";
constexpr const char* kBasePath = kNativeModuleCacheBasePath;
constexpr char kPartPath[] = "/profilecache/native-upload.part";
constexpr char kPartMetaPath[] = "/profilecache/native-upload.meta.part";
constexpr std::size_t kMetadataBytes = 48;

void native_module_watchdog_callback(void* argument) {
  // The phase and payload are committed to RTC memory before this timer is
  // armed. A reboot therefore turns an actually hung module call into a
  // deterministic quarantine during the next manager begin().
  static_cast<EspNativeModuleWatchdog*>(argument)->expire_from_timer();
}

char hex_digit(std::uint8_t value) {
  return value < 10 ? static_cast<char>('0' + value)
                    : static_cast<char>('a' + value - 10);
}

int hex_value(char value) {
  if (value >= '0' && value <= '9') return value - '0';
  if (value >= 'a' && value <= 'f') return value - 'a' + 10;
  return -1;
}

bool digest_equal(const std::uint8_t* left, const std::uint8_t* right) {
  return std::memcmp(left, right, 32) == 0;
}

bool digest_from_meta_name(const char* name, std::uint8_t digest[32]) {
  if (name == nullptr || std::strlen(name) != 70 || name[0] != 'n' ||
      std::strcmp(name + 65, ".meta") != 0) return false;
  for (std::size_t index = 0; index < 32; ++index) {
    const int high = hex_value(name[1 + index * 2]);
    const int low = hex_value(name[2 + index * 2]);
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

std::uint32_t read_u32(const std::uint8_t* input) {
  return (static_cast<std::uint32_t>(input[0]) << 24U) |
         (static_cast<std::uint32_t>(input[1]) << 16U) |
         (static_cast<std::uint32_t>(input[2]) << 8U) | input[3];
}

std::uint32_t checksum(const std::uint8_t* bytes, std::size_t size) {
  std::uint32_t value = 2166136261U;
  for (std::size_t index = 0; index < size; ++index)
    value = (value ^ bytes[index]) * 16777619U;
  return value;
}

bool is_pinned(const std::uint8_t digest[32],
               const NativeModuleLedger& pins) {
  for (const auto* binding : {&pins.active, &pins.staged, &pins.rollback}) {
    if (binding->present &&
        digest_equal(digest, binding->descriptor.payload_digest)) return true;
  }
  return false;
}

std::uint32_t helper_random(std::uint32_t* state) {
  if (state == nullptr) return 0;
  std::uint32_t value = *state;
  value ^= value << 13U;
  value ^= value >> 17U;
  value ^= value << 5U;
  *state = value;
  return value;
}

void helper_hsv(std::uint16_t hue, std::uint8_t saturation,
                std::uint8_t value, std::uint8_t rgb[3]) {
  if (rgb == nullptr) return;
  const double h = static_cast<double>(hue) * 6.0 / 65536.0;
  const int sector = static_cast<int>(h) % 6;
  const double fraction = h - std::floor(h);
  const double s = saturation / 255.0;
  const double v = value / 255.0;
  const double p = v * (1.0 - s);
  const double q = v * (1.0 - fraction * s);
  const double t = v * (1.0 - (1.0 - fraction) * s);
  double channels[3] = {};
  switch (sector) {
    case 0: channels[0] = v; channels[1] = t; channels[2] = p; break;
    case 1: channels[0] = q; channels[1] = v; channels[2] = p; break;
    case 2: channels[0] = p; channels[1] = v; channels[2] = t; break;
    case 3: channels[0] = p; channels[1] = q; channels[2] = v; break;
    case 4: channels[0] = t; channels[1] = p; channels[2] = v; break;
    default: channels[0] = v; channels[1] = p; channels[2] = q; break;
  }
  for (std::size_t index = 0; index < 3; ++index) {
    rgb[index] = static_cast<std::uint8_t>(
        std::nearbyint(std::clamp(channels[index], 0.0, 1.0) * 255.0));
  }
}

std::int16_t helper_sin(std::uint16_t phase) {
  return native_sin_q15(phase);
}

std::int16_t helper_cos(std::uint16_t phase) {
  return native_cos_q15(phase);
}

const ledgrid_native_helpers_v2 kHelpers = {
    LEDGRID_NATIVE_BACKGROUND_ABI_VERSION,
    sizeof(ledgrid_native_helpers_v2), helper_random, helper_hsv,
    helper_sin, helper_cos};

}  // namespace

EspNativeModuleStore::~EspNativeModuleStore() { abort_part(); }

bool EspNativeModuleStore::begin() {
  std::size_t total = 0, used = 0;
  ready_ = esp_spiffs_info(kPartitionLabel, &total, &used) == ESP_OK;
  if (!ready_) return false;
  const auto reconciled = reconcile_native_module_cache(kBasePath);
  if (!reconciled.ok) {
    ready_ = false;
    return false;
  }
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

std::uint32_t EspNativeModuleStore::capacity_bytes() const {
  std::size_t total = 0, used = 0;
  return ready_ && esp_spiffs_info(kPartitionLabel, &total, &used) == ESP_OK
      ? static_cast<std::uint32_t>(std::min<std::size_t>(total, UINT32_MAX)) : 0;
}

std::uint32_t EspNativeModuleStore::used_bytes() const {
  std::size_t total = 0, used = 0;
  return ready_ && esp_spiffs_info(kPartitionLabel, &total, &used) == ESP_OK
      ? static_cast<std::uint32_t>(std::min<std::size_t>(used, UINT32_MAX)) : 0;
}

bool EspNativeModuleStore::path_for(
    const std::uint8_t digest[32], const char* suffix, char* output,
    std::size_t output_size) const {
  if (digest == nullptr || suffix == nullptr || output == nullptr ||
      output_size < std::strlen(kBasePath) + 3U + 64U +
                        std::strlen(suffix)) return false;
  std::size_t cursor = static_cast<std::size_t>(
      std::snprintf(output, output_size, "%s/n", kBasePath));
  for (std::size_t index = 0; index < 32; ++index) {
    output[cursor++] = hex_digit(digest[index] >> 4U);
    output[cursor++] = hex_digit(digest[index] & 0x0FU);
  }
  std::strcpy(output + cursor, suffix);
  return true;
}

bool EspNativeModuleStore::write_metadata(
    const std::uint8_t digest[32], std::uint32_t size, std::uint32_t access,
    const char* path) const {
  std::uint8_t bytes[kMetadataBytes] = {};
  std::memcpy(bytes, "LGNM", 4);
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

bool EspNativeModuleStore::read_metadata(
    const std::uint8_t digest[32], std::uint32_t* size,
    std::uint32_t* access) const {
  char path[112] = {};
  if (!path_for(digest, ".meta", path, sizeof(path))) return false;
  std::uint8_t bytes[kMetadataBytes] = {};
  std::FILE* file = std::fopen(path, "rb");
  if (file == nullptr) return false;
  const bool read = std::fread(bytes, 1, sizeof(bytes), file) == sizeof(bytes);
  std::fclose(file);
  if (!read || std::memcmp(bytes, "LGNM", 4) != 0 ||
      checksum(bytes, 44) != read_u32(bytes + 44) ||
      !digest_equal(bytes + 12, digest)) return false;
  if (size != nullptr) *size = read_u32(bytes + 4);
  if (access != nullptr) *access = read_u32(bytes + 8);
  return true;
}

bool EspNativeModuleStore::probe(
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

bool EspNativeModuleStore::touch(const std::uint8_t digest[32]) {
  std::uint32_t size = 0;
  char path[112] = {};
  if (!read_metadata(digest, &size, nullptr) ||
      !path_for(digest, ".meta", path, sizeof(path)) ||
      !write_metadata(digest, size, access_counter_++, path)) return false;
  ++mutation_generation_;
  return true;
}

bool EspNativeModuleStore::can_stage(
    std::uint32_t size, const NativeModuleLedger& pins,
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
        reclaim = UINT32_MAX - reclaim < entry_size
            ? UINT32_MAX : reclaim + entry_size;
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

bool EspNativeModuleStore::begin_part(
    const std::uint8_t digest[32], std::uint32_t size,
    const NativeModuleLedger& pins, std::uint32_t* evicted) {
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

bool EspNativeModuleStore::write_part(
    std::uint32_t offset, const std::uint8_t* data, std::size_t size) {
  if (part_file_ == nullptr || data == nullptr || offset != part_received_ ||
      size > part_size_ - offset ||
      std::fseek(part_file_, static_cast<long>(offset), SEEK_SET) != 0 ||
      std::fwrite(data, 1, size, part_file_) != size) return false;
  part_received_ += static_cast<std::uint32_t>(size);
  return true;
}

bool EspNativeModuleStore::read_part(
    std::uint32_t offset, std::uint8_t* data, std::size_t size) const {
  if (part_file_ == nullptr || data == nullptr || offset > part_received_ ||
      size > part_received_ - offset || std::fflush(part_file_) != 0 ||
      std::fseek(part_file_, static_cast<long>(offset), SEEK_SET) != 0) return false;
  return std::fread(data, 1, size, part_file_) == size;
}

bool EspNativeModuleStore::commit_part(const std::uint8_t digest[32]) {
  if (part_file_ == nullptr || part_received_ != part_size_ ||
      !digest_equal(digest, part_digest_) || std::fflush(part_file_) != 0 ||
      fsync(fileno(part_file_)) != 0) return false;
  std::fclose(part_file_);
  part_file_ = nullptr;
  if (!write_metadata(digest, part_size_, access_counter_++, kPartMetaPath)) {
    abort_part();
    return false;
  }
  char data[112] = {}, metadata[112] = {};
  if (!path_for(digest, ".bin", data, sizeof(data)) ||
      !path_for(digest, ".meta", metadata, sizeof(metadata))) return false;
  unlink(data);
  unlink(metadata);
  if (rename(kPartPath, data) != 0 || rename(kPartMetaPath, metadata) != 0) {
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

void EspNativeModuleStore::abort_part() {
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

bool EspNativeModuleStore::read_committed(
    const std::uint8_t digest[32], std::uint32_t offset, std::uint8_t* data,
    std::size_t size) const {
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

bool EspNativeModuleStore::remove(const std::uint8_t digest[32]) {
  char data[112] = {}, metadata[112] = {};
  if (!path_for(digest, ".bin", data, sizeof(data)) ||
      !path_for(digest, ".meta", metadata, sizeof(metadata))) return false;
  const bool data_ok = unlink(data) == 0 || errno == ENOENT;
  const bool meta_ok = unlink(metadata) == 0 || errno == ENOENT;
  if (data_ok && meta_ok) ++mutation_generation_;
  return data_ok && meta_ok;
}

bool EspNativeModuleStore::committed_path(
    const std::uint8_t digest[32], char* output,
    std::size_t output_size) const {
  std::uint32_t ignored = 0;
  return probe(digest, &ignored) &&
         path_for(digest, ".bin", output, output_size);
}

std::uint64_t EspNativeModuleClock::now_us() const {
  return static_cast<std::uint64_t>(esp_timer_get_time());
}

EspNativeModuleWatchdog::~EspNativeModuleWatchdog() {
  disarm();
  if (timer_ != nullptr) {
    esp_timer_delete(static_cast<esp_timer_handle_t>(timer_));
    timer_ = nullptr;
  }
}

bool EspNativeModuleWatchdog::arm(NativeModulePhase) {
  if (gate_.armed()) return true;
  if (timer_ == nullptr) {
    const esp_timer_create_args_t args = {
        .callback = native_module_watchdog_callback,
        .arg = this,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "native-phase",
        .skip_unhandled_events = true,
    };
    esp_timer_handle_t timer = nullptr;
    if (esp_timer_create(&args, &timer) != ESP_OK) return false;
    timer_ = timer;
  }
  if (!gate_.arm()) return true;
  if (esp_timer_start_once(static_cast<esp_timer_handle_t>(timer_),
                           kNativeModuleWatchdogUs) != ESP_OK) {
    gate_.cancel();
    return false;
  }
  return true;
}

void EspNativeModuleWatchdog::disarm() {
  // cancel() and expire() are one atomic winner. If cancel wins, a callback
  // already queued by esp_timer observes false and cannot reboot a successful
  // module call. If expire wins at the actual deadline, disarm cannot mask the
  // genuine timeout.
  if (!gate_.cancel()) return;
  esp_timer_stop(static_cast<esp_timer_handle_t>(timer_));
}

void EspNativeModuleWatchdog::expire_from_timer() {
  if (gate_.expire()) esp_restart();
}

EspNativeModuleBackend::~EspNativeModuleBackend() {
  cleanup();
  unload();
}

bool EspNativeModuleBackend::initialize(
    const NativeModuleDescriptor& descriptor,
    const NativeModuleTopology& topology,
    const NativeModuleActivation& activation) {
  if (api_ == nullptr || state_ != nullptr) return false;
  state_ = heap_caps_aligned_alloc(
      api_->state_alignment, api_->state_size,
      MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  if (state_ == nullptr) return false;
  std::memset(state_, 0, api_->state_size);
  ledgrid_native_init_v2 init{};
  init.abi_version = LEDGRID_NATIVE_BACKGROUND_ABI_VERSION;
  init.struct_size = sizeof(init);
  init.global_strips = descriptor.global_strips;
  init.local_strips = descriptor.local_strips;
  init.leds_per_strip = descriptor.leds_per_strip;
  init.global_strip_offset = descriptor.global_strip_offset;
  init.reverse_local_strip_order = topology.reverse_local_strip_order ? 1 : 0;
  init.pixel_count = static_cast<std::uint32_t>(descriptor.local_strips) *
                     descriptor.leds_per_strip;
  init.deterministic_seed = activation.deterministic_seed;
  init.scene_epoch_ns = activation.scene_epoch_ns;
  init.helpers = &kHelpers;
  const bool initialized =
      api_->initialize(state_, &init) == LEDGRID_NATIVE_BACKGROUND_OK;
  if (!initialized) {
    heap_caps_free(state_);
    state_ = nullptr;
  }
  return initialized;
}

bool EspNativeModuleBackend::update_context(
    const NativeModuleParameters& parameters,
    const NativeModulePresentation& presentation) {
  if (api_ == nullptr || state_ == nullptr) return false;
  ledgrid_native_context_v2 context{};
  context.abi_version = LEDGRID_NATIVE_BACKGROUND_ABI_VERSION;
  context.struct_size = sizeof(context);
  context.parameters = parameters.entries;
  context.parameter_count = parameters.count;
  context.vibe = &presentation.vibe;
  context.modifiers = &presentation.modifier_view;
  context.profile = &presentation.profile_view;
  return api_->update_context(state_, &context) == LEDGRID_NATIVE_BACKGROUND_OK;
}

bool EspNativeModuleBackend::render(
    std::uint64_t unscaled_scene_time_us,
    std::uint64_t scaled_scene_time_us, std::uint64_t frame_index,
    std::uint8_t* rgb_output, std::size_t rgb_output_size,
    NativeModuleRenderResult* result) {
  if (api_ == nullptr || state_ == nullptr || result == nullptr) return false;
  ledgrid_native_render_request_v2 request{};
  request.abi_version = LEDGRID_NATIVE_BACKGROUND_ABI_VERSION;
  request.struct_size = sizeof(request);
  request.unscaled_scene_time_us = unscaled_scene_time_us;
  request.scaled_scene_time_us = scaled_scene_time_us;
  request.frame_index = frame_index;
  request.rgb_output = rgb_output;
  request.rgb_output_size = static_cast<std::uint32_t>(rgb_output_size);
  ledgrid_native_render_result_v2 native_result{};
  native_result.struct_size = sizeof(native_result);
  if (api_->render(state_, &request, &native_result) !=
          LEDGRID_NATIVE_BACKGROUND_OK ||
      native_result.status != LEDGRID_NATIVE_BACKGROUND_OK ||
      native_result.changed > 1 ||
      native_result.next_deadline_scene_time_us <= unscaled_scene_time_us) {
    return false;
  }
  result->changed = native_result.changed != 0;
  result->next_deadline_scene_time_us =
      native_result.next_deadline_scene_time_us;
  return true;
}

bool EspNativeModuleBackend::cleanup() {
  if (state_ == nullptr) return true;
  const bool ok = api_ != nullptr &&
      api_->cleanup(state_) == LEDGRID_NATIVE_BACKGROUND_OK;
  heap_caps_free(state_);
  state_ = nullptr;
  return ok;
}

}  // namespace ledgrid

#endif  // LEDGRID_ENABLE_RECEIVER_NATIVE_MODULES
