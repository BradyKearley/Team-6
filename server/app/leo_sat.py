import os
import urllib.request

import numpy as np
import matplotlib.pyplot as plt
from skyfield.api import load, EarthSatellite
from skyfield.framelib import itrs


TLE_FILE = "iridium_next.tle"          #  saved TLE file.
                                       
FC = 1626.0e6        # Iridium L-band downlink carrier [Hz]  (~1616-1626.5 MHz)
C = 299_792_458.0    # speed of light [m/s]

DURATION_S = 1200    # simulate 20 minutes of GPS outage
DT = 1.0             # time step [s]  (filter rate)
MIN_ELEV_DEG = 10.0  # ignore satellites lower than this above the horizon

# --- Truth flight ---
START_LAT_DEG, START_LON_DEG, ALT_M = 45.0, -60.0, 10_000.0
SPEED = 250.0                  # m/s
HEADING0_DEG = 50.0            # initial heading (0 = north, 90 = east)
TURN_START_S = 400.0           # at this time the plane turns right...
TURN_RATE_DEG_S = 3.0          # ...at 3 deg/s...
TURN_TOTAL_DEG = 90.0          # ...by 90 degrees

# --- IMU errors (what makes pure dead-reckoning drift) ---
ACCEL_BIAS = np.array([0.012, -0.010, 0.008])  # m/s^2 constant bias (~2 mg), ECEF axes
ACCEL_NOISE = 0.005                            # m/s^2 white noise per sample

# --- Doppler measurement errors ---
DOPPLER_SIGMA_HZ = 5.0         # measurement noise (1 sigma)
CLOCK_BIAS0_HZ = 800.0         # true initial oscillator offset (~0.5 ppm at 1.6 GHz)
CLOCK_RW_HZ_RTS = 0.2          # true clock random walk [Hz / sqrt(s)]
ORBIT_ERR_M = 0.0              # error in the satellite positions the filter uses [m]
                               # (real TLEs are roughly 0.5-2 km off; try 1000)

# --- Filter tuning (how much the filter trusts each source) ---
INIT_POS_SIGMA = 20.0          # m   (last GPS fix before the outage)
INIT_VEL_SIGMA = 0.2           # m/s
INIT_CLK_SIGMA = 2000.0        # Hz  (we have no idea what the clock offset is)
INIT_BIAS_SIGMA = 0.03         # m/s^2  (we don't know the accelerometer bias either)
Q_ACCEL = 0.01                 # m/s^2  IMU white noise the filter assumes
Q_BIAS = 2e-4                  # m/s^2/sqrt(s)  how fast the filter lets the bias wander
Q_CLOCK = 0.5                  # Hz/sqrt(s) clock random-walk assumed by the filter
N_STATE = 10
R_DOPPLER = DOPPLER_SIGMA_HZ   # what the filter believes the Doppler noise is

RNG = np.random.default_rng(42)

# WGS-84 ellipsoid
WGS_A = 6378137.0
WGS_F = 1 / 298.257223563
WGS_E2 = WGS_F * (2 - WGS_F)


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
# 2. Satellite data
# =============================================================================
HERE = os.path.dirname(os.path.abspath(__file__))   # folder tle script lives in


def find_tle_file():
    """Look next to this script (not the terminal's current folder) for the TLE file."""
    for name in (TLE_FILE, TLE_FILE + ".rtf", TLE_FILE + ".txt"):
        path = os.path.join(HERE, name)          # an absolute path (--tle) is used as-is
        if os.path.exists(path):
            return path
    return None


def get_tle_text():
    """Read the saved TLE file (plain text or RTF)"""
    path = find_tle_file()
    print(f"Reading orbit data from: {path}")
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


def load_satellites(ts):
    lines = [ln.rstrip() for ln in get_tle_text() if ln.strip()]
    sats = []
    for i in range(1, len(lines) - 1):
        if lines[i].startswith("1 ") and lines[i + 1].startswith("2 "):
            name = lines[i - 1] if not lines[i - 1].startswith(("1 ", "2 ")) else "UNKNOWN"
            sats.append(EarthSatellite(lines[i], lines[i + 1], name, ts))
    if not sats:
        raise SystemExit("No satellites found in the TLE file.")
    # Simulate around the typical TLE epoch so the orbit data is as accurate as possible,
    # and drop stale/odd entries (spares, decayed, etc.)
    epochs = np.array([s.epoch.tt for s in sats])
    med = np.median(epochs)
    sats = [s for s, e in zip(sats, epochs) if abs(e - med) < 3.0]
    return sats, med


