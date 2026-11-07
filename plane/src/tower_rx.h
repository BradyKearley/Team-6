// Receives tower beacons by sniffing ESP-NOW frames in WiFi promiscuous mode.
// We sniff instead of using esp_now_register_recv_cb() because the Arduino
// 2.x receive callback does not expose RSSI; the promiscuous rx_ctrl does.
#pragma once

#include <stdint.h>

#include "tower_packet.h"

struct TowerSample {
  char name[TOWER_NAME_LEN + 1];  // NUL-terminated copy of the tower name
  int8_t rssi;                    // dBm, from rx_ctrl.rssi
  uint32_t ms;                    // millis() when received
};

// Start WiFi in STA (not connected), lock to `channel`, enable promiscuous RX.
bool towerRxBegin(uint8_t channel);

// Pop one received sample (non-blocking). Called from loop().
bool towerRxPop(TowerSample &out);
