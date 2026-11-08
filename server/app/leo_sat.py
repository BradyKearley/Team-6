"""LEO satellite (Iridium) Doppler + IMU navigation, for when GPS is gone.

The idea: an aircraft loses GPS. It still has an IMU (which drifts badly on its own)
and a radio that can measure the *Doppler shift* of Iridium NEXT downlinks. Each
Doppler measurement says something about the plane's velocity along the line to that
satellite, so an Extended Kalman Filter can fuse them with the IMU and stop the drift.

Two ways to run it:

  python -m app.leo_sat --minutes 20          # CLI: prints a table, saves plots + CSV
  from app.leo_sat import SimConfig, simulate  # server: returns JSON for the web UI

The web UI calls `simulate()` through POST /api/leo/simulate (see app/main.py).
"""
from __future__ import annotations

import logging
import os
import time
import urllib.request
from dataclasses import dataclass, asdict, replace

import numpy as np
from skyfield.api import load, EarthSatellite
from skyfield.framelib import itrs

log = logging.getLogger("leo_sat")

C = 299_792_458.0            # speed of light [m/s]
N_STATE = 10                 # [pos(3), vel(3), clock(1), accel bias(3)]

HERE = os.path.dirname(os.path.abspath(__file__))     # folder this script lives in
TLE_FILE = "iridium_next.tle"                         # cached TLE file, next to this script
TLE_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP=iridium-NEXT&FORMAT=tle"
TLE_MAX_AGE_DAYS = 7.0       # re-download the cache when it gets older than this

# WGS-84 ellipsoid
WGS_A = 6378137.0
WGS_F = 1 / 298.257223563
WGS_E2 = WGS_F * (2 - WGS_F)


@dataclass
class SimConfig:
    """Every knob of the simulation. The web UI sends a subset of these as JSON."""

    # --- run length ---
    minutes: float = 20.0          # how long a GPS outage to simulate
    dt: float = 1.0                # time step [s] (also the filter rate)
    min_elev_deg: float = 10.0     # ignore satellites lower than this above the horizon
    fc_hz: float = 1626.0e6        # Iridium L-band carrier (~1616-1626.5 MHz)

    # --- truth flight ---
    start_lat: float = 45.0        # deg
    start_lon: float = -60.0       # deg
    alt_m: float = 10_000.0
    speed: float = 250.0           # m/s
    heading_deg: float = 50.0      # initial heading (0 = north, 90 = east)
    turn_start_s: float = 400.0    # at this time the plane turns right...
    turn_rate_deg_s: float = 3.0   # ...at this rate...
    turn_total_deg: float = 90.0   # ...by this much

    # --- IMU errors (what makes pure dead-reckoning drift) ---
    accel_bias: tuple[float, float, float] = (0.012, -0.010, 0.008)  # m/s^2, ECEF axes (~2 mg)
    accel_noise: float = 0.005     # m/s^2 white noise per sample

    # --- Doppler measurement errors ---
    doppler_sigma_hz: float = 5.0  # measurement noise (1 sigma)
    clock_bias0_hz: float = 800.0  # true initial oscillator offset (~0.5 ppm at 1.6 GHz)
    clock_rw_hz_rts: float = 0.2   # true clock random walk [Hz / sqrt(s)]
    orbit_err_m: float = 0.0       # error in the satellite positions the filter uses [m]
                                   # (real TLEs are roughly 0.5-2 km off; try 1000)

    # --- filter tuning (how much the filter trusts each source) ---
    init_pos_sigma: float = 20.0   # m    (last GPS fix before the outage)
    init_vel_sigma: float = 0.2    # m/s
    init_clk_sigma: float = 2000.0 # Hz   (we have no idea what the clock offset is)
    init_bias_sigma: float = 0.03  # m/s^2 (we don't know the accelerometer bias either)
    q_accel: float = 0.01          # m/s^2 IMU white noise the filter assumes
    q_bias: float = 2e-4           # m/s^2/sqrt(s) how fast the filter lets the bias wander
    q_clock: float = 0.5           # Hz/sqrt(s) clock random walk assumed by the filter
    r_doppler: float | None = None # what the filter believes the Doppler noise is
                                   # (None = exactly the truth, doppler_sigma_hz)

    seed: int = 42                 # same seed = same run, so the UI is reproducible

    @property
    def steps(self) -> int:
        return max(2, int(round(self.minutes * 60.0 / self.dt)))

    @property
    def r_dop(self) -> float:
        return self.doppler_sigma_hz if self.r_doppler is None else self.r_doppler


