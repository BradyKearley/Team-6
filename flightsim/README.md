# flightsim — can an RL agent fly on our sensors alone?

A PPO agent in PyTorch flies a point-mass aeroplane around a route of waypoints with **no GPS
and no knowledge of its true state**. Everything it sees comes from the same sensors as the rest
of the project — the IMU, the ground towers and Iridium Doppler — fused by the same Kalman filter
(imported from `../server/app`, not copied, so the physics stays in one place).

Watch a flight in the web UI under **RL Autopilot**.

```
 dynamics.py   3-DOF aeroplane: coordinated turns, drag polar, control lag
 sensors.py    IMU / AHRS / pitot / tower ranging / Iridium Doppler + the navigation filter
 env.py        the task: fly the route, hold altitude, don't stall or hit the sea
 ppo.py        PPO (clipped surrogate, GAE, running observation normalisation)
 train.py      CLI trainer -> runs/<name>.pt
 fly.py        flies a checkpoint and writes runs/<name>.flight.json for the UI to replay
 live.py       flies the policy on demand for the web UI, in the situations listed in SCENARIOS
```

`train.py` also writes `runs/<name>.policy.json`, the network's weights as plain JSON, which is
what the server flies with numpy (no torch needed). For a checkpoint trained before that existed,
run `python fly.py --name <name> --export`.

## Quick start

```bash
cd flightsim
python3.13 -m venv .venv                   # torch has no 3.14 wheels yet
.venv/bin/pip install -r requirements.txt  # ~250 MB: CPU torch
.venv/bin/python train.py                  # ~5 min on a laptop CPU
.venv/bin/python fly.py                    # flies it, saves a replay
.venv/bin/python -m pytest -q
```

Then open the **RL Autopilot** page (the server serves `runs/*.flight.json` at
`GET /api/flight/replay`).

## Every flight is a different flight

Nothing about the setup is fixed, so a policy has to learn to fly rather than memorise one map:

| Redrawn each episode | Range |
|---|---|
| Route | 3 waypoints, 8–14 km legs, turns up to ±70° |
| Where in the world | latitude 25–65°, any longitude — which changes the satellite geometry |
| Ground towers | 1–5 of them, scattered 12–90 km out at random bearings |
| Satellite geometry | real Iridium orbits sampled at a random time in a 1-hour window |
| Wind | up to 25 m/s from any direction, never observed |
| Start | position, heading and speed all jittered |

Pin any of it for an experiment: `--towers 4` forces a count (`0` = satellites only), and
`EnvConfig(randomize=False)` goes back to one fixed ring of towers at 45°N.

## The task

A flight starts at cruise with three waypoints 8–14 km apart, a wind of up to 25 m/s it is never
told about, and 300 seconds. Twice a second the agent commands **bank, flight path angle and
throttle**; it is scored on closing the distance to the next waypoint, holding 9,000 m, and not
crashing. The aeroplane cannot turn tighter than about 4 km at cruise, so the route has to be
flown, not cut.

The **reward** uses the true state — it is the training signal, not something the aeroplane knows.
The **observation** never does.

## What the agent is allowed to see

Every recorded flight carries the agent's actual observation vector and the actions it produced,
frame by frame, so the **RL Autopilot** page can show you exactly what went in and what came out.

| `--obs-mode` | It sees | Why |
|---|---|---|
| `fused` *(default)* | the navigation filter's position, velocity and uncertainty, plus IMU, AHRS and pitot | how a real aircraft is built: estimator first, controller second |
| `raw` | dead reckoning plus the raw tower ranges and Doppler, no filter | what "only sensors" means taken literally — much harder |
| `truth` | perfect state | the ceiling, for comparison |

Other knobs worth trying: `--towers 0` (satellites only), `--orbit-error 1000` (realistic TLE
error), `--wind 0`.

## Results

Trained on filtered sensors for ~460k steps (about 4 minutes of laptop CPU):

