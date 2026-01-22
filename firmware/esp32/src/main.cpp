#include <Arduino.h>
#include <FastLED.h>
#include "driver/spi_slave.h"
#include "driver/spi_common.h"
#include "driver/gpio.h"

// =========================
// SPI pin mapping (ESP32-S3 DevKitC - VSPI)
// =========================
static constexpr gpio_num_t PIN_SPI_MOSI = GPIO_NUM_11;
static constexpr gpio_num_t PIN_SPI_MISO = GPIO_NUM_13;
static constexpr gpio_num_t PIN_SPI_SCLK = GPIO_NUM_12;
static constexpr gpio_num_t PIN_SPI_CS   = GPIO_NUM_10;

// =========================
// LED configuration (8 strips)
// =========================
static constexpr uint8_t MAX_STRIPS         = 8;
static constexpr uint16_t MAX_LEDS_PER_STRIP = 500;
static constexpr uint16_t MAX_TOTAL_LEDS    = MAX_STRIPS * MAX_LEDS_PER_STRIP;

static constexpr uint8_t DEFAULT_STRIPS     = 8;
static constexpr uint16_t DEFAULT_LEDS_PER_STRIP = 140;

// LED data pins - ESP32-S3 DevKitC (user-specified pins)
static constexpr uint8_t PIN_STRIP_0 = 4;   // GPIO4
static constexpr uint8_t PIN_STRIP_1 = 5;   // GPIO5
static constexpr uint8_t PIN_STRIP_2 = 6;   // GPIO6
static constexpr uint8_t PIN_STRIP_3 = 7;   // GPIO7
static constexpr uint8_t PIN_STRIP_4 = 15;  // GPIO15
static constexpr uint8_t PIN_STRIP_5 = 16;  // GPIO16
static constexpr uint8_t PIN_STRIP_6 = 17;  // GPIO17
static constexpr uint8_t PIN_STRIP_7 = 18;  // GPIO18

static constexpr uint8_t PIN_STATUS_LED = 48;  // ESP32-S3 DevKitC built-in RGB LED

static CRGB leds[MAX_TOTAL_LEDS];
static uint8_t active_strips = DEFAULT_STRIPS;
static uint16_t leds_per_strip = DEFAULT_LEDS_PER_STRIP;
static uint16_t total_leds = DEFAULT_STRIPS * DEFAULT_LEDS_PER_STRIP;
static uint8_t global_brightness = 50;

// =========================
// SPI protocol definitions
// =========================
static constexpr uint8_t CMD_SET_PIXEL      = 0x01;
static constexpr uint8_t CMD_SET_BRIGHTNESS = 0x02;
static constexpr uint8_t CMD_SHOW           = 0x03;
static constexpr uint8_t CMD_CLEAR          = 0x04;
static constexpr uint8_t CMD_SET_RANGE      = 0x05;
static constexpr uint8_t CMD_SET_ALL        = 0x06;
static constexpr uint8_t CMD_CONFIG         = 0x07;
static constexpr uint8_t CMD_PING           = 0xFF;

// =========================
// SPI buffers
// =========================
static constexpr size_t SPI_FRAME_BYTES = 1 + (MAX_TOTAL_LEDS * 3);
static constexpr size_t SPI_BUFFER_SIZE = ((SPI_FRAME_BYTES + 63) / 64) * 64;
DMA_ATTR static uint8_t spi_rx_buffer[SPI_BUFFER_SIZE];
DMA_ATTR static uint8_t spi_tx_buffer[SPI_BUFFER_SIZE];

static volatile uint32_t packets_received = 0;
static volatile uint32_t frames_rendered = 0;
static volatile uint32_t zero_payload_packets = 0;
static volatile uint32_t config_commands_received = 0;
static volatile uint32_t set_all_commands_received = 0;

static uint32_t last_packet_millis = 0;
static uint32_t last_show_duration = 0;
static uint32_t last_frame_sample_time = 0;
static uint32_t last_frame_sample_count = 0;
static uint32_t total_bytes_received = 0;
static uint32_t last_bytes_sample = 0;
static uint32_t last_bytes_sample_time = 0;
static bool debug_logging = false;

  #define DEBUG_PRINT(...) do { if (debug_logging) { Serial.printf(__VA_ARGS__); } } while (0)
#define DEBUG_PRINTLN(msg) do { if (debug_logging) { Serial.println(msg); } } while (0)

