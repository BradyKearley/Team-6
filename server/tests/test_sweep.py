import math

import pytest

from app.sweep import Sweep, SweepPoint, fit_tower


def pts(rssi1m, n, distances, noise=None):
    """Points that follow rssi = rssi1m - 10 n log10(d) exactly (plus optional noise)."""
    noise = noise or [0] * len(distances)
    return [
        SweepPoint(batch=i, tower="A", distance=d, rssi=rssi1m - 10 * n * math.log10(d) + e, spread=1, samples=20)
        for i, (d, e) in enumerate(zip(distances, noise))
    ]


def test_fit_recovers_exact_curve():
    f = fit_tower(pts(-50, 2.8, [1, 2, 3, 5]))
    assert f["ok"] and f["quality"] == "good"
    assert f["rssi1m"] == pytest.approx(-50)
    assert f["n"] == pytest.approx(2.8)
    assert f["rms"] == pytest.approx(0, abs=1e-9)


def test_fit_needs_two_distances():
    f = fit_tower(pts(-50, 2.5, [1, 1]))
    assert not f["ok"] and f["quality"] == "need more"


def test_not_heard_points_are_ignored():
    p = pts(-50, 2.5, [1, 2, 3])
    p.append(SweepPoint(batch=9, tower="A", distance=5, rssi=None, spread=0, samples=0))
    f = fit_tower(p)
    assert f["points"] == 3 and f["n"] == pytest.approx(2.5)


def test_close_points_are_not_fitted():
    # Real sweep: the radio saturates up close, so 0.3 m reads the same as 1 m.
    p = pts(-51, 2.66, [1, 2, 3, 5])
    p.append(SweepPoint(batch=9, tower="A", distance=0.3, rssi=p[0].rssi, spread=1, samples=20))
    f = fit_tower(p)
    assert f["too_close"] == 1 and f["points"] == 4
    assert f["rssi1m"] == pytest.approx(-51) and f["n"] == pytest.approx(2.66)
    assert "under 1 m" in f["note"]


def test_clamped_slope_refits_intercept():
    # true n = 1.0 is below the allowed 1.5; the intercept must be refit for n = 1.5
    f = fit_tower(pts(-50, 1.0, [1, 2, 4, 8]))
    assert f["clamped"] and f["n"] == 1.5
    # with n fixed, the best intercept is mean(y) - n * mean(x)
    xs = [-10 * math.log10(d) for d in [1, 2, 4, 8]]
    ys = [-50 + 1.0 * x for x in xs]
    assert f["rssi1m"] == pytest.approx(sum(ys) / 4 - 1.5 * sum(xs) / 4)


def test_weak_tower_flagged():
    f = fit_tower(pts(-78, 2.5, [1, 2, 3]))
    assert f["quality"] == "weak"


def test_noisy_points_rated():
    f = fit_tower(pts(-50, 2.5, [1, 2, 3, 5, 7], noise=[6, -6, 6, -6, 6]))
    assert f["quality"] == "bad"


def test_batches_undo_and_persist(tmp_path):
    path = tmp_path / "sweep.json"
    s = Sweep(path)
    s.add_batch(1.0, {"A": [-50, -51, -49], "B": []})
    s.add_batch(2.0, {"A": [-58, -57, -59], "B": [-70, -71, -69]})
    assert len(s.points) == 4
    b = [p for p in s.points if p.tower == "B" and p.distance == 1.0][0]
    assert b.rssi is None  # not heard
    s.undo_last()  # removes both towers' points from the 2 m recording
    assert {p.distance for p in s.points} == {1.0}
    assert len(Sweep(path).points) == 2  # reloaded from disk
    s.clear()
    assert Sweep(path).points == []
