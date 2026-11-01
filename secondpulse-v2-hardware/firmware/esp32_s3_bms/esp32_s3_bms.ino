// firmware/esp32_s3_bms/esp32_s3_bms.ino
// SecondPulse V2: ESP32-S3 WROOM-1U Hardware ADC & Battery Triage Server
// Features:
// 1. High-speed 12-bit ADC reading for 12V and 5V battery rails
// 2. Dual-mode output: Native USB Serial (115200 baud) AND Local Wi-Fi Web Server / AP
// 3. Active pulse-load control & Smart Relay Router

#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>

// ADC Pins (ESP32-S3 ADC1 pins: GPIO 1 to GPIO 10 are safe and unshared)
const uint8_t PIN_BATT_12V = 1;  // GPIO 1 (ADC1_CH0): 12V Battery via 5:1 divider
const uint8_t PIN_BATT_5V  = 2;  // GPIO 2 (ADC1_CH1): 5V Battery via 2:1 divider

// Digital Relay / Switching Pins
const uint8_t PIN_RELAY_LOAD_12V = 4;  // GPIO 4: 12V Load switch
const uint8_t PIN_RELAY_LOAD_5V  = 5;  // GPIO 5: 5V Load switch
const uint8_t PIN_RELAY_ROUTER   = 6;  // GPIO 6: Output Power Router

// Voltage divider multipliers for 3.3V reference
// Channel A (12V Battery, max 16.8V): R1=47kΩ, R2=10kΩ -> (47+10)/10 = 5.70
const float SCALE_12V = 5.70f;
// Channel B (5V Battery, max 5.5V): R3=10kΩ, R4=10kΩ -> (10+10)/10 = 2.00
const float SCALE_5V  = 2.00f;

const float V_REF = 3.30f;
const float ADC_STEPS = 4095.0f;

unsigned long pulseEndTime12V = 0;
unsigned long pulseEndTime5V = 0;
bool loadActive12V = false;
bool loadActive5V = false;
bool routerActive = false;

unsigned long lastTelemetry = 0;
const unsigned long TELEMETRY_INTERVAL_MS = 200;

float v12_current = 0.0f;
float v5_current  = 0.0f;

// Optional: ESP32-S3 can run its own local Access Point for zero-dependency pitches!
WebServer server(80);

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

void handleStatus() {
  String json = "{\"v12\":" + String(v12_current, 3) +
                ",\"v5\":" + String(v5_current, 3) +
                ",\"load12\":" + (loadActive12V ? "true" : "false") +
                ",\"load5\":" + (loadActive5V ? "true" : "false") +
                ",\"router\":" + (routerActive ? "true" : "false") + "}";
  server.send(200, "application/json", json);
}

void setup() {
  Serial.begin(115200);

  // Configure 12-bit ADC and 11dB attenuation (~0 - 3.1V range)
  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);

  pinMode(PIN_RELAY_LOAD_12V, OUTPUT);
  pinMode(PIN_RELAY_LOAD_5V, OUTPUT);
  pinMode(PIN_RELAY_ROUTER, OUTPUT);

  digitalWrite(PIN_RELAY_LOAD_12V, LOW);
  digitalWrite(PIN_RELAY_LOAD_5V, LOW);
  digitalWrite(PIN_RELAY_ROUTER, LOW);

  // Optional: Start soft AP so the jury or laptop can connect directly over Wi-Fi
  WiFi.softAP("SecondPulse-Gateway", "theailab");
  server.on("/status", handleStatus);
  server.begin();
}

void loop() {
  unsigned long now = millis();
  server.handleClient();

  if (loadActive12V && now >= pulseEndTime12V) {
    loadActive12V = false;
    digitalWrite(PIN_RELAY_LOAD_12V, LOW);
  }

  if (loadActive5V && now >= pulseEndTime5V) {
    loadActive5V = false;
    digitalWrite(PIN_RELAY_LOAD_5V, LOW);
  }

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

  if (now - lastTelemetry >= TELEMETRY_INTERVAL_MS) {
    lastTelemetry = now;
    v12_current = readVoltage(PIN_BATT_12V, SCALE_12V);
    v5_current  = readVoltage(PIN_BATT_5V, SCALE_5V);

    Serial.print("{\"ts\":");
    Serial.print(now);
    Serial.print(",\"v12\":");
    Serial.print(v12_current, 3);
    Serial.print(",\"v5\":");
    Serial.print(v5_current, 3);
    Serial.print(",\"load12\":");
    Serial.print(loadActive12V ? "true" : "false");
    Serial.print(",\"load5\":");
    Serial.print(loadActive5V ? "true" : "false");
    Serial.print(",\"router\":");
    Serial.print(routerActive ? "true" : "false");
    Serial.println("}");
  }
}

