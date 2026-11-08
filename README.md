
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
| `flightsim/` | PyTorch PPO agent flying a simulated aeroplane on our sensors alone | [flightsim/README.md](flightsim/README.md) |

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

Open http://localhost:5173. A fake plane flies over a fixed 4-tower practice layout, and the green dot is its true position. The simulator's towers reset every time you switch to **Sim**, and nothing you change there touches your real towers in `server/towers.json`.

## With hardware

1. Flash towers: `cd cell-tower` then `pio run -e tower_a -t upload` (repeat for `tower_b`, `tower_c`, `tower_d`, one board each).
   ESP32-C3 boards use `tower_c_c3` / `tower_d_c3` instead (see `cell-tower/README.md`).
2. Flash the plane: `cd plane` then `pio run -t upload`. The OLED shows the towers it hears.
3. Start the server (`.venv\Scripts\python -m app.main`) and pick **USB** or **BLE** under *Data source* in the sidebar
   (or start with `--source serial` / `--source ble`, plus `--port COM5` if USB auto-detect picks the wrong port).
4. Calibrate with **Calibration sweep** in the toolbar: put all towers together, then record with the plane at 1, 2, 3 and 5 m and press **Apply**.
   Points under 1 m are ignored (the radio saturates that close). Calibration is saved in `server/towers.json`, so there's no reflashing.
   To update the plane's own OLED distances too, copy the values into `plane/src/calibration.h` and reflash.
5. Place the towers with **Setup towers**: it measures each tower's distance from the plane at (0, 0), then you drag each tower to its real direction.
   You can also type exact positions in the tower panel (feet). Names must match the flashed `TOWER_NAME`.

Accuracy tips: RSSI ranging indoors is typically 1–5 m off. Spread the towers wide around the flight area, keep them in line of sight, put them at different heights (needed to solve altitude), and use 4 or more.

## LEO satellite page (no hardware needed)

**LEO Satellite** in the sidebar answers "GPS just died - how long until we're lost?". It flies a simulated plane,
measures the Doppler shift of every Iridium NEXT satellite above the horizon, and fuses those with a drifting IMU
in an extended Kalman filter. Dead reckoning alone is off by ~12 km after 20 minutes; with Doppler it stays near 100 m.

The page is laid out like the tower page: a **3D globe / 2D map** of the run fills the pane with a play button and a
scrubber, and the sidebar carries the numbers and every knob. Green satellites are above the elevation mask, with a link
drawn to the plane - those are the Doppler measurements the filter is using at that moment. The frame is Earth-fixed
(ECEF), so the ground stays put and the constellation does the moving; the 2D map's *Near plane* view frames the flight
itself, where the true track (grey) and the estimate (blue) separate.

Everything lives in [`server/app/leo_sat.py`](server/app/leo_sat.py). The page posts the sidebar's settings to
`POST /api/leo/simulate` and renders what comes back, so changing the physics or the filter changes the page.

