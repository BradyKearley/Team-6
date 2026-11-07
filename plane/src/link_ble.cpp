#include "link_ble.h"

#include <Arduino.h>
#include <NimBLEDevice.h>

static NimBLEServer *server = nullptr;
static NimBLECharacteristic *txChar = nullptr;
static volatile bool connected = false;
static volatile uint16_t connHandle = 0;
static volatile uint16_t mtu = 23;

class ServerCallbacks : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer *s, ble_gap_conn_desc *desc) override {
    connHandle = desc->conn_handle;
    mtu = s->getPeerMTU(desc->conn_handle);
    connected = true;
    Serial.println("[ble] connected");
  }
  void onDisconnect(NimBLEServer *s) override {
    connected = false;
    mtu = 23;
    Serial.println("[ble] disconnected, advertising again");
    NimBLEDevice::startAdvertising();
  }
  void onMTUChange(uint16_t newMtu, ble_gap_conn_desc *desc) override {
    mtu = newMtu;
    Serial.printf("[ble] MTU %u\n", newMtu);
  }
};

void bleBegin() {
  NimBLEDevice::init(BLE_DEVICE_NAME);
  NimBLEDevice::setPower(ESP_PWR_LVL_P9);
  NimBLEDevice::setMTU(BLE_PREFERRED_MTU);

  server = NimBLEDevice::createServer();
  server->setCallbacks(new ServerCallbacks());

  NimBLEService *svc = server->createService(BLE_SERVICE_UUID);
  txChar = svc->createCharacteristic(BLE_NOTIFY_UUID, NIMBLE_PROPERTY::NOTIFY | NIMBLE_PROPERTY::READ);
  svc->start();

  NimBLEAdvertising *adv = NimBLEDevice::getAdvertising();
  adv->addServiceUUID(BLE_SERVICE_UUID);
  adv->setScanResponse(true);  // name goes in the scan response
  adv->start();
  Serial.printf("[ble] advertising as %s\n", BLE_DEVICE_NAME);
}

bool bleConnected() { return connected; }

size_t bleChunkSize() {
  uint16_t m = mtu;
  if (m < 23) m = 23;
  return m - 3;
}

void bleSendLine(const char *data, size_t len) {
  if (!connected || !txChar) return;
  size_t chunk = bleChunkSize();
  for (size_t off = 0; off < len; off += chunk) {
    size_t n = (len - off < chunk) ? (len - off) : chunk;
    txChar->setValue((const uint8_t *)data + off, n);
    txChar->notify();
  }
}
