#include <Arduino.h>
#include <FastLED.h>
#include "driver/spi_slave.h"
#include "driver/spi_common.h"
#include "driver/gpio.h"

// =========================
// SPI pin mapping
// =========================
static constexpr gpio_num_t PIN_SPI_MOSI = GPIO_NUM_9;  // XIAO D10
static constexpr gpio_num_t PIN_SPI_MISO = GPIO_NUM_8;  // XIAO D9 (unused but required)
static constexpr gpio_num_t PIN_SPI_SCLK = GPIO_NUM_7;  // XIAO D8
static constexpr gpio_num_t PIN_SPI_CS   = GPIO_NUM_44;  // XIAO D7 (RX)

// =========================
// LED configuration
// =========================
static constexpr uint8_t MAX_STRIPS         = 7;
static constexpr uint16_t MAX_LEDS_PER_STRIP = 500;
static constexpr uint16_t MAX_TOTAL_LEDS    = MAX_STRIPS * MAX_LEDS_PER_STRIP;

static constexpr uint8_t DEFAULT_STRIPS     = 7;
static constexpr uint16_t DEFAULT_LEDS_PER_STRIP = 140;

static constexpr gpio_num_t STRIP_DATA_PINS[MAX_STRIPS] = {
  GPIO_NUM_1,  // D0
  GPIO_NUM_2,  // D1
  GPIO_NUM_3,  // D2
  GPIO_NUM_4,  // D3
  GPIO_NUM_5,  // D4
  GPIO_NUM_6,  // D5
  GPIO_NUM_43  // D6
};
static constexpr uint8_t PIN_STATUS_LED     = LED_BUILTIN;

static CRGB leds[MAX_TOTAL_LEDS];
static uint8_t active_strips = DEFAULT_STRIPS;
static uint16_t leds_per_strip = DEFAULT_LEDS_PER_STRIP;
static uint16_t total_leds = DEFAULT_STRIPS * DEFAULT_LEDS_PER_STRIP;
static uint8_t global_brightness = 50;  // default brightness

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
// SPI buffers / bookkeeping
// =========================
static constexpr size_t SPI_FRAME_BYTES = 1 + (MAX_TOTAL_LEDS * 3);
static constexpr size_t SPI_BUFFER_SIZE = ((SPI_FRAME_BYTES + 63) / 64) * 64;
DMA_ATTR static uint8_t spi_rx_buffer[SPI_BUFFER_SIZE];
DMA_ATTR static uint8_t spi_tx_buffer[SPI_BUFFER_SIZE];

static volatile uint32_t packets_received = 0;
static volatile uint32_t frames_rendered = 0;
static volatile uint32_t cs_edge_count = 0;
static volatile uint32_t sck_edge_count = 0;
static volatile uint32_t mosi_edge_count = 0;
static volatile uint32_t zero_payload_packets = 0;

static volatile uint32_t frame_count_interval = 0; // unused but kept for reference
static uint32_t last_fps_millis = 0;
static uint32_t last_frame_sample_time = 0;
static uint32_t last_frame_sample_count = 0;

static uint32_t last_packet_millis = 0;
static uint32_t last_show_micros = 0;
static uint32_t last_show_duration = 0;
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

static void IRAM_ATTR cs_edge_isr(void* arg) {
  (void)arg;
  cs_edge_count++;
}

static void IRAM_ATTR sck_edge_isr(void* arg) {
  (void)arg;
  sck_edge_count++;
}

static void IRAM_ATTR mosi_edge_isr(void* arg) {
  (void)arg;
  mosi_edge_count++;
}

