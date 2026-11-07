"""Team-6 ground server.

Run from the server/ folder:
  python -m app.main --source sim             # fake plane, no hardware
  python -m app.main --source ble             # plane over Bluetooth LE
  python -m app.main --source serial --port COM5

The web UI can also switch source live (POST /api/source).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import statistics
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .sources.ble import BleSource
from .sources.serial_src import SerialSource, guess_port
from .sources.simulator import SimulatorSource
from .state import SystemState
from .sweep import Sweep
from .towers import Tower, TowerStore

log = logging.getLogger("server")
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

# ---- shared state (one server = one of each) --------------------------------------
real_towers = TowerStore()  # your real layout, saved in towers.json
towers = real_towers  # the layout in use right now (a fresh simulator layout while in sim mode)
state = SystemState(towers)
real_sweep = Sweep()  # calibration sweep points for the real towers (saved to sweep.json)
sweep = real_sweep  # in sim mode: a throwaway in-memory sweep
clients: set[WebSocket] = set()  # open browser websockets
source = None  # the running SimulatorSource / SerialSource / BleSource
source_port: str | None = None
source_task: asyncio.Task | None = None
startup = {"kind": "sim", "port": None, "ble_name": "PLANE-T6"}  # set by main() from the command line


# ---- request bodies -----------------------------------------------------------------
class TowerIn(BaseModel):
    name: str = Field(min_length=1, max_length=7)  # plane firmware names are char[8]
    x: float = Tower.x
    y: float = Tower.y
    z: float = Tower.z
    rssi1m: float = Tower.rssi1m
    n: float = Field(Tower.n, gt=0.5, lt=8.0)


class SecondsIn(BaseModel):
    seconds: float = Field(3.0, gt=0.5, le=30.0)


class CalibrateIn(SecondsIn):
    known_distance: float = Field(gt=0.05)  # meters


class SweepRecordIn(BaseModel):
    known_distance: float = Field(gt=0.05, le=100)  # meters from the tower cluster
    seconds: float = Field(6.0, gt=0.5, le=30.0)


class SimIn(BaseModel):
    speed: float = Field(ge=0.0, le=10.0)  # m/s, 0 = hover in place


class SourceIn(BaseModel):
    kind: Literal["sim", "serial", "ble"]
    port: str | None = None  # serial only; None = auto-detect


# ---- data source ----------------------------------------------------------------------
async def switch_source(kind: str, port: str | None = None) -> None:
    """Stop the current source (if any) and start a new one.

    Sim mode always starts from the fixed simulator layout (reset every time);
    USB/BLE use your real towers from towers.json.
    """
    global source, source_port, source_task, towers, sweep
    if source_task is not None:
        source_task.cancel()
        try:
            await source_task  # lets the serial source close its COM port
        except (asyncio.CancelledError, Exception):
            pass
    if kind == "sim":
        towers, sweep = TowerStore.simulator(), Sweep(path=None)
    else:
        towers, sweep = real_towers, real_sweep
    state.towers = towers
    state.clear_live()
    if kind == "sim":
        source = SimulatorSource(towers)
    elif kind == "serial":
        source = SerialSource(port)
    else:
        source = BleSource(startup["ble_name"])
    source_port = port
    source_task = asyncio.create_task(run_source(source))
    log.info("source -> %s%s", kind, f" ({port})" if port else "")


async def run_source(src) -> None:
    try:
        await src.run(state.ingest)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # e.g. bleak/pyserial missing or adapter error
        log.exception("source %s crashed", src.kind)
        src.status = f"crashed: {e}"


def link_info() -> dict:
    """Which source is running and how it's doing (shown in the UI sidebar)."""
    if source is None:
        return {"source": "none", "status": "starting"}
    info = {"source": source.kind, "status": source.status}
    if source.kind == "serial":
        info["port"] = source_port
    if source.kind == "sim":
        info["sim_speed"] = source.speed
    return info


async def collect_rssi(names: list[str], seconds: float) -> dict[str, list[float]]:
    """Watch these towers for `seconds`. Returns {name: [RSSI of each NEW plane report]}."""
    samples: dict[str, list[float]] = {n: [] for n in names}
    last_seen: dict[str, float] = {}
    loop = asyncio.get_running_loop()
    end = loop.time() + seconds
    while loop.time() < end:
        for n in names:
            r = state.readings.get(n)
            if r is not None and r.rssi is not None and r.at != last_seen.get(n):  # only new reports
                samples[n].append(r.rssi)
                last_seen[n] = r.at
        await asyncio.sleep(0.05)
    return samples


async def measure_rssi(names: list[str], seconds: float) -> dict[str, tuple[float | None, int]]:
    """Like collect_rssi, but returns {name: (median RSSI or None, sample count)}.

    The median is None if fewer than 3 reports came in (tower off or out of range).
    """
    samples = await collect_rssi(names, seconds)
    return {n: (statistics.median(s) if len(s) >= 3 else None, len(s)) for n, s in samples.items()}


async def broadcaster() -> None:
    """Push the live state to every open browser 10 times a second."""
    while True:
        await asyncio.sleep(0.1)
        if not clients:
            continue
        snap = state.snapshot(link_info())
        for ws in list(clients):
            try:
                await ws.send_json(snap)
            except Exception:
                clients.discard(ws)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await switch_source(startup["kind"], startup["port"])
    task = asyncio.create_task(broadcaster())
    yield
    task.cancel()
    if source_task:
        source_task.cancel()


