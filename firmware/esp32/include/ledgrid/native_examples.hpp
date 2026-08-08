#pragma once

#include "ledgrid/animation_abi.h"

// Named entrypoints let the baseline firmware compile every checked-in example
// without colliding on the shared-object entrypoint. A standalone package build
// also exports the canonical `ledgrid_animation_v1` symbol from each source.
extern "C" const ledgrid_animation_callbacks_v1*
ledgrid_builtin_startup_rainbow_v1(void);
extern "C" const ledgrid_animation_callbacks_v1*
ledgrid_builtin_aurora_ribbons_v1(void);
extern "C" const ledgrid_animation_callbacks_v1*
ledgrid_builtin_meteor_shower_v1(void);