# =============================================================================
# 1. Geometry helpers
# =============================================================================
def geodetic_to_ecef(lat, lon, h):
    """lat/lon in radians, h in meters -> ECEF [m]. Works on arrays."""
    n = WGS_A / np.sqrt(1 - WGS_E2 * np.sin(lat) ** 2)
    x = (n + h) * np.cos(lat) * np.cos(lon)
    y = (n + h) * np.cos(lat) * np.sin(lon)
    z = (n * (1 - WGS_E2) + h) * np.sin(lat)
    return np.stack([x, y, z], axis=-1)


def up_vector(lat, lon):
    """Local 'up' (ellipsoid normal) in ECEF."""
    return np.stack([np.cos(lat) * np.cos(lon),
                     np.cos(lat) * np.sin(lon),
                     np.sin(lat)], axis=-1)


def ecef_to_geodetic(p):
    """ECEF [m] -> (lat deg, lon deg, altitude m). Works on (n,3) arrays."""
    x, y, z = p[..., 0], p[..., 1], p[..., 2]
    lon = np.arctan2(y, x)
    r = np.hypot(x, y)
    lat = np.arctan2(z, r * (1 - WGS_E2))
    for _ in range(6):
        n = WGS_A / np.sqrt(1 - WGS_E2 * np.sin(lat) ** 2)
        h = r / np.cos(lat) - n
        lat = np.arctan2(z, r * (1 - WGS_E2 * n / (n + h)))
    return np.degrees(lat), np.degrees(lon), h


# =============================================================================
# 2. Satellite data (TLEs)
# =============================================================================
def find_tle_file(name: str = TLE_FILE) -> str | None:
    """Look next to this script (not the terminal's current folder) for the TLE file."""
    if os.path.isabs(name):
        return name if os.path.exists(name) else None
    for n in (name, name + ".rtf", name + ".txt"):
        path = os.path.join(HERE, n)
        if os.path.exists(path):
            return path
    return None


def download_tle(dest: str | None = None, url: str = TLE_URL, timeout: float = 20.0) -> str:
    """Fetch fresh Iridium NEXT elements from Celestrak and cache them next to this script."""
    dest = dest or os.path.join(HERE, TLE_FILE)
    log.info("downloading TLEs from %s", url)
    with urllib.request.urlopen(url, timeout=timeout) as r:
        text = r.read().decode("utf-8", errors="ignore")
    if "1 " not in text:
        raise RuntimeError("Celestrak did not return TLE data (rate limited?)")
    tmp = dest + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, dest)
    return dest


def tle_lines(path: str) -> list[str]:
    """Read a TLE file (plain text or RTF) into clean lines."""
    with open(path, errors="ignore") as f:
        raw = f.read().splitlines()
    # An .rtf file wraps the same text in formatting codes: each line ends with a "\" and the
    # file ends with "}". Strip those, and the TLE lines come out exactly as in a .txt file.
    lines = []
    for ln in raw:
        ln = ln.strip().rstrip("\\").rstrip("}").strip()
        if ln:
            lines.append(ln)
    return lines


def ensure_tle(path: str | None = None, refresh: bool = False, max_age_days: float = TLE_MAX_AGE_DAYS) -> tuple[str, str]:
    """Return (path to a usable TLE file, where it came from).

    Uses the cached file next to this script; downloads it if missing, stale or `refresh`.
    If the download fails but a cached file exists, the cache is used anyway.
    """
    found = find_tle_file(path or TLE_FILE)
    if path and found and not refresh:
        return found, "file"                      # an explicit --tle path is used as-is
    stale = found is not None and (time.time() - os.path.getmtime(found)) / 86400.0 > max_age_days
    if found is not None and not refresh and not stale:
        return found, "cache"
    try:
        return download_tle(), "download"
    except Exception as e:
        if found is not None:
            log.warning("TLE download failed (%s); using the cached file", e)
            return found, "cache (download failed)"
        raise RuntimeError(f"no TLE file and the download failed: {e}") from e


