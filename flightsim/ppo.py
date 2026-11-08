"""PPO, small and self-contained.

Clipped-surrogate PPO with GAE, running observation normalisation and a Gaussian policy
over the three continuous controls. Two 64-unit hidden layers is plenty for a point-mass
aeroplane, and it trains on a laptop CPU in a few minutes.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import torch
import torch.nn as nn


@dataclass
class PPOConfig:
    lr: float = 3e-4
    gamma: float = 0.995          # 600-step episodes, so look a long way ahead
    lam: float = 0.95             # GAE
    clip: float = 0.2
    epochs: int = 10
    minibatch: int = 512
    rollout: int = 256            # steps per environment per update
    ent_coef: float = 0.003
    vf_coef: float = 0.5
    max_grad_norm: float = 0.5
    log_std_init: float = -0.5


class RunningNorm:
    """Welford mean/variance, so the network sees roughly unit-scale inputs."""

    def __init__(self, dim: int):
        self.mean = np.zeros(dim)
        self.var = np.ones(dim)
        self.count = 1e-4

    def update(self, x: np.ndarray):
        bm, bv, bc = x.mean(0), x.var(0), x.shape[0]
        d = bm - self.mean
        tot = self.count + bc
        self.mean += d * bc / tot
        m_a = self.var * self.count
        m_b = bv * bc
        self.var = (m_a + m_b + d ** 2 * self.count * bc / tot) / tot
        self.count = tot

    def __call__(self, x):
        return np.clip((x - self.mean) / np.sqrt(self.var + 1e-8), -10.0, 10.0)

    def state_dict(self):
        return {"mean": self.mean, "var": self.var, "count": self.count}

    def load_state_dict(self, d):
        self.mean, self.var, self.count = d["mean"], d["var"], d["count"]


def mlp(sizes, out_gain=1.0):
    layers = []
    for i in range(len(sizes) - 1):
        lin = nn.Linear(sizes[i], sizes[i + 1])
        gain = out_gain if i == len(sizes) - 2 else np.sqrt(2)
        nn.init.orthogonal_(lin.weight, gain)
        nn.init.zeros_(lin.bias)
        layers += [lin, nn.Tanh()] if i < len(sizes) - 2 else [lin]
    return nn.Sequential(*layers)


class ActorCritic(nn.Module):
    def __init__(self, obs_dim: int, act_dim: int, hidden=(64, 64), log_std_init=-0.5):
        super().__init__()
        self.pi = mlp([obs_dim, *hidden, act_dim], out_gain=0.01)
        self.v = mlp([obs_dim, *hidden, 1], out_gain=1.0)
        self.log_std = nn.Parameter(torch.full((act_dim,), float(log_std_init)))

    def dist(self, obs):
        return torch.distributions.Normal(self.pi(obs), self.log_std.exp())

    def act(self, obs):
        d = self.dist(obs)
        a = d.sample()
        return a, d.log_prob(a).sum(-1), self.v(obs).squeeze(-1)

    def evaluate(self, obs, act):
        d = self.dist(obs)
        return d.log_prob(act).sum(-1), d.entropy().sum(-1), self.v(obs).squeeze(-1)


class VecEnv:
    """A handful of environments stepped in a loop, auto-resetting when a flight ends."""

    def __init__(self, make_env, n: int, seed: int = 0):
        self.envs = [make_env(seed + i) for i in range(n)]
        self.n = n
        self.finished = []            # episode summaries, drained by the trainer
        self.ep_ret = np.zeros(n)
        self.ep_alt = np.zeros(n)

    def reset(self):
        self.ep_ret[:] = 0.0
        self.ep_alt[:] = 0.0
        self.obs = np.stack([e.reset() for e in self.envs])
        return self.obs

    def step(self, actions):
        obs, rew, done = [], np.zeros(self.n), np.zeros(self.n, dtype=bool)
        for i, (e, a) in enumerate(zip(self.envs, actions)):
            o, r, d, tr, info = e.step(a)
            rew[i] = r
            self.ep_ret[i] += r
            self.ep_alt[i] += abs(info["alt_error"])
            if d or tr:
                self.finished.append({"return": self.ep_ret[i], "captured": info["captured"],
                                      "crashed": info["crashed"], "steps": e.steps,
                                      "nav_error": info["nav_error"],
                                      "alt_error": self.ep_alt[i] / max(e.steps, 1)})
                self.ep_ret[i] = 0.0
                self.ep_alt[i] = 0.0
                o = e.reset()
                # A flight that merely ran out of time is not a failure of the policy, but
                # cutting the value bootstrap there is the standard (small) approximation.
                done[i] = True
            obs.append(o)
        return np.stack(obs), rew, done


def rollout(model, venv, norm, cfg, device):
    """Collect `cfg.rollout` steps from every environment."""
    T, N = cfg.rollout, venv.n
    obs_buf = np.zeros((T, N, venv.envs[0].obs_dim))
    act_buf = np.zeros((T, N, venv.envs[0].action_dim))
    logp_buf = np.zeros((T, N))
    val_buf = np.zeros((T, N))
    rew_buf = np.zeros((T, N))
    done_buf = np.zeros((T, N))

    obs = venv.obs
    for t in range(T):
        obs_buf[t] = obs
        with torch.no_grad():
            o = torch.as_tensor(norm(obs), dtype=torch.float32, device=device)
            a, logp, v = model.act(o)
        act_buf[t], logp_buf[t], val_buf[t] = a.cpu().numpy(), logp.cpu().numpy(), v.cpu().numpy()
        obs, r, d = venv.step(act_buf[t])
        rew_buf[t], done_buf[t] = r, d
    venv.obs = obs

    with torch.no_grad():
        last_v = model.v(torch.as_tensor(norm(obs), dtype=torch.float32, device=device)).squeeze(-1).cpu().numpy()

    # GAE
    adv = np.zeros_like(rew_buf)
    gae = np.zeros(N)
    for t in reversed(range(T)):
        nxt = last_v if t == T - 1 else val_buf[t + 1]
        nonterm = 1.0 - done_buf[t]
        delta = rew_buf[t] + cfg.gamma * nxt * nonterm - val_buf[t]
        gae = delta + cfg.gamma * cfg.lam * nonterm * gae
        adv[t] = gae
    ret = adv + val_buf
    norm.update(obs_buf.reshape(-1, obs_buf.shape[-1]))
    return (obs_buf.reshape(-1, obs_buf.shape[-1]), act_buf.reshape(-1, act_buf.shape[-1]),
            logp_buf.ravel(), adv.ravel(), ret.ravel())


def update(model, opt, batch, norm, cfg, device):
    obs, act, logp_old, adv, ret = batch
    obs = torch.as_tensor(norm(obs), dtype=torch.float32, device=device)
    act = torch.as_tensor(act, dtype=torch.float32, device=device)
    logp_old = torch.as_tensor(logp_old, dtype=torch.float32, device=device)
    adv = torch.as_tensor(adv, dtype=torch.float32, device=device)
    ret = torch.as_tensor(ret, dtype=torch.float32, device=device)
    adv = (adv - adv.mean()) / (adv.std() + 1e-8)

    n = obs.shape[0]
    stats = {}
    for _ in range(cfg.epochs):
        for idx in torch.randperm(n, device=device).split(cfg.minibatch):
            logp, ent, v = model.evaluate(obs[idx], act[idx])
            ratio = (logp - logp_old[idx]).exp()
            pg = -torch.min(ratio * adv[idx],
                            ratio.clamp(1 - cfg.clip, 1 + cfg.clip) * adv[idx]).mean()
            vf = ((v - ret[idx]) ** 2).mean()
            loss = pg + cfg.vf_coef * vf - cfg.ent_coef * ent.mean()
            opt.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), cfg.max_grad_norm)
            opt.step()
            stats = {"policy_loss": pg.item(), "value_loss": vf.item(), "entropy": ent.mean().item()}
    return stats
