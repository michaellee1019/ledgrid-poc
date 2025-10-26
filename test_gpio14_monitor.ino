// Simple GPIO 14 monitor - continuously reads and reports state
// Upload this to SCORPIO, then run the Python script on Pi

#include <Arduino.h>

#define SCK_PIN 14

void setup() {
  Serial.begin(115200);
  delay(2000);
  
  Serial.println("\n\n========================================");
  Serial.println("SCORPIO GPIO 14 Monitor");
  Serial.println("========================================");
  Serial.println("Monitoring GPIO 14 continuously");
  Serial.println("Run test_direct_gpio.py on Raspberry Pi");
  Serial.println("GPIO 14 should toggle 0→1→0→1...");
  Serial.println("========================================\n");
  
  // Configure GPIO 14 as INPUT with pull-down
  pinMode(SCK_PIN, INPUT_PULLDOWN);
  
  Serial.println("GPIO 14 configured as INPUT_PULLDOWN");
  Serial.println("Starting monitor...\n");
}

void loop() {
  static uint8_t last_value = 255;  // Invalid initial value
  static unsigned long last_change = 0;
  static int stable_count = 0;
  
  uint8_t current_value = digitalRead(SCK_PIN);
  
  // Print on every change
  if (current_value != last_value && last_value != 255) {
    unsigned long now = millis();
    unsigned long duration = now - last_change;
    
    Serial.print("*** GPIO 14 CHANGED: ");
    Serial.print(last_value);
    Serial.print(" → ");
    Serial.print(current_value);
    Serial.print(" (was ");
    Serial.print(duration);
    Serial.println(" ms)");
    
    last_change = now;
    stable_count = 0;
  }
  
  if (current_value != last_value) {
    last_value = current_value;
  }
  
  // Print periodic status every 5 seconds
  stable_count++;
  if (stable_count >= 500) {  // 500 * 10ms = 5 seconds
    Serial.print("[Status] GPIO 14 = ");
    Serial.print(current_value);
    Serial.println(" (no change for 5+ seconds)");
    stable_count = 0;
  }
  
  delay(10);
}

