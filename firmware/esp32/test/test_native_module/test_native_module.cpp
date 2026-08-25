#include <unity.h>

#include <algorithm>
#include <array>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <string>
#include <vector>
#include <sys/stat.h>
#include <unistd.h>

#include "ledgrid/native_module.hpp"
#include "ledgrid/native_module_filesystem.hpp"
#include "ledgrid/protocol.hpp"
#include "ledgrid/sha256.hpp"

namespace {

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
  return static_cast<std::uint16_t>(input[0] << 8U | input[1]);
}

std::uint32_t read_u32(const std::uint8_t* input) {
  return (static_cast<std::uint32_t>(input[0]) << 24U) |
         (static_cast<std::uint32_t>(input[1]) << 16U) |
         (static_cast<std::uint32_t>(input[2]) << 8U) | input[3];
}

std::uint64_t read_u64(const std::uint8_t* input) {
  std::uint64_t value = 0;
  for (std::size_t index = 0; index < 8; ++index) value = value << 8U | input[index];
  return value;
}

std::uint32_t metadata_checksum(const std::uint8_t* bytes, std::size_t size) {
  std::uint32_t value = 2166136261U;
  for (std::size_t index = 0; index < size; ++index) {
    value = (value ^ bytes[index]) * 16777619U;
  }
  return value;
}

std::string cache_path(const char* directory, const std::uint8_t digest[32],
                       const char* suffix) {
  static constexpr char kHex[] = "0123456789abcdef";
  std::string path(directory);
  path += "/n";
  for (std::size_t index = 0; index < 32; ++index) {
    path += kHex[digest[index] >> 4U];
    path += kHex[digest[index] & 0x0FU];
  }
  path += suffix;
  return path;
}

bool write_test_file(const std::string& path, const std::uint8_t* bytes,
                     std::size_t size) {
  std::FILE* file = std::fopen(path.c_str(), "wb");
  if (file == nullptr) return false;
  const bool ok = std::fwrite(bytes, 1, size, file) == size;
  std::fclose(file);
  return ok;
}

bool write_test_metadata(const char* directory,
                         const std::uint8_t digest[32],
                         std::uint32_t size, bool valid_checksum = true) {
  std::uint8_t metadata[48] = {};
  std::memcpy(metadata, "LGNM", 4);
  write_u32(metadata + 4, size);
  write_u32(metadata + 8, 7);
  std::memcpy(metadata + 12, digest, 32);
  write_u32(metadata + 44, metadata_checksum(metadata, 44) ^
                                (valid_checksum ? 0U : 1U));
  return write_test_file(cache_path(directory, digest, ".meta"), metadata,
                         sizeof(metadata));
}

bool path_exists(const std::string& path) {
  struct stat info {};
  return stat(path.c_str(), &info) == 0;
}

std::string key(const std::uint8_t digest[32]) {
  return std::string(reinterpret_cast<const char*>(digest), 32);
}

class FakeStore final : public ledgrid::NativeModuleStore {
 public:
  bool ready() const override { return ready_value; }
  std::uint32_t capacity_bytes() const override { return capacity; }
  std::uint32_t used_bytes() const override {
    std::uint32_t used = 0;
    for (const auto& entry : committed) used += entry.second.size();
    return used;
  }
  std::uint32_t reserve_bytes() const override { return reserve; }
  std::uint64_t mutation_generation() const override { return generation; }
  bool probe(const std::uint8_t digest[32], std::uint32_t* size) const override {
    const auto found = committed.find(key(digest));
    if (found == committed.end()) return false;
    if (size != nullptr) *size = found->second.size();
    return true;
  }
  bool touch(const std::uint8_t digest[32]) override {
    if (!probe(digest, nullptr)) return false;
    ++generation;
    return true;
  }
  bool can_stage(std::uint32_t size, const ledgrid::NativeModuleLedger& pins,
                 std::uint32_t* reclaimable) const override {
    std::uint32_t reclaim = 0;
    for (const auto& entry : committed) {
      bool pinned = false;
      for (const auto* binding : {&pins.active, &pins.staged, &pins.rollback}) {
        if (binding->present &&
            entry.first == key(binding->descriptor.payload_digest)) pinned = true;
      }
      if (!pinned) reclaim += entry.second.size();
    }
    if (reclaimable != nullptr) *reclaimable = reclaim;
    return size <= capacity - reserve && used_bytes() - reclaim <=
        capacity - reserve - size;
  }
  bool begin_part(const std::uint8_t digest[32], std::uint32_t size,
                  const ledgrid::NativeModuleLedger& pins,
                  std::uint32_t* evicted) override {
    std::uint32_t reclaim = 0;
    if (!can_stage(size, pins, &reclaim)) return false;
    std::uint32_t count = 0;
    while (used_bytes() + size > capacity - reserve) {
      auto selected = committed.end();
      for (auto it = committed.begin(); it != committed.end(); ++it) {
        bool pinned = false;
        for (const auto* binding : {&pins.active, &pins.staged, &pins.rollback}) {
          if (binding->present &&
              it->first == key(binding->descriptor.payload_digest)) pinned = true;
        }
        if (!pinned) { selected = it; break; }
      }
      if (selected == committed.end()) return false;
      committed.erase(selected);
      ++count;
    }
    part.assign(size, 0);
    received = 0;
    std::memcpy(part_digest.data(), digest, 32);
    if (evicted != nullptr) *evicted = count;
    ++generation;
    return true;
  }
  bool write_part(std::uint32_t offset, const std::uint8_t* data,
                  std::size_t size) override {
    if (data == nullptr || offset != received || size > part.size() - offset)
      return false;
    std::memcpy(part.data() + offset, data, size);
    received += size;
    return true;
  }
  bool read_part(std::uint32_t offset, std::uint8_t* data,
                 std::size_t size) const override {
    if (data == nullptr || offset > received || size > received - offset)
      return false;
    std::memcpy(data, part.data() + offset, size);
    return true;
  }
  bool commit_part(const std::uint8_t digest[32]) override {
    if (received != part.size() ||
        std::memcmp(digest, part_digest.data(), 32) != 0) return false;
    committed[key(digest)] = part;
    part.clear();
    received = 0;
    ++generation;
    return true;
  }
  void abort_part() override { part.clear(); received = 0; ++generation; }
  bool read_committed(const std::uint8_t digest[32], std::uint32_t offset,
                      std::uint8_t* data, std::size_t size) const override {
    const auto found = committed.find(key(digest));
    if (found == committed.end() || offset > found->second.size() ||
        size > found->second.size() - offset) return false;
    std::memcpy(data, found->second.data() + offset, size);
    return true;
  }
  bool remove(const std::uint8_t digest[32]) override {
    committed.erase(key(digest));
    ++generation;
    return true;
  }
  bool committed_path(const std::uint8_t digest[32], char* output,
                      std::size_t output_size) const override {
    if (!probe(digest, nullptr) || output_size < 5) return false;
    std::memcpy(output, "fake", 5);
    return true;
  }

