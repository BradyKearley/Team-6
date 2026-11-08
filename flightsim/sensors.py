"""The aeroplane's senses, and the filter that turns them into a position.

Everything the agent is allowed to know comes from here. The models are the same ones
the web pages use - they are imported from ../server/app rather than copied:

  IMU        accelerometer with a constant bias and white noise (ECEF axes, as in leo_sat)
  AHRS       bank / pitch / heading with noise and a slow bias
  pitot      airspeed with noise
  towers     distance to each ground station in range (two-way, so no clock term)
  satellites Doppler shift of every Iridium NEXT satellite above the elevation mask

and an extended Kalman filter (the one from app/fusion.py) fuses the lot into an
estimated position, velocity and uncertainty. There is no GPS anywhere in this folder:
that's the point.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

import paths  # noqa: F401  (puts ../server on the path)
from app.fusion import FusionConfig, ekf_update_range
from app.leo_sat import (
    N_STATE,
    ecef_to_geodetic,
    ekf_predict,
    ekf_update_doppler,
    geodetic_to_ecef,
    load_satellites,
    propagate_constellation,
    up_vector,
)
from skyfield.api import load

C = 299_792_458.0


# =============================================================================
# Frames: a local east/north/up box anchored on the Earth
# =============================================================================
class Anchor:
    """Converts between the sim's local ENU metres and ECEF."""

    def __init__(self, lat_deg: float, lon_deg: float):
        self.lat, self.lon = np.radians(lat_deg), np.radians(lon_deg)
        self.origin = geodetic_to_ecef(self.lat, self.lon, 0.0)
        sl, cl = np.sin(self.lat), np.cos(self.lat)
        so, co = np.sin(self.lon), np.cos(self.lon)
        # columns: east, north, up
        self.R = np.array([[-so, -sl * co, cl * co],
                           [co, -sl * so, cl * so],
                           [0.0, cl, sl]])

    def to_ecef(self, enu: np.ndarray) -> np.ndarray:
        return self.origin + self.R @ enu

    def vec_to_ecef(self, enu: np.ndarray) -> np.ndarray:
        return self.R @ enu

    def to_enu(self, ecef: np.ndarray) -> np.ndarray:
        return self.R.T @ (ecef - self.origin)


# =============================================================================
# The Iridium constellation, propagated once and shared by every environment
# =============================================================================
class Constellation:
    """Real orbits from the cached TLEs, sampled on a grid and interpolated."""

    def __init__(self, window_s: float = 3600.0, grid_s: float = 1.0):
        ts = load.timescale()
        sats, tt0, self.info = load_satellites(ts)
        self.grid = grid_s
        n = int(window_s / grid_s) + 2
        self.p, self.v = propagate_constellation(sats, ts, tt0, n, grid_s)  # (sats, n, 3) metres
        self.window = (n - 2) * grid_s
        self.names = [s.name for s in sats]

    def at(self, t: float):
        """Satellite positions and velocities at time t seconds into the window."""
        x = np.clip(t / self.grid, 0, self.p.shape[1] - 1.001)
        i = int(x)
        f = x - i
        return (self.p[:, i] * (1 - f) + self.p[:, i + 1] * f,
                self.v[:, i] * (1 - f) + self.v[:, i + 1] * f)


_CONSTELLATION: Constellation | None = None


def constellation() -> Constellation:
    """One shared copy - propagating 80 satellites is not something to redo per episode."""
    global _CONSTELLATION
    if _CONSTELLATION is None:
        _CONSTELLATION = Constellation()
    return _CONSTELLATION


# =============================================================================
# Sensors
# =============================================================================
@dataclass
class SensorConfig:
    dt: float = 0.5
    # IMU: a constant bias drawn per flight, plus white noise (see README on realism)
    imu_bias_sigma: float = 0.012     # m/s^2, ~1.2 mg
    imu_noise: float = 0.02           # m/s^2 per sample
    ahrs_noise_deg: float = 0.4
    ahrs_bias_deg: float = 0.5
    airspeed_noise: float = 1.5       # m/s
    # radio navigation
    doppler_sigma_hz: float = 5.0
    min_elev_deg: float = 10.0
    max_sats: int = 4                 # how many satellites the raw observation carries
    tower_sigma_m: float = 30.0
    tower_max_km: float = 250.0
    max_towers: int = 4
    orbit_err_m: float = 0.0          # error in the satellite positions the filter uses
    clock_bias0_hz: float = 800.0
    clock_rw_hz_rts: float = 0.2
    # filter start-up (the last fix before the outage)
    init_pos_sigma: float = 20.0
    init_vel_sigma: float = 0.5


@dataclass
class Reading:
    """One tick of everything the aircraft can know about itself."""

    est_pos: np.ndarray               # ECEF [m], the filter's answer
    est_vel: np.ndarray
    pos_sigma: float                  # 1 sigma position uncertainty [m]
    imu_accel: np.ndarray             # measured acceleration, ECEF [m/s^2]
    bank: float                       # AHRS, rad
    gamma: float
    heading: float
    airspeed: float                   # pitot, m/s
    sat_dirs: np.ndarray              # (max_sats, 3) unit vectors to satellites in view
    sat_doppler: np.ndarray           # (max_sats,) kHz, 0 where padded
    sat_valid: np.ndarray             # (max_sats,) 1/0
    tower_range: np.ndarray           # (max_towers,) metres, 0 where padded
    tower_valid: np.ndarray
    n_sats: int
    n_towers: int
    sat_ids: list = field(default_factory=list)      # which satellites, for labelling
    tower_ids: list = field(default_factory=list)