- Orbit elements are downloaded from celestrak.org the first time and cached in `server/app/iridium_next.tle`
  (re-downloaded when it's over a week old, or with **Re-download orbit elements**). That one download needs internet;
  after that the page works offline.
- The same file runs standalone with matplotlib plots and a CSV:
  `cd server` then `.venv\Scripts\python -m app.leo_sat --minutes 20` (`--help` lists the knobs). The web page has no
  charts - if you want the error-vs-time plots for a report, that's where they are.
- Try the **Stale TLEs** preset: 1 km of satellite position error makes the filter *worse* than the IMU alone - which is
  why real receivers care about ephemeris accuracy.
- The globe, the coastlines (`frontend/src/scene/coastlines.js`, Natural Earth 1:110m, public domain) and the orbit paths
  are all drawn from code and shipped data - nothing is fetched at runtime.

## Sensor fusion page: does combining techniques help?

**Sensor Fusion** runs the experiment. [`server/app/fusion.py`](server/app/fusion.py) flies ONE truth trajectory and then
runs the same Kalman filter over it four times, with identical IMU errors and identical noise draws - the only thing that
differs is what each filter was allowed to listen to:

| Combination | Final error after 20 min |
|---|---|
| IMU only (dead reckoning) | 12.1 km |
| IMU + Iridium Doppler | 49 m |
| IMU + ground beacons | 18 m |
| **IMU + Doppler + beacons** | **9 m** |

So yes: together beats either one alone, and the sidebar leads with that answer for whatever settings you pick. Ground
towers are the cell-tower technique scaled up to aviation - known stations, a distance to each - so the *One lonely
tower*, *No towers at all* and *Everything is noisy* presets show what happens as that network gets worse.

The 3D view opens at **Fly along** - the camera down on the route, where the ground towers are actually legible: each is a
labelled pin with a faint ring showing how far it reaches, and the plane has to be inside a ring to use that tower.
**Globe** pulls back out to the constellation. The 2D map draws the same rings.

Because 9 m and 12 km land on the same pixel at world scale, the page has two more devices the globe can't provide: the four
**bars share one log scale** (each step is 10x), and the inset in the corner zooms all the way in - the truth sits at the
crosshair and each filter's estimate is plotted as its offset from it, so during playback you watch the unaided IMU
wander off while the fused one stays on the cross. Tick a row to draw that filter's path on the map too.

The star camera isn't in the comparison: it measures attitude, and this filter doesn't carry an attitude state yet.

  `cd server` then `.venv\Scripts\python -m app.fusion --minutes 20` prints the same table in the terminal.

## RL autopilot (no hardware needed)

`flightsim/` asks the next question: if the navigation solution is all you have, can something
*fly* on it? A PPO agent in PyTorch commands bank, pitch and throttle twice a second, sees only
what the sensors and the Kalman filter give it, and flies a route of waypoints through a wind it
is never told about. After about four minutes of laptop training it completes 100% of routes,
holds altitude to ±69 m, and never crashes - flying on a 19 m navigation fix.

Every flight is a different one: the route, the map location (which changes the satellite
geometry), the number and placement of ground towers, and the wind are all redrawn per episode,
so the policy has to fly rather than memorise.

**RL Autopilot** in the sidebar replays a recorded flight in 3D: chase or free camera, the
waypoint gates, the ground towers, the true track against what the filter thought at the time,
and a HUD. It also shows what the agent is working from — the satellites it is hearing (drawn in
the right direction, with a sky plot of their azimuth and elevation), the towers it is ranging
off, and live bars for every number going into the network and every command coming out. Record a
flight with `python fly.py` in `flightsim/`; the server serves it from `runs/*.flight.json`.

**Watch the network learn** on that page animates the policy's weights across training — training
keeps ~50 snapshots of them — ending on the finished network. The shape never changes; the
connections to the three controls start at almost zero and grow in.

## Adding things later

- **New plane sensor** (MPU6050, etc.): subclass `Sensor` in `plane/src/sensors/`, register it, and its fields appear in the report's `sensors` object and in the UI's "Plane sensors" panel automatically.
- **New tracking method page**: add a component in `frontend/src/pages/` and swap it in for `Placeholder` in `frontend/src/App.svelte`.
- Server API: `GET/POST/PUT/DELETE /api/towers`, `POST /api/towers/{name}/calibrate`, `POST /api/track/reset`, WebSocket `/ws` (live state at 10 Hz),
  `GET /api/leo/info` + `POST /api/leo/simulate` for the LEO page, `POST /api/fusion/simulate` for the fusion page,
  `GET /api/flight/list` + `GET /api/flight/replay` for the RL autopilot replays. Docs at http://localhost:8000/docs.
- **Space view**: `frontend/src/lib/SpaceView.svelte` is the shared pane (3D/2D toggle + playback) used by both pages;
  `frontend/src/scene/SpaceGlobe.svelte` (Threlte) and `SpaceMap2D.svelte` (SVG) render the `scene` block a simulation
  returns - satellite positions, visibility, ground beacons and any number of estimated tracks, in ECEF kilometres.
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