  bool ready_value = true;
  std::uint32_t capacity = 1024 * 1024;
  std::uint32_t reserve = 128;
  std::uint64_t generation = 1;
  std::map<std::string, std::vector<std::uint8_t>> committed;
  std::vector<std::uint8_t> part;
  std::array<std::uint8_t, 32> part_digest{};
  std::uint32_t received = 0;
};

class FakePersistence final : public ledgrid::NativeModulePersistence {
 public:
  bool load(ledgrid::NativeModuleLedger* output,
            std::uint8_t quarantine_output[32],
            std::uint8_t attributed_output[32],
            ledgrid::NativeModulePhase* phase_output) override {
    *output = ledger;
    std::memcpy(quarantine_output, quarantine.data(), 32);
    std::memcpy(attributed_output, attributed.data(), 32);
    *phase_output = phase;
    return load_ok;
  }
  bool save(const ledgrid::NativeModuleLedger& value,
            const std::uint8_t quarantined[32]) override {
    if (!save_ok) return false;
    ledger = value;
    std::memcpy(quarantine.data(), quarantined, 32);
    return true;
  }
  bool mark_phase(const std::uint8_t payload[32],
                  ledgrid::NativeModulePhase value) override {
    if (!mark_ok) return false;
    std::memcpy(attributed.data(), payload, 32);
    phase = value;
    phases.push_back(value);
    return true;
  }
  bool clear_phase() override {
    phase = ledgrid::NativeModulePhase::None;
    attributed.fill(0);
    return clear_ok;
  }

  ledgrid::NativeModuleLedger ledger{};
  std::array<std::uint8_t, 32> quarantine{};
  std::array<std::uint8_t, 32> attributed{};
  ledgrid::NativeModulePhase phase = ledgrid::NativeModulePhase::None;
  std::vector<ledgrid::NativeModulePhase> phases;
  bool load_ok = true;
  bool save_ok = true;
  bool mark_ok = true;
  bool clear_ok = true;
};

class FakeBackend final : public ledgrid::NativeModuleBackend {
 public:
  bool pass(ledgrid::NativeModulePhase phase) {
    calls.push_back(phase);
    return failure != phase;
  }
  bool load(const char*) override { return pass(ledgrid::NativeModulePhase::Load); }
  bool resolve_entrypoint() override {
    return pass(ledgrid::NativeModulePhase::Entrypoint);
  }
  bool initialize(const ledgrid::NativeModuleDescriptor&,
                  const ledgrid::NativeModuleTopology&,
                  const ledgrid::NativeModuleActivation&) override {
    return pass(ledgrid::NativeModulePhase::Initialize);
  }
  bool update_context(const ledgrid::NativeModuleParameters&,
                      const ledgrid::NativeModulePresentation&) override {
    return pass(ledgrid::NativeModulePhase::ContextUpdate);
  }
  bool render(std::uint64_t now, std::uint64_t, std::uint64_t,
              std::uint8_t* output, std::size_t size,
              ledgrid::NativeModuleRenderResult* result) override {
    const bool ok = pass(ledgrid::NativeModulePhase::Render);
    if (ok) {
      std::memset(output, 7, size);
      result->changed = render_changed;
      result->next_deadline_scene_time_us = now + deadline_delta;
    }
    return ok;
  }
  bool cleanup() override { return pass(ledgrid::NativeModulePhase::Cleanup); }
  bool unload() override { return pass(ledgrid::NativeModulePhase::Unload); }

  ledgrid::NativeModulePhase failure = ledgrid::NativeModulePhase::None;
  bool render_changed = true;
  std::uint64_t deadline_delta = 16667;
  std::vector<ledgrid::NativeModulePhase> calls;
};

class FakeClock final : public ledgrid::NativeModuleClock {
 public:
  std::uint64_t now_us() const override {
    const auto value = now;
    now += step;
    return value;
  }
  mutable std::uint64_t now = 100;
  std::uint64_t step = 1;
};

class FakeWatchdog final : public ledgrid::NativeModuleWatchdog {
 public:
  bool arm(ledgrid::NativeModulePhase phase) override {
    armed.push_back(phase);
    return arm_ok;
  }
  void disarm() override { ++disarms; }
  std::vector<ledgrid::NativeModulePhase> armed;
  std::uint32_t disarms = 0;
  bool arm_ok = true;
};

ledgrid::NativeModuleDescriptor descriptor_for(
    const std::vector<std::uint8_t>& payload, std::uint8_t local_width = 1,
    std::uint16_t offset = 32) {
  ledgrid::NativeModuleDescriptor descriptor{};
  std::memset(descriptor.bundle_digest, 0xB1, 32);
  ledgrid::sha256(payload.data(), payload.size(), descriptor.payload_digest);
  descriptor.payload_size = payload.size();
  descriptor.abi = ledgrid::kNativeModuleAbiV2;
  descriptor.target = ledgrid::kNativeModuleTargetEsp32S3;
  descriptor.global_strips = 33;
  descriptor.local_strips = local_width;
  descriptor.leds_per_strip = 138;
  descriptor.global_strip_offset = offset;
  descriptor.cadence_hz = 60;
  descriptor.parameter_schema_revision = 0x10203040;
  return descriptor;
}

void encode_descriptor(const ledgrid::NativeModuleDescriptor& descriptor,
                       std::uint8_t* output) {
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

struct Rig {
  Rig(bool enabled = true)
      : manager(&store, &persistence, &backend, &clock, scratch.data(),
                scratch.size(), enabled) {
    TEST_ASSERT_EQUAL(enabled, manager.begin());
    ledgrid::NativeModuleTopology topology{};
    topology.configured = true;
    topology.logical_receiver_id = 4;
    topology.global_strips = 33;
    topology.local_strips = 1;
    topology.leds_per_strip = 138;
    topology.global_strip_offset = 32;
    manager.configure_topology(topology);
    manager.configure_scene(true, 9001);
    manager.set_watchdog(&watchdog);
  }
  FakeStore store;
  FakePersistence persistence;
  FakeBackend backend;
  FakeClock clock;
  FakeWatchdog watchdog;
  std::array<std::uint8_t, 4096> scratch{};
  ledgrid::NativeModuleManager manager;
};

std::vector<std::uint8_t> preflight_command(
    const ledgrid::NativeModuleDescriptor& descriptor) {
  std::vector<std::uint8_t> command(ledgrid::kNativeModulePreflightBytes);
  command[0] = 0x51;
  encode_descriptor(descriptor, command.data() + 1);
  return command;
}

void stage(Rig& rig, const ledgrid::NativeModuleDescriptor& descriptor,
           const std::vector<std::uint8_t>& payload) {
  auto preflight = preflight_command(descriptor);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
      static_cast<std::uint8_t>(rig.manager.process(
          preflight.data(), preflight.size())));
  std::vector<std::uint8_t> begin(ledgrid::kNativeModuleBeginBytes);
  begin[0] = 0x52;
  write_u64(begin.data() + 1, rig.manager.status().preflight_token);
  encode_descriptor(descriptor, begin.data() + 9);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
      static_cast<std::uint8_t>(rig.manager.process(begin.data(), begin.size())));
  std::size_t offset = 0;
  while (offset < payload.size()) {
    const std::size_t amount = std::min<std::size_t>(
        ledgrid::kNativeModuleMaxChunkBytes, payload.size() - offset);
    std::vector<std::uint8_t> chunk(5 + amount);
    chunk[0] = 0x53;
    write_u32(chunk.data() + 1, offset);
    std::memcpy(chunk.data() + 5, payload.data() + offset, amount);
    TEST_ASSERT_EQUAL_UINT8(
        static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
        static_cast<std::uint8_t>(rig.manager.process(chunk.data(), chunk.size())));
    // Exact retries are idempotent and conflicting overlap is rejected.
    TEST_ASSERT_EQUAL_UINT8(
        static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
        static_cast<std::uint8_t>(rig.manager.process(chunk.data(), chunk.size())));
    offset += amount;
  }
  std::array<std::uint8_t, ledgrid::kNativeModuleFinalizeBytes> finalize{};
  finalize[0] = 0x54;
  std::memcpy(finalize.data() + 1, descriptor.bundle_digest, 32);
  std::memcpy(finalize.data() + 33, descriptor.payload_digest, 32);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
      static_cast<std::uint8_t>(rig.manager.process(
          finalize.data(), finalize.size())));
}

