#!/usr/bin/env python3
"""Team-6 Raspberry Pi agent: the star camera and the MPU6050 IMU over plain HTTP.

You normally never touch this file. The ground server copies it to the Pi over SSH and
starts it (IMU page -> "Connect to the Pi"). It needs only the Python standard library
plus what Raspberry Pi OS already ships:
  - IMU:    smbus2 or smbus (python3-smbus), with I2C enabled
  - camera: picamera2, or failing that the rpicam-still / libcamera-still command

Endpoints (port 5000 by default):
  GET  /health          what works: {"ok", "imu": {...}, "camera": {...}}
  GET  /imu             the latest IMU sample as JSON (100 Hz filter, read as often as you like)
  POST /imu/calibrate   hold the sensor still: re-measure the gyro bias and re-level
  GET  /frame           one camera frame as a JPEG (the star camera solves this)
  GET  /plane           newest tower report from the Heltec plugged into the Pi's USB:
                        {"seq", "report": {"towers": [...]}, "age_s", "port", "error"}

Run it by hand on the Pi:     python3 team6_pi_agent.py
Run it on a laptop, no Pi:    python team6_pi_agent.py --fake-imu --frame-file some_sky.png
"""
from __future__ import annotations

import argparse
import io
import json
import math
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = 1

# ---- MPU6050 -------------------------------------------------------------------------------------
REG_SMPLRT_DIV, REG_CONFIG, REG_GYRO_CONFIG, REG_ACCEL_CONFIG = 0x19, 0x1A, 0x1B, 0x1C
REG_ACCEL_XOUT_H, REG_PWR_MGMT_1, REG_WHO_AM_I = 0x3B, 0x6B, 0x75
G = 9.80665
ACCEL_LSB_PER_G = 8192.0   # +/-4 g
GYRO_LSB_PER_DPS = 65.5    # +/-500 deg/s
RATE_HZ = 100
KP, KI = 2.0, 0.005        # Mahony filter gains (same as MPU/imu_tracker.py)


def _i16(hi, lo):
    v = (hi << 8) | lo
    return v - 65536 if v & 0x8000 else v


