#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>

namespace ledgrid {

// The caller holds the short admission/publication lock for every operation.
// received() also runs in the SPI completion ISR: an unclassified DMA packet
// must fence publication even before the service task knows its command.
template <std::size_t Capacity, std::size_t MaxBytes>
class ReceiverCommandQueue {
 public:
  struct Command {
    std::uint8_t bytes[MaxBytes]{};
    std::size_t size = 0;
    bool rejected = false;
  };
  enum class Admission { Accepted, Overflow, Faulted };

  void received() { ++received_; }
  void classified() { ++classified_; }

  Admission admit(const std::uint8_t* bytes, std::size_t size) {
    if (faulted_) return Admission::Faulted;
    if (size == 0 || size > MaxBytes || count_ + (executing_ ? 1U : 0U) >= Capacity) {
      faulted_ = true;
      rejection_pending_ = true;
      rejected_command_ = size ? bytes[0] : 0;
      return Admission::Overflow;
    }
    auto& slot = slots_[(head_ + count_) % Capacity];
    std::memcpy(slot.bytes, bytes, size);
    slot.size = size;
    slot.rejected = false;
    ++count_;
    return Admission::Accepted;
  }

  bool take(Command* command) {
    if (executing_ || command == nullptr) return false;
    if (count_) {
      const auto& slot = slots_[head_];
      std::memcpy(command->bytes, slot.bytes, slot.size);
      command->size = slot.size;
      command->rejected = false;
      head_ = (head_ + 1U) % Capacity;
      --count_;
    } else if (rejection_pending_) {
      command->bytes[0] = rejected_command_;
      command->size = 1;
      command->rejected = true;
      rejection_pending_ = false;
    } else {
      return false;
    }
    executing_ = true;
    return true;
  }

  void complete() { executing_ = false; }
  bool pending() const { return executing_ || count_ || rejection_pending_; }
  bool faulted() const { return faulted_; }

  bool publication_ticket(std::uint64_t* ticket) const {
    if (pending() || received_ != classified_ || ticket == nullptr) return false;
    *ticket = received_;
    return true;
  }
  bool can_publish(std::uint64_t ticket) const {
    return !pending() && received_ == classified_ && received_ == ticket;
  }

 private:
  Command slots_[Capacity]{};
  std::size_t head_ = 0, count_ = 0;
  std::uint64_t received_ = 0, classified_ = 0;
  bool executing_ = false, faulted_ = false, rejection_pending_ = false;
  std::uint8_t rejected_command_ = 0;
};

}  // namespace ledgrid
