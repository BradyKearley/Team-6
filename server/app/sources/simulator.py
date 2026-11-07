"""Fake plane for building/demoing without hardware.

Models the real signal chain instead of adding fresh noise to every report:

  1. Truth: the plane flies a smooth figure-eight over the tower area.
  2. Radio: each tower beacons at 20 Hz. Every beacon's RSSI is
       path loss (with each tower's slightly-off "true" calibration)
     + shadowing   (slowly drifting multipath bias, Ornstein-Uhlenbeck)
     + fast fading (per-packet jitter, with occasional deep fades)
     and is quantized to whole dBm, like the ESP32 reports. Some packets are lost.
  3. Plane: the same median/MAD + EMA filter as plane/src/ranging.cpp,
     reported at 5 Hz in the plane's JSON format.

The true position is included as "sim_truth" so the UI can show the error.
"""
from __future__ import annotations

import asyncio
import math
import random
import statistics
import time
from collections import deque
from dataclasses import dataclass, field

from ..towers import Tower, TowerStore
from .base import ReportHandler

SPEED_MPS = 1.0  # fake plane ground speed

# ---- radio model (tweak these to make the sim harder or easier) ---------------
BEACON_HZ = 20
REPORT_EVERY = 4  # beacons per report -> 5 Hz, like the plane firmware
SHADOW_SIGMA_DB = 2.0  # spread of the slow multipath bias
SHADOW_TAU_S = 4.0  # how long that bias takes to change
FADE_SIGMA_DB = 2.5  # per-packet jitter
DEEP_FADE_P = 0.03  # chance a packet lands in a deep fade
DEEP_FADE_DB = 12.0
LOSS_P = 0.10  # base packet loss (grows near the sensitivity floor)
SENSITIVITY_DBM = -95
CAL_ERROR_DB = 1.5  # each tower's true RSSI@1m differs from its configured value by up to this

# ---- plane filter, mirrors plane/src/ranging.{h,cpp} --------------------------
WINDOW = 20
EMA_ALPHA = 0.3
OUTLIER_K = 3.0
OUTLIER_MIN_DB = 6.0


@dataclass
class _TowerSim:
    cal_offset: float  # hidden calibration error, dB
    shadow: float = 0.0
    window: deque = field(default_factory=lambda: deque(maxlen=WINDOW))
    ema: float | None = None
    last_rx: float = -1e9