def load_satellites(ts, path: str | None = None, refresh: bool = False):
    """Return (satellites, median TLE epoch, info dict about the TLE file)."""
    tle_path, origin = ensure_tle(path, refresh)
    lines = tle_lines(tle_path)
    sats = []
    for i in range(1, len(lines) - 1):
        if lines[i].startswith("1 ") and lines[i + 1].startswith("2 "):
            name = lines[i - 1] if not lines[i - 1].startswith(("1 ", "2 ")) else "UNKNOWN"
            sats.append(EarthSatellite(lines[i], lines[i + 1], name.strip(), ts))
    if not sats:
        raise RuntimeError(f"No satellites found in the TLE file {tle_path}.")
    # Simulate around the typical TLE epoch so the orbit data is as accurate as possible,
    # and drop stale/odd entries (spares, decayed, etc.)
    epochs = np.array([s.epoch.tt for s in sats])
    med = float(np.median(epochs))
    kept = [s for s, e in zip(sats, epochs) if abs(e - med) < 3.0]
    info = {
        "file": os.path.basename(tle_path),
        "path": tle_path,
        "origin": origin,
        "satellites": len(kept),
        "dropped": len(sats) - len(kept),
        "epoch_utc": ts.tt_jd(med).utc_iso(),
        "age_days": round(float(ts.now().tt - med), 2),          # plain floats: this goes out as JSON
        "file_age_days": round((time.time() - os.path.getmtime(tle_path)) / 86400.0, 2),
    }
    return kept, med, info


def propagate_constellation(sats, ts, tt_start, n, dt):
    """Return sat positions/velocities in ECEF: arrays shaped (n_sats, n_steps, 3), meters."""
    times = ts.tt_jd(tt_start + np.arange(n) * dt / 86400.0)
    sp, sv = [], []
    for s in sats:
        pos, vel = s.at(times).frame_xyz_and_velocity(itrs)   # Earth-fixed frame
        sp.append(pos.km.T * 1000.0)
        sv.append(vel.km_per_s.T * 1000.0)
    return np.array(sp), np.array(sv)


# =============================================================================
# 3. Truth trajectory + IMU + Doppler measurements
# =============================================================================
def make_truth(cfg: SimConfig, n: int):
    t = np.arange(n) * cfg.dt
    heading = np.radians(cfg.heading_deg) + np.clip(
        (t - cfg.turn_start_s) * np.radians(cfg.turn_rate_deg_s), 0, np.radians(cfg.turn_total_deg))
    lat = np.zeros(n)
    lon = np.zeros(n)
    lat[0], lon[0] = np.radians(cfg.start_lat), np.radians(cfg.start_lon)
    r = 6371000.0 + cfg.alt_m
    for k in range(1, n):
        lat[k] = lat[k - 1] + cfg.speed * np.cos(heading[k - 1]) * cfg.dt / r
        lon[k] = lon[k - 1] + cfg.speed * np.sin(heading[k - 1]) * cfg.dt / (r * np.cos(lat[k - 1]))
    p = geodetic_to_ecef(lat, lon, cfg.alt_m)
    v = np.gradient(p, cfg.dt, axis=0)   # velocity relative to the rotating Earth (ECEF)
    a = np.gradient(v, cfg.dt, axis=0)   # true acceleration
    return t, lat, lon, p, v, a


def range_rate(p_rx, v_rx, p_sat, v_sat):
    """Rate of change of distance receiver<->satellite [m/s]. Positive = moving apart."""
    d = p_sat - p_rx
    rho = np.linalg.norm(d, axis=-1)
    u = d / rho[..., None]
    return np.sum((v_sat - v_rx) * u, axis=-1), rho


