#!/usr/bin/env python3
"""
Test if the wires are physically working by manually toggling GPIO pins
This bypasses the SPI hardware to test raw connectivity
"""

try:
    import RPi.GPIO as GPIO
    import time
    
    print("=== Direct Wire Test (No SPI Hardware) ===\n")
    print("This will manually toggle the GPIO pins to test wire connectivity.")
    print("Watch the SCORPIO serial monitor for GPIO state changes.\n")
    
    # Set up GPIO
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    
    # SPI pins
    SCK_PIN = 11   # Physical pin 23 -> SCORPIO GPIO 14
    MOSI_PIN = 10  # Physical pin 19 -> SCORPIO GPIO 12
    CS_PIN = 8     # Physical pin 24 -> SCORPIO GPIO 13
    
    print("Configuring pins as outputs...")
    GPIO.setup(SCK_PIN, GPIO.OUT)
    GPIO.setup(MOSI_PIN, GPIO.OUT)
    GPIO.setup(CS_PIN, GPIO.OUT)
    
    # Initialize all LOW
    GPIO.output(SCK_PIN, GPIO.LOW)
    GPIO.output(MOSI_PIN, GPIO.LOW)
    GPIO.output(CS_PIN, GPIO.HIGH)  # CS is active LOW
    
    print("\n" + "="*60)
    print("Starting manual toggle test...")
    print("="*60 + "\n")
    
    print("Test 1: Toggle SCK (GPIO 11 -> SCORPIO GPIO 14)")
    print("  Watch SCORPIO serial monitor for 'SCK=' state changes")
    print("  If SCK doesn't change, the wire is faulty!\n")
    
    for i in range(10):
        GPIO.output(SCK_PIN, GPIO.HIGH)
        print(f"  [{i+1}] SCK -> HIGH", flush=True)
        time.sleep(0.5)
        GPIO.output(SCK_PIN, GPIO.LOW)
        print(f"  [{i+1}] SCK -> LOW", flush=True)
        time.sleep(0.5)
    
    print("\n" + "-"*60 + "\n")
    
    print("Test 2: Toggle MOSI (GPIO 10 -> SCORPIO GPIO 12)")
    print("  Watch SCORPIO serial monitor for 'MOSI=' state changes\n")
    
    for i in range(10):
        GPIO.output(MOSI_PIN, GPIO.HIGH)
        print(f"  [{i+1}] MOSI -> HIGH", flush=True)
        time.sleep(0.5)
        GPIO.output(MOSI_PIN, GPIO.LOW)
        print(f"  [{i+1}] MOSI -> LOW", flush=True)
        time.sleep(0.5)
    
    print("\n" + "-"*60 + "\n")
    
    print("Test 3: Toggle CS (GPIO 8 -> SCORPIO GPIO 13)")
    print("  Watch SCORPIO serial monitor for '[CS] Asserted/Released'\n")
    
    for i in range(10):
        GPIO.output(CS_PIN, GPIO.LOW)  # Assert (active LOW)
        print(f"  [{i+1}] CS -> LOW (asserted)", flush=True)
        time.sleep(0.5)
        GPIO.output(CS_PIN, GPIO.HIGH)  # Release
        print(f"  [{i+1}] CS -> HIGH (released)", flush=True)
        time.sleep(0.5)
    
    print("\n" + "="*60)
    print("\n✓ Test complete!")
    print("\nResults to check on SCORPIO serial monitor:")
    print("  - If SCK state never changed: SCK wire is faulty")
    print("  - If MOSI state never changed: MOSI wire is faulty")
    print("  - If no '[CS] Asserted' messages: CS wire is faulty")
    print("\nIf all wires show state changes, then the issue is")
    print("with SPI hardware configuration, not the wires.")
    
    # Clean up
    GPIO.cleanup()
    
except ImportError:
    print("✗ Error: RPi.GPIO not installed")
    print("  Install: sudo apt-get install python3-rpi.gpio")
except Exception as e:
    print(f"✗ Error: {e}")
    try:
        GPIO.cleanup()
    except:
        pass





