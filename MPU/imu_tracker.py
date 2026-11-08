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
    sudo raspi-config        # Interface Options -> I2C -> Enable
    sudo apt install python3-smbus      (or: pip3 install smbus2)
    i2cdetect -y 1                      # should show 69 (or 68)

Run (keep the sensor completely still for the first ~3 seconds):
    python3 imu_tracker.py
    python3 imu_tracker.py --addr 0x68 --log run.csv

What it does:
    1. Wakes and configures the MPU6050 (+/-4 g, +/-500 dps, 44 Hz DLPF, 100 Hz).
    2. Calibrates: averages still samples to get gyro bias, the initial tilt,
       and an accel scale factor so that the resting magnitude equals 1 g.
    3. Tracks orientation with a Mahony complementary filter (gyro + accel).
    4. Rotates accel into a fixed world frame (Z up) and subtracts gravity.
    5. Integrates to velocity and position, with zero-velocity updates (ZUPT)
       whenever the sensor is detected to be stationary, to limit drift.

Output frame: world X/Y/Z, Z up. X/Y are fixed by the sensor's initial
heading (no magnetometer, so yaw is relative to the start and slowly drifts).

Accuracy note: double-integrating a MEMS accelerometer drifts quickly
(error grows ~ t^2). Expect usable results over a few seconds of motion,
not minutes. ZUPT resets velocity error each time the sensor comes to rest.
"""

import argparse
import math
import sys
import time

try:
    from smbus2 import SMBus
except ImportError:
    from smbus import SMBus

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
CAL_SAMPLES = 300            # ~3 s of stillness for calibration
CAL_GYRO_MAX_DPS = 3.0       # reject calibration if gyro varies more than this

KP = 2.0                     # Mahony proportional gain
KI = 0.005                   # Mahony integral gain

ZUPT_ACC_THRESH = 0.20       # m/s^2, linear accel below this counts as still
ZUPT_GYRO_THRESH = 0.06      # rad/s (~3.4 deg/s)
ZUPT_SAMPLES = 25            # consecutive still samples (~0.25 s) to trigger
ACC_DEADBAND = 0.05          # m/s^2, ignore tiny residual accel


def to_int16(hi, lo):
    v = (hi << 8) | lo
    return v - 65536 if v & 0x8000 else v


class MPU6050:
    def __init__(self, bus_num=1, addr=0x69):
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
        """Return (ax, ay, az) in m/s^2 (unscaled) and (gx, gy, gz) in rad/s (raw)."""
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
    # axis = u x z = (uy, -ux, 0)
    w, x, y, z = 1.0 + dot, uy, -ux, 0.0
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


# ---------------- Calibration ----------------
def calibrate(imu):
    """Hold the sensor still. Returns gyro_bias, accel_scale, initial quaternion."""
    while True:
        print(f"Calibrating: keep the sensor still for ~{CAL_SAMPLES / SAMPLE_RATE_HZ:.0f} s...")
        asum = [0.0, 0.0, 0.0]
        gsum = [0.0, 0.0, 0.0]
        gmin = [1e9] * 3
        gmax = [-1e9] * 3
        period = 1.0 / SAMPLE_RATE_HZ
        next_t = time.perf_counter()
        for _ in range(CAL_SAMPLES):
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

        spread = max(gmax[i] - gmin[i] for i in range(3))
        if math.degrees(spread) > CAL_GYRO_MAX_DPS * 2:
            print("Sensor moved during calibration, retrying...")
            continue

        a_mean = tuple(s / CAL_SAMPLES for s in asum)
        g_bias = tuple(s / CAL_SAMPLES for s in gsum)
        a_scale = G / norm3(a_mean)
        a_cal = tuple(c * a_scale for c in a_mean)
        q0 = quat_from_up_vector(a_cal)
        print(f"  gyro bias (rad/s): {g_bias[0]:+.4f} {g_bias[1]:+.4f} {g_bias[2]:+.4f}")
        print(f"  accel scale factor: {a_scale:.4f} (resting magnitude was {norm3(a_mean):.3f} m/s^2)")
        r, p, _ = euler_deg(q0)
        print(f"  initial tilt: roll {r:+.1f} deg, pitch {p:+.1f} deg")
        return g_bias, a_scale, q0


# ---------------- Main loop ----------------
def main():
    ap = argparse.ArgumentParser(description="MPU6050 position/velocity/acceleration tracker")
    ap.add_argument("--addr", type=lambda s: int(s, 0), default=0x69, help="I2C address (default 0x69)")
    ap.add_argument("--bus", type=int, default=1, help="I2C bus number (default 1)")
    ap.add_argument("--log", help="optional CSV log file")
    args = ap.parse_args()

    imu = MPU6050(args.bus, args.addr)
    try:
        imu.start()
    except OSError as e:
        print(f"Could not talk to MPU6050 at 0x{args.addr:02X}: {e}")
        print("Check wiring (pins 2,3,5,6), that I2C is enabled, and `i2cdetect -y 1`.")
        sys.exit(1)
    print(f"Connected to MPU6050 at 0x{args.addr:02X}")

    g_bias, a_scale, q0 = calibrate(imu)
    filt = Mahony(q0)

    pos = [0.0, 0.0, 0.0]
    vel = [0.0, 0.0, 0.0]
    acc_prev = [0.0, 0.0, 0.0]
    dist = 0.0
    still_count = 0
    stationary = True

    logf = None
    if args.log:
        logf = open(args.log, "w")
        logf.write("t,px,py,pz,vx,vy,vz,ax,ay,az,roll,pitch,yaw,disp,dist,stationary\n")

    print("Tracking. Ctrl-C to stop.\n")
    t_start = time.perf_counter()
    t_prev = t_start
    next_t = t_start
    next_print = t_start
    period = 1.0 / SAMPLE_RATE_HZ

    try:
        while True:
            a_raw, g_raw = imu.read()
            now = time.perf_counter()
            dt = min(max(now - t_prev, 0.001), 0.1)
            t_prev = now

            accel = (a_raw[0] * a_scale, a_raw[1] * a_scale, a_raw[2] * a_scale)
            gyro = (g_raw[0] - g_bias[0], g_raw[1] - g_bias[1], g_raw[2] - g_bias[2])

            # Only trust the accelerometer for tilt when it is close to 1 g
            a_mag = norm3(accel)
            use_accel = abs(a_mag - G) < 0.1 * G
            filt.update(gyro, accel, dt, use_accel)

            # Linear acceleration in world frame (gravity removed)
            aw = rotate_to_world(filt.q, accel)
            lin = [aw[0], aw[1], aw[2] - G]

            # Stationary detection (ZUPT)
            if norm3(lin) < ZUPT_ACC_THRESH and norm3(gyro) < ZUPT_GYRO_THRESH:
                still_count += 1
            else:
                still_count = 0
            stationary = still_count >= ZUPT_SAMPLES

            if stationary:
                vel = [0.0, 0.0, 0.0]
                lin = [0.0, 0.0, 0.0]
            else:
                lin = [0.0 if abs(c) < ACC_DEADBAND else c for c in lin]
                step = [0.0, 0.0, 0.0]
                for i in range(3):
                    v_old = vel[i]
                    vel[i] += 0.5 * (acc_prev[i] + lin[i]) * dt   # trapezoidal
                    step[i] = 0.5 * (v_old + vel[i]) * dt
                    pos[i] += step[i]
                dist += norm3(step)
            acc_prev = lin

            disp = norm3(pos)
            roll, pitch, yaw = euler_deg(filt.q)
            t_rel = now - t_start

            if logf:
                logf.write(
                    f"{t_rel:.3f},{pos[0]:.4f},{pos[1]:.4f},{pos[2]:.4f},"
                    f"{vel[0]:.4f},{vel[1]:.4f},{vel[2]:.4f},"
                    f"{lin[0]:.4f},{lin[1]:.4f},{lin[2]:.4f},"
                    f"{roll:.2f},{pitch:.2f},{yaw:.2f},{disp:.4f},{dist:.4f},{int(stationary)}\n"
                )

            if now >= next_print:
                next_print = now + 0.1
                sys.stdout.write(
                    "\rPos[{:+7.3f} {:+7.3f} {:+7.3f}] m  "
                    "Vel[{:+6.3f} {:+6.3f} {:+6.3f}] m/s  "
                    "Acc[{:+6.3f} {:+6.3f} {:+6.3f}] m/s2  "
                    "Disp {:6.3f} m  Dist {:6.3f} m  {}   ".format(
                        pos[0], pos[1], pos[2],
                        vel[0], vel[1], vel[2],
                        lin[0], lin[1], lin[2],
                        disp, dist,
                        "STILL" if stationary else "MOVING",
                    )
                )
                sys.stdout.flush()

            next_t += period
            delay = next_t - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                next_t = time.perf_counter()  # fell behind, resync
    except KeyboardInterrupt:
        print("\n\nStopped.")
        print(f"Final position: {pos[0]:+.3f}, {pos[1]:+.3f}, {pos[2]:+.3f} m")
        print(f"Displacement (straight-line from start): {norm3(pos):.3f} m")
        print(f"Distance traveled (path length): {dist:.3f} m")
    finally:
        if logf:
            logf.close()
        imu.close()


if __name__ == "__main__":
    main()
