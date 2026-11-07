"""Live system state: latest tower readings, current fix, trail, sensors."""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

from .towers import Tower, TowerStore
from .trilateration import Fix, KalmanTracker, Range, range_sigma, solve

STALE_S = 2.0
TRAIL_LEN = 300


@dataclass
class Reading:
    rssi: float | None
    plane_distance: float | None
    std: float | None
    count: int
    at: float  # monotonic seconds


class SystemState:
    def __init__(self, towers: TowerStore):
        self.towers = towers
        self.readings: dict[str, Reading] = {}
        self.sensors: dict = {}
        self.truth: list[float] | None = None
        self.fix = Fix(ok=False, reason="no data yet")
        self.filtered: list[float] | None = None
        self.tracker = KalmanTracker()
        self.trail: deque[list[float]] = deque(maxlen=TRAIL_LEN)
        self.last_report_at: float | None = None
        self.reports = 0
        # name -> list of (known_distance_m, rssi) points for calibration
        self.calibration_points: dict[str, list[tuple[float, float]]] = {}

    # ---- ingest -------------------------------------------------------------
    def ingest(self, report: dict) -> None:
        now = time.monotonic()
        self.last_report_at = now
        self.reports += 1
        for t in report.get("towers", []):
            name = str(t.get("n", "?"))
            self.readings[name] = Reading(
                rssi=_f(t.get("r")),
                plane_distance=_f(t.get("d")),
                std=_f(t.get("s")),
                count=int(t.get("c", 0) or 0),
                at=now,
            )
        if isinstance(report.get("sensors"), dict):
            self.sensors = report["sensors"]
        self.truth = report.get("sim_truth")
        self._solve(now)

    def distance_for(self, name: str, r: Reading) -> float | None:
        """Distance from RSSI using the tower's calibration (default calibration if not placed yet)."""
        if r.rssi is None:
            return r.plane_distance
        tower = self.towers.get(name) or Tower(name)
        return tower.rssi_to_distance(r.rssi)

    def _solve(self, now: float) -> None:
        ranges = []
        for name, r in self.readings.items():
            if now - r.at > STALE_S:
                continue
            tower = self.towers.get(name)
            d = self.distance_for(name, r)
            if tower is None or d is None or not math.isfinite(d):
                continue
            ranges.append(Range(name, (tower.x, tower.y, tower.z), d, range_sigma(d, r.std, tower.n)))

        initial = np.array(self.filtered) if self.filtered is not None else None
        z_prior = self.filtered[2] if (self.filtered is not None and len(ranges) == 3) else None
        self.fix = solve(ranges, z_prior=z_prior, z_prior_sigma=1.5, initial=initial)
        if self.fix.ok:
            self.filtered = self.tracker.update(self.fix.position, self.fix.error_radius, now, self.fix.cov).tolist()
            self.trail.append([round(v, 3) for v in self.filtered])

    def clear_live(self) -> None:
        """Forget readings from the previous source (on source switch)."""
        self.readings.clear()
        self.sensors = {}
        self.truth = None
        self.last_report_at = None
        self.reports = 0
        self.reset_track()

    def reset_track(self) -> None:
        """Forget the current position and trail (after towers move, etc.)."""
        self.fix = Fix(ok=False, reason="waiting for data")
        self.tracker.reset()
        self.filtered = None
        self.trail.clear()

    # ---- calibration ----------------------------------------------------------
    def add_calibration_point(self, name: str, known_distance: float, rssi: float) -> dict:
        pts = self.calibration_points.setdefault(name, [])
        pts.append((known_distance, rssi))
        tower = self.towers.get(name)
        assert tower is not None
        if len({round(d, 2) for d, _ in pts}) >= 2:
            # Fit rssi = rssi1m - 10 n log10(d) by least squares.
            x = np.array([-10 * math.log10(d) for d, _ in pts])
            y = np.array([r for _, r in pts])
            A = np.vstack([np.ones_like(x), x]).T
            (rssi1m, n), *_ = np.linalg.lstsq(A, y, rcond=None)
            n = float(min(max(n, 1.5), 6.0))
            tower.rssi1m = float(rssi1m)
            tower.n = n
        else:
            tower.rssi1m = rssi + 10 * tower.n * math.log10(known_distance)
        self.towers.upsert(tower)
        return {"tower": tower.__dict__, "points": pts}

    # ---- snapshot for the UI ----------------------------------------------------
    def snapshot(self, link: dict) -> dict:
        now = time.monotonic()
        towers = []
        used = set(self.fix.used) if self.fix.ok else set()
        known = set()
        for t in self.towers.all():
            known.add(t.name)
            r = self.readings.get(t.name)
            live = None
            if r is not None and now - r.at <= STALE_S:
                live = {
                    "rssi": r.rssi,
                    "distance": self.distance_for(t.name, r),
                    "std": r.std,
                    "count": r.count,
                    "age": round(now - r.at, 2),
                }
            towers.append({**t.__dict__, "live": live, "used": t.name in used})
        unknown = [
            {"name": n, "rssi": r.rssi, "distance": self.distance_for(n, r)}
            for n, r in self.readings.items()
            if n not in known and now - r.at <= STALE_S
        ]
        age = None if self.last_report_at is None else round(now - self.last_report_at, 2)
        return {
            "link": {**link, "last_report_age": age, "reports": self.reports},
            "towers": towers,
            "unknown_towers": unknown,
            "estimate": {
                "ok": self.fix.ok,
                "raw": self.fix.position if self.fix.ok else None,
                "position": self.filtered if self.fix.ok else None,
                "error_radius": self.fix.error_radius,
                "residual_rms": self.fix.residual_rms,
                "used": self.fix.used,
                "reason": self.fix.reason,
                "velocity": self.tracker.velocity,
            },
            "trail": list(self.trail),
            "sensors": self.sensors,
            "truth": self.truth,
        }


def _f(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None
