"""Do the techniques work better together?

Flies ONE truth trajectory and then runs the same extended Kalman filter over it four
times, each with a different set of aiding measurements:

  imu      - dead reckoning only (the baseline that drifts)
  doppler  - IMU + Iridium Doppler        (the LEO Satellite page's method)
  beacons  - IMU + ground beacon ranges   (the cell-tower method, scaled up to aviation)
  both     - IMU + Doppler + beacon ranges

Same truth, same IMU errors, same noise draws, so the only thing that differs between
the four runs is what the filter was allowed to listen to. That makes the comparison
honest: any difference in the final error is the sensors, not luck.

  python -m app.fusion --minutes 20          # prints the comparison table
  POST /api/fusion/simulate                  # what the Sensor Fusion page calls
"""
from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, replace

import numpy as np
from skyfield.api import load

from .leo_sat import (
    N_STATE,
    SimConfig,
    ecef_to_geodetic,
    ekf_predict,
    ekf_update_doppler,
    geodetic_to_ecef,
    load_satellites,
    make_truth,
    propagate_constellation,
    simulate_measurements,
    up_vector,
)

log = logging.getLogger("fusion")

COMBOS = [
    ("imu", "IMU only", []),
    ("doppler", "IMU + Iridium Doppler", ["doppler"]),
    ("beacons", "IMU + ground beacons", ["beacons"]),
    ("both", "IMU + Doppler + beacons", ["doppler", "beacons"]),
]


@dataclass
class FusionConfig(SimConfig):
    """SimConfig plus the ground beacon network."""

    beacon_count: int = 4
    beacon_spacing_km: float = 70.0      # how far off the route each beacon sits
    beacon_range_sigma_m: float = 30.0   # ranging noise (cell-tower RSSI is far worse; DME is ~50 m)
    beacon_max_range_km: float = 250.0   # past this the plane can't hear the beacon
    beacon_min_elev_deg: float = 1.0     # and it has to be above the beacon's horizon


# =============================================================================
# Ground beacons
# =============================================================================
def place_beacons(cfg: FusionConfig, lat, lon):
    """Spread beacons along the planned route, alternating sides of the ground track.

    Returns (positions ECEF [m], names). They sit on the ellipsoid (altitude 0).
    """
    n = len(lat)
    out, names = [], []
    for i in range(cfg.beacon_count):
        k = int((i + 0.5) / cfg.beacon_count * (n - 1))          # evenly along the route
        side = 1.0 if i % 2 == 0 else -1.0
        j = min(k + 1, n - 1)
        course = np.arctan2((lon[j] - lon[k]) * np.cos(lat[k]), lat[j] - lat[k])
        off = cfg.beacon_spacing_km * 1000.0 / 6371000.0          # radians of arc
        blat = lat[k] + off * np.cos(course + side * np.pi / 2)
        blon = lon[k] + off * np.sin(course + side * np.pi / 2) / np.cos(lat[k])
        out.append(geodetic_to_ecef(blat, blon, 0.0))
        names.append(f"GND-{i + 1}")
    # reshape() keeps the (n, 3) shape when there are no beacons at all, so everything
    # downstream still works instead of blowing up on an empty 1-D array
    return np.array(out).reshape(-1, 3), names


def beacon_measurements(cfg: FusionConfig, rng, p_true, bp):
    """True range + noise from every beacon at every step; NaN when out of range/below the horizon."""
    if len(bp) == 0:  # "no towers at all" is a scenario worth running, not an error
        empty = np.zeros((0, len(p_true)))
        return empty, empty.astype(bool)
    d = p_true[None, :, :] - bp[:, None, :]                      # beacon -> plane, (n_beacons, n, 3)
    rho = np.linalg.norm(d, axis=2)
    blat, blon, _ = ecef_to_geodetic(bp)
    up = up_vector(np.radians(blat), np.radians(blon))            # local vertical at each beacon
    elev = np.degrees(np.arcsin(np.einsum("bnj,bj->bn", d, up) / rho))
    ok = (rho < cfg.beacon_max_range_km * 1000.0) & (elev > cfg.beacon_min_elev_deg)
    z = rho + rng.normal(0, cfg.beacon_range_sigma_m, rho.shape)
    return np.where(ok, z, np.nan), ok


