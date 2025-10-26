// Simple wiring test for SCORPIO
// Upload this, then wiggle wires on the Pi side to verify connections

#include <Arduino.h>

#define MOSI_PIN 12
#define CS_PIN 13
#define SCK_PIN 14

void setup() {
  Serial.begin(115200);
  delay(2000);
  
  Serial.println("\n\n========================================");
  Serial.println("SCORPIO Wiring Verification Test");
  Serial.println("========================================");
  Serial.println("This will monitor GPIO pins as inputs");
  Serial.println("Wiggle wires on Pi to verify connections");
  Serial.println("========================================\n");
  
  // Configure all pins as inputs with pull-downs
  pinMode(MOSI_PIN, INPUT_PULLDOWN);
  pinMode(CS_PIN, INPUT_PULLUP);    // CS should be pulled HIGH when idle
  pinMode(SCK_PIN, INPUT_PULLDOWN);
  
  Serial.println("Pin configuration:");
  Serial.println("  GPIO 12 (MOSI): Input with pull-down");
  Serial.println("  GPIO 13 (CS):   Input with pull-up");
  Serial.println("  GPIO 14 (SCK):  Input with pull-down");
  Serial.println("\nExpected wiring:");
  Serial.println("  RPi GPIO 10 (MOSI) → SCORPIO GPIO 12");
  Serial.println("  RPi GPIO 8  (CE0)  → SCORPIO GPIO 13");
  Serial.println("  RPi GPIO 11 (SCLK) → SCORPIO GPIO 14");
  Serial.println("  RPi GND → SCORPIO GND");
  Serial.println("\n========================================\n");
}

void loop() {
  static uint8_t last_mosi = 2;
  static uint8_t last_cs = 2;
  static uint8_t last_sck = 2;
  
  uint8_t mosi = digitalRead(MOSI_PIN);
  uint8_t cs = digitalRead(CS_PIN);
  uint8_t sck = digitalRead(SCK_PIN);
  
  // Print on change
  if (mosi != last_mosi || cs != last_cs || sck != last_sck) {
    Serial.print("Pins: MOSI=");
    Serial.print(mosi);
    Serial.print(" CS=");
    Serial.print(cs);
    Serial.print(" SCK=");
    Serial.println(sck);
    
    last_mosi = mosi;
    last_cs = cs;
    last_sck = sck;
  }
  
  // Also print periodic status
  static unsigned long last_print = 0;
  if (millis() - last_print > 2000) {
    Serial.print("Current state: MOSI(12)=");
    Serial.print(mosi);
    Serial.print(" CS(13)=");
    Serial.print(cs);
    Serial.print(" SCK(14)=");
    Serial.println(sck);
    last_print = millis();
  }
  
  delay(10);
}

