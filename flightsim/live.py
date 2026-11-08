"""Fly the trained policy on demand, in whatever situation you ask for.

The web server uses this to run real flights for the RL Autopilot page. It runs the same
FlightEnv, sensors and navigation filter as training, and the policy's real weights, so
nothing here is canned: a new seed is a new flight the network has never seen.

Inference needs nothing but numpy, so the server does not have to install torch. The
weights come from runs/<name>.policy.json, which train.py writes (or `fly.py --export`
for an older checkpoint).
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

import numpy as np

from env import EnvConfig, FlightEnv

RUNS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs")

# Anything left out of a scenario keeps the value the policy was trained with. `trained`
# is False when the situation is outside what the policy ever saw, so the page can say so.
SCENARIOS = {
    "standard": {"title": "As trained", "trained": True,
                 "blurb": "The conditions it learned in: towers and satellites, wind up to 25 m/s, turns up to 70 degrees."},
    "calm": {"title": "Calm air", "trained": True, "wind": (0.0, 0.0),
             "blurb": "No wind at all. Should be its easiest flight."},
    "gale": {"title": "Gale", "trained": False, "wind": (45.0, 60.0),
             "blurb": "45-60 m/s of wind it is never told about, about twice the strongest it trained in."},
    "one_tower": {"title": "One tower", "trained": False, "towers": 1,
                  "blurb": "A single ground tower plus satellites, so the fix is weaker than in training."},
    "sats_only": {"title": "Satellites only", "trained": False, "towers": 0,
                  "blurb": "No ground towers. The position comes from Iridium Doppler and the IMU alone."},
    "bad_orbits": {"title": "Bad orbit data", "trained": False, "towers": 0, "orbit_error": 1500.0,
                   "blurb": "No towers and satellite positions wrong by 1.5 km, so the filter reports the wrong place."},
    "hairpins": {"title": "Hairpin route", "trained": False, "turn_max": 150.0, "legs": (6.0, 9.0),
                 "blurb": "Short legs with turns up to 150 degrees, where training stopped at 70."},
    "long_haul": {"title": "Long route", "trained": False, "waypoints": 6, "max_steps": 1200,
                  "blurb": "Six waypoints instead of three, with twice the time."},
    "polar": {"title": "Near the pole", "trained": False, "lat": (75.0, 85.0),
              "blurb": "Latitude 75-85 degrees, north of anywhere it trained, where the satellite geometry changes."},
    "worst_day": {"title": "Worst day", "trained": False, "towers": 0, "orbit_error": 500.0, "wind": (40.0, 50.0),
                  "turn_max": 120.0,
                  "blurb": "Satellites only, 500 m orbit error, 40-50 m/s of wind and turns up to 120 degrees, all at once."},
}


class Policy:
    """The trained actor network and its observation scaling, in plain numpy."""

    def __init__(self, data: dict):
        self.meta = {k: v for k, v in data.items() if k not in ("layers", "norm")}
        self.name = data["name"]
        self.obs_mode = data["obs_mode"]
        self.layers = [(np.array(l["w"], dtype=np.float32), np.array(l["b"], dtype=np.float32))
                       for l in data["layers"]]
        self.mean = np.array(data["norm"]["mean"], dtype=np.float64)
        self.var = np.array(data["norm"]["var"], dtype=np.float64)

    def __call__(self, obs: np.ndarray) -> np.ndarray:
        """The mean action: the same deterministic output fly.py uses."""
        x = np.clip((obs - self.mean) / np.sqrt(self.var + 1e-8), -10.0, 10.0).astype(np.float32)
        for w, b in self.layers[:-1]:
            x = np.tanh(w @ x + b)
        w, b = self.layers[-1]
        return w @ x + b


def policies() -> list[str]:
    """Names of the policies that can be flown, the original `fused` first if it is there."""
    if not os.path.isdir(RUNS):
        return []
    tail = ".policy.json"
    names = sorted(n[: -len(tail)] for n in os.listdir(RUNS) if n.endswith(tail))
    return sorted(names, key=lambda n: n != "fused")


@lru_cache(maxsize=8)
def _read(path: str, _mtime: float) -> Policy:
    with open(path) as f:
        return Policy(json.load(f))


def load(name: str) -> Policy:
    """Load a policy by name. Re-reads the file if it has been retrained since."""
    path = os.path.join(RUNS, f"{os.path.basename(name)}.policy.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"no policy called {name}")
    return _read(path, os.path.getmtime(path))


def make_env(policy: Policy, scenario: str, seed: int) -> FlightEnv:
    """The environment the policy trained in, with one scenario's changes on top."""
    s, m = SCENARIOS[scenario], policy.meta
    cfg = EnvConfig(obs_mode=policy.obs_mode, n_towers=s.get("towers", m.get("towers", -1)))
    cfg.wind_min, cfg.wind_max = s.get("wind", (0.0, m.get("wind", cfg.wind_max)))
    cfg.sensors.orbit_err_m = s.get("orbit_error", m.get("orbit_error", 0.0))
    cfg.turn_max_deg = s.get("turn_max", cfg.turn_max_deg)
    cfg.leg_km = s.get("legs", cfg.leg_km)
    cfg.n_waypoints = s.get("waypoints", cfg.n_waypoints)
    cfg.max_steps = s.get("max_steps", cfg.max_steps)
    cfg.lat_range = s.get("lat", cfg.lat_range)
    return FlightEnv(cfg, seed=seed)


