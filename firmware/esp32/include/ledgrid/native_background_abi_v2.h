#pragma once

// Stable authoring/runtime seam for repository-owned receiver backgrounds.
//
// This ABI is intentionally C-compatible even when a module is authored in
// C++.  Every structure carries its byte size so a loader can reject drift
// before following pointers.  All pointers are borrowed for the duration of a
// callback. Modules may retain only bytes copied into their caller-owned state,
// with one explicit exception: the init helper table and its function pointers
// remain valid from a successful initialize call through cleanup. The helper
// table and function pointers are read-only and must never be mutated. All
// context, profile, modifier, parameter, vibe, request, result, and output
// pointers are callback-borrowed and must never be retained or mutated by a
// module.

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define LEDGRID_NATIVE_BACKGROUND_ABI_SCHEMA "ledgrid.native-background-abi"
#define LEDGRID_NATIVE_BACKGROUND_ABI_VERSION 2U
#define LEDGRID_NATIVE_BACKGROUND_ENTRYPOINT_V2 "ledgrid_native_background_v2"
#define LEDGRID_NATIVE_BACKGROUND_OK 0
#define LEDGRID_NATIVE_BACKGROUND_ERROR -1
#define LEDGRID_NATIVE_BACKGROUND_MAX_PARAMETERS 31U
#define LEDGRID_NATIVE_BACKGROUND_PALETTE_ROLES 8U
#define LEDGRID_NATIVE_BACKGROUND_MAX_MODIFIERS 14U
#define LEDGRID_NATIVE_BACKGROUND_MAX_PROFILE_SECTIONS 9U
#define LEDGRID_NATIVE_BACKGROUND_MAX_STATE_BYTES 65536U
#define LEDGRID_NATIVE_BACKGROUND_MAX_STATE_ALIGNMENT 64U

typedef enum ledgrid_native_parameter_type_v2 {
  LEDGRID_NATIVE_PARAMETER_INT32 = 1,
  LEDGRID_NATIVE_PARAMETER_FLOAT32 = 2,
  LEDGRID_NATIVE_PARAMETER_BOOL = 3,
  LEDGRID_NATIVE_PARAMETER_ENUM = 4,
  LEDGRID_NATIVE_PARAMETER_COLOR_RGB = 5,
} ledgrid_native_parameter_type_v2;

typedef union ledgrid_native_parameter_value_v2 {
  int32_t integer;
  float real;
  uint8_t boolean;
  uint16_t enum_index;
  uint8_t color[3];
} ledgrid_native_parameter_value_v2;

// Parameter IDs are the zero-based positions of names in canonical sorted
// schema order.  Bundle validation freezes that ordering for each component.
typedef struct ledgrid_native_parameter_v2 {
  uint16_t id;
  uint8_t type;
  uint8_t reserved_zero;
  ledgrid_native_parameter_value_v2 value;
} ledgrid_native_parameter_v2;

typedef struct ledgrid_native_vibe_v2 {
  uint32_t struct_size;
  uint32_t profile_version;
  uint64_t revision;
  uint8_t palette[LEDGRID_NATIVE_BACKGROUND_PALETTE_ROLES][3];
  uint16_t tempo_q8_8;
  // Framework-owned final-output scale in the closed range 0..256 (Q8.8).
  // Modules claiming the luminance
  // capability must not apply this value; the receiver applies it exactly once
  // after local rendering. It remains visible here for complete context parity.
  uint16_t luminance_q8_8;
  uint16_t chroma_q8_8;
  uint16_t energy_q8_8;
} ledgrid_native_vibe_v2;

// Modifier IDs preserve animation.core.plant_awareness.PLANT_MODIFIER_IDS.
typedef enum ledgrid_native_modifier_id_v2 {
  LEDGRID_NATIVE_MODIFIER_ILLUMINATE = 1,
  LEDGRID_NATIVE_MODIFIER_SHADOW = 2,
  LEDGRID_NATIVE_MODIFIER_REFRACT = 3,
  LEDGRID_NATIVE_MODIFIER_HUE_SHIFT = 4,
  LEDGRID_NATIVE_MODIFIER_LIQUID_GLASS = 5,
  LEDGRID_NATIVE_MODIFIER_ATTRACTOR = 6,
  LEDGRID_NATIVE_MODIFIER_REPULSOR = 7,
  LEDGRID_NATIVE_MODIFIER_SLOW_ZONE = 8,
  LEDGRID_NATIVE_MODIFIER_OBSTACLE = 9,
  LEDGRID_NATIVE_MODIFIER_PORTAL = 10,
  LEDGRID_NATIVE_MODIFIER_BUMPER = 11,
  LEDGRID_NATIVE_MODIFIER_HAZARD = 12,
  LEDGRID_NATIVE_MODIFIER_HABITAT = 13,
  LEDGRID_NATIVE_MODIFIER_EMITTER = 14,
} ledgrid_native_modifier_id_v2;

