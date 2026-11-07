// BLE link to the laptop: Nordic-UART-style service, one notify characteristic.
// Each report is one JSON line ending in '\n', split into (MTU - 3)-byte
// notifications; the receiver concatenates chunks and splits on '\n'.
#pragma once

#include <stddef.h>

#define BLE_DEVICE_NAME "PLANE-T6"
#define BLE_SERVICE_UUID "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
#define BLE_NOTIFY_UUID "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
#define BLE_PREFERRED_MTU 247

void bleBegin();
bool bleConnected();
// Payload bytes per notification for the current connection (MTU - 3).
size_t bleChunkSize();
// Sends `len` bytes (should end in '\n') if a client is subscribed.
void bleSendLine(const char *data, size_t len);