class SensorSuite:
    """Generates the measurements for one flight and runs the navigation filter."""

    def __init__(self, cfg: SensorConfig, anchor: Anchor, towers_enu: np.ndarray):
        self.cfg = cfg
        self.set_layout(anchor, towers_enu)
        # the filter's tuning, shared with the web pages
        self.fcfg = FusionConfig(dt=cfg.dt, doppler_sigma_hz=cfg.doppler_sigma_hz,
                                 beacon_range_sigma_m=cfg.tower_sigma_m,
                                 min_elev_deg=cfg.min_elev_deg)
        self.sky = constellation()

    def set_layout(self, anchor: Anchor, towers_enu: np.ndarray):
        """Move the whole scene: a new anchor point and a new set of ground towers."""
        self.anchor = anchor
        self.towers = (np.array([anchor.to_ecef(t) for t in towers_enu])
                       if len(towers_enu) else np.zeros((0, 3)))
        if len(self.towers):
            lat, lon, _ = ecef_to_geodetic(self.towers)
            self.tower_up = up_vector(np.radians(lat), np.radians(lon))
        else:
            self.tower_up = np.zeros((0, 3))

    # ---- per flight ---------------------------------------------------------
    def reset(self, rng: np.random.Generator, p_ecef: np.ndarray, v_ecef: np.ndarray, t0: float):
        c = self.cfg
        self.rng = rng
        self.t0 = t0
        self.bias = rng.normal(0, c.imu_bias_sigma, 3)          # the IMU's constant offset
        self.ahrs_bias = np.radians(rng.normal(0, c.ahrs_bias_deg, 3))
        self.clock = c.clock_bias0_hz
        self.sat_err = (rng.normal(0, c.orbit_err_m, (self.sky.p.shape[0], 3))
                        if c.orbit_err_m > 0 else None)
        # the filter starts from the last GPS fix, which was a little bit wrong
        self.x = np.zeros(N_STATE)
        self.x[0:3] = p_ecef + rng.normal(0, c.init_pos_sigma, 3)
        self.x[3:6] = v_ecef + rng.normal(0, c.init_vel_sigma, 3)
        self.P = np.diag([c.init_pos_sigma ** 2] * 3 + [c.init_vel_sigma ** 2] * 3
                         + [2000.0 ** 2] + [0.03 ** 2] * 3)

    # ---- per tick -----------------------------------------------------------
    def update(self, p_ecef, v_ecef, a_ecef, t, bank, gamma, heading, airspeed) -> Reading:
        c = self.cfg
        rng = self.rng

        # --- inertial: the filter integrates this, bias and all ---
        imu = a_ecef + self.bias + rng.normal(0, c.imu_noise, 3)
        self.x, self.P = ekf_predict(self.fcfg, self.x, self.P, imu)

        # --- satellites ---
        sp, sv = self.sky.at(self.t0 + t)
        lat, lon, _ = ecef_to_geodetic(p_ecef)
        up = up_vector(np.radians(lat), np.radians(lon))
        rel = sp - p_ecef
        rho = np.linalg.norm(rel, axis=1)
        elev = np.degrees(np.arcsin(rel @ up / rho))
        vis = np.where(elev > c.min_elev_deg)[0]
        vis = vis[np.argsort(-elev[vis])]                        # highest first

        self.clock += rng.normal(0, c.clock_rw_hz_rts * np.sqrt(c.dt))
        dirs = np.zeros((c.max_sats, 3))
        dopp = np.zeros(c.max_sats)
        sval = np.zeros(c.max_sats)
        sat_ids = []
        for k, s in enumerate(vis):
            u = rel[s] / rho[s]
            rr = (sv[s] - v_ecef) @ u                        # closing speed along the line
            z = -self.fcfg.fc_hz / C * rr + self.clock + rng.normal(0, c.doppler_sigma_hz)
            p_filter = sp[s] + self.sat_err[s] if self.sat_err is not None else sp[s]
            self.x, self.P, _ = ekf_update_doppler(self.fcfg, self.x, self.P, z, p_filter, sv[s])
            if k < c.max_sats:
                dirs[k], dopp[k], sval[k] = u, z / 1000.0, 1.0
                sat_ids.append(int(s))

        # --- ground towers (two-way ranging: no clock term) ---
        trange = np.zeros(c.max_towers)
        tval = np.zeros(c.max_towers)
        tower_ids = []
        n_towers = 0
        for b in range(len(self.towers)):
            d = p_ecef - self.towers[b]
            r = np.linalg.norm(d)
            if r > c.tower_max_km * 1000.0 or np.degrees(np.arcsin(d @ self.tower_up[b] / r)) < 1.0:
                continue
            z = r + rng.normal(0, c.tower_sigma_m)
            self.x, self.P = ekf_update_range(self.fcfg, self.x, self.P, z, self.towers[b])
            if n_towers < c.max_towers:
                trange[n_towers], tval[n_towers] = z, 1.0
                tower_ids.append(b)
            n_towers += 1

        n = np.radians(rng.normal(0, c.ahrs_noise_deg, 3)) + self.ahrs_bias
        return Reading(
            est_pos=self.x[0:3].copy(), est_vel=self.x[3:6].copy(),
            pos_sigma=float(np.sqrt(np.trace(self.P[0:3, 0:3]) / 3.0)),
            imu_accel=imu, bank=bank + n[0], gamma=gamma + n[1], heading=heading + n[2],
            airspeed=airspeed + rng.normal(0, c.airspeed_noise),
            sat_dirs=dirs, sat_doppler=dopp, sat_valid=sval,
            tower_range=trange, tower_valid=tval,
            n_sats=int(len(vis)), n_towers=n_towers,
            sat_ids=sat_ids, tower_ids=tower_ids,
        )
