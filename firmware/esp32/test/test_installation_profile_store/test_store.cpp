#include <unity.h>

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <vector>

#include "../fixtures/installation_profile_receiver_v1.hpp"
#include "ledgrid/installation_profile_store.hpp"
#include "ledgrid/receiver_runtime.hpp"
#include "ledgrid/sha256.hpp"

namespace {

void append_u16(std::vector<std::uint8_t>* output, std::uint16_t value) {
  output->push_back(static_cast<std::uint8_t>(value >> 8U));
  output->push_back(static_cast<std::uint8_t>(value));
}
void append_u32(std::vector<std::uint8_t>* output, std::uint32_t value) {
  output->push_back(static_cast<std::uint8_t>(value >> 24U));
  output->push_back(static_cast<std::uint8_t>(value >> 16U));
  output->push_back(static_cast<std::uint8_t>(value >> 8U));
  output->push_back(static_cast<std::uint8_t>(value));
}
void append_u64(std::vector<std::uint8_t>* output, std::uint64_t value) {
  for (unsigned shift = 56; shift <= 56; shift -= 8)
    output->push_back(static_cast<std::uint8_t>(value >> shift));
}

bool same(const std::uint8_t* left, const std::uint8_t* right) {
  return std::memcmp(left, right, 32) == 0;
}

bool pinned(
    const std::uint8_t digest[32],
    const ledgrid::InstallationProfileLedger& ledger) {
  for (const auto* binding : {&ledger.active, &ledger.staged, &ledger.rollback}) {
    if (binding->present && same(digest, binding->payload_digest)) return true;
  }
  return false;
}

class MemoryStore final : public ledgrid::InstallationProfileStore {
 public:
  struct Entry {
    std::array<std::uint8_t, 32> digest{};
    std::vector<std::uint8_t> bytes;
    std::uint32_t access = 0;
  };

  bool ready() const override { return available; }
  std::uint32_t capacity_bytes() const override { return capacity; }
  std::uint32_t used_bytes() const override {
    std::uint32_t result = 0;
    for (const auto& entry : entries) result += entry.bytes.size();
    return result;
  }
  std::uint32_t reserve_bytes() const override { return reserve; }
  std::uint64_t mutation_generation() const override { return generation; }
  bool probe(const std::uint8_t digest[32], std::uint32_t* size) const override {
    for (const auto& entry : entries) {
      if (same(digest, entry.digest.data())) {
        if (size != nullptr) *size = entry.bytes.size();
        return true;
      }
    }
    return false;
  }
  bool touch(const std::uint8_t digest[32]) override {
    for (auto& entry : entries) {
      if (same(digest, entry.digest.data())) {
        entry.access = ++clock;
        ++generation;
        return true;
      }
    }
    return false;
  }
  bool can_stage(
      std::uint32_t size, const ledgrid::InstallationProfileLedger& pins,
      std::uint32_t* reclaimable) const override {
    std::uint32_t reclaim = 0;
    for (const auto& entry : entries)
      if (!pinned(entry.digest.data(), pins)) reclaim += entry.bytes.size();
    if (reclaimable != nullptr) *reclaimable = reclaim;
    const std::uint32_t usable = capacity > reserve ? capacity - reserve : 0;
    return size <= usable && used_bytes() - reclaim <= usable - size;
  }
  bool begin_part(
      const std::uint8_t digest[32], std::uint32_t size,
      const ledgrid::InstallationProfileLedger& pins,
      std::uint32_t* evicted) override {
    std::uint32_t reclaim = 0;
    if (!can_stage(size, pins, &reclaim)) return false;
    std::uint32_t count = 0;
    while (used_bytes() + size > capacity - reserve) {
      auto selected = entries.end();
      for (auto it = entries.begin(); it != entries.end(); ++it) {
        if (!pinned(it->digest.data(), pins) &&
            (selected == entries.end() || it->access < selected->access)) {
          selected = it;
        }
      }
      if (selected == entries.end()) return false;
      entries.erase(selected);
      ++count;
      ++generation;
    }
    part_digest = {};
    std::memcpy(part_digest.data(), digest, 32);
    part.clear();
    part.reserve(size);
    part_size = size;
    if (evicted != nullptr) *evicted = count;
    ++generation;
    return true;
  }
  bool write_part(
      std::uint32_t offset, const std::uint8_t* data,
      std::size_t size) override {
    if (!allow_writes || offset != part.size() || size > part_size - offset)
      return false;
    part.insert(part.end(), data, data + size);
    return true;
  }
  bool read_part(
      std::uint32_t offset, std::uint8_t* data,
      std::size_t size) const override {
    if (offset > part.size() || size > part.size() - offset) return false;
    std::memcpy(data, part.data() + offset, size);
    return true;
  }
  bool commit_part(const std::uint8_t digest[32]) override {
    if (!allow_commit || part.size() != part_size || !same(digest, part_digest.data()))
      return false;
    entries.push_back({part_digest, part, ++clock});
    part.clear();
    ++generation;
    return true;
  }
  void abort_part() override { part.clear(); part_size = 0; ++generation; }
  bool read_committed(
      const std::uint8_t digest[32], std::uint32_t offset,
      std::uint8_t* data, std::size_t size) const override {
    for (const auto& entry : entries) {
      if (same(digest, entry.digest.data()) && offset <= entry.bytes.size() &&
          size <= entry.bytes.size() - offset) {
        std::memcpy(data, entry.bytes.data() + offset, size);
        return true;
      }
    }
    return false;
  }
  bool remove(const std::uint8_t digest[32]) override {
    const auto found = std::find_if(entries.begin(), entries.end(), [&](const Entry& e) {
      return same(digest, e.digest.data());
    });
    if (found == entries.end()) return true;
    entries.erase(found);
    ++generation;
    return true;
  }
  void seed(const std::uint8_t digest[32], std::vector<std::uint8_t> bytes,
            std::uint32_t access) {
    Entry entry{};
    std::memcpy(entry.digest.data(), digest, 32);
    entry.bytes = std::move(bytes);
    entry.access = access;
    entries.push_back(std::move(entry));
    ++generation;
  }

