#include <Arduino.h>
#include <FastLED.h>

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
// UART protocol definitions
// =========================
static constexpr uint8_t CMD_SET_PIXEL      = 0x01;
static constexpr uint8_t CMD_SET_BRIGHTNESS = 0x02;
static constexpr uint8_t CMD_SHOW           = 0x03;
static constexpr uint8_t CMD_CLEAR          = 0x04;
static constexpr uint8_t CMD_SET_RANGE      = 0x05;
static constexpr uint8_t CMD_SET_ALL        = 0x06;
static constexpr uint8_t CMD_CONFIG         = 0x07;
static constexpr uint8_t CMD_ECHO           = 0xFE;
static constexpr uint8_t CMD_PING           = 0xFF;

// UART packet framing
static constexpr uint8_t PACKET_START       = 0xAA;
static constexpr uint8_t PACKET_END         = 0x55;
static constexpr size_t MAX_PACKET_SIZE     = 1 + (MAX_TOTAL_LEDS * 3);  // cmd + RGB data
static uint8_t uart_buffer[MAX_PACKET_SIZE];

// Response codes
static constexpr uint8_t RESP_OK            = 0x00;
static constexpr uint8_t RESP_ERROR         = 0x01;
static constexpr uint8_t RESP_STATUS        = 0x02;

// =========================
// Statistics
// =========================
static volatile uint32_t packets_received = 0;
static volatile uint32_t frames_rendered = 0;
static volatile uint32_t packet_errors = 0;
static volatile uint32_t config_commands_received = 0;
static volatile uint32_t set_all_commands_received = 0;

static uint32_t last_show_duration = 0;
static uint32_t last_frame_sample_time = 0;
static uint32_t last_frame_sample_count = 0;
static uint32_t total_bytes_received = 0;
static uint32_t last_bytes_sample = 0;
static uint32_t last_bytes_sample_time = 0;
static bool debug_logging = false;

#define DEBUG_PRINT(...) do { if (debug_logging) { Serial.printf(__VA_ARGS__); } } while (0)
#define DEBUG_PRINTLN(msg) do { if (debug_logging) { Serial.println(msg); } } while (0)

// Send response packet back to Pi
void send_response(uint8_t response_code, const char* message = nullptr) {
  Serial.write(PACKET_START);
  
  // Calculate payload length
  uint16_t payload_len = 1;  // response code
  if (message) {
    payload_len += strlen(message);
  }
  
  Serial.write(payload_len & 0xFF);
  Serial.write((payload_len >> 8) & 0xFF);
  Serial.write(response_code);
  
  if (message) {
    Serial.print(message);
  }
  
  Serial.write(PACKET_END);
  Serial.flush();
}

inline uint16_t logical_to_physical(uint16_t logical) {
  uint16_t strip = logical / leds_per_strip;
  uint16_t offset = logical % leds_per_strip;
  if (strip >= active_strips) {
    strip = active_strips - 1;
    offset = leds_per_strip - 1;
  }
  return strip * MAX_LEDS_PER_STRIP + offset;
}