inline uint16_t logical_to_physical(uint16_t logical) {
  uint16_t strip = logical / leds_per_strip;
  uint16_t offset = logical % leds_per_strip;
  if (strip >= active_strips) {
    strip = active_strips - 1;
    offset = leds_per_strip - 1;
  }
  return strip * MAX_LEDS_PER_STRIP + offset;
}

static void IRAM_ATTR on_spi_post_transaction(spi_slave_transaction_t *trans) {
  packets_received++;
}

static void process_command(const uint8_t *data, size_t length) {
  if (length == 0) return;

  total_bytes_received += length;
  const uint8_t cmd = data[0];

  // DEBUG: Show first 10 packets to diagnose the issue
  static uint32_t debug_packet_count = 0;
  if (debug_packet_count < 10) {
    Serial.printf("🔍 Pkt#%u: cmd=0x%02X len=%u bytes: ", 
                  debug_packet_count++, cmd, length);
    for (size_t i = 0; i < min(length, (size_t)16); i++) {
      Serial.printf("%02X ", data[i]);
    }
    Serial.println();
  }

  if (length > 1) {
    uint8_t payload_or = 0;
    for (size_t i = 1; i < length; ++i) {
      payload_or |= data[i];
    }
    if (payload_or == 0) {
      zero_payload_packets++;
      DEBUG_PRINT("⚠️ Packet cmd=0x%02X has zero payload\n", cmd);
    }
  }

  switch (cmd) {
    case CMD_PING: {
      DEBUG_PRINTLN("📥 CMD_PING");
      digitalWrite(PIN_STATUS_LED, !digitalRead(PIN_STATUS_LED));
      break;
    }

    case CMD_SET_PIXEL: {
      if (length < 6) return;
      const uint16_t pixel = (static_cast<uint16_t>(data[1]) << 8) | data[2];
      const uint8_t r = data[3];
      const uint8_t g = data[4];
      const uint8_t b = data[5];
      if (pixel < total_leds) {
        leds[logical_to_physical(pixel)] = CRGB(r, g, b);
      }
      break;
    }

    case CMD_SET_BRIGHTNESS: {
      if (length < 2) return;
      global_brightness = data[1];
      FastLED.setBrightness(global_brightness);
      DEBUG_PRINT("📥 Brightness → %u\n", global_brightness);
      break;
    }

    case CMD_SHOW: {
      uint32_t start_us = micros();
      FastLED.show();
      last_show_duration = micros() - start_us;
      // Don't increment frames_rendered here - only count SET_ALL frames
      DEBUG_PRINTLN("📥 CMD_SHOW");
      break;
    }

    case CMD_CLEAR: {
      for (uint8_t strip = 0; strip < active_strips; ++strip) {
        for (uint16_t offset = 0; offset < MAX_LEDS_PER_STRIP; ++offset) {
          leds[strip * MAX_LEDS_PER_STRIP + offset] = CRGB::Black;
        }
      }
      FastLED.show();
      // Don't increment frames_rendered here - only count SET_ALL frames
      DEBUG_PRINTLN("📥 CMD_CLEAR");
      break;
    }

    case CMD_SET_RANGE: {
      if (length < 4) return;
      const uint16_t start = (static_cast<uint16_t>(data[1]) << 8) | data[2];
      if (start >= total_leds) break;

      uint8_t count = data[3];
      const size_t expected = 4 + static_cast<size_t>(count) * 3;
      if (length < expected) return;

      if (start + count > total_leds) {
        count = total_leds - start;
      }

      for (uint8_t i = 0; i < count; ++i) {
        const uint16_t logical = start + i;
        if (logical >= total_leds) break;
        const size_t base = 4 + static_cast<size_t>(i) * 3;
        leds[logical_to_physical(logical)] = CRGB(data[base], data[base + 1], data[base + 2]);
      }
      break;
    }

    case CMD_SET_ALL: {
      set_all_commands_received++;
      const size_t expected = 1 + static_cast<size_t>(total_leds) * 3;
      if (length < expected) {
        Serial.printf("⚠️ CMD_SET_ALL expected %u bytes, got %u\n", 
                      static_cast<unsigned>(expected), static_cast<unsigned>(length));
        return;
      }

      for (uint16_t logical = 0; logical < total_leds; ++logical) {
        const size_t base = 1 + static_cast<size_t>(logical) * 3;
        leds[logical_to_physical(logical)] = CRGB(data[base], data[base + 1], data[base + 2]);
      }
      
      // Clear unused LEDs
      for (uint8_t strip = 0; strip < active_strips; ++strip) {
        for (uint16_t offset = leds_per_strip; offset < MAX_LEDS_PER_STRIP; ++offset) {
          leds[strip * MAX_LEDS_PER_STRIP + offset] = CRGB::Black;
        }
      }

      uint32_t start_us = micros();
      FastLED.show();
      last_show_duration = micros() - start_us;
      frames_rendered++;
      break;
    }

    case CMD_CONFIG: {
      config_commands_received++;
      if (length < 4) return;
      uint8_t new_strips = data[1];
      uint16_t new_len = (static_cast<uint16_t>(data[2]) << 8) | data[3];

      if (new_strips == 0 || new_strips > MAX_STRIPS) return;
      if (new_len == 0 || new_len > MAX_LEDS_PER_STRIP) return;

      // Only clear LEDs if configuration actually changed
      bool config_changed = (active_strips != new_strips) || (leds_per_strip != new_len);
      
      active_strips = new_strips;
      leds_per_strip = new_len;
      total_leds = active_strips * leds_per_strip;

      if (config_changed) {
        // Clear all LEDs only on actual config change
        for (uint16_t i = 0; i < MAX_TOTAL_LEDS; ++i) {
          leds[i] = CRGB::Black;
        }
        FastLED.show();
        DEBUG_PRINT("📐 Config changed: strips=%u, length=%u, total=%u (cleared LEDs)\n",
                    active_strips, leds_per_strip, total_leds);
      } else {
        DEBUG_PRINT("📐 Config refresh: strips=%u, length=%u, total=%u (no change)\n",
                    active_strips, leds_per_strip, total_leds);
      }
      
      if (length >= 5) {
        debug_logging = data[4] != 0;
        if (debug_logging) {
          Serial.println("🔧 Debug logging enabled");
        }
      }
      break;
    }

    default:
      DEBUG_PRINT("⚠️ Unknown command 0x%02X\n", cmd);
      break;
  }
}