typedef struct ledgrid_native_modifier_v2 {
  uint8_t id;
  uint8_t reserved_zero;
  uint16_t strength_q8_8;
} ledgrid_native_modifier_v2;

typedef struct ledgrid_native_modifier_view_v2 {
  uint32_t struct_size;
  uint64_t revision;
  const ledgrid_native_modifier_v2* entries;
  uint8_t count;
  uint8_t reserved_zero[7];
} ledgrid_native_modifier_view_v2;

typedef enum ledgrid_native_profile_section_id_v2 {
  LEDGRID_NATIVE_PROFILE_CATEGORY = 1,
  LEDGRID_NATIVE_PROFILE_CLEARANCE = 2,
  LEDGRID_NATIVE_PROFILE_FOLIAGE_EDGE = 3,
  LEDGRID_NATIVE_PROFILE_GLOBE_EDGE = 4,
  LEDGRID_NATIVE_PROFILE_OBSTACLE_EDGE = 5,
  LEDGRID_NATIVE_PROFILE_GLOBE_REGION = 6,
  LEDGRID_NATIVE_PROFILE_DISTANCE = 7,
  LEDGRID_NATIVE_PROFILE_NORMAL_X = 8,
  LEDGRID_NATIVE_PROFILE_NORMAL_Y = 9,
} ledgrid_native_profile_section_id_v2;

typedef enum ledgrid_native_profile_encoding_v2 {
  LEDGRID_NATIVE_PROFILE_UNSIGNED_ENUM = 1,
  LEDGRID_NATIVE_PROFILE_UNSIGNED_BOOLEAN = 2,
  LEDGRID_NATIVE_PROFILE_UNSIGNED_BYTE = 3,
  LEDGRID_NATIVE_PROFILE_SIGNED_BYTE = 4,
} ledgrid_native_profile_encoding_v2;

typedef enum ledgrid_native_profile_category_v2 {
  LEDGRID_NATIVE_CATEGORY_OPEN = 0,
  LEDGRID_NATIVE_CATEGORY_FOLIAGE = 1,
  LEDGRID_NATIVE_CATEGORY_GLOBE = 2,
} ledgrid_native_profile_category_v2;

typedef enum ledgrid_native_globe_region_v2 {
  LEDGRID_NATIVE_GLOBE_REGION_NONE = 0,
  LEDGRID_NATIVE_GLOBE_REGION_TOP_LEFT = 1,
  LEDGRID_NATIVE_GLOBE_REGION_TOP_RIGHT = 2,
  LEDGRID_NATIVE_GLOBE_REGION_UPPER_MIDDLE = 3,
  LEDGRID_NATIVE_GLOBE_REGION_MIDDLE_LEFT = 4,
  LEDGRID_NATIVE_GLOBE_REGION_MIDDLE_RIGHT = 5,
  LEDGRID_NATIVE_GLOBE_REGION_LOWER_LEFT = 6,
  LEDGRID_NATIVE_GLOBE_REGION_LOWER_RIGHT = 7,
} ledgrid_native_globe_region_v2;

typedef struct ledgrid_native_profile_section_v2 {
  uint16_t id;
  uint8_t encoding;
  uint8_t element_width;
  uint32_t element_count;
  const uint8_t* data;
} ledgrid_native_profile_section_v2;

typedef struct ledgrid_native_profile_view_v2 {
  uint32_t struct_size;
  uint16_t global_strips;
  uint16_t leds_per_strip;
  uint16_t global_strip_offset;
  uint16_t local_strips;
  const ledgrid_native_profile_section_v2* sections;
  uint8_t section_count;
  uint8_t clearance_radius;
  uint8_t reverse_local_strip_order;
  uint8_t reserved_zero[5];
} ledgrid_native_profile_view_v2;