  bool available = true;
  bool allow_writes = true;
  bool allow_commit = true;
  std::uint32_t capacity = 128U * 1024U;
  std::uint32_t reserve = 4096;
  std::uint64_t generation = 1;
  std::uint32_t clock = 1;
  std::vector<Entry> entries;
  std::vector<std::uint8_t> part;
  std::array<std::uint8_t, 32> part_digest{};
  std::uint32_t part_size = 0;
};

class MemoryPersistence final : public ledgrid::InstallationProfilePersistence {
 public:
  bool load(ledgrid::InstallationProfileLedger* output) override {
    if (!allow || output == nullptr) return false;
    *output = ledger;
    return true;
  }
  bool save(const ledgrid::InstallationProfileLedger& value) override {
    if (!allow) return false;
    ledger = value;
    ++saves;
    return true;
  }
  bool allow = true;
  int saves = 0;
  ledgrid::InstallationProfileLedger ledger{};
};

struct Harness {
  Harness(bool enabled = true)
      : manager(&store, &persistence, scratch.data(), scratch.size(), enabled) {
    const auto& fixture = ledgrid::installation_profile_fixture::kInstalledReceivers[0];
    payload.assign(fixture.bytes, fixture.bytes + fixture.size);
    ledgrid::sha256(payload.data(), payload.size(), payload_digest.data());
    global_id.fill(0xA5);
    manager.configure_identity(0, false);
    started = manager.begin();
  }

  std::vector<std::uint8_t> preflight() const {
    std::vector<std::uint8_t> out{0x40};
    out.insert(out.end(), global_id.begin(), global_id.end());
    out.insert(out.end(), payload_digest.begin(), payload_digest.end());
    append_u32(&out, payload.size());
    return out;
  }
  std::vector<std::uint8_t> begin() const {
    std::vector<std::uint8_t> out{0x41};
    append_u64(&out, manager.status().preflight_token);
    out.insert(out.end(), global_id.begin(), global_id.end());
    out.insert(out.end(), payload_digest.begin(), payload_digest.end());
    append_u32(&out, payload.size());
    out.push_back(0);
    append_u16(&out, 0);
    out.push_back(0);
    return out;
  }
  std::vector<std::uint8_t> binding_command(std::uint8_t command) const {
    std::vector<std::uint8_t> out{command};
    out.insert(out.end(), global_id.begin(), global_id.end());
    out.insert(out.end(), payload_digest.begin(), payload_digest.end());
    return out;
  }
  void upload() {
    auto pre = preflight();
    TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
        manager.process(pre.data(), pre.size())));
    auto start = begin();
    TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
        manager.process(start.data(), start.size())));
    std::size_t offset = 0;
    while (offset < payload.size()) {
      const std::size_t amount = std::min<std::size_t>(4089, payload.size() - offset);
      std::vector<std::uint8_t> chunk{0x42};
      append_u32(&chunk, offset);
      chunk.insert(chunk.end(), payload.begin() + offset,
                   payload.begin() + offset + amount);
      TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
          manager.process(chunk.data(), chunk.size())));
      TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
          manager.process(chunk.data(), chunk.size())));
      offset += amount;
    }
    auto finish = binding_command(0x43);
    TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
        manager.process(finish.data(), finish.size())));
  }

  MemoryStore store;
  MemoryPersistence persistence;
  std::array<std::uint8_t, 2 * ledgrid::kInstallationProfileReceiverBytesV1> scratch{};
  ledgrid::InstallationProfileManager manager;
  std::vector<std::uint8_t> payload;
  std::array<std::uint8_t, 32> payload_digest{};
  std::array<std::uint8_t, 32> global_id{};
  bool started = false;
};

