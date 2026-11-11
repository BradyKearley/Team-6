"""The Raspberry Pi: start its agent over SSH, then poll its IMU over HTTP.

The Pi runs `pi/team6_pi_agent.py` (stdlib-only HTTP server). This module can put it there:
`deploy()` SSHes in with paramiko, copies the file, starts it in the background and waits
for it to answer. From then on everything is plain HTTP over Wi-Fi - no Bluetooth:

    GET http://<pi>:5000/imu     -> polled here ~20x a second, sent to the browser over /ws
    GET http://<pi>:5000/frame   -> the star camera (app/star_camera.py is pointed at it)

The SSH password is held in memory for this server run only (so "Stop" can SSH back in);
it is never written to disk or sent to the browser.

There is also a "sim" mode - a simulated IMU computed right here - so the page can be
tried with no Pi at all.
"""
from __future__ import annotations

import asyncio
import http.client
import json
import logging
import math
import shlex
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger("pi")

AGENT = Path(__file__).resolve().parents[2] / "pi" / "team6_pi_agent.py"
REMOTE_AGENT = "team6_pi_agent.py"           # in the Pi user's home folder
REMOTE_LOG = "team6_pi_agent.log"
POLL_HZ = 20


class PiError(Exception):
    """Something the user can act on (wrong password, Pi unreachable, ...)."""


@dataclass
class PiLink:
    mode: str = "off"                 # off | connecting | live | sim | error
    base_url: str | None = None       # http://<pi>:5000
    host: str | None = None
    user: str | None = None
    port: int = 5000
    imu: dict | None = None           # latest sample from the agent (or the sim)
    imu_error: str | None = None
    health: dict | None = None        # last /health answer
    last_ok: float = 0.0
    log: list[str] = field(default_factory=list)  # what deploy() did, shown in the UI
    _password: str | None = None
    _sim_t0: float = 0.0

    def say(self, msg: str) -> None:
        log.info(msg)
        self.log.append(msg)
        del self.log[:-40]

    def snapshot(self) -> dict:
        age = time.time() - self.last_ok if self.last_ok else None
        return {
            "mode": self.mode, "base_url": self.base_url, "host": self.host, "user": self.user,
            "imu": self.imu, "imu_error": self.imu_error, "age_s": None if age is None else round(age, 2),
            "health": self.health, "log": self.log[-12:],
        }


pi = PiLink()


# ---- SSH ------------------------------------------------------------------------------------------
def _ssh(host: str, user: str, password: str | None):
    try:
        import paramiko
    except ImportError as e:
        raise PiError("paramiko is not installed: run  .venv\\Scripts\\pip install paramiko") from e
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        c.connect(host, username=user, password=password or None, timeout=8,
                  look_for_keys=not password, allow_agent=not password)
    except paramiko.AuthenticationException as e:
        raise PiError(f"SSH login to {user}@{host} was refused - check the username and password") from e
    except Exception as e:
        raise PiError(f"cannot SSH to {host}: {e}") from e
    return c


def _run(c, cmd: str, timeout: float = 20) -> tuple[int, str]:
    _, out, err = c.exec_command(cmd, timeout=timeout)
    text = out.read().decode(errors="replace") + err.read().decode(errors="replace")
    return out.channel.recv_exit_status(), text.strip()


