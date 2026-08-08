#include "ledgrid/frame_track.hpp"

#include <cstring>
#include <limits>

namespace ledgrid {
namespace {

std::uint16_t read_u16(const std::uint8_t* input) {
  return static_cast<std::uint16_t>(
      (static_cast<std::uint16_t>(input[0]) << 8U) | input[1]);
}

std::uint32_t read_u32(const std::uint8_t* input) {
  return (static_cast<std::uint32_t>(input[0]) << 24U) |
         (static_cast<std::uint32_t>(input[1]) << 16U) |
         (static_cast<std::uint32_t>(input[2]) << 8U) | input[3];
}

std::uint8_t scale(std::uint8_t value, std::uint8_t brightness) {
  return static_cast<std::uint8_t>(
      (static_cast<std::uint16_t>(value) * brightness + 127U) / 255U);
}

void rgb565_to_rgb(
    std::uint16_t value, std::uint8_t brightness, std::uint8_t* output) {
  const std::uint8_t red = static_cast<std::uint8_t>((value >> 11U) & 0x1FU);
  const std::uint8_t green = static_cast<std::uint8_t>((value >> 5U) & 0x3FU);
  const std::uint8_t blue = static_cast<std::uint8_t>(value & 0x1FU);
  output[0] = scale(static_cast<std::uint8_t>((red << 3U) | (red >> 2U)), brightness);
  output[1] = scale(
      static_cast<std::uint8_t>((green << 2U) | (green >> 4U)), brightness);
  output[2] = scale(
      static_cast<std::uint8_t>((blue << 3U) | (blue >> 2U)), brightness);
}

}  // namespace

std::size_t FrameTrackDecoder::required_rgb_bytes() const {
  return static_cast<std::size_t>(info_.strip_count) * info_.leds_per_strip * 3U;
}

bool FrameTrackDecoder::open(
    const std::uint8_t* track,
    std::size_t track_size,
    std::uint8_t expected_strips,
    std::uint16_t expected_leds,
    std::uint8_t expected_device) {
  track_ = nullptr;
  track_size_ = 0;
  info_ = {};
  error_ = FrameTrackError::None;
  if (track == nullptr) {
    error_ = FrameTrackError::NullInput;
    return false;
  }
  if (track_size < kFrameTrackHeaderBytes) {
    error_ = FrameTrackError::Truncated;
    return false;
  }
  if (std::memcmp(track, "LGT1", 4) != 0) {
    error_ = FrameTrackError::BadMagic;
    return false;
  }
  if (track[4] != kFrameTrackVersion) {
    error_ = FrameTrackError::BadVersion;
    return false;
  }
  info_.flags = track[5];
  info_.strip_count = track[6];
  info_.logical_device = track[7];
  info_.leds_per_strip = read_u16(track + 8);
  info_.frame_count = read_u16(track + 10);
  info_.data_size = read_u32(track + 12);
  const std::uint32_t loop_count = read_u32(track + 16);
  if (info_.strip_count != expected_strips ||
      info_.leds_per_strip != expected_leds || info_.strip_count == 0 ||
      info_.leds_per_strip == 0) {
    error_ = FrameTrackError::BadGeometry;
    return false;
  }
  if (info_.logical_device != expected_device) {
    error_ = FrameTrackError::BadDevice;
    return false;
  }
  if (info_.frame_count == 0) {
    error_ = FrameTrackError::BadFrameCount;
    return false;
  }
  if (info_.flags != kFrameTrackLoop || loop_count != 0) {
    error_ = FrameTrackError::BadLoopCount;
    return false;
  }
  if (info_.data_size != track_size - kFrameTrackHeaderBytes) {
    error_ = FrameTrackError::BadDataSize;
    return false;
  }

  track_ = track;
  track_size_ = track_size;
  std::size_t cursor = kFrameTrackHeaderBytes;
  for (std::uint16_t frame = 0; frame < info_.frame_count; ++frame) {
    if (track_size_ - cursor < kFrameRecordHeaderBytes) {
      error_ = FrameTrackError::Truncated;
      track_ = nullptr;
      return false;
    }
    const std::uint32_t duration = read_u32(track_ + cursor);
    const std::uint32_t encoded_size = read_u32(track_ + cursor + 4);
    const bool keyframe = (track_[cursor + 8] & kFrameRecordKeyframe) != 0;
    if (duration == 0) {
      error_ = FrameTrackError::BadDuration;
      track_ = nullptr;
      return false;
    }
    if (frame == 0 && !keyframe) {
      error_ = FrameTrackError::MissingKeyframe;
      track_ = nullptr;
      return false;
    }
    cursor += kFrameRecordHeaderBytes;
    if (encoded_size > track_size_ - cursor) {
      error_ = FrameTrackError::Truncated;
      track_ = nullptr;
      return false;
    }
    if (!validate_record(track_ + cursor, encoded_size, keyframe)) {
      track_ = nullptr;
      return false;
    }
    cursor += encoded_size;
  }
  if (cursor != track_size_) {
    error_ = FrameTrackError::TrailingData;
    track_ = nullptr;
    return false;
  }
  return true;
}

bool FrameTrackDecoder::validate_record(
    const std::uint8_t* encoded,
    std::size_t encoded_size,
    bool keyframe) const {
  const std::size_t pixels =
      static_cast<std::size_t>(info_.strip_count) * info_.leds_per_strip;
  std::size_t cursor = 0;
  std::size_t produced = 0;
  while (cursor < encoded_size) {
    if (encoded_size - cursor < 3) {
      error_ = FrameTrackError::Truncated;
      return false;
    }
    const std::uint8_t opcode = encoded[cursor++];
    const std::uint16_t count = read_u16(encoded + cursor);
    cursor += 2;
    if (count == 0 || count > pixels - produced) {
      error_ = FrameTrackError::RunOverflow;
      return false;
    }
    std::size_t bytes = 0;
    if (opcode == 0) {
      bytes = static_cast<std::size_t>(count) * 2U;
    } else if (opcode == 1) {
      if (keyframe) {
        error_ = FrameTrackError::MissingKeyframe;
        return false;
      }
    } else if (opcode == 2) {
      bytes = 2;
    } else {
      error_ = FrameTrackError::BadOpcode;
      return false;
    }
    if (bytes > encoded_size - cursor) {
      error_ = FrameTrackError::Truncated;
      return false;
    }
    cursor += bytes;
    produced += count;
  }
  if (produced != pixels) {
    error_ = FrameTrackError::RunOverflow;
    return false;
  }
  return true;
}

bool FrameTrackDecoder::locate_frame(
    std::uint16_t frame_index,
    const std::uint8_t** encoded,
    std::size_t* encoded_size,
    std::uint8_t* frame_flags,
    std::uint32_t* duration_ms) const {
  if (track_ == nullptr || frame_index >= info_.frame_count) return false;
  std::size_t cursor = kFrameTrackHeaderBytes;
  for (std::uint16_t frame = 0; frame <= frame_index; ++frame) {
    const std::uint32_t duration = read_u32(track_ + cursor);
    const std::uint32_t size = read_u32(track_ + cursor + 4);
    const std::uint8_t flags = track_[cursor + 8];
    cursor += kFrameRecordHeaderBytes;
    if (frame == frame_index) {
      *encoded = track_ + cursor;
      *encoded_size = size;
      *frame_flags = flags;
      *duration_ms = duration;
      return true;
    }
    cursor += size;
  }
  return false;
}

bool FrameTrackDecoder::decode_frame(
    std::uint16_t frame_index,
    std::uint8_t brightness,
    std::uint8_t* rgb_output,
    std::size_t rgb_output_size,
    std::uint32_t* duration_ms) const {
  if (rgb_output == nullptr || rgb_output_size < required_rgb_bytes()) {
    error_ = FrameTrackError::OutputTooSmall;
    return false;
  }
  // Find the nearest keyframe so direct seeks and live brightness changes
  // rebuild unchanged/delta pixels with the same brightness as changed pixels.
  std::uint16_t first = frame_index;
  while (true) {
    const std::uint8_t* candidate = nullptr;
    std::size_t candidate_size = 0;
    std::uint8_t candidate_flags = 0;
    std::uint32_t candidate_duration = 0;
    if (!locate_frame(first, &candidate, &candidate_size, &candidate_flags,
                      &candidate_duration)) return false;
    if ((candidate_flags & kFrameRecordKeyframe) != 0) break;
    if (first == 0) {
      error_ = FrameTrackError::MissingKeyframe;
      return false;
    }
    --first;
  }

  std::uint32_t duration = 0;
  // Validate the complete dependency chain before the first buffer write.
  for (std::uint16_t current = first;; ++current) {
    const std::uint8_t* encoded = nullptr;
    std::size_t encoded_size = 0;
    std::uint8_t flags = 0;
    if (!locate_frame(current, &encoded, &encoded_size, &flags, &duration) ||
        !validate_record(encoded, encoded_size,
                         (flags & kFrameRecordKeyframe) != 0)) return false;
    if (current == frame_index) break;
  }
  for (std::uint16_t current = first;; ++current) {
    const std::uint8_t* encoded = nullptr;
    std::size_t encoded_size = 0;
    std::uint8_t flags = 0;
    if (!locate_frame(current, &encoded, &encoded_size, &flags, &duration))
      return false;
    std::size_t cursor = 0;
    std::size_t pixel = 0;
    while (cursor < encoded_size) {
      const std::uint8_t opcode = encoded[cursor++];
      const std::uint16_t count = read_u16(encoded + cursor);
      cursor += 2;
      if (opcode == 0) {
        for (std::uint16_t i = 0; i < count; ++i) {
          rgb565_to_rgb(read_u16(encoded + cursor), brightness,
                        rgb_output + pixel * 3U);
          cursor += 2;
          ++pixel;
        }
      } else if (opcode == 1) {
        pixel += count;
      } else {
        const std::uint16_t color = read_u16(encoded + cursor);
        cursor += 2;
        for (std::uint16_t i = 0; i < count; ++i) {
          rgb565_to_rgb(color, brightness, rgb_output + pixel * 3U);
          ++pixel;
        }
      }
    }
    if (current == frame_index) break;
  }
  if (duration_ms != nullptr) *duration_ms = duration;
  error_ = FrameTrackError::None;
  return true;
}

void FrameTrackPlayer::set_decoder(const FrameTrackDecoder* decoder) {
  decoder_ = decoder;
  restart();
}

bool FrameTrackPlayer::set_controls(const FramePlaybackControls& controls) {
  if (controls.speed_permille < 100 || controls.speed_permille > 4000) {
    return false;
  }
  const bool brightness_changed = controls_.brightness != controls.brightness;
  controls_ = controls;
  if (brightness_changed) decoded_ = false;
  return true;
}

void FrameTrackPlayer::restart(std::uint64_t now_ms) {
  frame_index_ = 0;
  frame_started_ms_ = now_ms;
  frame_duration_ms_ = 0;
  clock_started_ = false;
  decoded_ = false;
  finished_ = false;
  last_render_ms_ = now_ms;
}

bool FrameTrackPlayer::render(
    std::uint64_t now_ms,
    std::uint8_t* rgb_output,
    std::size_t rgb_output_size,
    bool* changed) {
  if (changed != nullptr) *changed = false;
  if (decoder_ == nullptr) return false;
  if (finished_) return true;
  if (!clock_started_) {
    frame_started_ms_ = now_ms;
    last_render_ms_ = now_ms;
    clock_started_ = true;
  }

  if (!decoded_) {
    if (!decoder_->decode_frame(frame_index_, controls_.brightness, rgb_output,
                                rgb_output_size, &frame_duration_ms_)) {
      return false;
    }
    decoded_ = true;
    if (changed != nullptr) *changed = true;
  }
  if (controls_.paused) {
    // Move the origin by the paused wall-clock interval so a later resume does
    // not catch up through time that was intentionally frozen.
    frame_started_ms_ += now_ms - last_render_ms_;
    last_render_ms_ = now_ms;
    return true;
  }
  last_render_ms_ = now_ms;

  const std::uint64_t scaled_elapsed =
      (now_ms - frame_started_ms_) * controls_.speed_permille;
  if (scaled_elapsed < static_cast<std::uint64_t>(frame_duration_ms_) * 1000U) {
    return true;
  }
  // Advance at most one frame per call. This bounds catch-up work after a stall.
  ++frame_index_;
  if (frame_index_ >= decoder_->info().frame_count) {
    const bool track_loops = (decoder_->info().flags & kFrameTrackLoop) != 0;
    if (controls_.loop && track_loops) {
      frame_index_ = 0;
    } else {
      frame_index_ = static_cast<std::uint16_t>(decoder_->info().frame_count - 1U);
      finished_ = true;
      return true;
    }
  }
  frame_started_ms_ = now_ms;
  decoded_ = false;
  if (!decoder_->decode_frame(frame_index_, controls_.brightness, rgb_output,
                              rgb_output_size, &frame_duration_ms_)) {
    return false;
  }
  decoded_ = true;
  if (changed != nullptr) *changed = true;
  return true;
}

}  // namespace ledgrid
