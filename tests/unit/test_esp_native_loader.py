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

HARNESS = r'''
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <new>
#include "esp_elf.h"
#include "ledgrid/esp_native_module.hpp"
#include "ledgrid/native_module_filesystem.hpp"

static std::string directory, opened_name;
static int opens, closes, inits, relocates, deinits, failure, entry_calls;
static bool missing_symbol;
void* operator new(std::size_t size, const std::nothrow_t&) noexcept {
  if (failure == 4) return nullptr;
  try { return ::operator new(size); } catch (...) { return nullptr; }
}
static int initialize(void*, const ledgrid_native_init_v2*) { return 0; }
static int context(void*, const ledgrid_native_context_v2*) { return 0; }
static int render(void*, const ledgrid_native_render_request_v2*, ledgrid_native_render_result_v2*) { return 0; }
static int cleanup(void*) { return 0; }
static ledgrid_native_background_api_v2 api{2, sizeof(api), 8, 8, initialize, context, render, cleanup};
static const ledgrid_native_background_api_v2* entrypoint() { ++entry_calls; return &api; }
static esp_symtab_t symbols[2];
int esp_elf_open(elf_file_t* file, const char* name) {
  ++opens; opened_name = name;
  // Exact upstream join: FS_PATH + "/" + name. The test directory substitutes
  // the mounted SPIFFS root; an absolute input would duplicate the root.
  std::string path = directory + "/" + name;
  auto* source = std::fopen(path.c_str(), "rb");
  if (!source) return -1;
  std::fclose(source);
  if (failure == 1) return -1;
  file->payload = static_cast<std::uint8_t*>(std::malloc(1));
  file->payload[0] = 42; file->size = 1;
  return 0;
}
void esp_elf_close(elf_file_t* file) { ++closes; std::free(file->payload); file->payload = nullptr; }
int esp_elf_init(esp_elf_t* module) { ++inits; *module = {}; return failure == 2 ? -1 : 0; }
int esp_elf_relocate(esp_elf_t* module, const std::uint8_t* bytes) {
  ++relocates; assert(bytes && bytes[0] == 42);
  symbols[0] = {nullptr, nullptr};
  symbols[1] = {reinterpret_cast<void*>(entrypoint), const_cast<char*>(
      missing_symbol ? "wrong_entrypoint" : LEDGRID_NATIVE_BACKGROUND_ENTRYPOINT_V2)};
  module->num = 2; module->symtab = symbols;
  return failure == 3 ? -1 : 0;
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
  }
#ifdef TEST_ROOT_MISMATCH
  ledgrid::EspNativeModuleBackend backend;
  assert(!backend.load(path.c_str())); assert(opens == 0);
#else
  for (int stage : {1,2,3,4,0}) {
    opens=closes=inits=relocates=deinits=entry_calls=0; failure=stage;
    ledgrid::EspNativeModuleBackend backend;
    assert(backend.load(path.c_str()) == (stage == 0));
    assert(opened_name == name && std::string("/profilecache/")+opened_name == path);
    assert(opens == 1 && closes == (stage == 1 ? 0 : 1));
    assert(inits == (stage == 1 || stage == 4 ? 0 : 1));
    assert(relocates == (stage == 1 || stage == 2 || stage == 4 ? 0 : 1));
    assert(deinits == (stage == 3 ? 1 : 0));
    if (stage) {
      assert(!backend.resolve_entrypoint());
      failure=0; assert(backend.load(path.c_str()));
    }
    assert(!backend.load(path.c_str()));
    assert(backend.resolve_entrypoint() && entry_calls == 1);
    assert(!backend.resolve_entrypoint());
    const int before=deinits;
    assert(backend.unload()); assert(deinits == before+1);
    assert(backend.unload()); assert(deinits == before+1);
    assert(!backend.resolve_entrypoint());
  }
  for (bool absent : {true,false}) {
    ledgrid::EspNativeModuleBackend backend;
    missing_symbol=absent; api.abi_version=absent ? 2 : 99;
    assert(backend.load(path.c_str())); assert(!backend.resolve_entrypoint());
    assert(backend.unload());
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
            (directory / "harness.cpp").write_text(HARNESS)
            sources = ROOT / "firmware/esp32/src"
            for mismatch in (False, True):
                with self.subTest(loader_root_mismatch=mismatch):
                    base = "/wrong-cache" if mismatch else "/profilecache"
                    (directory / "sdkconfig.h").write_text(
                        f'#define CONFIG_ELF_FILE_SYSTEM_BASE_PATH "{base}"\n'
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
