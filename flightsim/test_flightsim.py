"""Run with:  .venv/bin/python -m pytest -q"""
import json
import os

import numpy as np
import pytest

import dynamics as dyn
from env import EnvConfig, FlightEnv
from live import SCENARIOS, make_env
from live import rollout as fly_rollout
from ppo import ActorCritic, PPOConfig, RunningNorm, VecEnv, rollout, update
from sensors import Anchor


# ---- the aeroplane ---------------------------------------------------------------
def test_level_flight_stays_level():
    ac = dyn.Aircraft()
    s = dyn.State(pos=np.array([0.0, 0.0, 9000.0]), airspeed=200.0, vel=dyn.air_velocity(200, 0, 0))
    th = dyn.trim_throttle(ac, 200.0, 9000.0)
    for _ in range(240):
        s = dyn.step(ac, s, 0.0, 0.0, th, np.zeros(3), 0.5)
    assert abs(s.pos[2] - 9000.0) < 1.0
    assert abs(s.airspeed - 200.0) < 2.0
    assert s.pos[1] > 20_000.0            # it went north


def test_turn_rate_matches_the_physics():
    """A coordinated turn turns at g*tan(bank)/V, which is what the agent has to live with."""
    ac = dyn.Aircraft()
    bank = np.radians(30.0)
    s = dyn.State(pos=np.array([0.0, 0.0, 9000.0]), airspeed=200.0, bank=bank,
                  vel=dyn.air_velocity(200, 0, 0))
    for _ in range(120):
        s = dyn.step(ac, s, bank, 0.0, dyn.trim_throttle(ac, 200.0, 9000.0), np.zeros(3), 0.5)
    expected = dyn.G * np.tan(bank) / s.airspeed * 60.0
    assert np.degrees(s.heading) == pytest.approx(np.degrees(expected), rel=0.1)


def test_wind_pushes_it_sideways():
    ac = dyn.Aircraft()
    wind = np.array([30.0, 0.0, 0.0])      # 30 m/s from the west
    s = dyn.State(pos=np.array([0.0, 0.0, 9000.0]), airspeed=200.0, vel=dyn.air_velocity(200, 0, 0))
    for _ in range(120):
        s = dyn.step(ac, s, 0.0, 0.0, 0.7, wind, 0.5)
    assert s.pos[0] == pytest.approx(30.0 * 60.0, rel=0.02)   # drifted exactly with the wind


# ---- frames and sensors ------------------------------------------------------------
def test_enu_ecef_round_trip():
    a = Anchor(45.0, -60.0)
    p = np.array([12_345.0, -6_789.0, 9_000.0])
    assert np.allclose(a.to_enu(a.to_ecef(p)), p, atol=1e-6)


def test_navigation_filter_beats_dead_reckoning():
    """The whole point of the sensor suite: the filter has to hold the IMU down."""
    env = FlightEnv(EnvConfig(obs_mode="fused"), seed=0)
    env.reset(seed=0)
    for _ in range(400):
        env.step(np.array([0.0, 0.0, 0.0]))
    nav = np.linalg.norm(env.anchor.to_enu(env.reading.est_pos) - env.state.pos)
    dr = np.linalg.norm(env.dr_pos - env.state.pos)
    assert nav < 200.0
    assert nav < dr / 5.0


# ---- the task ----------------------------------------------------------------------
@pytest.mark.parametrize("mode,dim", [("truth", 18), ("fused", 19), ("raw", 46)])
def test_observation_shapes(mode, dim):
    env = FlightEnv(EnvConfig(obs_mode=mode), seed=0)
    obs = env.reset(seed=1)
    assert obs.shape == (dim,) and env.obs_dim == dim
    assert np.isfinite(obs).all()
    # the UI shows these names against the live values, so they must stay in step
    assert len(env.obs_labels()) == dim


def test_every_flight_gets_a_new_map():
    """Route, tower network and position on Earth are redrawn, or the policy just memorises."""
    env = FlightEnv(EnvConfig(), seed=0)
    layouts, places, routes = set(), set(), set()
    for k in range(8):
        env.reset(seed=k)
        layouts.add((len(env.towers), round(float(env.towers[0][0]), 1) if len(env.towers) else 0))
        places.add((round(float(np.degrees(env.anchor.lat)), 2), round(float(np.degrees(env.anchor.lon)), 2)))
        routes.add(round(float(env.route[0][0]), 1))
    assert len(places) == 8 and len(routes) == 8
    assert len(layouts) > 1
    # and pinning it off gives the same map every time
    fixed = FlightEnv(EnvConfig(randomize=False, n_towers=4), seed=0)
    a = fixed.towers.copy()
    fixed.reset(seed=5)
    assert np.allclose(a, fixed.towers)


def test_same_seed_same_flight():
    a = FlightEnv(EnvConfig(), seed=0)
    b = FlightEnv(EnvConfig(), seed=0)
    oa, ob = a.reset(seed=7), b.reset(seed=7)
    assert np.allclose(oa, ob)
    for _ in range(20):
        act = np.array([0.2, -0.1, 0.3])
        ra = a.step(act)
        rb = b.step(act)
        assert np.allclose(ra[0], rb[0]) and ra[1] == rb[1]


