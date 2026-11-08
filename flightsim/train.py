"""Train a PPO agent to fly the route on sensor data alone.

  python train.py                          # the realistic case: filtered sensors
  python train.py --obs-mode truth         # the ceiling, for comparison
  python train.py --obs-mode raw           # no navigation filter at all
  python train.py --towers 0 --steps 600000  # satellites only: much harder

Checkpoints land in runs/<name>.pt and can be flown with fly.py.
"""
from __future__ import annotations

import argparse
import json
import os
import time

import numpy as np
import torch

from env import EnvConfig, FlightEnv
from ppo import ActorCritic, PPOConfig, RunningNorm, VecEnv, rollout, update


def save_network_movie(path, snaps, env, obs_mode, k1=14, k2=14):
    """Write the policy's weights over training, thinned enough to draw.

    All inputs and outputs are kept - they are the meaningful ends. Only a subset of the
    two 64-unit hidden layers is shown, chosen by how much each unit drives the controls
    in the FINAL network, and the same units are then shown at every step so the picture
    is a movie of one network rather than a different sample each frame.
    """
    if not snaps:
        return
    w1, w2, w3 = snaps[-1]["w"]                       # (64,obs) (64,64) (3,64)
    pick2 = np.argsort(-np.abs(w3).sum(axis=0))[:k2]  # units the outputs lean on most
    pick1 = np.argsort(-np.abs(w2[pick2]).sum(axis=0))[:k1]
    r3 = lambda a: [[round(float(v), 3) for v in row] for row in a]   # noqa: E731
    movie = {
        "obs_mode": obs_mode,
        "input_labels": env.obs_labels(),
        "input_groups": env.obs_groups(),
        "output_labels": ["Bank command", "Pitch command", "Throttle"],
        "hidden": [int(k1), int(k2)],
        "full_hidden": int(w1.shape[0]),
        "frames": [
            {"step": s["step"], "return": round(float(s["return"]), 2),
             # weights are [to][from], as torch stores them
             "w1": r3(s["w"][0][np.ix_(pick1, range(w1.shape[1]))]),
             "w2": r3(s["w"][1][np.ix_(pick2, pick1)]),
             "w3": r3(s["w"][2][:, pick2])}
            for s in snaps
        ],
    }
    with open(path, "w") as f:
        json.dump(movie, f)
    print(f"network movie: {len(snaps)} snapshots -> {path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--steps", type=int, default=400_000, help="total environment steps")
    ap.add_argument("--envs", type=int, default=16)
    ap.add_argument("--obs-mode", choices=["fused", "raw", "truth"], default="fused")
    ap.add_argument("--towers", type=int, default=4, help="ground stations (0 = satellites only)")
    ap.add_argument("--wind", type=float, default=25.0, help="max wind speed [m/s]")
    ap.add_argument("--orbit-error", type=float, default=0.0, help="satellite position error [m]")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--lr", type=float, default=PPOConfig.lr)
    ap.add_argument("--name", default=None, help="checkpoint name (default: the obs mode)")
    ap.add_argument("--snapshots", type=int, default=48, help="weight snapshots to keep for the UI")
    a = ap.parse_args()

    torch.manual_seed(a.seed)
    np.random.seed(a.seed)
    device = torch.device("cpu")           # the nets are tiny; a GPU is slower here
    pcfg = PPOConfig(lr=a.lr)

    def make_env(seed):
        cfg = EnvConfig(obs_mode=a.obs_mode, n_towers=a.towers, wind_max=a.wind)
        cfg.sensors.orbit_err_m = a.orbit_error
        return FlightEnv(cfg, seed=seed)

    venv = VecEnv(make_env, a.envs, seed=a.seed * 1000)
    venv.reset()
    obs_dim, act_dim = venv.envs[0].obs_dim, venv.envs[0].action_dim
    model = ActorCritic(obs_dim, act_dim, log_std_init=pcfg.log_std_init).to(device)
    norm = RunningNorm(obs_dim)
    opt = torch.optim.Adam(model.parameters(), lr=pcfg.lr, eps=1e-5)

    name = a.name or a.obs_mode
    os.makedirs("runs", exist_ok=True)
    print(f"{a.obs_mode}: obs {obs_dim}, {a.envs} envs, {a.towers} towers, wind <= {a.wind:.0f} m/s")
    print(f"{'steps':>9} {'return':>9} {'waypoints':>10} {'crash %':>8} {'alt err':>9} {'nav err':>9} {'min':>6}")

    per_update = pcfg.rollout * a.envs
    best = -1e9
    t0 = time.time()
    done_steps = 0
    # Snapshots of the policy's weights as they change, for the "watch it learn" view.
    snaps = []
    updates = max(1, a.steps // per_update)
    snap_every = max(1, updates // max(a.snapshots, 1))
    n_update = 0
    while done_steps < a.steps:
        batch = rollout(model, venv, norm, pcfg, device)
        update(model, opt, batch, norm, pcfg, device)
        done_steps += per_update
        n_update += 1

        eps = venv.finished[-40:]   # the last few dozen flights, so the numbers keep up
        if eps:
            ret = float(np.mean([e["return"] for e in eps]))
            wp = float(np.mean([e["captured"] for e in eps]))
            crash = 100.0 * float(np.mean([e["crashed"] for e in eps]))
            alt = float(np.mean([e["alt_error"] for e in eps]))
            nav = float(np.mean([e["nav_error"] for e in eps]))
            print(f"{done_steps:>9,} {ret:>9.1f} {wp:>10.2f} {crash:>8.0f} {alt:>8.0f}m {nav:>8.0f}m {(time.time()-t0)/60:>6.1f}")
            if n_update % snap_every == 0 or done_steps >= a.steps:
                snaps.append({"step": done_steps, "return": ret,
                              "w": [model.pi[i].weight.detach().cpu().numpy().copy() for i in (0, 2, 4)]})
            if ret > best:
                best = ret
                torch.save({"model": model.state_dict(), "norm": norm.state_dict(),
                            "obs_mode": a.obs_mode, "towers": a.towers, "wind": a.wind,
                            "orbit_error": a.orbit_error, "obs_dim": obs_dim, "act_dim": act_dim,
                            "return": ret, "steps": done_steps},
                           os.path.join("runs", f"{name}.pt"))
    save_network_movie(os.path.join("runs", f"{name}.net.json"), snaps, venv.envs[0], a.obs_mode)
    print(f"\nbest mean return {best:.1f} -> runs/{name}.pt  ({(time.time()-t0)/60:.1f} min)")
    with open(os.path.join("runs", f"{name}.json"), "w") as f:
        json.dump({"obs_mode": a.obs_mode, "towers": a.towers, "steps": a.steps,
                   "best_return": best, "minutes": (time.time() - t0) / 60}, f, indent=1)


if __name__ == "__main__":
    main()