std::vector<std::uint8_t> activate_command(
    const ledgrid::NativeModuleDescriptor& descriptor,
    std::uint64_t generation) {
  const std::uint8_t parameters[] = {1, 0};
  std::vector<std::uint8_t> command(
      ledgrid::kNativeModuleActivateHeaderBytes + sizeof(parameters));
  command[0] = 0x56;
  write_u64(command.data() + 1, generation);
  std::memcpy(command.data() + 9, descriptor.bundle_digest, 32);
  std::memcpy(command.data() + 41, descriptor.payload_digest, 32);
  write_u64(command.data() + 73, 9001);
  write_u32(command.data() + 81, 77);
  write_u16(command.data() + 85, sizeof(parameters));
  std::memcpy(command.data() + 87, parameters, sizeof(parameters));
  return command;
}

void test_compact_typed_parameters_are_canonical_and_bounded() {
  const std::uint8_t valid[] = {
      1, 5,
      0, 0, 2, 0, 0x3f, 0x00, 0x00, 0x00,
      0, 1, 1, 0, 0, 0, 0, 7,
      0, 2, 1, 0, 0xff, 0xff, 0xff, 0xfe,
      0, 3, 3, 0, 1,
      0, 4, 4, 0, 0, 3};
  ledgrid::NativeModuleParameters decoded{};
  TEST_ASSERT_TRUE(ledgrid::decode_native_typed_parameters(
      valid, sizeof(valid), &decoded));
  TEST_ASSERT_EQUAL_UINT8(5, decoded.count);
  TEST_ASSERT_EQUAL_INT32(-2, decoded.entries[2].value.integer);
  TEST_ASSERT_EQUAL_UINT16(3, decoded.entries[4].value.enum_index);
  auto malformed = std::vector<std::uint8_t>(valid, valid + sizeof(valid));
  malformed[5] = 1;  // reserved byte
  TEST_ASSERT_FALSE(ledgrid::decode_native_typed_parameters(
      malformed.data(), malformed.size(), &decoded));
  malformed.assign(valid, valid + sizeof(valid));
  malformed[10] = 0; malformed[11] = 0;  // duplicate/non-increasing id
  TEST_ASSERT_FALSE(ledgrid::decode_native_typed_parameters(
      malformed.data(), malformed.size(), &decoded));
  const std::uint8_t sparse_id[] = {1, 1, 0, 5, 3, 0, 1};
  TEST_ASSERT_FALSE(ledgrid::decode_native_typed_parameters(
      sparse_id, sizeof(sparse_id), &decoded));
  const std::uint8_t nan[] = {1, 1, 0, 0, 2, 0, 0x7f, 0xc0, 0, 0};
  TEST_ASSERT_FALSE(ledgrid::decode_native_typed_parameters(
      nan, sizeof(nan), &decoded));
}

void test_one_strip_fifth_receiver_upload_is_ordered_atomic_and_idempotent() {
  Rig rig;
  std::vector<std::uint8_t> payload(5000);
  for (std::size_t i = 0; i < payload.size(); ++i) payload[i] = i * 17U;
  const auto descriptor = descriptor_for(payload);
  stage(rig, descriptor, payload);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleTransferState::Staged),
      static_cast<std::uint8_t>(rig.manager.status().transfer_state));
  TEST_ASSERT_EQUAL_UINT32(payload.size(), rig.manager.status().total_bytes);
  std::array<std::uint8_t, ledgrid::kNativeModuleVerifyBytes> verify{};
  verify[0] = 0x55;
  std::memcpy(verify.data() + 1, descriptor.bundle_digest, 32);
  std::memcpy(verify.data() + 33, descriptor.payload_digest, 32);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
      static_cast<std::uint8_t>(rig.manager.process(verify.data(), verify.size())));
  auto activate = activate_command(descriptor, rig.manager.ledger().generation);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
      static_cast<std::uint8_t>(rig.manager.process(
          activate.data(), activate.size())));
  TEST_ASSERT_TRUE(rig.manager.active());
  TEST_ASSERT_EQUAL_UINT8(1, rig.manager.status().active_local_strips);
  TEST_ASSERT_EQUAL_UINT16(33, rig.manager.status().active_global_strips);
  TEST_ASSERT_EQUAL_UINT16(32, rig.manager.status().active_global_strip_offset);
}

void test_wrong_geometry_and_capacity_preflight_fail_before_mutation() {
  Rig rig;
  const std::vector<std::uint8_t> payload(64, 9);
  auto descriptor = descriptor_for(payload, 8, 24);
  auto preflight = preflight_command(descriptor);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::WrongGeometry),
      static_cast<std::uint8_t>(rig.manager.process(
          preflight.data(), preflight.size())));
  TEST_ASSERT_TRUE(rig.store.committed.empty());

  descriptor = descriptor_for(payload);
  rig.store.capacity = 200;
  rig.store.reserve = 160;
  preflight = preflight_command(descriptor);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::NoSpace),
      static_cast<std::uint8_t>(rig.manager.process(
          preflight.data(), preflight.size())));
  TEST_ASSERT_TRUE(rig.store.part.empty());
}