def test_flying_at_the_waypoint_scores_better_than_away_from_it():
    env = FlightEnv(EnvConfig(), seed=2)
    env.reset(seed=2)
    straight = sum(env.step(np.array([0.0, 0.0, 0.2]))[1] for _ in range(40))
    env.reset(seed=2)
    turning = sum(env.step(np.array([1.0, 0.0, 0.2]))[1] for _ in range(40))
    assert straight > turning   # it starts pointed at the first waypoint


def test_a_crash_ends_the_flight():
    """Low enough that a steady dive reaches the sea inside the time limit."""
    env = FlightEnv(EnvConfig(target_alt=1500.0), seed=3)
    env.reset(seed=3)
    done = False
    for _ in range(600):
        _, r, done, trunc, info = env.step(np.array([0.0, -1.0, 0.0]))  # nose down, idle
        if done:
            break
    assert done and info["crashed"]
    assert r < 0


def test_running_out_of_speed_ends_the_flight():
    env = FlightEnv(EnvConfig(), seed=4)
    env.reset(seed=4)
    for _ in range(600):
        _, _, done, _, info = env.step(np.array([0.0, 1.0, -1.0]))      # nose up, idle
        if done:
            break
    assert done and info["crashed"] and info["airspeed"] <= env.ac.v_min + 1e-6


# ---- the learner --------------------------------------------------------------------
def test_one_ppo_update_runs():
    import torch

    cfg = PPOConfig(rollout=8, epochs=1, minibatch=16)
    venv = VecEnv(lambda s: FlightEnv(EnvConfig(max_steps=20), seed=s), 2)
    venv.reset()
    model = ActorCritic(venv.envs[0].obs_dim, 3)
    norm = RunningNorm(venv.envs[0].obs_dim)
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    before = [p.clone() for p in model.parameters()]
    batch = rollout(model, venv, norm, cfg, torch.device("cpu"))
    assert all(np.isfinite(b).all() for b in batch)
    update(model, opt, batch, norm, cfg, torch.device("cpu"))
    assert any(not torch.equal(a, b) for a, b in zip(before, model.parameters()))


# ---- flying it live -----------------------------------------------------------------
def _exported(tmp_path, obs_dim=19):
    """A freshly initialised network, saved and exported the way train.py does it."""
    import torch

    from fly import export_policy
    from live import Policy

    model = ActorCritic(obs_dim, 3)
    norm = RunningNorm(obs_dim)
    norm.update(np.random.default_rng(0).normal(0, 3, (500, obs_dim)))
    ck = tmp_path / "t.pt"
    torch.save({"model": model.state_dict(), "norm": norm.state_dict(), "obs_mode": "fused",
                "towers": 4, "wind": 25.0, "orbit_error": 0.0, "obs_dim": obs_dim, "act_dim": 3}, ck)
    out = tmp_path / "t.policy.json"
    export_policy(str(ck), str(out), "t")
    with open(out) as f:
        return model, norm, Policy(json.load(f))


def test_exported_policy_matches_the_torch_network(tmp_path):
    """The server flies the numpy copy, so it has to be the same network."""
    import torch

    model, norm, policy = _exported(tmp_path)
    for obs in np.random.default_rng(1).normal(0, 2, (50, 19)):
        with torch.no_grad():
            want = model.pi(torch.as_tensor(norm(obs[None]), dtype=torch.float32))[0].numpy()
        assert np.allclose(policy(obs), want, atol=1e-5)


@pytest.mark.parametrize("key", list(SCENARIOS))
def test_every_scenario_builds_a_flyable_env(key, tmp_path):
    _, _, policy = _exported(tmp_path)
    env = make_env(policy, key, seed=3)
    env.cfg.max_steps = 5
    r = fly_rollout(policy, env, record=True)
    assert r["steps"] <= 5 and r["outcome"] and len(r["flight"]["frames"]) == r["steps"]
    json.dumps(r)                # the server sends this straight to the browser


def test_scenarios_change_what_they_claim(tmp_path):
    _, _, policy = _exported(tmp_path)
    assert len(make_env(policy, "sats_only", 1).towers) == 0
    assert np.linalg.norm(make_env(policy, "calm", 1).wind) == 0.0
    assert 45.0 <= np.linalg.norm(make_env(policy, "gale", 1).wind) <= 60.0
    assert len(make_env(policy, "long_haul", 1).route) == 6
    assert np.degrees(make_env(policy, "polar", 1).anchor.lat) >= 75.0
    assert make_env(policy, "bad_orbits", 1).cfg.sensors.orbit_err_m == 1500.0


def test_same_seed_is_the_same_live_flight(tmp_path):
    """The page re-flies a seed to show one flight from a batch, so it must be repeatable."""
    _, _, policy = _exported(tmp_path)
    a = fly_rollout(policy, make_env(policy, "standard", 11))
    b = fly_rollout(policy, make_env(policy, "standard", 11))
    assert a == b


@pytest.mark.skipif(not os.path.exists("runs/fused.pt"), reason="no trained policy yet")
def test_the_trained_policy_flies_the_route():
    from fly import fly, load_policy, make_env

    model, norm, ck = load_policy("runs/fused.pt")
    results = [fly(model, norm, make_env(ck, seed=500 + i)) for i in range(5)]
    assert np.mean([r["route_done"] for r in results]) >= 0.6
    assert not any(r["crashed"] for r in results)
