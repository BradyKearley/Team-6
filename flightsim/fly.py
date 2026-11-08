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
from live import rollout
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


def fly(model, norm, env, deterministic=True, record=False):
    """One flight. Returns a summary, and the whole track if `record`."""
    def act(obs):
        with torch.no_grad():
            o = torch.as_tensor(norm(obs[None]), dtype=torch.float32)
            return model.pi(o)[0].numpy() if deterministic else model.act(o)[0][0].numpy()
    return rollout(act, env, record)


def export_policy(ck_path: str, out_path: str, name: str):
    """Write the actor's weights as JSON, so the web server can fly it without torch."""
    ck = torch.load(ck_path, map_location="cpu", weights_only=False)
    sd = ck["model"]
    idx = sorted(int(k.split(".")[1]) for k in sd if k.startswith("pi.") and k.endswith(".weight"))
    with open(out_path, "w") as f:
        json.dump({"name": name, "obs_mode": ck["obs_mode"], "towers": ck.get("towers", -1),
                   "wind": ck.get("wind", 25.0), "orbit_error": ck.get("orbit_error", 0.0),
                   "obs_dim": ck["obs_dim"], "act_dim": ck["act_dim"],
                   "steps": ck.get("steps"), "return": ck.get("return"),
                   "layers": [{"w": sd[f"pi.{i}.weight"].tolist(), "b": sd[f"pi.{i}.bias"].tolist()} for i in idx],
                   "norm": {"mean": np.asarray(ck["norm"]["mean"]).tolist(),
                            "var": np.asarray(ck["norm"]["var"]).tolist()}}, f)
    return out_path


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
    ap.add_argument("--export", action="store_true", help="write runs/<name>.policy.json for the web UI and stop")
    a = ap.parse_args()

    path = os.path.join("runs", f"{a.name}.pt")
    if a.export:
        print("wrote", export_policy(path, os.path.join("runs", f"{a.name}.policy.json"), a.name))
        return
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
