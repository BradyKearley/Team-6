// Per-tower RSSI filtering and RSSI -> distance conversion.
//
// Pipeline per tower:
//   raw RSSI -> window of the last RANGING_WINDOW samples
//            -> reject samples far from the window median (outliers)
//            -> EMA on accepted samples = filtered RSSI
//            -> d = 10^((rssi1m - rssi) / (10 * n)), per-tower values in calibration.h
// Towers silent for RANGING_TIMEOUT_MS are dropped.
//
// Not thread-safe by itself: only call from loop() (tower_rx hands samples
// over through a FreeRTOS queue).
#pragma once

#include <stdint.h>

#include "tower_rx.h"

#ifndef RSSI_AT_1M
#define RSSI_AT_1M -45.0f
#endif
#ifndef PATH_LOSS_N
#define PATH_LOSS_N 2.5f
#endif

#define RANGING_WINDOW 20
#define RANGING_MAX_TOWERS 8
#define RANGING_TIMEOUT_MS 2000
#define RANGING_EMA_ALPHA 0.3f
// Reject a sample if it is more than max(OUTLIER_K * robust sigma,
// OUTLIER_MIN_DB) away from the window median.
#define RANGING_OUTLIER_K 3.0f
#define RANGING_OUTLIER_MIN_DB 6.0f

struct TowerState {
  bool active;
  char name[TOWER_NAME_LEN + 1];
  int8_t window[RANGING_WINDOW];  // ring buffer of raw RSSI
  uint8_t head;                   // next write position
  uint8_t count;                  // samples in window (<= RANGING_WINDOW)
  float ema;                      // filtered RSSI (dBm)
  bool emaInit;
  float stddev;                   // std-dev of raw RSSI in the window (dB)
  float distance;                 // meters
  uint32_t lastSeenMs;
};

void rangingAddSample(const TowerSample &s);

// Drop towers not heard from in RANGING_TIMEOUT_MS.
void rangingExpire(uint32_t nowMs);

// Active towers live in this fixed table; check `.active`.
const TowerState *rangingTowers();
int rangingMaxTowers();

// Uses the tower's entry in calibration.h, else RSSI_AT_1M / PATH_LOSS_N.
float rssiToDistance(const char *name, float rssi);
