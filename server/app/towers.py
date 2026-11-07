"""Tower positions + calibration, persisted to towers.json.

Coordinates are meters in the room frame: x/y on the floor, z up.

The simulator uses its own fixed layout (SIM_LAYOUT) that is never saved, so
playing with towers in sim mode can't mess up the real towers in towers.json.
"""
from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, replace
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "towers.json"


@dataclass
class Tower:
    """One tower. These are also the defaults for a new tower everywhere in the app."""

    name: str
    x: float = 0.0
    y: float = 0.0
    z: float = 0.9144  # antenna height, 3 ft
    rssi1m: float = -45.0  # RSSI measured at 1 m
    n: float = 2.5  # path-loss exponent (2 = free space, 2.5-4 indoors)

    def rssi_to_distance(self, rssi: float) -> float:
        """Log-distance path-loss model: every 10*n dB weaker = 10x farther."""
        return float(10 ** ((self.rssi1m - rssi) / (10.0 * self.n)))


# The simulator's towers: a 10 m (about 33 ft) square with different antenna
# heights so altitude can be solved. Default calibration, matching the simulated radio.
SIM_LAYOUT = [
    Tower("A", 0.0, 0.0, 1.0),
    Tower("B", 10.0, 0.0, 1.0),
    Tower("C", 10.0, 10.0, 2.5),
    Tower("D", 0.0, 10.0, 0.2),
]


class TowerStore:
    """In-memory towers, saved to a JSON file on every change.

    path=None keeps them in memory only (used for the simulator's layout).
    The lock is needed because FastAPI runs plain `def` endpoints in worker threads.
    """

    def __init__(self, path: Path | None = DEFAULT_PATH, initial: list[Tower] | None = None):
        self.path = path
        self._lock = threading.Lock()
        self._towers: dict[str, Tower] = {t.name: replace(t) for t in initial or []}  # copies
        self.load()

    @classmethod
    def simulator(cls) -> TowerStore:
        """A fresh copy of the simulator layout (in memory, never saved)."""
        return cls(path=None, initial=SIM_LAYOUT)

    def load(self) -> None:
        if self.path is not None and self.path.exists():
            data = json.loads(self.path.read_text())
            self._towers = {t["name"]: Tower(**t) for t in data}

    def save(self) -> None:
        if self.path is not None:
            self.path.write_text(json.dumps([asdict(t) for t in self._towers.values()], indent=2))

    def all(self) -> list[Tower]:
        with self._lock:
            return list(self._towers.values())

    def get(self, name: str) -> Tower | None:
        with self._lock:
            return self._towers.get(name)

    def upsert(self, tower: Tower) -> Tower:
        with self._lock:
            self._towers[tower.name] = tower
            self.save()
        return tower

    def delete(self, name: str) -> bool:
        with self._lock:
            existed = self._towers.pop(name, None) is not None
            if existed:
                self.save()
        return existed
