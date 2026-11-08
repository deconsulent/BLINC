// firmware/arduino_micro_bms/arduino_micro_bms.ino
// SecondPulse V2: Physical Analog ADC Front-End and Relay Actuator
// Target: Arduino Micro (ATmega32U4, 5.0V Logic, 10-bit ADC)
// Emits real-time JSON telemetry at 115200 baud over native USB Serial.

#include <Arduino.h>

// Analog inputs
const uint8_t PIN_BATT_12V = A0;  // 12V Car Jump Starter via 4:1 voltage divider
const uint8_t PIN_BATT_5V  = A1;  // 5V Mobile Power Bank via 2:1 voltage divider

// Digital relay outputs
const uint8_t PIN_RELAY_LOAD_12V = 4; // Relay 1: switches load across 12V battery
const uint8_t PIN_RELAY_LOAD_5V  = 5; // Relay 2: switches load across 5V battery
const uint8_t PIN_RELAY_ROUTER   = 6; // Relay 3: routes healthiest pack to Hub Output

// Hardware divider scaling factors
// Channel A: R1 = 30k, R2 = 10k -> (30 + 10) / 10 = 4.00
const float SCALE_12V = 4.00f;
// Channel B: R3 = 10k, R4 = 10k -> (10 + 10) / 10 = 2.00
const float SCALE_5V  = 2.00f;

// ADC reference: 5.0V on ATmega32U4
const float V_REF = 5.00f;
const float ADC_STEPS = 1023.0f;

// Active pulse timing state
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
  Serial.begin(115200);

  pinMode(PIN_RELAY_LOAD_12V, OUTPUT);
  pinMode(PIN_RELAY_LOAD_5V, OUTPUT);
  pinMode(PIN_RELAY_ROUTER, OUTPUT);

  // Default relays off (assuming active HIGH or adjust for active LOW modules)
  digitalWrite(PIN_RELAY_LOAD_12V, LOW);
  digitalWrite(PIN_RELAY_LOAD_5V, LOW);
  digitalWrite(PIN_RELAY_ROUTER, LOW);
}

void loop() {
  unsigned long now = millis();

  // Handle pulse load duration timeouts
  if (loadActive12V && now >= pulseEndTime12V) {
    loadActive12V = false;
    digitalWrite(PIN_RELAY_LOAD_12V, LOW);
  }

  if (loadActive5V && now >= pulseEndTime5V) {
    loadActive5V = false;
    digitalWrite(PIN_RELAY_LOAD_5V, LOW);
  }

  // Parse incoming serial commands from Raspberry Pi
  if (Serial.available() > 0) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();

    if (cmd == "LOAD12V_PULSE") {
      loadActive12V = true;
      digitalWrite(PIN_RELAY_LOAD_12V, HIGH);
      pulseEndTime12V = now + 1000; // 1 second pulse test
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

  // Emit JSON telemetry stream
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

