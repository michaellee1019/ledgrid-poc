#pragma once

#include <cstddef>
#include <cstdint>

namespace ledgrid {

constexpr std::uint8_t kFrameTrackVersion = 1;
constexpr std::size_t kFrameTrackHeaderBytes = 20;
constexpr std::size_t kFrameRecordHeaderBytes = 12;
constexpr std::uint8_t kFrameTrackLoop = 1U << 0U;
constexpr std::uint8_t kFrameRecordKeyframe = 1U << 0U;

enum class FrameTrackError : std::uint8_t {
  None = 0,
  NullInput,
  Truncated,
  BadMagic,
  BadVersion,
  BadGeometry,
  BadDevice,
  BadFrameCount,
  BadLoopCount,
  BadDataSize,
  BadDuration,
  BadOpcode,
  RunOverflow,
  MissingKeyframe,
  TrailingData,
  OutputTooSmall,
};

struct FrameTrackInfo {
  std::uint8_t flags = 0;
  std::uint8_t strip_count = 0;
  std::uint8_t logical_device = 0;
  std::uint16_t leds_per_strip = 0;
  std::uint16_t frame_count = 0;
  std::uint32_t data_size = 0;
};

class FrameTrackDecoder {
 public:
  bool open(
      const std::uint8_t* track,
      std::size_t track_size,
      std::uint8_t expected_strips,
      std::uint16_t expected_leds,
      std::uint8_t expected_device);

  // Validates the complete selected record before touching rgb_output.
  bool decode_frame(
      std::uint16_t frame_index,
      std::uint8_t brightness,
      std::uint8_t* rgb_output,
      std::size_t rgb_output_size,
      std::uint32_t* duration_ms = nullptr) const;

  const FrameTrackInfo& info() const { return info_; }
  FrameTrackError error() const { return error_; }
  std::size_t required_rgb_bytes() const;

 private:
  bool locate_frame(
      std::uint16_t frame_index,
      const std::uint8_t** encoded,
      std::size_t* encoded_size,
      std::uint8_t* frame_flags,
      std::uint32_t* duration_ms) const;
  bool validate_record(
      const std::uint8_t* encoded,
      std::size_t encoded_size,
      bool keyframe) const;

  const std::uint8_t* track_ = nullptr;
  std::size_t track_size_ = 0;
  FrameTrackInfo info_{};
  mutable FrameTrackError error_ = FrameTrackError::None;
};

struct FramePlaybackControls {
  bool paused = false;
  bool loop = true;
  std::uint16_t speed_permille = 1000;
  std::uint8_t brightness = 255;
};

class FrameTrackPlayer {
 public:
  explicit FrameTrackPlayer(const FrameTrackDecoder* decoder = nullptr)
      : decoder_(decoder) {}
  void set_decoder(const FrameTrackDecoder* decoder);
  bool set_controls(const FramePlaybackControls& controls);
  const FramePlaybackControls& controls() const { return controls_; }
  std::uint16_t frame_index() const { return frame_index_; }
  bool finished() const { return finished_; }
  void restart(std::uint64_t now_ms = 0);
  bool render(
      std::uint64_t now_ms,
      std::uint8_t* rgb_output,
      std::size_t rgb_output_size,
      bool* changed = nullptr);

 private:
  const FrameTrackDecoder* decoder_ = nullptr;
  FramePlaybackControls controls_{};
  std::uint16_t frame_index_ = 0;
  std::uint64_t frame_started_ms_ = 0;
  std::uint32_t frame_duration_ms_ = 0;
  bool clock_started_ = false;
  bool decoded_ = false;
  bool finished_ = false;
  std::uint64_t last_render_ms_ = 0;
};

}  // namespace ledgrid
