import math
import random

import numpy as np
import pytest

from app.sources.base import LineAssembler
from app.state import SystemState
from app.towers import Tower, TowerStore
from app.trilateration import KalmanTracker, Range, solve

TOWERS4 = [(0, 0, 1), (10, 0, 1), (10, 10, 2.5), (0, 10, 0.2)]


def ranges_for(point, towers, noise=0.0, seed=0):
    rng = random.Random(seed)
    return [
        Range(chr(65 + i), t, math.dist(point, t) + rng.gauss(0, noise), sigma=max(noise, 0.25))
        for i, t in enumerate(towers)
    ]


@pytest.mark.parametrize("point", [(5, 5, 1.5), (2, 8, 0.5), (9, 1, 2.0)])
def test_four_towers_exact(point):
    fix = solve(ranges_for(point, TOWERS4))
    assert fix.ok
    assert np.allclose(fix.position, point, atol=0.05)


def test_four_towers_noisy():
    errs = []
    for seed in range(30):
        fix = solve(ranges_for((4, 6, 1.2), TOWERS4, noise=0.3, seed=seed))
        errs.append(math.dist(fix.position, (4, 6, 1.2)))
    # z is weakly constrained by low towers, so allow ~1 m median error
    assert np.median(errs) < 1.0


def test_three_towers_with_altitude_prior():
    towers = TOWERS4[:3]
    fix = solve(ranges_for((5, 4, 1.0), towers), z_prior=1.0)
    assert fix.ok
    assert math.dist(fix.position[:2], (5, 4)) < 0.2


def test_two_towers_insufficient():
    fix = solve(ranges_for((5, 5, 1), TOWERS4[:2]))
    assert not fix.ok and "need 3" in fix.reason


def test_kalman_smooths():
    k = KalmanTracker()
    rng = random.Random(1)
    out = None
    for i in range(50):
        out = k.update([5 + rng.gauss(0, 0.5), 5 + rng.gauss(0, 0.5), 1], 0.5, i * 0.2)
    assert math.dist(out, (5, 5, 1)) < 0.5


def test_line_assembler_handles_chunks():
    asm = LineAssembler()
    line = '{"t":1,"towers":[{"n":"A","r":-60,"d":3,"s":1,"c":5}],"sensors":{}}\n'
    assert asm.feed("boot msg\n") == []
    assert asm.feed(line[:20]) == []
    out = asm.feed(line[20:])
    assert len(out) == 1 and out[0]["towers"][0]["n"] == "A"


def test_state_end_to_end(tmp_path):
    store = TowerStore(tmp_path / "towers.json")
    for i, (x, y, z) in enumerate(TOWERS4):
        store.upsert(Tower(chr(65 + i), x, y, z, rssi1m=-45, n=2.5))
    state = SystemState(store)
    truth = (3, 7, 1.5)
    towers = []
    for t in store.all():
        d = math.dist(truth, (t.x, t.y, t.z))
        towers.append({"n": t.name, "r": t.rssi1m - 25 * math.log10(d), "d": 0, "s": 1.0, "c": 20})
    state.ingest({"t": 0, "towers": towers, "sensors": {"vbat": 4.0}})
    snap = state.snapshot({"source": "test", "status": "ok"})
    assert snap["estimate"]["ok"]
    assert math.dist(snap["estimate"]["raw"], truth) < 0.1
    assert snap["sensors"]["vbat"] == 4.0


def test_unplaced_tower_uses_default_calibration(tmp_path):
    state = SystemState(TowerStore(tmp_path / "towers.json"))
    state.ingest({"t": 0, "towers": [{"n": "Z", "r": -45 - 25, "d": 99, "s": 1, "c": 5}]})
    snap = state.snapshot({})
    # -70 dBm with defaults (-45 @ 1 m, n=2.5) is 10 m, regardless of the plane's own "d"
    assert snap["unknown_towers"][0]["distance"] == pytest.approx(10)


def test_reset_track_clears_fix(tmp_path):
    store = TowerStore(tmp_path / "towers.json")
    for i, (x, y, z) in enumerate(TOWERS4):
        store.upsert(Tower(chr(65 + i), x, y, z))
    state = SystemState(store)
    towers = [{"n": t.name, "r": -55, "d": 0, "s": 1, "c": 20} for t in store.all()]
    state.ingest({"t": 0, "towers": towers})
    assert state.snapshot({})["estimate"]["ok"]
    state.reset_track()
    est = state.snapshot({})["estimate"]
    assert not est["ok"] and est["position"] is None


def test_calibration_two_points(tmp_path):
    store = TowerStore(tmp_path / "towers.json")
    store.upsert(Tower("A"))
    state = SystemState(store)
    # true model: rssi1m=-50, n=3
    state.add_calibration_point("A", 1.0, -50.0)
    state.add_calibration_point("A", 5.0, -50 - 30 * math.log10(5))
    t = store.get("A")
    assert t.rssi1m == pytest.approx(-50, abs=0.01)
    assert t.n == pytest.approx(3.0, abs=0.01)
