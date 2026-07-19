#include "ledgrid/parallel_led_driver.hpp"

#include <algorithm>
#include <cstring>

#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "ledgrid/ws2812_encoder.hpp"

namespace ledgrid {
namespace {

constexpr std::size_t kDmaAlignment = 64;
constexpr int kGhostClockPin = 0;

std::uint16_t duration_u16(std::uint32_t value) {
  return value > UINT16_MAX ? UINT16_MAX : static_cast<std::uint16_t>(value);
}

}  // namespace

ParallelLedDriver::~ParallelLedDriver() {
  if (io_ != nullptr) esp_lcd_panel_io_del(io_);
  if (bus_ != nullptr) esp_lcd_del_i80_bus(bus_);
  for (auto*& buffer : buffers_) {
    if (buffer != nullptr) heap_caps_free(buffer);
    buffer = nullptr;
  }
  if (done_ != nullptr) vSemaphoreDelete(done_);
}

bool ParallelLedDriver::begin(
    const int* pins,
    std::uint8_t strip_count,
    std::uint16_t max_leds_per_strip) {
  if (pins == nullptr || strip_count == 0 || strip_count > kMaxParallelStrips ||
      max_leds_per_strip == 0 || io_ != nullptr) {
    return false;
  }

  buffer_capacity_ = ws2812_encoded_size(max_leds_per_strip);
  for (auto*& buffer : buffers_) {
    buffer = static_cast<std::uint8_t*>(heap_caps_aligned_alloc(
        kDmaAlignment,
        buffer_capacity_,
        MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA | MALLOC_CAP_8BIT));
    if (buffer == nullptr) return false;
    if (!initialize_parallel_grb_waveform(
            strip_count,
            max_leds_per_strip,
            buffer,
            buffer_capacity_)) {
      return false;
    }
  }

  done_ = xSemaphoreCreateBinary();
  if (done_ == nullptr) return false;

  esp_lcd_i80_bus_config_t bus_config = {};
  bus_config.clk_src = LCD_CLK_SRC_PLL160M;
  bus_config.dc_gpio_num = kGhostClockPin;
  bus_config.wr_gpio_num = kGhostClockPin;
  for (auto& pin : bus_config.data_gpio_nums) pin = -1;
  for (std::uint8_t i = 0; i < strip_count; ++i) {
    bus_config.data_gpio_nums[i] = pins[i];
  }
  bus_config.bus_width = 8;
  bus_config.max_transfer_bytes = buffer_capacity_;
  bus_config.dma_burst_size = kDmaAlignment;
  if (esp_lcd_new_i80_bus(&bus_config, &bus_) != ESP_OK) return false;

  esp_lcd_panel_io_i80_config_t io_config = {};
  io_config.cs_gpio_num = -1;
  io_config.pclk_hz = kWs2812SampleRateHz;
  io_config.trans_queue_depth = 2;
  io_config.dc_levels.dc_idle_level = 0;
  io_config.dc_levels.dc_cmd_level = 0;
  io_config.dc_levels.dc_dummy_level = 0;
  io_config.dc_levels.dc_data_level = 1;
  io_config.on_color_trans_done = on_transfer_done;
  io_config.user_ctx = this;
  io_config.lcd_cmd_bits = 0;
  io_config.lcd_param_bits = 0;
  return esp_lcd_new_panel_io_i80(bus_, &io_config, &io_) == ESP_OK;
}

bool ParallelLedDriver::submit(
    const std::uint8_t* rgb,
    std::size_t rgb_bytes,
    std::uint8_t strip_count,
    std::uint16_t leds_per_strip,
    std::uint8_t brightness,
    std::uint32_t sequence) {
  if (io_ == nullptr || in_flight_) return false;

  std::uint8_t* output = buffers_[next_buffer_];
  const std::uint32_t encode_started =
      static_cast<std::uint32_t>(esp_timer_get_time());
  const EncodeResult encoded = encode_parallel_grb_pixels(
      rgb,
      rgb_bytes,
      strip_count,
      leds_per_strip,
      brightness,
      output,
      buffer_capacity_);
  last_encode_us_ = duration_u16(
      static_cast<std::uint32_t>(esp_timer_get_time()) - encode_started);
  if (!encoded.ok) return false;

  xSemaphoreTake(done_, 0);
  last_submitted_sequence_ = sequence;
  show_started_us_ = static_cast<std::uint32_t>(esp_timer_get_time());
  in_flight_ = true;
  const esp_err_t result =
      esp_lcd_panel_io_tx_color(io_, 0, output, encoded.bytes_written);
  if (result != ESP_OK) {
    in_flight_ = false;
    return false;
  }
  next_buffer_ ^= 1U;
  return true;
}

bool ParallelLedDriver::wait_for_done(TickType_t timeout_ticks) {
  if (!in_flight_) return true;
  if (xSemaphoreTake(done_, timeout_ticks) != pdTRUE) return false;
  in_flight_ = false;
  return true;
}

bool IRAM_ATTR ParallelLedDriver::on_transfer_done(
    esp_lcd_panel_io_handle_t,
    esp_lcd_panel_io_event_data_t*,
    void* user_context) {
  auto* driver = static_cast<ParallelLedDriver*>(user_context);
  const std::uint32_t now = static_cast<std::uint32_t>(esp_timer_get_time());
  driver->last_show_us_ = duration_u16(now - driver->show_started_us_);
  driver->last_completed_sequence_ = driver->last_submitted_sequence_;
  BaseType_t task_woken = pdFALSE;
  xSemaphoreGiveFromISR(driver->done_, &task_woken);
  return task_woken == pdTRUE;
}

}  // namespace ledgrid
