#include <unity.h>
#include <array>
#include <cstring>
#include "ledgrid/receiver_command_queue.hpp"
#include "ledgrid/protocol.hpp"

namespace {
using Queue = ledgrid::ReceiverCommandQueue<4, 4096>;

struct Receiver {
  Queue queue;
  ledgrid::ReceiverOperationTracker tracker;
  ledgrid::ReceiverStatusV7 live{};
  std::array<std::uint8_t, ledgrid::kStatusBytesV8> published{};
  Receiver() { publish(); }
  void receive(const std::uint8_t* bytes, std::size_t size) {
    queue.received();
    ++live.packets;
    if (bytes[0] != 8 && queue.admit(bytes, size) != Queue::Admission::Accepted)
      ++live.spi_queue_errors;
    queue.classified();
  }
  bool publish() {
    std::uint64_t ticket;
    if (!queue.publication_ticket(&ticket)) return false;
    std::array<std::uint8_t, ledgrid::kStatusBytesV8> candidate{};
    ledgrid::encode_receiver_status_v8(live, candidate.data(), candidate.size());
    if (!queue.can_publish(ticket)) return false;
    published = candidate;
    return true;
  }
  void finish(const Queue::Command& command, bool accepted = true) {
    TEST_ASSERT_TRUE(tracker.begin(command.bytes[0]));
    live.operation_sequence = tracker.sequence();
    live.last_processed_command = tracker.last_processed_command();
    live.last_result = command.rejected || !accepted
        ? ledgrid::ReceiverOperationResult::InvalidState
        : ledgrid::ReceiverOperationResult::Ok;
    queue.complete();
  }
};

void test_slow_or_hung_config_freezes_every_status_byte_while_queries_continue() {
  Receiver receiver;
  const auto initial = receiver.published;
  const std::uint8_t config[] = {0x20, 8, 0, 138, 0, 2, 0, 16};
  const std::uint8_t query[] = {8};
  receiver.receive(config, sizeof(config));
  Queue::Command command;
  TEST_ASSERT_TRUE(receiver.queue.take(&command));
  for (unsigned i = 0; i < 1200; ++i) {
    receiver.receive(query, sizeof(query));
    TEST_ASSERT_FALSE(receiver.publish());
    TEST_ASSERT_EQUAL_MEMORY(initial.data(), receiver.published.data(), initial.size());
    TEST_ASSERT_FALSE(receiver.queue.faulted());
  }
  receiver.finish(command);
  TEST_ASSERT_TRUE(receiver.publish());
  TEST_ASSERT_EQUAL_UINT32(1201, receiver.live.packets);
  TEST_ASSERT_EQUAL_UINT32(1, receiver.live.operation_sequence);
  TEST_ASSERT_EQUAL_UINT8(0x20, receiver.published[313]);
  TEST_ASSERT_EQUAL_UINT8(1, receiver.published[319]);
  TEST_ASSERT_TRUE(receiver.published != initial);
  // Subsequent distinct query boundaries advance without another mutation.
  for (unsigned i = 0; i < 3; ++i) {
    const auto previous = receiver.published;
    receiver.receive(query, sizeof(query));
    TEST_ASSERT_TRUE(receiver.publish());
    TEST_ASSERT_TRUE(receiver.published != previous);
    TEST_ASSERT_EQUAL_UINT32(1, receiver.live.operation_sequence);
  }
}

void test_unclassified_receive_and_new_admission_invalidate_snapshot_candidate() {
  Queue queue;
  std::uint64_t ticket;
  TEST_ASSERT_TRUE(queue.publication_ticket(&ticket));
  queue.received();  // ISR wins while executor holds a candidate, before decode.
  TEST_ASSERT_FALSE(queue.can_publish(ticket));
  TEST_ASSERT_FALSE(queue.publication_ticket(&ticket));
  queue.classified(); // Query bypassed the mutation queue.
  TEST_ASSERT_TRUE(queue.publication_ticket(&ticket));
  const std::uint8_t config[] = {0x20};
  queue.received();
  TEST_ASSERT_EQUAL(Queue::Admission::Accepted, queue.admit(config, sizeof(config)));
  queue.classified();
  TEST_ASSERT_FALSE(queue.can_publish(ticket));
  Queue::Command command;
  TEST_ASSERT_TRUE(queue.take(&command));
  TEST_ASSERT_FALSE(queue.publication_ticket(&ticket));
  queue.complete();
  TEST_ASSERT_TRUE(queue.publication_ticket(&ticket));
}

void test_owned_payloads_survive_dma_and_fec_scratch_reuse_in_order() {
  Queue queue;
  std::uint8_t scratch[4096];
  Queue::Command command;
  for (unsigned i = 0; i < 4; ++i) {
    std::memset(scratch, i + 1, sizeof(scratch));
    queue.received();
    TEST_ASSERT_EQUAL(Queue::Admission::Accepted, queue.admit(scratch, sizeof(scratch)));
    queue.classified();
  }
  std::memset(scratch, 0xff, sizeof(scratch));
  for (unsigned i = 0; i < 4; ++i) {
    TEST_ASSERT_TRUE(queue.take(&command));
    TEST_ASSERT_EQUAL_UINT32(sizeof(scratch), command.size);
    for (auto byte : command.bytes) TEST_ASSERT_EQUAL_UINT8(i + 1, byte);
    queue.complete();
  }
  TEST_ASSERT_FALSE(queue.take(&command));
}

void test_overflow_is_one_ordered_rejection_and_permanent_admission_fault() {
  Receiver receiver;
  const std::uint8_t config[] = {0x20};
  receiver.receive(config, sizeof(config));
  Queue::Command command;
  TEST_ASSERT_TRUE(receiver.queue.take(&command));
  for (unsigned i = 0; i < 4; ++i) {
    const std::uint8_t frame[] = {static_cast<std::uint8_t>(i + 1)};
    receiver.receive(frame, sizeof(frame));
  }
  TEST_ASSERT_TRUE(receiver.queue.faulted());
  TEST_ASSERT_EQUAL_UINT32(1, receiver.live.spi_queue_errors);
  TEST_ASSERT_FALSE(receiver.publish());
  receiver.finish(command);
  for (unsigned i = 0; i < 4; ++i) {
    TEST_ASSERT_TRUE(receiver.queue.take(&command));
    TEST_ASSERT_EQUAL_UINT8(i + 1, command.bytes[0]);
    TEST_ASSERT_EQUAL(i == 3, command.rejected);
    receiver.finish(command);
  }
  TEST_ASSERT_TRUE(receiver.publish());
  TEST_ASSERT_EQUAL_UINT32(5, receiver.live.operation_sequence);
  TEST_ASSERT_EQUAL(ledgrid::ReceiverOperationResult::InvalidState, receiver.live.last_result);
  receiver.receive(config, sizeof(config));
  TEST_ASSERT_FALSE(receiver.queue.take(&command));
  TEST_ASSERT_EQUAL_UINT32(2, receiver.live.spi_queue_errors);
  TEST_ASSERT_TRUE(receiver.publish());
  TEST_ASSERT_EQUAL_UINT32(5, receiver.live.operation_sequence);
}

void test_failed_command_completes_with_failure_and_reset_has_no_old_work() {
  Receiver receiver;
  const std::uint8_t config[] = {0x20};
  receiver.receive(config, sizeof(config));
  Queue::Command command;
  TEST_ASSERT_TRUE(receiver.queue.take(&command));
  receiver.finish(command, false);
  TEST_ASSERT_TRUE(receiver.publish());
  TEST_ASSERT_EQUAL(ledgrid::ReceiverOperationResult::InvalidState, receiver.live.last_result);
  receiver.receive(config, sizeof(config));
  TEST_ASSERT_TRUE(receiver.queue.pending());
  receiver = Receiver(); // Reboot creates all state and DMA snapshots afresh.
  TEST_ASSERT_FALSE(receiver.queue.pending());
  TEST_ASSERT_FALSE(receiver.queue.faulted());
  TEST_ASSERT_FALSE(receiver.queue.take(&command));
  TEST_ASSERT_EQUAL_UINT32(0, receiver.live.packets);
  TEST_ASSERT_EQUAL_UINT32(0, receiver.live.operation_sequence);
}
}
void setUp() {}
void tearDown() {}
int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_slow_or_hung_config_freezes_every_status_byte_while_queries_continue);
  RUN_TEST(test_unclassified_receive_and_new_admission_invalidate_snapshot_candidate);
  RUN_TEST(test_owned_payloads_survive_dma_and_fec_scratch_reuse_in_order);
  RUN_TEST(test_overflow_is_one_ordered_rejection_and_permanent_admission_fault);
  RUN_TEST(test_failed_command_completes_with_failure_and_reset_has_no_old_work);
  return UNITY_END();
}
