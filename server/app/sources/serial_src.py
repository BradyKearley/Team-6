"""Read plane JSON lines from USB serial (bench testing)."""
from __future__ import annotations

import asyncio
import logging

from .base import LineAssembler, ReportHandler

log = logging.getLogger("serial")


def guess_port() -> str | None:
    """Pick the COM port that looks most like an ESP32 board."""
    from serial.tools import list_ports

    ports = list(list_ports.comports())
    # Heltec V3 enumerates via CP2102 (Silicon Labs) or native USB (Espressif).
    for p in ports:
        desc = f"{p.description} {p.manufacturer or ''}".lower()
        if any(k in desc for k in ("cp210", "silicon labs", "espressif", "usb serial", "ch340")):
            return p.device
    return ports[0].device if ports else None


class SerialSource:
    kind = "serial"

    def __init__(self, port: str | None, baud: int = 115200):
        self.status = "starting"
        self.port = port  # None = auto-detect
        self.baud = baud

    async def run(self, on_report: ReportHandler) -> None:
        import serial

        while True:
            # Listing ports can take ~100 ms on Windows, so do it off the event loop.
            port = self.port or await asyncio.to_thread(guess_port)
            if not port:
                self.status = "no serial port found"
                await asyncio.sleep(2)
                continue
            try:
                self.status = f"opening {port}"
                ser = serial.Serial(port, self.baud, timeout=0.2)
            except serial.SerialException as e:
                self.status = f"error: {e}"
                await asyncio.sleep(2)
                continue
            self.status = f"connected {port}"
            asm = LineAssembler()
            try:
                while True:
                    chunk = await asyncio.to_thread(ser.read, 1024)
                    for report in asm.feed(chunk.decode("utf-8", errors="ignore")):
                        on_report(report)
            except serial.SerialException as e:
                self.status = f"disconnected: {e}"
                log.warning("serial lost: %s", e)
            finally:
                ser.close()
            await asyncio.sleep(1)
