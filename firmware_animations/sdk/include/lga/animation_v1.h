#pragma once

// Canonical ABI v1 header shared verbatim by receiver and authoring SDK.

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define LEDGRID_ANIMATION_ABI_V1 1U
#define LEDGRID_ANIMATION_ENTRYPOINT_V1 "ledgrid_animation_v1"
#define LEDGRID_ANIMATION_MAX_PARAMETERS 32U
#define LEDGRID_ANIMATION_OK 0
#define LEDGRID_ANIMATION_ERROR -1

typedef enum ledgrid_parameter_type_v1 {
  LEDGRID_PARAMETER_INT32 = 1,
  LEDGRID_PARAMETER_FLOAT32 = 2,
  LEDGRID_PARAMETER_BOOL = 3,
  LEDGRID_PARAMETER_ENUM = 4,
  LEDGRID_PARAMETER_COLOR_RGB = 5,
} ledgrid_parameter_type_v1;

typedef struct ledgrid_parameter_v1 {
  const char* name;
  uint8_t type;
  uint8_t reserved[3];
  union {
    int32_t integer;
    float real;
    uint8_t boolean;
    const char* enum_value;
    uint8_t color[3];
  } value;
} ledgrid_parameter_v1;

typedef struct ledgrid_render_context_v1 {
  uint32_t abi_version;
  uint8_t local_strips;
  uint16_t leds_per_strip;
  uint16_t global_strip_offset;
  uint64_t elapsed_us;
  uint64_t scaled_elapsed_us;
  uint32_t frame_index;
  const ledgrid_parameter_v1* parameters;
  uint8_t parameter_count;
  uint8_t* rgb_output;
  size_t rgb_output_size;
} ledgrid_render_context_v1;

typedef struct ledgrid_host_helpers_v1 {
  uint32_t abi_version;
  uint32_t (*random_u32)(uint32_t* state);
  void (*hsv_to_rgb)(uint16_t hue, uint8_t saturation, uint8_t value,
                     uint8_t rgb[3]);
  uint16_t (*rgb_to_565)(uint8_t red, uint8_t green, uint8_t blue);
  float (*sin_f32)(float radians);
  float (*cos_f32)(float radians);
} ledgrid_host_helpers_v1;

typedef struct ledgrid_animation_callbacks_v1 {
  uint32_t abi_version;
  // initialize/render return LEDGRID_ANIMATION_OK on success and any nonzero
  // value on failure. Module state must remain private and may not retain the
  // caller-owned frame buffer.
  int (*initialize)(const ledgrid_render_context_v1* context,
                    const ledgrid_host_helpers_v1* helpers, void** state);
  int (*render)(void* state, const ledgrid_render_context_v1* context);
  void (*cleanup)(void* state);
} ledgrid_animation_callbacks_v1;

// Every native animation shared object exports this exact unmangled symbol.
typedef const ledgrid_animation_callbacks_v1* (*ledgrid_animation_entrypoint_v1)(void);

#ifdef __cplusplus
}
#endif
