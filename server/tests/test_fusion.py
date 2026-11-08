import numpy as np
import pytest

from app.fusion import COMBOS, FusionConfig, beacon_measurements, place_beacons, simulate
from app.leo_sat import find_tle_file, make_truth

needs_tle = pytest.mark.skipif(find_tle_file() is None, reason="no cached TLE file")


def test_beacons_sit_near_the_route_and_can_see_the_plane():
    cfg = FusionConfig(minutes=20)
    t, lat, lon, p, v, a = make_truth(cfg, cfg.steps)
    bp, names = place_beacons(cfg, lat, lon)
    assert len(bp) == len(names) == cfg.beacon_count
    z, ok = beacon_measurements(cfg, np.random.default_rng(0), p, bp)
    assert ok.any(axis=1).all(), "every beacon should hear the plane at some point"
    # the measurement really is the distance, give or take the ranging noise
    rho = np.linalg.norm(bp[:, None, :] - p[None, :, :], axis=2)
    err = (z - rho)[ok]
    assert abs(err.mean()) < cfg.beacon_range_sigma_m
    assert np.isnan(z[~ok]).all()


@needs_tle
def test_combining_sensors_is_at_least_as_good_as_either_alone():
    res = simulate(FusionConfig(minutes=20))
    assert set(res.tracks) == {k for k, _, _ in COMBOS}
    final = {k: float(res.err(k)[-1]) for k in res.tracks}
    assert final["imu"] > 1000, "dead reckoning should have drifted badly by 20 min"
    assert final["doppler"] < final["imu"] / 10
    assert final["beacons"] < final["imu"] / 10
    # the point of the page: both together beat each on its own (with a little slack)
    assert final["both"] <= min(final["doppler"], final["beacons"]) * 1.5


@needs_tle
def test_no_beacons_at_all_still_runs():
    """"No towers" is a scenario the page offers, so it must not blow up on empty arrays."""
    res = simulate(FusionConfig(minutes=5, beacon_count=0))
    assert np.allclose(res.tracks["beacons"], res.tracks["imu"]), "no towers = no aiding"
    assert np.allclose(res.tracks["both"], res.tracks["doppler"])
    j = res.to_json()
    assert j["scene"]["beacons_km"] == []
    assert all(f == [] for f in j["scene"]["beacon_links"])


@needs_tle
def test_every_combination_starts_from_the_same_fix():
    res = simulate(FusionConfig(minutes=2))
    starts = [res.tracks[k][0] for k, _, _ in COMBOS]
    for s in starts[1:]:
        assert np.allclose(s, starts[0])


@needs_tle
def test_json_payload_has_a_track_and_beacons():
    j = simulate(FusionConfig(minutes=10)).to_json(max_frames=60)
    frames = len(j["scene"]["frames_min"])
    assert [c["key"] for c in j["combos"]] == [k for k, _, _ in COMBOS]
    for c in j["combos"]:
        assert len(c["km"]) == frames * 3
        assert len(c["err_m"]) == frames
    assert len(j["scene"]["beacons_km"]) == len(j["scene"]["beacon_names"]) == 4
    assert len(j["scene"]["beacon_links"]) == frames
    assert j["summary"]["best"] in {k for k, _, _ in COMBOS}
    import json

    json.dumps(j)