void send_payload_chunks(
    ledgrid::InstallationProfileManager* manager,
    const std::vector<std::uint8_t>& payload) {
  std::size_t offset = 0;
  while (offset < payload.size()) {
    const std::size_t amount = std::min<std::size_t>(
        ledgrid::kInstallationProfileMaxChunkBytes, payload.size() - offset);
    std::vector<std::uint8_t> chunk{0x42};
    append_u32(&chunk, offset);
    chunk.insert(chunk.end(), payload.begin() + offset,
                 payload.begin() + offset + amount);
    TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
        manager->process(chunk.data(), chunk.size())));
    offset += amount;
  }
}

std::vector<std::uint8_t> make_binding_command(
    std::uint8_t command, const std::array<std::uint8_t, 32>& global_id,
    const std::array<std::uint8_t, 32>& payload_digest) {
  std::vector<std::uint8_t> out{command};
  out.insert(out.end(), global_id.begin(), global_id.end());
  out.insert(out.end(), payload_digest.begin(), payload_digest.end());
  return out;
}

void test_ordered_retryable_upload_verify_activate_and_exact_restore() {
  Harness value;
  TEST_ASSERT_TRUE(value.started);
  value.upload();
  auto status = value.manager.status();
  TEST_ASSERT_EQUAL_UINT8(4, static_cast<std::uint8_t>(status.transfer_state));
  TEST_ASSERT_EQUAL_UINT32(value.payload.size(), status.received_bytes);
  TEST_ASSERT_EQUAL_UINT16(1, status.stages);
  TEST_ASSERT_TRUE(value.manager.ledger().staged.present);

  auto verify = value.binding_command(0x44);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      value.manager.process(verify.data(), verify.size())));
  const std::uint64_t staged_generation = value.manager.ledger().generation;
  std::vector<std::uint8_t> activate{0x45};
  append_u64(&activate, staged_generation);
  activate.insert(activate.end(), value.global_id.begin(), value.global_id.end());
  activate.insert(activate.end(), value.payload_digest.begin(), value.payload_digest.end());
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      value.manager.process(activate.data(), activate.size())));
  auto wrong_retry_generation = activate;
  ++wrong_retry_generation[8];
  TEST_ASSERT_EQUAL_UINT8(14, static_cast<std::uint8_t>(
      value.manager.process(wrong_retry_generation.data(),
                            wrong_retry_generation.size())));
  TEST_ASSERT_TRUE(value.manager.ledger().active.present);
  TEST_ASSERT_FALSE(value.manager.ledger().staged.present);
  TEST_ASSERT_NOT_NULL(value.manager.active_view().category);
  TEST_ASSERT_EQUAL_UINT16(0, value.manager.active_view().strip_origin);
  TEST_ASSERT_EQUAL_UINT8(1, value.manager.status().activations);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      value.manager.process(activate.data(), activate.size())));

  std::vector<std::uint8_t> restore{0x46};
  append_u64(&restore, value.manager.ledger().generation);
  restore.insert(restore.end(), 3 * 65, 0);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      value.manager.process(restore.data(), restore.size())));
  TEST_ASSERT_FALSE(value.manager.ledger().active.present);
  TEST_ASSERT_FALSE(value.manager.ledger().staged.present);
  TEST_ASSERT_FALSE(value.manager.ledger().rollback.present);
  TEST_ASSERT_EQUAL_UINT8(1, value.manager.status().restores);
}

