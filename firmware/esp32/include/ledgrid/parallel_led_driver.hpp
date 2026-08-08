#pragma once

#include <cstddef>
#include <cstdint>

#include "esp_lcd_panel_io.h"
#include "esp_lcd_io_i80.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

namespace ledgrid {

class ParallelLedDriver {
 public:
  ParallelLedDriver() = default;
  ~ParallelLedDriver();

  bool begin(
      const int* pins,
      std::uint8_t strip_count,
      std::uint16_t max_leds_per_strip);

  bool submit(
      const std::uint8_t* rgb,
      std::size_t rgb_bytes,
      std::uint8_t strip_count,
      std::uint16_t leds_per_strip,
      std::uint8_t brightness,
      std::uint32_t sequence);

  bool wait_for_done(TickType_t timeout_ticks);
  bool in_flight() const { return in_flight_; }

  std::uint16_t last_encode_us() const { return last_encode_us_; }
  std::uint16_t last_show_us() const { return last_show_us_; }
  std::uint32_t last_submitted_sequence() const {
    return last_submitted_sequence_;
  }
  std::uint32_t last_completed_sequence() const {
    return last_completed_sequence_;
  }

 private:
  static bool IRAM_ATTR on_transfer_done(
      esp_lcd_panel_io_handle_t panel_io,
      esp_lcd_panel_io_event_data_t* event_data,
      void* user_context);

  esp_lcd_i80_bus_handle_t bus_ = nullptr;
  esp_lcd_panel_io_handle_t io_ = nullptr;
  SemaphoreHandle_t done_ = nullptr;
  std::uint8_t* buffers_[2] = {};
  std::size_t buffer_capacity_ = 0;
  std::uint8_t next_buffer_ = 0;
  volatile bool in_flight_ = false;
  volatile std::uint32_t show_started_us_ = 0;
  volatile std::uint16_t last_encode_us_ = 0;
  volatile std::uint16_t last_show_us_ = 0;
  volatile std::uint32_t last_submitted_sequence_ = 0;
  volatile std::uint32_t last_completed_sequence_ = 0;
};

}  // namespace ledgrid
