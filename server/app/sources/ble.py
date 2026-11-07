"""Receive plane reports over Bluetooth LE (no WiFi needed).

The plane advertises as PLANE-T6 with a Nordic-UART-style service and notifies
JSON lines on the TX characteristic, split into MTU-sized chunks.
"""
from __future__ import annotations

import asyncio
import logging

from .base import LineAssembler, ReportHandler

log = logging.getLogger("ble")

NOTIFY_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # must match plane/src/link_ble.h


class BleSource:
    kind = "ble"

    def __init__(self, name: str = "PLANE-T6"):
        self.status = "starting"
        self.name = name

    async def run(self, on_report: ReportHandler) -> None:
        from bleak import BleakClient, BleakScanner

        while True:
            self.status = f"scanning for {self.name}"
            try:
                device = await BleakScanner.find_device_by_name(self.name, timeout=8.0)
            except Exception as e:  # adapter off, etc.
                self.status = f"scan error: {e}"
                await asyncio.sleep(3)
                continue
            if device is None:
                continue

            asm = LineAssembler()
            disconnected = asyncio.Event()

            def on_notify(_char, data: bytearray) -> None:
                for report in asm.feed(data.decode("utf-8", errors="ignore")):
                    on_report(report)

            try:
                self.status = f"connecting {device.address}"
                async with BleakClient(device, disconnected_callback=lambda _c: disconnected.set()) as client:
                    await client.start_notify(NOTIFY_UUID, on_notify)
                    self.status = f"connected {device.address}"
                    await disconnected.wait()
            except Exception as e:
                log.warning("BLE error: %s", e)
                self.status = f"error: {e}"
            await asyncio.sleep(1)