void test_every_module_controlled_phase_is_attributed_and_quarantined() {
  const ledgrid::NativeModulePhase activation_phases[] = {
      ledgrid::NativeModulePhase::Load,
      ledgrid::NativeModulePhase::Entrypoint,
      ledgrid::NativeModulePhase::Initialize,
      ledgrid::NativeModulePhase::ContextUpdate};
  for (const auto phase : activation_phases) {
    Rig rig;
    const std::vector<std::uint8_t> payload(32, static_cast<std::uint8_t>(phase));
    const auto descriptor = descriptor_for(payload);
    stage(rig, descriptor, payload);
    rig.backend.failure = phase;
    auto activate = activate_command(descriptor, rig.manager.ledger().generation);
    TEST_ASSERT_NOT_EQUAL(
        static_cast<int>(ledgrid::NativeModuleResult::Ok),
        static_cast<int>(rig.manager.process(activate.data(), activate.size())));
    TEST_ASSERT_EQUAL_HEX8_ARRAY(
        descriptor.payload_digest,
        rig.manager.status().quarantine_payload_digest, 32);
    TEST_ASSERT_EQUAL_UINT8(static_cast<std::uint8_t>(phase),
                            static_cast<std::uint8_t>(
                                rig.manager.status().watchdog_phase));
  }

  for (const auto phase : {ledgrid::NativeModulePhase::Render,
                           ledgrid::NativeModulePhase::Cleanup,
                           ledgrid::NativeModulePhase::Unload}) {
    Rig rig;
    const std::vector<std::uint8_t> payload(32, static_cast<std::uint8_t>(phase));
    const auto descriptor = descriptor_for(payload);
    stage(rig, descriptor, payload);
    auto activate = activate_command(descriptor, rig.manager.ledger().generation);
    TEST_ASSERT_EQUAL_UINT8(
        static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
        static_cast<std::uint8_t>(rig.manager.process(
            activate.data(), activate.size())));
    rig.backend.failure = phase;
    if (phase == ledgrid::NativeModulePhase::Render) {
      std::array<std::uint8_t, 138 * 3> frame{};
      ledgrid::NativeModuleRenderResult result{};
      TEST_ASSERT_FALSE(rig.manager.render(
          1000, 1000, 0, frame.data(), frame.size(), &result));
    } else {
      const std::uint8_t stop[] = {0x57};
      TEST_ASSERT_NOT_EQUAL(
          static_cast<int>(ledgrid::NativeModuleResult::Ok),
          static_cast<int>(rig.manager.process(stop, sizeof(stop))));
    }
    TEST_ASSERT_EQUAL_HEX8_ARRAY(
        descriptor.payload_digest,
        rig.manager.status().quarantine_payload_digest, 32);
  }
}

void test_slow_phase_is_watchdog_failure_and_boot_marker_quarantines() {
  Rig rig;
  const std::vector<std::uint8_t> payload(64, 5);
  const auto descriptor = descriptor_for(payload);
  stage(rig, descriptor, payload);
  rig.clock.step = 30000;
  auto activate = activate_command(descriptor, rig.manager.ledger().generation);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Watchdog),
      static_cast<std::uint8_t>(rig.manager.process(
          activate.data(), activate.size())));
  TEST_ASSERT_EQUAL_UINT16(1, rig.manager.status().watchdog_events);

  FakeStore store;
  store.committed[key(descriptor.payload_digest)] = payload;
  const std::vector<std::uint8_t> prior_payload(64, 4);
  auto prior = descriptor_for(prior_payload);
  std::memset(prior.bundle_digest, 4, sizeof(prior.bundle_digest));
  store.committed[key(prior.payload_digest)] = prior_payload;
  FakePersistence persistence;
  persistence.ledger.active.present = true;
  persistence.ledger.active.descriptor = descriptor;
  persistence.ledger.rollback.present = true;
  persistence.ledger.rollback.descriptor = prior;
  std::memcpy(persistence.attributed.data(), descriptor.payload_digest, 32);
  persistence.phase = ledgrid::NativeModulePhase::Initialize;
  FakeBackend backend;
  FakeClock clock;
  std::array<std::uint8_t, 512> scratch{};
  ledgrid::NativeModuleManager restarted(
      &store, &persistence, &backend, &clock, scratch.data(), scratch.size(),
      true);
  TEST_ASSERT_TRUE(restarted.begin());
  TEST_ASSERT_FALSE(restarted.ledger().active.present);
  TEST_ASSERT_TRUE(restarted.ledger().rollback.present);
  TEST_ASSERT_EQUAL_HEX8_ARRAY(
      prior.payload_digest,
      restarted.ledger().rollback.descriptor.payload_digest, 32);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleTransferState::Quarantined),
      static_cast<std::uint8_t>(restarted.status().transfer_state));
}

void test_failed_initialization_is_recovered_before_next_payload_activates() {
  Rig rig;
  const std::vector<std::uint8_t> failed_payload(48, 0x31);
  const auto failed = descriptor_for(failed_payload);
  stage(rig, failed, failed_payload);
  rig.backend.failure = ledgrid::NativeModulePhase::Initialize;
  auto activate = activate_command(failed, rig.manager.ledger().generation);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::InitializeFailed),
      static_cast<std::uint8_t>(rig.manager.process(
          activate.data(), activate.size())));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModulePhase::Cleanup),
      static_cast<std::uint8_t>(rig.backend.calls[3]));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModulePhase::Unload),
      static_cast<std::uint8_t>(rig.backend.calls[4]));

  std::array<std::uint8_t, ledgrid::kNativeModuleQuarantineClearBytes> clear{};
  clear[0] = 0x5C;
  std::memcpy(clear.data() + 1, failed.payload_digest, 32);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
      static_cast<std::uint8_t>(rig.manager.process(clear.data(), clear.size())));

  std::array<std::uint8_t, ledgrid::kNativeModuleRestoreBytes> restore{};
  restore[0] = 0x5B;
  write_u64(restore.data() + 1, rig.manager.ledger().generation);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
      static_cast<std::uint8_t>(rig.manager.process(
          restore.data(), restore.size())));

  rig.backend.failure = ledgrid::NativeModulePhase::None;
  const std::vector<std::uint8_t> replacement_payload(48, 0x32);
  auto replacement = descriptor_for(replacement_payload);
  std::memset(replacement.bundle_digest, 0x32,
              sizeof(replacement.bundle_digest));
  stage(rig, replacement, replacement_payload);
  activate = activate_command(replacement, rig.manager.ledger().generation);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
      static_cast<std::uint8_t>(rig.manager.process(
          activate.data(), activate.size())));
  TEST_ASSERT_TRUE(rig.manager.active());
}

void test_phase_marker_failure_never_enters_untrusted_backend() {
  Rig rig;
  const std::vector<std::uint8_t> payload(48, 0x33);
  const auto descriptor = descriptor_for(payload);
  stage(rig, descriptor, payload);
  rig.persistence.mark_ok = false;
  auto activate = activate_command(descriptor, rig.manager.ledger().generation);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::StorageError),
      static_cast<std::uint8_t>(rig.manager.process(
          activate.data(), activate.size())));
  TEST_ASSERT_TRUE(rig.backend.calls.empty());
  TEST_ASSERT_TRUE(rig.watchdog.armed.empty());
  TEST_ASSERT_EQUAL_HEX8_ARRAY(
      descriptor.payload_digest,
      rig.manager.status().quarantine_payload_digest, 32);
}

