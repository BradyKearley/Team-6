#!/usr/bin/env python3
"""
MPU6050 motion tracker for Raspberry Pi (Model B+).

Wiring (physical header pins):
    Pin 2 -> VCC (5V; fine for GY-521 boards with an onboard regulator,
                  use pin 1 / 3.3V instead if your board has no regulator)
    Pin 3 -> SDA (GPIO2)
    Pin 5 -> SCL (GPIO3)
    Pin 6 -> GND
    AD0 high -> I2C address 0x69 (AD0 low/floating -> 0x68)

Setup:
    sudo raspi-config                   # Interface Options -> I2C -> Enable
    sudo apt install python3-smbus      # or: pip3 install smbus2
    i2cdetect -y 1                      # should show 69 (or 68)

Usage:
    # ONE-TIME (repeat if the sensor/temperature changes a lot):
    python3 imu_tracker.py --calibrate-6pos
        Walks you through holding the sensor in 6 orientations and saves
        per-axis accelerometer offsets and scales to imu_calib.json.

    # Normal run (keep the sensor still for the first ~3 s):
    python3 imu_tracker.py                    # centimeters (default)
    python3 imu_tracker.py --units in         # inches  (m, cm, in)
    python3 imu_tracker.py --log run.csv      # choose the CSV name/path
    python3 imu_tracker.py --no-log           # turn CSV logging off
        (By default every run logs to imu_log_<date>_<time>.csv in the
        current folder, in SI units: m, m/s, m/s^2. The path is printed.)
    python3 imu_tracker.py --addr 0x68        # if AD0 is low

Keys while running:
    z  zero position/velocity/distance (set a new origin)
    c  recalibrate gyro bias and tilt (sensor must be still)
    q  quit

How it works:
    1. Configures the MPU6050 (+/-4 g, +/-500 dps, 44 Hz DLPF, 100 Hz).
    2. Applies per-axis accel offset/scale from the 6-position calibration.
       Without it, only a single overall scale factor is used and accel
       offsets (typically up to ~0.5 m/s^2) will cause large drift.
    3. Calibrates gyro bias and starting tilt from ~3 s of stillness.
    4. Tracks orientation with a Mahony filter (gyro + accel).
    5. Rotates accel into a fixed world frame (Z up), subtracts gravity,
       and integrates to velocity and position.
    6. Zero-velocity updates: when the sensor is still, velocity is forced
       to zero. When a movement ends, the leftover velocity is treated as a
       constant accel bias and removed from that whole movement, and the
       position for that movement is recomputed. So during a move the
       displayed position may drift, then it snaps to the corrected value
       once the sensor comes to rest.

World frame: X/Y/Z with Z up. X/Y are set by the sensor's starting heading
(no magnetometer), so yaw is relative to the start and drifts slowly.

Accuracy note: double-integrating a MEMS accelerometer is inherently
drift-prone. Expect decent results for short movements that end at rest.
"""

import argparse
import json
import math
import os
import select
import sys
import time

try:
    import termios
    import tty
except ImportError:  # not Linux
    termios = None
    tty = None

try:
    from smbus2 import SMBus
except ImportError:
    try:
        from smbus import SMBus
    except ImportError:
        SMBus = None

# ---------------- MPU6050 registers ----------------
REG_SMPLRT_DIV = 0x19
REG_CONFIG = 0x1A
REG_GYRO_CONFIG = 0x1B
REG_ACCEL_CONFIG = 0x1C
REG_ACCEL_XOUT_H = 0x3B
REG_PWR_MGMT_1 = 0x6B
REG_WHO_AM_I = 0x75

# ---------------- Configuration ----------------
G = 9.80665                  # m/s^2
ACCEL_LSB_PER_G = 8192.0     # +/-4 g range (ACCEL_CONFIG = 0x08)
GYRO_LSB_PER_DPS = 65.5      # +/-500 dps range (GYRO_CONFIG = 0x08)
SAMPLE_RATE_HZ = 100         # 1 kHz gyro rate / (1 + 9)
CAL_SAMPLES = 300            # ~3 s of stillness for startup calibration
SIX_POS_SAMPLES = 300        # ~3 s per orientation in 6-position calibration
CAL_GYRO_SPREAD_DPS = 6.0    # reject a calibration if gyro varies more than this
SIX_POS_MIN_ALIGN = 0.985    # cos(tilt) needed for the "up" axis (~10 deg)
CALIB_FILE = "imu_calib.json"

