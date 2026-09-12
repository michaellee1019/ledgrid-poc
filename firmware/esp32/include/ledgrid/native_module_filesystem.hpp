#pragma once

#include <cstdint>

namespace ledgrid {

constexpr char kNativeModuleCacheBasePath[] = "/profilecache";

// Borrow the exact full-digest filename from a managed absolute payload path.
// No normalization, directory traversal, metadata or shortened aliases allowed.
const char* native_module_loader_name(const char* managed_path);

struct NativeModuleCacheReconcileResult {
  bool ok = false;
  std::uint32_t removed_data_files = 0;
  std::uint32_t removed_metadata_files = 0;
  std::uint32_t removed_partial_files = 0;
};

// Repairs only files in the reserved `n<sha256>.bin/.meta` namespace. Valid
// pairs are retained regardless of ledger state, so active/staged/rollback
// pins cannot be lost during recovery. Unrelated profile-cache files are never
// considered.
NativeModuleCacheReconcileResult reconcile_native_module_cache(
    const char* base_path);

}  // namespace ledgrid