void test_host_takeover_stops_module_and_protects_rollback_payload() {
  Rig rig;
  const std::vector<std::uint8_t> payload(64, 8);
  const auto descriptor = descriptor_for(payload);
  stage(rig, descriptor, payload);
  auto activate = activate_command(descriptor, rig.manager.ledger().generation);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
      static_cast<std::uint8_t>(rig.manager.process(
          activate.data(), activate.size())));
  rig.manager.host_takeover();
  TEST_ASSERT_FALSE(rig.manager.active());
  TEST_ASSERT_FALSE(rig.manager.ledger().active.present);
  TEST_ASSERT_TRUE(rig.manager.ledger().rollback.present);
  std::array<std::uint8_t, ledgrid::kNativeModuleRemoveBytes> remove{};
  remove[0] = 0x59;
  std::memcpy(remove.data() + 1, descriptor.bundle_digest, 32);
  std::memcpy(remove.data() + 33, descriptor.payload_digest, 32);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Pinned),
      static_cast<std::uint8_t>(rig.manager.process(remove.data(), remove.size())));
}

void test_probe_miss_succeeds_and_shared_payload_aliases_stay_pinned() {
  {
    Rig rig;
    std::array<std::uint8_t, ledgrid::kNativeModuleProbeBytes> probe{};
    probe[0] = 0x50;
    std::memset(probe.data() + 1, 0xE1, 32);
    TEST_ASSERT_EQUAL_UINT8(
        static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
        static_cast<std::uint8_t>(rig.manager.process(
            probe.data(), probe.size())));
    TEST_ASSERT_FALSE((rig.manager.status().flags & 0x02U) != 0);
    TEST_ASSERT_EQUAL_HEX8_ARRAY(
        probe.data() + 1, rig.manager.status().last_probe_payload_digest, 32);
  }

  // Cache storage is keyed only by payload digest. Exercise every durable pin
  // slot with a different bundle identity that aliases the pinned ELF bytes.
  for (std::uint8_t pin_slot = 0; pin_slot < 3; ++pin_slot) {
    Rig rig;
    const std::vector<std::uint8_t> payload(48, 0xE2 + pin_slot);
    const auto descriptor = descriptor_for(payload);
    stage(rig, descriptor, payload);
    if (pin_slot >= 1) {
      auto activate = activate_command(
          descriptor, rig.manager.ledger().generation);
      TEST_ASSERT_EQUAL_UINT8(
          static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
          static_cast<std::uint8_t>(rig.manager.process(
              activate.data(), activate.size())));
    }
    if (pin_slot == 2) rig.manager.host_takeover();

    std::array<std::uint8_t, ledgrid::kNativeModuleRemoveBytes> remove{};
    remove[0] = 0x59;
    std::memset(remove.data() + 1, 0xFA, 32);
    std::memcpy(remove.data() + 33, descriptor.payload_digest, 32);
    TEST_ASSERT_EQUAL_UINT8(
        static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Pinned),
        static_cast<std::uint8_t>(rig.manager.process(
            remove.data(), remove.size())));
    TEST_ASSERT_TRUE(rig.store.probe(descriptor.payload_digest, nullptr));
  }
}

void test_operation_result_latch_survives_render_result_race() {
  Rig rig;
  const std::vector<std::uint8_t> payload(48, 0xE8);
  const auto descriptor = descriptor_for(payload);
  stage(rig, descriptor, payload);
  auto activate = activate_command(descriptor, rig.manager.ledger().generation);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
      static_cast<std::uint8_t>(rig.manager.process(
          activate.data(), activate.size())));

  std::array<std::uint8_t, ledgrid::kNativeModuleProbeBytes> probe{};
  probe[0] = 0x50;
  std::memset(probe.data() + 1, 0xE9, 32);
  const auto command_result = rig.manager.process(probe.data(), probe.size());
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
      static_cast<std::uint8_t>(command_result));
  ledgrid::NativeModuleOperationResultLatch latch;
  latch.record(91, probe[0], command_result);

  rig.backend.failure = ledgrid::NativeModulePhase::Render;
  std::array<std::uint8_t, 138 * 3> frame{};
  ledgrid::NativeModuleRenderResult render_result{};
  TEST_ASSERT_FALSE(rig.manager.render(
      1000, 1000, 0, frame.data(), frame.size(), &render_result));
  auto live_status = rig.manager.status();
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::RenderFailed),
      static_cast<std::uint8_t>(live_status.result));

  TEST_ASSERT_TRUE(latch.apply(91, probe[0], &live_status));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
      static_cast<std::uint8_t>(live_status.result));
  live_status = rig.manager.status();
  TEST_ASSERT_FALSE(latch.apply(92, probe[0], &live_status));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::RenderFailed),
      static_cast<std::uint8_t>(live_status.result));
}

void test_status_v6_prefix_offsets_and_feature_gate_are_exact() {
  ledgrid::ReceiverStatusV6 status{};
  status.packets = 0x01020304;
  status.installation_profile.state_generation = 0x1122334455667788ULL;
  status.native_module.result = ledgrid::NativeModuleResult::ContextFailed;
  status.native_module.transfer_state = ledgrid::NativeModuleTransferState::Active;
  status.native_module.watchdog_phase = ledgrid::NativeModulePhase::Render;
  status.native_module.flags = 0xA5;
  status.native_module.capacity_bytes = 0x01020304;
  status.native_module.state_generation = 0x1020304050607080ULL;
  status.native_module.active_global_strips = 33;
  status.native_module.active_local_strips = 1;
  status.native_module.active_global_strip_offset = 32;
  status.native_module.quarantines = 9;
  std::array<std::uint8_t, ledgrid::kStatusBytesV6> encoded{};
  TEST_ASSERT_TRUE(ledgrid::encode_receiver_status_v6(
      status, encoded.data(), encoded.size()));
  TEST_ASSERT_EQUAL_MEMORY("LGS6", encoded.data(), 4);
  TEST_ASSERT_EQUAL_UINT32(0x01020304, read_u32(encoded.data() + 12));
  TEST_ASSERT_EQUAL_UINT64(0x1122334455667788ULL,
                           read_u64(encoded.data() + 448));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::ContextFailed),
      encoded[768]);
  TEST_ASSERT_EQUAL_UINT8(0xA5, encoded[771]);
  TEST_ASSERT_EQUAL_UINT64(0x1020304050607080ULL,
                           read_u64(encoded.data() + 800));
  TEST_ASSERT_EQUAL_UINT8(1, encoded[1142]);
  TEST_ASSERT_EQUAL_UINT16(33, read_u16(encoded.data() + 1144));
  TEST_ASSERT_EQUAL_UINT16(32, read_u16(encoded.data() + 1148));
  TEST_ASSERT_EQUAL_UINT16(9, read_u16(encoded.data() + 1212));

  const std::uint8_t probe[33] = {0x50, 1};
  auto disabled = ledgrid::classify_receiver_dispatch(
      probe, sizeof(probe), 3 * 138, ledgrid::BaseMode::HostFullScene,
      true, true, false);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::ReceiverDispatchRoute::Reject),
      static_cast<std::uint8_t>(disabled.route));
  auto enabled = ledgrid::classify_receiver_dispatch(
      probe, sizeof(probe), 3 * 138, ledgrid::BaseMode::HostFullScene,
      true, true, true);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::ReceiverDispatchRoute::NativeModule),
      static_cast<std::uint8_t>(enabled.route));
}

