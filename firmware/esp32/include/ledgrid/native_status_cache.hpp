#pragma once

#include <mutex>

#include "ledgrid/native_module.hpp"

namespace ledgrid {

// The manager is serialized by its existing native-operation mutex. Readers
// must not take that mutex: a render can outlast the host's SPI query interval.
// Mutex protects only the fixed-size copy, never rendering or filesystem work.
template <typename Mutex>
class NativeModuleStatusCache {
 public:
  NativeModuleStatusV1 snapshot() const {
    std::lock_guard<Mutex> guard(mutex_);
    return status_;
  }

  // Call with the manager's mutex held, before releasing it after every native
  // mutation. Render changes no SPIFFS storage; reuse only the capacity metrics
  // there, avoiding filesystem queries in the per-frame path. Boot, commands,
  // and writes through the shared installation-profile store refresh them.
  void publish(const NativeModuleManager& manager,
               bool storage_unchanged = false) {
    const auto prior = storage_unchanged ? snapshot() : NativeModuleStatusV1{};
    const auto next = manager.status(storage_unchanged ? &prior : nullptr);
    std::lock_guard<Mutex> guard(mutex_);
    status_ = next;
  }

 private:
  mutable Mutex mutex_;
  NativeModuleStatusV1 status_{};
};

}  // namespace ledgrid
