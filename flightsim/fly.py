"""Fly a trained policy and write the flight out for the web UI to replay.

  python fly.py                       # fly runs/fused.pt, save runs/fused.flight.json
  python fly.py --episodes 20 --no-save   # just the statistics
  python fly.py --name truth --deterministic

The JSON it writes is what GET /api/flight/replay serves to the "RL Autopilot" page.
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np
import torch

from env import EnvConfig, FlightEnv
from ppo import ActorCritic, RunningNorm


def load_policy(path: str):
    ck = torch.load(path, map_location="cpu", weights_only=False)
    model = ActorCritic(ck["obs_dim"], ck["act_dim"])
    model.load_state_dict(ck["model"])
    model.eval()
    norm = RunningNorm(ck["obs_dim"])
    norm.load_state_dict(ck["norm"])
    return model, norm, ck


def make_env(ck, seed, towers=None, orbit_error=None, wind=None):
    """Rebuild the training environment, optionally with the navigation made worse.

    Flying a policy under conditions it never trained on is how you find out whether it
    was really using the sensors or just riding the shape of the task.
    """
    cfg = EnvConfig(obs_mode=ck["obs_mode"],
                    n_towers=ck.get("towers", -1) if towers is None else towers,
                    wind_max=ck.get("wind", 25.0) if wind is None else wind)
    cfg.sensors.orbit_err_m = ck.get("orbit_error", 0.0) if orbit_error is None else orbit_error
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


def fly(model, norm, env, deterministic=True, record=False):
    """One flight. Returns a summary, and the whole track if `record`."""
    obs = env.reset()
    frames = []
    total = 0.0
    nav, alt = [], []
    while True:
        with torch.no_grad():
            o = torch.as_tensor(norm(obs[None]), dtype=torch.float32)
            a = model.pi(o)[0].numpy() if deterministic else model.act(o)[0][0].numpy()
        a = np.clip(a, -1.0, 1.0)      # what the aeroplane actually gets, so that's what we record
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
           "route_done": env.captured >= len(env.route)}
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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default="fused", help="checkpoint in runs/ to fly")
    ap.add_argument("--episodes", type=int, default=10)
    ap.add_argument("--seed", type=int, default=100)
    ap.add_argument("--stochastic", action="store_true", help="sample actions instead of taking the mean")
    ap.add_argument("--no-save", action="store_true", help="don't write the replay file")
    ap.add_argument("--towers", type=int, default=None, help="override the tower count (0 = satellites only)")
    ap.add_argument("--orbit-error", type=float, default=None, help="override the satellite position error [m]")
    ap.add_argument("--wind", type=float, default=None, help="override the maximum wind [m/s]")
    a = ap.parse_args()

    path = os.path.join("runs", f"{a.name}.pt")
    model, norm, ck = load_policy(path)
    print(f"{path}: {ck['obs_mode']} observations, {ck.get('towers', 4)} towers, "
          f"trained {ck['steps']:,} steps (mean return {ck['return']:.1f})")

    rows = []
    best = None
    for i in range(a.episodes):
        env = make_env(ck, seed=a.seed + i, towers=a.towers, orbit_error=a.orbit_error, wind=a.wind)
        r = fly(model, norm, env, deterministic=not a.stochastic, record=True)
        rows.append(r)
        if best is None or (r["captured"], -r["steps"]) > (best["captured"], -best["steps"]):
            best = r
        print(f"  flight {i + 1:>2}: {r['captured']}/{len(env.route)} waypoints, "
              f"{'CRASHED' if r['crashed'] else 'ok':>7}, alt err {r['alt_error']:>5.0f} m, "
              f"nav err {r['nav_error']:>5.0f} m, return {r['return']:>6.1f}")

    done = np.mean([r["route_done"] for r in rows])
    print(f"\n{a.episodes} flights: routes completed {done * 100:.0f}%, "
          f"waypoints {np.mean([r['captured'] for r in rows]):.2f}/3, "
          f"crashes {100 * np.mean([r['crashed'] for r in rows]):.0f}%, "
          f"altitude error {np.mean([r['alt_error'] for r in rows]):.0f} m, "
          f"navigation error {np.mean([r['nav_error'] for r in rows]):.0f} m")

    if not a.no_save:
        out = os.path.join("runs", f"{a.name}.flight.json")
        with open(out, "w") as f:
            json.dump({"name": a.name, "summary": {k: v for k, v in best.items() if k != "flight"},
                       **best["flight"]}, f)
        print(f"saved the best flight for replay: {out}")


if __name__ == "__main__":
    main()
