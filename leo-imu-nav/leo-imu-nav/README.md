# LEO Doppler + IMU navigation (simulation)

Can Iridium NEXT satellite Doppler shifts, fused with a low-cost IMU (MPU6050-class), bound navigation drift
during a GPS outage? This is a simulation only: no SDR and no real RF. The Doppler is computed from satellite
orbits (TLE file) and the aircraft's true motion, then corrupted with noise, a receiver clock offset and
satellite orbit error.

## Files
| File | What it does |
|---|---|
| `nav_sim.py` | Simulation: truth flight, IMU model, strapdown + 16-state error-state EKF with Doppler and barometer updates. Reads the TLE. Writes `results/nav_sim_track.csv` and `results/nav_sim_results.png`. |
| `leo_analysis.py` | Quantitative analysis: satellite coverage by latitude, Doppler size and sensitivity, orbit-error impact, Doppler-only precision, optional Monte Carlo sweep. Writes CSVs and `results/leo_analysis_results.png`. Needs `nav_sim.py` in the same folder. |
| `imu_tracker.py` | Raspberry Pi script for a real MPU6050 (calibration, CSV logging). Separate from the simulation. |
| `data/iridium_next.tle` | Iridium NEXT orbital elements from Celestrak. Refresh before each run. |

## Setup
```
python3 -m venv --system-site-packages venv && source venv/bin/activate
pip install -r requirements.txt
```

## Get the TLE
```
wget -O data/iridium_next.tle "https://celestrak.org/NORAD/elements/gp.php?GROUP=iridium-NEXT&FORMAT=tle"
```
The scripts also find `gp.php`/`gp.txt` style names in `.`, `data/` or next to the script, or take `--tle PATH`.
Without a TLE file they fall back to a synthetic Iridium-like constellation (`--offline` skips auto-download).

## Run
```
python3 nav_sim.py                       # 20 min GPS outage, MPU6050, 1 km orbit error
python3 nav_sim.py --imu tactical --orbit-err 500 --minutes 30
python3 leo_analysis.py                  # tables + plot
python3 leo_analysis.py --sweep          # adds the Monte Carlo sweep (slow on a Pi)
```
`--help` lists all options.

## Results from our run (real TLE, 80 satellites)
- 20 min GPS outage, MPU6050: IMU alone drifts about 1,400 km. With Doppler + barometer: about 50 m with perfect
  orbits, 2 to 3 km with 1 km satellite orbit error.
- Satellite orbit (TLE) error is the accuracy floor, not the IMU grade.
- Coverage is thinnest near the equator (2+ satellites 20% of the time) and best at high latitude.

## Limitations
Simulated Doppler only (needs an SDR measuring burst carrier frequency to validate); ionosphere and signal travel
time not modelled; IMU error values are datasheet-style assumptions; orbit error is an input assumption; the filter
is overconfident when orbit error is present; sweep rows use few runs.
