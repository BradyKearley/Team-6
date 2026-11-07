// Shared beacon format, broadcast by every cell tower over ESP-NOW and
// parsed by the plane. Both firmwares include this file via `-I ../common`.
//
// Keep it packed and only append fields at the end: the plane checks the
// magic and the minimum length, so old towers keep working with new planes.
#pragma once

#include <stdint.h>
#include <string.h>

#define TOWER_MAGIC "TWR1"  // first 4 bytes of every beacon, so the plane can spot ours
#define TOWER_NAME_LEN 8

typedef struct __attribute__((packed)) {
  char magic[4];               // "TWR1" (not NUL-terminated)
  char name[TOWER_NAME_LEN];   // tower name, NUL-padded, may fill all 8 bytes
  uint32_t seq;                // increments on every beacon
  int8_t txPowerDbm;           // configured TX power in dBm
} TowerPacket;

static_assert(sizeof(TowerPacket) == 17, "TowerPacket must stay packed");

static inline bool towerPacketValid(const TowerPacket *p) {
  return memcmp(p->magic, TOWER_MAGIC, 4) == 0;
}