def ekf_update_range(cfg: FusionConfig, x, P, z, p_beacon):
    """Correct the state with ONE distance measurement z [m] to a beacon at a known spot."""
    r = cfg.beacon_range_sigma_m
    d = p_beacon - x[0:3]
    rho = np.linalg.norm(d)
    u = d / rho

    H = np.zeros(N_STATE)
    H[0:3] = -u                      # moving toward the beacon shortens the range
    y = z - rho
    S = H @ P @ H + r ** 2
    K = P @ H / S
    x = x + K * y
    IKH = np.eye(N_STATE) - np.outer(K, H)
    P = IKH @ P @ IKH.T + r ** 2 * np.outer(K, K)
    return x, 0.5 * (P + P.T)


# =============================================================================
# The experiment
# =============================================================================
@dataclass
class FusionResult:
    cfg: FusionConfig
    tle: dict
    t: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    p_true: np.ndarray
    sat_p: np.ndarray
    sat_names: list[str]
    visible: np.ndarray
    beacon_p: np.ndarray
    beacon_names: list[str]
    beacon_ok: np.ndarray
    tracks: dict[str, np.ndarray]     # combo key -> (n, 3) estimated positions
    runtime_s: float = 0.0

    def err(self, key):
        return np.linalg.norm(self.tracks[key] - self.p_true, axis=1)

    def scene_json(self, max_frames: int = 120) -> dict:
        n = len(self.t)
        step = max(1, -(-n // max_frames))
        ix = list(range(0, n, step))
        if ix[-1] != n - 1:
            ix.append(n - 1)
        flat = lambda p: [int(round(v)) for v in (p[ix] / 1000.0).ravel()]       # noqa: E731
        fine = lambda p: [round(float(v), 4) for v in (p[ix] / 1000.0).ravel()]  # noqa: E731
        return {
            "frames_min": [round(float(self.t[i] / 60.0), 3) for i in ix],
            "earth_radius_km": 6378.1,
            "sat_names": self.sat_names,
            "sat_km": [flat(self.sat_p[s]) for s in range(len(self.sat_names))],
            "visible": [[int(s) for s in np.where(self.visible[:, i])[0]] for i in ix],
            "plane_true_km": fine(self.p_true),
            "beacons_km": [[round(float(v / 1000.0), 3) for v in b] for b in self.beacon_p],
            "beacon_names": self.beacon_names,
            "beacon_range_km": self.cfg.beacon_max_range_km,   # radius of each tower's reach
            "beacon_links": [[int(b) for b in np.where(self.beacon_ok[:, i])[0]] for i in ix],
        }

    def to_json(self, max_frames: int = 120) -> dict:
        n = len(self.t)
        half = n // 2
        scene = self.scene_json(max_frames)
        ix_len = len(scene["frames_min"])
        step = max(1, -(-n // max_frames))
        ix = list(range(0, n, step))
        if ix[-1] != n - 1:
            ix.append(n - 1)
        base = float(self.err("imu")[-1])
        combos = []
        for key, label, sensors in COMBOS:
            e = self.err(key)
            combos.append({
                "key": key,
                "label": label,
                "sensors": sensors,
                "final_m": float(e[-1]),
                "mean_second_half_m": float(e[half:].mean()),
                "max_m": float(e.max()),
                "improvement": float(base / max(e[-1], 1e-6)),
                "km": [round(float(v), 4) for v in (self.tracks[key][ix] / 1000.0).ravel()],
                "err_m": [round(float(e[i]), 2) for i in ix],   # per frame, for the playback readout
            })
        lat_e, lon_e, alt_e = ecef_to_geodetic(self.tracks["both"][-1])
        return {
            "config": asdict(self.cfg),
            "tle": self.tle,
            "runtime_s": round(self.runtime_s, 2),
            "combos": combos,
            "scene": scene,
            "summary": {
                "steps": n,
                "frames": ix_len,
                "sats_mean": round(float(self.visible.sum(axis=0).mean()), 1),
                "beacons_mean": round(float(self.beacon_ok.sum(axis=0).mean()), 1),
                "beacons_total": len(self.beacon_names),
                "best": min(combos, key=lambda c: c["final_m"])["key"],
                "final": {
                    "est_lat": float(lat_e), "est_lon": float(lon_e), "est_alt_m": float(alt_e),
                    "true_lat": float(np.degrees(self.lat[-1])), "true_lon": float(np.degrees(self.lon[-1])),
                    "true_alt_m": float(self.cfg.alt_m),
                },
            },
        }


def simulate(cfg: FusionConfig | None = None, tle_path: str | None = None, refresh_tle: bool = False) -> FusionResult:
    cfg = cfg or FusionConfig()
    started = time.perf_counter()
    rng = np.random.default_rng(cfg.seed)
    ts = load.timescale()
    sats, tt_start, tle = load_satellites(ts, tle_path, refresh_tle)
    n = cfg.steps

    SP, SV = propagate_constellation(sats, ts, tt_start, n, cfg.dt)
    t, lat, lon, p_true, v_true, a_true = make_truth(cfg, n)
    z_dop, visible, _ = simulate_measurements(cfg, rng, p_true, v_true, lat, lon, SP, SV)
    bp, bnames = place_beacons(cfg, lat, lon)
    z_rng, b_ok = beacon_measurements(cfg, rng, p_true, bp)

    a_imu = a_true + np.asarray(cfg.accel_bias) + rng.normal(0, cfg.accel_noise, a_true.shape)

    # Every combination starts from the same (slightly wrong) last GPS fix.
    x0 = np.zeros(N_STATE)
    x0[0:3] = p_true[0] + rng.normal(0, cfg.init_pos_sigma, 3)
    x0[3:6] = v_true[0] + rng.normal(0, cfg.init_vel_sigma, 3)
    P0 = np.diag([cfg.init_pos_sigma ** 2] * 3 + [cfg.init_vel_sigma ** 2] * 3
                 + [cfg.init_clk_sigma ** 2] + [cfg.init_bias_sigma ** 2] * 3)

    tracks = {}
    for key, _label, sensors in COMBOS:
        x, P = x0.copy(), P0.copy()
        out = np.zeros((n, 3))
        out[0] = x[0:3]
        for k in range(1, n):
            x, P = ekf_predict(cfg, x, P, a_imu[k - 1])
            if "doppler" in sensors:
                for s in np.where(visible[:, k])[0]:
                    x, P, _ = ekf_update_doppler(cfg, x, P, z_dop[s, k], SP[s, k], SV[s, k])
            if "beacons" in sensors:
                for b in np.where(b_ok[:, k])[0]:
                    x, P = ekf_update_range(cfg, x, P, z_rng[b, k], bp[b])
            out[k] = x[0:3]
        tracks[key] = out
        log.info("fusion %-8s final error %.0f m", key, np.linalg.norm(out[-1] - p_true[-1]))

    return FusionResult(cfg=cfg, tle=tle, t=t, lat=lat, lon=lon, p_true=p_true, sat_p=SP,
                        sat_names=[s.name for s in sats], visible=visible, beacon_p=bp,
                        beacon_names=bnames, beacon_ok=b_ok, tracks=tracks,
                        runtime_s=time.perf_counter() - started)


def main(argv: list[str] | None = None) -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--minutes", type=float)
    ap.add_argument("--beacons", type=int, help="how many ground beacons along the route")
    ap.add_argument("--beacon-noise", type=float, help="beacon ranging noise [m]")
    ap.add_argument("--seed", type=int)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    over = {k: v for k, v in {"minutes": a.minutes, "beacon_count": a.beacons,
                              "beacon_range_sigma_m": a.beacon_noise, "seed": a.seed}.items() if v is not None}
    res = simulate(replace(FusionConfig(), **over))
    j = res.to_json()
    print(f"\n{res.cfg.minutes:.0f} min without GPS · {j['summary']['sats_mean']} satellites and "
          f"{j['summary']['beacons_mean']} of {j['summary']['beacons_total']} beacons in range on average\n")
    print(f"{'combination':>26} {'final':>10} {'mean 2nd half':>15} {'worst':>10} {'vs IMU':>8}")
    for c in j["combos"]:
        print(f"{c['label']:>26} {c['final_m']:>9.0f}m {c['mean_second_half_m']:>14.0f}m "
              f"{c['max_m']:>9.0f}m {c['improvement']:>7.1f}x")
    print(f"\nbest: {j['summary']['best']}  (run time {j['runtime_s']}s)")


if __name__ == "__main__":
    main()
