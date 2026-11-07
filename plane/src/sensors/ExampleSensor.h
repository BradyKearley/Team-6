// Example sensor: uptime and Heltec V3 battery voltage.
// Copy this file as a template for new sensors (see Sensor.h).
//
// Battery: VBAT -> 390k/100k divider -> GPIO1 (ADC1_CH0). The divider is
// switched on by GPIO37 (ADC_Ctrl) to avoid draining the battery. On V3.2
// boards ADC_Ctrl is active HIGH; on V3.0/V3.1 it is active LOW. Override
// with -DVBAT_CTRL_ACTIVE=LOW if you have an older board.
// With no battery attached the reading is meaningless (floats near 0-4.2 V).
#pragma once

#include <Arduino.h>

#include "Sensor.h"

#ifndef VBAT_ADC_PIN
#define VBAT_ADC_PIN 1
#endif
#ifndef VBAT_CTRL_PIN
#define VBAT_CTRL_PIN 37
#endif
#ifndef VBAT_CTRL_ACTIVE
#define VBAT_CTRL_ACTIVE HIGH
#endif
#ifndef VBAT_DIVIDER
#define VBAT_DIVIDER 4.9f  // (390k + 100k) / 100k
#endif

class ExampleSensor : public Sensor {
 public:
  const char *name() const override { return "example"; }

  bool begin() override {
    pinMode(VBAT_CTRL_PIN, OUTPUT);
    digitalWrite(VBAT_CTRL_PIN, !VBAT_CTRL_ACTIVE);
    analogReadResolution(12);
    analogSetPinAttenuation(VBAT_ADC_PIN, ADC_11db);
    return true;
  }

  void update() override {
    // Battery changes slowly: sample once a second.
    uint32_t now = millis();
    if (lastRead_ != 0 && now - lastRead_ < 1000) return;
    lastRead_ = now;
    digitalWrite(VBAT_CTRL_PIN, VBAT_CTRL_ACTIVE);
    delayMicroseconds(200);  // let the divider settle
    uint32_t mv = analogReadMilliVolts(VBAT_ADC_PIN);
    digitalWrite(VBAT_CTRL_PIN, !VBAT_CTRL_ACTIVE);
    float v = mv * VBAT_DIVIDER / 1000.0f;
    if (vbat_ == 0) vbat_ = v;            // first reading
    else vbat_ += 0.2f * (v - vbat_);     // light smoothing
  }

  void writeJson(JsonObject &out) override {
    out["uptime_s"] = millis() / 1000;
    out["vbat"] = serialized(String(vbat_, 2));
  }

 private:
  uint32_t lastRead_ = 0;
  float vbat_ = 0;
};
