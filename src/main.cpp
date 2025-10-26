// SPI Slave LED Controller - Using the WORKING approach
// Continuous TX FIFO fill + manual RX FIFO reads (no DMA)
// This matches the test that successfully received data!

#include <Arduino.h>
#include <Adafruit_NeoPXL8.h>
#include "hardware/spi.h"
#include "hardware/gpio.h"

#define SPI_INST spi1
#define SPI_MOSI_PIN 12
#define SPI_CS_PIN 13
#define SPI_SCK_PIN 14
#define SPI_MISO_PIN 15
#define SPI_BAUDRATE (10 * 1000 * 1000)

// LED Configuration
#define NUM_LED 20
#define TOTAL_LEDS (NUM_LED * 8)

// Commands
#define CMD_SET_PIXEL 0x01
#define CMD_SET_BRIGHTNESS 0x02
#define CMD_SHOW 0x03
#define CMD_CLEAR 0x04
#define CMD_SET_RANGE 0x05
#define CMD_SET_ALL_PIXELS 0x06
#define CMD_PING 0xFF

// NeoPixel strips
int8_t pins[8] = { 16, 17, 18, 19, 20, 21, 22, 23 };
Adafruit_NeoPXL8 leds(NUM_LED, pins, NEO_GRB);

// Command buffer
#define CMD_BUFFER_SIZE 512
uint8_t cmd_buffer[CMD_BUFFER_SIZE];
uint16_t cmd_index = 0;
bool in_transaction = false;

void processCommand() {
  if (cmd_index == 0) return;
  
  uint8_t cmd = cmd_buffer[0];
  
  Serial.print("[CMD] 0x");
  Serial.print(cmd, HEX);
  Serial.print(" (");
  Serial.print(cmd_index);
  Serial.println(" bytes)");
  
  switch (cmd) {
    case CMD_PING:
      Serial.println("  → PING");
      break;
    
    case CMD_SET_PIXEL:
      if (cmd_index >= 6) {
        uint16_t pixel = (cmd_buffer[1] << 8) | cmd_buffer[2];
        uint8_t r = cmd_buffer[3];
        uint8_t g = cmd_buffer[4];
        uint8_t b = cmd_buffer[5];
        if (pixel < TOTAL_LEDS) {
          leds.setPixelColor(pixel, leds.Color(r, g, b));
        }
      }
      break;
    
    case CMD_SET_BRIGHTNESS:
      if (cmd_index >= 2) {
        leds.setBrightness(cmd_buffer[1]);
      }
      break;
    
    case CMD_SHOW:
      leds.show();
      Serial.println("  → SHOW");
      break;
    
    case CMD_CLEAR:
      for (int i = 0; i < TOTAL_LEDS; i++) {
        leds.setPixelColor(i, 0);
      }
      leds.show();
      Serial.println("  → CLEAR");
      break;
    
    case CMD_SET_ALL_PIXELS:
      if (cmd_index >= 1 + (TOTAL_LEDS * 3)) {
        for (uint16_t i = 0; i < TOTAL_LEDS; i++) {
          uint8_t r = cmd_buffer[1 + (i * 3)];
          uint8_t g = cmd_buffer[1 + (i * 3) + 1];
          uint8_t b = cmd_buffer[1 + (i * 3) + 2];
          leds.setPixelColor(i, leds.Color(r, g, b));
        }
        Serial.println("  → SET_ALL");
      }
      break;
  }
}

void setup() {
  Serial.begin(115200);
  delay(2000);
  
  Serial.println("\n\n========================================");
  Serial.println("LED Controller (Working Method)");
  Serial.println("Mode 3 + Continuous TX Fill");
  Serial.println("========================================\n");
  
  // Initialize LEDs
  if (!leds.begin()) {
    Serial.println("✗ NeoPXL8 failed!");
    while (1) delay(1000);
  }
  leds.setBrightness(50);
  for (int i = 0; i < TOTAL_LEDS; i++) {
    leds.setPixelColor(i, 0);
  }
  leds.show();
  Serial.println("✓ NeoPXL8 initialized");
  
  // Initialize SPI slave with Mode 3
  spi_init(SPI_INST, SPI_BAUDRATE);
  spi_set_slave(SPI_INST, true);
  spi_set_format(SPI_INST, 8, SPI_CPOL_1, SPI_CPHA_1, SPI_MSB_FIRST);
  
  gpio_set_function(SPI_MOSI_PIN, GPIO_FUNC_SPI);
  gpio_set_function(SPI_SCK_PIN, GPIO_FUNC_SPI);
  gpio_set_function(SPI_MISO_PIN, GPIO_FUNC_SPI);
  
  gpio_init(SPI_CS_PIN);
  gpio_set_dir(SPI_CS_PIN, GPIO_IN);
  gpio_pull_up(SPI_CS_PIN);
  
  Serial.println("✓ SPI1 slave configured (Mode 3)");
  Serial.println("\n*** Ready for commands! ***\n");
}

void loop() {
  // CRITICAL: Keep TX FIFO filled!
  while (spi_is_writable(SPI_INST)) {
    spi_get_hw(SPI_INST)->dr = 0x00;
  }
  
  // Check CS state
  bool cs_low = !gpio_get(SPI_CS_PIN);
  
  if (cs_low && !in_transaction) {
    // Start of transaction
    in_transaction = true;
    cmd_index = 0;
    memset(cmd_buffer, 0, CMD_BUFFER_SIZE);
  }
  
  if (!cs_low && in_transaction) {
    // End of transaction
    in_transaction = false;
    processCommand();
  }
  
  // Read RX FIFO
  while (spi_is_readable(SPI_INST)) {
    uint8_t byte = spi_get_hw(SPI_INST)->dr;
    if (in_transaction && cmd_index < CMD_BUFFER_SIZE) {
      cmd_buffer[cmd_index++] = byte;
    }
  }
}