void test_preflight_is_read_only_and_chunk_conflicts_fail_closed() {
  Harness value;
  const auto store_generation = value.store.generation;
  auto pre = value.preflight();
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      value.manager.process(pre.data(), pre.size())));
  TEST_ASSERT_EQUAL_UINT64(store_generation, value.store.generation);
  auto start = value.begin();
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      value.manager.process(start.data(), start.size())));
  std::vector<std::uint8_t> gap{0x42, 0, 0, 0, 1, 7};
  TEST_ASSERT_EQUAL_UINT8(6, static_cast<std::uint8_t>(
      value.manager.process(gap.data(), gap.size())));
  std::vector<std::uint8_t> first{0x42, 0, 0, 0, 0,
                                  value.payload[0], value.payload[1]};
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      value.manager.process(first.data(), first.size())));
  value.scratch[0] = 0xCC;
  value.scratch[1] = 0xCC;
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      value.manager.process(first.data(), first.size())));
  TEST_ASSERT_EQUAL_HEX8(0xCC, value.scratch[0]);
  TEST_ASSERT_EQUAL_HEX8(0xCC, value.scratch[1]);
  auto conflict = first;
  conflict[5] ^= 1;
  TEST_ASSERT_EQUAL_UINT8(14, static_cast<std::uint8_t>(
      value.manager.process(conflict.data(), conflict.size())));
  std::vector<std::uint8_t> abort{0x47};
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      value.manager.process(abort.data(), abort.size())));
  TEST_ASSERT_TRUE(value.store.part.empty());
}

void test_committed_cache_hit_does_not_require_duplicate_capacity() {
  Harness value;
  value.upload();
  value.store.capacity = value.store.reserve;
  const std::uint64_t mutation = value.store.generation;
  auto pre = value.preflight();
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      value.manager.process(pre.data(), pre.size())));
  TEST_ASSERT_EQUAL_UINT64(mutation, value.store.generation);
  auto start = value.begin();
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      value.manager.process(start.data(), start.size())));
  TEST_ASSERT_TRUE(value.manager.ledger().staged.present);
}

void test_generation_cas_wrong_identity_digest_and_storage_failures_are_atomic() {
  Harness value;
  auto pre = value.preflight();
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      value.manager.process(pre.data(), pre.size())));
  auto start = value.begin();
  start[77] = 1;
  TEST_ASSERT_EQUAL_UINT8(9, static_cast<std::uint8_t>(
      value.manager.process(start.data(), start.size())));
  start = value.begin();
  start[79] = 8;
  TEST_ASSERT_EQUAL_UINT8(10, static_cast<std::uint8_t>(
      value.manager.process(start.data(), start.size())));
  start = value.begin();
  start[80] = 1;
  TEST_ASSERT_EQUAL_UINT8(10, static_cast<std::uint8_t>(
      value.manager.process(start.data(), start.size())));
  start = value.begin();
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      value.manager.process(start.data(), start.size())));
  value.store.allow_writes = false;
  std::vector<std::uint8_t> chunk{0x42, 0, 0, 0, 0, value.payload[0]};
  TEST_ASSERT_EQUAL_UINT8(11, static_cast<std::uint8_t>(
      value.manager.process(chunk.data(), chunk.size())));
  TEST_ASSERT_FALSE(value.manager.ledger().staged.present);
}

void test_stale_preflight_token_and_incomplete_finalize_are_rejected() {
  Harness stale;
  auto pre = stale.preflight();
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      stale.manager.process(pre.data(), pre.size())));
  auto start = stale.begin();
  stale.store.abort_part();
  TEST_ASSERT_EQUAL_UINT8(5, static_cast<std::uint8_t>(
      stale.manager.process(start.data(), start.size())));

  Harness incomplete;
  pre = incomplete.preflight();
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      incomplete.manager.process(pre.data(), pre.size())));
  start = incomplete.begin();
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      incomplete.manager.process(start.data(), start.size())));
  std::vector<std::uint8_t> chunk{0x42, 0, 0, 0, 0,
                                  incomplete.payload[0]};
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      incomplete.manager.process(chunk.data(), chunk.size())));
  auto finish = incomplete.binding_command(0x43);
  TEST_ASSERT_EQUAL_UINT8(4, static_cast<std::uint8_t>(
      incomplete.manager.process(finish.data(), finish.size())));
}