class Mpu6050:
    def __init__(self, addr: int | None, bus_num: int = 1):
        try:
            from smbus2 import SMBus
        except ImportError:
            from smbus import SMBus  # python3-smbus, preinstalled on Raspberry Pi OS
        self.bus = SMBus(bus_num)
        self.addr = addr if addr is not None else self._find()
        self.bus.write_byte_data(self.addr, REG_PWR_MGMT_1, 0x80)  # reset
        time.sleep(0.1)
        self.bus.write_byte_data(self.addr, REG_PWR_MGMT_1, 0x01)  # wake, PLL clock
        time.sleep(0.05)
        self.bus.write_byte_data(self.addr, REG_CONFIG, 0x03)       # ~44 Hz low-pass
        self.bus.write_byte_data(self.addr, REG_SMPLRT_DIV, 1000 // RATE_HZ - 1)
        self.bus.write_byte_data(self.addr, REG_GYRO_CONFIG, 0x08)
        self.bus.write_byte_data(self.addr, REG_ACCEL_CONFIG, 0x08)

    def _find(self) -> int:
        for a in (0x68, 0x69):
            try:
                if self.bus.read_byte_data(a, REG_WHO_AM_I) in (0x68, 0x69, 0x70, 0x71, 0x72, 0x98):
                    return a
            except OSError:
                pass
        raise RuntimeError("no MPU6050 at 0x68 or 0x69 - check the wiring and `i2cdetect -y 1`")

    def read(self):
        """(accel m/s^2, gyro rad/s, temp C) in the sensor's own axes."""
        d = self.bus.read_i2c_block_data(self.addr, REG_ACCEL_XOUT_H, 14)
        a = [_i16(d[i], d[i + 1]) / ACCEL_LSB_PER_G * G for i in (0, 2, 4)]
        temp = _i16(d[6], d[7]) / 340.0 + 36.53
        g = [math.radians(_i16(d[i], d[i + 1]) / GYRO_LSB_PER_DPS) for i in (8, 10, 12)]
        return a, g, temp


class FakeImu:
    """A gentle simulated flight, so everything downstream can be tested with no sensor."""

    addr = None

    def __init__(self):
        self.t0 = time.time()

    def read(self):
        t = time.time() - self.t0
        roll, pitch = math.radians(35) * math.sin(0.5 * t), math.radians(12) * math.sin(0.3 * t)
        yaw = math.radians(25) * t
        q = q_from_euler(roll, pitch, yaw)
        # gravity seen by the sensor = R^T * (0, 0, g)
        w, x, y, z = q
        a = [2 * (x * z - w * y) * G, 2 * (w * x + y * z) * G, (w * w - x * x - y * y + z * z) * G]
        gyro = [math.radians(35) * 0.5 * math.cos(0.5 * t), math.radians(12) * 0.3 * math.cos(0.3 * t), math.radians(25)]
        return a, gyro, 25.0


# ---- quaternion helpers (q = [w, x, y, z], sensor -> world, world Z up) --------------------------
def q_mul(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return [aw * bw - ax * bx - ay * by - az * bz, aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx, aw * bz + ax * by - ay * bx + az * bw]


def q_norm(q):
    n = math.sqrt(sum(v * v for v in q)) or 1.0
    return [v / n for v in q]


def q_from_euler(roll, pitch, yaw):
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    return [cr * cp * cy + sr * sp * sy, sr * cp * cy - cr * sp * sy,
            cr * sp * cy + sr * cp * sy, cr * cp * sy - sr * sp * cy]


def euler_deg(q):
    """(roll, pitch, yaw) in degrees, aviation signs: right wing down, nose up, turn right = +."""
    w, x, y, z = q
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return math.degrees(roll), -math.degrees(pitch), -math.degrees(yaw)


class ImuThread(threading.Thread):
    """Reads the sensor at 100 Hz and keeps an orientation with a Mahony filter."""

    def __init__(self, sensor):
        super().__init__(daemon=True)
        self.sensor = sensor
        self.lock = threading.Lock()
        self.latest: dict | None = None
        self.error: str | None = None
        self.want_cal = threading.Event()
        self.want_cal.set()
        self.calibrating = False
        self.bias = [0.0, 0.0, 0.0]
        self.q = [1.0, 0.0, 0.0, 0.0]
        self.e_int = [0.0, 0.0, 0.0]
        self.n = 0
        self.rate = 0.0

    def calibrate(self, seconds=2.0):
        self.calibrating = True
        samples = []
        end = time.time() + seconds
        while time.time() < end:
            samples.append(self.sensor.read())
            time.sleep(1.0 / RATE_HZ)
        k = len(samples)
        a = [sum(s[0][i] for s in samples) / k for i in range(3)]
        self.bias = [sum(s[1][i] for s in samples) / k for i in range(3)] if not isinstance(self.sensor, FakeImu) else [0, 0, 0]
        roll = math.atan2(a[1], a[2])
        pitch = math.atan2(-a[0], math.hypot(a[1], a[2]))
        self.q = q_from_euler(roll, pitch, 0.0)
        self.e_int = [0.0, 0.0, 0.0]
        self.calibrating = False

    def step(self, a, g, dt):
        an = math.sqrt(sum(v * v for v in a))
        if 0.85 * G < an < 1.15 * G:  # only trust the accel as "down" when not accelerating hard
            ax, ay, az = (v / an for v in a)
            w, x, y, z = self.q
            vx, vy, vz = 2 * (x * z - w * y), 2 * (w * x + y * z), w * w - x * x - y * y + z * z
            e = [ay * vz - az * vy, az * vx - ax * vz, ax * vy - ay * vx]
            self.e_int = [self.e_int[i] + KI * e[i] * dt for i in range(3)]
            g = [g[i] + KP * e[i] + self.e_int[i] for i in range(3)]
        dq = q_mul(self.q, [0.0, *g])
        self.q = q_norm([self.q[i] + 0.5 * dq[i] * dt for i in range(4)])

    def run(self):
        last = time.time()
        rate_t0, rate_n = last, 0
        while True:
            try:
                if self.want_cal.is_set():
                    self.want_cal.clear()
                    self.calibrate()
                    last = time.time()
                a, graw, temp = self.sensor.read()
                now = time.time()
                dt = min(max(now - last, 1e-4), 0.1)
                last = now
                g = [graw[i] - self.bias[i] for i in range(3)]
                if isinstance(self.sensor, FakeImu):
                    self.q = q_from_euler(*(math.radians(v) for v in self._fake_euler(now)))
                else:
                    self.step(a, g, dt)
                self.n += 1
                rate_n += 1
                if now - rate_t0 >= 1.0:
                    self.rate, rate_t0, rate_n = rate_n / (now - rate_t0), now, 0
                r, p, y = euler_deg(self.q)
                with self.lock:
                    self.latest = {
                        "t": now, "n": self.n, "rate_hz": round(self.rate, 1),
                        "accel": [round(v, 4) for v in a], "gyro": [round(v, 5) for v in g],
                        "temp_c": round(temp, 2), "q": [round(v, 6) for v in self.q],
                        "roll": round(r, 2), "pitch": round(p, 2), "yaw": round(y, 2),
                    }
                self.error = None
                time.sleep(max(0.0, 1.0 / RATE_HZ - (time.time() - now)))
            except Exception as e:  # keep serving, report what went wrong
                self.error = f"{type(e).__name__}: {e}"
                time.sleep(0.5)

    def _fake_euler(self, now):
        t = now - self.sensor.t0
        return 35 * math.sin(0.5 * t), 12 * math.sin(0.3 * t), (25 * t + 180) % 360 - 180


# ---- camera --------------------------------------------------------------------------------------
class Camera:
    """picamera2 if it imports, else the rpicam-still / libcamera-still command."""

    def __init__(self, size: int, frame_file: str | None):
        self.size = size
        self.frame_file = frame_file
        self.lock = threading.Lock()
        self.picam = None
        self.mode = None
        self.error = None

    def _open(self):
        if self.mode:
            return
        if self.frame_file:
            self.mode = "file"
            return
        try:
            from picamera2 import Picamera2
            cam = Picamera2()
            cam.configure(cam.create_still_configuration(main={"size": (self.size, self.size)}))
            cam.start()
            time.sleep(1.0)  # let exposure settle
            self.picam, self.mode = cam, "picamera2"
            return
        except Exception as e:
            self.error = f"picamera2 unavailable ({e})"
        for cmd in ("rpicam-still", "libcamera-still"):
            if shutil.which(cmd):
                self.mode = cmd
                return
        raise RuntimeError("no camera: picamera2 failed and neither rpicam-still nor libcamera-still is installed")

    def jpeg(self) -> tuple[bytes, str]:
        with self.lock:
            self._open()
            if self.mode == "file":
                with open(self.frame_file, "rb") as f:
                    data = f.read()
                return data, ("image/png" if self.frame_file.lower().endswith(".png") else "image/jpeg")
            if self.mode == "picamera2":
                buf = io.BytesIO()
                self.picam.capture_file(buf, format="jpeg")
                return buf.getvalue(), "image/jpeg"
            out = subprocess.run(
                [self.mode, "-n", "-t", "500", "--width", str(self.size), "--height", str(self.size), "-o", "-"],
                capture_output=True, timeout=20,
            )
            if out.returncode != 0 or not out.stdout:
                raise RuntimeError(f"{self.mode} failed: {out.stderr.decode(errors='replace')[-300:]}")
            return out.stdout, "image/jpeg"

    def info(self) -> dict:
        return {"mode": self.mode, "error": self.error}


# ---- the plane (Heltec) on USB: tower RSSI reports ------------------------------------------------
class PlaneSerial(threading.Thread):
    """Reads the Heltec's JSON lines (one report per 200 ms) from its USB serial port.

    Stdlib only (termios), so nothing to install. The newest report is kept for GET /plane;
    the laptop server turns the tower distances into a position.
    """

    def __init__(self, port: str | None, baud: int = 115200):
        super().__init__(daemon=True)
        self.want_port, self.baud = port, baud
        self.port: str | None = None
        self.lock = threading.Lock()
        self.latest: dict | None = None
        self.seq = 0
        self.last_at = 0.0
        self.error: str | None = "starting"

    @staticmethod
    def _candidates() -> list[str]:
        import glob
        return sorted(glob.glob("/dev/ttyACM*")) + sorted(glob.glob("/dev/ttyUSB*"))

    def _open(self, path: str) -> int:
        import os
        import termios
        fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        attrs = termios.tcgetattr(fd)
        speed = getattr(termios, f"B{self.baud}")
        attrs[0] = attrs[1] = attrs[3] = 0                      # raw: no input/output/local processing
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL  # 8N1, no modem lines
        attrs[4] = attrs[5] = speed
        attrs[6][termios.VMIN], attrs[6][termios.VTIME] = 0, 0
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        return fd

    def run(self):
        import os
        import select
        while True:
            paths = [self.want_port] if self.want_port else self._candidates()
            if not paths:
                self.error = "no USB serial port - is the Heltec plugged into the Pi?"
                time.sleep(1.0)
                continue
            path = paths[0]
            try:
                fd = self._open(path)
            except Exception as e:
                self.error = f"cannot open {path}: {e}" + (
                    " (add the user to the dialout group: sudo usermod -aG dialout $USER, then log in again)"
                    if isinstance(e, PermissionError) else "")
                time.sleep(1.0)
                continue
            self.port, self.error = path, None
            buf = b""
            try:
                while True:
                    if not select.select([fd], [], [], 1.0)[0]:
                        continue
                    chunk = os.read(fd, 4096)
                    if not chunk:
                        raise OSError("port closed")
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        self._line(line.decode("utf-8", errors="ignore").strip())
                    if len(buf) > 8192:
                        buf = b""
            except Exception as e:
                self.error = f"lost {path}: {e}"
            finally:
                try:
                    os.close(fd)
                except OSError:
                    pass
                self.port = None
            time.sleep(1.0)

    def _line(self, line: str):
        if not line.startswith("{"):
            return  # firmware debug prints
        try:
            report = json.loads(line)
        except ValueError:
            return
        if isinstance(report, dict) and "towers" in report:
            with self.lock:
                self.latest, self.last_at, self.seq = report, time.time(), self.seq + 1

    def info(self) -> dict:
        age = round(time.time() - self.last_at, 2) if self.last_at else None
        return {"ok": self.error is None and age is not None and age < 3, "port": self.port,
                "error": self.error, "reports": self.seq, "age_s": age}


# ---- HTTP ----------------------------------------------------------------------------------------
imu: ImuThread | None = None
imu_error: str | None = None
camera: Camera | None = None
plane: PlaneSerial | None = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quiet: /imu is polled 20x a second
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _imu_info(self):
        if imu is None:
            return {"ok": False, "error": imu_error}
        return {"ok": imu.latest is not None and imu.error is None, "error": imu.error,
                "calibrating": imu.calibrating, "addr": imu.sensor.addr, "fake": isinstance(imu.sensor, FakeImu)}

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/health":
            self._json(200, {"ok": True, "version": VERSION, "imu": self._imu_info(),
                             "camera": camera.info() if camera else {"mode": None, "error": "disabled"},
                             "plane": plane.info() if plane else {"ok": False, "error": "disabled"}})
        elif path == "/plane":
            if plane is None:
                return self._json(503, {"error": "plane serial disabled"})
            with plane.lock:
                body = {"seq": plane.seq, "report": plane.latest}
            body.update(plane.info())
            self._json(200 if plane.latest else 503, body)
        elif path == "/imu":
            if imu is None:
                return self._json(503, {"error": imu_error or "IMU disabled"})
            with imu.lock:
                sample = dict(imu.latest) if imu.latest else None
            if sample is None:
                return self._json(503, {"error": imu.error or "IMU starting (calibrating - keep it still)"})
            sample.update(calibrating=imu.calibrating, error=imu.error)
            self._json(200, sample)
        elif path == "/frame":
            if camera is None:
                return self._json(503, {"error": "camera disabled"})
            try:
                data, ctype = camera.jpeg()
                self._send(200, data, ctype)
            except Exception as e:
                camera.error = str(e)
                self._json(500, {"error": str(e)})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path.split("?")[0] == "/imu/calibrate" and imu is not None:
            imu.want_cal.set()
            return self._json(200, {"ok": True, "note": "calibrating for 2 s - keep the sensor still"})
        self._json(404, {"error": "not found"})


def main():
    global imu, imu_error, camera, plane
    ap =argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--imu-addr", type=lambda s: int(s, 0), default=None, help="0x68 or 0x69 (default: find it)")
    ap.add_argument("--fake-imu", action="store_true", help="simulated IMU, no sensor needed")
    ap.add_argument("--no-imu", action="store_true")
    ap.add_argument("--no-camera", action="store_true")
    ap.add_argument("--no-plane", action="store_true", help="don't read the Heltec on USB")
    ap.add_argument("--plane-port", help="serial port of the Heltec (default: first /dev/ttyACM* or ttyUSB*)")
    ap.add_argument("--frame-file", help="serve this image as the camera frame (testing)")
    ap.add_argument("--size", type=int, default=1024, help="camera frame size in pixels (square)")
    a = ap.parse_args()

    if not a.no_imu:
        try:
            imu = ImuThread(FakeImu() if a.fake_imu else Mpu6050(a.imu_addr))
            imu.start()
            print(f"IMU: {'simulated' if a.fake_imu else f'MPU6050 at {hex(imu.sensor.addr)}'} - keep it still for 2 s", flush=True)
        except Exception as e:
            imu_error = f"{type(e).__name__}: {e}"
            print(f"IMU unavailable: {imu_error}", flush=True)
    if not a.no_camera:
        camera = Camera(a.size, a.frame_file)
    if not a.no_plane and sys.platform != "win32":
        plane = PlaneSerial(a.plane_port)
        plane.start()
        print("plane: reading the Heltec's tower reports from USB serial", flush=True)

    srv = ThreadingHTTPServer(("0.0.0.0", a.port), Handler)
    print(f"Team-6 Pi agent v{VERSION} listening on port {a.port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
