#ifdef RUN_GPIO_DIRECT_TEST

// Direct GPIO test - NO SPI peripheral
// This will tell us if signals are physically arriving at ESP32

#include <Arduino.h>

// Expected pins based on wiring
#define TEST_CS_PIN    2   // D1 - should see CS toggle
#define TEST_SCK_PIN   7   // D8 - should see clock
#define TEST_MOSI_PIN  9   // D10 - should see data

volatile uint32_t cs_toggles = 0;
volatile uint32_t sck_toggles = 0;
volatile uint32_t mosi_changes = 0;

void IRAM_ATTR cs_isr() {
  cs_toggles++;
}

void IRAM_ATTR sck_isr() {
  sck_toggles++;
}

void IRAM_ATTR mosi_isr() {
  mosi_changes++;
}

void setup() {
  Serial.begin(115200);
  delay(2000);
  while (!Serial && millis() < 5000) {
    delay(100);
  }
  
  Serial.println("\n\n========================================");
  Serial.println("ESP32 GPIO Direct Signal Test");
  Serial.println("Testing if SPI signals physically arrive");
  Serial.println("========================================");
  Serial.printf("CS pin:   GPIO%d (D1)\n", TEST_CS_PIN);
  Serial.printf("SCK pin:  GPIO%d (D8)\n", TEST_SCK_PIN);
  Serial.printf("MOSI pin: GPIO%d (D10)\n", TEST_MOSI_PIN);
  Serial.println("========================================\n");
  
  // Configure as inputs with interrupts
  pinMode(TEST_CS_PIN, INPUT_PULLUP);
  pinMode(TEST_SCK_PIN, INPUT_PULLUP);
  pinMode(TEST_MOSI_PIN, INPUT_PULLUP);
  
  attachInterrupt(digitalPinToInterrupt(TEST_CS_PIN), cs_isr, CHANGE);
  attachInterrupt(digitalPinToInterrupt(TEST_SCK_PIN), sck_isr, CHANGE);
  attachInterrupt(digitalPinToInterrupt(TEST_MOSI_PIN), mosi_isr, CHANGE);
  
  Serial.println("✓ GPIO interrupts configured");
  Serial.println("\n*** Waiting for signals from Raspberry Pi ***");
  Serial.println("Run test_esp32_simple.py on the RPi now!\n");
  
  // Show initial pin states
  Serial.printf("Initial states: CS=%d SCK=%d MOSI=%d\n\n",
                digitalRead(TEST_CS_PIN),
                digitalRead(TEST_SCK_PIN),
                digitalRead(TEST_MOSI_PIN));
}

void loop() {
  static uint32_t last_print = 0;
  static uint32_t last_cs = 0;
  static uint32_t last_sck = 0;
  static uint32_t last_mosi = 0;
  
  // Print status every 2 seconds
  if (millis() - last_print > 2000) {
    // Read current states
    int cs_state = digitalRead(TEST_CS_PIN);
    int sck_state = digitalRead(TEST_SCK_PIN);
    int mosi_state = digitalRead(TEST_MOSI_PIN);
    
    Serial.printf("Pin States: CS=%d SCK=%d MOSI=%d | ", cs_state, sck_state, mosi_state);
    Serial.printf("Toggles: CS=%u SCK=%u MOSI=%u\n", 
                  cs_toggles, sck_toggles, mosi_changes);
    
    // Check if anything changed
    if (cs_toggles != last_cs) {
      Serial.println("  ✓ CS is toggling! (Good - RPi is sending)");
      last_cs = cs_toggles;
    }
    if (sck_toggles != last_sck) {
      Serial.println("  ✓ SCK is toggling! (Good - clock is working)");
      last_sck = sck_toggles;
    }
    if (mosi_changes != last_mosi) {
      Serial.println("  ✓ MOSI is changing! (Good - data is arriving)");
      last_mosi = mosi_changes;
    }
    
    if (cs_toggles == 0 && sck_toggles == 0 && mosi_changes == 0) {
      Serial.println("  ✗ NO SIGNALS DETECTED - Check wiring/RPi sending");
    }
    
    last_print = millis();
  }
}

#endif  // RUN_GPIO_DIRECT_TEST