#pragma once

#include <cstddef>
#include <cstdint>

namespace ledgrid {

constexpr std::size_t kFrameMailboxSlots = 3;

struct FrameMetadata {
  std::uint32_t sequence = 0;
  std::uint32_t byte_count = 0;
  std::uint16_t leds_per_strip = 0;
  std::uint8_t strip_count = 0;
  std::uint8_t brightness = 0;
};

struct FrameMailboxCounters {
  std::uint32_t accepted = 0;
  std::uint32_t displayed = 0;
  std::uint32_t superseded = 0;
  std::uint32_t publish_drops = 0;
};

class LatestFrameMailbox {
 public:
  enum class SlotState : std::uint8_t { Free, Writing, Ready, Reading };

  LatestFrameMailbox() = default;

  int begin_write() {
    for (std::size_t i = 0; i < kFrameMailboxSlots; ++i) {
      if (states_[i] == SlotState::Free) {
        states_[i] = SlotState::Writing;
        return static_cast<int>(i);
      }
    }

    // A queued frame has not begun display yet, so replacing it is explicit
    // latest-frame-wins behavior rather than unexplained loss.
    int ready = newest_slot(SlotState::Ready);
    if (ready >= 0) {
      states_[ready] = SlotState::Writing;
      ++counters_.superseded;
      return ready;
    }

    ++counters_.publish_drops;
    return -1;
  }

  bool commit_write(int slot, const FrameMetadata& metadata) {
    if (!valid_slot(slot) || states_[slot] != SlotState::Writing) return false;
    metadata_[slot] = metadata;
    states_[slot] = SlotState::Ready;
    ++counters_.accepted;
    return true;
  }

  void cancel_write(int slot) {
    if (valid_slot(slot) && states_[slot] == SlotState::Writing) {
      states_[slot] = SlotState::Free;
    }
  }

  int begin_read(FrameMetadata* metadata) {
    if (newest_slot(SlotState::Reading) >= 0) return -1;

    const int newest = newest_slot(SlotState::Ready);
    if (newest < 0) return -1;

    for (std::size_t i = 0; i < kFrameMailboxSlots; ++i) {
      if (static_cast<int>(i) != newest && states_[i] == SlotState::Ready) {
        states_[i] = SlotState::Free;
        ++counters_.superseded;
      }
    }

    states_[newest] = SlotState::Reading;
    if (metadata != nullptr) *metadata = metadata_[newest];
    return newest;
  }

  bool finish_read(int slot) {
    if (!valid_slot(slot) || states_[slot] != SlotState::Reading) return false;
    states_[slot] = SlotState::Free;
    ++counters_.displayed;
    return true;
  }

  bool cancel_read(int slot) {
    if (!valid_slot(slot) || states_[slot] != SlotState::Reading) return false;
    states_[slot] = SlotState::Free;
    return true;
  }

  SlotState state(int slot) const {
    return valid_slot(slot) ? states_[slot] : SlotState::Free;
  }

  const FrameMailboxCounters& counters() const { return counters_; }

 private:
  static bool valid_slot(int slot) {
    return slot >= 0 && slot < static_cast<int>(kFrameMailboxSlots);
  }

  int newest_slot(SlotState state) const {
    int result = -1;
    std::uint32_t sequence = 0;
    for (std::size_t i = 0; i < kFrameMailboxSlots; ++i) {
      if (states_[i] != state) continue;
      if (result < 0 || metadata_[i].sequence >= sequence) {
        result = static_cast<int>(i);
        sequence = metadata_[i].sequence;
      }
    }
    return result;
  }

  SlotState states_[kFrameMailboxSlots] = {};
  FrameMetadata metadata_[kFrameMailboxSlots] = {};
  FrameMailboxCounters counters_ = {};
};

}  // namespace ledgrid
