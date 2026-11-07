"""Shared helpers for the data sources (simulator, serial, BLE).

Every source class has:
  kind    "sim", "serial" or "ble"
  status  short text shown in the UI ("connected COM4", ...)
  async run(on_report)  loops forever, calling on_report(report) for each plane report

Report format (sent by plane firmware):
  {"t": ms, "towers": [{"n": "A", "r": -61.2, "d": 4.8, "s": 0.9, "c": 18}], "sensors": {...}}
  n = tower name, r = filtered RSSI (dBm), d = plane's distance estimate (m),
  s = RSSI std-dev (dB), c = samples in window.
"""
from __future__ import annotations

import json
import logging
from typing import Callable

log = logging.getLogger("source")

ReportHandler = Callable[[dict], None]  # called with each parsed report


class LineAssembler:
    """Joins chunks (BLE notifications / serial reads) into complete JSON lines."""

    def __init__(self) -> None:
        self.buf = ""

    def feed(self, chunk: str) -> list[dict]:
        self.buf += chunk
        out = []
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            report = parse_line(line)
            if report is not None:
                out.append(report)
        if len(self.buf) > 8192:  # garbage with no newline; drop it
            self.buf = ""
        return out


def parse_line(line: str) -> dict | None:
    line = line.strip()
    if not line.startswith("{"):
        return None  # firmware debug prints etc.
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        log.debug("bad line: %s", line[:120])
        return None
    if not isinstance(data, dict) or "towers" not in data:
        return None
    return data