static void process_command(const uint8_t *data, size_t length) {
  if (length == 0) {
    return;
  }

  const uint8_t cmd = data[0];

  if (length > 1) {
    uint8_t payload_or = 0;
    for (size_t i = 1; i < length; ++i) {
      payload_or |= data[i];
    }
    if (payload_or == 0) {
      zero_payload_packets++;
      DEBUG_PRINT("⚠️ Packet cmd=0x%02X length=%u has zero payload\n", cmd, static_cast<unsigned>(length));
    }
  }

  switch (cmd) {
    case CMD_PING: {
      DEBUG_PRINTLN("📥 CMD_PING");
      digitalWrite(PIN_STATUS_LED, !digitalRead(PIN_STATUS_LED));
      break;
    }

    case CMD_SET_PIXEL: {
      if (length < 6) {
        Serial.println("⚠️ CMD_SET_PIXEL too short");
        return;
      }
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
      if (length < 2) {
        Serial.println("⚠️ CMD_SET_BRIGHTNESS too short");
        return;
      }
      global_brightness = data[1];
      FastLED.setBrightness(global_brightness);
      DEBUG_PRINT("📥 Brightness → %u\n", global_brightness);
      break;
    }

    case CMD_SHOW: {
      uint32_t start_us = micros();
      FastLED.show();
      last_show_duration = micros() - start_us;
      frames_rendered++;
      last_show_micros = micros();
      DEBUG_PRINTLN("📥 CMD_SHOW");
      break;
    }

    case CMD_CLEAR: {
      for (uint8_t strip = 0; strip < active_strips; ++strip) {
        for (uint16_t offset = 0; offset < MAX_LEDS_PER_STRIP; ++offset) {
          leds[strip * MAX_LEDS_PER_STRIP + offset] = CRGB::Black;
        }
      }
      for (uint8_t strip = active_strips; strip < MAX_STRIPS; ++strip) {
        for (uint16_t offset = 0; offset < MAX_LEDS_PER_STRIP; ++offset) {
          leds[strip * MAX_LEDS_PER_STRIP + offset] = CRGB::Black;
        }
      }
      FastLED.show();
      frames_rendered++;
      DEBUG_PRINTLN("📥 CMD_CLEAR");
      break;
    }

    case CMD_SET_RANGE: {
      if (length < 4) {
        Serial.println("⚠️ CMD_SET_RANGE too short");
        return;
      }
      const uint16_t start = (static_cast<uint16_t>(data[1]) << 8) | data[2];
      if (start >= total_leds) {
        break;
      }

      uint8_t count = data[3];
      const size_t expected = 4 + static_cast<size_t>(count) * 3;
      if (length < expected) {
        Serial.printf("⚠️ CMD_SET_RANGE expected %u bytes, got %u\n", static_cast<unsigned>(expected), static_cast<unsigned>(length));
        return;
      }

      if (start + count > total_leds) {
        count = total_leds - start;
      }

      for (uint8_t i = 0; i < count; ++i) {
        const uint16_t logical = start + i;
        if (logical >= total_leds) {
          break;
        }
        const size_t base = 4 + static_cast<size_t>(i) * 3;
        leds[logical_to_physical(logical)] = CRGB(data[base], data[base + 1], data[base + 2]);
      }
      break;
    }

    case CMD_SET_ALL: {
      const size_t expected = 1 + static_cast<size_t>(total_leds) * 3;
      if (length < expected) {
        Serial.printf("⚠️ CMD_SET_ALL expected %u bytes, got %u\n", static_cast<unsigned>(expected), static_cast<unsigned>(length));
        return;
      }

      for (uint16_t logical = 0; logical < total_leds; ++logical) {
        const size_t base = 1 + static_cast<size_t>(logical) * 3;
        leds[logical_to_physical(logical)] = CRGB(data[base], data[base + 1], data[base + 2]);
      }
      for (uint8_t strip = 0; strip < active_strips; ++strip) {
        for (uint16_t offset = leds_per_strip; offset < MAX_LEDS_PER_STRIP; ++offset) {
          leds[strip * MAX_LEDS_PER_STRIP + offset] = CRGB::Black;
        }
      }
      for (uint8_t strip = active_strips; strip < MAX_STRIPS; ++strip) {
        for (uint16_t offset = 0; offset < MAX_LEDS_PER_STRIP; ++offset) {
          leds[strip * MAX_LEDS_PER_STRIP + offset] = CRGB::Black;
        }
      }

      uint32_t start_us = micros();
      FastLED.show();
      last_show_duration = micros() - start_us;
      frames_rendered++;
      last_show_micros = micros();
      break;
    }

    case CMD_CONFIG: {
      if (length < 4) {
        Serial.println("⚠️ CMD_CONFIG too short");
        return;
      }
      uint8_t new_strips = data[1];
      uint16_t new_len = (static_cast<uint16_t>(data[2]) << 8) | data[3];

      if (new_strips == 0 || new_strips > MAX_STRIPS) {
        Serial.printf("⚠️ CMD_CONFIG strips out of range: %u\n", new_strips);
        return;
      }
      if (new_len == 0 || new_len > MAX_LEDS_PER_STRIP) {
        Serial.printf("⚠️ CMD_CONFIG length out of range: %u\n", new_len);
        return;
      }

      active_strips = new_strips;
      leds_per_strip = new_len;
      total_leds = active_strips * leds_per_strip;

      for (uint8_t strip = 0; strip < active_strips; ++strip) {
        for (uint16_t offset = leds_per_strip; offset < MAX_LEDS_PER_STRIP; ++offset) {
          leds[strip * MAX_LEDS_PER_STRIP + offset] = CRGB::Black;
        }
      }
      for (uint8_t strip = active_strips; strip < MAX_STRIPS; ++strip) {
        for (uint16_t offset = 0; offset < MAX_LEDS_PER_STRIP; ++offset) {
          leds[strip * MAX_LEDS_PER_STRIP + offset] = CRGB::Black;
        }
      }
      FastLED.show();
      if (length >= 5) {
        debug_logging = data[4] != 0;
        if (debug_logging) {
          Serial.println("🔧 Debug logging enabled");
        }
      }

      DEBUG_PRINT("📐 Config updated: strips=%u length=%u total=%u\n",
                  active_strips, leds_per_strip, total_leds);
      break;
    }

    default: {
      DEBUG_PRINT("⚠️ Unknown command 0x%02X\n", cmd);
      break;
    }
  }
}

