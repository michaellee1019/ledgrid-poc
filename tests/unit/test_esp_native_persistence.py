"""Exercise the real ESP persistence adapter with controlled NVS/reset APIs.

The retained memory is kept across simulated warm boots, then reset through the
production cold-boot path. This proves recovery and zero flash work per callback,
not physical RTC retention or receiver timing.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]

NVS_HEADER = r'''
#pragma once
#include <cstddef>
#include <cstdint>
using esp_err_t = int;
using nvs_handle_t = unsigned;
constexpr int ESP_OK=0, ESP_FAIL=-1, ESP_ERR_NVS_NOT_FOUND=1;
constexpr int NVS_READONLY=0, NVS_READWRITE=1;
int nvs_flash_init();
int nvs_open(const char*, int, nvs_handle_t*);
void nvs_close(nvs_handle_t);
int nvs_get_blob(nvs_handle_t, const char*, void*, std::size_t*);
int nvs_get_u8(nvs_handle_t, const char*, std::uint8_t*);
int nvs_set_blob(nvs_handle_t, const char*, const void*, std::size_t);
int nvs_set_u8(nvs_handle_t, const char*, std::uint8_t);
int nvs_erase_key(nvs_handle_t, const char*);
int nvs_commit(nvs_handle_t);
'''

RESET_HEADER = r'''
#pragma once
enum esp_reset_reason_t {
  ESP_RST_UNKNOWN, ESP_RST_POWERON, ESP_RST_EXT, ESP_RST_SW, ESP_RST_PANIC,
  ESP_RST_INT_WDT, ESP_RST_TASK_WDT, ESP_RST_WDT, ESP_RST_DEEPSLEEP,
  ESP_RST_BROWNOUT, ESP_RST_SDIO, ESP_RST_USB, ESP_RST_JTAG, ESP_RST_EFUSE,
  ESP_RST_PWR_GLITCH, ESP_RST_CPU_LOCKUP
};
esp_reset_reason_t esp_reset_reason();
'''

HARNESS = r'''
#include <algorithm>
#include <array>
#include <cassert>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <map>
#include <numeric>
#include <string>
#include <vector>
#include "esp_native_module_persistence.cpp"

static std::map<std::string,std::vector<std::uint8_t>> flash;
static esp_reset_reason_t reset_reason = ESP_RST_POWERON;
static std::uint64_t flash_calls, flash_time_us;
static bool fail_writes;
esp_reset_reason_t esp_reset_reason() { return reset_reason; }
int nvs_flash_init() { ++flash_calls; return ESP_OK; }
int nvs_open(const char* name, int mode, nvs_handle_t* handle) {
  ++flash_calls; assert(std::strcmp(name,"ledgrid_native")==0); *handle=1;
  return flash.empty() && mode==NVS_READONLY ? ESP_ERR_NVS_NOT_FOUND : ESP_OK;
}
void nvs_close(nvs_handle_t) { ++flash_calls; }
int nvs_get_blob(nvs_handle_t, const char* key, void* output, std::size_t* size) {
  ++flash_calls;
  auto found=flash.find(key);
  if(found==flash.end()) return ESP_ERR_NVS_NOT_FOUND;
  if(*size<found->second.size()) return ESP_FAIL;
  *size=found->second.size(); std::memcpy(output,found->second.data(),*size);
  return ESP_OK;
}
int nvs_get_u8(nvs_handle_t h, const char* key, std::uint8_t* output) {
  std::size_t size=1; return nvs_get_blob(h,key,output,&size);
}
int nvs_set_blob(nvs_handle_t, const char* key, const void* input, std::size_t size) {
  ++flash_calls; flash_time_us+=32000;  // deterministic flash-maintenance spike
  if(fail_writes) return ESP_FAIL;
  auto* bytes=static_cast<const std::uint8_t*>(input);
  flash[key]=std::vector<std::uint8_t>(bytes,bytes+size); return ESP_OK;
}
int nvs_set_u8(nvs_handle_t h, const char* key, std::uint8_t input) {
  return nvs_set_blob(h,key,&input,1);
}
int nvs_erase_key(nvs_handle_t, const char* key) {
  ++flash_calls; flash_time_us+=32000;
  if(fail_writes) return ESP_FAIL;
  return flash.erase(key) ? ESP_OK : ESP_ERR_NVS_NOT_FOUND;
}
int nvs_commit(nvs_handle_t) {
  ++flash_calls; return fail_writes ? ESP_FAIL : ESP_OK;
}

using namespace ledgrid;
class EmptyStore final : public NativeModuleStore {
 public:
  bool ready() const override { return true; }
  std::uint32_t capacity_bytes() const override { return 100000; }
  std::uint32_t used_bytes() const override { return 0; }
  std::uint32_t reserve_bytes() const override { return 0; }
  std::uint64_t mutation_generation() const override { return 0; }
  bool probe(const std::uint8_t*,std::uint32_t*) const override { return false; }
  bool touch(const std::uint8_t*) override { return false; }
  bool can_stage(std::uint32_t,const NativeModuleLedger&,std::uint32_t*) const override { return false; }
  bool begin_part(const std::uint8_t*,std::uint32_t,const NativeModuleLedger&,std::uint32_t*) override { return false; }
  bool write_part(std::uint32_t,const std::uint8_t*,std::size_t) override { return false; }
  bool read_part(std::uint32_t,std::uint8_t*,std::size_t) const override { return false; }
  bool commit_part(const std::uint8_t*) override { return false; }
  void abort_part() override {}
  bool read_committed(const std::uint8_t*,std::uint32_t,std::uint8_t*,std::size_t) const override { return false; }
  bool remove(const std::uint8_t*) override { return false; }
  bool committed_path(const std::uint8_t*,char*,std::size_t) const override { return false; }
};
class NoCallsBackend final : public NativeModuleBackend {
 public:
  bool load(const char*) override { assert(false); return false; }
  bool resolve_entrypoint() override { assert(false); return false; }
  bool initialize(const NativeModuleDescriptor&,const NativeModuleTopology&,const NativeModuleActivation&) override { assert(false); return false; }
  bool update_context(const NativeModuleParameters&,const NativeModulePresentation&) override { assert(false); return false; }
  bool render(std::uint64_t,std::uint64_t,std::uint64_t,std::uint8_t*,std::size_t,NativeModuleRenderResult*) override { assert(false); return false; }
  bool cleanup() override { assert(false); return false; }
  bool unload() override { assert(false); return false; }
};
class Clock final : public NativeModuleClock {
  std::uint64_t now_us() const override { return flash_time_us; }
};

static std::array<std::uint8_t,32> payload;
static void assert_loaded(NvsNativeModulePersistence& p, NativeModulePhase expected,
                          const std::uint8_t* expected_quarantine=nullptr) {
  NativeModuleLedger ledger{}; std::uint8_t q[32]{}, a[32]{};
  NativeModulePhase phase=NativeModulePhase::None;
  assert(p.load(&ledger,q,a,&phase)); assert(phase==expected);
  if(expected!=NativeModulePhase::None) assert(std::memcmp(a,payload.data(),32)==0);
  if(expected_quarantine) assert(std::memcmp(q,expected_quarantine,32)==0);
}
static void quarantine_via_manager(NvsNativeModulePersistence& p, NativeModulePhase expected) {
  EmptyStore store; NoCallsBackend backend; Clock clock; std::uint8_t scratch[256]{};
  NativeModuleManager manager(&store,&p,&backend,&clock,scratch,sizeof(scratch),true);
  assert(manager.begin());
  auto status=manager.status();
  assert(status.result==NativeModuleResult::Quarantined);
  assert(status.watchdog_phase==expected);
  assert(std::memcmp(status.quarantine_payload_digest,payload.data(),32)==0);
  assert(!manager.active());
}
int main() {
  for(std::size_t i=0;i<payload.size();++i) payload[i]=i+1;
  // An empty NVS namespace must still allow warm-boot crash attribution.
  NvsNativeModulePersistence p; assert(p.begin());
  const auto calls_before=flash_calls, time_before=flash_time_us;
  std::vector<double> timings;
  for(int i=0;i<10000;++i) {
    const auto start=std::chrono::steady_clock::now();
    assert(p.mark_phase(payload.data(),NativeModulePhase::Render));
    assert(p.clear_phase());
    timings.push_back(std::chrono::duration<double,std::micro>(
        std::chrono::steady_clock::now()-start).count());
  }
  // Old production code fails here: every callback writes and erases NVS,
  // including the injected 32 ms stall counted by the module phase clock.
  assert(flash_calls==calls_before && flash_time_us==time_before);
  std::sort(timings.begin(),timings.end());
  std::printf("desktop mark+clear us mean=%.3f p95=%.3f p99=%.3f max=%.3f; zero NVS calls across 10000 callbacks\n",
      std::accumulate(timings.begin(),timings.end(),0.0)/timings.size(),
      timings[9500],timings[9900],timings.back());
  assert(!p.mark_phase(nullptr,NativeModulePhase::Render));
  assert(!p.mark_phase(payload.data(),NativeModulePhase::None));
  assert(!p.mark_phase(payload.data(),static_cast<NativeModulePhase>(255)));

  for(auto reason : {ESP_RST_SW,ESP_RST_PANIC,ESP_RST_INT_WDT,ESP_RST_TASK_WDT,
                     ESP_RST_WDT,ESP_RST_CPU_LOCKUP}) {
    for(int phase=1;phase<=static_cast<int>(NativeModulePhase::Unload);++phase) {
      flash.clear();
      const auto expected=static_cast<NativeModulePhase>(phase);
      assert(p.mark_phase(payload.data(),expected));
      reset_reason=reason;
      NvsNativeModulePersistence reboot; assert(reboot.begin());
      assert_loaded(reboot,expected);
      quarantine_via_manager(reboot,expected);
      assert_loaded(reboot,NativeModulePhase::None,payload.data());
      // Quarantine is NVS durable even when RTC is lost at the next power-on.
      reset_reason=ESP_RST_POWERON;
      NvsNativeModulePersistence cold; assert(cold.begin());
      assert_loaded(cold,NativeModulePhase::None,payload.data());
    }
  }
  for(auto reason : {ESP_RST_POWERON,ESP_RST_BROWNOUT,ESP_RST_PWR_GLITCH}) {
    flash.clear(); assert(p.mark_phase(payload.data(),NativeModulePhase::Render));
    reset_reason=reason;
    NvsNativeModulePersistence cold; assert(cold.begin());
    assert_loaded(cold,NativeModulePhase::None);
  }
  // A completed call cannot become a quarantine on the next warm reboot.
  assert(p.mark_phase(payload.data(),NativeModulePhase::Render)); assert(p.clear_phase());
  reset_reason=ESP_RST_SW;
  NvsNativeModulePersistence clean; assert(clean.begin());
  assert_loaded(clean,NativeModulePhase::None);

  // Publication occurs last; an incomplete marker is ignored. A published
  // marker with corrupt data is rejected, never attributed to the wrong digest.
  assert(p.mark_phase(payload.data(),NativeModulePhase::Render));
  retained_phase.committed=0;
  assert_loaded(clean,NativeModulePhase::None);
  assert(p.mark_phase(payload.data(),NativeModulePhase::Render));
  retained_phase.payload_and_phase[5]^=1;
  NativeModuleLedger ledger{}; std::uint8_t q[32]{},a[32]{};
  NativeModulePhase phase=NativeModulePhase::None;
  assert(!clean.load(&ledger,q,a,&phase));
  assert(p.clear_phase());

  // A failed durable quarantine write must preserve the only crash evidence
  // and keep the failed module inactive. A later warm boot can retry it.
  flash.clear();
  assert(p.mark_phase(payload.data(),NativeModulePhase::Render));
  fail_writes=true;
  {
    EmptyStore store; NoCallsBackend backend; Clock clock; std::uint8_t scratch[256]{};
    NativeModuleManager manager(&store,&clean,&backend,&clock,scratch,sizeof(scratch),true);
    assert(manager.begin());
    assert(manager.status().result==NativeModuleResult::Quarantined);
    assert(!manager.active());
  }
  assert_loaded(clean,NativeModulePhase::Render);
  fail_writes=false;
  NvsNativeModulePersistence retry; assert(retry.begin());
  quarantine_via_manager(retry,NativeModulePhase::Render);
  assert_loaded(retry,NativeModulePhase::None,payload.data());

  // The old firmware's outstanding NVS crash marker still gets quarantined
  // once on upgrade, including on a cold boot, before retiring its legacy keys.
  flash["phase_payload"]={payload.begin(),payload.end()};
  flash["phase"]={static_cast<std::uint8_t>(NativeModulePhase::Render)};
  reset_reason=ESP_RST_POWERON;
  NvsNativeModulePersistence upgrade; assert(upgrade.begin());
  quarantine_via_manager(upgrade,NativeModulePhase::Render);
  assert(flash.count("phase_payload")==0 && flash.count("phase")==0);
  assert_loaded(upgrade,NativeModulePhase::None,payload.data());
  const auto migrated_calls=flash_calls;
  assert(upgrade.mark_phase(payload.data(),NativeModulePhase::Render));
  assert(upgrade.clear_phase()); assert(flash_calls==migrated_calls);
  // Persistent storage failures remain observable; RTC does not replace NVS.
  fail_writes=true; assert(!upgrade.save(ledger,payload.data()));
}
'''


class EspNativePersistenceTests(unittest.TestCase):
    def test_retained_phase_recovery_and_no_per_callback_flash_work(self):
        compiler = shutil.which("c++") or shutil.which("g++")
        if not compiler:
            self.skipTest("C++ compiler unavailable")
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / "nvs.h").write_text(NVS_HEADER)
            (directory / "nvs_flash.h").write_text('#include "nvs.h"\n')
            (directory / "esp_system.h").write_text(RESET_HEADER)
            (directory / "esp_attr.h").write_text('#define RTC_NOINIT_ATTR\n')
            (directory / "harness.cpp").write_text(HARNESS)
            sources = ROOT / "firmware/esp32/src"
            executable = directory / "persistence_test"
            compiled = subprocess.run([
                compiler, "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                "-DLEDGRID_ENABLE_RECEIVER_NATIVE_MODULES=1",
                "-I", str(directory), "-I", str(sources),
                "-I", str(ROOT / "firmware/esp32/include"),
                str(directory / "harness.cpp"), str(sources / "native_module.cpp"),
                str(sources / "sha256.cpp"), "-o", str(executable),
            ], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            ran = subprocess.run([str(executable)], capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            print(ran.stdout.strip())
