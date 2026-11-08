"""The task: fly a route of waypoints without GPS.

A flight starts at cruise, somewhere over the North Atlantic, with a route of four
waypoints and a wind the aeroplane is never told about. The agent commands bank, flight
path angle and throttle twice a second, and is scored on getting to each waypoint while
holding its altitude.

What it sees is the whole point, so it is switchable:

  fused   what the navigation filter makes of the sensors  (the realistic case)
  raw     dead reckoning plus the raw radio measurements   (no filter - the hard case)
  truth   perfect knowledge                                (the ceiling, for comparison)

The reward always uses the true state: it is the training signal, not something the
aeroplane knows.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

import dynamics as dyn
from sensors import Anchor, SensorConfig, SensorSuite

OBS_MODES = ("fused", "raw", "truth")


@dataclass
class EnvConfig:
    dt: float = 0.5
    max_steps: int = 600              # 300 s of flight
    obs_mode: str = "fused"
    # the route
    n_waypoints: int = 3
    leg_km: tuple[float, float] = (8.0, 14.0)
    turn_max_deg: float = 70.0
    # The aeroplane cannot turn tighter than ~4 km at cruise, so the gate has to be
    # wider than a point: 1.5 km is "overhead" at this scale.
    capture_m: float = 1500.0
    target_alt: float = 9000.0
    # conditions. Everything below is redrawn for each flight unless it is pinned, so a
    # policy has to learn to fly rather than memorise one map.
    start_speed: float = 200.0
    wind_min: float = 0.0             # m/s, only raised to test situations it did not train in
    wind_max: float = 25.0            # m/s, constant per flight, never observed
    randomize: bool = True
    anchor_lat: float = 45.0          # used when randomize is off
    anchor_lon: float = -60.0
    lat_range: tuple[float, float] = (25.0, 65.0)   # where in the world the flight happens,
    lon_range: tuple[float, float] = (-180.0, 180.0)  # which changes the satellite geometry
    n_towers: int = -1                # -1 = random per flight, else exactly this many
    tower_count_range: tuple[int, int] = (1, 5)
    tower_radius_km: tuple[float, float] = (12.0, 90.0)
    tower_spread_km: float = 45.0     # used when randomize is off
    # scoring
    w_progress: float = 1.0
    w_alt: float = 0.3
    w_smooth: float = 0.05
    w_bank: float = 0.02
    capture_bonus: float = 15.0
    crash_penalty: float = 50.0
    # Returns run to several hundred otherwise, which drowns the policy gradient in
    # value-function error. Scaling here keeps the critic's targets near unit scale.
    reward_scale: float = 0.05
    sensors: SensorConfig = field(default_factory=SensorConfig)


class FlightEnv:
    """Gym-shaped, but with no gym dependency: reset() -> obs, step(a) -> obs, r, done, info."""

    def __init__(self, cfg: EnvConfig | None = None, seed: int = 0):
        self.cfg = cfg or EnvConfig()
        if self.cfg.obs_mode not in OBS_MODES:
            raise ValueError(f"obs_mode must be one of {OBS_MODES}")
        self.cfg.sensors.dt = self.cfg.dt
        self.ac = dyn.Aircraft()
        self.anchor = Anchor(self.cfg.anchor_lat, self.cfg.anchor_lon)
        self.rng = np.random.default_rng(seed)
        self.action_dim = 3
        self._place_towers()
        self.suite = SensorSuite(self.cfg.sensors, self.anchor, self.towers)
        self.reset()
        self.obs_dim = len(self.obs)

    # ---- setup ---------------------------------------------------------------
    def _place_towers(self):
        """Scatter the ground stations: how many, and where, is drawn per flight.

        A fixed ring would let the policy memorise one geometry; the real point is that it
        has to cope with whatever infrastructure happens to be under it, including none.
        """
        c = self.cfg
        if not c.randomize:
            n = max(c.n_towers, 0) if c.n_towers >= 0 else 4
            ang = np.linspace(0, 2 * np.pi, max(n, 1), endpoint=False)[:n]
            d = c.tower_spread_km * 1000.0
            self.towers = np.stack([d * np.cos(ang), d * np.sin(ang), np.zeros(n)], axis=1) if n else np.zeros((0, 3))
            self.anchor = Anchor(c.anchor_lat, c.anchor_lon)
            return
        n = c.n_towers if c.n_towers >= 0 else int(self.rng.integers(c.tower_count_range[0], c.tower_count_range[1] + 1))
        ang = self.rng.uniform(0, 2 * np.pi, n)
        rad = np.sqrt(self.rng.uniform(0, 1, n)) * (c.tower_radius_km[1] - c.tower_radius_km[0]) * 1000.0 \
            + c.tower_radius_km[0] * 1000.0
        self.towers = np.stack([rad * np.cos(ang), rad * np.sin(ang), np.zeros(n)], axis=1)
        # Somewhere else in the world each flight: different latitude, different constellation.
        self.anchor = Anchor(float(self.rng.uniform(*c.lat_range)), float(self.rng.uniform(*c.lon_range)))

    def _make_route(self):
        c = self.cfg
        wps = []
        p = np.array([0.0, 0.0])
        heading = self.rng.uniform(0, 2 * np.pi)
        for _ in range(c.n_waypoints):
            heading += np.radians(self.rng.uniform(-c.turn_max_deg, c.turn_max_deg))
            leg = self.rng.uniform(*c.leg_km) * 1000.0
            p = p + leg * np.array([np.sin(heading), np.cos(heading)])
            wps.append(np.array([p[0], p[1], c.target_alt]))
        return wps, heading

    # ---- episode -------------------------------------------------------------
    def reset(self, seed: int | None = None):
        c = self.cfg
        if seed is not None:
            self.rng = np.random.default_rng(seed)
        self._place_towers()                 # new map, new tower network, new satellite geometry
        self.suite.set_layout(self.anchor, self.towers)
        self.route, _ = self._make_route()
        self.wp = 0
        self.steps = 0
        self.captured = 0

        start = np.array([self.rng.normal(0, 2000.0), self.rng.normal(0, 2000.0),
                          c.target_alt + self.rng.normal(0, 150.0)])
        to_first = self.route[0] - start
        heading = float(np.arctan2(to_first[0], to_first[1]) + np.radians(self.rng.normal(0, 15)))
        v = c.start_speed + self.rng.normal(0, 10.0)
        wdir = self.rng.uniform(0, 2 * np.pi)
        wspd = self.rng.uniform(c.wind_min, c.wind_max)
        self.wind = np.array([wspd * np.sin(wdir), wspd * np.cos(wdir), 0.0])

        self.state = dyn.State(pos=start, airspeed=v, heading=heading,
                               vel=dyn.air_velocity(v, heading, 0.0) + self.wind)
        self.dr_pos = start.copy()      # IMU-only dead reckoning, for the raw observation
        self.dr_vel = self.state.vel.copy()
        self.prev_action = np.zeros(3)
        self.t0 = float(self.rng.uniform(0, max(self.suite.sky.window - c.max_steps * c.dt - 10, 1)))
        self.suite.reset(self.rng, self._ecef(self.state.pos), self._ecef_vec(self.state.vel), self.t0)
        self.reading = self.suite.update(self._ecef(self.state.pos), self._ecef_vec(self.state.vel),
                                         np.zeros(3), 0.0, self.state.bank, self.state.gamma,
                                         self.state.heading, self.state.airspeed)
        self.prev_dist = self._dist()
        self.obs = self._observe()
        return self.obs

    def _ecef(self, enu):
        return self.anchor.to_ecef(enu)

    def _ecef_vec(self, enu):
        return self.anchor.vec_to_ecef(enu)

    def _dist(self):
        return float(np.linalg.norm(self.route[self.wp][:2] - self.state.pos[:2]))

    def step(self, action):
        c = self.cfg
        a = np.clip(np.asarray(action, dtype=float), -1.0, 1.0)
        bank_cmd = a[0] * self.ac.bank_max
        gamma_cmd = a[1] * self.ac.gamma_max
        throttle = 0.5 * (a[2] + 1.0)

        self.state = dyn.step(self.ac, self.state, bank_cmd, gamma_cmd, throttle, self.wind, c.dt)
        self.steps += 1
        t = self.steps * c.dt

        # sensors see the true motion; the filter only ever sees what they report
        a_ecef = self._ecef_vec(self.state.accel)
        self.reading = self.suite.update(self._ecef(self.state.pos), self._ecef_vec(self.state.vel),
                                         a_ecef, t, self.state.bank, self.state.gamma,
                                         self.state.heading, self.state.airspeed)
        # dead reckoning: the IMU on its own, bias and all
        self.dr_vel = self.dr_vel + self.anchor.R.T @ self.reading.imu_accel * c.dt
        self.dr_pos = self.dr_pos + self.dr_vel * c.dt

        # --- reward ---
        dist = self._dist()
        progress = (self.prev_dist - dist) / max(c.start_speed * c.dt, 1e-6)
        self.prev_dist = dist
        alt_err = self.state.pos[2] - c.target_alt
        r = c.w_progress * float(np.clip(progress, -2.0, 2.0))
        r -= c.w_alt * float(min((alt_err / 300.0) ** 2, 4.0))
        r -= c.w_smooth * float(np.sum((a - self.prev_action) ** 2))
        r -= c.w_bank * float((self.state.bank / self.ac.bank_max) ** 2)

        if dist < c.capture_m:
            r += c.capture_bonus
            self.captured += 1
            self.wp = min(self.wp + 1, len(self.route) - 1)
            self.prev_dist = self._dist()

        crashed = (self.state.pos[2] <= 0.0 or self.state.airspeed <= self.ac.v_min
                   or abs(self.state.bank) > np.radians(60.0)
                   or np.linalg.norm(self.state.pos[:2]) > 250_000.0)
        if crashed:
            r -= c.crash_penalty
        done = crashed or self.captured >= len(self.route)
        truncated = self.steps >= c.max_steps and not done

        r *= c.reward_scale
        self.prev_action = a
        self.obs = self._observe()
        info = {
            "pos": self.state.pos.copy(),
            "est_pos": self.anchor.to_enu(self.reading.est_pos),
            "nav_error": float(np.linalg.norm(self.reading.est_pos - self._ecef(self.state.pos))),
            "alt_error": float(alt_err),
            "captured": self.captured,
            "crashed": bool(crashed),
            "n_sats": self.reading.n_sats,
            "n_towers": self.reading.n_towers,
            "airspeed": self.state.airspeed,
        }
        return self.obs, r, bool(done), bool(truncated), info

    def obs_labels(self) -> list[str]:
        """A name per observation channel, in the same order as _observe() builds them.

        The RL Autopilot page shows these live, so they have to stay in step.
        """
        names = ["WP ahead", "WP right", "WP up", "Next ahead", "Next right", "Next up",
                 "Ground speed", "Vertical speed", "Airspeed", "Bank", "Pitch",
                 "IMU x", "IMU y", "IMU z", "Altitude error",
                 "Last bank cmd", "Last pitch cmd", "Last throttle"]
        if self.cfg.obs_mode == "fused":
            names.append("Fix quality")
        if self.cfg.obs_mode == "raw":
            n = self.cfg.sensors
            names += [f"Tower {i + 1} range" for i in range(n.max_towers)]
            names += [f"Tower {i + 1} heard" for i in range(n.max_towers)]
            names += [f"Sat {i + 1} Doppler" for i in range(n.max_sats)]
            names += [f"Sat {i + 1} in view" for i in range(n.max_sats)]
            names += [f"Sat {i + 1} dir {ax}" for i in range(n.max_sats) for ax in "xyz"]
        return names

    def obs_sources(self) -> list[str]:
        """Where each observation channel comes from, in plain words.

        Recorded with every flight so the UI can explain itself; keeping it next to
        _observe() is the only way it stays true.
        """
        fused = self.cfg.obs_mode == "fused"
        where = ("the navigation filter's position estimate (IMU + ground towers + Iridium Doppler)"
                 if fused else "dead reckoning: the IMU integrated with nothing to correct it"
                 if self.cfg.obs_mode == "raw" else "the true position (this mode is a baseline, not realistic)")
        leg = ("Flight-plan waypoint minus " + where + ", rotated into the aeroplane's own frame. "
               "Any error in the fix is an error in this number.")
        vel = ("the navigation filter's velocity estimate" if fused else
               "dead-reckoned velocity" if self.cfg.obs_mode == "raw" else "true velocity")
        src = [
            f"{leg} Distance along the current ground track, scaled by 10 km.",
            f"{leg} Distance across the track, positive to the right.",
            f"{leg} Height difference to the waypoint.",
            f"{leg} The waypoint AFTER this one, so a turn can be started early.",
            f"{leg} Across-track distance to the next-but-one waypoint.",
            f"{leg} Height difference to the next-but-one waypoint.",
            f"Horizontal speed over the ground, from {vel}. It differs from airspeed by the wind, "
            "which is never given to the agent directly.",
            f"Climb or descent rate, from {vel}.",
            "Pitot tube: speed through the air, with about 1.5 m/s of noise. A direct sensor - "
            "no filter involved.",
            "AHRS roll angle, with 0.4 deg of noise and a fixed bias drawn per flight. "
            "Scaled by the 45 deg bank limit.",
            "AHRS pitch (flight path angle), same errors, scaled by the 8 deg limit.",
            "Accelerometer, ECEF x axis: a constant bias plus white noise. This is the signal "
            "whose bias makes dead reckoning run away.",
            "Accelerometer, ECEF y axis.",
            "Accelerometer, ECEF z axis.",
            f"Altitude from {where}, minus the 9,000 m target. Wrong by however wrong the fix is.",
            "Its own bank command from the previous tick - the reward penalises thrashing.",
            "Its own pitch command from the previous tick.",
            "Its own throttle setting from the previous tick.",
        ]
        if fused:
            src.append("The filter's own position uncertainty (log10 of 1 sigma, over 3). It rises "
                       "when towers drop out of range and falls when the geometry is good.")
        if self.cfg.obs_mode == "raw":
            n = self.cfg.sensors
            src += [f"Measured distance to ground tower {i + 1}, about 30 m of noise, two-way ranging "
                    "so there is no clock error in it." for i in range(n.max_towers)]
            src += [f"1 when tower {i + 1} was in range and above its own horizon this tick, else 0."
                    for i in range(n.max_towers)]
            src += [f"Doppler shift of satellite {i + 1}: how fast the distance to it is changing, "
                    "plus the receiver's own oscillator error." for i in range(n.max_sats)]
            src += [f"1 when satellite {i + 1} was above the 10 deg elevation mask." for i in range(n.max_sats)]
            src += [f"Direction to satellite {i + 1}, ECEF {ax} component of a unit vector."
                    for i in range(n.max_sats) for ax in "xyz"]
        return src

    def obs_groups(self) -> list[dict]:
        """How the observation splits up by where each number comes from.

        The vector's own order is whatever was convenient to build; this is the order it
        makes sense to *read* in, which is what the RL Autopilot page displays.
        """
        fused = self.cfg.obs_mode == "fused"
        nav = ("Where it thinks it is" if fused else
               "Where it thinks it is (dead reckoning)" if self.cfg.obs_mode == "raw" else
               "Where it is")
        note = ("the navigation filter's answer - this is where the towers and satellites "
                "actually enter" if fused else
                "the IMU integrated on its own, with no filter to correct it" if self.cfg.obs_mode == "raw"
                else "perfect knowledge, for comparison only")
        groups = [
            {"title": "The aeroplane itself", "note": "measured directly, no filter involved",
             "idx": [8, 9, 10, 11, 12, 13]},
            {"title": nav, "note": note, "idx": [0, 1, 2, 3, 4, 5, 6, 7, 14] + ([18] if fused else [])},
            {"title": "What it just did", "note": "its own last command, so it can be smooth",
             "idx": [15, 16, 17]},
        ]
        if self.cfg.obs_mode == "raw":
            n = self.cfg.sensors
            base = 18
            t, s_ = n.max_towers, n.max_sats
            groups.append({"title": "Ground towers, unfiltered", "note": "a distance to each one in range",
                           "idx": list(range(base, base + 2 * t))})
            groups.append({"title": "Satellites, unfiltered",
                           "note": "Doppler shift and the direction to each one in view",
                           "idx": list(range(base + 2 * t, base + 2 * t + 2 * s_ + 3 * s_))})
        return groups

    # ---- what the agent is allowed to know ------------------------------------
    def _observe(self, first: bool = False) -> np.ndarray:
        c = self.cfg
        rd = self.reading
        if c.obs_mode == "truth":
            here, vel, heading = self.state.pos, self.state.vel, self.state.heading
        elif c.obs_mode == "fused":
            here = self.anchor.to_enu(rd.est_pos)
            vel = self.anchor.R.T @ rd.est_vel
            heading = float(np.arctan2(vel[0], vel[1]))
        else:  # raw: nothing but the IMU, integrated, plus the radios below
            here, vel = self.dr_pos, self.dr_vel
            heading = float(np.arctan2(vel[0], vel[1]))

        def leg(target):
            """Vector to a waypoint, in the frame the aeroplane thinks it is flying in."""
            d = target - here
            ch, sh = np.cos(heading), np.sin(heading)
            fwd = d[0] * sh + d[1] * ch
            right = d[0] * ch - d[1] * sh
            return np.array([fwd, right, d[2]]) / 10_000.0

        nxt = self.route[min(self.wp + 1, len(self.route) - 1)]
        obs = [
            leg(self.route[self.wp]),
            leg(nxt),
            [np.linalg.norm(vel[:2]) / 100.0, vel[2] / 10.0],
            [rd.airspeed / 100.0],
            [rd.bank / self.ac.bank_max, rd.gamma / self.ac.gamma_max],
            rd.imu_accel / 5.0,
            [(here[2] - c.target_alt) / 300.0],
            self.prev_action,
        ]
        if c.obs_mode == "fused":
            obs.append([np.log10(max(rd.pos_sigma, 1.0)) / 3.0])   # how much to trust the fix
        if c.obs_mode == "raw":
            # the radios, unfiltered: ranges to the towers and Doppler from the satellites
            obs += [rd.tower_range / 100_000.0, rd.tower_valid,
                    rd.sat_doppler / 30.0, rd.sat_valid, rd.sat_dirs.ravel()]
        return np.concatenate([np.asarray(o, dtype=np.float64).ravel() for o in obs])
