#include "ranging.h"

#include <math.h>
#include <string.h>

#include <algorithm>

#include "calibration.h"

static TowerState towers[RANGING_MAX_TOWERS];

float rssiToDistance(const char *name, float rssi) {
  float rssi1m = RSSI_AT_1M, n = PATH_LOSS_N;
  for (const auto &c : TOWER_CAL) {
    if (strncmp(c.name, name, TOWER_NAME_LEN) == 0) {
      rssi1m = c.rssi1m;
      n = c.n;
      break;
    }
  }
  return powf(10.0f, (rssi1m - rssi) / (10.0f * n));
}

static TowerState *findOrCreate(const char *name) {
  TowerState *freeSlot = nullptr;
  for (auto &t : towers) {
    if (t.active && strncmp(t.name, name, TOWER_NAME_LEN) == 0) return &t;
    if (!t.active && !freeSlot) freeSlot = &t;
  }
  if (!freeSlot) return nullptr;
  memset(freeSlot, 0, sizeof(*freeSlot));
  freeSlot->active = true;
  strncpy(freeSlot->name, name, TOWER_NAME_LEN);
  freeSlot->name[TOWER_NAME_LEN] = '\0';
  return freeSlot;
}

static float median(float *v, int n) {
  std::sort(v, v + n);
  if (n % 2 == 1) return v[n / 2];            // odd count: the middle value
  return 0.5f * (v[n / 2 - 1] + v[n / 2]);    // even count: average of the two middle values
}

void rangingAddSample(const TowerSample &s) {
  TowerState *t = findOrCreate(s.name);
  if (!t) return;  // table full

  t->window[t->head] = s.rssi;
  t->head = (t->head + 1) % RANGING_WINDOW;
  if (t->count < RANGING_WINDOW) t->count++;
  t->lastSeenMs = s.ms;

  // Window statistics.
  float buf[RANGING_WINDOW];
  float sum = 0, sumSq = 0;
  for (int i = 0; i < t->count; i++) {
    buf[i] = t->window[i];
    sum += buf[i];
    sumSq += buf[i] * buf[i];
  }
  float mean = sum / t->count;
  t->stddev = sqrtf(std::max(0.0f, sumSq / t->count - mean * mean));

  // Outlier check: ignore a sample that's far from the window's median
  // (MAD = median absolute deviation; 1.4826 * MAD ~ one standard deviation).
  // Needs a few samples to be meaningful.
  if (t->count >= 5) {
    float med = median(buf, t->count);
    for (int i = 0; i < t->count; i++) buf[i] = fabsf(buf[i] - med);
    float mad = median(buf, t->count);
    float limit = std::max(RANGING_OUTLIER_K * 1.4826f * mad, RANGING_OUTLIER_MIN_DB);
    if (fabsf(s.rssi - med) > limit) return;  // outlier: keep it in the window, but don't use it
  }

  // Exponential moving average = the smoothed RSSI.
  if (!t->emaInit) {
    t->ema = s.rssi;
    t->emaInit = true;
  } else {
    t->ema += RANGING_EMA_ALPHA * (s.rssi - t->ema);
  }
  t->distance = rssiToDistance(t->name, t->ema);
}

void rangingExpire(uint32_t nowMs) {
  for (auto &t : towers) {
    if (t.active && (int32_t)(nowMs - t.lastSeenMs) > RANGING_TIMEOUT_MS) t.active = false;
  }
}

const TowerState *rangingTowers() { return towers; }
int rangingMaxTowers() { return RANGING_MAX_TOWERS; }