def propagate_constellation(sats, ts, tt_start, n):
    """Return sat positions/velocities in ECEF: arrays shaped (n_sats, n_steps, 3), meters."""
    times = ts.tt_jd(tt_start + np.arange(n) * DT / 86400.0)
    sp, sv = [], []
    for s in sats:
        pos, vel = s.at(times).frame_xyz_and_velocity(itrs)   # Earth-fixed frame
        sp.append(pos.km.T * 1000.0)
        sv.append(vel.km_per_s.T * 1000.0)
    return np.array(sp), np.array(sv)


# =============================================================================
# 3. Truth trajectory + IMU + Doppler measurements
# =============================================================================
def make_truth(n):
    t = np.arange(n) * DT
    heading = np.radians(HEADING0_DEG) + np.clip(
        (t - TURN_START_S) * np.radians(TURN_RATE_DEG_S), 0, np.radians(TURN_TOTAL_DEG))
    lat = np.zeros(n)
    lon = np.zeros(n)
    lat[0], lon[0] = np.radians(START_LAT_DEG), np.radians(START_LON_DEG)
    r = 6371000.0 + ALT_M
    for k in range(1, n):
        lat[k] = lat[k - 1] + SPEED * np.cos(heading[k - 1]) * DT / r
        lon[k] = lon[k - 1] + SPEED * np.sin(heading[k - 1]) * DT / (r * np.cos(lat[k - 1]))
    p = geodetic_to_ecef(lat, lon, ALT_M)
    v = np.gradient(p, DT, axis=0)       # velocity relative to the rotating Earth (ECEF)
    a = np.gradient(v, DT, axis=0)       # true acceleration
    return t, lat, lon, p, v, a


def range_rate(p_rx, v_rx, p_sat, v_sat):
    """Rate of change of distance receiver<->satellite [m/s]. Positive = moving apart."""
    d = p_sat - p_rx
    rho = np.linalg.norm(d, axis=-1)
    u = d / rho[..., None]
    return np.sum((v_sat - v_rx) * u, axis=-1), rho


def simulate_measurements(p, v, lat, lon, SP, SV):
    """Doppler [Hz] from every satellite at every step, NaN when below the horizon."""
    n = len(p)
    up = up_vector(lat, lon)
    rel = SP - p[None, :, :]
    elev = np.degrees(np.arcsin(np.einsum("snj,nj->sn", rel, up) / np.linalg.norm(rel, axis=2)))
    visible = elev > MIN_ELEV_DEG

    rr, _ = range_rate(p[None], v[None], SP, SV)                 # (n_sats, n)
    clock = CLOCK_BIAS0_HZ + np.cumsum(RNG.normal(0, CLOCK_RW_HZ_RTS * np.sqrt(DT), n))
    clock -= clock[0] - CLOCK_BIAS0_HZ
    z = -FC / C * rr + clock[None, :] + RNG.normal(0, DOPPLER_SIGMA_HZ, rr.shape)
    z = np.where(visible, z, np.nan)
    return z, visible, clock


# =============================================================================
# 4. The Extended Kalman Filter
# =============================================================================
def ekf_predict(x, P, a_imu):
    """Use the IMU acceleration (minus our current bias estimate) to move the state forward."""
    x = x.copy()
    a = a_imu - x[7:10]                       # remove the bias we currently believe in
    x[0:3] += x[3:6] * DT + 0.5 * a * DT ** 2
    x[3:6] += a * DT

    F = np.eye(N_STATE)
    F[0:3, 3:6] = np.eye(3) * DT
    F[0:3, 7:10] = -0.5 * DT ** 2 * np.eye(3)  # a wrong bias estimate moves position...
    F[3:6, 7:10] = -DT * np.eye(3)             # ...and velocity

    q2 = Q_ACCEL ** 2
    Q = np.zeros((N_STATE, N_STATE))
    for i in range(3):
        Q[i, i] = q2 * DT ** 4 / 4
        Q[i, i + 3] = Q[i + 3, i] = q2 * DT ** 3 / 2
        Q[i + 3, i + 3] = q2 * DT ** 2
        Q[7 + i, 7 + i] = Q_BIAS ** 2 * DT
    Q[6, 6] = Q_CLOCK ** 2 * DT
    return x, F @ P @ F.T + Q