void test_extended_config_supports_fifth_receiver_without_changing_lane_mask() {
  const std::uint8_t config[] = {0x07, 1, 0, 138, 0x80, 4, 0, 32};
  std::uint8_t logical_id = 0xFF;
  std::uint16_t offset = 0;
  TEST_ASSERT_TRUE(ledgrid::parse_receiver_topology(
      config, sizeof(config), logical_id, offset, &logical_id, &offset));
  TEST_ASSERT_EQUAL_UINT8(4, logical_id);
  TEST_ASSERT_EQUAL_UINT16(32, offset);
  // Active width and physical GPIO selection remain distinct commands.
  TEST_ASSERT_EQUAL_UINT8(1, config[1]);
  TEST_ASSERT_EQUAL_UINT8(0x09,
      static_cast<std::uint8_t>(ledgrid::ReceiverCommand::SetLaneMask));
}

void test_interrupted_upload_rejects_conflicts_and_abort_is_clean() {
  Rig rig;
  const std::vector<std::uint8_t> expected(32, 0x31);
  const auto descriptor = descriptor_for(expected);
  auto preflight = preflight_command(descriptor);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
      static_cast<std::uint8_t>(rig.manager.process(
          preflight.data(), preflight.size())));
  std::array<std::uint8_t, ledgrid::kNativeModuleBeginBytes> begin{};
  begin[0] = 0x52;
  write_u64(begin.data() + 1, rig.manager.status().preflight_token);
  encode_descriptor(descriptor, begin.data() + 9);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
      static_cast<std::uint8_t>(rig.manager.process(begin.data(), begin.size())));
  std::array<std::uint8_t, 13> chunk{};
  chunk[0] = 0x53;
  std::memcpy(chunk.data() + 5, expected.data(), chunk.size() - 5);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
      static_cast<std::uint8_t>(rig.manager.process(chunk.data(), chunk.size())));
  chunk[5] ^= 0xFF;
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Conflict),
      static_cast<std::uint8_t>(rig.manager.process(chunk.data(), chunk.size())));
  const std::uint8_t abort[] = {0x5A};
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
      static_cast<std::uint8_t>(rig.manager.process(abort, sizeof(abort))));
  TEST_ASSERT_TRUE(rig.store.part.empty());
  TEST_ASSERT_EQUAL_UINT32(0, rig.manager.status().received_bytes);
}

void test_finalize_digest_mismatch_never_commits_or_stages() {
  Rig rig;
  const std::vector<std::uint8_t> expected(40, 0x44);
  auto corrupted = expected;
  corrupted.back() ^= 1;
  const auto descriptor = descriptor_for(expected);
  auto preflight = preflight_command(descriptor);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
      static_cast<std::uint8_t>(rig.manager.process(
          preflight.data(), preflight.size())));
  std::array<std::uint8_t, ledgrid::kNativeModuleBeginBytes> begin{};
  begin[0] = 0x52;
  write_u64(begin.data() + 1, rig.manager.status().preflight_token);
  encode_descriptor(descriptor, begin.data() + 9);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
      static_cast<std::uint8_t>(rig.manager.process(begin.data(), begin.size())));
  std::vector<std::uint8_t> chunk(5 + corrupted.size());
  chunk[0] = 0x53;
  std::memcpy(chunk.data() + 5, corrupted.data(), corrupted.size());
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
      static_cast<std::uint8_t>(rig.manager.process(chunk.data(), chunk.size())));
  std::array<std::uint8_t, ledgrid::kNativeModuleFinalizeBytes> finalize{};
  finalize[0] = 0x54;
  std::memcpy(finalize.data() + 1, descriptor.bundle_digest, 32);
  std::memcpy(finalize.data() + 33, descriptor.payload_digest, 32);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::DigestMismatch),
      static_cast<std::uint8_t>(rig.manager.process(
          finalize.data(), finalize.size())));
  TEST_ASSERT_TRUE(rig.store.committed.empty());
  TEST_ASSERT_FALSE(rig.manager.ledger().staged.present);
}

void test_verify_and_activate_rehash_committed_payload_bytes() {
  Rig rig;
  const std::vector<std::uint8_t> payload(48, 0xA5);
  const auto descriptor = descriptor_for(payload);
  stage(rig, descriptor, payload);
  auto& committed = rig.store.committed[key(descriptor.payload_digest)];
  committed[committed.size() / 2] ^= 0x01;

  std::array<std::uint8_t, ledgrid::kNativeModuleVerifyBytes> verify{};
  verify[0] = 0x55;
  std::memcpy(verify.data() + 1, descriptor.bundle_digest, 32);
  std::memcpy(verify.data() + 33, descriptor.payload_digest, 32);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::IntegrityError),
      static_cast<std::uint8_t>(rig.manager.process(
          verify.data(), verify.size())));

  auto activate = activate_command(descriptor, rig.manager.ledger().generation);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::IntegrityError),
      static_cast<std::uint8_t>(rig.manager.process(
          activate.data(), activate.size())));
  TEST_ASSERT_TRUE(rig.backend.calls.empty());
}

void test_active_phase_attribution_is_not_confused_by_new_staged_payload() {
  Rig rig;
  const std::vector<std::uint8_t> active_payload(48, 0xA1);
  const auto active = descriptor_for(active_payload);
  stage(rig, active, active_payload);
  auto activate = activate_command(active, rig.manager.ledger().generation);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
      static_cast<std::uint8_t>(rig.manager.process(
          activate.data(), activate.size())));
  const std::vector<std::uint8_t> staged_payload(48, 0xB2);
  auto staged = descriptor_for(staged_payload);
  std::memset(staged.bundle_digest, 0xB2, sizeof(staged.bundle_digest));
  stage(rig, staged, staged_payload);
  rig.backend.failure = ledgrid::NativeModulePhase::Render;
  std::array<std::uint8_t, 138 * 3> frame{};
  ledgrid::NativeModuleRenderResult result{};
  TEST_ASSERT_FALSE(rig.manager.render(
      1000, 1000, 0, frame.data(), frame.size(), &result));
  TEST_ASSERT_EQUAL_HEX8_ARRAY(
      active.payload_digest,
      rig.manager.status().quarantine_payload_digest, 32);
  TEST_ASSERT_TRUE(rig.manager.ledger().staged.present);
  TEST_ASSERT_EQUAL_HEX8_ARRAY(
      staged.payload_digest,
      rig.manager.ledger().staged.descriptor.payload_digest, 32);
}

