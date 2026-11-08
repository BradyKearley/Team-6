import numpy as np
import pytest

from app.leo_sat import SimConfig, ecef_to_geodetic, find_tle_file, geodetic_to_ecef, simulate

# The simulation needs orbit elements. Skip (don't fail) when there's no cached TLE file
# and no internet - CI and offline laptops still run the rest of the suite.
needs_tle = pytest.mark.skipif(find_tle_file() is None, reason="no cached TLE file")


def test_geodetic_round_trip():
    for lat, lon, h in [(45.0, -60.0, 10_000.0), (-33.9, 151.2, 0.0), (71.0, 25.0, 3_000.0)]:
        p = geodetic_to_ecef(np.radians(lat), np.radians(lon), h)
        back_lat, back_lon, back_h = ecef_to_geodetic(p)
        assert back_lat == pytest.approx(lat, abs=1e-6)
        assert back_lon == pytest.approx(lon, abs=1e-6)
        assert back_h == pytest.approx(h, abs=1e-3)


@needs_tle
def test_doppler_beats_dead_reckoning():
    res = simulate(SimConfig(minutes=10))
    assert res.n_vis.max() >= 1, "no Iridium satellites in view at all"
    # The whole point: fusing Doppler keeps the error bounded while the IMU runs away.
    assert res.err_ekf[-1] < res.err_dr[-1] / 5
    assert res.err_ekf[-1] < 2000.0
    assert abs(res.ekf_x[-1, 6] - res.clock_true[-1]) < 50.0  # clock offset is solved too


@needs_tle
def test_same_seed_same_run():
    a = simulate(SimConfig(minutes=2, seed=7))
    b = simulate(SimConfig(minutes=2, seed=7))
    assert np.allclose(a.err_ekf, b.err_ekf)
    assert not np.allclose(a.err_ekf, simulate(SimConfig(minutes=2, seed=8)).err_ekf)


@needs_tle
def test_scene_payload_lines_up_with_the_run():
    res = simulate(SimConfig(minutes=20))
    sc = res.scene_json(max_frames=60)
    n = len(sc["frames_min"])
    assert n <= 61  # the last step is always included, even when it isn't on the stride
    assert len(sc["sat_names"]) == len(sc["sat_km"]) == res.sat_p.shape[0]
    assert all(len(s) == n * 3 for s in sc["sat_km"])
    assert len(sc["plane_true_km"]) == len(sc["plane_est_km"]) == n * 3
    assert len(sc["visible"]) == n
    assert all(0 <= i < len(sc["sat_names"]) for f in sc["visible"] for i in f)
    assert sum(len(f) for f in sc["visible"]) > 0, "nothing was ever in view"
    # satellites sit at Iridium's altitude (~780 km) and the plane just above the surface
    r = lambda p, i: (p[i] ** 2 + p[i + 1] ** 2 + p[i + 2] ** 2) ** 0.5  # noqa: E731
    assert 7000 < r(sc["sat_km"][0], 0) < 7400
    assert 6370 < r(sc["plane_true_km"], 0) < 6400


@needs_tle
def test_json_payload_is_small_and_complete():
    j = simulate(SimConfig(minutes=20)).to_json(max_points=200)
    assert len(j["series"]["t_min"]) <= 200
    for key in ("err_dr_m", "err_ekf_m", "sigma3_m", "n_vis", "clock_true_hz", "clock_est_hz"):
        assert len(j["series"][key]) == len(j["series"]["t_min"])
    assert len(j["track"]["fused_en_km"]) == len(j["series"]["t_min"])
    assert j["summary"]["checkpoints"][-1]["minute"] == 20
    assert j["scene"]["sat_km"], "the 3D/2D space view has nothing to draw"
    import json

    json.dumps(j)  # must be plain JSON types (no numpy scalars)