void test_digest_mismatch_and_strict_decoder_fail_before_visibility() {
  Harness digest_mismatch;
  auto pre = digest_mismatch.preflight();
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      digest_mismatch.manager.process(pre.data(), pre.size())));
  auto start = digest_mismatch.begin();
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      digest_mismatch.manager.process(start.data(), start.size())));
  auto changed = digest_mismatch.payload;
  changed[changed.size() / 2] ^= 0x01;
  send_payload_chunks(&digest_mismatch.manager, changed);
  auto finish = digest_mismatch.binding_command(0x43);
  TEST_ASSERT_EQUAL_UINT8(7, static_cast<std::uint8_t>(
      digest_mismatch.manager.process(finish.data(), finish.size())));
  TEST_ASSERT_FALSE(digest_mismatch.manager.ledger().staged.present);

  Harness malformed;
  malformed.payload[0] ^= 0x01;
  ledgrid::sha256(malformed.payload.data(), malformed.payload.size(),
                  malformed.payload_digest.data());
  pre = malformed.preflight();
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      malformed.manager.process(pre.data(), pre.size())));
  start = malformed.begin();
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      malformed.manager.process(start.data(), start.size())));
  send_payload_chunks(&malformed.manager, malformed.payload);
  finish = malformed.binding_command(0x43);
  TEST_ASSERT_EQUAL_UINT8(8, static_cast<std::uint8_t>(
      malformed.manager.process(finish.data(), finish.size())));
  TEST_ASSERT_FALSE(malformed.manager.ledger().staged.present);
}

void test_verify_corruption_and_activation_generation_fail_closed() {
  Harness value;
  value.upload();
  std::vector<std::uint8_t> stale_activate{0x45};
  append_u64(&stale_activate, value.manager.ledger().generation - 1);
  stale_activate.insert(stale_activate.end(), value.global_id.begin(),
                        value.global_id.end());
  stale_activate.insert(stale_activate.end(), value.payload_digest.begin(),
                        value.payload_digest.end());
  TEST_ASSERT_EQUAL_UINT8(14, static_cast<std::uint8_t>(
      value.manager.process(stale_activate.data(), stale_activate.size())));
  TEST_ASSERT_FALSE(value.manager.ledger().active.present);

  TEST_ASSERT_EQUAL_UINT32(1, value.store.entries.size());
  value.store.entries[0].bytes[100] ^= 0x01;
  auto verify = value.binding_command(0x44);
  TEST_ASSERT_EQUAL_UINT8(16, static_cast<std::uint8_t>(
      value.manager.process(verify.data(), verify.size())));
  TEST_ASSERT_EQUAL_UINT8(0, value.manager.status().flags & 1U);
}

void test_restore_rejects_noncanonical_absent_and_missing_backing() {
  Harness value;
  std::vector<std::uint8_t> restore{0x46};
  append_u64(&restore, value.manager.ledger().generation);
  restore.insert(restore.end(), 3 * 65, 0);
  restore[10] = 1;
  TEST_ASSERT_EQUAL_UINT8(16, static_cast<std::uint8_t>(
      value.manager.process(restore.data(), restore.size())));

  restore.assign(1, 0x46);
  append_u64(&restore, value.manager.ledger().generation);
  restore.insert(restore.end(), 3 * 65, 0);
  restore[9] = 1;
  std::fill(restore.begin() + 10, restore.begin() + 42, 0xA1);
  std::fill(restore.begin() + 42, restore.begin() + 74, 0xB2);
  TEST_ASSERT_EQUAL_UINT8(16, static_cast<std::uint8_t>(
      value.manager.process(restore.data(), restore.size())));
  TEST_ASSERT_FALSE(value.manager.ledger().active.present);
}

void test_abort_discards_interrupted_upload_and_restart_can_retry() {
  Harness value;
  auto pre = value.preflight();
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      value.manager.process(pre.data(), pre.size())));
  auto start = value.begin();
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      value.manager.process(start.data(), start.size())));
  std::vector<std::uint8_t> chunk{0x42, 0, 0, 0, 0,
                                  value.payload[0], value.payload[1]};
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      value.manager.process(chunk.data(), chunk.size())));
  std::vector<std::uint8_t> abort{0x47};
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      value.manager.process(abort.data(), abort.size())));
  TEST_ASSERT_TRUE(value.store.part.empty());

  std::array<std::uint8_t,
             2 * ledgrid::kInstallationProfileReceiverBytesV1> restarted_scratch{};
  ledgrid::InstallationProfileManager restarted(
      &value.store, &value.persistence, restarted_scratch.data(),
      restarted_scratch.size(), true);
  TEST_ASSERT_TRUE(restarted.begin());
  restarted.configure_identity(0, false);
  pre = value.preflight();
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      restarted.process(pre.data(), pre.size())));
  start = value.begin();
  start.clear();
  start.push_back(0x41);
  append_u64(&start, restarted.status().preflight_token);
  start.insert(start.end(), value.global_id.begin(), value.global_id.end());
  start.insert(start.end(), value.payload_digest.begin(), value.payload_digest.end());
  append_u32(&start, value.payload.size());
  start.push_back(0);
  append_u16(&start, 0);
  start.push_back(0);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      restarted.process(start.data(), start.size())));
  send_payload_chunks(&restarted, value.payload);
  auto finish = make_binding_command(0x43, value.global_id, value.payload_digest);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      restarted.process(finish.data(), finish.size())));
}