def sky_view(env, reading):
    """Satellites as the aeroplane sees them: azimuth/elevation and their Doppler."""
    out = []
    for k, valid in enumerate(reading.sat_valid):
        if not valid:
            continue
        e, n, u = env.anchor.R.T @ reading.sat_dirs[k]          # ECEF direction -> local
        out.append({
            "n": env.suite.sky.names[reading.sat_ids[k]].replace("IRIDIUM ", "") if k < len(reading.sat_ids) else "?",
            "az": round(float(np.degrees(np.arctan2(e, n))) % 360.0, 1),
            "el": round(float(np.degrees(np.arcsin(np.clip(u, -1, 1)))), 1),
            "khz": round(float(reading.sat_doppler[k]), 2),
        })
    return out


def outcome(env, info) -> str:
    """How the flight ended, checked in the same order as FlightEnv.step()."""
    if info["crashed"]:
        s = env.state
        if s.pos[2] <= 0.0:
            return "hit the sea"
        if s.airspeed <= env.ac.v_min:
            return "stalled"
        if abs(s.bank) > np.radians(60.0):
            return "over-banked"
        return "strayed off the map"
    return "route complete" if env.captured >= len(env.route) else "ran out of time"


def rollout(act, env, record=False):
    """One flight, with `act(obs) -> action` in control. Returns a summary, plus the track if `record`."""
    obs = env.reset()
    frames = []
    total = 0.0
    nav, alt = [], []
    while True:
        a = np.clip(act(obs), -1.0, 1.0)      # what the aeroplane actually gets, so that's what we record
        obs, r, done, trunc, info = env.step(a)
        total += r
        nav.append(info["nav_error"])
        alt.append(abs(info["alt_error"]))
        if record:
            s = env.state
            rd = env.reading
            frames.append({
                "obs": [round(float(v), 3) for v in obs],
                "act": [round(float(v), 3) for v in a],
                "sky": sky_view(env, rd),
                "twr": [{"i": int(b), "km": round(float(rd.tower_range[k]) / 1000.0, 2)}
                        for k, b in enumerate(rd.tower_ids[: len(rd.tower_valid)])],
                "t": round(env.steps * env.cfg.dt, 2),
                "p": [round(float(v), 1) for v in s.pos],
                "e": [round(float(v), 1) for v in info["est_pos"]],
                "hdg": round(float(s.heading), 4),
                "bank": round(float(s.bank), 4),
                "gam": round(float(s.gamma), 4),
                "v": round(float(s.airspeed), 1),
                "wp": env.wp,
                "sats": info["n_sats"],
                "towers": info["n_towers"],
                "nav": round(info["nav_error"], 1),
            })
        if done or trunc:
            break
    out = {"return": total, "captured": env.captured, "crashed": info["crashed"],
           "steps": env.steps, "nav_error": float(np.mean(nav)), "alt_error": float(np.mean(alt)),
           "route_done": env.captured >= len(env.route), "waypoints": len(env.route),
           "outcome": outcome(env, info)}
    if record:
        out["flight"] = {
            "dt": env.cfg.dt,
            "obs_mode": env.cfg.obs_mode,
            "obs_labels": env.obs_labels(),
            "obs_groups": env.obs_groups(),
            "obs_sources": env.obs_sources(),
            "act_labels": ["Bank command", "Pitch command", "Throttle"],
            "target_alt": env.cfg.target_alt,
            "capture_m": env.cfg.capture_m,
            "n_towers": len(env.towers),
            "wind": [round(float(v), 2) for v in env.wind],
            # the randomised anchor this flight actually used, not the config default
            "anchor": [round(float(np.degrees(env.anchor.lat)), 3),
                       round(float(np.degrees(env.anchor.lon)), 3)],
            "towers": [[round(float(v), 1) for v in t] for t in env.towers],
            "route": [[round(float(v), 1) for v in w] for w in env.route],
            "frames": frames,
        }
    return out


def fly_one(name: str, scenario: str, seed: int) -> dict:
    """One full flight in the same shape as a saved replay. The same seed is the same flight."""
    policy = load(name)
    r = rollout(policy, make_env(policy, scenario, seed), record=True)
    s = SCENARIOS[scenario]
    return {"name": name, "live": True, "seed": seed,
            "scenario": {"key": scenario, "title": s["title"], "trained": s["trained"]},
            "summary": {k: v for k, v in r.items() if k != "flight"}, **r["flight"]}


def fly_batch(name: str, scenario: str, flights: int, seed: int) -> dict:
    """Several flights on consecutive seeds, summaries only: how it does, not one lucky run."""
    policy = load(name)
    rows = []
    for s in range(seed, seed + flights):
        env = make_env(policy, scenario, s)
        r = rollout(policy, env)
        rows.append({"seed": s, "seconds": r["steps"] * env.cfg.dt,
                     **{k: r[k] for k in ("captured", "waypoints", "crashed", "route_done",
                                          "outcome", "nav_error", "alt_error")}})
    return {"scenario": scenario, "flights": rows, "stats": {
        "completed": float(np.mean([r["route_done"] for r in rows])),
        "crashed": float(np.mean([r["crashed"] for r in rows])),
        "alt_error": float(np.mean([r["alt_error"] for r in rows])),
        "nav_error": float(np.mean([r["nav_error"] for r in rows])),
    }}