void test_replacement_activation_releases_old_backend_and_preserves_rollback() {
  Rig rig;
  const std::vector<std::uint8_t> first_payload(48, 0xC1);
  const auto first = descriptor_for(first_payload);
  stage(rig, first, first_payload);
  auto activate = activate_command(first, rig.manager.ledger().generation);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
      static_cast<std::uint8_t>(rig.manager.process(
          activate.data(), activate.size())));

  const std::vector<std::uint8_t> replacement_payload(48, 0xC2);
  auto replacement = descriptor_for(replacement_payload);
  std::memset(replacement.bundle_digest, 0xC2,
              sizeof(replacement.bundle_digest));
  stage(rig, replacement, replacement_payload);
  const std::size_t before_replacement = rig.backend.calls.size();
  activate = activate_command(replacement, rig.manager.ledger().generation);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
      static_cast<std::uint8_t>(rig.manager.process(
          activate.data(), activate.size())));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModulePhase::Cleanup),
      static_cast<std::uint8_t>(rig.backend.calls[before_replacement]));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModulePhase::Unload),
      static_cast<std::uint8_t>(rig.backend.calls[before_replacement + 1]));
  TEST_ASSERT_TRUE(rig.manager.ledger().rollback.present);
  TEST_ASSERT_EQUAL_HEX8_ARRAY(
      first.payload_digest,
      rig.manager.ledger().rollback.descriptor.payload_digest, 32);

  rig.backend.failure = ledgrid::NativeModulePhase::Render;
  std::array<std::uint8_t, 138 * 3> frame{};
  ledgrid::NativeModuleRenderResult result{};
  TEST_ASSERT_FALSE(rig.manager.render(
      1000, 1000, 0, frame.data(), frame.size(), &result));
  TEST_ASSERT_FALSE(rig.manager.ledger().active.present);
  TEST_ASSERT_TRUE(rig.manager.ledger().rollback.present);
  TEST_ASSERT_EQUAL_HEX8_ARRAY(
      first.payload_digest,
      rig.manager.ledger().rollback.descriptor.payload_digest, 32);
  TEST_ASSERT_EQUAL_HEX8_ARRAY(
      replacement.payload_digest,
      rig.manager.status().quarantine_payload_digest, 32);
}

void test_restore_can_reconstruct_snapshot_displaced_by_activation() {
  Rig rig;
  std::array<ledgrid::NativeModuleDescriptor, 3> descriptors{};
  for (std::size_t index = 0; index < descriptors.size(); ++index) {
    const std::vector<std::uint8_t> payload(
        48, static_cast<std::uint8_t>(0xD0 + index));
    descriptors[index] = descriptor_for(payload);
    std::memset(descriptors[index].bundle_digest,
                static_cast<int>(0xD0 + index),
                sizeof(descriptors[index].bundle_digest));
    stage(rig, descriptors[index], payload);
    if (index < 2) {
      auto activate = activate_command(
          descriptors[index], rig.manager.ledger().generation);
      TEST_ASSERT_EQUAL_UINT8(
          static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
          static_cast<std::uint8_t>(rig.manager.process(
              activate.data(), activate.size())));
    }
  }

  const auto snapshot = rig.manager.ledger();
  auto activate = activate_command(
      descriptors[2], rig.manager.ledger().generation);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
      static_cast<std::uint8_t>(rig.manager.process(
          activate.data(), activate.size())));

  std::array<std::uint8_t, ledgrid::kNativeModuleRestoreBytes> restore{};
  restore[0] = 0x5B;
  write_u64(restore.data() + 1, rig.manager.ledger().generation);
  const ledgrid::NativeModuleBinding* bindings[] = {
      &snapshot.active, &snapshot.staged, &snapshot.rollback};
  for (std::size_t slot = 0; slot < 3; ++slot) {
    std::uint8_t* encoded = restore.data() + 9 + slot * 65U;
    encoded[0] = bindings[slot]->present ? 1 : 0;
    if (bindings[slot]->present) {
      std::memcpy(encoded + 1, bindings[slot]->descriptor.bundle_digest, 32);
      std::memcpy(encoded + 33, bindings[slot]->descriptor.payload_digest, 32);
    }
  }
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
      static_cast<std::uint8_t>(rig.manager.process(
          restore.data(), restore.size())));
  TEST_ASSERT_TRUE(ledgrid::native_module_binding_equal(
      snapshot.active, rig.manager.ledger().active));
  TEST_ASSERT_TRUE(ledgrid::native_module_binding_equal(
      snapshot.staged, rig.manager.ledger().staged));
  TEST_ASSERT_TRUE(ledgrid::native_module_binding_equal(
      snapshot.rollback, rig.manager.ledger().rollback));
  TEST_ASSERT_FALSE(rig.manager.active());
}

void test_render_contract_requires_changed_first_frame_and_bounded_deadline() {
  for (const bool unchanged_first : {true, false}) {
    Rig rig;
    const std::vector<std::uint8_t> payload(48, unchanged_first ? 7 : 8);
    const auto descriptor = descriptor_for(payload);
    stage(rig, descriptor, payload);
    auto activate = activate_command(descriptor, rig.manager.ledger().generation);
    TEST_ASSERT_EQUAL_UINT8(
        static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
        static_cast<std::uint8_t>(rig.manager.process(
            activate.data(), activate.size())));
    rig.backend.render_changed = !unchanged_first;
    if (!unchanged_first) rig.backend.deadline_delta = 16668;
    std::array<std::uint8_t, 138 * 3> frame{};
    ledgrid::NativeModuleRenderResult result{};
    TEST_ASSERT_FALSE(rig.manager.render(
        1000, 1000, 0, frame.data(), frame.size(), &result));
    TEST_ASSERT_EQUAL_UINT8(
        static_cast<std::uint8_t>(ledgrid::NativeModuleResult::RenderFailed),
        static_cast<std::uint8_t>(rig.manager.status().result));
  }
}

void test_live_typed_parameter_update_is_bound_and_observable() {
  Rig rig;
  const std::vector<std::uint8_t> payload(48, 0x5A);
  const auto descriptor = descriptor_for(payload);
  stage(rig, descriptor, payload);
  auto activate = activate_command(descriptor, rig.manager.ledger().generation);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
      static_cast<std::uint8_t>(rig.manager.process(
          activate.data(), activate.size())));
  const std::uint8_t parameters[] = {1, 1, 0, 0, 3, 0, 1};
  std::vector<std::uint8_t> update(
      ledgrid::kNativeModuleParametersHeaderBytes + sizeof(parameters));
  update[0] = 0x58;
  std::memcpy(update.data() + 1, descriptor.bundle_digest, 32);
  std::memcpy(update.data() + 33, descriptor.payload_digest, 32);
  write_u32(update.data() + 65, descriptor.parameter_schema_revision);
  write_u16(update.data() + 69, sizeof(parameters));
  std::memcpy(update.data() + 71, parameters, sizeof(parameters));
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<std::uint8_t>(ledgrid::NativeModuleResult::Ok),
      static_cast<std::uint8_t>(rig.manager.process(
          update.data(), update.size())));
  std::uint8_t digest[32] = {};
  ledgrid::sha256(parameters, sizeof(parameters), digest);
  TEST_ASSERT_EQUAL_UINT16(sizeof(parameters),
                           rig.manager.status().active_parameter_size);
  TEST_ASSERT_EQUAL_HEX8_ARRAY(
      digest, rig.manager.status().active_parameter_digest, 32);
}