void test_corrupt_persisted_active_is_cleared_and_can_be_repaired() {
  const auto& fixture =
      ledgrid::installation_profile_fixture::kInstalledReceivers[0];
  std::vector<std::uint8_t> payload(fixture.bytes, fixture.bytes + fixture.size);
  std::array<std::uint8_t, 32> digest{};
  ledgrid::sha256(payload.data(), payload.size(), digest.data());
  auto corrupt = payload;
  corrupt[100] ^= 0x01;
  MemoryStore store;
  store.seed(digest.data(), corrupt, 1);
  MemoryPersistence persistence;
  persistence.ledger.generation = 3;
  persistence.ledger.active.present = true;
  std::memset(persistence.ledger.active.global_id, 0xA5, 32);
  std::memcpy(persistence.ledger.active.payload_digest, digest.data(), 32);
  std::array<std::uint8_t, 32> global_id{};
  global_id.fill(0xA5);
  std::array<std::uint8_t,
             2 * ledgrid::kInstallationProfileReceiverBytesV1> scratch{};
  ledgrid::InstallationProfileManager manager(
      &store, &persistence, scratch.data(), scratch.size(), true);
  TEST_ASSERT_TRUE(manager.begin());
  manager.configure_identity(0, false);
  TEST_ASSERT_FALSE(manager.ledger().active.present);
  TEST_ASSERT_EQUAL_UINT8(16, static_cast<std::uint8_t>(manager.status().result));
  TEST_ASSERT_EQUAL_UINT8(1, manager.status().flags & 1U);

  auto pre = make_binding_command(0x40, global_id, digest);
  append_u32(&pre, payload.size());
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      manager.process(pre.data(), pre.size())));
  std::vector<std::uint8_t> start{0x41};
  append_u64(&start, manager.status().preflight_token);
  start.insert(start.end(), global_id.begin(), global_id.end());
  start.insert(start.end(), digest.begin(), digest.end());
  append_u32(&start, payload.size());
  start.push_back(0);
  append_u16(&start, 0);
  start.push_back(0);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      manager.process(start.data(), start.size())));
  send_payload_chunks(&manager, payload);
  auto finish = make_binding_command(0x43, global_id, digest);
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(
      manager.process(finish.data(), finish.size())));
  TEST_ASSERT_TRUE(manager.ledger().staged.present);
  TEST_ASSERT_EQUAL_UINT8(1, manager.status().flags & 1U);
}

