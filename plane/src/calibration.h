// Per-tower calibration used for the plane's own distance ("d" in the report
// and the OLED). The server recomputes distance from "r" with its own copy of
// these values, so the UI is unaffected by this table.
//
// After a calibration sweep in the UI, copy each tower's rssi1m / n here
// (or read them from server/towers.json) and reflash the plane.
// Towers not listed use RSSI_AT_1M / PATH_LOSS_N from platformio.ini.
#pragma once

struct TowerCal {
  const char *name;
  float rssi1m;  // filtered RSSI at 1 m (dBm)
  float n;       // path-loss exponent
};

static const TowerCal TOWER_CAL[] = {
    // name  rssi1m   n
    {"A", -57.9f, 2.66f},  // single 1 m point 2026-11-07 (OLED up, USB toward A); n from B
    {"B", -57.9f, 2.66f},  // copied from A (same WROOM board type), per user 2026-11-07
    {"C", -71.6f, 2.49f},  // copied from D (same ESP32-C3 board type), per user 2026-11-07
    {"D", -71.6f, 2.49f},  // single 1 m point 2026-11-07; n from C (same ESP32-C3 board)
};