static void process_command(const uint8_t *data, size_t length) {
  if (length == 0) return;

  total_bytes_received += length;
  const uint8_t cmd = data[0];
  packets_received++;

  // Always log first 20 packets for debugging
  static uint32_t startup_packet_count = 0;
  if (startup_packet_count < 20) {
    Serial.printf("🔍 Pkt#%u: cmd=0x%02X len=%u bytes: ", 
                  startup_packet_count++, cmd, length);
    for (size_t i = 0; i < min(length, (size_t)32); i++) {
      Serial.printf("%02X ", data[i]);
    }
    if (length > 32) Serial.print("...");
    Serial.println();
  }
  
  DEBUG_PRINT("📥 Pkt#%u: cmd=0x%02X len=%u\n", packets_received, cmd, length);

  switch (cmd) {
    case CMD_ECHO: {
      // Echo back exactly what we received
      Serial.printf("📥 CMD_ECHO: %u bytes received, echoing back...\n", length);
      
      // Print what we received
      Serial.print("   RX: ");
      for (size_t i = 0; i < min(length, (size_t)32); i++) {
        Serial.printf("%02X ", data[i]);
      }
      if (length > 32) Serial.print("...");
      Serial.println();
      
      // Echo it back with RESP_OK prefix
      Serial.write(PACKET_START);
      uint16_t response_len = 1 + length; // RESP_OK + original data
      Serial.write(response_len & 0xFF);
      Serial.write((response_len >> 8) & 0xFF);
      Serial.write(RESP_OK);
      Serial.write(data, length); // Echo back the entire payload
      Serial.write(PACKET_END);
      Serial.flush();
      
      Serial.println("   ✅ Echo sent");
      break;
    }
    
    case CMD_PING: {
      Serial.println("📥 CMD_PING received - sending ACK");
      digitalWrite(PIN_STATUS_LED, !digitalRead(PIN_STATUS_LED));
      send_response(RESP_OK, "PONG");
      Serial.println("✅ ACK sent");
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
        Serial.printf("⚠️ CMD_SET_ALL expected %u bytes, got %u (strips=%u, leds=%u)\n", 
                      static_cast<unsigned>(expected), static_cast<unsigned>(length),
                      active_strips, leds_per_strip);
        packet_errors++;
        send_response(RESP_ERROR, "SIZE_MISMATCH");
        return;
      }

      // Log first few SET_ALL for debugging
      if (set_all_commands_received <= 5) {
        Serial.printf("✅ CMD_SET_ALL #%u: %u bytes, first RGB: (%02X,%02X,%02X) - rendering...\n",
                      static_cast<unsigned>(set_all_commands_received),
                      static_cast<unsigned>(length),
                      data[1], data[2], data[3]);
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
      
      // Send acknowledgment for first few frames
      if (frames_rendered <= 3) {
        send_response(RESP_OK, "FRAME_OK");
      }
      break;
    }

    case CMD_CONFIG: {
      config_commands_received++;
      if (length < 4) {
        send_response(RESP_ERROR, "CONFIG_TOO_SHORT");
        return;
      }
      uint8_t new_strips = data[1];
      uint16_t new_len = (static_cast<uint16_t>(data[2]) << 8) | data[3];

      if (new_strips == 0 || new_strips > MAX_STRIPS) {
        Serial.printf("⚠️ Invalid strips: %u (max %u)\n", new_strips, MAX_STRIPS);
        send_response(RESP_ERROR, "INVALID_STRIPS");
        return;
      }
      if (new_len == 0 || new_len > MAX_LEDS_PER_STRIP) {
        Serial.printf("⚠️ Invalid LEDs/strip: %u (max %u)\n", new_len, MAX_LEDS_PER_STRIP);
        send_response(RESP_ERROR, "INVALID_LENGTH");
        return;
      }

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
        Serial.printf("📐 Config changed: strips=%u, length=%u, total=%u (cleared LEDs)\n",
                    active_strips, leds_per_strip, total_leds);
        send_response(RESP_OK, "CONFIG_CHANGED");
      } else {
        DEBUG_PRINT("📐 Config refresh: strips=%u, length=%u, total=%u (no change)\n",
                    active_strips, leds_per_strip, total_leds);
        send_response(RESP_OK, "CONFIG_OK");
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
      packet_errors++;
      break;
  }
}

void setup() {
  // Initialize Serial (USB-CDC)
  Serial.begin(115200);  // Standard baudrate for compatibility
  delay(1000);  // Give time for serial to stabilize
  
  Serial.println("");
  Serial.println("========================================");
  Serial.println("ESP32-S3 DevKitC UART LED Controller");  
  Serial.println("========================================");
  Serial.printf("Board: ESP32-S3 DevKitC (8MB Flash)\n");
  Serial.printf("Strips: %d x %d LEDs = %d total\n", active_strips, leds_per_strip, total_leds);
  Serial.printf("Protocol: UART (USB-CDC) @ 115200 bps\n");
  Serial.printf("Max packet size: %u bytes\n", MAX_PACKET_SIZE);
  Serial.println("\nLED Strip Pins:");
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
  
  // Rainbow animation for first 1 second to verify LED strips
  Serial.println("\n🌈 Running rainbow animation for 1 second...");
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
  Serial.println("✅ Rainbow complete, entering UART mode\n");
  Serial.println("Waiting for packets...\n");
}

void loop() {
  // UART packet reading with framing
  // Packet format: [0xAA] [LEN_LOW] [LEN_HIGH] [PAYLOAD...] [0x55]
  
  if (Serial.available() >= 4) {  // At least: start + len(2) + end
    uint8_t start = Serial.read();
    
    if (start == PACKET_START) {
      // Read packet length (16-bit, little-endian)
      uint8_t len_low = Serial.read();
      uint8_t len_high = Serial.read();
      uint16_t payload_len = len_low | (len_high << 8);
      
      // Validate length
      if (payload_len > MAX_PACKET_SIZE) {
        DEBUG_PRINT("⚠️ Invalid packet length: %u (max %u)\n", payload_len, MAX_PACKET_SIZE);
        packet_errors++;
        // Flush serial buffer
        while (Serial.available()) Serial.read();
        return;
      }
      
      // Wait for complete payload + end byte (with timeout)
      uint32_t timeout_start = millis();
      while (Serial.available() < payload_len + 1) {
        if (millis() - timeout_start > 100) {  // 100ms timeout
          DEBUG_PRINTLN("⚠️ Packet timeout");
          packet_errors++;
          return;
        }
        delayMicroseconds(100);
      }
      
      // Read payload
      Serial.readBytes(uart_buffer, payload_len);
      
      // Read end marker
      uint8_t end = Serial.read();
      if (end != PACKET_END) {
        DEBUG_PRINT("⚠️ Invalid end marker: 0x%02X (expected 0x%02X)\n", end, PACKET_END);
        packet_errors++;
        return;
      }
      
      // Process the command
      process_command(uart_buffer, payload_len);
    } else {
      // Not a start marker - discard and resync
      DEBUG_PRINT("⚠️ Expected start marker 0x%02X, got 0x%02X\n", PACKET_START, start);
      packet_errors++;
    }
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

    Serial.printf("📊 Pkts=%u Frames=%u FPS=%.1f | Throughput=%.1fkb/s | Errors=%u | Show=%luµs | Heap=%u\n",
                  static_cast<unsigned>(packets_received),
                  static_cast<unsigned>(frames_rendered),
                  fps,
                  throughput_kbps,
                  static_cast<unsigned>(packet_errors),
                  static_cast<unsigned long>(last_show_duration),
                  static_cast<unsigned>(ESP.getFreeHeap()));
    Serial.printf("    Configs=%u SetAlls=%u | %ux%u LEDs\n",
                  static_cast<unsigned>(config_commands_received),
                  static_cast<unsigned>(set_all_commands_received),
                  active_strips,
                  leds_per_strip);
    last_stats = now_ms;
  }
}
