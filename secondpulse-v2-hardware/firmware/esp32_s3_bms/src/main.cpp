// firmware/esp32_s3_bms/src/main.cpp
// DFRobot FireBeetle 2 ESP32-S3 Hardware ADC & Battery Triage Server
// Noise-gated true physical voltage readings with pulldown baseline.

#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>

// ADC Pins mapped specifically to DFRobot FireBeetle 2 ESP32-S3:
// A0 on silkscreen = GPIO 4 (Channel A: 12V Battery via DFR0051)
// A1 on silkscreen = GPIO 5 (Channel B: 5V Battery via DFR0051)
const uint8_t PIN_BATT_12V = 4;
const uint8_t PIN_BATT_5V  = 5;

// Digital Relay Pins
const uint8_t PIN_RELAY_LOAD_12V = 6;
const uint8_t PIN_RELAY_LOAD_5V  = 8;
const uint8_t PIN_RELAY_ROUTER   = 10;

// DFRobot DFR0051 has exact 5.00:1 ratio
const float SCALE_RATIO = 5.00f;
const float V_REF = 3.30f;
const float ADC_STEPS = 4095.0f;

// Noise cutoff threshold: 0.15V pin voltage * 5.0 = 0.75V
// Below this, the channel is completely disconnected (floating static)
const float DISCONNECTED_THRESHOLD_V = 0.75f;

unsigned long pulseEndTime12V = 0;
unsigned long pulseEndTime5V = 0;
bool loadActive12V = false;
bool loadActive5V = false;
bool routerActive = false;

unsigned long lastTelemetry = 0;
const unsigned long TELEMETRY_INTERVAL_MS = 200;

float v12_current = 0.0f;
float v5_current  = 0.0f;

WebServer server(80);

float readVoltage(uint8_t pin, uint8_t samples = 32) {
  uint32_t rawSum = 0;
  for (uint8_t i = 0; i < samples; i++) {
    rawSum += analogRead(pin);
    delayMicroseconds(30);
  }
  float avgRaw = (float)rawSum / (float)samples;
  float pinVoltage = (avgRaw * V_REF) / ADC_STEPS;
  float scaledVoltage = pinVoltage * SCALE_RATIO;

  // Zero out floating noise when no battery is physically plugged in
  if (scaledVoltage < DISCONNECTED_THRESHOLD_V) {
    return 0.0f;
  }
  return scaledVoltage;
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

  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);

  pinMode(PIN_RELAY_LOAD_12V, OUTPUT);
  pinMode(PIN_RELAY_LOAD_5V, OUTPUT);
  pinMode(PIN_RELAY_ROUTER, OUTPUT);

  digitalWrite(PIN_RELAY_LOAD_12V, LOW);
  digitalWrite(PIN_RELAY_LOAD_5V, LOW);
  digitalWrite(PIN_RELAY_ROUTER, LOW);

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
    v12_current = readVoltage(PIN_BATT_12V);
    v5_current  = readVoltage(PIN_BATT_5V);

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