def simulate_measurements(cfg: SimConfig, rng, p, v, lat, lon, SP, SV):
    """Doppler [Hz] from every satellite at every step, NaN when below the horizon."""
    n = len(p)
    up = up_vector(lat, lon)
    rel = SP - p[None, :, :]
    elev = np.degrees(np.arcsin(np.einsum("snj,nj->sn", rel, up) / np.linalg.norm(rel, axis=2)))
    visible = elev > cfg.min_elev_deg

    rr, _ = range_rate(p[None], v[None], SP, SV)                 # (n_sats, n)
    clock = cfg.clock_bias0_hz + np.cumsum(rng.normal(0, cfg.clock_rw_hz_rts * np.sqrt(cfg.dt), n))
    clock -= clock[0] - cfg.clock_bias0_hz
    z = -cfg.fc_hz / C * rr + clock[None, :] + rng.normal(0, cfg.doppler_sigma_hz, rr.shape)
    z = np.where(visible, z, np.nan)
    return z, visible, clock


# =============================================================================
# 4. The Extended Kalman Filter
# =============================================================================
def ekf_predict(cfg: SimConfig, x, P, a_imu):
    """Use the IMU acceleration (minus our current bias estimate) to move the state forward."""
    dt = cfg.dt
    x = x.copy()
    a = a_imu - x[7:10]                       # remove the bias we currently believe in
    x[0:3] += x[3:6] * dt + 0.5 * a * dt ** 2
    x[3:6] += a * dt

    F = np.eye(N_STATE)
    F[0:3, 3:6] = np.eye(3) * dt
    F[0:3, 7:10] = -0.5 * dt ** 2 * np.eye(3)  # a wrong bias estimate moves position...
    F[3:6, 7:10] = -dt * np.eye(3)             # ...and velocity

    q2 = cfg.q_accel ** 2
    Q = np.zeros((N_STATE, N_STATE))
    for i in range(3):
        Q[i, i] = q2 * dt ** 4 / 4
        Q[i, i + 3] = Q[i + 3, i] = q2 * dt ** 3 / 2
        Q[i + 3, i + 3] = q2 * dt ** 2
        Q[7 + i, 7 + i] = cfg.q_bias ** 2 * dt
    Q[6, 6] = cfg.q_clock ** 2 * dt
    return x, F @ P @ F.T + Q


def ekf_update_doppler(cfg: SimConfig, x, P, z, p_sat, v_sat):
    """Correct the state using ONE Doppler measurement z [Hz] from one satellite."""
    k = cfg.fc_hz / C
    r = cfg.r_dop
    d = p_sat - x[0:3]
    rho = np.linalg.norm(d)
    u = d / rho
    dv = v_sat - x[3:6]

    # What Doppler would we expect IF our current estimate were true?
    z_pred = -k * (u @ dv) + x[6]

    # Jacobian: how the predicted Doppler changes if position / velocity / clock change
    H = np.zeros(N_STATE)                # (bias states don't appear directly -> zeros)
    H[0:3] = k * (np.eye(3) - np.outer(u, u)) @ dv / rho
    H[3:6] = k * u
    H[6] = 1.0

    y = z - z_pred                      # innovation: measured minus expected
    S = H @ P @ H + r ** 2              # how uncertain that innovation is
    K = P @ H / S                       # Kalman gain: how much to trust the measurement
    x = x + K * y
    IKH = np.eye(N_STATE) - np.outer(K, H)
    P = IKH @ P @ IKH.T + r ** 2 * np.outer(K, K)   # Joseph form (numerically safe)
    return x, 0.5 * (P + P.T), y


