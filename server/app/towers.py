"""Tower positions + calibration, persisted to towers.json.

Coordinates are meters in the room frame: x/y on the floor, z up.
"""
from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
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


class TowerStore:
    """In-memory towers, saved to towers.json on every change.

    The lock is needed because FastAPI runs plain `def` endpoints in worker threads.
    """

    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = path
        self._lock = threading.Lock()
        self._towers: dict[str, Tower] = {}
        self.load()

    def load(self) -> None:
        if self.path.exists():
            data = json.loads(self.path.read_text())
            self._towers = {t["name"]: Tower(**t) for t in data}

    def save(self) -> None:
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
