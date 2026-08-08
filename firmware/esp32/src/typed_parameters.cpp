#include "ledgrid/typed_parameters.hpp"

#include <cstring>
#include <algorithm>
#include <cmath>

namespace ledgrid {
namespace {

bool valid_utf8_text(const std::uint8_t* text, std::size_t size) {
  std::size_t i = 0;
  while (i < size) {
    const std::uint8_t lead = text[i++];
    if (lead < 0x80U) {
      if (lead < 0x21U || lead == 0x7FU) return false;
      continue;
    }
    std::size_t continuation = 0;
    if (lead >= 0xC2U && lead <= 0xDFU) continuation = 1;
    else if (lead >= 0xE0U && lead <= 0xEFU) continuation = 2;
    else if (lead >= 0xF0U && lead <= 0xF4U) continuation = 3;
    else return false;
    if (continuation > size - i) return false;
    if (lead == 0xE0U && text[i] < 0xA0U) return false;
    if (lead == 0xEDU && text[i] >= 0xA0U) return false;
    if (lead == 0xF0U && text[i] < 0x90U) return false;
    if (lead == 0xF4U && text[i] >= 0x90U) return false;
    for (std::size_t j = 0; j < continuation; ++j) {
      if ((text[i + j] & 0xC0U) != 0x80U) return false;
    }
    i += continuation;
  }
  return true;
}

}  // namespace

bool validate_typed_parameter_blob(const std::uint8_t* data, std::size_t size) {
  if (data == nullptr || size < 2 || size > kMaxTypedParameterBlobBytes ||
      data[0] != kTypedParameterBlobVersion || data[1] > kMaxTypedParameters) {
    return false;
  }
  struct Name { const std::uint8_t* data; std::uint8_t size; };
  Name names[kMaxTypedParameters] = {};
  std::size_t cursor = 2;
  for (std::uint8_t record = 0; record < data[1]; ++record) {
    if (cursor >= size) return false;
    const std::uint8_t name_size = data[cursor++];
    if (name_size == 0 || name_size > kMaxParameterNameBytes ||
        name_size > size - cursor) return false;
    const std::uint8_t* name = data + cursor;
    if (!valid_utf8_text(name, name_size)) return false;
    for (std::uint8_t i = 0; i < record; ++i) {
      if (names[i].size == name_size &&
          std::memcmp(names[i].data, name, name_size) == 0) return false;
    }
    names[record] = {name, name_size};
    cursor += name_size;
    if (cursor >= size) return false;
    const auto type = static_cast<WireParameterType>(data[cursor++]);
    std::size_t value_size = 0;
    switch (type) {
      case WireParameterType::Int32:
      case WireParameterType::Float32:
        value_size = 4;
        break;
      case WireParameterType::Bool:
        value_size = 1;
        if (cursor >= size || data[cursor] > 1) return false;
        break;
      case WireParameterType::Color:
        value_size = 3;
        break;
      case WireParameterType::Enum:
        if (cursor >= size || data[cursor] == 0 ||
            data[cursor] > kMaxEnumValueBytes) return false;
        value_size = static_cast<std::size_t>(data[cursor]) + 1U;
        if (value_size > size - cursor ||
            !valid_utf8_text(data + cursor + 1, value_size - 1U)) return false;
        break;
      default:
        return false;
    }
    if (value_size > size - cursor) return false;
    cursor += value_size;
  }
  return cursor == size;
}

bool decode_runtime_playback_controls(
    const std::uint8_t* data, std::size_t size,
    RuntimePlaybackControls* controls) {
  if (controls == nullptr || !validate_typed_parameter_blob(data, size))
    return false;
  RuntimePlaybackControls decoded = *controls;
  std::size_t cursor = 2;
  for (std::uint8_t record = 0; record < data[1]; ++record) {
    const std::uint8_t name_size = data[cursor++];
    const std::uint8_t* name = data + cursor;
    cursor += name_size;
    const auto type = static_cast<WireParameterType>(data[cursor++]);
    auto name_is = [&](const char* expected) {
      return std::strlen(expected) == name_size &&
             std::memcmp(name, expected, name_size) == 0;
    };
    if (type == WireParameterType::Bool) {
      if (name_is("pause")) decoded.paused = data[cursor] != 0;
      else if (name_is("loop")) decoded.loop = data[cursor] != 0;
      ++cursor;
    } else if (type == WireParameterType::Int32 ||
               type == WireParameterType::Float32) {
      const std::uint32_t bits =
          (static_cast<std::uint32_t>(data[cursor]) << 24U) |
          (static_cast<std::uint32_t>(data[cursor + 1]) << 16U) |
          (static_cast<std::uint32_t>(data[cursor + 2]) << 8U) |
          data[cursor + 3];
      if (type == WireParameterType::Float32) {
        float value = 0;
        std::memcpy(&value, &bits, sizeof(value));
        if (!std::isfinite(value)) return false;
        if (name_is("playback_speed")) decoded.playback_speed = value;
        else if (name_is("time_scale")) decoded.time_scale = value;
        else if (name_is("asset_brightness")) decoded.asset_brightness = value;
      }
      cursor += 4;
    } else if (type == WireParameterType::Color) {
      cursor += 3;
    } else {
      cursor += static_cast<std::size_t>(data[cursor]) + 1U;
    }
  }
  decoded.playback_speed = std::clamp(decoded.playback_speed, 0.1F, 4.0F);
  decoded.time_scale = std::clamp(decoded.time_scale, 0.1F, 4.0F);
  decoded.asset_brightness = std::clamp(decoded.asset_brightness, 0.0F, 1.0F);
  *controls = decoded;
  return true;
}

std::uint64_t scale_animation_elapsed_us(
    std::uint64_t elapsed_us, float time_scale) {
  const long double scaled = static_cast<long double>(elapsed_us) *
      std::clamp(time_scale, 0.1F, 4.0F);
  return scaled >= static_cast<long double>(UINT64_MAX)
             ? UINT64_MAX
             : static_cast<std::uint64_t>(scaled);
}

std::uint16_t compose_frame_speed_permille(
    const RuntimePlaybackControls& controls) {
  return static_cast<std::uint16_t>(std::clamp(
      static_cast<int>(std::lround(controls.playback_speed *
                                   controls.time_scale * 1000.0F)),
      100, 4000));
}

std::uint8_t asset_brightness_u8(const RuntimePlaybackControls& controls) {
  return static_cast<std::uint8_t>(std::clamp(
      static_cast<int>(std::lround(controls.asset_brightness * 255.0F)),
      0, 255));
}

}  // namespace ledgrid
