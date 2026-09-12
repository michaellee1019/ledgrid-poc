"""Exercise the production loader adapter with controlled public ESP ELF calls.

This proves path/ownership/ABI behavior, not target ELF execution or timing.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]

ELF_HEADER = r'''
#pragma once
#include <cstddef>
#include <cstdint>
struct esp_symtab_t { void* addr; char* name; };
struct esp_elf_t { std::uint16_t num; esp_symtab_t* symtab; };
struct elf_file_t { std::uint8_t* payload; std::size_t size; };
int esp_elf_open(elf_file_t*, const char*);
void esp_elf_close(elf_file_t*);
int esp_elf_init(esp_elf_t*);
int esp_elf_relocate(esp_elf_t*, const std::uint8_t*);
void esp_elf_deinit(esp_elf_t*);
'''

LOG_HEADER = r'''
#pragma once
void test_log(const char*, const char*, ...) __attribute__((format(printf, 2, 3)));
#define ESP_LOGE(tag, ...) test_log(tag, __VA_ARGS__)
'''

HARNESS = r'''
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <new>
#include <cerrno>
#include <cstdarg>
#include <vector>
#include <sys/mman.h>
#include <unistd.h>
#include "esp_elf.h"
#include "sdkconfig.h"
#include "ledgrid/esp_native_module.hpp"
#include "ledgrid/native_module_filesystem.hpp"

static std::string directory, opened_name;
static int opens, closes, inits, relocates, deinits, failure, entry_calls;
static bool missing_symbol, null_api;
static const ledgrid_native_background_api_v2* short_api;
static std::vector<std::string> logs;
void test_log(const char* tag, const char* format, ...) {
  assert(std::strcmp(tag, "native-loader") == 0);
  char message[512]; va_list args; va_start(args,format);
  std::vsnprintf(message,sizeof(message),format,args); va_end(args);
  logs.emplace_back(message);
}
static bool logged(const char* expected) {
  for (const auto& message : logs) if (message.find(expected) != std::string::npos) return true;
  return false;
}
void* operator new(std::size_t size, const std::nothrow_t&) noexcept {
  if (failure == 4) return nullptr;
  try { return ::operator new(size); } catch (...) { return nullptr; }
}
static int initialize(void*, const ledgrid_native_init_v2*) { return 0; }
static int context(void*, const ledgrid_native_context_v2*) { return 0; }
static int render(void*, const ledgrid_native_render_request_v2*, ledgrid_native_render_result_v2*) { return 0; }
static int cleanup(void*) { return 0; }
static ledgrid_native_background_api_v2 api{2, sizeof(api), 8, 8, initialize, context, render, cleanup};
static const ledgrid_native_background_api_v2* entrypoint() { ++entry_calls; return null_api ? nullptr : short_api ? short_api : &api; }
static esp_symtab_t symbols[2];
// An intentionally uncallable data alias must be translated before invocation.
static constexpr std::uintptr_t data_alias = 0x1234;
std::uintptr_t elf_remap_text(esp_elf_t* module, std::uintptr_t address) {
  assert(module && address == data_alias);
  return reinterpret_cast<std::uintptr_t>(entrypoint);
}
int esp_elf_open(elf_file_t* file, const char* name) {
  ++opens; opened_name = name;
  // Exact upstream join: FS_PATH + "/" + name. The test directory substitutes
  // the mounted SPIFFS root; an absolute input would duplicate the root.
  std::string path = directory + "/" + name;
  auto* source = std::fopen(path.c_str(), "rb");
  if (!source) return -1;
  std::fclose(source);
  if (failure == 1) { errno=EIO; return -11; }
  file->payload = static_cast<std::uint8_t*>(std::malloc(1));
  file->payload[0] = 42; file->size = 1;
  return 0;
}
void esp_elf_close(elf_file_t* file) { ++closes; std::free(file->payload); file->payload = nullptr; }
int esp_elf_init(esp_elf_t* module) { ++inits; *module = {}; return failure == 2 ? -22 : 0; }
int esp_elf_relocate(esp_elf_t* module, const std::uint8_t* bytes) {
  ++relocates; assert(bytes && bytes[0] == 42);
  symbols[0] = {nullptr, nullptr};
  symbols[1] = {
#if CONFIG_ELF_LOADER_CACHE_OFFSET
      reinterpret_cast<void*>(data_alias),
#else
      reinterpret_cast<void*>(entrypoint),
#endif
      const_cast<char*>(
      missing_symbol ? "wrong_entrypoint" : LEDGRID_NATIVE_BACKGROUND_ENTRYPOINT_V2)};
  module->num = 2; module->symtab = symbols;
  return failure == 3 ? -33 : 0;
}
void esp_elf_deinit(esp_elf_t* module) { ++deinits; *module = {}; }
// The production load/resolve/unload definitions are compiled separately.
// Remaining backend operations are irrelevant to this controlled ELF boundary.
namespace ledgrid {
EspNativeModuleBackend::~EspNativeModuleBackend() { unload(); }
bool EspNativeModuleBackend::initialize(const NativeModuleDescriptor&, const NativeModuleTopology&, const NativeModuleActivation&) { return false; }
bool EspNativeModuleBackend::update_context(const NativeModuleParameters&, const NativeModulePresentation&) { return false; }
bool EspNativeModuleBackend::render(std::uint64_t,std::uint64_t,std::uint64_t,std::uint8_t*,std::size_t,NativeModuleRenderResult*) { return false; }
bool EspNativeModuleBackend::cleanup() { return true; }
}
int main(int argc, char** argv) {
  assert(argc == 2); directory = argv[1];
  const std::string name = "n" + std::string(64, 'a') + ".bin";
  const std::string path = "/profilecache/" + name;
  const std::string stored = directory + "/" + name;
  auto* file = std::fopen(stored.c_str(), "wb"); assert(file); std::fputc(42,file); std::fclose(file);
  // Both previous public-dlopen inputs fail: stem65 >= FILE_NAME_MAX64;
  // and passing the absolute path to esp_elf_open duplicates the cache root.
  assert(name.find_last_of('.') == 65);
  elf_file_t old{}; assert(esp_elf_open(&old, path.c_str()) < 0); opens = 0;
  assert(ledgrid::native_module_loader_name(nullptr) == nullptr);
  for (const std::string& bad : {name, "/other/"+name, "/profilecache-other/"+name,
      "/profilecache//"+name, "/profilecache/../"+name, path+"/../x",
      path+".meta", "/profilecache/n"+std::string(64,'A')+".bin",
      "/profilecache/n"+std::string(63,'a')+".bin", std::string("/profilecache/")}) {
    ledgrid::EspNativeModuleBackend backend;
    assert(!backend.load(bad.c_str())); assert(opens == 0);
#ifndef TEST_ROOT_MISMATCH
    assert(logged("invalid managed payload path"));
#endif
    // Unvalidated paths must never be copied to diagnostic output.
    for (const auto& message : logs) assert(message.find(bad) == std::string::npos);
    logs.clear();
  }
#ifdef TEST_ROOT_MISMATCH
  ledgrid::EspNativeModuleBackend backend;
  assert(!backend.load(path.c_str())); assert(opens == 0);
  assert(logged("load root mismatch: configured=/wrong-cache expected=/profilecache"));
#else
  for (int stage : {1,2,3,4,0}) {
    opens=closes=inits=relocates=deinits=entry_calls=0; failure=stage; logs.clear();
    ledgrid::EspNativeModuleBackend backend;
    assert(backend.load(path.c_str()) == (stage == 0));
    assert(opened_name == name && std::string("/profilecache/")+opened_name == path);
    assert(opens == 1 && closes == (stage == 1 ? 0 : 1));
    assert(inits == (stage == 1 || stage == 4 ? 0 : 1));
    assert(relocates == (stage == 1 || stage == 2 || stage == 4 ? 0 : 1));
    assert(deinits == (stage == 3 ? 1 : 0));
    if (stage == 1) { assert(logged("load open failed:")); assert(logged("rc=-11 errno=")); }
    if (stage == 2) { assert(logged("load init failed:")); assert(logged("rc=-22")); }
    if (stage == 3) { assert(logged("load relocate failed:")); assert(logged("rc=-33")); }
    if (stage == 4) assert(logged("load allocation failed:"));
    if (stage == 0) assert(logs.empty());
    if (stage) {
      assert(!backend.resolve_entrypoint());
      failure=0; assert(backend.load(path.c_str()));
    }
    assert(!backend.load(path.c_str()));
    logs.clear();
    assert(backend.resolve_entrypoint() && entry_calls == 1);
    assert(logs.empty());
    assert(!backend.resolve_entrypoint());
    const int before=deinits;
    assert(backend.unload()); assert(deinits == before+1);
    assert(backend.unload()); assert(deinits == before+1);
    assert(!backend.resolve_entrypoint());
  }
  for (bool absent : {true,false}) {
    ledgrid::EspNativeModuleBackend backend;
    missing_symbol=absent; api.abi_version=absent ? 2 : 99; logs.clear();
    assert(backend.load(path.c_str())); assert(!backend.resolve_entrypoint());
    assert(logged(absent ? "entrypoint absent:" : "entrypoint ABI rejected: abi=99"));
    assert(backend.unload());
  }
#endif
#ifndef TEST_ROOT_MISMATCH
  // Any read beyond the rejected version/header crosses into PROT_NONE.
  // This catches diagnostic reads that bypass the original short circuit.
  for (std::size_t header_bytes : {4U,8U}) {
    const auto page_size = static_cast<std::size_t>(sysconf(_SC_PAGESIZE));
    void* mapping = mmap(nullptr, page_size*2, PROT_READ|PROT_WRITE,
                         MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);
    assert(mapping != MAP_FAILED);
    auto* boundary = static_cast<unsigned char*>(mapping)+page_size;
    assert(mprotect(boundary,page_size,PROT_NONE) == 0);
    auto* header = reinterpret_cast<std::uint32_t*>(boundary-header_bytes);
    header[0] = header_bytes == 4 ? 99 : 2;
    if (header_bytes == 8) header[1] = 8;
    short_api = reinterpret_cast<const ledgrid_native_background_api_v2*>(header);
    ledgrid::EspNativeModuleBackend backend;
    missing_symbol=false; logs.clear();
    assert(backend.load(path.c_str())); assert(!backend.resolve_entrypoint());
    assert(logged(header_bytes == 4 ? "entrypoint ABI rejected: abi=99"
                                  : "entrypoint ABI size rejected: abi=2 api_bytes=8"));
    assert(!logged("state_bytes="));
    assert(backend.unload()); short_api=nullptr;
    assert(munmap(mapping,page_size*2) == 0);
  }
  {
    ledgrid::EspNativeModuleBackend backend;
    missing_symbol=false; null_api=true; logs.clear();
    assert(backend.load(path.c_str())); assert(!backend.resolve_entrypoint());
    assert(logged("entrypoint returned null:"));
  }
#endif
  std::remove(stored.c_str());
}
'''


class EspNativeLoaderTests(unittest.TestCase):
    def test_managed_loader_path_and_resource_ownership(self):
        compiler = shutil.which("clang++") or shutil.which("g++")
        self.assertIsNotNone(compiler, "C++ compiler required for native loader checks")
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / "esp_elf.h").write_text(ELF_HEADER)
            (directory / "esp_log.h").write_text(LOG_HEADER)
            (directory / "harness.cpp").write_text(HARNESS)
            (directory / "private").mkdir()
            (directory / "private/elf_platform.h").write_text(
                '#pragma once\n#include "esp_elf.h"\n'
                'std::uintptr_t elf_remap_text(esp_elf_t*, std::uintptr_t);\n'
            )
            sources = ROOT / "firmware/esp32/src"
            for mismatch, cache_offset in ((False, False), (False, True), (True, True)):
                with self.subTest(loader_root_mismatch=mismatch, cache_offset=cache_offset):
                    base = "/wrong-cache" if mismatch else "/profilecache"
                    (directory / "sdkconfig.h").write_text(
                        f'#define CONFIG_ELF_FILE_SYSTEM_BASE_PATH "{base}"\n'
                        f'#define CONFIG_ELF_LOADER_CACHE_OFFSET {int(cache_offset)}\n'
                    )
                    executable = directory / "loader_test"
                    compiled = subprocess.run([
                        compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        "-DLEDGRID_ENABLE_RECEIVER_NATIVE_MODULES=1",
                        *(["-DTEST_ROOT_MISMATCH=1"] if mismatch else []),
                        "-I", str(directory), "-I", str(ROOT / "firmware/esp32/include"),
                        str(directory / "harness.cpp"),
                        str(sources / "esp_native_module_loader.cpp"),
                        str(sources / "native_module_filesystem.cpp"),
                        str(sources / "sha256.cpp"), "-o", str(executable),
                    ], capture_output=True, text=True)
                    self.assertEqual(compiled.returncode, 0, compiled.stderr)
                    subprocess.run([str(executable), str(directory)], check=True,
                                   capture_output=True, text=True)