void test_status_v5_offsets_and_profile_dispatch_never_claim_display() {
  ledgrid::ReceiverStatusV5 status{};
  status.frames_accepted = 0x01020304;
  status.overlay_commits = 0x11223344;
  auto& profile = status.installation_profile;
  profile.result = ledgrid::InstallationProfileResult::Pinned;
  profile.transfer_state = ledgrid::InstallationProfileTransferState::Receiving;
  profile.state_generation = 0x0102030405060708ULL;
  profile.preflight_token = 0x1112131415161718ULL;
  profile.writes = 0x21222324;
  profile.restores = 0x3132;
  for (std::size_t index = 0; index < 32; ++index)
    profile.active_payload_digest[index] = static_cast<std::uint8_t>(index);
  std::array<std::uint8_t, ledgrid::kStatusBytesV5> encoded{};
  TEST_ASSERT_TRUE(ledgrid::encode_receiver_status_v5(
      status, encoded.data(), encoded.size()));
  TEST_ASSERT_EQUAL_MEMORY("LGS5", encoded.data(), 4);
  TEST_ASSERT_EQUAL_UINT8(5, encoded[4]);
  TEST_ASSERT_EQUAL_HEX8(0x01, encoded[24]);
  TEST_ASSERT_EQUAL_HEX8(0x11, encoded[408]);
  TEST_ASSERT_EQUAL_UINT8(15, encoded[416]);
  TEST_ASSERT_EQUAL_UINT8(2, encoded[417]);
  TEST_ASSERT_EQUAL_HEX8(0x01, encoded[448]);
  TEST_ASSERT_EQUAL_MEMORY(profile.active_payload_digest, encoded.data() + 592, 32);
  TEST_ASSERT_EQUAL_HEX8(0x21, encoded[752]);
  TEST_ASSERT_EQUAL_HEX8(0x31, encoded[766]);

  for (std::uint8_t command = 0x40; command <= 0x47; ++command) {
    std::size_t size = command == 0x40 ? 69 : command == 0x41 ? 81 :
                       command == 0x42 ? 6 : command == 0x43 ? 65 :
                       command == 0x44 ? 65 : command == 0x45 ? 73 :
                       command == 0x46 ? 204 : 1;
    std::vector<std::uint8_t> bytes(size, 0);
    bytes[0] = command;
    const auto decision = ledgrid::classify_receiver_dispatch(
        bytes.data(), bytes.size(), 3312, ledgrid::BaseMode::LocalBackground,
        true, true);
    TEST_ASSERT_EQUAL_UINT8(5, static_cast<std::uint8_t>(decision.route));
    TEST_ASSERT_FALSE(decision.publishes_host_frame);
    TEST_ASSERT_FALSE(decision.may_claim_base);
    const auto disabled = ledgrid::classify_receiver_dispatch(
        bytes.data(), bytes.size(), 3312, ledgrid::BaseMode::HostFullScene,
        true, false);
    TEST_ASSERT_EQUAL_UINT8(0, static_cast<std::uint8_t>(disabled.route));
    TEST_ASSERT_EQUAL_UINT8(2, static_cast<std::uint8_t>(disabled.result));
  }
  std::vector<std::uint8_t> maximum_chunk(4094, 0);
  maximum_chunk[0] = 0x42;
  auto maximum = ledgrid::classify_receiver_dispatch(
      maximum_chunk.data(), maximum_chunk.size(), 3312,
      ledgrid::BaseMode::StartupFallback, false, true);
  TEST_ASSERT_EQUAL_UINT8(5, static_cast<std::uint8_t>(maximum.route));
  maximum_chunk.push_back(0);
  auto oversized = ledgrid::classify_receiver_dispatch(
      maximum_chunk.data(), maximum_chunk.size(), 3312,
      ledgrid::BaseMode::StartupFallback, false, true);
  TEST_ASSERT_EQUAL_UINT8(0, static_cast<std::uint8_t>(oversized.route));
  TEST_ASSERT_EQUAL_UINT32(4089, ledgrid::kInstallationProfileMaxChunkBytes);
}

void test_disabled_manager_and_bad_boot_storage_fail_closed() {
  Harness disabled(false);
  TEST_ASSERT_FALSE(disabled.started);
  auto pre = disabled.preflight();
  TEST_ASSERT_EQUAL_UINT8(2, static_cast<std::uint8_t>(
      disabled.manager.process(pre.data(), pre.size())));
  Harness unavailable;
  TEST_ASSERT_TRUE(unavailable.started);
  unavailable.persistence.allow = false;
  std::array<std::uint8_t, 2 * ledgrid::kInstallationProfileReceiverBytesV1> scratch{};
  ledgrid::InstallationProfileManager failed(
      &unavailable.store, &unavailable.persistence, scratch.data(), scratch.size());
  failed.configure_identity(0, false);
  TEST_ASSERT_FALSE(failed.begin());
  TEST_ASSERT_EQUAL_UINT8(16, static_cast<std::uint8_t>(failed.status().result));
}