def ekf_update_doppler(x, P, z, p_sat, v_sat):
    """Correct the state using ONE Doppler measurement z [Hz] from one satellite."""
    k = FC / C
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
    S = H @ P @ H + R_DOPPLER ** 2      # how uncertain that innovation is
    K = P @ H / S                       # Kalman gain: how much to trust the measurement
    x = x + K * y
    IKH = np.eye(N_STATE) - np.outer(K, H)
    P = IKH @ P @ IKH.T + R_DOPPLER ** 2 * np.outer(K, K)   # Joseph form (numerically safe)
    return x, 0.5 * (P + P.T), y


# =============================================================================
# 5. Main
# =============================================================================
def main(plot=True):
    ts = load.timescale()
    sats, tt_start = load_satellites(ts)
    n = int(DURATION_S / DT)
    print(f"Using {len(sats)} Iridium NEXT satellites, simulating {DURATION_S/60:.0f} min.")

    SP, SV = propagate_constellation(sats, ts, tt_start, n)
    # What the FILTER believes the satellite positions are (the truth is SP).
    SP_filter = SP + RNG.normal(0, ORBIT_ERR_M, (len(sats), 1, 3)) if ORBIT_ERR_M > 0 else SP
    t, lat, lon, p_true, v_true, a_true = make_truth(n)
    z, visible, clock_true = simulate_measurements(p_true, v_true, lat, lon, SP, SV)

    # IMU: truth acceleration + bias + noise
    a_imu = a_true + ACCEL_BIAS + RNG.normal(0, ACCEL_NOISE, a_true.shape)

    # Initial state = last GPS fix before the outage (small error), clock unknown (0 Hz guess)
    x = np.zeros(N_STATE)
    x[0:3] = p_true[0] + RNG.normal(0, INIT_POS_SIGMA, 3)
    x[3:6] = v_true[0] + RNG.normal(0, INIT_VEL_SIGMA, 3)
    P = np.diag([INIT_POS_SIGMA ** 2] * 3 + [INIT_VEL_SIGMA ** 2] * 3
                + [INIT_CLK_SIGMA ** 2] + [INIT_BIAS_SIGMA ** 2] * 3)

    x_dr = x[:6].copy()                 # IMU-only dead reckoning from the same start

    ekf_x = np.zeros((n, N_STATE))
    ekf_sigma_pos = np.zeros(n)
    dr_pos = np.zeros((n, 3))
    ekf_x[0], dr_pos[0] = x, x_dr[:3]
    ekf_sigma_pos[0] = np.sqrt(np.trace(P[0:3, 0:3]))

    for k in range(1, n):
        # IMU-only (no Doppler)
        x_dr[0:3] += x_dr[3:6] * DT + 0.5 * a_imu[k - 1] * DT ** 2
        x_dr[3:6] += a_imu[k - 1] * DT
        dr_pos[k] = x_dr[:3]

        # EKF predict with IMU, then correct with each visible satellite's Doppler
        x, P = ekf_predict(x, P, a_imu[k - 1])
        for s in np.where(visible[:, k])[0]:
            x, P, _ = ekf_update_doppler(x, P, z[s, k], SP_filter[s, k], SV[s, k])
        ekf_x[k] = x
        ekf_sigma_pos[k] = np.sqrt(np.trace(P[0:3, 0:3]))

    err_ekf = np.linalg.norm(ekf_x[:, 0:3] - p_true, axis=1)
    err_dr = np.linalg.norm(dr_pos - p_true, axis=1)
    n_vis = visible.sum(axis=0)

    print(f"\nSatellites in view: min {n_vis.min()}, mean {n_vis.mean():.1f}, max {n_vis.max()}")
    print("Position error (3D) [m]:")
    print(f"{'time':>8} {'IMU only':>12} {'IMU+Doppler':>13}")
    for minute in (1, 5, 10, 15, 20, 30, 45, 60, 90):
        if minute * 60 > DURATION_S:
            break
        i = int(minute * 60 / DT) - 1
        print(f"{minute:>5} min {err_dr[i]:>12.0f} {err_ekf[i]:>13.0f}")
    print(f"Mean EKF error over the second half of the run: {err_ekf[n//2:].mean():.0f} m")

    # The answer you actually want: the estimated position of the plane
    lat_e, lon_e, alt_e = ecef_to_geodetic(ekf_x[:, 0:3])
    print("\nFinal position at the end of the run:")
    print(f"  estimated: lat {lat_e[-1]:.4f}  lon {lon_e[-1]:.4f}  alt {alt_e[-1]:.0f} m")
    print(f"  true:      lat {np.degrees(lat[-1]):.4f}  lon {np.degrees(lon[-1]):.4f}  alt {ALT_M:.0f} m")
    track = np.column_stack([t, lat_e, lon_e, alt_e, np.degrees(lat), np.degrees(lon),
                             np.full(n, ALT_M), err_ekf])
    np.savetxt("estimated_track.csv", track, delimiter=",", fmt="%.6f", comments="",
               header="time_s,est_lat,est_lon,est_alt_m,true_lat,true_lon,true_alt_m,error_m")
    print("Saved estimated track: estimated_track.csv")
    if not plot:
        return err_ekf, err_dr, n_vis

    # ------------------------------ plots ------------------------------
    tm = t / 60
    fig, ax = plt.subplots(2, 3, figsize=(18, 8))

    ax[0, 0].plot(tm, err_dr / 1000, label="IMU only (drifts)", color="tab:red")
    ax[0, 0].plot(tm, err_ekf / 1000, label="IMU + Iridium Doppler (EKF)", color="tab:green")
    ax[0, 0].plot(tm, 3 * ekf_sigma_pos / 1000, "--", color="tab:green", alpha=0.5,
                  label="EKF's own 3-sigma estimate")
    ax[0, 0].set(title="Position error", xlabel="time [min]", ylabel="3D error [km]")
    ax[0, 0].legend(); ax[0, 0].grid(alpha=0.3)

    ax[0, 1].step(tm, n_vis, where="post", color="tab:blue")
    ax[0, 1].set(title=f"Iridium satellites above {MIN_ELEV_DEG:.0f} deg elevation",
                 xlabel="time [min]", ylabel="count")
    ax[0, 1].grid(alpha=0.3)

    for s in range(len(sats)):
        if visible[s].any():
            ax[1, 0].plot(tm, z[s] / 1000, ".", ms=2)
    ax[1, 0].set(title="Measured Doppler (each colour = one satellite, includes clock error)",
                 xlabel="time [min]", ylabel="Doppler [kHz]")
    ax[1, 0].grid(alpha=0.3)

    ax[1, 1].plot(tm, clock_true, label="true clock offset")
    ax[1, 1].plot(tm, ekf_x[:, 6], "--", label="EKF estimate")
    ax[1, 1].set(title="Receiver clock frequency offset", xlabel="time [min]", ylabel="Hz")
    ax[1, 1].legend(); ax[1, 1].grid(alpha=0.3)

    for i, c in enumerate(["tab:blue", "tab:orange", "tab:purple"]):
        ax[0, 2].axhline(ACCEL_BIAS[i] * 1000, color=c, alpha=0.6)
        ax[0, 2].plot(tm, ekf_x[:, 7 + i] * 1000, "--", color=c)
    ax[0, 2].set(title="Accelerometer bias: true (solid) vs EKF estimate (dashed)",
                 xlabel="time [min]", ylabel="bias [mm/s^2]")
    ax[0, 2].grid(alpha=0.3)

    def to_en(pos):                       # local east/north [km] relative to the start point
        e = np.array([-np.sin(lon[0]), np.cos(lon[0]), 0.0])
        nn = np.array([-np.sin(lat[0]) * np.cos(lon[0]), -np.sin(lat[0]) * np.sin(lon[0]), np.cos(lat[0])])
        d = pos - p_true[0]
        return d @ e / 1000, d @ nn / 1000
    ax[1, 2].plot(*to_en(p_true), "k", lw=2, label="truth")
    ax[1, 2].plot(*to_en(dr_pos), color="tab:red", label="IMU only")
    ax[1, 2].plot(*to_en(ekf_x[:, 0:3]), color="tab:green", label="IMU + Doppler")
    ax[1, 2].set(title="Flight path (top view)", xlabel="east [km]", ylabel="north [km]")
    ax[1, 2].set_aspect("equal", adjustable="datalim")
    ax[1, 2].legend(); ax[1, 2].grid(alpha=0.3)

    plt.tight_layout()
    plt.savefig("iridium_doppler_ekf_results.png", dpi=130)
    print("\nSaved plot: iridium_doppler_ekf_results.png")
    plt.show()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Iridium Doppler + IMU navigation simulation")
    ap.add_argument("--minutes", type=float, help="length of the GPS outage to simulate")
    ap.add_argument("--tle", help="path to a TLE file (default: iridium_next.tle)")
    args = ap.parse_args()
    if args.minutes:
        DURATION_S = args.minutes * 60
    if args.tle:
        TLE_FILE = args.tle
    main()