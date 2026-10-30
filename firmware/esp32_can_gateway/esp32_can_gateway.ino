// firmware/esp32_can_gateway/esp32_can_gateway.ino
// Climathon 2026 Track 3: SecondPulse gateway state machine demo.
//
// WHAT THIS IS: a simulation. It does not talk to any CAN hardware. It does not read a
// real battery and it does not talk to a real inverter. It runs the gateway logic
// (precharge hold, isolation test masking, current limits) against made up pack values
// and prints the result as JSON over USB serial at 115200 baud, twice per second.
//
// The four inverter side frames are built as real 8 byte little endian payloads using
// the same layout as backend/main.py, so the printed bytes can be compared with the API.
//
// Runs on a plain ESP32 DevKit. GPIO 25 and 26 drive the two contactor outputs and GPIO 2
// is the onboard LED. With nothing connected to 25 and 26 the outputs just toggle.
//
// Serial commands (one character, no newline needed):
//   e  toggle the simulated inverter enable line (stops the automatic cycle)
//   i  start an isolation test pulse now
//   a  go back to the automatic cycle
//
// Automatic cycle: every 10 s a 1.2 s isolation pulse, every 30 s the enable line drops
// for 2 s so the 1500 ms precharge hold can be seen again.

#include <Arduino.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

constexpr uint8_t PIN_CONTACTOR_PRECHARGE = 25;
constexpr uint8_t PIN_CONTACTOR_MAIN = 26;
constexpr uint8_t PIN_STATUS_LED = 2;

constexpr uint32_t PRECHARGE_HOLD_MS = 1500;
constexpr uint32_t REPORT_PERIOD_MS = 500;
constexpr uint32_t ISO_PERIOD_MS = 10000;
constexpr uint32_t ISO_PULSE_MS = 1200;
constexpr uint32_t ENABLE_DROP_PERIOD_MS = 30000;
constexpr uint32_t ENABLE_DROP_MS = 2000;

// Simulated Nissan Leaf ZE1 pack, same numbers as the backend registry
constexpr float NOMINAL_KWH = 40.0f;
constexpr float NOMINAL_V = 350.0f;
constexpr float MAX_C_RATE = 0.20f;
constexpr int CELLS_SERIES = 96;
constexpr float PACK_V_BASE = 364.8f;
constexpr float PACK_SOC_PCT = 78.0f;
constexpr float PACK_SOH_PCT = 81.2f;
constexpr float PACK_TEMP_C = 24.2f;
constexpr uint16_t CELL_MIN_MV = 3785;
constexpr uint16_t CELL_MAX_MV = 3802;

enum GatewayState { STATE_DISABLED, STATE_PRECHARGE, STATE_RUN };

GatewayState state = STATE_DISABLED;
bool enableHigh = true;
bool autoCycle = true;
bool isoActive = false;
uint32_t enableChangedMs = 0;
uint32_t isoStartMs = 0;
uint32_t isoEndMs = 0;
uint32_t lastReportMs = 0;

static void put16(uint8_t *b, int i, uint16_t v) {
  b[i] = v & 0xFF;
  b[i + 1] = v >> 8;
}

static uint16_t toU16(float v) {
  if (v < 0.0f) return 0;
  if (v > 65535.0f) return 65535;
  return (uint16_t)lroundf(v);
}

static void hex8(const uint8_t *b, char *out) {
  for (int i = 0; i < 8; i++) {
    snprintf(out + i * 3, 4, "%02X%s", b[i], i < 7 ? " " : "");
  }
}

static void setEnable(bool high, uint32_t now) {
  if (high == enableHigh) return;
  enableHigh = high;
  enableChangedMs = now;
  state = high ? STATE_PRECHARGE : STATE_DISABLED;
}

static void startIsoPulse(uint32_t now) {
  isoActive = true;
  isoStartMs = now;
}

static void handleSerial(uint32_t now) {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == 'e') {
      autoCycle = false;
      setEnable(!enableHigh, now);
    } else if (c == 'i') {
      startIsoPulse(now);
    } else if (c == 'a') {
      autoCycle = true;
      isoEndMs = now;
      setEnable(true, now);
    }
  }
}

static void updateAutoCycle(uint32_t now) {
  if (isoActive && (uint32_t)(now - isoStartMs) >= ISO_PULSE_MS) {
    isoActive = false;
    isoEndMs = now;
  }
  if (!autoCycle) return;
  if (!isoActive && (uint32_t)(now - isoEndMs) >= ISO_PERIOD_MS) startIsoPulse(now);
  if (enableHigh && state == STATE_RUN && (uint32_t)(now - enableChangedMs) >= ENABLE_DROP_PERIOD_MS) {
    setEnable(false, now);
  } else if (!enableHigh && (uint32_t)(now - enableChangedMs) >= ENABLE_DROP_MS) {
    setEnable(true, now);
  }
}

