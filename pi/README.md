# Raspberry Pi: IMU + star camera, live in the web UI

`team6_pi_agent.py` runs on the Pi and serves the MPU6050 and the camera over plain HTTP on Wi-Fi
(no Bluetooth). **You don't copy it by hand**: the server SSHes in, copies it, and starts it.

```
MPU6050 --I2C--> Pi agent :5000 --Wi-Fi HTTP--> server :8000 --WebSocket--> browser :5173
camera  -------/                                (app/pi.py)                 IMU page / Star Camera page
```

## Once: wire the sensor (Pi powered OFF)

| MPU6050 (GY-521) | Pi header pin |
|---|---|
| VCC | pin 1 (3.3 V) |
| GND | pin 6 |
| SDA | pin 3 (GPIO2) |
| SCL | pin 5 (GPIO3) |

Mount it with the printed **X arrow toward the plane's nose**, chip side up. Plug the camera ribbon into the CSI port.

## Every time

1. Power the Pi on and wait ~1 minute. It must be on the same Wi-Fi/hotspot as the laptop.
2. On the laptop, check SSH works: `ssh pi@172.20.10.5` (use your Pi's address), type `exit`.
3. Start the server: `cd server` then `.venv\Scripts\python -m app.main`
4. Start the UI: `cd frontend` then `npm run dev`, and open http://localhost:5173
5. Open **IMU (live from Pi)**. Under *Raspberry Pi*, enter the address, username and password and press
   **Connect to the Pi**. Keep the sensor still for 2 seconds while it calibrates.
6. The plane moves with the sensor. Hold your model level, nose forward, and press **Zero**.
7. **Star Camera** page: the camera address is already filled in. Press **Take a test photo** and the photo
   appears in the preview (bottom right). **Capture and solve** solves the real sky.

The "What happened" list under *Raspberry Pi* shows each step (SSH, I2C, sensor found, agent started).

## If something fails

| Message | Fix |
|---|---|
| `cannot SSH ... timed out` | Wrong address or not on the same network. Find it with `ping raspberrypi.local` or on the hotspot's device list. |
| `SSH login ... refused` | Wrong username/password. |
| `could not enable I2C automatically` | On the Pi: `sudo raspi-config` -> Interface Options -> I2C -> Yes, then reboot. |
| `no MPU6050 seen at 0x68/0x69` | Wiring. On the Pi run `i2cdetect -y 1`; you should see `68` or `69`. |
| `I2C library: none` | On the Pi: `sudo apt install python3-smbus` |
| camera errors on the test photo | On the Pi: `rpicam-still -o test.jpg` should work; check the ribbon cable. |
| plane tilts the wrong way | Sensor isn't mounted X-forward. Level the model and press **Zero**. |

No sensor yet? Tick **No sensor yet: fake the IMU on the Pi** to test the whole Wi-Fi link, or press
**Try with no Pi (simulated)** to see the page with no hardware at all.

Run the agent by hand instead (on the Pi): `python3 team6_pi_agent.py`, then on the IMU page use
**Agent already running?** with `http://<pi>:5000`. Agent log on the Pi: `~/team6_pi_agent.log`.
