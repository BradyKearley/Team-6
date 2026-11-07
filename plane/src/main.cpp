// Plane: sniffs tower beacons, filters RSSI per tower, estimates distance,
// and every REPORT_PERIOD_MS emits one JSON line on USB serial and BLE:
//   {"t":ms,"towers":[{"n":"A","r":-61.2,"d":4.8,"s":0.9,"c":18}],"sensors":{...}}
//   n name, r filtered RSSI (dBm), d distance (m), s RSSI std-dev (dB),
//   c samples in the filter window
#include <Arduino.h>
#include <ArduinoJson.h>

#include "display.h"
#include "link_ble.h"
#include "ranging.h"
#include "tower_rx.h"

// ---- Sensors: to add one, #include it here and add it to SENSORS (see sensors/Sensor.h) ----
#include "sensors/ExampleSensor.h"

static ExampleSensor exampleSensor;
static Sensor *SENSORS[] = {&exampleSensor};
static const int SENSOR_COUNT = sizeof(SENSORS) / sizeof(SENSORS[0]);
static bool sensorOk[SENSOR_COUNT];  // false = begin() failed, skip this sensor
// ----------------------------------------------------------------------------------------------

#ifndef CHANNEL
#define CHANNEL 1
#endif
#ifndef REPORT_PERIOD_MS
#define REPORT_PERIOD_MS 200
#endif
#define DISPLAY_PERIOD_MS 250

static void sendReport(uint32_t now) {
  JsonDocument doc;
  doc["t"] = now;
  JsonArray towers = doc["towers"].to<JsonArray>();
  const TowerState *t = rangingTowers();
  for (int i = 0; i < rangingMaxTowers(); i++) {
    if (!t[i].active || !t[i].emaInit) continue;
    JsonObject o = towers.add<JsonObject>();
    o["n"] = t[i].name;
    o["r"] = serialized(String(t[i].ema, 1));  // serialized(): fixed decimals in the JSON
    o["d"] = serialized(String(t[i].distance, 2));
    o["s"] = serialized(String(t[i].stddev, 1));
    o["c"] = t[i].count;
  }
  JsonObject sensors = doc["sensors"].to<JsonObject>();
  for (int i = 0; i < SENSOR_COUNT; i++) {
    if (sensorOk[i]) SENSORS[i]->writeJson(sensors);
  }

  String line;
  serializeJson(doc, line);
  line += '\n';  // the server splits reports on newlines
  Serial.print(line);
  bleSendLine(line.c_str(), line.length());
}

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("[plane] boot");

  if (!displayBegin()) Serial.println("[plane] OLED init failed");
  for (int i = 0; i < SENSOR_COUNT; i++) {
    sensorOk[i] = SENSORS[i]->begin();
    Serial.printf("[sensor] %s: %s\n", SENSORS[i]->name(), sensorOk[i] ? "ok" : "FAILED, disabled");
  }
  // BLE first, then WiFi: both share the 2.4 GHz radio (coexistence).
  bleBegin();
  if (!towerRxBegin(CHANNEL)) Serial.println("[plane] WiFi promiscuous init FAILED");
  else Serial.printf("[plane] listening for towers on channel %d\n", CHANNEL);
}

void loop() {
  static uint32_t nextReport = 0;
  static uint32_t nextDisplay = 0;

  TowerSample s;
  while (towerRxPop(s)) rangingAddSample(s);

  uint32_t now = millis();
  rangingExpire(now);
  for (int i = 0; i < SENSOR_COUNT; i++) {
    if (sensorOk[i]) SENSORS[i]->update();
  }

  // (int32_t)(now - next) >= 0 is "now is past next", and still works when millis() wraps around.
  if ((int32_t)(now - nextReport) >= 0) {
    nextReport = now + REPORT_PERIOD_MS;
    sendReport(now);
  }
  if ((int32_t)(now - nextDisplay) >= 0) {
    nextDisplay = now + DISPLAY_PERIOD_MS;
    displayUpdate(bleConnected());
  }
  delay(2);
}