app = FastAPI(title="Team-6 tracker", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---- live state -------------------------------------------------------------------------
@app.get("/api/state")
def get_state():
    return state.snapshot(link_info())


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    try:
        await ws.send_json(state.snapshot(link_info()))
        while True:
            await ws.receive_text()  # keepalive; client messages ignored
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)


@app.post("/api/track/reset")
def reset_track():
    state.reset_track()
    return {"ok": True}


# ---- towers -------------------------------------------------------------------------------
@app.get("/api/towers")
def list_towers():
    return [asdict(t) for t in towers.all()]


@app.post("/api/towers")
def create_tower(t: TowerIn):
    if towers.get(t.name):
        raise HTTPException(409, f"tower {t.name} exists")
    return asdict(towers.upsert(Tower(**t.model_dump())))


@app.put("/api/towers/{name}")
def update_tower(name: str, t: TowerIn):
    if t.name != name:
        towers.delete(name)  # rename
    state.reset_track()
    return asdict(towers.upsert(Tower(**t.model_dump())))


@app.delete("/api/towers/{name}")
def delete_tower(name: str):
    if not towers.delete(name):
        raise HTTPException(404, "no such tower")
    state.reset_track()
    return {"deleted": name}


@app.post("/api/towers/{name}/calibrate")
async def calibrate(name: str, body: CalibrateIn):
    """Hold the plane `known_distance` meters from the tower, then call this."""
    if not towers.get(name):
        raise HTTPException(404, "no such tower")
    rssi, count = (await measure_rssi([name], body.seconds))[name]
    if rssi is None:
        raise HTTPException(409, f"only {count} readings from tower {name}; is it on?")
    result = state.add_calibration_point(name, body.known_distance, rssi)
    return {**result, "measured_rssi": rssi, "samples": count}


@app.delete("/api/towers/{name}/calibrate")
def clear_calibration(name: str):
    state.calibration_points.pop(name, None)
    return {"cleared": name}


# ---- calibration sweep (all towers at several known distances) --------------------------------
@app.get("/api/sweep")
def get_sweep():
    return sweep.to_json()


@app.post("/api/sweep/record")
async def sweep_record(body: SweepRecordIn):
    """Record every tower's RSSI with the plane `known_distance` meters from the tower cluster."""
    names = sorted({t.name for t in towers.all()} | set(state.readings))
    if not names:
        raise HTTPException(409, "no towers heard or placed yet")
    readings = await collect_rssi(names, body.seconds)
    sweep.add_batch(body.known_distance, readings)
    return sweep.to_json()


@app.delete("/api/sweep/last")
def sweep_undo():
    sweep.undo_last()
    return sweep.to_json()


@app.delete("/api/sweep")
def sweep_clear():
    sweep.clear()
    return sweep.to_json()


@app.post("/api/sweep/apply")
def sweep_apply():
    """Save each good fit's rssi1m and n to its tower (position is kept)."""
    applied = []
    for name, fit in sweep.fits().items():
        tower = towers.get(name)
        if tower is None or not fit["ok"]:
            continue
        tower.rssi1m = round(fit["rssi1m"], 1)
        tower.n = round(fit["n"], 2)
        towers.upsert(tower)
        applied.append({"name": name, "rssi1m": tower.rssi1m, "n": tower.n})
    state.reset_track()
    return {"applied": applied}


@app.post("/api/measure")
async def measure(body: SecondsIn):
    """Distance to every tower the plane hears, averaged over `seconds`. Moves nothing.

    Used by the Setup Towers guide. Includes towers heard but not placed yet.
    """
    names = sorted({t.name for t in towers.all()} | set(state.readings))
    results = await measure_rssi(names, body.seconds)
    out = []
    for n, (rssi, count) in results.items():
        known = towers.get(n) is not None
        if rssi is None:
            out.append({"name": n, "known": known, "heard": False})
        else:
            dist = (towers.get(n) or Tower(n)).rssi_to_distance(rssi)
            out.append({"name": n, "known": known, "heard": True, "rssi": rssi, "distance": dist, "samples": count})
    return {"towers": out}


# ---- data source + simulator ----------------------------------------------------------------
@app.post("/api/source")
async def set_source(body: SourceIn):
    """Swap between the simulator, the plane on USB serial, and the plane over BLE."""
    try:
        await switch_source(body.kind, body.port or None)
    except ImportError as e:
        raise HTTPException(500, f"missing package for {body.kind}: {e}")
    return link_info()


@app.get("/api/ports")
def list_ports():
    """Serial ports, for the USB port picker."""
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        return {"ports": [], "guess": None}
    ports = [{"device": p.device, "description": p.description} for p in lp.comports()]
    return {"ports": ports, "guess": guess_port()}


@app.post("/api/sim")
def set_sim(body: SimIn):
    """Change the simulated plane's speed (only while the simulator is the active source)."""
    if source is None or source.kind != "sim":
        raise HTTPException(409, "not running the simulator")
    source.speed = body.speed
    return {"speed": source.speed}


if FRONTEND_DIST.exists():  # serve the built UI from the same port
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="ui")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", choices=["sim", "serial", "ble"], default="sim")
    p.add_argument("--port", help="serial port, e.g. COM5 (auto-detected if omitted)")
    p.add_argument("--ble-name", default="PLANE-T6")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--http-port", type=int, default=8000)
    a = p.parse_args()
    startup.update(kind=a.source, port=a.port, ble_name=a.ble_name)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    uvicorn.run(app, host=a.host, port=a.http_port)


if __name__ == "__main__":
    main()