void test_persisted_views_wait_for_explicit_installed_topology_config() {
  for (std::size_t logical_id = 0; logical_id < 4; ++logical_id) {
    const auto& fixture =
        ledgrid::installation_profile_fixture::kInstalledReceivers[logical_id];
    std::vector<std::uint8_t> payload(fixture.bytes, fixture.bytes + fixture.size);
    std::array<std::uint8_t, 32> digest{};
    ledgrid::sha256(payload.data(), payload.size(), digest.data());
    MemoryStore store;
    store.seed(digest.data(), payload, 1);
    MemoryPersistence persistence;
    persistence.ledger.generation = 7;
    persistence.ledger.active.present = true;
    std::memset(persistence.ledger.active.global_id, 0xA0 + logical_id, 32);
    std::memcpy(persistence.ledger.active.payload_digest, digest.data(), 32);
    std::array<std::uint8_t,
               2 * ledgrid::kInstallationProfileReceiverBytesV1> scratch{};
    ledgrid::InstallationProfileManager manager(
        &store, &persistence, scratch.data(), scratch.size(), true);
    TEST_ASSERT_TRUE(manager.begin());
    TEST_ASSERT_NULL(manager.active_view().encoded);
    std::vector<std::uint8_t> preflight(69, 0);
    preflight[0] = 0x40;
    TEST_ASSERT_EQUAL_UINT8(4, static_cast<std::uint8_t>(
        manager.process(preflight.data(), preflight.size())));
    manager.configure_identity(
        static_cast<std::uint8_t>(logical_id), fixture.reversed_strip_order);
    TEST_ASSERT_NOT_NULL(manager.active_view().encoded);
    TEST_ASSERT_EQUAL_UINT16(fixture.strip_origin,
                             manager.active_view().strip_origin);
    TEST_ASSERT_EQUAL(fixture.reversed_strip_order,
                      manager.active_view().reversed_strip_order);
    TEST_ASSERT_TRUE(manager.ledger().active.present);
  }
}

void test_cache_preflight_and_lru_eviction_protect_all_three_pins() {
  MemoryStore store;
  store.reserve = 10;
  store.capacity = 50;
  ledgrid::InstallationProfileLedger pins{};
  std::array<std::array<std::uint8_t, 32>, 4> digests{};
  for (std::size_t index = 0; index < digests.size(); ++index)
    digests[index][0] = static_cast<std::uint8_t>(index + 1);
  auto bind = [&](ledgrid::InstallationProfileBinding* binding,
                  std::size_t digest) {
    binding->present = true;
    std::memset(binding->global_id, 0x80 + digest, 32);
    std::memcpy(binding->payload_digest, digests[digest].data(), 32);
  };
  bind(&pins.active, 0);
  bind(&pins.staged, 1);
  bind(&pins.rollback, 2);
  store.seed(digests[0].data(), std::vector<std::uint8_t>(10, 1), 30);
  store.seed(digests[1].data(), std::vector<std::uint8_t>(10, 2), 20);
  store.seed(digests[2].data(), std::vector<std::uint8_t>(10, 3), 10);
  store.seed(digests[3].data(), std::vector<std::uint8_t>(10, 4), 1);
  std::uint32_t reclaimable = 0;
  TEST_ASSERT_TRUE(store.can_stage(10, pins, &reclaimable));
  TEST_ASSERT_EQUAL_UINT32(10, reclaimable);
  std::uint32_t evicted = 0;
  TEST_ASSERT_TRUE(store.begin_part(digests[3].data(), 10, pins, &evicted));
  TEST_ASSERT_EQUAL_UINT32(1, evicted);
  std::uint32_t ignored = 0;
  TEST_ASSERT_TRUE(store.probe(digests[0].data(), &ignored));
  TEST_ASSERT_TRUE(store.probe(digests[1].data(), &ignored));
  TEST_ASSERT_TRUE(store.probe(digests[2].data(), &ignored));
  TEST_ASSERT_FALSE(store.probe(digests[3].data(), &ignored));
}

}  // namespace

void setUp() {}
void tearDown() {}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_ordered_retryable_upload_verify_activate_and_exact_restore);
  RUN_TEST(test_preflight_is_read_only_and_chunk_conflicts_fail_closed);
  RUN_TEST(test_committed_cache_hit_does_not_require_duplicate_capacity);
  RUN_TEST(test_generation_cas_wrong_identity_digest_and_storage_failures_are_atomic);
  RUN_TEST(test_stale_preflight_token_and_incomplete_finalize_are_rejected);
  RUN_TEST(test_digest_mismatch_and_strict_decoder_fail_before_visibility);
  RUN_TEST(test_verify_corruption_and_activation_generation_fail_closed);
  RUN_TEST(test_restore_rejects_noncanonical_absent_and_missing_backing);
  RUN_TEST(test_abort_discards_interrupted_upload_and_restart_can_retry);
  RUN_TEST(test_corrupt_persisted_active_is_cleared_and_can_be_repaired);
  RUN_TEST(test_status_v5_offsets_and_profile_dispatch_never_claim_display);
  RUN_TEST(test_disabled_manager_and_bad_boot_storage_fail_closed);
  RUN_TEST(test_persisted_views_wait_for_explicit_installed_topology_config);
  RUN_TEST(test_cache_preflight_and_lru_eviction_protect_all_three_pins);
  return UNITY_END();
}
