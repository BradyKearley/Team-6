"""Calibration sweep: measure every tower at several known distances, then fit each tower.

How it's used (see the Calibration sweep dialog in the UI):
  1. Put all towers together in one spot.
  2. Put the plane at a known distance from them and press Record.
     Every tower's RSSI is recorded at once (one "batch").
  3. Repeat at more distances (e.g. 1, 2, 3, 5 m). Points under 1 m are kept but not fitted.
  4. Each tower gets a least-squares fit of   rssi = rssi1m - 10 * n * log10(d)
     over all its points, and Apply saves rssi1m and n to the tower.

Points are saved to sweep.json so they survive a page reload or server restart.
"""
from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path

N_MIN, N_MAX = 1.5, 6.0  # allowed range for the path-loss exponent
SPREAD_WARN_DB = 4.0  # a point whose readings vary more than this is worth redoing
# Closer than ~1 m the plane's receiver saturates: real sweeps read the same at 0.3 m
# as at 1 m, which wrecks the fit. Such points are recorded and shown, but not fitted.
MIN_FIT_DISTANCE_M = 0.95
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "sweep.json"


@dataclass
class SweepPoint:
    batch: int  # which Record press this came from
    tower: str
    distance: float  # meters
    rssi: float | None  # median RSSI (dBm); None = tower not heard
    spread: float  # max - min of the readings (dB)
    samples: int


def fit_tower(points: list[SweepPoint]) -> dict:
    """Fit rssi = rssi1m - 10 n log10(d) to one tower's heard points."""
    heard = [p for p in points if p.rssi is not None and p.distance >= MIN_FIT_DISTANCE_M]
    too_close = sum(1 for p in points if p.rssi is not None and p.distance < MIN_FIT_DISTANCE_M)
    out = {"points": len(heard), "distances": len({round(p.distance, 3) for p in heard}), "too_close": too_close}
    skipped = f" {too_close} point(s) under 1 m not used (the radio saturates that close)." if too_close else ""
    if out["distances"] < 2:
        return {**out, "ok": False, "quality": "need more", "note": "Record at 2 or more distances of 1 m or more." + skipped}

    xs = [-10 * math.log10(p.distance) for p in heard]  # so that rssi = rssi1m + n * x
    ys = [p.rssi for p in heard]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    n = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    clamped = not (N_MIN <= n <= N_MAX)
    n = min(max(n, N_MIN), N_MAX)
    rssi1m = my - n * mx  # best intercept for this (possibly clamped) slope
    rms = math.sqrt(statistics.mean((y - (rssi1m + n * x)) ** 2 for x, y in zip(xs, ys)))

    if rssi1m < -70:
        quality, note = "weak", "Tower is too weak. Raise its TX power or move it closer."
    elif rms > 5:
        quality, note = "bad", "Readings don't follow a clean curve. Redo noisy points."
    elif rms > 3:
        quality, note = "ok", "Usable, but some points are noisy."
    else:
        quality, note = "good", "Good fit."
    if clamped:
        note += f" Slope was limited to {N_MIN}-{N_MAX}; check the distances."
    note += skipped
    return {**out, "ok": True, "rssi1m": rssi1m, "n": n, "rms": rms, "clamped": clamped, "quality": quality, "note": note}


class Sweep:
    """Recorded sweep points. path=None keeps them in memory only (used in sim mode)."""

    def __init__(self, path: Path | None = DEFAULT_PATH):
        self.path = path
        self.points: list[SweepPoint] = []
        if path is not None and path.exists():
            self.points = [SweepPoint(**p) for p in json.loads(path.read_text())]

    def save(self) -> None:
        if self.path is not None:
            self.path.write_text(json.dumps([asdict(p) for p in self.points], indent=1))

    def add_batch(self, distance: float, readings: dict[str, list[float]]) -> int:
        """Store one Record press. readings = {tower: [rssi samples]} (empty list = not heard)."""
        batch = max((p.batch for p in self.points), default=0) + 1
        for tower, rs in sorted(readings.items()):
            heard = len(rs) >= 3
            self.points.append(
                SweepPoint(
                    batch=batch,
                    tower=tower,
                    distance=distance,
                    rssi=statistics.median(rs) if heard else None,
                    spread=(max(rs) - min(rs)) if heard else 0.0,
                    samples=len(rs),
                )
            )
        self.save()
        return batch

    def undo_last(self) -> None:
        if self.points:
            last = max(p.batch for p in self.points)
            self.points = [p for p in self.points if p.batch != last]
            self.save()

    def clear(self) -> None:
        self.points = []
        self.save()

    def fits(self) -> dict[str, dict]:
        towers = sorted({p.tower for p in self.points})
        return {t: fit_tower([p for p in self.points if p.tower == t]) for t in towers}

    def to_json(self) -> dict:
        return {
            "points": [asdict(p) for p in self.points],
            "fits": self.fits(),
            "spread_warn_db": SPREAD_WARN_DB,
            "min_fit_distance_m": MIN_FIT_DISTANCE_M,
        }
