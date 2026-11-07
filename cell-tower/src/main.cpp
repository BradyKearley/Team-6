// Cell tower: broadcasts a TowerPacket over ESP-NOW every BEACON_PERIOD_MS
// on a fixed WiFi channel at maximum TX power. The plane measures the RSSI
// of these frames to estimate its distance to this tower.
#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <string.h>

#include "tower_packet.h"

#ifndef TOWER_NAME
#define TOWER_NAME "A"
#endif
#ifndef CHANNEL
#define CHANNEL 1
#endif
#ifndef BEACON_PERIOD_MS
#define BEACON_PERIOD_MS 50
#endif
#ifndef LED_PIN
#define LED_PIN 2
#endif
// esp_wifi_set_max_tx_power() takes units of 0.25 dBm; 84 = 21 dBm (max).
#ifndef TX_POWER_QDBM
#define TX_POWER_QDBM 84
#endif

static const uint8_t kBroadcast[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

static TowerPacket packet;
static volatile uint32_t sendOk = 0;
static volatile uint32_t sendFail = 0;

static void onSent(const uint8_t *mac, esp_now_send_status_t status) {
  if (status == ESP_NOW_SEND_SUCCESS) sendOk++;
  else sendFail++;
}

static void fail(const char *what, esp_err_t err) {
  Serial.printf("[tower %s] %s failed: %s\n", TOWER_NAME, what, esp_err_to_name(err));
  // Fast blink forever so a broken tower is obvious in the field.
  for (;;) {
    digitalWrite(LED_PIN, !digitalRead(LED_PIN));
    delay(100);
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
  delay(200);

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  esp_wifi_set_ps(WIFI_PS_NONE);

  // Lock the radio to CHANNEL (STA is not connected, so nothing moves it).
  esp_wifi_set_promiscuous(true);
  esp_err_t err = esp_wifi_set_channel(CHANNEL, WIFI_SECOND_CHAN_NONE);
  esp_wifi_set_promiscuous(false);
  if (err != ESP_OK) fail("set_channel", err);

  err = esp_now_init();
  if (err != ESP_OK) fail("esp_now_init", err);
  esp_now_register_send_cb(onSent);

  // 1 Mbps long-preamble: the most robust rate, best range.
  esp_wifi_config_espnow_rate(WIFI_IF_STA, WIFI_PHY_RATE_1M_L);

  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, kBroadcast, 6);
  peer.channel = CHANNEL;
  peer.ifidx = WIFI_IF_STA;
  peer.encrypt = false;
  err = esp_now_add_peer(&peer);
  if (err != ESP_OK) fail("esp_now_add_peer", err);

  // Set TX power last: earlier init steps can lower the cap again.
  err = esp_wifi_set_max_tx_power(TX_POWER_QDBM);
  if (err != ESP_OK) fail("set_max_tx_power", err);
  int8_t actualQdbm = 0;
  esp_wifi_get_max_tx_power(&actualQdbm);

  memset(&packet, 0, sizeof(packet));
  memcpy(packet.magic, TOWER_MAGIC, 4);
  strncpy(packet.name, TOWER_NAME, TOWER_NAME_LEN);  // NUL-padded
  packet.txPowerDbm = (int8_t)(actualQdbm / 4);

  Serial.printf("[tower %s] MAC %s  channel %d  tx %.2f dBm  period %d ms\n",
                TOWER_NAME, WiFi.macAddress().c_str(), CHANNEL,
                actualQdbm / 4.0f, BEACON_PERIOD_MS);
}

void loop() {
  static uint32_t nextSend = 0;
  static uint32_t lastLog = 0;
  static uint32_t ledOffAt = 0;

  uint32_t now = millis();

  if ((int32_t)(now - nextSend) >= 0) {
    nextSend = now + BEACON_PERIOD_MS;
    packet.seq++;
    esp_err_t err = esp_now_send(kBroadcast, (const uint8_t *)&packet, sizeof(packet));
    if (err == ESP_OK) {
      digitalWrite(LED_PIN, HIGH);
      ledOffAt = now + 5;
    } else {
      sendFail++;
    }
  }

  if (ledOffAt && (int32_t)(now - ledOffAt) >= 0) {
    digitalWrite(LED_PIN, LOW);
    ledOffAt = 0;
  }

  if (now - lastLog >= 1000) {
    lastLog = now;
    Serial.printf("[tower %s] seq=%lu ok=%lu fail=%lu\n", TOWER_NAME,
                  (unsigned long)packet.seq, (unsigned long)sendOk,
                  (unsigned long)sendFail);
  }

  delay(1);
}