| | |
|---|---|
| Routes completed | 100% of 8 flights |
| Waypoints | 3.00 / 3 |
| Crashes | 0% |
| Altitude hold | ±69 m mean |
| Navigation error it flew on | 19 m mean |

## Watching it learn

`train.py` keeps ~50 snapshots of the policy's weights as it trains and writes them to
`runs/<name>.net.json`. The **Watch the network learn** button on the RL Autopilot page plays
them back: 19 labelled inputs, the 14 hidden units that drive the controls hardest, and the three
commands, with every connection drawn blue where it excites and orange where it inhibits.

The architecture never changes — what grows is the weights. The output layer is initialised at
almost exactly zero on purpose (orthogonal init, gain 0.01), so the control connections really do
fade in from nothing as the policy works out what to do:

| | input → hidden 1 | hidden 1 → hidden 2 | hidden 2 → controls |
|---|---|---|---|
| 16k steps | 0.148 | 0.170 | **0.008** |
| 460k steps | 0.158 | 0.188 | 0.054 |
| 900k steps | 0.163 | 0.197 | **0.076** |

(mean absolute weight). Nearly all the visible change is in that last column.

## Is it really using the sensors?

A fair question about `WP ahead`, `WP right`, `WP up`: isn't that just handing the agent the
answer? No — the waypoint is known exactly, but the aeroplane's own position is not, so

    WP ahead  =  (known waypoint)  -  (what the filter thinks our position is)

and the error in that vector *is* the navigation error. Take the same trained policy and fly it
with the navigation made progressively worse, changing nothing else:

| Navigation | Fix error | Routes completed | Waypoints | Crashes |
|---|---|---|---|---|
| 1–5 towers + satellites (as trained) | 32 m | 100% | 3.00 / 3 | 0% |
| 1 tower only | 130 m | 92% | 2.92 | 0% |
| No towers, satellites only | 148 m | 75% | 2.58 | 0% |
| No towers + 1.5 km orbit error | 1524 m | 50% | 2.00 | 25% |

Flying degrades in lockstep with the fix, which it could not do if the waypoint vector were free
information. In the worst row the failure is worth seeing: the filter reports the aeroplane
1.5 km *lower* than it is, so the agent climbs to correct an altitude error that isn't there,
bleeds airspeed doing it, and stalls. Its altimeter lied and it flew the lie.

`python fly.py --towers 0 --orbit-error 1500 --episodes 12 --no-save` reproduces that row.

## How realistic is it?

Honest answer: the *flying* is a cartoon and the *sensing* is the same model the rest of the
project uses, so read it as a controls demonstration, not a flight-test report.

- **Aircraft**: 3-DOF point mass. Bank, pitch and throttle are commanded directly, with a 1.5–2 s
  lag. No sideslip, no rotational dynamics, no control surfaces, no compressibility, no stall
  behaviour beyond "below 55 m/s the flight ends".
- **IMU**: a constant bias plus white noise, in ECEF — the same simplification as
  `app/leo_sat.py`. A real accelerometer measures specific force in the body frame and needs
  attitude and gravity to resolve it; the attitude error that dominates real dead reckoning is
  not modelled. The AHRS is a separate sensor here with its own noise and bias.
- **Radios**: exact geometry, Gaussian noise, every satellite above the mask assumed audible,
  satellite positions exact by default (`--orbit-error` is where reality lives). Tower ranging
  assumes two-way (synchronised) ranging, so there is no clock-bias state on it.
- **Wind** is constant per flight. No turbulence, no gusts, no shear.
- The filter is given exactly the noise parameters the simulator used, i.e. it is perfectly tuned.

What the experiment *does* support: with a navigation solution of this quality, a small policy can
fly a route to within a waypoint gate while holding altitude, and the comparison between `fused`,
`raw` and `truth` measures how much the navigation filter is worth to the controller.