void setup() {
  // Initialize Serial (UART)
  Serial.begin(115200);
  delay(1000);  // Give time for serial to stabilize
  
  Serial.println("");
  Serial.println("========================================");
  Serial.println("ESP32-S3 DevKitC SPI Slave LED Controller");  
  Serial.println("========================================");
  Serial.printf("Board: ESP32-S3 DevKitC (8MB Flash)\n");
  Serial.printf("Strips: %d x %d LEDs = %d total\n", active_strips, leds_per_strip, total_leds);
  Serial.println("\nPin mapping:");
  Serial.println("SPI:");
  Serial.printf("  MOSI: GPIO %d\n", PIN_SPI_MOSI);
  Serial.printf("  MISO: GPIO %d\n", PIN_SPI_MISO);
  Serial.printf("  SCK:  GPIO %d\n", PIN_SPI_SCLK);
  Serial.printf("  CS:   GPIO %d\n", PIN_SPI_CS);
  Serial.println("LED Strips:");
  Serial.printf("  Strip 0: GPIO %d\n", PIN_STRIP_0);
  Serial.printf("  Strip 1: GPIO %d\n", PIN_STRIP_1);
  Serial.printf("  Strip 2: GPIO %d\n", PIN_STRIP_2);
  Serial.printf("  Strip 3: GPIO %d\n", PIN_STRIP_3);
  Serial.printf("  Strip 4: GPIO %d\n", PIN_STRIP_4);
  Serial.printf("  Strip 5: GPIO %d\n", PIN_STRIP_5);
  Serial.printf("  Strip 6: GPIO %d\n", PIN_STRIP_6);
  Serial.printf("  Strip 7: GPIO %d\n", PIN_STRIP_7);

  // Init FastLED for all 8 strips (maximum supported)
  // Using MAX_LEDS_PER_STRIP to allow dynamic configuration via CMD_CONFIG
  FastLED.addLeds<NEOPIXEL, PIN_STRIP_0>(leds + (0 * MAX_LEDS_PER_STRIP), MAX_LEDS_PER_STRIP);
  FastLED.addLeds<NEOPIXEL, PIN_STRIP_1>(leds + (1 * MAX_LEDS_PER_STRIP), MAX_LEDS_PER_STRIP);
  FastLED.addLeds<NEOPIXEL, PIN_STRIP_2>(leds + (2 * MAX_LEDS_PER_STRIP), MAX_LEDS_PER_STRIP);
  FastLED.addLeds<NEOPIXEL, PIN_STRIP_3>(leds + (3 * MAX_LEDS_PER_STRIP), MAX_LEDS_PER_STRIP);
  FastLED.addLeds<NEOPIXEL, PIN_STRIP_4>(leds + (4 * MAX_LEDS_PER_STRIP), MAX_LEDS_PER_STRIP);
  FastLED.addLeds<NEOPIXEL, PIN_STRIP_5>(leds + (5 * MAX_LEDS_PER_STRIP), MAX_LEDS_PER_STRIP);
  FastLED.addLeds<NEOPIXEL, PIN_STRIP_6>(leds + (6 * MAX_LEDS_PER_STRIP), MAX_LEDS_PER_STRIP);
  FastLED.addLeds<NEOPIXEL, PIN_STRIP_7>(leds + (7 * MAX_LEDS_PER_STRIP), MAX_LEDS_PER_STRIP);

  FastLED.setBrightness(global_brightness);
  FastLED.clear();
  FastLED.show();

  pinMode(PIN_STATUS_LED, OUTPUT);
  digitalWrite(PIN_STATUS_LED, LOW);

  // Startup LED flash sequence
  for (uint16_t i = 0; i < total_leds; ++i) {
    leds[i] = CRGB(64, 64, 64);
  }
  FastLED.show();
  delay(200);
  FastLED.clear();
  FastLED.show();
  delay(200);

  // Configure SPI pins - CRITICAL: Set all as INPUT before SPI init
  gpio_reset_pin(PIN_SPI_CS);
  gpio_reset_pin(PIN_SPI_SCLK);
  gpio_reset_pin(PIN_SPI_MOSI);
  gpio_set_direction(PIN_SPI_CS, GPIO_MODE_INPUT);
  gpio_set_direction(PIN_SPI_SCLK, GPIO_MODE_INPUT);  // FIX: Explicitly set as input
  gpio_set_direction(PIN_SPI_MOSI, GPIO_MODE_INPUT);  // FIX: Explicitly set as input
  gpio_set_pull_mode(PIN_SPI_CS, GPIO_PULLUP_ONLY);
  gpio_set_pull_mode(PIN_SPI_SCLK, GPIO_FLOATING);
  gpio_set_pull_mode(PIN_SPI_MOSI, GPIO_FLOATING);

  // Configure SPI slave
  spi_bus_config_t bus_cfg = {};
  bus_cfg.mosi_io_num = PIN_SPI_MOSI;
  bus_cfg.miso_io_num = PIN_SPI_MISO;
  bus_cfg.sclk_io_num = PIN_SPI_SCLK;
  bus_cfg.quadwp_io_num = -1;
  bus_cfg.quadhd_io_num = -1;
  bus_cfg.max_transfer_sz = SPI_BUFFER_SIZE;
  bus_cfg.flags = SPICOMMON_BUSFLAG_SCLK | SPICOMMON_BUSFLAG_MOSI | SPICOMMON_BUSFLAG_MISO;
  bus_cfg.intr_flags = 0;

  spi_slave_interface_config_t slave_cfg = {};
  slave_cfg.mode = 3;  // CPOL=1, CPHA=1
  slave_cfg.spics_io_num = PIN_SPI_CS;
  slave_cfg.queue_size = 4;
  slave_cfg.flags = 0;
  slave_cfg.post_setup_cb = nullptr;
  slave_cfg.post_trans_cb = on_spi_post_transaction;

  esp_err_t err = spi_slave_initialize(SPI2_HOST, &bus_cfg, &slave_cfg, SPI_DMA_CH_AUTO);
  if (err != ESP_OK) {
    Serial.printf("❌ spi_slave_initialize failed: %d\n", err);
    while (true) delay(1000);
  }

  memset(spi_tx_buffer, 0, sizeof(spi_tx_buffer));

  Serial.println("\n✅ SPI slave ready");
  Serial.printf("Buffer size: %u bytes\n", SPI_BUFFER_SIZE);
  
  // DEBUG: Rainbow animation for first 10 seconds to verify LED strips
  Serial.println("\n🌈 Running rainbow animation for 10 seconds...");
  uint32_t rainbow_start = millis();
  uint8_t hue = 0;
  while (millis() - rainbow_start < 1000) {
    for (uint16_t i = 0; i < total_leds; ++i) {
      uint16_t physical = logical_to_physical(i);
      leds[physical] = CHSV(hue + (i * 256 / total_leds), 255, 200);
    }
    FastLED.show();
    hue += 2;
    delay(20);
  }
  
  // Clear after rainbow
  FastLED.clear();
  FastLED.show();
  Serial.println("✅ Rainbow complete, entering SPI mode\n");
}