void setup() {
  Serial.begin(115200);
  delay(2000);
  while (!Serial && millis() < 5000) {
    delay(50);
  }

  DEBUG_PRINTLN("");
  DEBUG_PRINTLN("========================================");
  DEBUG_PRINTLN("ESP32-S3 SPI slave LED controller");
  DEBUG_PRINTLN("========================================");

  // Init FastLED for each strip (dedicated GPIOs D0-D6)
  FastLED.addLeds<NEOPIXEL, GPIO_NUM_1>(leds + (0 * MAX_LEDS_PER_STRIP), MAX_LEDS_PER_STRIP);  // Strip 0 on D0
  FastLED.addLeds<NEOPIXEL, GPIO_NUM_2>(leds + (1 * MAX_LEDS_PER_STRIP), MAX_LEDS_PER_STRIP);  // Strip 1 on D1
  FastLED.addLeds<NEOPIXEL, GPIO_NUM_3>(leds + (2 * MAX_LEDS_PER_STRIP), MAX_LEDS_PER_STRIP);  // Strip 2 on D2
  FastLED.addLeds<NEOPIXEL, GPIO_NUM_4>(leds + (3 * MAX_LEDS_PER_STRIP), MAX_LEDS_PER_STRIP);  // Strip 3 on D3
  FastLED.addLeds<NEOPIXEL, GPIO_NUM_5>(leds + (4 * MAX_LEDS_PER_STRIP), MAX_LEDS_PER_STRIP);  // Strip 4 on D4
  FastLED.addLeds<NEOPIXEL, GPIO_NUM_6>(leds + (5 * MAX_LEDS_PER_STRIP), MAX_LEDS_PER_STRIP);  // Strip 5 on D5
  FastLED.addLeds<NEOPIXEL, GPIO_NUM_43>(leds + (6 * MAX_LEDS_PER_STRIP), MAX_LEDS_PER_STRIP);  // Strip 6 on D6

  for (uint8_t strip = 0; strip < active_strips; ++strip) {
    DEBUG_PRINT("Strip %u -> GPIO%d\n", strip, static_cast<int>(STRIP_DATA_PINS[strip]));
  }
  FastLED.setBrightness(global_brightness);
  FastLED.clear();
  FastLED.show();

  pinMode(PIN_STATUS_LED, OUTPUT);
  digitalWrite(PIN_STATUS_LED, LOW);

  // Startup LED flash sequence to verify wiring
  for (uint16_t i = 0; i < total_leds; ++i) {
    leds[i] = CRGB(64, 64, 64);
  }
  FastLED.show();
  delay(200);
  FastLED.clear();
  FastLED.show();
  delay(200);

  // configure SPI slave driver
  gpio_reset_pin(PIN_SPI_CS);
  gpio_reset_pin(PIN_SPI_SCLK);
  gpio_reset_pin(PIN_SPI_MOSI);
  gpio_set_direction(PIN_SPI_CS, GPIO_MODE_INPUT);
  gpio_set_pull_mode(PIN_SPI_CS, GPIO_PULLUP_ONLY);
  gpio_set_pull_mode(PIN_SPI_SCLK, GPIO_FLOATING);
  gpio_set_pull_mode(PIN_SPI_MOSI, GPIO_FLOATING);

  esp_err_t isr_ret = gpio_install_isr_service(0);
  if (isr_ret != ESP_OK && isr_ret != ESP_ERR_INVALID_STATE) {
    Serial.printf("❌ gpio_install_isr_service failed: %d\n", isr_ret);
  }
  gpio_set_intr_type(PIN_SPI_CS, GPIO_INTR_ANYEDGE);
  gpio_set_intr_type(PIN_SPI_SCLK, GPIO_INTR_ANYEDGE);
  gpio_set_intr_type(PIN_SPI_MOSI, GPIO_INTR_ANYEDGE);
  gpio_isr_handler_add(PIN_SPI_CS, cs_edge_isr, nullptr);
  gpio_isr_handler_add(PIN_SPI_SCLK, sck_edge_isr, nullptr);
  gpio_isr_handler_add(PIN_SPI_MOSI, mosi_edge_isr, nullptr);

  spi_bus_config_t bus_cfg = {};
  bus_cfg.mosi_io_num = PIN_SPI_MOSI;
  bus_cfg.miso_io_num = PIN_SPI_MISO;
  bus_cfg.sclk_io_num = PIN_SPI_SCLK;
  bus_cfg.quadwp_io_num = -1;
  bus_cfg.quadhd_io_num = -1;
  bus_cfg.max_transfer_sz = SPI_BUFFER_SIZE;
  bus_cfg.flags = SPICOMMON_BUSFLAG_SCLK | SPICOMMON_BUSFLAG_MOSI;
  if (PIN_SPI_MISO != GPIO_NUM_NC) {
    bus_cfg.flags |= SPICOMMON_BUSFLAG_MISO;
  }
  bus_cfg.intr_flags = 0;

  spi_slave_interface_config_t slave_cfg = {};
  slave_cfg.mode = 3;  // CPOL=1, CPHA=1 → matches Raspberry Pi driver
  slave_cfg.spics_io_num = PIN_SPI_CS;
  slave_cfg.queue_size = 4;
  slave_cfg.flags = 0;
  slave_cfg.post_setup_cb = nullptr;
  slave_cfg.post_trans_cb = on_spi_post_transaction;

  esp_err_t err = spi_slave_initialize(SPI2_HOST, &bus_cfg, &slave_cfg, SPI_DMA_CH_AUTO);
  if (err != ESP_OK) {
    Serial.printf("❌ spi_slave_initialize failed: %d\n", err);
    while (true) {
      delay(1000);
    }
  }

  // preload dummy TX buffer (all zeros)
  memset(spi_tx_buffer, 0, sizeof(spi_tx_buffer));

  DEBUG_PRINTLN("SPI slave ready: MOSI=D10, MISO=D9, SCK=D8, CS=D7");
  DEBUG_PRINT("Total LEDs: %u (per strip: %u)\n", static_cast<unsigned>(total_leds), static_cast<unsigned>(leds_per_strip));
}

