#pragma once

#include <cstdint>

namespace ledgrid {

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