static void updateStateMachine(uint32_t now) {
  if (state == STATE_PRECHARGE && (uint32_t)(now - enableChangedMs) >= PRECHARGE_HOLD_MS) {
    state = STATE_RUN;
  }
  digitalWrite(PIN_CONTACTOR_PRECHARGE, state == STATE_PRECHARGE ? HIGH : LOW);
  digitalWrite(PIN_CONTACTOR_MAIN, state == STATE_RUN ? HIGH : LOW);

  if (state == STATE_RUN) {
    digitalWrite(PIN_STATUS_LED, HIGH);
  } else if (state == STATE_PRECHARGE) {
    digitalWrite(PIN_STATUS_LED, (now / 200) % 2);
  } else {
    digitalWrite(PIN_STATUS_LED, LOW);
  }
}

static void report(uint32_t now) {
  bool txActive = (state == STATE_RUN);
  float packV = PACK_V_BASE + sinf((float)now / 5000.0f) * 0.4f;
  float limitA = roundf(NOMINAL_KWH * 1000.0f / NOMINAL_V * MAX_C_RATE * 10.0f) / 10.0f;
  float chargeA = txActive ? limitA : 0.0f;
  float dischargeA = txActive ? limitA : 0.0f;

  uint8_t f351[8], f355[8], f356[8], f359[8];
  put16(f351, 0, toU16(roundf(CELLS_SERIES * 4.12f * 10.0f)));
  put16(f351, 2, toU16(chargeA * 10.0f));
  put16(f351, 4, toU16(dischargeA * 10.0f));
  put16(f351, 6, toU16(roundf(CELLS_SERIES * 3.20f * 10.0f)));
  memset(f355, 0, 8);
  put16(f355, 0, toU16(PACK_SOC_PCT));
  put16(f355, 2, toU16(PACK_SOH_PCT));
  memset(f356, 0, 8);
  put16(f356, 0, toU16(packV * 100.0f));
  put16(f356, 4, (uint16_t)(int16_t)lroundf(PACK_TEMP_C * 10.0f));
  const uint8_t f359Bytes[8] = {0, 0, 0, 0, 1, 0x50, 0x4E, 0};
  memcpy(f359, f359Bytes, 8);

  char h351[24], h355[24], h356[24], h359[24];
  hex8(f351, h351);
  hex8(f355, h355);
  hex8(f356, h356);
  hex8(f359, h359);

  const char *stateName = state == STATE_RUN ? "RUN" : (state == STATE_PRECHARGE ? "PRECHARGE" : "DISABLED");
  char line[640];
  snprintf(line, sizeof(line),
           "{\"uptime_ms\":%lu,\"state\":\"%s\",\"enable_high\":%s,\"contactor_closed\":%s,"
           "\"can_tx_active\":%s,\"iso_pulse_active\":%s,\"iso_610h_masked\":%s,"
           "\"rx_ev_can\":{\"id\":\"0x1DB\",\"v_pack\":%.1f,\"soc\":%.1f,\"soh\":%.1f,\"delta_mv\":%u},"
           "\"tx_inverter_can\":{\"i_chg_max_a\":%.1f,\"i_dis_max_a\":%.1f,"
           "\"frames\":{\"0x351\":\"%s\",\"0x355\":\"%s\",\"0x356\":\"%s\",\"0x359\":\"%s\"}}}",
           (unsigned long)now, stateName, enableHigh ? "true" : "false", txActive ? "true" : "false",
           txActive ? "true" : "false", isoActive ? "true" : "false", isoActive ? "true" : "false",
           packV, PACK_SOC_PCT, PACK_SOH_PCT, (unsigned)(CELL_MAX_MV - CELL_MIN_MV),
           chargeA, dischargeA, h351, h355, h356, h359);
  Serial.println(line);
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_CONTACTOR_PRECHARGE, OUTPUT);
  pinMode(PIN_CONTACTOR_MAIN, OUTPUT);
  pinMode(PIN_STATUS_LED, OUTPUT);
  digitalWrite(PIN_CONTACTOR_PRECHARGE, LOW);
  digitalWrite(PIN_CONTACTOR_MAIN, LOW);

  uint32_t now = millis();
  enableHigh = true;
  enableChangedMs = now;
  isoEndMs = now;
  state = STATE_PRECHARGE;
  lastReportMs = now;

  Serial.println("{\"event\":\"boot\",\"firmware\":\"secondpulse_sim_v1.1\",\"mode\":\"simulation, no CAN hardware\"}");
}

void loop() {
  uint32_t now = millis();
  handleSerial(now);
  updateAutoCycle(now);
  updateStateMachine(now);
  if ((uint32_t)(now - lastReportMs) >= REPORT_PERIOD_MS) {
    lastReportMs = now;
    report(now);
  }
}
