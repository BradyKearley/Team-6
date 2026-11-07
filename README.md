
Challenge 2.

# System overview

```
 cell-tower (ESP32 x4)  ──2.4 GHz ESP-NOW beacons──▶  plane (Heltec V3)  ──BLE / USB──▶  server (Python)  ──WebSocket──▶  frontend (Svelte 3D)
   broadcasts its name        20 Hz, channel 1         RSSI → distance                  trilateration +            tower placement,
                                                        + extra sensors                  Kalman filter              plane render, tabs
```

| Folder | What it is | Details |
|---|---|---|
| `cell-tower/` | PlatformIO firmware for classic ESP32 boards acting as cell towers | [cell-tower/README.md](cell-tower/README.md) |
| `plane/` | PlatformIO firmware for the Heltec WiFi LoRa 32 V3 | [plane/README.md](plane/README.md) |
| `common/` | `tower_packet.h`, the beacon format both firmwares share | |
| `server/` | FastAPI server: reads the plane, trilaterates, serves the API + WebSocket | below |
| `frontend/` | Svelte 5 + Threlte 3D UI with a sidebar of tracking methods | below |

No WiFi network is needed anywhere: towers → plane is ESP-NOW, plane → laptop is Bluetooth LE.

## Quick start (no hardware: simulator)

```bash
cd server
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m app.main --source sim
```

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. A fake plane flies over the 4 towers in `server/towers.json`, and the green dot is its true position.

## With hardware

1. Flash towers: `cd cell-tower` then `pio run -e tower_a -t upload` (repeat for `tower_b`, `tower_c`, `tower_d`, one board each).
2. Flash the plane: `cd plane` then `pio run -t upload`. The OLED shows the towers it hears.
3. Run the server with the plane over Bluetooth: `.venv\Scripts\python -m app.main --source ble`
   (or plugged in over USB: `--source serial`, with `--port COM5` if auto-detect picks the wrong port).
4. In the UI, place each tower where it really is (tape-measure x/y from a corner of the room, z = antenna height). Names must match the flashed `TOWER_NAME`.
5. Calibrate: select a tower, hold the plane 1 m from it, click **Measure**. Repeat at about 5 m to also fit the path-loss exponent `n`. Calibration is saved in `server/towers.json`, so there's no reflashing.

Accuracy tips: RSSI ranging indoors is typically 1–5 m off. Spread the towers wide around the flight area, keep them in line of sight, put them at different heights (needed to solve altitude), and use 4 or more.

## Adding things later

- **New plane sensor** (MPU6050, etc.): subclass `Sensor` in `plane/src/sensors/`, register it, and its fields appear in the report's `sensors` object and in the UI's "Plane sensors" panel automatically.
- **New tracking method page**: add a component in `frontend/src/pages/` and swap it in for `Placeholder` in `frontend/src/App.svelte`.
- Server API: `GET/POST/PUT/DELETE /api/towers`, `POST /api/towers/{name}/calibrate`, `POST /api/track/reset`, WebSocket `/ws` (live state at 10 Hz). Docs at http://localhost:8000/docs.
- Tests: `cd server` then `.venv\Scripts\python -m pytest`.

# TODO:
* Set up towers
   - We need to make towers (griffin)
   - They need to send signals (Manasha, Brady)
 
* Set up "plane"
   - We need to make a physical model that can hold everything (jillian)
   - Probably learn how to use MPU6050 (accelerometers) (isaac griffin)
   - Use camera? (isaac)
   - Figure out what to recive wifi? (esp 32) (resberry 4 model e) (Manahsa)
   
* laptop
   - Recive signals (esp to esp) (Brady)
   - Predict position using laptop (laptop knows layout and can correct for error and calc position) (below) (Brady Griffin)
   - UI [photo in gc] (react) (isaac jillan manasha)
     
* Extra
   - Look into using sun/stars for positioning (jillian)
   - Simulate LEO satellite (Manasha)
   - Kalman filter (isaac)

 * Video (whoever can)
   - Make presentation for video 
   - record demonstration 
# TIMELINE STUFF

* Sunday (before midnight?) submit video
* Sign NDA before NOV 12
* have presentation NOV 12
# PERSONAL NOTES
Griffin: Triangulate position (location vector) and map out where you are based off where the towers are in any code you want. Ask claude. 3d print towers
Isaac: Figure out how to translate accelorometers to raw usable data. work on website?
Brady: work with griff and manasha to communicate esps and laptops to website. vibe out website? 
Jillian: Figure out how to use starcamera. Make plane to fit, batterypack, esp, accelorometer, camera (star or normal)
Manasha: Simulate LEO satellite. Data processing and flow