typedef struct ledgrid_native_helpers_v2 {
  uint32_t abi_version;
  uint32_t struct_size;
  // Applies xorshift32 to the module-owned writable uint32_t at state, stores
  // the new value there, and returns it. The transform, with uint32_t wrap, is
  // x ^= x << 13; x ^= x >> 17; x ^= x << 5. The module owns seeding and must
  // pass a non-null pointer; a zero state remains the degenerate zero stream.
  uint32_t (*random_u32)(uint32_t* state);
  // hue is a full-turn uint16 phase: hue / 65536 turns in [0, 1).
  // saturation and value span 0..255 and map to fractions / 255. rgb points to
  // three module-owned writable bytes in the range 0..255. Each final channel
  // is rounded to the nearest integer, with exact half-way cases to even.
  void (*hsv_to_rgb)(uint16_t hue, uint8_t saturation, uint8_t value,
                     uint8_t rgb[3]);
  // phase maps to phase * 2*pi / 65536. Results are the mathematical sine or
  // cosine multiplied by 32767 and rounded to nearest integer, with exact
  // half-way cases to even, in [-32767, 32767]. Cardinal sine outputs at phases
  // 0, 16384, 32768, and 49152 are 0, 32767, 0, and -32767; cosine is sine at
  // (phase + 16384) modulo 65536.
  int16_t (*sin_q15)(uint16_t phase);
  int16_t (*cos_q15)(uint16_t phase);
} ledgrid_native_helpers_v2;

typedef struct ledgrid_native_init_v2 {
  uint32_t abi_version;
  uint32_t struct_size;
  uint16_t global_strips;
  uint16_t local_strips;
  uint16_t leds_per_strip;
  uint16_t global_strip_offset;
  // The payload always writes rgb_output in receiver-native local strip order.
  // When true, global coordinate = offset + local_strips - 1 - local_strip;
  // otherwise global coordinate = offset + local_strip.
  uint8_t reverse_local_strip_order;
  uint8_t reserved_zero[7];
  uint32_t pixel_count;
  uint32_t deterministic_seed;
  uint64_t scene_epoch_ns;
  const ledgrid_native_helpers_v2* helpers;
} ledgrid_native_init_v2;

// Parameters and presentation views change independently of simulation time.
// A context update must not advance a tick or consume random state.
typedef struct ledgrid_native_context_v2 {
  uint32_t abi_version;
  uint32_t struct_size;
  const ledgrid_native_parameter_v2* parameters;
  uint8_t parameter_count;
  uint8_t reserved_zero[7];
  const ledgrid_native_vibe_v2* vibe;
  const ledgrid_native_modifier_view_v2* modifiers;
  const ledgrid_native_profile_view_v2* profile;
} ledgrid_native_context_v2;

typedef struct ledgrid_native_render_request_v2 {
  uint32_t abi_version;
  uint32_t struct_size;
  uint64_t unscaled_scene_time_us;
  uint64_t scaled_scene_time_us;
  uint64_t frame_index;
  uint8_t* rgb_output;
  uint32_t rgb_output_size;
  uint32_t reserved_zero;
} ledgrid_native_render_request_v2;

typedef struct ledgrid_native_render_result_v2 {
  uint32_t struct_size;
  int32_t status;
  // changed=1 publishes the complete local rgb_output. changed=0 ignores
  // rgb_output and retains the receiver's previous complete local RGB frame.
  // The first successful render after initialize must return changed=1.
  uint8_t changed;
  uint8_t reserved_zero[7];
  // Absolute unscaled microseconds since the current scene epoch. A successful
  // result must be strictly later than the request time, no later than one
  // declared fixed-FPS period after it, and must not move backward across calls.
  uint64_t next_deadline_scene_time_us;
} ledgrid_native_render_result_v2;

typedef struct ledgrid_native_background_api_v2 {
  uint32_t abi_version;
  uint32_t struct_size;
  // state_size is 1..LEDGRID_NATIVE_BACKGROUND_MAX_STATE_BYTES. Alignment is a
  // power of two in 1..LEDGRID_NATIVE_BACKGROUND_MAX_STATE_ALIGNMENT.
  uint32_t state_size;
  uint32_t state_alignment;
  int (*initialize)(void* state, const ledgrid_native_init_v2* init);
  int (*update_context)(void* state,
                        const ledgrid_native_context_v2* context);
  int (*render)(void* state,
                const ledgrid_native_render_request_v2* request,
                ledgrid_native_render_result_v2* result);
  int (*cleanup)(void* state);
} ledgrid_native_background_api_v2;

typedef const ledgrid_native_background_api_v2*
    (*ledgrid_native_background_entrypoint_v2)(void);

#ifdef __cplusplus
}
#endif