void loop() {
  memset(spi_rx_buffer, 0, sizeof(spi_rx_buffer));

  spi_slave_transaction_t trans = {};
  trans.length = SPI_BUFFER_SIZE * 8;  // length in bits
  trans.tx_buffer = spi_tx_buffer;
  trans.rx_buffer = spi_rx_buffer;

  const esp_err_t err = spi_slave_transmit(SPI2_HOST, &trans, pdMS_TO_TICKS(100));
  if (err == ESP_OK) {
    if (trans.trans_len > 0) {
      const size_t bytes = trans.trans_len / 8;
      uint32_t now = millis();
      if (last_packet_millis != 0) {
        DEBUG_PRINT("⏱️ Packet interval: %lu ms\n", static_cast<unsigned long>(now - last_packet_millis));
      }
      last_packet_millis = now;

      DEBUG_PRINT("📥 Packet %u bytes, cmd=0x%02X\n", static_cast<unsigned>(bytes), spi_rx_buffer[0]);
      if (bytes < (1 + static_cast<size_t>(total_leds) * 3) && spi_rx_buffer[0] == CMD_SET_ALL && debug_logging) {
        Serial.println("⚠️ Packet shorter than expected frame size");
      }
      process_command(spi_rx_buffer, bytes);
    }
  } else if (err != ESP_ERR_TIMEOUT) {
    Serial.printf("⚠️ spi_slave_transmit error %d\n", err);
  }

  static uint32_t last_stats = 0;
  uint32_t now_ms = millis();
  if (now_ms - last_stats > 5000) {
    float esp_fps = 0.0f;
    if (last_frame_sample_time != 0) {
      uint32_t dt = now_ms - last_frame_sample_time;
      uint32_t frames_delta = frames_rendered - last_frame_sample_count;
      if (dt > 0) {
        esp_fps = (1000.0f * frames_delta) / static_cast<float>(dt);
      }
    }
    last_frame_sample_time = now_ms;
    last_frame_sample_count = frames_rendered;

    Serial.printf("📊 Packets=%u Frames=%u FreeHeap=%u | CS=%u SCK=%u MOSI=%u | ZeroPayload=%u | LastShow=%lu µs | ESP_FPS=%.1f | Config=%u×%u\n",
                  static_cast<unsigned>(packets_received),
                  static_cast<unsigned>(frames_rendered),
                  static_cast<unsigned>(ESP.getFreeHeap()),
                  static_cast<unsigned>(cs_edge_count),
                  static_cast<unsigned>(sck_edge_count),
                  static_cast<unsigned>(mosi_edge_count),
                  static_cast<unsigned>(zero_payload_packets),
                  static_cast<unsigned long>(last_show_duration),
                  esp_fps,
                  active_strips,
                  leds_per_strip);
    last_stats = now_ms;
  }
}