# =============================================================================
# 5. Running the whole thing
# =============================================================================
@dataclass
class SimResult:
    """Everything one run produced. `to_json()` is what the web UI gets."""

    cfg: SimConfig
    tle: dict
    t: np.ndarray                 # [s]
    lat: np.ndarray               # truth, radians
    lon: np.ndarray
    p_true: np.ndarray            # (n,3) ECEF
    ekf_x: np.ndarray             # (n,10) filter state
    ekf_sigma_pos: np.ndarray     # 1-sigma position uncertainty [m]
    dr_pos: np.ndarray            # (n,3) IMU-only dead reckoning
    err_ekf: np.ndarray           # 3D position error [m]
    err_dr: np.ndarray
    n_vis: np.ndarray             # satellites in view per step
    clock_true: np.ndarray        # [Hz]
    z: np.ndarray                 # (n_sats, n) measured Doppler, NaN when not visible
    visible: np.ndarray           # (n_sats, n) bool
    sat_p: np.ndarray             # (n_sats, n, 3) satellite positions, ECEF [m]
    sat_names: list[str]
    runtime_s: float = 0.0

    # ---- derived views -------------------------------------------------------
    def geodetic(self):
        """Estimated (lat deg, lon deg, alt m) per step."""
        return ecef_to_geodetic(self.ekf_x[:, 0:3])

    def east_north(self, pos):
        """Local east/north [km] of an ECEF track, relative to the starting point."""
        e = np.array([-np.sin(self.lon[0]), np.cos(self.lon[0]), 0.0])
        nn = np.array([-np.sin(self.lat[0]) * np.cos(self.lon[0]),
                       -np.sin(self.lat[0]) * np.sin(self.lon[0]), np.cos(self.lat[0])])
        d = pos - self.p_true[0]
        return d @ e / 1000.0, d @ nn / 1000.0

    def checkpoints(self):
        """IMU-only vs fused error at round minute marks, for the summary table."""
        out = []
        for minute in (1, 2, 5, 10, 15, 20, 30, 45, 60, 90, 120):
            if minute * 60 > self.cfg.minutes * 60:
                break
            i = int(minute * 60 / self.cfg.dt) - 1
            out.append({"minute": minute, "imu_only_m": float(self.err_dr[i]),
                        "fused_m": float(self.err_ekf[i])})
        return out

    def scene_json(self, max_frames: int = 120) -> dict:
        """Positions for the 3D globe / 2D map, in ECEF kilometres.

        Satellites are rounded to whole km (invisible at globe scale and it halves the
        payload); the plane keeps 0.1 m so the true and estimated tracks stay apart.
        """
        n = len(self.t)
        step = max(1, -(-n // max_frames))
        ix = list(range(0, n, step))
        if ix[-1] != n - 1:
            ix.append(n - 1)
        flat_km = lambda p: [int(round(v)) for v in (p[ix] / 1000.0).ravel()]      # noqa: E731
        fine_km = lambda p: [round(float(v), 4) for v in (p[ix] / 1000.0).ravel()]  # noqa: E731
        return {
            "frames_min": [round(float(self.t[i] / 60.0), 3) for i in ix],
            "earth_radius_km": round(WGS_A / 1000.0, 1),
            "sat_names": self.sat_names,
            "sat_km": [flat_km(self.sat_p[s]) for s in range(len(self.sat_names))],
            "visible": [[int(s) for s in np.where(self.visible[:, i])[0]] for i in ix],
            "plane_true_km": fine_km(self.p_true),
            "plane_est_km": fine_km(self.ekf_x[:, 0:3]),
            "plane_dr_km": fine_km(self.dr_pos),
        }

    def to_json(self, max_points: int = 400) -> dict:
        """JSON-safe, downsampled version of the run (what POST /api/leo/simulate returns)."""
        n = len(self.t)
        step = max(1, n // max_points)
        s = slice(None, None, step)
        lat_e, lon_e, alt_e = self.geodetic()
        e_t, n_t = self.east_north(self.p_true)
        e_d, n_d = self.east_north(self.dr_pos)
        e_k, n_k = self.east_north(self.ekf_x[:, 0:3])
        f = lambda a: [round(float(v), 4) for v in np.asarray(a)[s]]   # noqa: E731
        pair = lambda a, b: [[round(float(u), 4), round(float(v), 4)]  # noqa: E731
                             for u, v in zip(np.asarray(a)[s], np.asarray(b)[s])]

        half = n // 2
        return {
            "config": asdict(self.cfg),
            "tle": self.tle,
            "runtime_s": round(self.runtime_s, 2),
            "summary": {
                "steps": n,
                "sats_min": int(self.n_vis.min()),
                "sats_mean": round(float(self.n_vis.mean()), 1),
                "sats_max": int(self.n_vis.max()),
                "err_ekf_final_m": float(self.err_ekf[-1]),
                "err_dr_final_m": float(self.err_dr[-1]),
                "err_ekf_mean_second_half_m": float(self.err_ekf[half:].mean()),
                "err_ekf_max_m": float(self.err_ekf.max()),
                "err_dr_max_m": float(self.err_dr.max()),
                "clock_err_final_hz": float(self.ekf_x[-1, 6] - self.clock_true[-1]),
                "final": {
                    "est_lat": float(lat_e[-1]), "est_lon": float(lon_e[-1]), "est_alt_m": float(alt_e[-1]),
                    "true_lat": float(np.degrees(self.lat[-1])), "true_lon": float(np.degrees(self.lon[-1])),
                    "true_alt_m": float(self.cfg.alt_m),
                },
                "checkpoints": self.checkpoints(),
            },
            "series": {
                "t_min": f(self.t / 60.0),
                "err_dr_m": f(self.err_dr),
                "err_ekf_m": f(self.err_ekf),
                "sigma3_m": f(3 * self.ekf_sigma_pos),
                "n_vis": [int(v) for v in self.n_vis[s]],
                "clock_true_hz": f(self.clock_true),
                "clock_est_hz": f(self.ekf_x[:, 6]),
                "bias_est_mms2": [f(self.ekf_x[:, 7 + i] * 1000.0) for i in range(3)],
                "bias_true_mms2": [round(b * 1000.0, 4) for b in self.cfg.accel_bias],
            },
            "track": {
                "truth_en_km": pair(e_t, n_t),
                "imu_en_km": pair(e_d, n_d),
                "fused_en_km": pair(e_k, n_k),
                "est_lat": f(lat_e), "est_lon": f(lon_e), "est_alt_m": f(alt_e),
                "true_lat": f(np.degrees(self.lat)), "true_lon": f(np.degrees(self.lon)),
            },
            "scene": self.scene_json(),
        }


def simulate(cfg: SimConfig | None = None, tle_path: str | None = None, refresh_tle: bool = False) -> SimResult:
    """Run one GPS-outage simulation and return everything it produced."""
    cfg = cfg or SimConfig()
    started = time.perf_counter()
    rng = np.random.default_rng(cfg.seed)
    ts = load.timescale()
    sats, tt_start, tle = load_satellites(ts, tle_path, refresh_tle)
    n = cfg.steps
    log.info("LEO sim: %d sats, %.0f min, dt %.2fs", len(sats), cfg.minutes, cfg.dt)

    SP, SV = propagate_constellation(sats, ts, tt_start, n, cfg.dt)
    # What the FILTER believes the satellite positions are (the truth is SP).
    SP_filter = SP + rng.normal(0, cfg.orbit_err_m, (len(sats), 1, 3)) if cfg.orbit_err_m > 0 else SP
    t, lat, lon, p_true, v_true, a_true = make_truth(cfg, n)
    z, visible, clock_true = simulate_measurements(cfg, rng, p_true, v_true, lat, lon, SP, SV)

    # IMU: truth acceleration + bias + noise
    a_imu = a_true + np.asarray(cfg.accel_bias) + rng.normal(0, cfg.accel_noise, a_true.shape)

    # Initial state = last GPS fix before the outage (small error), clock unknown (0 Hz guess)
    x = np.zeros(N_STATE)
    x[0:3] = p_true[0] + rng.normal(0, cfg.init_pos_sigma, 3)
    x[3:6] = v_true[0] + rng.normal(0, cfg.init_vel_sigma, 3)
    P = np.diag([cfg.init_pos_sigma ** 2] * 3 + [cfg.init_vel_sigma ** 2] * 3
                + [cfg.init_clk_sigma ** 2] + [cfg.init_bias_sigma ** 2] * 3)

    x_dr = x[:6].copy()                 # IMU-only dead reckoning from the same start

    ekf_x = np.zeros((n, N_STATE))
    ekf_sigma_pos = np.zeros(n)
    dr_pos = np.zeros((n, 3))
    ekf_x[0], dr_pos[0] = x, x_dr[:3]
    ekf_sigma_pos[0] = np.sqrt(np.trace(P[0:3, 0:3]))

    for k in range(1, n):
        # IMU-only (no Doppler)
        x_dr[0:3] += x_dr[3:6] * cfg.dt + 0.5 * a_imu[k - 1] * cfg.dt ** 2
        x_dr[3:6] += a_imu[k - 1] * cfg.dt
        dr_pos[k] = x_dr[:3]

        # EKF predict with IMU, then correct with each visible satellite's Doppler
        x, P = ekf_predict(cfg, x, P, a_imu[k - 1])
        for si in np.where(visible[:, k])[0]:
            x, P, _ = ekf_update_doppler(cfg, x, P, z[si, k], SP_filter[si, k], SV[si, k])
        ekf_x[k] = x
        ekf_sigma_pos[k] = np.sqrt(np.trace(P[0:3, 0:3]))

    return SimResult(
        cfg=cfg, tle=tle, t=t, lat=lat, lon=lon, p_true=p_true,
        ekf_x=ekf_x, ekf_sigma_pos=ekf_sigma_pos, dr_pos=dr_pos,
        err_ekf=np.linalg.norm(ekf_x[:, 0:3] - p_true, axis=1),
        err_dr=np.linalg.norm(dr_pos - p_true, axis=1),
        n_vis=visible.sum(axis=0), clock_true=clock_true, z=z, visible=visible,
        sat_p=SP, sat_names=[s.name for s in sats],
        runtime_s=time.perf_counter() - started,
    )


def tle_status(refresh: bool = False) -> dict:
    """TLE file info only (no simulation) - used by GET /api/leo/info."""
    ts = load.timescale()
    _, _, info = load_satellites(ts, None, refresh)
    return info


# =============================================================================
# 6. CLI: print a table, save plots and a CSV
# =============================================================================
def report(res: SimResult, plot: bool = True, csv_path: str = "estimated_track.csv",
           png_path: str = "iridium_doppler_ekf_results.png") -> None:
    cfg, n = res.cfg, len(res.t)
    print(f"TLEs: {res.tle['path']} ({res.tle['origin']}, {res.tle['satellites']} sats, "
          f"epoch {res.tle['epoch_utc']})")
    print(f"Simulated {cfg.minutes:.0f} min at dt {cfg.dt:g}s in {res.runtime_s:.1f}s.")
    print(f"\nSatellites in view: min {res.n_vis.min()}, mean {res.n_vis.mean():.1f}, max {res.n_vis.max()}")
    print("Position error (3D) [m]:")
    print(f"{'time':>8} {'IMU only':>12} {'IMU+Doppler':>13}")
    for c in res.checkpoints():
        print(f"{c['minute']:>5} min {c['imu_only_m']:>12.0f} {c['fused_m']:>13.0f}")
    print(f"Mean EKF error over the second half of the run: {res.err_ekf[n // 2:].mean():.0f} m")

    # The answer you actually want: the estimated position of the plane
    lat_e, lon_e, alt_e = res.geodetic()
    print("\nFinal position at the end of the run:")
    print(f"  estimated: lat {lat_e[-1]:.4f}  lon {lon_e[-1]:.4f}  alt {alt_e[-1]:.0f} m")
    print(f"  true:      lat {np.degrees(res.lat[-1]):.4f}  lon {np.degrees(res.lon[-1]):.4f}  alt {cfg.alt_m:.0f} m")
    track = np.column_stack([res.t, lat_e, lon_e, alt_e, np.degrees(res.lat), np.degrees(res.lon),
                             np.full(n, cfg.alt_m), res.err_ekf])
    np.savetxt(csv_path, track, delimiter=",", fmt="%.6f", comments="",
               header="time_s,est_lat,est_lon,est_alt_m,true_lat,true_lon,true_alt_m,error_m")
    print(f"Saved estimated track: {csv_path}")
    if not plot:
        return

    # ------------------------------ plots ------------------------------
    import matplotlib.pyplot as plt      # imported here so the server never needs matplotlib

    tm = res.t / 60
    fig, ax = plt.subplots(2, 3, figsize=(18, 8))

    ax[0, 0].plot(tm, res.err_dr / 1000, label="IMU only (drifts)", color="tab:red")
    ax[0, 0].plot(tm, res.err_ekf / 1000, label="IMU + Iridium Doppler (EKF)", color="tab:green")
    ax[0, 0].plot(tm, 3 * res.ekf_sigma_pos / 1000, "--", color="tab:green", alpha=0.5,
                  label="EKF's own 3-sigma estimate")
    ax[0, 0].set(title="Position error", xlabel="time [min]", ylabel="3D error [km]")
    ax[0, 0].legend(); ax[0, 0].grid(alpha=0.3)

    ax[0, 1].step(tm, res.n_vis, where="post", color="tab:blue")
    ax[0, 1].set(title=f"Iridium satellites above {cfg.min_elev_deg:.0f} deg elevation",
                 xlabel="time [min]", ylabel="count")
    ax[0, 1].grid(alpha=0.3)

    for s in range(res.z.shape[0]):
        if res.visible[s].any():
            ax[1, 0].plot(tm, res.z[s] / 1000, ".", ms=2)
    ax[1, 0].set(title="Measured Doppler (each colour = one satellite, includes clock error)",
                 xlabel="time [min]", ylabel="Doppler [kHz]")
    ax[1, 0].grid(alpha=0.3)

    ax[1, 1].plot(tm, res.clock_true, label="true clock offset")
    ax[1, 1].plot(tm, res.ekf_x[:, 6], "--", label="EKF estimate")
    ax[1, 1].set(title="Receiver clock frequency offset", xlabel="time [min]", ylabel="Hz")
    ax[1, 1].legend(); ax[1, 1].grid(alpha=0.3)

    for i, c in enumerate(["tab:blue", "tab:orange", "tab:purple"]):
        ax[0, 2].axhline(cfg.accel_bias[i] * 1000, color=c, alpha=0.6)
        ax[0, 2].plot(tm, res.ekf_x[:, 7 + i] * 1000, "--", color=c)
    ax[0, 2].set(title="Accelerometer bias: true (solid) vs EKF estimate (dashed)",
                 xlabel="time [min]", ylabel="bias [mm/s^2]")
    ax[0, 2].grid(alpha=0.3)

    ax[1, 2].plot(*res.east_north(res.p_true), "k", lw=2, label="truth")
    ax[1, 2].plot(*res.east_north(res.dr_pos), color="tab:red", label="IMU only")
    ax[1, 2].plot(*res.east_north(res.ekf_x[:, 0:3]), color="tab:green", label="IMU + Doppler")
    ax[1, 2].set(title="Flight path (top view)", xlabel="east [km]", ylabel="north [km]")
    ax[1, 2].set_aspect("equal", adjustable="datalim")
    ax[1, 2].legend(); ax[1, 2].grid(alpha=0.3)

    plt.tight_layout()
    plt.savefig(png_path, dpi=130)
    print(f"\nSaved plot: {png_path}")
    plt.show()


def main(argv: list[str] | None = None) -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Iridium Doppler + IMU navigation simulation")
    ap.add_argument("--minutes", type=float, help="length of the GPS outage to simulate")
    ap.add_argument("--dt", type=float, help="filter time step [s]")
    ap.add_argument("--min-elev", type=float, help="elevation mask [deg]")
    ap.add_argument("--orbit-err", type=float, help="satellite position error the filter suffers [m]")
    ap.add_argument("--doppler-sigma", type=float, help="Doppler measurement noise [Hz]")
    ap.add_argument("--seed", type=int, help="random seed")
    ap.add_argument("--tle", help=f"path to a TLE file (default: cached {TLE_FILE}, downloaded if missing)")
    ap.add_argument("--refresh-tle", action="store_true", help="re-download the TLE file first")
    ap.add_argument("--no-plot", action="store_true", help="skip the matplotlib figure")
    a = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    overrides = {k: v for k, v in {
        "minutes": a.minutes, "dt": a.dt, "min_elev_deg": a.min_elev,
        "orbit_err_m": a.orbit_err, "doppler_sigma_hz": a.doppler_sigma, "seed": a.seed,
    }.items() if v is not None}
    res = simulate(replace(SimConfig(), **overrides), tle_path=a.tle, refresh_tle=a.refresh_tle)
    report(res, plot=not a.no_plot)


if __name__ == "__main__":
    main()
