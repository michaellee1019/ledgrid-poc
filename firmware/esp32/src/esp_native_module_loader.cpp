#include "ledgrid/esp_native_module.hpp"
#include "ledgrid/native_module_filesystem.hpp"

#if LEDGRID_ENABLE_RECEIVER_NATIVE_MODULES

#include <cerrno>
#include <cstring>
#include <new>

#include "esp_elf.h"
#include "esp_log.h"
#include "sdkconfig.h"

namespace ledgrid {
namespace {
constexpr char kLogTag[] = "native-loader";
}

bool EspNativeModuleBackend::load(const char* path) {
  if (module_handle_ != nullptr) {
    ESP_LOGE(kLogTag, "load rejected: module already loaded");
    return false;
  }
  if (std::strcmp(CONFIG_ELF_FILE_SYSTEM_BASE_PATH,
                  kNativeModuleCacheBasePath) != 0) {
    ESP_LOGE(kLogTag, "load root mismatch: configured=%s expected=%s",
             CONFIG_ELF_FILE_SYSTEM_BASE_PATH, kNativeModuleCacheBasePath);
    return false;
  }
  const char* name = native_module_loader_name(path);
  if (name == nullptr) {
    ESP_LOGE(kLogTag, "load rejected: invalid managed payload path");
    return false;
  }

  // esp_elf_open prepends its configured cache root. The dlopen registry also
  // limits module stems to 63 bytes, shorter than our n<full SHA-256> stem.
  // Own the public ELF object directly so the exact cache name is preserved.
  elf_file_t file{};
  const int opened = esp_elf_open(&file, name);
  if (opened < 0) {
    const int open_errno = errno;
    ESP_LOGE(kLogTag, "load open failed: name=%s rc=%d errno=%d",
             name, opened, open_errno);
    return false;
  }
  auto* module = new (std::nothrow) esp_elf_t{};
  if (module == nullptr) {
    ESP_LOGE(kLogTag, "load allocation failed: name=%s bytes=%u",
             name, static_cast<unsigned>(sizeof(esp_elf_t)));
    esp_elf_close(&file);
    return false;
  }
  const int initialized = esp_elf_init(module);
  if (initialized < 0) {
    ESP_LOGE(kLogTag, "load init failed: name=%s rc=%d", name, initialized);
    delete module;
    esp_elf_close(&file);
    return false;
  }
  const int relocated = esp_elf_relocate(module, file.payload);
  esp_elf_close(&file);
  if (relocated < 0) {
    ESP_LOGE(kLogTag, "load relocate failed: name=%s rc=%d", name, relocated);
    esp_elf_deinit(module);
    delete module;
    return false;
  }
  module_handle_ = module;
  return true;
}

bool EspNativeModuleBackend::resolve_entrypoint() {
  if (module_handle_ == nullptr || api_ != nullptr) {
    ESP_LOGE(kLogTag, "entrypoint rejected: loaded=%u resolved=%u",
             static_cast<unsigned>(module_handle_ != nullptr),
             static_cast<unsigned>(api_ != nullptr));
    return false;
  }
  auto* module = static_cast<esp_elf_t*>(module_handle_);
  ledgrid_native_background_entrypoint_v2 entrypoint = nullptr;
  if (module->symtab != nullptr) {
    for (std::size_t index = 0; index < module->num; ++index) {
      if (module->symtab[index].name != nullptr &&
          std::strcmp(module->symtab[index].name,
                      LEDGRID_NATIVE_BACKGROUND_ENTRYPOINT_V2) == 0) {
        entrypoint = reinterpret_cast<ledgrid_native_background_entrypoint_v2>(
            module->symtab[index].addr);
        break;
      }
    }
  }
  if (entrypoint == nullptr) {
    ESP_LOGE(kLogTag, "entrypoint absent: symbol=%s exported_count=%u",
             LEDGRID_NATIVE_BACKGROUND_ENTRYPOINT_V2,
             static_cast<unsigned>(module->num));
    return false;
  }
  api_ = entrypoint();
  if (api_ == nullptr) {
    ESP_LOGE(kLogTag, "entrypoint returned null: symbol=%s",
             LEDGRID_NATIVE_BACKGROUND_ENTRYPOINT_V2);
    return false;
  }
  // Preserve the validation read order: an incompatible module may return
  // only its version/header, so diagnostics cannot inspect its later fields.
  if (api_->abi_version != LEDGRID_NATIVE_BACKGROUND_ABI_VERSION) {
    ESP_LOGE(kLogTag, "entrypoint ABI rejected: abi=%u",
             static_cast<unsigned>(api_->abi_version));
    return false;
  }
  if (api_->struct_size != sizeof(ledgrid_native_background_api_v2)) {
    ESP_LOGE(kLogTag, "entrypoint ABI size rejected: abi=%u api_bytes=%u",
             static_cast<unsigned>(api_->abi_version),
             static_cast<unsigned>(api_->struct_size));
    return false;
  }
  const bool valid = api_->state_size >= 1 &&
      api_->state_size <= LEDGRID_NATIVE_BACKGROUND_MAX_STATE_BYTES &&
      api_->state_alignment >= 1 &&
      api_->state_alignment <= LEDGRID_NATIVE_BACKGROUND_MAX_STATE_ALIGNMENT &&
      (api_->state_alignment & (api_->state_alignment - 1U)) == 0 &&
      api_->initialize != nullptr && api_->update_context != nullptr &&
      api_->render != nullptr && api_->cleanup != nullptr;
  if (!valid) {
    ESP_LOGE(kLogTag,
             "entrypoint ABI rejected: abi=%u api_bytes=%u state_bytes=%u "
             "alignment=%u callbacks=%u%u%u%u",
             static_cast<unsigned>(api_->abi_version),
             static_cast<unsigned>(api_->struct_size),
             static_cast<unsigned>(api_->state_size),
             static_cast<unsigned>(api_->state_alignment),
             static_cast<unsigned>(api_->initialize != nullptr),
             static_cast<unsigned>(api_->update_context != nullptr),
             static_cast<unsigned>(api_->render != nullptr),
             static_cast<unsigned>(api_->cleanup != nullptr));
  }
  return valid;
}

bool EspNativeModuleBackend::unload() {
  if (module_handle_ != nullptr) {
    auto* module = static_cast<esp_elf_t*>(module_handle_);
    esp_elf_deinit(module);
    delete module;
    module_handle_ = nullptr;
  }
  api_ = nullptr;
  return true;
}

}  // namespace ledgrid

#endif  // LEDGRID_ENABLE_RECEIVER_NATIVE_MODULES