def _get_json(url: str, timeout: float = 2.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def deploy(host: str, user: str, password: str | None, port: int = 5000,
           fake_imu: bool = False) -> dict:
    """Copy the agent to the Pi, (re)start it, wait for it to answer. Blocking - run in a thread."""
    pi.log.clear()
    pi.mode, pi.host, pi.user, pi.port, pi._password = "connecting", host, user, port, password
    pi.base_url = None
    try:
        pi.say(f"SSH to {user}@{host} ...")
        c = _ssh(host, user, password)
        try:
            pi.say("connected; copying team6_pi_agent.py")
            sftp = c.open_sftp()
            sftp.put(str(AGENT), REMOTE_AGENT)
            sftp.close()

            code, out = _run(c, "test -e /dev/i2c-1 && echo yes || echo no")
            if out.strip().endswith("no"):
                pi.say("I2C is off; turning it on (sudo raspi-config nonint do_i2c 0)")
                code, out = _run(c, "sudo -n raspi-config nonint do_i2c 0 && sudo -n modprobe i2c-dev; test -e /dev/i2c-1 && echo yes || echo no")
                pi.say("I2C is on" if out.strip().endswith("yes") else
                       f"could not enable I2C automatically ({out[-160:]}) - run `sudo raspi-config` on the Pi")
            code, out = _run(c, "python3 -c 'import smbus2' 2>/dev/null && echo smbus2 || "
                                "(python3 -c 'import smbus' 2>/dev/null && echo smbus || echo none)")
            pi.say(f"I2C library: {out}" + ("  (install with: sudo apt install python3-smbus)" if out == "none" else ""))
            code, out = _run(c, "command -v i2cdetect >/dev/null && i2cdetect -y 1 | grep -Eo ' (68|69) ' | head -1 || true")
            pi.say(f"MPU6050 found at 0x{out.strip()}" if out.strip() else "no MPU6050 seen at 0x68/0x69 on I2C bus 1 (check wiring)")

            # Stop anything holding the port or the camera: an older agent, or star-tracker/camera.py.
            # ([t] so the pattern doesn't match the shell running this very command)
            _run(c, "pkill -f '[t]eam6_pi_agent.py'; pkill -f '[c]amera\\.py'; sleep 1; true")
            args = f"--port {int(port)}" + (" --fake-imu" if fake_imu else "")
            _run(c, f"nohup python3 -u ~/{REMOTE_AGENT} {args} > ~/{REMOTE_LOG} 2>&1 < /dev/null &", timeout=5)
            pi.say(f"agent started on port {port}; waiting for it to answer")

            base = f"http://{host}:{int(port)}"
            for _ in range(30):
                time.sleep(0.5)
                try:
                    pi.health = _get_json(base + "/health")
                    break
                except Exception:
                    continue
            else:
                _, tail = _run(c, f"tail -n 15 ~/{REMOTE_LOG}")
                raise PiError(f"the agent started but {base}/health never answered. Its log:\n{tail}")
        finally:
            c.close()
        _went_live(base)
        return pi.snapshot()
    except PiError as e:
        pi.mode = "error"
        pi.say(str(e))
        raise


def attach(base_url: str) -> dict:
    """The agent is already running (started by hand) - just start polling it."""
    base_url = base_url.rstrip("/")
    if not base_url.startswith("http"):
        base_url = "http://" + base_url
    u = urlparse(base_url)  # keep only scheme://host:port, so ".../frame" works too
    base_url = f"{u.scheme}://{u.hostname}:{u.port or 5000}"
    pi.log.clear()
    pi.mode, pi.host = "connecting", urlparse(base_url).hostname
    try:
        pi.health = _get_json(base_url + "/health", timeout=3)
    except Exception as e:
        pi.mode = "error"
        pi.say(f"nothing answered at {base_url}/health: {e}")
        raise PiError(f"nothing answered at {base_url}/health - is the agent running?") from e
    _went_live(base_url)
    return pi.snapshot()


def _went_live(base: str) -> None:
    pi.base_url, pi.mode = base, "live"
    h = pi.health or {}
    imu, cam = h.get("imu", {}), h.get("camera", {})
    pi.say("IMU: " + ("ok" if imu.get("ok") or imu.get("calibrating") else f"not working - {imu.get('error')}"))
    pi.say(f"camera: {cam.get('mode') or 'opens on the first photo'}")
    plane = h.get("plane", {})
    pi.say("Heltec on USB: " + (f"ok ({plane.get('port')})" if plane.get("port") else
                                f"not found - {plane.get('error')}"))
    try:  # point the star camera page at the same Pi
        from . import star_camera
        star_camera.set_camera(url=base + "/frame", enabled=True)
        pi.say(f"star camera pointed at {base}/frame")
    except Exception as e:  # Pillow/tetra3 missing - the IMU still works
        pi.say(f"(star camera page not updated: {e})")


def start_sim() -> dict:
    pi.log.clear()
    pi.mode, pi.base_url, pi.imu_error, pi._sim_t0 = "sim", None, None, time.time()
    pi.say("simulated IMU running on the server (no Pi)")
    return pi.snapshot()


def stop(on_pi: bool = True) -> dict:
    """Stop polling; if we started the agent over SSH, stop it on the Pi too."""
    was = pi.mode
    pi.mode, pi.imu, pi.base_url = "off", None, None
    if on_pi and was in ("live", "error") and pi.host and pi.user and pi._password is not None:
        try:
            c = _ssh(pi.host, pi.user, pi._password)
            _run(c, "pkill -f '[t]eam6_pi_agent.py'; true")
            c.close()
            pi.say("agent stopped on the Pi")
        except PiError as e:
            pi.say(f"could not stop the agent on the Pi: {e}")
    return pi.snapshot()


def calibrate() -> dict:
    if pi.mode == "sim":
        return {"ok": True}
    if pi.mode != "live" or not pi.base_url:
        raise PiError("not connected to the Pi")
    req = urllib.request.Request(pi.base_url + "/imu/calibrate", method="POST", data=b"")
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.loads(r.read().decode())
    except urllib.error.URLError as e:
        raise PiError(f"the Pi did not answer: {e.reason}") from e


# ---- the poller (one asyncio task for the whole server) -------------------------------------------
def _sim_sample() -> dict:
    t = time.time() - pi._sim_t0
    roll, pitch = 35 * math.sin(0.5 * t), 12 * math.sin(0.3 * t)
    yaw = (25 * t + 180) % 360 - 180
    r, p, y = (math.radians(v) / 2 for v in (roll, pitch, yaw))
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    q = [cr * cp * cy + sr * sp * sy, sr * cp * cy - cr * sp * sy, cr * sp * cy + sr * cp * sy, cr * cp * sy - sr * sp * cy]
    w, x, yy, z = q
    g = 9.80665
    accel = [2 * (x * z - w * yy) * g, 2 * (w * x + yy * z) * g, (w * w - x * x - yy * yy + z * z) * g]
    return {"t": time.time(), "rate_hz": POLL_HZ, "q": [round(v, 6) for v in q],
            "roll": round(roll, 2), "pitch": round(-pitch, 2), "yaw": round(-yaw, 2),  # aviation signs
            "accel": [round(v, 3) for v in accel], "gyro": [0.0, 0.0, round(math.radians(25), 4)],
            "temp_c": None, "calibrating": False, "error": None, "sim": True}


class _Http:
    """A kept-alive connection to the agent, so 20 polls a second don't each open a socket."""

    def __init__(self):
        self.conn = None
        self.base = None
        self.lock = threading.Lock()

    def get_imu(self, base: str) -> tuple[int, dict]:
        with self.lock:
            if self.conn is None or self.base != base:
                u = urlparse(base)
                self.conn, self.base = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=1.5), base
            try:
                self.conn.request("GET", "/imu")
                r = self.conn.getresponse()
                return r.status, json.loads(r.read().decode() or "{}")
            except Exception:
                self.conn.close()
                self.conn = None
                raise


_http = _Http()


async def poller() -> None:
    while True:
        await asyncio.sleep(1.0 / POLL_HZ)
        if pi.mode == "sim":
            pi.imu, pi.last_ok = _sim_sample(), time.time()
            continue
        if pi.mode != "live" or not pi.base_url:
            continue
        try:
            status, body = await asyncio.to_thread(_http.get_imu, pi.base_url)
            if status == 200:
                pi.imu, pi.imu_error, pi.last_ok = body, body.get("error"), time.time()
            else:
                pi.imu_error = body.get("error") or f"HTTP {status}"
        except Exception as e:
            pi.imu_error = f"cannot reach the Pi: {e}"
            await asyncio.sleep(0.5)  # back off while it's down
