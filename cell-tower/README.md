# cell-tower

Firmware for the "cell towers": classic ESP32 DevKit (WROOM-32) boards that broadcast an
ESP-NOW beacon (`common/tower_packet.h`, magic `TWR1`) to `FF:FF:FF:FF:FF:FF` every 50 ms,
on a fixed WiFi channel at max TX power (21 dBm). The on-board LED blinks on each send; a
fast continuous blink means init failed (see the serial log).

## Flash

Each physical tower gets its own env, which sets its name (`A`..`D`):

```bash
pio run -e tower_a -t upload
```

```bash
pio device monitor
```

`pio run` with no `-e` builds all four. Label the boards; the name is the only thing that
tells the plane which tower is which, and the server maps names to tower positions.
To add a tower E, copy an `[env:tower_x]` block in `platformio.ini`.

**ESP32-C3 boards:** the `tower_*` envs are for the classic ESP32 only. `tower_c_c3` / `tower_d_c3` build
towers C / D for an ESP32-C3 (native USB serial, LED on GPIO8; shared settings in `[c3]`).
A C3 antenna differs from a WROOM one, so give C3 towers their own `rssi1m` in the server.
Never run two boards with the same name (e.g. `tower_c` and `tower_c_c3`) at once.

## Settings (`platformio.ini` build flags)

| Flag | Default | Notes |
|---|---|---|
| `CHANNEL` | 1 | WiFi channel 1-13. **Must match the plane.** Pick the least busy channel at the venue. |
| `BEACON_PERIOD_MS` | 50 | 20 Hz per tower. Lower = more samples but more airtime. |
| `LED_PIN` | 2 | On-board LED on most DevKits. |
| `TX_POWER_QDBM` | 84 | Units of 0.25 dBm. Lower it to shrink range indoors. |

## Calibration notes

Distance is computed on the plane, but the towers affect it:
- Use the same board model, antenna orientation, power source and `TX_POWER_QDBM` on every
  tower, or calibrate `RSSI_AT_1M` per tower.
- Mount towers at the same height, antenna vertical, away from metal and people.
- See `plane/README.md` for the actual calibration procedure.