class SimulatorSource:
    kind = "sim"

    def __init__(self, towers: TowerStore, seed: int | None = None):
        self.status = "starting"
        self.towers = towers
        self.rng = random.Random(seed)
        self.sims: dict[str, _TowerSim] = {}
        self.speed = SPEED_MPS  # changeable live from the UI (POST /api/sim)
        self.phase = 0.0  # position along the figure-eight, radians

    # ---- truth -----------------------------------------------------------
    def _path(self) -> tuple[float, float, float, float]:
        ts = self.towers.all()
        if ts:
            xs = [p.x for p in ts]
            ys = [p.y for p in ts]
            cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
            rx = max((max(xs) - min(xs)) / 2 * 0.6, 1.0)
            ry = max((max(ys) - min(ys)) / 2 * 0.6, 1.0)
        else:
            cx = cy = 0.0
            rx = ry = 3.0
        return cx, cy, rx, ry

    def advance(self, dt: float) -> tuple[float, float, float]:
        """Move along the path at self.speed m/s and return the new position.

        The phase is integrated (not computed from t), so changing the speed
        mid-flight doesn't teleport the plane.
        """
        cx, cy, rx, ry = self._path()
        # Path length of this figure-eight is roughly 5.3 * max(rx, ry) per 2*pi.
        self.phase += dt * self.speed * 2 * math.pi / (5.3 * max(rx, ry))
        a = self.phase
        return (cx + rx * math.sin(a), cy + ry * math.sin(2 * a) / 2 * 1.6, 1.5 + 0.5 * math.sin(a * 0.5))

    # ---- radio -------------------------------------------------------------
    def _sim(self, tw: Tower) -> _TowerSim:
        s = self.sims.get(tw.name)
        if s is None:
            s = _TowerSim(cal_offset=self.rng.uniform(-CAL_ERROR_DB, CAL_ERROR_DB))
            s.shadow = self.rng.gauss(0, SHADOW_SIGMA_DB)
            self.sims[tw.name] = s
        return s

    def _beacon(self, tw: Tower, s: _TowerSim, pos, dt: float) -> int | None:
        """RSSI of one received beacon, or None if the packet was lost."""
        # Ornstein-Uhlenbeck step: slow, mean-reverting drift.
        k = math.exp(-dt / SHADOW_TAU_S)
        s.shadow = s.shadow * k + self.rng.gauss(0, SHADOW_SIGMA_DB * math.sqrt(1 - k * k))

        d = max(math.dist(pos, (tw.x, tw.y, tw.z)), 0.1)
        mean = tw.rssi1m + s.cal_offset - 10 * tw.n * math.log10(d) + s.shadow
        rssi = mean + self.rng.gauss(0, FADE_SIGMA_DB)
        if self.rng.random() < DEEP_FADE_P:
            rssi -= self.rng.uniform(0.5, 1.0) * DEEP_FADE_DB

        margin = rssi - SENSITIVITY_DBM
        loss = LOSS_P if margin > 6 else LOSS_P + (1 - LOSS_P) * (1 - max(margin, 0) / 6)
        if self.rng.random() < loss:
            return None
        return int(round(rssi))

    # ---- plane filter --------------------------------------------------------
    @staticmethod
    def _filter(s: _TowerSim, rssi: int) -> None:
        s.window.append(rssi)
        accept = True
        if len(s.window) >= 5:
            med = statistics.median(s.window)
            mad = statistics.median(abs(v - med) for v in s.window)
            if abs(rssi - med) > max(OUTLIER_K * 1.4826 * mad, OUTLIER_MIN_DB):
                accept = False
        if accept:
            s.ema = float(rssi) if s.ema is None else s.ema + EMA_ALPHA * (rssi - s.ema)

    # ---- main loop -----------------------------------------------------------
    async def run(self, on_report: ReportHandler) -> None:
        self.status = "simulating"
        dt = 1.0 / BEACON_HZ
        t0 = time.monotonic()
        tick = 0
        while True:
            t = tick * dt
            pos = self.advance(dt)
            live = self.towers.all()
            names = {tw.name for tw in live}
            for gone in set(self.sims) - names:
                del self.sims[gone]

            for tw in live:
                s = self._sim(tw)
                rssi = self._beacon(tw, s, pos, dt)
                if rssi is not None:
                    self._filter(s, rssi)
                    s.last_rx = t

            tick += 1
            if tick % REPORT_EVERY == 0:
                towers = []
                for tw in live:
                    s = self.sims[tw.name]
                    if s.ema is None or t - s.last_rx > 2.0:  # plane drops silent towers after 2 s
                        continue
                    towers.append(
                        {
                            "n": tw.name,
                            "r": round(s.ema, 1),
                            # the plane firmware uses default calibration for its own "d"
                            "d": round(Tower(tw.name).rssi_to_distance(s.ema), 2),
                            "s": round(statistics.pstdev(s.window), 1) if len(s.window) > 1 else 0.0,
                            "c": len(s.window),
                        }
                    )
                report = {
                    "t": int(t * 1000),
                    "towers": towers,
                    "sensors": {"uptime_s": int(t), "vbat": round(4.1 - t / 3600, 2)},
                    "sim_truth": list(pos),
                }
                on_report(report)

            # Keep to wall-clock time without drifting.
            delay = t0 + tick * dt - time.monotonic()
            await asyncio.sleep(max(delay, 0))