KP = 2.0                     # Mahony proportional gain
KI = 0.005                   # Mahony integral gain

ZUPT_ACC_THRESH = 0.20       # m/s^2, linear accel below this counts as still
ZUPT_GYRO_THRESH = 0.06      # rad/s (~3.4 deg/s)
ZUPT_SAMPLES = 25            # consecutive still samples (~0.25 s) to trigger
ACC_DEADBAND = 0.05          # m/s^2, ignore tiny residual accel
BIAS_RATE = 0.002            # gyro bias tracking rate while stationary
MAX_SEG_SAMPLES = 6000       # longest movement (~60 s) that gets drift correction

UNITS = {"m": (1.0, "m"), "cm": (100.0, "cm"), "in": (39.3701, "in")}

# (description, axis index, sign) -- sign is the direction the axis points
ORIENTATIONS = [
    ("flat, component side UP (+Z axis pointing up)", 2, +1),
    ("upside down (+Z axis pointing DOWN)", 2, -1),
    ("on its edge with the +X arrow pointing UP", 0, +1),
    ("on its edge with the +X arrow pointing DOWN", 0, -1),
    ("on its edge with the +Y arrow pointing UP", 1, +1),
    ("on its edge with the +Y arrow pointing DOWN", 1, -1),
]


def to_int16(hi, lo):
    v = (hi << 8) | lo
    return v - 65536 if v & 0x8000 else v


