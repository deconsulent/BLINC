// firmware/stm32_bms/stm32_bms.ino
// SecondPulse V2: STM32 Nucleo Physical Analog Front-End & Relay Router
// Target: STM32 Nucleo Board (ARM Cortex-M, 3.3V Logic, 12-bit ADC)
// Serial: ST-Link Virtual COM Port (COM17 on Windows or /dev/ttyACM0 on Linux) at 115200 baud.

#include <Arduino.h>

// Analog inputs (Arduino headers on Nucleo)
const uint8_t PIN_BATT_12V = A0;  // 12V Car Jump Starter via 5:1 divider (e.g. 40k + 10k or 47k + 10k)
const uint8_t PIN_BATT_5V  = A1;  // 5V Mobile Power Bank via 2:1 divider (e.g. 10k + 10k)

// Digital relay outputs
const uint8_t PIN_RELAY_LOAD_12V = D4; // Relay 1: switches load across 12V battery
const uint8_t PIN_RELAY_LOAD_5V  = D5; // Relay 2: switches load across 5V battery
const uint8_t PIN_RELAY_ROUTER   = D6; // Relay 3: routes healthiest pack to Hub Output
const uint8_t PIN_LED_STATUS     = LED_BUILTIN; // Built-in green LED (usually D13)

// HARDWARE VOLTAGE DIVIDER MULTIPLIERS FOR 3.3V STM32 ADC:
// Channel A (12V Battery, max ~16.8V):
// Recommended: R1 = 47kΩ, R2 = 10kΩ -> (47 + 10) / 10 = 5.70
// (Or 4:1 divider if battery is strictly <= 13.2V)
const float SCALE_12V = 5.70f;

// Channel B (5V Battery, max ~5.5V):
// Recommended: R3 = 10kΩ, R4 = 10kΩ -> (10 + 10) / 10 = 2.00
const float SCALE_5V = 2.00f;

// STM32 ADC reference: 3.30V, 12-bit resolution (4095 steps)
const float V_REF = 3.30f;
const float ADC_STEPS = 4095.0f;

unsigned long pulseEndTime12V = 0;
unsigned long pulseEndTime5V = 0;
bool loadActive12V = false;
bool loadActive5V = false;
bool routerActive = false;

unsigned long lastTelemetryTime = 0;
const unsigned long TELEMETRY_INTERVAL_MS = 200;

float readVoltage(uint8_t pin, float scaleMultiplier, uint8_t samples = 16) {
  uint32_t rawSum = 0;
  for (uint8_t i = 0; i < samples; i++) {
    rawSum += analogRead(pin);
    delayMicroseconds(50);
  }
  float avgRaw = (float)rawSum / (float)samples;
  float pinVoltage = (avgRaw * V_REF) / ADC_STEPS;
  return pinVoltage * scaleMultiplier;
}

void setup() {
  // Initialize ST-Link USB Virtual COM Port
  Serial.begin(115200);

  // Set 12-bit ADC resolution for STM32
  #if defined(analogReadResolution)
    analogReadResolution(12);
  #endif

  pinMode(PIN_RELAY_LOAD_12V, OUTPUT);
  pinMode(PIN_RELAY_LOAD_5V, OUTPUT);
  pinMode(PIN_RELAY_ROUTER, OUTPUT);
  pinMode(PIN_LED_STATUS, OUTPUT);

  // Default relays off
  digitalWrite(PIN_RELAY_LOAD_12V, LOW);
  digitalWrite(PIN_RELAY_LOAD_5V, LOW);
  digitalWrite(PIN_RELAY_ROUTER, LOW);
  digitalWrite(PIN_LED_STATUS, HIGH); // LED on when active
}

void loop() {
  unsigned long now = millis();

  // Pulse load timeouts
  if (loadActive12V && now >= pulseEndTime12V) {
    loadActive12V = false;
    digitalWrite(PIN_RELAY_LOAD_12V, LOW);
  }

  if (loadActive5V && now >= pulseEndTime5V) {
    loadActive5V = false;
    digitalWrite(PIN_RELAY_LOAD_5V, LOW);
  }

  // Parse serial commands from Daemon
  if (Serial.available() > 0) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();

    if (cmd == "LOAD12V_PULSE") {
      loadActive12V = true;
      digitalWrite(PIN_RELAY_LOAD_12V, HIGH);
      pulseEndTime12V = now + 1000;
    } else if (cmd == "LOAD5V_PULSE") {
      loadActive5V = true;
      digitalWrite(PIN_RELAY_LOAD_5V, HIGH);
      pulseEndTime5V = now + 1000;
    } else if (cmd == "LOAD12V_ON") {
      loadActive12V = true;
      digitalWrite(PIN_RELAY_LOAD_12V, HIGH);
    } else if (cmd == "LOAD12V_OFF") {
      loadActive12V = false;
      digitalWrite(PIN_RELAY_LOAD_12V, LOW);
    } else if (cmd == "LOAD5V_ON") {
      loadActive5V = true;
      digitalWrite(PIN_RELAY_LOAD_5V, HIGH);
    } else if (cmd == "LOAD5V_OFF") {
      loadActive5V = false;
      digitalWrite(PIN_RELAY_LOAD_5V, LOW);
    } else if (cmd == "ROUTE_ON") {
      routerActive = true;
      digitalWrite(PIN_RELAY_ROUTER, HIGH);
    } else if (cmd == "ROUTE_OFF") {
      routerActive = false;
      digitalWrite(PIN_RELAY_ROUTER, LOW);
    }
  }

  // Emit JSON telemetry
  if (now - lastTelemetryTime >= TELEMETRY_INTERVAL_MS) {
    lastTelemetryTime = now;

    float v12 = readVoltage(PIN_BATT_12V, SCALE_12V);
    float v5  = readVoltage(PIN_BATT_5V, SCALE_5V);

    Serial.print("{\"ts\":");
    Serial.print(now);
    Serial.print(",\"v12\":");
    Serial.print(v12, 3);
    Serial.print(",\"v5\":");
    Serial.print(v5, 3);
    Serial.print(",\"load12\":");
    Serial.print(loadActive12V ? "true" : "false");
    Serial.print(",\"load5\":");
    Serial.print(loadActive5V ? "true" : "false");
    Serial.print(",\"router\":");
    Serial.print(routerActive ? "true" : "false");
    Serial.println("}");
  }
}
