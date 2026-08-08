#pragma once

#include <cstddef>
#include <cstdint>

namespace ledgrid {

constexpr std::uint8_t kTypedParameterBlobVersion = 1;
constexpr std::size_t kMaxTypedParameterBlobBytes = 1024;
constexpr std::uint8_t kMaxTypedParameters = 32;
constexpr std::uint8_t kMaxParameterNameBytes = 63;
constexpr std::uint8_t kMaxEnumValueBytes = 63;

enum class WireParameterType : std::uint8_t {
  Int32 = 1,
  Float32 = 2,
  Bool = 3,
  Enum = 4,
  Color = 5,
};

// Validates the self-describing v1 blob without allocating. Names must be
// unique; all lengths, types and canonical boolean values are checked.
bool validate_typed_parameter_blob(const std::uint8_t* data, std::size_t size);

struct RuntimePlaybackControls {
  bool paused = false;
  bool loop = true;
  float playback_speed = 1.0F;
  float time_scale = 1.0F;
  float asset_brightness = 1.0F;
};

bool decode_runtime_playback_controls(
    const std::uint8_t* data, std::size_t size,
    RuntimePlaybackControls* controls);
std::uint64_t scale_animation_elapsed_us(
    std::uint64_t elapsed_us, float time_scale);
std::uint16_t compose_frame_speed_permille(
    const RuntimePlaybackControls& controls);
std::uint8_t asset_brightness_u8(const RuntimePlaybackControls& controls);

}  // namespace ledgrid
