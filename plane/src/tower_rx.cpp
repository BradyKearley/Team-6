#include "tower_rx.h"

#include <Arduino.h>
#include <WiFi.h>
#include <esp_wifi.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <string.h>

// The promiscuous callback runs in the WiFi driver task. It only parses the
// frame and pushes a small struct onto a FreeRTOS queue (thread-safe); all
// filtering happens in loop() after towerRxPop().
static QueueHandle_t rxQueue = nullptr;

// ESP-NOW frame layout (802.11 vendor-specific action frame):
//   [0..23]  MAC header, frame control byte 0 = 0xD0 (mgmt / action)
//   [24]     category = 127 (vendor specific)
//   [25..27] OUI 18:FE:34 (Espressif)
//   [28..31] random bytes
//   [32]     element ID = 0xDD
//   [33]     element length = 5 + body length
//   [34..36] OUI 18:FE:34
//   [37]     type = 4 (ESP-NOW)
//   [38]     version
//   [39..]   body (our TowerPacket)
static const int kHdrLen = 24;
static const int kBodyOffset = 39;

static void onPromisc(void *buf, wifi_promiscuous_pkt_type_t type) {
  if (type != WIFI_PKT_MGMT) return;
  const wifi_promiscuous_pkt_t *pkt = (const wifi_promiscuous_pkt_t *)buf;
  const uint8_t *p = pkt->payload;
  int len = pkt->rx_ctrl.sig_len;  // includes the 4-byte FCS

  if (len < kBodyOffset + (int)sizeof(TowerPacket)) return;
  if (p[0] != 0xD0) return;                                    // action frame
  if (p[kHdrLen] != 127) return;                               // vendor specific
  if (p[25] != 0x18 || p[26] != 0xFE || p[27] != 0x34) return; // Espressif
  if (p[32] != 0xDD || p[37] != 4) return;                     // ESP-NOW element
  int bodyLen = (int)p[33] - 5;
  if (bodyLen < (int)sizeof(TowerPacket)) return;

  TowerPacket tp;
  memcpy(&tp, p + kBodyOffset, sizeof(tp));
  if (!towerPacketValid(&tp)) return;

  TowerSample s;
  memcpy(s.name, tp.name, TOWER_NAME_LEN);
  s.name[TOWER_NAME_LEN] = '\0';
  s.rssi = pkt->rx_ctrl.rssi;
  s.ms = millis();

  xQueueSend(rxQueue, &s, 0);  // if the queue is full, this sample is simply dropped
}

bool towerRxBegin(uint8_t channel) {
  rxQueue = xQueueCreate(64, sizeof(TowerSample));
  if (!rxQueue) return false;

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  // BLE is also running: the coexistence driver aborts unless modem sleep is on.
  esp_wifi_set_ps(WIFI_PS_MIN_MODEM);

  wifi_promiscuous_filter_t filt = {};
  filt.filter_mask = WIFI_PROMIS_FILTER_MASK_MGMT;
  esp_wifi_set_promiscuous_filter(&filt);
  esp_wifi_set_promiscuous_rx_cb(onPromisc);
  if (esp_wifi_set_promiscuous(true) != ESP_OK) return false;
  return esp_wifi_set_channel(channel, WIFI_SECOND_CHAN_NONE) == ESP_OK;
}

bool towerRxPop(TowerSample &out) {
  return rxQueue && xQueueReceive(rxQueue, &out, 0) == pdTRUE;
}