void test_cache_reconcile_repairs_rename_crashes_without_touching_valid_pairs() {
  char directory_template[] = "/tmp/ledgrid-native-cache-XXXXXX";
  char* directory = mkdtemp(directory_template);
  TEST_ASSERT_NOT_NULL(directory);
  const std::uint8_t data[] = {9, 8, 7};
  std::uint8_t valid_digest[32] = {};
  ledgrid::sha256(data, sizeof(data), valid_digest);
  const std::uint8_t orphan_data_digest[32] = {2};
  const std::uint8_t orphan_metadata_digest[32] = {3};
  const std::uint8_t corrupt_pair_digest[32] = {4};
  const std::uint8_t bit_rot_data[] = {9, 8, 6};
  // Give the bit-rot pair a valid name/metadata digest for different bytes,
  // then store bytes that do not match it.
  std::uint8_t bit_rot_expected[32] = {};
  ledgrid::sha256(bit_rot_data, sizeof(bit_rot_data), bit_rot_expected);
  TEST_ASSERT_TRUE(write_test_file(
      cache_path(directory, valid_digest, ".bin"), data, sizeof(data)));
  TEST_ASSERT_TRUE(write_test_metadata(
      directory, valid_digest, sizeof(data)));
  TEST_ASSERT_TRUE(write_test_file(
      cache_path(directory, orphan_data_digest, ".bin"), data, sizeof(data)));
  TEST_ASSERT_TRUE(write_test_metadata(
      directory, orphan_metadata_digest, sizeof(data)));
  TEST_ASSERT_TRUE(write_test_file(
      cache_path(directory, corrupt_pair_digest, ".bin"), data, sizeof(data)));
  TEST_ASSERT_TRUE(write_test_metadata(
      directory, corrupt_pair_digest, sizeof(data), false));
  TEST_ASSERT_TRUE(write_test_file(
      cache_path(directory, bit_rot_expected, ".bin"), data, sizeof(data)));
  TEST_ASSERT_TRUE(write_test_metadata(
      directory, bit_rot_expected, sizeof(data)));
  const std::uint8_t partial[] = {1};
  const std::string part = std::string(directory) + "/native-upload.part";
  const std::string part_meta =
      std::string(directory) + "/native-upload.meta.part";
  const std::string unrelated = std::string(directory) + "/profile.bin";
  TEST_ASSERT_TRUE(write_test_file(part, partial, sizeof(partial)));
  TEST_ASSERT_TRUE(write_test_file(part_meta, partial, sizeof(partial)));
  TEST_ASSERT_TRUE(write_test_file(unrelated, partial, sizeof(partial)));

  const auto repaired =
      ledgrid::reconcile_native_module_cache(directory);
  TEST_ASSERT_TRUE(repaired.ok);
  TEST_ASSERT_EQUAL_UINT32(3, repaired.removed_data_files);
  TEST_ASSERT_EQUAL_UINT32(3, repaired.removed_metadata_files);
  TEST_ASSERT_EQUAL_UINT32(2, repaired.removed_partial_files);
  TEST_ASSERT_TRUE(path_exists(cache_path(directory, valid_digest, ".bin")));
  TEST_ASSERT_TRUE(path_exists(cache_path(directory, valid_digest, ".meta")));
  TEST_ASSERT_TRUE(path_exists(unrelated));
  TEST_ASSERT_FALSE(path_exists(
      cache_path(directory, orphan_data_digest, ".bin")));
  TEST_ASSERT_FALSE(path_exists(
      cache_path(directory, orphan_metadata_digest, ".meta")));
  TEST_ASSERT_FALSE(path_exists(
      cache_path(directory, corrupt_pair_digest, ".bin")));
  TEST_ASSERT_FALSE(path_exists(
      cache_path(directory, corrupt_pair_digest, ".meta")));
  TEST_ASSERT_FALSE(path_exists(
      cache_path(directory, bit_rot_expected, ".bin")));
  TEST_ASSERT_FALSE(path_exists(
      cache_path(directory, bit_rot_expected, ".meta")));

  unlink(cache_path(directory, valid_digest, ".bin").c_str());
  unlink(cache_path(directory, valid_digest, ".meta").c_str());
  unlink(unrelated.c_str());
  rmdir(directory);
}

void test_watchdog_cancel_and_callback_are_single_atomic_winner() {
  ledgrid::NativeModuleWatchdogGate gate;
  TEST_ASSERT_TRUE(gate.arm());
  TEST_ASSERT_TRUE(gate.cancel());
  TEST_ASSERT_FALSE(gate.expire());
  TEST_ASSERT_TRUE(gate.arm());
  TEST_ASSERT_TRUE(gate.expire());
  TEST_ASSERT_FALSE(gate.cancel());
  TEST_ASSERT_FALSE(gate.armed());
}

}  // namespace

void setUp() {}
void tearDown() {}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_compact_typed_parameters_are_canonical_and_bounded);
  RUN_TEST(test_one_strip_fifth_receiver_upload_is_ordered_atomic_and_idempotent);
  RUN_TEST(test_wrong_geometry_and_capacity_preflight_fail_before_mutation);
  RUN_TEST(test_every_module_controlled_phase_is_attributed_and_quarantined);
  RUN_TEST(test_slow_phase_is_watchdog_failure_and_boot_marker_quarantines);
  RUN_TEST(test_failed_initialization_is_recovered_before_next_payload_activates);
  RUN_TEST(test_phase_marker_failure_never_enters_untrusted_backend);
  RUN_TEST(test_host_takeover_stops_module_and_protects_rollback_payload);
  RUN_TEST(test_probe_miss_succeeds_and_shared_payload_aliases_stay_pinned);
  RUN_TEST(test_operation_result_latch_survives_render_result_race);
  RUN_TEST(test_status_v6_prefix_offsets_and_feature_gate_are_exact);
  RUN_TEST(test_extended_config_supports_fifth_receiver_without_changing_lane_mask);
  RUN_TEST(test_interrupted_upload_rejects_conflicts_and_abort_is_clean);
  RUN_TEST(test_finalize_digest_mismatch_never_commits_or_stages);
  RUN_TEST(test_verify_and_activate_rehash_committed_payload_bytes);
  RUN_TEST(test_active_phase_attribution_is_not_confused_by_new_staged_payload);
  RUN_TEST(test_replacement_activation_releases_old_backend_and_preserves_rollback);
  RUN_TEST(test_restore_can_reconstruct_snapshot_displaced_by_activation);
  RUN_TEST(test_render_contract_requires_changed_first_frame_and_bounded_deadline);
  RUN_TEST(test_live_typed_parameter_update_is_bound_and_observable);
  RUN_TEST(test_cache_reconcile_repairs_rename_crashes_without_touching_valid_pairs);
  RUN_TEST(test_watchdog_cancel_and_callback_are_single_atomic_winner);
  return UNITY_END();
}
