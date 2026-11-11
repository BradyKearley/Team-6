"""Plane reports relayed by the Raspberry Pi (the Heltec is plugged into the Pi's USB).

The Pi agent (pi/team6_pi_agent.py) reads the Heltec's JSON lines and serves the newest one at
GET http://<pi>:5000/plane. This source polls that and hands every NEW report to the same
trilateration as the USB/BLE sources. Connect the Pi first (app/pi.py); this follows its address.
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request

from .. import pi as pi_mod
from .base import ReportHandler

log = logging.getLogger("pi-source")
POLL_S = 0.1


def _fetch(base: str) -> dict:
    try:
        with urllib.request.urlopen(base + "/plane", timeout=1.5) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:  # 503 = agent is up but has no report yet; body says why
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"error": f"HTTP {e.code}"}


class PiSource:
    kind = "pi"

    def __init__(self) -> None:
        self.status = "connect the Raspberry Pi first"

    async def run(self, on_report: ReportHandler) -> None:
        last_seq = None
        while True:
            await asyncio.sleep(POLL_S)
            base = pi_mod.pi.base_url
            if pi_mod.pi.mode != "live" or not base:
                self.status = "connect the Raspberry Pi first (Raspberry Pi section below)"
                last_seq = None
                continue
            try:
                body = await asyncio.to_thread(_fetch, base)
            except Exception as e:
                self.status = f"cannot reach the Pi: {e}"
                await asyncio.sleep(0.5)
                continue
            report = body.get("report")
            if report is None:
                self.status = f"Pi is up but no plane data: {body.get('error') or 'waiting for the Heltec'}"
                continue
            if body.get("seq") != last_seq:
                last_seq = body.get("seq")
                on_report(report)
            age = body.get("age_s")
            self.status = f"receiving via Pi ({body.get('port')})" + (
                f", last report {age}s ago" if age is not None and age > 1 else "")
