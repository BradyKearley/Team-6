#include "display.h"

#include <Arduino.h>
#include <SSD1306Wire.h>

#include "ranging.h"

// Heltec V3 pins (the board variant defines them; fallbacks just in case).
#ifndef Vext
#define Vext 36  // LOW = power on the OLED (and the external 3V3 header)
#endif
#ifndef RST_OLED
#define RST_OLED 21
#endif
#ifndef SDA_OLED
#define SDA_OLED 17
#endif
#ifndef SCL_OLED
#define SCL_OLED 18
#endif

static SSD1306Wire oled(0x3c, SDA_OLED, SCL_OLED, GEOMETRY_128_64);

bool displayBegin() {
  pinMode(Vext, OUTPUT);
  digitalWrite(Vext, LOW);
  delay(50);

  pinMode(RST_OLED, OUTPUT);
  digitalWrite(RST_OLED, LOW);
  delay(20);
  digitalWrite(RST_OLED, HIGH);
  delay(20);

  if (!oled.init()) return false;
  oled.flipScreenVertically();
  oled.setFont(ArialMT_Plain_10);
  oled.clear();
  oled.drawString(0, 0, "PLANE-T6 starting...");
  oled.display();
  return true;
}

void displayUpdate(bool bleConnected) {
  oled.clear();
  oled.setTextAlignment(TEXT_ALIGN_LEFT);
  oled.drawString(0, 0, bleConnected ? "BLE: connected" : "BLE: advertising");
  oled.drawHorizontalLine(0, 12, 128);

  const TowerState *t = rangingTowers();
  int y = 14;
  int shown = 0;
  for (int i = 0; i < rangingMaxTowers() && y <= 54; i++) {
    if (!t[i].active) continue;
    char line[40];
    snprintf(line, sizeof(line), "%-4s %5.1fm %4.0fdB", t[i].name, t[i].distance, t[i].ema);
    oled.drawString(0, y, line);
    y += 10;
    shown++;
  }
  if (!shown) oled.drawString(0, 14, "no towers heard");
  oled.display();
}
