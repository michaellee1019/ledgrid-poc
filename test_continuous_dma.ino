// Continuous DMA RX with manual TX FIFO management
// No CS interrupt - simpler approach

#include <Arduino.h>
#include "hardware/spi.h"
#include "hardware/gpio.h"
#include "hardware/dma.h"

#define SPI_INST spi1
#define SPI_MOSI_PIN 12
#define SPI_CS_PIN 13
#define SPI_SCK_PIN 14
#define SPI_MISO_PIN 15
#define SPI_BAUDRATE (10 * 1000 * 1000)

#define DMA_BUFFER_SIZE 512
uint8_t dma_buffer[DMA_BUFFER_SIZE] __attribute__((aligned(4)));
int dma_rx_channel;
volatile uint16_t last_write_addr = 0;

void setup() {
  Serial.begin(115200);
  delay(2000);
  
  Serial.println("\n\n========================================");
  Serial.println("Continuous DMA Test (Mode 3)");
  Serial.println("========================================\n");
  
  // Initialize SPI1 in SLAVE mode with Mode 3
  spi_init(SPI_INST, SPI_BAUDRATE);
  spi_set_slave(SPI_INST, true);
  spi_set_format(SPI_INST, 8, SPI_CPOL_1, SPI_CPHA_1, SPI_MSB_FIRST);
  
  // Set up SPI pins
  gpio_set_function(SPI_MOSI_PIN, GPIO_FUNC_SPI);
  gpio_set_function(SPI_SCK_PIN, GPIO_FUNC_SPI);
  gpio_set_function(SPI_MISO_PIN, GPIO_FUNC_SPI);
  
  // CS as GPIO input
  gpio_init(SPI_CS_PIN);
  gpio_set_dir(SPI_CS_PIN, GPIO_IN);
  gpio_pull_up(SPI_CS_PIN);
  
  Serial.println("✓ SPI1 configured as slave (Mode 3)");
  
  // Clear buffer
  memset(dma_buffer, 0, DMA_BUFFER_SIZE);
  
  // Set up RX DMA in circular mode
  dma_rx_channel = dma_claim_unused_channel(true);
  dma_channel_config rx_config = dma_channel_get_default_config(dma_rx_channel);
  channel_config_set_transfer_data_size(&rx_config, DMA_SIZE_8);
  channel_config_set_read_increment(&rx_config, false);  // Read from SPI FIFO
  channel_config_set_write_increment(&rx_config, true);  // Write to buffer
  channel_config_set_ring(&rx_config, true, 9);  // 2^9 = 512 byte ring on write
  channel_config_set_dreq(&rx_config, spi_get_dreq(SPI_INST, false));  // RX DREQ
  
  dma_channel_configure(
    dma_rx_channel,
    &rx_config,
    dma_buffer,
    &spi_get_hw(SPI_INST)->dr,
    DMA_BUFFER_SIZE,
    true  // Start now
  );
  
  Serial.println("✓ RX DMA running in circular mode");
  Serial.println("\n*** Send data from Raspberry Pi! ***\n");
}

void loop() {
  // CRITICAL: Keep TX FIFO filled at all times!
  while (spi_is_writable(SPI_INST)) {
    spi_get_hw(SPI_INST)->dr = 0x00;
  }
  
  // Check current DMA write address
  uint16_t current_addr = DMA_BUFFER_SIZE - dma_channel_hw_addr(dma_rx_channel)->transfer_count;
  
  if (current_addr != last_write_addr) {
    // New data received!
    uint16_t start = last_write_addr;
    uint16_t end = current_addr;
    
    if (end > start) {
      // No wrap
      for (uint16_t i = start; i < end; i++) {
        if (dma_buffer[i] != 0x00) {  // Only print non-zero
          Serial.print("📥 [");
          Serial.print(i);
          Serial.print("] 0x");
          if (dma_buffer[i] < 0x10) Serial.print("0");
          Serial.println(dma_buffer[i], HEX);
        }
      }
    }
    
    last_write_addr = current_addr;
  }
  
  // Periodic status
  static unsigned long last_status = 0;
  if (millis() - last_status > 5000) {
    Serial.print("[Status] DMA write addr: ");
    Serial.print(current_addr);
    Serial.print(" / ");
    Serial.println(DMA_BUFFER_SIZE);
    last_status = millis();
  }
}

