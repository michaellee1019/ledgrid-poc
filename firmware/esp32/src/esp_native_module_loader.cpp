#include "ledgrid/esp_native_module.hpp"
#include "ledgrid/native_module_filesystem.hpp"

#if LEDGRID_ENABLE_RECEIVER_NATIVE_MODULES

#include <cstring>
#include <new>

#include "esp_elf.h"
#include "sdkconfig.h"

namespace ledgrid {

bool EspNativeModuleBackend::load(const char* path) {
  if (module_handle_ != nullptr ||
      std::strcmp(CONFIG_ELF_FILE_SYSTEM_BASE_PATH,
                  kNativeModuleCacheBasePath) != 0) return false;
  const char* name = native_module_loader_name(path);
  if (name == nullptr) return false;

  // esp_elf_open prepends its configured cache root. The dlopen registry also
  // limits module stems to 63 bytes, shorter than our n<full SHA-256> stem.
  // Own the public ELF object directly so the exact cache name is preserved.
  elf_file_t file{};
  if (esp_elf_open(&file, name) < 0) return false;
  auto* module = new (std::nothrow) esp_elf_t{};
  if (module == nullptr) {
    esp_elf_close(&file);
    return false;
  }
  if (esp_elf_init(module) < 0) {
    delete module;
    esp_elf_close(&file);
    return false;
  }
  const bool relocated = esp_elf_relocate(module, file.payload) >= 0;
  esp_elf_close(&file);
  if (!relocated) {
    esp_elf_deinit(module);
    delete module;
    return false;
  }
  module_handle_ = module;
  return true;
}

bool EspNativeModuleBackend::resolve_entrypoint() {
  if (module_handle_ == nullptr || api_ != nullptr) return false;
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
  if (entrypoint == nullptr) return false;
  api_ = entrypoint();
  return api_ != nullptr &&
      api_->abi_version == LEDGRID_NATIVE_BACKGROUND_ABI_VERSION &&
      api_->struct_size == sizeof(ledgrid_native_background_api_v2) &&
      api_->state_size >= 1 &&
      api_->state_size <= LEDGRID_NATIVE_BACKGROUND_MAX_STATE_BYTES &&
      api_->state_alignment >= 1 &&
      api_->state_alignment <= LEDGRID_NATIVE_BACKGROUND_MAX_STATE_ALIGNMENT &&
      (api_->state_alignment & (api_->state_alignment - 1U)) == 0 &&
      api_->initialize != nullptr && api_->update_context != nullptr &&
      api_->render != nullptr && api_->cleanup != nullptr;
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
