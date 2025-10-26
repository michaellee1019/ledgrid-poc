// Simple SPI slave test - manual FIFO reading (no DMA)
// This will prove if SPI peripheral is receiving data

#include <Arduino.h>
#include "hardware/spi.h"
#include "hardware/gpio.h"

#define SPI_INST spi1
#define SPI_MOSI_PIN 12
#define SPI_CS_PIN 13
#define SPI_SCK_PIN 14
#define SPI_MISO_PIN 15

#define SPI_BAUDRATE (1 * 1000 * 1000)  // 1 MHz for testing

void setup() {
  Serial.begin(115200);
  delay(2000);
  
  Serial.println("\n\n========================================");
  Serial.println("SPI Slave Manual FIFO Test");
  Serial.println("========================================");
  Serial.println("Testing if SPI peripheral receives data");
  Serial.println("WITHOUT DMA - just reading FIFO directly");
  Serial.println("========================================\n");
  
  // Initialize SPI1 in SLAVE mode
  spi_init(SPI_INST, SPI_BAUDRATE);
  spi_set_slave(SPI_INST, true);  // SLAVE mode
  
  // Configure SPI format: 8 bits, Mode 3 (CPOL=1, CPHA=1)
  // Mode 3 is MORE RELIABLE on RP2040 slave - avoids dropped bytes!
  spi_set_format(SPI_INST, 8, SPI_CPOL_1, SPI_CPHA_1, SPI_MSB_FIRST);
  
  // Set up SPI pins
  gpio_set_function(SPI_MOSI_PIN, GPIO_FUNC_SPI);
  gpio_set_function(SPI_SCK_PIN, GPIO_FUNC_SPI);
  gpio_set_function(SPI_MISO_PIN, GPIO_FUNC_SPI);
  
  // CS as GPIO input
  gpio_init(SPI_CS_PIN);
  gpio_set_dir(SPI_CS_PIN, GPIO_IN);
  gpio_pull_up(SPI_CS_PIN);
  
  Serial.println("✓ SPI1 configured as SLAVE");
  Serial.println("  MOSI: GPIO 12");
  Serial.println("  SCK:  GPIO 14");
  Serial.println("  CS:   GPIO 13 (GPIO mode)");
  Serial.println("  MISO: GPIO 15");
  Serial.println("\n*** Send data from Raspberry Pi! ***\n");
}

void loop() {
  // CRITICAL: Keep TX FIFO filled for RX to work!
  // SPI is full-duplex - must send to receive
  while (spi_is_writable(SPI_INST)) {
    spi_get_hw(SPI_INST)->dr = 0x00;  // Send dummy byte
  }
  
  // Check if there's data in RX FIFO
  if (spi_is_readable(SPI_INST)) {
    // Read byte from FIFO
    uint8_t data = spi_get_hw(SPI_INST)->dr;
    
    Serial.print("📥 Received: 0x");
    if (data < 0x10) Serial.print("0");
    Serial.print(data, HEX);
    Serial.print(" (");
    Serial.print(data);
    Serial.print(") '");
    if (data >= 32 && data < 127) {
      Serial.print((char)data);
    } else {
      Serial.print(".");
    }
    Serial.println("'");
  }
  
  // Periodic status
  static unsigned long last_status = 0;
  if (millis() - last_status > 5000) {
    Serial.println("[Status] Waiting for SPI data...");
    last_status = millis();
  }
}