void loop() {
  memset(spi_rx_buffer, 0, sizeof(spi_rx_buffer));

  spi_slave_transaction_t trans = {};
  trans.length = SPI_BUFFER_SIZE * 8;
  trans.tx_buffer = spi_tx_buffer;
  trans.rx_buffer = spi_rx_buffer;

  const esp_err_t err = spi_slave_transmit(SPI2_HOST, &trans, pdMS_TO_TICKS(100));
  if (err == ESP_OK) {
    if (trans.trans_len > 0) {
      const size_t bytes = trans.trans_len / 8;
      uint32_t now = millis();
      if (last_packet_millis != 0) {
        DEBUG_PRINT("⏱️ Interval: %lu ms\n", now - last_packet_millis);
      }
      last_packet_millis = now;

      DEBUG_PRINT("📥 %u bytes, cmd=0x%02X\n", static_cast<unsigned>(bytes), spi_rx_buffer[0]);
      process_command(spi_rx_buffer, bytes);
    }
  } else if (err != ESP_ERR_TIMEOUT) {
    Serial.printf("⚠️ SPI error %d\n", err);
  }

  // Stats every 5 seconds
  static uint32_t last_stats = 0;
  uint32_t now_ms = millis();
  if (now_ms - last_stats > 5000) {
    float fps = 0.0f;
    if (last_frame_sample_time != 0) {
      uint32_t dt = now_ms - last_frame_sample_time;
      uint32_t frames_delta = frames_rendered - last_frame_sample_count;
      if (dt > 0) {
        fps = (1000.0f * frames_delta) / static_cast<float>(dt);
      }
    }
    last_frame_sample_time = now_ms;
    last_frame_sample_count = frames_rendered;

    // Calculate throughput
    float throughput_kbps = 0.0f;
    if (last_bytes_sample_time != 0) {
      uint32_t dt = now_ms - last_bytes_sample_time;
      uint32_t bytes_delta = total_bytes_received - last_bytes_sample;
      if (dt > 0) {
        throughput_kbps = (bytes_delta * 8.0f) / static_cast<float>(dt);  // kbps
      }
    }
    last_bytes_sample = total_bytes_received;
    last_bytes_sample_time = now_ms;

    // Calculate frame success rate (successful SET_ALL commands)
    float set_all_success_rate = 0.0f;
    if (set_all_commands_received > 0) {
      // Only SET_ALL increments frames_rendered, so this should be close to 100%
      set_all_success_rate = (100.0f * frames_rendered) / static_cast<float>(set_all_commands_received);
    }
    
    // Calculate packet error rate
    float packet_error_rate = 0.0f;
    if (packets_received > 0) {
      packet_error_rate = (100.0f * zero_payload_packets) / static_cast<float>(packets_received);
    }

    Serial.printf("📊 Pkts=%u Frames=%u FPS=%.1f | Throughput=%.1fkb/s | Success=%.1f%% Errors=%.1f%% | Show=%luµs | Heap=%u\n",
                  static_cast<unsigned>(packets_received),
                  static_cast<unsigned>(frames_rendered),
                  fps,
                  throughput_kbps,
                  set_all_success_rate,
                  packet_error_rate,
                  static_cast<unsigned long>(last_show_duration),
                  static_cast<unsigned>(ESP.getFreeHeap()));
    Serial.printf("    Configs=%u SetAlls=%u ZeroPayload=%u | Buffer=%u/%u bytes | %ux%u LEDs\n",
                  static_cast<unsigned>(config_commands_received),
                  static_cast<unsigned>(set_all_commands_received),
                  static_cast<unsigned>(zero_payload_packets),
                  static_cast<unsigned>(SPI_FRAME_BYTES),
                  static_cast<unsigned>(SPI_BUFFER_SIZE),
                  active_strips,
                  leds_per_strip);
    last_stats = now_ms;
  }
}
