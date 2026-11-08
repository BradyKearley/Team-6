#!/usr/bin/env python3
"""
IMU + Iridium Doppler navigation simulation (no hardware, no SDR needed).

Question this answers: if an aircraft loses GPS and only has an IMU, can the
Doppler shift of Iridium satellite signals be used to keep the position
estimate from drifting away?

What the script does
--------------------
1. Flies a simulated aircraft (level flight, two turns, a small climb) and
   records the TRUE position, velocity and attitude.
2. Turns that flight into what a strapdown IMU would measure in the BODY frame
   (specific force + angular rate), then corrupts it with realistic errors:
   bias, bias drift, white noise and scale-factor error. Presets: mpu6050
   (after a 6-position calibration), consumer, tactical.
3. Dead-reckons with those IMU samples (attitude, gravity, Coriolis, Earth
   rotation all handled in the ECEF frame). This is the "IMU only" result.
4. Simulates the Doppler an Iridium receiver would measure from the visible
   satellites (real TLEs through skyfield if available, otherwise a synthetic
   66-satellite Iridium-like constellation), including receiver clock offset
   and satellite orbit error (TLE error).
5. Fuses IMU + Doppler (+ optional barometer) in an error-state Extended
   Kalman Filter that estimates position, velocity, attitude, accelerometer
   bias, gyro bias and the receiver clock offset, with outlier gating.
6. Prints IMU-only vs fused position error, saves a CSV and a plot.

Usage
-----
    python3 nav_sim.py                          # 20 min GPS outage, MPU6050-class IMU
    python3 nav_sim.py --imu consumer           # better IMU
    python3 nav_sim.py --imu tactical           # tactical-grade IMU
    python3 nav_sim.py --no-doppler             # baro-aided IMU only (shows what Doppler adds)
    python3 nav_sim.py --no-doppler --no-baro   # IMU + filter, no aiding at all
    python3 nav_sim.py --orbit-err 0            # perfect satellite orbits
    python3 nav_sim.py --orbit-err 2000         # poor / old TLEs
    python3 nav_sim.py --runs 5                 # repeat with 5 random seeds, summary table
    python3 nav_sim.py --minutes 40 --no-plot

Needs: numpy (always), matplotlib (plots, optional), skyfield (real TLEs, optional).
Without skyfield or a TLE file it falls back to a synthetic Iridium-like constellation.

Assumptions you should know about (edit IMU_PRESETS / Config to match your hardware):
  * IMU error numbers are approximate datasheet-style values, not measurements of your unit.
  * The Doppler "measurement" is simulated. No RF processing is involved.
  * Light-time (signal travel time) and ionosphere effects are not modelled.
  * The filter is told which satellite each Doppler came from and its (imperfect) orbit.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time
import urllib.request
from dataclasses import dataclass, field, replace

import numpy as np

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------
C_LIGHT = 299_792_458.0
WGS_A = 6378137.0
WGS_F = 1 / 298.257223563
WGS_E2 = WGS_F * (2 - WGS_F)
MU = 3.986004418e14           # Earth gravitational parameter [m^3/s^2]
J2 = 1.08262668e-3
W_IE = 7.2921150e-5           # Earth rotation rate [rad/s]
G0 = 9.80665

HERE = os.path.dirname(os.path.abspath(__file__))
TLE_NAME = "iridium_next.tle"
TLE_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP=iridium-NEXT&FORMAT=tle"

# error-state layout: [dpos(3), dvel(3), dtheta(3), accel_bias(3), gyro_bias(3), clock(1)]
NS = 16
I_P, I_V, I_A, I_BA, I_BG, I_CK = slice(0, 3), slice(3, 6), slice(6, 9), slice(9, 12), slice(12, 15), 15

DEG = math.pi / 180.0

# IMU error models. Units: accel [m/s^2], gyro [rad/s]. "bias" = 1-sigma turn-on residual
# (what is left AFTER calibration), "rw" = bias random walk per sqrt(s), "noise" = white
# noise density per sqrt(Hz), "sf" = 1-sigma scale factor error (fraction).
IMU_PRESETS = {
    "mpu6050": dict(accel_bias=0.03, accel_rw=2e-4, accel_noise=3.9e-3, accel_sf=1e-3,
                    gyro_bias=0.02 * DEG, gyro_rw=2e-6, gyro_noise=0.005 * DEG, gyro_sf=1e-3),
    "consumer": dict(accel_bias=0.01, accel_rw=5e-5, accel_noise=7e-4, accel_sf=3e-4,
                     gyro_bias=0.005 * DEG, gyro_rw=5e-7, gyro_noise=0.0028 * DEG, gyro_sf=2e-4),
    "tactical": dict(accel_bias=1e-3, accel_rw=1e-5, accel_noise=2e-4, accel_sf=1e-4,
                     gyro_bias=1.0 * DEG / 3600, gyro_rw=1e-8, gyro_noise=0.00017 * DEG, gyro_sf=5e-5),
}


# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
@dataclass
class Config:
    # run
    minutes: float = 20.0
    imu_rate_hz: float = 20.0       # IMU sample rate used in the simulation
    meas_dt: float = 1.0            # filter / Doppler / baro epoch spacing [s]
    imu: str = "mpu6050"
    seed: int = 42

    # truth flight
    start_lat: float = 45.0
    start_lon: float = -60.0
    alt_m: float = 10_000.0
    speed: float = 250.0            # true airspeed [m/s]
    heading_deg: float = 50.0
    # (start_s, heading change deg, duration s): smooth coordinated turns
    turns: tuple = ((300.0, 90.0, 60.0), (700.0, -60.0, 40.0))
    climb_start_s: float = 950.0
    climb_m: float = 600.0
    climb_dur_s: float = 60.0

    # Doppler / satellites
    use_doppler: bool = True
    fc_hz: float = 1626.0e6
    doppler_sigma_hz: float = 5.0   # measurement noise (1 sigma)
    clock_bias0_hz: float = 800.0   # true initial oscillator offset
    clock_rw_hz_rts: float = 0.2    # true oscillator random walk [Hz/sqrt(s)]
    orbit_err_m: float = 1000.0     # along-track orbit (TLE) error the filter suffers, 1 sigma
    min_elev_deg: float = 10.0
    max_sats: int = 4               # at most this many Doppler measurements per epoch

    # barometer
    baro_sigma: float = 15.0        # m, 0 = no barometer
    baro_rw: float = 0.3            # m/sqrt(s) true weather drift of the baro

    # filter tuning
    gate_sigma: float = 4.0         # reject measurements with |innovation| > gate * sigma
    q_scale: float = 2.0            # multiply the assumed IMU noise by this (safety margin)
    r_doppler: float | None = None  # filter's assumed Doppler sigma; None = automatic
    init_pos_sigma: float = 20.0    # m (last GPS fix)
    init_vel_sigma: float = 0.2     # m/s
    init_tilt_deg: float = 0.3      # roll/pitch error at the start of the outage
    init_yaw_deg: float = 1.0       # heading error at the start of the outage
    init_clk_sigma: float = 2000.0  # Hz
    q_clock: float = 0.5            # Hz/sqrt(s) assumed clock random walk

    @property
    def n_epochs(self) -> int:
        return int(round(self.minutes * 60.0 / self.meas_dt)) + 1


# ----------------------------------------------------------------------------
# Small math helpers
# ----------------------------------------------------------------------------
def skew(v):
    return np.array([[0.0, -v[2], v[1]], [v[2], 0.0, -v[0]], [-v[1], v[0], 0.0]])


def exp_so3(w):
    """Rotation matrix for the rotation vector w (Rodrigues)."""
    a2 = float(w @ w)
    K = skew(w)
    if a2 < 1e-18:
        return np.eye(3) + K
    a = math.sqrt(a2)
    return np.eye(3) + (math.sin(a) / a) * K + ((1.0 - math.cos(a)) / a2) * (K @ K)


def geodetic_to_ecef(lat, lon, h):
    n = WGS_A / np.sqrt(1 - WGS_E2 * np.sin(lat) ** 2)
    return np.stack([(n + h) * np.cos(lat) * np.cos(lon),
                     (n + h) * np.cos(lat) * np.sin(lon),
                     (n * (1 - WGS_E2) + h) * np.sin(lat)], axis=-1)


def ecef_to_geodetic(p):
    """ECEF [m] -> (lat rad, lon rad, height m). Works on (3,) or (n,3)."""
    p = np.asarray(p, dtype=float)
    x, y, z = p[..., 0], p[..., 1], p[..., 2]
    lon = np.arctan2(y, x)
    r = np.hypot(x, y)
    lat = np.arctan2(z, r * (1 - WGS_E2))
    h = 0.0
    for _ in range(6):
        n = WGS_A / np.sqrt(1 - WGS_E2 * np.sin(lat) ** 2)
        h = r / np.cos(lat) - n
        lat = np.arctan2(z, r * (1 - WGS_E2 * n / (n + h)))
    return lat, lon, h


def up_vector(lat, lon):
    return np.stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)], axis=-1)


def dcm_ned_to_ecef(lat, lon):
    sl, cl, so, co = np.sin(lat), np.cos(lat), np.sin(lon), np.cos(lon)
    C = np.zeros(np.shape(lat) + (3, 3))
    C[..., 0, 0], C[..., 1, 0], C[..., 2, 0] = -sl * co, -sl * so, cl      # north
    C[..., 0, 1], C[..., 1, 1], C[..., 2, 1] = -so, co, 0.0                # east
    C[..., 0, 2], C[..., 1, 2], C[..., 2, 2] = -cl * co, -cl * so, -sl     # down
    return C


def dcm_body_to_ned(roll, pitch, yaw):
    cr, sr, cp, sp, cy, sy = np.cos(roll), np.sin(roll), np.cos(pitch), np.sin(pitch), np.cos(yaw), np.sin(yaw)
    C = np.zeros(np.shape(roll) + (3, 3))
    C[..., 0, 0], C[..., 0, 1], C[..., 0, 2] = cp * cy, sr * sp * cy - cr * sy, cr * sp * cy + sr * sy
    C[..., 1, 0], C[..., 1, 1], C[..., 1, 2] = cp * sy, sr * sp * sy + cr * cy, cr * sp * sy - sr * cy
    C[..., 2, 0], C[..., 2, 1], C[..., 2, 2] = -sp, sr * cp, cr * cp
    return C


def gravity_ecef(p):
    """Plumb-bob gravity (J2 gravitation + centrifugal) in ECEF. Works on (3,) or (n,3)."""
    x, y, z = p[..., 0], p[..., 1], p[..., 2]
    r2 = x * x + y * y + z * z
    r = np.sqrt(r2)
    k = -MU / (r2 * r)
    j = 1.5 * J2 * (WGS_A / r) ** 2
    zz = 5.0 * z * z / r2
    gx = k * x * (1 + j * (1 - zz)) + W_IE ** 2 * x
    gy = k * y * (1 + j * (1 - zz)) + W_IE ** 2 * y
    gz = k * z * (1 + j * (3 - zz))
    return np.stack([gx, gy, gz], axis=-1)


def gravity_gradient(p):
    r = float(np.linalg.norm(p))
    u = p / r
    return -(MU / r ** 3) * (np.eye(3) - 3.0 * np.outer(u, u)) + W_IE ** 2 * np.diag([1.0, 1.0, 0.0])


def rotvec_from_dcm(R):
    """Rotation vectors for a stack of small rotation matrices (n,3,3) -> (n,3)."""
    vee = 0.5 * np.stack([R[:, 2, 1] - R[:, 1, 2], R[:, 0, 2] - R[:, 2, 0], R[:, 1, 0] - R[:, 0, 1]], axis=-1)
    s = np.linalg.norm(vee, axis=1)
    c = 0.5 * (np.trace(R, axis1=1, axis2=2) - 1.0)
    ang = np.arctan2(s, c)
    scale = np.where(s > 1e-12, ang / np.maximum(s, 1e-12), 1.0)
    return vee * scale[:, None]


def strapdown_step(C, v, p, f_b, w_ib, dt):
    """One ECEF-frame strapdown step. C: body->ECEF, f_b: specific force, w_ib: gyro rate."""
    w_eb = w_ib - W_IE * C[2]                 # remove Earth rotation seen in the body frame
    C_new = C @ exp_so3(w_eb * dt)
    acc = C @ f_b + gravity_ecef(p)
    acc[0] += 2.0 * W_IE * v[1]               # - 2 w_ie x v  (Coriolis)
    acc[1] -= 2.0 * W_IE * v[0]
    v_new = v + acc * dt
    return C_new, v_new, p + 0.5 * (v + v_new) * dt


def smoothstep(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


def smoothstep_rate(x):
    inside = (x > 0) & (x < 1)
    return np.where(inside, 6 * x * (1 - x), 0.0)


# ----------------------------------------------------------------------------
# 1. Truth flight and the IMU data it would produce
# ----------------------------------------------------------------------------
def make_truth(cfg: Config, n_steps: int, dt: float):
    """Truth states at n_steps+1 instants: ECEF pos/vel, body->ECEF DCM, geodetic."""
    t = np.arange(n_steps + 1) * dt
    psi = np.full_like(t, cfg.heading_deg * DEG)
    psi_dot = np.zeros_like(t)
    for start, dpsi, dur in cfg.turns:
        x = (t - start) / dur
        psi += dpsi * DEG * smoothstep(x)
        psi_dot += dpsi * DEG * smoothstep_rate(x) / dur
    xc = (t - cfg.climb_start_s) / cfg.climb_dur_s
    alt = cfg.alt_m + cfg.climb_m * smoothstep(xc)
    h_dot = cfg.climb_m * smoothstep_rate(xc) / cfg.climb_dur_s

    gamma = np.arcsin(np.clip(h_dot / cfg.speed, -0.5, 0.5))       # flight path angle
    roll = np.clip(np.arctan(cfg.speed * psi_dot / G0), -60 * DEG, 60 * DEG)   # coordinated turn
    vg = cfg.speed * np.cos(gamma)

    lat = np.zeros_like(t)
    lon = np.zeros_like(t)
    lat[0], lon[0] = cfg.start_lat * DEG, cfg.start_lon * DEG
    for k in range(n_steps):
        sl = math.sin(lat[k])
        w = math.sqrt(1 - WGS_E2 * sl * sl)
        N = WGS_A / w
        M = WGS_A * (1 - WGS_E2) / w ** 3
        lat[k + 1] = lat[k] + vg[k] * math.cos(psi[k]) * dt / (M + alt[k])
        lon[k + 1] = lon[k] + vg[k] * math.sin(psi[k]) * dt / ((N + alt[k]) * math.cos(lat[k]))

    p = geodetic_to_ecef(lat, lon, alt)
    v = np.gradient(p, dt, axis=0)
    Cbn = dcm_body_to_ned(roll, gamma, psi)
    C = np.einsum("nij,njk->nik", dcm_ned_to_ecef(lat, lon), Cbn)
    return dict(t=t, lat=lat, lon=lon, alt=alt, p=p, v=v, C=C)


def ideal_imu(truth, dt):
    """Error-free specific force and gyro rate that exactly reproduce the truth when
    fed through strapdown_step (so any drift you see later is caused by IMU errors)."""
    p, v, C = truth["p"], truth["v"], truth["C"]
    a_tot = (v[1:] - v[:-1]) / dt - gravity_ecef(p[:-1])
    a_tot[:, 0] += -2.0 * W_IE * v[:-1, 1]
    a_tot[:, 1] += 2.0 * W_IE * v[:-1, 0]
    f_b = np.einsum("nji,nj->ni", C[:-1], a_tot)
    R = np.einsum("nji,njk->nik", C[:-1], C[1:])
    w_ib = rotvec_from_dcm(R) / dt + W_IE * C[:-1, 2, :]
    return f_b, w_ib


def corrupt_imu(cfg, rng, f_b, w_ib, dt):
    m = IMU_PRESETS[cfg.imu]
    n = len(f_b)
    sq = math.sqrt(dt)
    b_a0 = rng.normal(0, m["accel_bias"], 3)
    b_g0 = rng.normal(0, m["gyro_bias"], 3)
    b_a = b_a0 + np.cumsum(rng.normal(0, m["accel_rw"] * sq, (n, 3)), axis=0)
    b_g = b_g0 + np.cumsum(rng.normal(0, m["gyro_rw"] * sq, (n, 3)), axis=0)
    sf_a = rng.normal(0, m["accel_sf"], 3)
    sf_g = rng.normal(0, m["gyro_sf"], 3)
    f_m = f_b * (1 + sf_a) + b_a + rng.normal(0, m["accel_noise"] / sq, (n, 3))
    w_m = w_ib * (1 + sf_g) + b_g + rng.normal(0, m["gyro_noise"] / sq, (n, 3))
    return f_m, w_m


# ----------------------------------------------------------------------------
# 2. Satellites: real TLEs (skyfield) or a synthetic Iridium-like constellation
# ----------------------------------------------------------------------------
def synthetic_iridium(n_ep, dt):
    a = WGS_A + 780e3
    nm = math.sqrt(MU / a ** 3)
    inc = math.radians(86.4)
    t = np.arange(n_ep) * dt
    th = W_IE * t
    c, s = np.cos(th), np.sin(th)

    def to_ecef(vv):
        return np.stack([c * vv[:, 0] + s * vv[:, 1], -s * vv[:, 0] + c * vv[:, 1], vv[:, 2]], axis=-1)

    sp, sv, names = [], [], []
    for pl in range(6):
        raan = math.radians(31.6 * pl)
        P = np.array([math.cos(raan), math.sin(raan), 0.0])
        Q = np.array([-math.sin(raan) * math.cos(inc), math.cos(raan) * math.cos(inc), math.sin(inc)])
        for k in range(11):
            u = 2 * math.pi * (k / 11 + (pl % 2) * 0.5 / 11) + nm * t
            r_i = a * (np.cos(u)[:, None] * P + np.sin(u)[:, None] * Q)
            v_i = a * nm * (-np.sin(u)[:, None] * P + np.cos(u)[:, None] * Q)
            r_e = to_ecef(r_i)
            v_e = to_ecef(v_i) - np.cross([0.0, 0.0, W_IE], r_e)
            sp.append(r_e)
            sv.append(v_e)
            names.append(f"SYN-{pl + 1}-{k + 1:02d}")
    return np.array(sp), np.array(sv), names, {"source": "synthetic Iridium-like constellation (66 sats)"}


def read_tle_lines(path):
    with open(path, errors="ignore") as f:
        raw = f.read().splitlines()
    out = []
    for ln in raw:
        ln = ln.strip().rstrip("\\").rstrip("}").strip()    # also copes with .rtf copies
        if ln:
            out.append(ln)
    return out


def find_tle(explicit):
    # Default: look for the file you saved from Celestrak under any of its common names
    # (iridium_next.tle, or what a browser/wget calls it: gp.php, gp.txt, ...).
    names = [explicit] if explicit else [TLE_NAME, "iridium-NEXT", "iridium_next", "gp"]
    for n in names:
        suffixes = ("", ".txt", ".rtf", ".tle", ".php")
        if os.path.isabs(n):
            dirs = [""]
        else:
            dirs = [os.getcwd(), HERE, os.path.join(HERE, "data"), os.path.join(os.getcwd(), "data"),
                    os.path.join(HERE, "app"), os.path.join(os.getcwd(), "app")]
        for d in dirs:
            for suf in suffixes:
                path = os.path.join(d, n + suf) if d else n + suf
                if os.path.exists(path):
                    return path
    return None


def tle_constellation(path, n_ep, dt):
    from skyfield.api import load, EarthSatellite
    from skyfield.framelib import itrs

    ts = load.timescale()
    lines = read_tle_lines(path)
    sats = []
    for i in range(1, len(lines) - 1):
        if lines[i].startswith("1 ") and lines[i + 1].startswith("2 "):
            name = lines[i - 1] if not lines[i - 1].startswith(("1 ", "2 ")) else "UNKNOWN"
            sats.append(EarthSatellite(lines[i], lines[i + 1], name.strip(), ts))
    if not sats:
        raise RuntimeError(f"no satellites found in {os.path.basename(path)}. Expected Celestrak's TLE format: "
                           "a name line, then lines starting with '1 ' and '2 '")
    epochs = np.array([s.epoch.tt for s in sats])
    med = float(np.median(epochs))
    age_days = float(ts.now().tt - med)
    if age_days > 7:
        print(f"Note: the TLE file is {age_days:.0f} days old. Orbit accuracy degrades with age, "
              "so re-download it for best results.")
    kept = [s for s, e in zip(sats, epochs) if abs(e - med) < 3.0]
    times = ts.tt_jd(med + np.arange(n_ep) * dt / 86400.0)
    sp, sv = [], []
    for s in kept:
        pos, vel = s.at(times).frame_xyz_and_velocity(itrs)
        sp.append(pos.km.T * 1000.0)
        sv.append(vel.km_per_s.T * 1000.0)
    info = {"source": f"TLE file {os.path.basename(path)} ({len(kept)} sats, epoch {ts.tt_jd(med).utc_iso()}, "
                      f"{age_days:.1f} days old)"}
    return np.array(sp), np.array(sv), [s.name for s in kept], info


def load_constellation(cfg, n_ep, dt, tle=None, synthetic=False, offline=False):
    if not synthetic:
        try:
            import skyfield  # noqa: F401
        except ImportError:
            print("Note: 'skyfield' is not installed, so a built-in synthetic Iridium-like constellation is used.")
            print("      For real satellite orbits run:  pip3 install skyfield --break-system-packages")
            print("      (or use a virtual environment), then run this script again.\n")
            return synthetic_iridium(n_ep, dt)
        try:
            path = find_tle(tle)
            if path is None and not offline and not tle:
                dest = os.path.join(HERE, TLE_NAME)
                print(f"Downloading Iridium TLEs from Celestrak -> {dest}")
                with urllib.request.urlopen(TLE_URL, timeout=20) as r:
                    text = r.read().decode("utf-8", errors="ignore")
                if "1 " not in text:
                    raise RuntimeError("Celestrak did not return TLE data")
                with open(dest, "w", encoding="utf-8") as f:
                    f.write(text)
                path = dest
            if path is not None:
                return tle_constellation(path, n_ep, dt)
            print("No TLE file found; using the synthetic constellation.")
        except Exception as e:  # no network, bad file...
            print(f"Could not load real TLE data ({e}); using the synthetic constellation instead.\n")
    return synthetic_iridium(n_ep, dt)


# ----------------------------------------------------------------------------
# 3. The simulation: dead reckoning vs error-state EKF
# ----------------------------------------------------------------------------
@dataclass
class Result:
    cfg: Config
    sat_info: dict
    t: np.ndarray
    p_true: np.ndarray
    p_fused: np.ndarray
    p_dr: np.ndarray
    err_fused: np.ndarray
    err_dr: np.ndarray
    herr_fused: np.ndarray
    verr_fused: np.ndarray
    att_err_fused: np.ndarray      # degrees
    att_err_dr: np.ndarray
    sigma_pos: np.ndarray
    n_used: np.ndarray             # Doppler measurements accepted per epoch
    n_rejected: np.ndarray
    n_visible: np.ndarray
    clock_true: np.ndarray
    clock_est: np.ndarray
    ba_est: np.ndarray
    bg_est: np.ndarray
    lat_t: np.ndarray
    lon_t: np.ndarray
    alt_t: np.ndarray
    runtime_s: float = 0.0


def angle_between(C1, C2):
    c = 0.5 * (np.trace(C1.T @ C2) - 1.0)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def simulate(cfg: Config, sats=None, tle=None, synthetic=False, offline=False, verbose=True) -> Result:
    started = time.perf_counter()
    rng = np.random.default_rng(cfg.seed)

    ratio = max(1, int(round(cfg.meas_dt * cfg.imu_rate_hz)))
    dt = 1.0 / cfg.imu_rate_hz
    cfg = replace(cfg, meas_dt=ratio * dt)
    n_ep = cfg.n_epochs
    n_steps = (n_ep - 1) * ratio
    T_f = cfg.meas_dt
    k_hz = cfg.fc_hz / C_LIGHT                                   # Hz per (m/s) of range rate
    imu_m = IMU_PRESETS[cfg.imu]

    # --- truth + IMU measurements -------------------------------------------------------
    truth = make_truth(cfg, n_steps, dt)
    f_ideal, w_ideal = ideal_imu(truth, dt)
    f_meas, w_meas = corrupt_imu(cfg, rng, f_ideal, w_ideal, dt)
    ep = np.arange(n_ep) * ratio
    p_ep, v_ep = truth["p"][ep], truth["v"][ep]
    lat_ep, lon_ep, alt_ep = truth["lat"][ep], truth["lon"][ep], truth["alt"][ep]
    up_ep = up_vector(lat_ep, lon_ep)

    # --- satellites -------------------------------------------------------------------------
    if sats is None:
        sats = load_constellation(cfg, n_ep, T_f, tle, synthetic, offline)
    SP, SV, names, sat_info = sats
    n_sat = len(names)
    if SP.shape[1] != n_ep:
        raise RuntimeError("constellation was built for a different run length")

    # The filter's (imperfect) knowledge of each satellite: its true state a little earlier or
    # later along the orbit, i.e. an along-track error (what an old/inaccurate TLE gives).
    if cfg.orbit_err_m > 0:
        tau = rng.normal(0, cfg.orbit_err_m / 7500.0, n_sat)[:, None, None]
        acc = np.gradient(SV, T_f, axis=1)
        SPf = SP + SV * tau + 0.5 * acc * tau ** 2
        SVf = SV + acc * tau
    else:
        SPf, SVf = SP, SV

    # --- simulated Doppler (truth) --------------------------------------------------------------
    rel = SP - p_ep[None]
    rho = np.linalg.norm(rel, axis=2)
    u_los = rel / rho[..., None]
    elev = np.degrees(np.arcsin(np.einsum("snj,nj->sn", rel, up_ep) / rho))
    visible = elev > cfg.min_elev_deg
    rr = np.sum((SV - v_ep[None]) * u_los, axis=2)
    clock_true = cfg.clock_bias0_hz + np.cumsum(rng.normal(0, cfg.clock_rw_hz_rts * math.sqrt(T_f), n_ep))
    clock_true -= clock_true[0] - cfg.clock_bias0_hz
    z_dop = -k_hz * rr + clock_true[None] + rng.normal(0, cfg.doppler_sigma_hz, rr.shape)
    z_dop = np.where(visible, z_dop, np.nan)
    elev_masked = np.where(visible, elev, -np.inf)

    baro_bias = np.cumsum(rng.normal(0, cfg.baro_rw * math.sqrt(T_f), n_ep))
    baro_bias -= baro_bias[0]
    z_baro = alt_ep + baro_bias + rng.normal(0, max(cfg.baro_sigma, 1e-9), n_ep)

    # What the filter assumes about the Doppler noise: measurement noise plus orbit error.
    # (1 km of along-track error is worth roughly 17 Hz of Doppler error at worst.)
    r_dop = cfg.r_doppler if cfg.r_doppler is not None else math.hypot(cfg.doppler_sigma_hz, 0.017 * cfg.orbit_err_m)

    # --- initial estimate: last GPS fix before the outage ---------------------------------------
    C_ne0 = dcm_ned_to_ecef(lat_ep[0], lon_ep[0])
    phi0 = C_ne0 @ rng.normal(0, [cfg.init_tilt_deg * DEG, cfg.init_tilt_deg * DEG, cfg.init_yaw_deg * DEG])
    C0 = exp_so3(-phi0) @ truth["C"][0]
    v0 = truth["v"][0] + rng.normal(0, cfg.init_vel_sigma, 3)
    p0 = truth["p"][0] + rng.normal(0, cfg.init_pos_sigma, 3)

    Cd, vd, pd = C0.copy(), v0.copy(), p0.copy()       # IMU only (dead reckoning)
    Cn, vn, pn = C0.copy(), v0.copy(), p0.copy()       # fused solution
    ba, bg, clk = np.zeros(3), np.zeros(3), 0.0

    P = np.zeros((NS, NS))
    P[I_P, I_P] = np.eye(3) * cfg.init_pos_sigma ** 2
    P[I_V, I_V] = np.eye(3) * cfg.init_vel_sigma ** 2
    att_n = np.diag([cfg.init_tilt_deg * DEG] * 2 + [cfg.init_yaw_deg * DEG]) ** 2
    P[I_A, I_A] = C_ne0 @ att_n @ C_ne0.T
    P[I_BA, I_BA] = np.eye(3) * imu_m["accel_bias"] ** 2
    P[I_BG, I_BG] = np.eye(3) * imu_m["gyro_bias"] ** 2
    P[I_CK, I_CK] = cfg.init_clk_sigma ** 2

    # continuous-time process noise the filter assumes (true values x safety factor)
    qs = cfg.q_scale
    Qc = np.zeros((NS, NS))
    Qc[I_V, I_V] = np.eye(3) * (qs * imu_m["accel_noise"]) ** 2
    Qc[I_A, I_A] = np.eye(3) * (qs * imu_m["gyro_noise"]) ** 2
    Qc[I_BA, I_BA] = np.eye(3) * (qs * imu_m["accel_rw"]) ** 2
    Qc[I_BG, I_BG] = np.eye(3) * (qs * imu_m["gyro_rw"]) ** 2
    Qc[I_CK, I_CK] = cfg.q_clock ** 2
    Om = skew(np.array([0.0, 0.0, W_IE]))
    eye = np.eye(NS)
    gate2 = cfg.gate_sigma ** 2

    def zeros(): return np.zeros(n_ep)
    p_fused, p_dr = np.zeros((n_ep, 3)), np.zeros((n_ep, 3))
    att_f, att_d, sig = zeros(), zeros(), zeros()
    n_used, n_rej = np.zeros(n_ep, int), np.zeros(n_ep, int)
    clock_est = zeros()
    ba_est, bg_est = np.zeros((n_ep, 3)), np.zeros((n_ep, 3))

    def record(e):
        p_fused[e], p_dr[e] = pn, pd
        att_f[e] = angle_between(Cn, truth["C"][e * ratio])
        att_d[e] = angle_between(Cd, truth["C"][e * ratio])
        sig[e] = math.sqrt(np.trace(P[I_P, I_P]))
        clock_est[e] = clk
        ba_est[e], bg_est[e] = ba, bg

    record(0)
    if verbose:
        print(f"Simulating {cfg.minutes:g} min, {n_steps} IMU steps at {cfg.imu_rate_hz:g} Hz, "
              f"IMU '{cfg.imu}', {n_sat} satellites, Doppler {'on' if cfg.use_doppler else 'off'}, "
              f"baro {'on' if cfg.baro_sigma > 0 else 'off'}")

    for k in range(n_steps):
        f_k, w_k = f_meas[k], w_meas[k]
        Cd, vd, pd = strapdown_step(Cd, vd, pd, f_k, w_k, dt)
        Cn, vn, pn = strapdown_step(Cn, vn, pn, f_k - ba, w_k - bg, dt)
        if (k + 1) % ratio:
            continue
        e = (k + 1) // ratio

        # ---- covariance propagation over one epoch -------------------------------------
        a_sf = Cn @ (f_k - ba)
        F = np.zeros((NS, NS))
        F[I_P, I_V] = np.eye(3)
        F[I_V, I_P] = gravity_gradient(pn)
        F[I_V, I_V] = -2.0 * Om
        F[I_V, I_A] = -skew(a_sf)
        F[I_V, I_BA] = -Cn
        F[I_A, I_A] = -Om
        F[I_A, I_BG] = -Cn
        FT = F * T_f
        Phi = eye + FT + 0.5 * FT @ FT
        P = Phi @ P @ Phi.T + 0.5 * (Phi @ Qc @ Phi.T + Qc) * T_f

        def update(H, y, R):
            """Scalar measurement update with gating and immediate feedback. Returns accepted?"""
            nonlocal P, pn, vn, Cn, ba, bg, clk
            S = float(H @ P @ H) + R
            if y * y > gate2 * S:
                return False
            K = (P @ H) / S
            dx = K * y
            IKH = eye - np.outer(K, H)
            P = IKH @ P @ IKH.T + R * np.outer(K, K)
            P = 0.5 * (P + P.T)
            pn = pn + dx[I_P]
            vn = vn + dx[I_V]
            Cn = exp_so3(dx[I_A]) @ Cn
            ba = ba + dx[I_BA]
            bg = bg + dx[I_BG]
            clk += dx[I_CK]
            return True

        # ---- Doppler updates --------------------------------------------------------------
        nv = int(visible[:, e].sum())
        if cfg.use_doppler and nv:
            order = np.argsort(-elev_masked[:, e])[:min(cfg.max_sats, nv)]
            for s in order:
                d = SPf[s, e] - pn
                rho_s = float(np.linalg.norm(d))
                u = d / rho_s
                dv = SVf[s, e] - vn
                H = np.zeros(NS)
                H[I_P] = k_hz * (np.eye(3) - np.outer(u, u)) @ dv / rho_s
                H[I_V] = k_hz * u
                H[I_CK] = 1.0
                y = z_dop[s, e] - (-k_hz * float(u @ dv) + clk)
                if update(H, y, r_dop ** 2):
                    n_used[e] += 1
                else:
                    n_rej[e] += 1

        # ---- barometer update ---------------------------------------------------------------
        if cfg.baro_sigma > 0:
            lat_e, lon_e, h_e = ecef_to_geodetic(pn)
            H = np.zeros(NS)
            H[I_P] = up_vector(lat_e, lon_e)
            update(H, z_baro[e] - float(h_e), cfg.baro_sigma ** 2)

        record(e)

    # --- errors ------------------------------------------------------------------------------------
    err_vec = p_fused - p_ep
    v_err = np.einsum("nj,nj->n", err_vec, up_ep)
    err_fused = np.linalg.norm(err_vec, axis=1)
    return Result(
        cfg=cfg, sat_info=sat_info, t=np.arange(n_ep) * T_f, p_true=p_ep, p_fused=p_fused, p_dr=p_dr,
        err_fused=err_fused, err_dr=np.linalg.norm(p_dr - p_ep, axis=1),
        herr_fused=np.sqrt(np.maximum(err_fused ** 2 - v_err ** 2, 0.0)), verr_fused=np.abs(v_err),
        att_err_fused=att_f, att_err_dr=att_d, sigma_pos=sig, n_used=n_used, n_rejected=n_rej,
        n_visible=visible.sum(axis=0), clock_true=clock_true, clock_est=clock_est,
        ba_est=ba_est, bg_est=bg_est, lat_t=lat_ep, lon_t=lon_ep, alt_t=alt_ep,
        runtime_s=time.perf_counter() - started,
    )


# ----------------------------------------------------------------------------
# 4. Reporting
# ----------------------------------------------------------------------------
def checkpoints(res):
    out = []
    for minute in (1, 2, 5, 10, 15, 20, 30, 45, 60):
        i = int(round(minute * 60 / res.cfg.meas_dt))
        if i >= len(res.t):
            break
        out.append((minute, res.err_dr[i], res.err_fused[i], res.herr_fused[i], res.verr_fused[i],
                    res.att_err_dr[i], res.att_err_fused[i]))
    return out


def report(res, csv_path=None, png_path=None, plot=True):
    cfg = res.cfg
    for _p in (csv_path, png_path):
        if _p:
            os.makedirs(os.path.dirname(os.path.abspath(_p)), exist_ok=True)
    n = len(res.t)
    print(f"\nSatellites: {res.sat_info['source']}")
    print(f"IMU: {cfg.imu}   Doppler: {'on' if cfg.use_doppler else 'off'}   "
          f"baro: {'on' if cfg.baro_sigma > 0 else 'off'}   orbit error: {cfg.orbit_err_m:g} m   "
          f"(ran in {res.runtime_s:.1f} s)")
    print(f"Satellites in view: min {res.n_visible.min()}, mean {res.n_visible.mean():.1f}, max {res.n_visible.max()}; "
          f"Doppler accepted {int(res.n_used.sum())}, rejected {int(res.n_rejected.sum())}")
    print(f"Sensitivity: 1 m/s along the line of sight = {cfg.fc_hz / C_LIGHT:.2f} Hz of Doppler\n")
    def dist(m):
        return f"{m / 1000:.1f} km" if m >= 10_000 else f"{m:.0f} m"

    print("Position error vs. truth (smaller is better); attitude error in degrees")
    print(f"{'time':>7} | {'IMU only':>10} | {'FUSED 3D':>10} {'horizontal':>11} {'vertical':>9} | {'att IMU':>8} {'att fused':>9}")
    print("-" * 80)
    for minute, dr, fu, hf, vf, ad, af in checkpoints(res):
        print(f"{minute:>4} min | {dist(dr):>10} | {dist(fu):>10} {dist(hf):>11} {dist(vf):>9} | {ad:>7.1f}° {af:>8.1f}°")
    half = n // 2
    ok = float(np.mean(res.err_fused[1:] <= 3 * res.sigma_pos[1:]))
    print(f"\nAverage error over the second half of the run:  IMU only {dist(res.err_dr[half:].mean())}   "
          f"fused {dist(res.err_fused[half:].mean())}")
    print(f"Filter honesty check: true error stayed inside the filter's own 3-sigma "
          f"{100 * ok:.0f}% of the time (close to 100% is good)")
    print(f"\nBottom line: after {cfg.minutes:g} min without GPS the IMU alone was off by {dist(res.err_dr[-1])}; "
          f"with the filter it was off by {dist(res.err_fused[-1])}.")

    if csv_path:
        lat_e, lon_e, alt_e = ecef_to_geodetic(res.p_fused)
        data = np.column_stack([res.t, np.degrees(lat_e), np.degrees(lon_e), alt_e,
                                np.degrees(res.lat_t), np.degrees(res.lon_t), res.alt_t,
                                res.err_fused, res.err_dr, res.sigma_pos, res.n_used, res.n_rejected])
        np.savetxt(csv_path, data, delimiter=",", fmt="%.6f", comments="",
                   header="time_s,est_lat,est_lon,est_alt_m,true_lat,true_lon,true_alt_m,"
                          "err_fused_m,err_imu_only_m,sigma_pos_m,doppler_used,doppler_rejected")
        print(f"Saved track: {csv_path}")
    if not plot:
        return
    try:
        import matplotlib
        if png_path:
            matplotlib.use("Agg", force=False)
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed; skipping plots (pip3 install matplotlib).")
        return

    tm = res.t / 60.0
    fig, ax = plt.subplots(2, 3, figsize=(18, 8.5))
    ax[0, 0].semilogy(tm, np.maximum(res.err_dr, 1), color="tab:red", label="IMU only (dead reckoning)")
    ax[0, 0].semilogy(tm, np.maximum(res.err_fused, 1), color="tab:green", label="IMU + Doppler + baro (EKF)")
    ax[0, 0].semilogy(tm, 3 * res.sigma_pos, "--", color="tab:green", alpha=0.5, label="filter's own 3-sigma")
    ax[0, 0].set(title="3D position error", xlabel="time [min]", ylabel="error [m]")
    ax[0, 0].legend(); ax[0, 0].grid(alpha=0.3, which="both")

    ax[0, 1].plot(tm, res.herr_fused, label="horizontal")
    ax[0, 1].plot(tm, res.verr_fused, label="vertical")
    ax[0, 1].set(title="Fused error split", xlabel="time [min]", ylabel="error [m]")
    ax[0, 1].legend(); ax[0, 1].grid(alpha=0.3)

    ax[0, 2].semilogy(tm, np.maximum(res.att_err_dr, 1e-3), color="tab:red", label="IMU only")
    ax[0, 2].semilogy(tm, np.maximum(res.att_err_fused, 1e-3), color="tab:green", label="fused")
    ax[0, 2].set(title="Attitude error", xlabel="time [min]", ylabel="degrees")
    ax[0, 2].legend(); ax[0, 2].grid(alpha=0.3, which="both")

    ax[1, 0].step(tm, res.n_visible, where="post", label="in view")
    ax[1, 0].step(tm, res.n_used, where="post", label="Doppler accepted")
    ax[1, 0].step(tm, res.n_rejected, where="post", label="rejected")
    ax[1, 0].set(title="Satellites / measurements per epoch", xlabel="time [min]")
    ax[1, 0].legend(); ax[1, 0].grid(alpha=0.3)

    ax[1, 1].plot(tm, res.clock_true, label="true clock offset")
    ax[1, 1].plot(tm, res.clock_est, "--", label="EKF estimate")
    ax[1, 1].set(title="Receiver clock frequency offset", xlabel="time [min]", ylabel="Hz")
    ax[1, 1].legend(); ax[1, 1].grid(alpha=0.3)

    C0 = dcm_ned_to_ecef(res.lat_t[0], res.lon_t[0])
    def en(p):
        d = (p - res.p_true[0]) @ C0 / 1000.0
        return d[:, 1], d[:, 0]
    ax[1, 2].plot(*en(res.p_true), "k", lw=2, label="truth")
    ax[1, 2].plot(*en(res.p_dr), color="tab:red", label="IMU only")
    ax[1, 2].plot(*en(res.p_fused), color="tab:green", label="fused")
    ax[1, 2].set(title="Ground track", xlabel="east [km]", ylabel="north [km]")
    ax[1, 2].set_aspect("equal", adjustable="datalim")
    ax[1, 2].legend(); ax[1, 2].grid(alpha=0.3)
    plt.tight_layout()
    if png_path:
        plt.savefig(png_path, dpi=120)
        print(f"Saved plot: {png_path}")
    plt.show()


def main(argv=None):
    d = Config()
    ap = argparse.ArgumentParser(description="IMU + Iridium Doppler navigation simulation")
    ap.add_argument("--minutes", type=float, default=d.minutes, help="GPS outage length")
    ap.add_argument("--imu", choices=sorted(IMU_PRESETS), default=d.imu, help="IMU quality preset")
    ap.add_argument("--imu-rate", type=float, default=d.imu_rate_hz, help="simulated IMU rate [Hz]")
    ap.add_argument("--no-doppler", action="store_true", help="do not use satellite Doppler")
    ap.add_argument("--no-baro", action="store_true", help="do not use the barometer")
    ap.add_argument("--baro-sigma", type=float, default=d.baro_sigma, help="baro noise [m]")
    ap.add_argument("--orbit-err", type=float, default=d.orbit_err_m, help="satellite along-track error [m]")
    ap.add_argument("--doppler-sigma", type=float, default=d.doppler_sigma_hz, help="Doppler noise [Hz]")
    ap.add_argument("--r-doppler", type=float, default=None,
                    help="Doppler sigma [Hz] the filter assumes (default: automatic from noise + orbit error)")
    ap.add_argument("--max-sats", type=int, default=d.max_sats, help="Doppler measurements per epoch")
    ap.add_argument("--min-elev", type=float, default=d.min_elev_deg, help="elevation mask [deg]")
    ap.add_argument("--gate", type=float, default=d.gate_sigma, help="outlier gate [sigma]")
    ap.add_argument("--q-scale", type=float, default=d.q_scale, help="inflate assumed IMU noise")
    ap.add_argument("--speed", type=float, default=d.speed, help="aircraft speed [m/s]")
    ap.add_argument("--seed", type=int, default=d.seed)
    ap.add_argument("--runs", type=int, default=1, help="repeat with seed, seed+1, ... and summarise")
    ap.add_argument("--tle", help="TLE file (default: iridium_next.tle next to this script, else download)")
    ap.add_argument("--synthetic", action="store_true", help="force the synthetic constellation")
    ap.add_argument("--offline", action="store_true", help="never try to download TLEs")
    ap.add_argument("--csv", default="results/nav_sim_track.csv", help="output CSV ('' to skip)")
    ap.add_argument("--png", default="results/nav_sim_results.png", help="output plot ('' to only show)")
    ap.add_argument("--no-plot", action="store_true")
    a = ap.parse_args(argv)

    cfg = replace(d, minutes=a.minutes, imu=a.imu, imu_rate_hz=a.imu_rate, use_doppler=not a.no_doppler,
                  baro_sigma=0.0 if a.no_baro else a.baro_sigma, orbit_err_m=a.orbit_err,
                  doppler_sigma_hz=a.doppler_sigma, max_sats=a.max_sats, min_elev_deg=a.min_elev,
                  gate_sigma=a.gate, q_scale=a.q_scale, speed=a.speed, seed=a.seed, r_doppler=a.r_doppler)

    ratio = max(1, int(round(cfg.meas_dt * cfg.imu_rate_hz)))
    n_ep = cfg.n_epochs
    sats = load_constellation(cfg, n_ep, ratio / cfg.imu_rate_hz, a.tle, a.synthetic, a.offline)

    if a.runs <= 1:
        res = simulate(cfg, sats)
        report(res, a.csv or None, a.png or None, plot=not a.no_plot)
        return

    rows = []
    for i in range(a.runs):
        r = simulate(replace(cfg, seed=cfg.seed + i), sats, verbose=False)
        half = len(r.t) // 2
        rows.append((cfg.seed + i, r.err_dr[-1], r.err_fused[-1], r.err_fused[half:].mean(),
                     float(np.mean(r.err_fused[1:] <= 3 * r.sigma_pos[1:]))))
        print(f"seed {rows[-1][0]:>4}: IMU only {rows[-1][1]:>9.0f} m   fused final {rows[-1][2]:>7.0f} m   "
              f"fused mean(2nd half) {rows[-1][3]:>7.0f} m   inside 3-sigma {100 * rows[-1][4]:.0f}%")
    arr = np.array(rows)
    print(f"\nMedian over {a.runs} runs: IMU only {np.median(arr[:, 1]):.0f} m, "
          f"fused final {np.median(arr[:, 2]):.0f} m, worst fused final {arr[:, 2].max():.0f} m")


if __name__ == "__main__":
    main()