class MPU6050:
    def __init__(self, bus_num=1, addr=0x69):
        if SMBus is None:
            raise RuntimeError("No I2C library found. Run: sudo apt install python3-smbus")
        self.addr = addr
        self.bus = SMBus(bus_num)

    def start(self):
        who = self.bus.read_byte_data(self.addr, REG_WHO_AM_I)
        if who not in (0x68, 0x69, 0x70, 0x71, 0x72):
            print(f"Warning: unexpected WHO_AM_I value 0x{who:02X}")
        self.bus.write_byte_data(self.addr, REG_PWR_MGMT_1, 0x80)  # reset
        time.sleep(0.1)
        self.bus.write_byte_data(self.addr, REG_PWR_MGMT_1, 0x01)  # wake, PLL clock
        time.sleep(0.1)
        self.bus.write_byte_data(self.addr, REG_CONFIG, 0x03)       # DLPF ~44 Hz
        self.bus.write_byte_data(self.addr, REG_SMPLRT_DIV, 1000 // SAMPLE_RATE_HZ - 1)
        self.bus.write_byte_data(self.addr, REG_GYRO_CONFIG, 0x08)  # +/-500 dps
        self.bus.write_byte_data(self.addr, REG_ACCEL_CONFIG, 0x08) # +/-4 g
        time.sleep(0.1)

    def read(self):
        """Return (ax, ay, az) in m/s^2 (nominal scale) and (gx, gy, gz) in rad/s (raw)."""
        d = self.bus.read_i2c_block_data(self.addr, REG_ACCEL_XOUT_H, 14)
        ax = to_int16(d[0], d[1]) / ACCEL_LSB_PER_G * G
        ay = to_int16(d[2], d[3]) / ACCEL_LSB_PER_G * G
        az = to_int16(d[4], d[5]) / ACCEL_LSB_PER_G * G
        k = math.pi / 180.0 / GYRO_LSB_PER_DPS
        gx = to_int16(d[8], d[9]) * k
        gy = to_int16(d[10], d[11]) * k
        gz = to_int16(d[12], d[13]) * k
        return (ax, ay, az), (gx, gy, gz)

    def close(self):
        self.bus.close()


# ---------------- Small vector / quaternion helpers ----------------
def norm3(v):
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def quat_from_up_vector(u):
    """Quaternion (w,x,y,z), body->world, that rotates body-frame 'up' u to world +Z."""
    n = norm3(u)
    ux, uy, uz = u[0] / n, u[1] / n, u[2] / n
    dot = uz  # u . (0,0,1)
    if dot < -0.999999:
        return (0.0, 1.0, 0.0, 0.0)  # upside down: 180 deg about X
    w, x, y, z = 1.0 + dot, uy, -ux, 0.0  # axis = u x z = (uy, -ux, 0)
    q_n = math.sqrt(w * w + x * x + y * y + z * z)
    return (w / q_n, x / q_n, y / q_n, z / q_n)


def rotate_to_world(q, v):
    """Rotate body-frame vector v into the world frame using body->world quaternion q."""
    q0, q1, q2, q3 = q
    x, y, z = v
    return (
        (1 - 2 * (q2 * q2 + q3 * q3)) * x + 2 * (q1 * q2 - q0 * q3) * y + 2 * (q1 * q3 + q0 * q2) * z,
        2 * (q1 * q2 + q0 * q3) * x + (1 - 2 * (q1 * q1 + q3 * q3)) * y + 2 * (q2 * q3 - q0 * q1) * z,
        2 * (q1 * q3 - q0 * q2) * x + 2 * (q2 * q3 + q0 * q1) * y + (1 - 2 * (q1 * q1 + q2 * q2)) * z,
    )


def euler_deg(q):
    q0, q1, q2, q3 = q
    roll = math.atan2(2 * (q0 * q1 + q2 * q3), 1 - 2 * (q1 * q1 + q2 * q2))
    s = max(-1.0, min(1.0, 2 * (q0 * q2 - q3 * q1)))
    pitch = math.asin(s)
    yaw = math.atan2(2 * (q0 * q3 + q1 * q2), 1 - 2 * (q2 * q2 + q3 * q3))
    return math.degrees(roll), math.degrees(pitch), math.degrees(yaw)


class Mahony:
    def __init__(self, q0, kp=KP, ki=KI):
        self.q = q0
        self.kp = kp
        self.ki = ki
        self.ix = self.iy = self.iz = 0.0

    def update(self, gyro, accel, dt, use_accel=True):
        q0, q1, q2, q3 = self.q
        gx, gy, gz = gyro

        if use_accel:
            an = norm3(accel)
            if an > 1e-6:
                ax, ay, az = accel[0] / an, accel[1] / an, accel[2] / an
                # Estimated gravity direction in body frame
                vx = 2 * (q1 * q3 - q0 * q2)
                vy = 2 * (q0 * q1 + q2 * q3)
                vz = q0 * q0 - q1 * q1 - q2 * q2 + q3 * q3
                # Error = measured x estimated
                ex = ay * vz - az * vy
                ey = az * vx - ax * vz
                ez = ax * vy - ay * vx
                self.ix += self.ki * ex * dt
                self.iy += self.ki * ey * dt
                self.iz += self.ki * ez * dt
                gx += self.kp * ex + self.ix
                gy += self.kp * ey + self.iy
                gz += self.kp * ez + self.iz

        # q_dot = 0.5 * q (x) (0, gx, gy, gz)
        h = 0.5 * dt
        nq0 = q0 + h * (-q1 * gx - q2 * gy - q3 * gz)
        nq1 = q1 + h * (q0 * gx + q2 * gz - q3 * gy)
        nq2 = q2 + h * (q0 * gy - q1 * gz + q3 * gx)
        nq3 = q3 + h * (q0 * gz + q1 * gy - q2 * gx)
        n = math.sqrt(nq0 * nq0 + nq1 * nq1 + nq2 * nq2 + nq3 * nq3)
        self.q = (nq0 / n, nq1 / n, nq2 / n, nq3 / n)


# ---------------- Motion tracker (pure computation, no hardware) ----------------
class Tracker:
    def __init__(self, a_off, a_sc, g_bias, q0):
        self.a_off = list(a_off)
        self.a_sc = list(a_sc)
        self.g_bias = list(g_bias)
        self.filt = Mahony(q0)
        self.still_count = 0
        self.stationary = True
        self.lin = [0.0, 0.0, 0.0]
        self.zero()

    def zero(self):
        """Set the current position as the origin and clear velocity/distance."""
        self.pos = [0.0, 0.0, 0.0]
        self.vel = [0.0, 0.0, 0.0]
        self.dist = 0.0
        self.acc_prev = [0.0, 0.0, 0.0]
        self.in_seg = False
        self.seg = []
        self.seg_overflow = False
        self.seg_time = 0.0
        self.seg_pos0 = [0.0, 0.0, 0.0]
        self.seg_dist0 = 0.0

    def _start_segment(self):
        self.in_seg = True
        self.seg = []
        self.seg_overflow = False
        self.seg_time = 0.0
        self.seg_pos0 = list(self.pos)
        self.seg_dist0 = self.dist

    def _finish_segment(self):
        """Movement ended: remove the leftover velocity as a linear ramp and redo position."""
        if self.seg and not self.seg_overflow and self.seg_time > 0:
            v_end = list(self.vel)
            total = self.seg_time
            pos = list(self.seg_pos0)
            dist = self.seg_dist0
            v_prev = [0.0, 0.0, 0.0]
            t = 0.0
            for v_k, dt in self.seg:
                t += dt
                frac = t / total
                v_c = [v_k[i] - v_end[i] * frac for i in range(3)]
                step = [0.5 * (v_prev[i] + v_c[i]) * dt for i in range(3)]
                for i in range(3):
                    pos[i] += step[i]
                dist += norm3(step)
                v_prev = v_c
            self.pos = pos
            self.dist = dist
        self.in_seg = False
        self.seg = []

    def update(self, a_raw, g_raw, dt):
        accel = [(a_raw[i] - self.a_off[i]) * self.a_sc[i] for i in range(3)]
        gyro = [g_raw[i] - self.g_bias[i] for i in range(3)]

        # Only trust the accelerometer for tilt when it is close to 1 g
        use_accel = abs(norm3(accel) - G) < 0.1 * G
        self.filt.update(gyro, accel, dt, use_accel)

        # Linear acceleration in world frame (gravity removed)
        aw = rotate_to_world(self.filt.q, accel)
        lin = [aw[0], aw[1], aw[2] - G]

        # Stationary detection (ZUPT)
        if norm3(lin) < ZUPT_ACC_THRESH and norm3(gyro) < ZUPT_GYRO_THRESH:
            self.still_count += 1
        else:
            self.still_count = 0
        self.stationary = self.still_count >= ZUPT_SAMPLES

        if self.stationary:
            if self.in_seg:
                self._finish_segment()
            self.vel = [0.0, 0.0, 0.0]
            self.lin = [0.0, 0.0, 0.0]
            self.acc_prev = [0.0, 0.0, 0.0]
            for i in range(3):  # slowly track gyro bias while at rest
                self.g_bias[i] += BIAS_RATE * (g_raw[i] - self.g_bias[i])
            return

        if not self.in_seg:
            self._start_segment()

        lin = [0.0 if abs(c) < ACC_DEADBAND else c for c in lin]
        step = [0.0, 0.0, 0.0]
        for i in range(3):
            v_old = self.vel[i]
            self.vel[i] += 0.5 * (self.acc_prev[i] + lin[i]) * dt   # trapezoidal
            step[i] = 0.5 * (v_old + self.vel[i]) * dt
            self.pos[i] += step[i]
        self.dist += norm3(step)
        self.acc_prev = lin
        self.lin = lin
        self.seg_time += dt
        if len(self.seg) < MAX_SEG_SAMPLES:
            self.seg.append((list(self.vel), dt))
        else:
            self.seg_overflow = True


# ---------------- Calibration ----------------
def collect_still(imu, n):
    """Average n samples at the sample rate. Returns (accel_mean, gyro_mean, gyro_spread_dps)."""
    asum = [0.0, 0.0, 0.0]
    gsum = [0.0, 0.0, 0.0]
    gmin = [1e9] * 3
    gmax = [-1e9] * 3
    period = 1.0 / SAMPLE_RATE_HZ
    next_t = time.perf_counter()
    for _ in range(n):
        a, g = imu.read()
        for i in range(3):
            asum[i] += a[i]
            gsum[i] += g[i]
            gmin[i] = min(gmin[i], g[i])
            gmax[i] = max(gmax[i], g[i])
        next_t += period
        delay = next_t - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
    a_mean = [s / n for s in asum]
    g_mean = [s / n for s in gsum]
    spread = math.degrees(max(gmax[i] - gmin[i] for i in range(3)))
    return a_mean, g_mean, spread


def compute_six_pos(means):
    """means: six accel mean vectors in ORIENTATIONS order -> (offsets, scales) per axis."""
    off = [0.0, 0.0, 0.0]
    sc = [1.0, 1.0, 1.0]
    for axis in range(3):
        up = down = None
        for (_, ax, sign), m in zip(ORIENTATIONS, means):
            if ax == axis:
                if sign > 0:
                    up = m[axis]
                else:
                    down = m[axis]
        off[axis] = (up + down) / 2.0
        sc[axis] = 2.0 * G / (up - down)
    return off, sc


def six_position_calibration(imu, path):
    print("\n6-position accelerometer calibration")
    print("For each step, hold the sensor still against a flat, level surface")
    print("(a book or box edge works well) with the named axis straight up.")
    print("The X/Y arrows are printed on the board. Don't touch it while sampling.\n")
    means = []
    for idx, (desc, axis, sign) in enumerate(ORIENTATIONS, 1):
        while True:
            input(f"[{idx}/6] Place the sensor {desc}, then press Enter... ")
            print("   sampling, hold still...")
            a_mean, _, spread = collect_still(imu, SIX_POS_SAMPLES)
            align = sign * a_mean[axis] / norm3(a_mean)
            if spread > CAL_GYRO_SPREAD_DPS:
                print("   Sensor moved during sampling. Try again.")
            elif align < SIX_POS_MIN_ALIGN:
                tilt = math.degrees(math.acos(max(-1.0, min(1.0, align))))
                print(f"   Axis is ~{tilt:.0f} deg off vertical. Level it better and try again.")
            else:
                means.append(a_mean)
                print(f"   OK ({a_mean[0]:+.2f}, {a_mean[1]:+.2f}, {a_mean[2]:+.2f} m/s^2)")
                break

    off, sc = compute_six_pos(means)
    print("\nResults:")
    for name, o, s in zip("XYZ", off, sc):
        print(f"  {name}: offset {o:+.3f} m/s^2, scale {s:.4f}")
    if any(abs(s - 1.0) > 0.05 for s in sc):
        print("  Warning: a scale is >5% from 1.0. Check that each orientation was level.")
    data = {
        "accel_offset": off,
        "accel_scale": sc,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"\nSaved to {path}")


def load_calib(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            d = json.load(f)
        return list(d["accel_offset"]), list(d["accel_scale"])
    except (ValueError, KeyError, OSError) as e:
        print(f"Could not read {path}: {e}")
        return None


def calibrate(imu, a_off, a_sc, per_axis):
    """Hold still. Returns gyro_bias, accel_offset, accel_scale, initial quaternion."""
    while True:
        print(f"Calibrating: keep the sensor still for ~{CAL_SAMPLES / SAMPLE_RATE_HZ:.0f} s...")
        a_mean, g_bias, spread = collect_still(imu, CAL_SAMPLES)
        if spread > CAL_GYRO_SPREAD_DPS:
            print("Sensor moved during calibration, retrying...")
            continue

        a_off = list(a_off)
        a_sc = list(a_sc)
        a_cal = [(a_mean[i] - a_off[i]) * a_sc[i] for i in range(3)]
        mag = norm3(a_cal)
        if not per_axis:
            # No 6-position data: fall back to a single overall scale so |a| = 1 g
            s = G / mag
            a_sc = [x * s for x in a_sc]
            a_cal = [c * s for c in a_cal]
        elif abs(mag - G) > 0.03 * G:
            print(f"  Warning: resting magnitude {mag:.2f} m/s^2 is >3% from 1 g."
                  " Consider rerunning --calibrate-6pos.")

        q0 = quat_from_up_vector(a_cal)
        r, p, _ = euler_deg(q0)
        print(f"  gyro bias (rad/s): {g_bias[0]:+.4f} {g_bias[1]:+.4f} {g_bias[2]:+.4f}")
        print(f"  resting accel magnitude after calibration: {norm3(a_cal):.3f} m/s^2")
        print(f"  initial tilt: roll {r:+.1f} deg, pitch {p:+.1f} deg")
        return list(g_bias), a_off, a_sc, q0


# ---------------- Terminal helpers ----------------
class KeyReader:
    """Non-blocking single-key input (no Enter needed)."""

    def __init__(self):
        self.enabled = termios is not None and sys.stdin.isatty()
        self.old = None

    def __enter__(self):
        if self.enabled:
            self.old = termios.tcgetattr(sys.stdin)
            tty.setcbreak(sys.stdin.fileno())
        return self

    def __exit__(self, *exc):
        if self.enabled and self.old is not None:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self.old)

    def poll(self):
        if not self.enabled:
            return None
        r, _, _ = select.select([sys.stdin], [], [], 0)
        if r:
            return os.read(sys.stdin.fileno(), 1).decode(errors="ignore")
        return None


class Display:
    """Redraws a fixed 4-line block in place (each line fits in 80 columns)."""
    LINES = 4

    def __init__(self, unit):
        self.k, self.u = UNITS[unit]
        self.first = True

    def draw(self, tr):
        k, u = self.k, self.u
        roll, pitch, yaw = euler_deg(tr.filt.q)
        lines = [
            "Pos  [{:+9.2f} {:+9.2f} {:+9.2f}] {}".format(tr.pos[0] * k, tr.pos[1] * k, tr.pos[2] * k, u),
            "Vel  [{:+9.2f} {:+9.2f} {:+9.2f}] {}/s".format(tr.vel[0] * k, tr.vel[1] * k, tr.vel[2] * k, u),
            "Acc  [{:+9.2f} {:+9.2f} {:+9.2f}] {}/s^2".format(tr.lin[0] * k, tr.lin[1] * k, tr.lin[2] * k, u),
            "Disp {:8.2f} {}  Dist {:8.2f} {}  {:6s} R{:+4.0f} P{:+4.0f} Y{:+4.0f}".format(
                norm3(tr.pos) * k, u, tr.dist * k, u,
                "STILL" if tr.stationary else "MOVING", roll, pitch, yaw),
        ]
        out = ""
        if not self.first:
            out += "\033[%dA" % self.LINES
        for line in lines:
            out += "\r" + line + "\033[K\n"
        sys.stdout.write(out)
        sys.stdout.flush()
        self.first = False


# ---------------- Main loop ----------------
def main():
    ap = argparse.ArgumentParser(description="MPU6050 position/velocity/acceleration tracker")
    ap.add_argument("--addr", type=lambda s: int(s, 0), default=0x69, help="I2C address (default 0x69)")
    ap.add_argument("--bus", type=int, default=1, help="I2C bus number (default 1)")
    ap.add_argument("--units", choices=sorted(UNITS), default="cm", help="display units (default cm)")
    ap.add_argument("--log", help="CSV log file path (default: imu_log_<date>_<time>.csv in the current folder)")
    ap.add_argument("--no-log", action="store_true", help="disable CSV logging")
    ap.add_argument("--calib", default=CALIB_FILE, help=f"calibration file (default {CALIB_FILE})")
    ap.add_argument("--calibrate-6pos", action="store_true", help="run the 6-position accel calibration and exit")
    args = ap.parse_args()

    try:
        imu = MPU6050(args.bus, args.addr)
        imu.start()
    except (OSError, RuntimeError) as e:
        print(f"Could not talk to MPU6050 at 0x{args.addr:02X}: {e}")
        print("Check wiring (pins 2,3,5,6), that I2C is enabled, and `i2cdetect -y 1`.")
        sys.exit(1)
    print(f"Connected to MPU6050 at 0x{args.addr:02X}")

    if args.calibrate_6pos:
        try:
            six_position_calibration(imu, args.calib)
        except KeyboardInterrupt:
            print("\nCancelled, nothing saved.")
        finally:
            imu.close()
        return

    cal = load_calib(args.calib)
    per_axis = cal is not None
    if per_axis:
        a_off, a_sc = cal
        print(f"Loaded per-axis accel calibration from {args.calib}")
    else:
        a_off, a_sc = [0.0, 0.0, 0.0], [1.0, 1.0, 1.0]
        print(f"WARNING: no {args.calib} found. Run `python3 imu_tracker.py --calibrate-6pos` first;")
        print("         without it, accel offsets will cause large position drift.")

    g_bias, a_off, a_sc, q0 = calibrate(imu, a_off, a_sc, per_axis)
    tracker = Tracker(a_off, a_sc, g_bias, q0)
    display = Display(args.units)

    log_path = None
    if not args.no_log:
        log_path = args.log or time.strftime("imu_log_%Y%m%d_%H%M%S.csv")
    logf = None
    if log_path:
        logf = open(log_path, "w")
        logf.write("t,px,py,pz,vx,vy,vz,ax,ay,az,roll,pitch,yaw,disp,dist,stationary\n")
        print(f"Logging to {os.path.abspath(log_path)} (SI units: m, m/s, m/s^2)")

    print("\nTracking. Keys: z = zero position, c = recalibrate, q = quit. Ctrl-C also quits.\n")
    t_start = time.perf_counter()
    t_prev = t_start
    next_t = t_start
    next_print = t_start
    period = 1.0 / SAMPLE_RATE_HZ

    try:
        with KeyReader() as keys:
            while True:
                a_raw, g_raw = imu.read()
                now = time.perf_counter()
                dt = min(max(now - t_prev, 0.001), 0.1)
                t_prev = now

                tracker.update(a_raw, g_raw, dt)

                if logf:
                    roll, pitch, yaw = euler_deg(tracker.filt.q)
                    logf.write(
                        f"{now - t_start:.3f},{tracker.pos[0]:.4f},{tracker.pos[1]:.4f},{tracker.pos[2]:.4f},"
                        f"{tracker.vel[0]:.4f},{tracker.vel[1]:.4f},{tracker.vel[2]:.4f},"
                        f"{tracker.lin[0]:.4f},{tracker.lin[1]:.4f},{tracker.lin[2]:.4f},"
                        f"{roll:.2f},{pitch:.2f},{yaw:.2f},{norm3(tracker.pos):.4f},{tracker.dist:.4f},"
                        f"{int(tracker.stationary)}\n"
                    )

                if now >= next_print:
                    next_print = now + 0.1
                    display.draw(tracker)
                    if logf:
                        logf.flush()

                key = keys.poll()
                if key:
                    key = key.lower()
                    if key == "q":
                        break
                    elif key == "z":
                        tracker.zero()
                    elif key == "c":
                        print("\nRecalibrating, keep the sensor still...")
                        g_bias, a_off, a_sc, q0 = calibrate(imu, tracker.a_off, tracker.a_sc, per_axis)
                        tracker = Tracker(a_off, a_sc, g_bias, q0)
                        display.first = True
                        print()
                        t_prev = next_t = time.perf_counter()

                next_t += period
                delay = next_t - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                else:
                    next_t = time.perf_counter()  # fell behind, resync
    except KeyboardInterrupt:
        pass
    finally:
        if logf:
            logf.close()
        imu.close()

    u = UNITS[args.units]
    print("\nStopped.")
    print(f"Final position: {tracker.pos[0] * u[0]:+.2f}, {tracker.pos[1] * u[0]:+.2f}, "
          f"{tracker.pos[2] * u[0]:+.2f} {u[1]}")
    print(f"Displacement (straight-line from origin): {norm3(tracker.pos) * u[0]:.2f} {u[1]}")
    print(f"Distance traveled (path length): {tracker.dist * u[0]:.2f} {u[1]}")


if __name__ == "__main__":
    main()
