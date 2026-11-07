# plane

Firmware for the "plane": Heltec WiFi LoRa 32 V3.2 (ESP32-S3). It sniffs the towers'
ESP-NOW beacons in WiFi promiscuous mode, filters the RSSI per tower, converts it to a
distance, and every 200 ms sends one JSON line over BLE and USB serial (115200):

```
{"t":12345,"towers":[{"n":"A","r":-61.2,"d":4.84,"s":0.9,"c":18}],"sensors":{"uptime_s":12,"vbat":3.98}}
```

`t` ms since boot · `n` tower name · `r` filtered RSSI (dBm) · `d` distance (m) ·
`s` RSSI std-dev over the window (dB) · `c` samples in the window (max 20).
A tower disappears from the list after 2 s without a beacon.

The OLED shows the BLE state and each tower's distance and filtered RSSI.

## Flash

```bash
pio run -t upload
```

```bash
pio device monitor
```

If upload fails, hold **PRG**, tap **RST**, release **PRG**, then upload again.

## BLE

- Device name `PLANE-T6`
- Service `6e400001-b5a3-f393-e0a9-e50e24dcca9e` (Nordic UART style)
- Notify characteristic `6e400003-b5a3-f393-e0a9-e50e24dcca9e`

Each JSON line ends in `\n` and is split into notifications of (MTU - 3) bytes. The
firmware asks for MTU 247; the receiver must concatenate notifications and split on `\n`.
Advertising restarts automatically after a disconnect.

## Calibration constants (`platformio.ini` build flags)

Distance model (log-distance path loss): `d = 10 ^ ((RSSI_AT_1M - rssi) / (10 * PATH_LOSS_N))`

| Flag | Default | Meaning |
|---|---|---|
| `CHANNEL` | 1 | WiFi channel, **must match the towers**. |
| `RSSI_AT_1M` | -45.0 | Filtered RSSI (dBm) with the plane 1 m from a tower. |
| `PATH_LOSS_N` | 2.5 | Path-loss exponent: ~2 outdoors line-of-sight, 2.5-4 indoors. |
| `REPORT_PERIOD_MS` | 200 | Report interval. |

Filter tuning is in `src/ranging.h` (`RANGING_WINDOW`, `RANGING_EMA_ALPHA`, outlier limits).

How to calibrate, at the demo venue:
1. Put the plane exactly 1 m from tower A, both at their demo height and orientation. Wait
   ~5 s, read `r` from the serial monitor or OLED and average a few readings. That is
   `RSSI_AT_1M`.
2. Move to a known far distance `D` (e.g. 5 m) and read `r` again as `rssiD`. Then
   `PATH_LOSS_N = (RSSI_AT_1M - rssiD) / (10 * log10(D))`.
3. Repeat for another tower or two and average; put both values in `platformio.ini`
   and reflash.

Expect a few dB of noise even when still (watch `s`); RSSI ranging is good to roughly
±30-50% of distance indoors, so the server should weight towers by distance/`s`.

## Adding sensors

See the comment at the top of `src/sensors/Sensor.h`: copy `src/sensors/ExampleSensor.h`
(uptime + battery voltage), rename the class, then in `src/main.cpp` `#include` it and add it
to the `SENSORS` list. Its fields are merged into `"sensors"` in every report. Battery note: on V3.2 the VBAT divider enable (GPIO37) is active HIGH; build
with `-DVBAT_CTRL_ACTIVE=LOW` on a V3.0/V3.1 board.

## Files

| File | Role |
|---|---|
| `src/tower_rx.*` | Promiscuous-mode sniffer, ESP-NOW frame parsing, RSSI, FreeRTOS queue to the loop |
| `src/ranging.*` | Per-tower window, median/MAD outlier rejection, EMA, distance, timeout |
| `src/link_ble.*` | NimBLE server, MTU-sized chunking |
| `src/display.*` | OLED (Vext power, reset, rendering) |
| `src/sensors/*` | Sensor interface and the example sensor (the active list is in `main.cpp`) |
| `../common/tower_packet.h` | Beacon format shared with the towers |
