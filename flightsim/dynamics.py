"""A 3-degree-of-freedom point-mass aircraft, flown in a local east/north/up frame.

Coordinated turns, a drag polar and a first-order lag on the controls - enough that
speed, bank and climb trade against each other the way they do in a real aeroplane,
and little enough that a PPO rollout costs microseconds.

What it does NOT model: sideslip, rotational dynamics, control-surface authority,
compressibility, ground effect, or anything about the airframe's structure. The agent
commands attitude, not stick and rudder.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

G = 9.80665


@dataclass
class Aircraft:
    """A small jet: roughly a light twin at cruise."""

    mass: float = 5000.0          # kg
    wing_area: float = 30.0       # m^2
    cd0: float = 0.025            # parasite drag
    k: float = 0.045              # induced drag factor
    cl_max: float = 1.4
    thrust_max: float = 26_000.0  # N at sea level, scaled by air density
    v_min: float = 55.0           # m/s, below this it stalls
    v_max: float = 290.0          # m/s, structural/mach limit
    bank_max: float = np.radians(45.0)
    gamma_max: float = np.radians(8.0)
    tau_bank: float = 1.5         # s, how quickly it rolls to a commanded bank
    tau_gamma: float = 2.0        # s, and pitches to a commanded flight path angle


@dataclass
class State:
    pos: np.ndarray = field(default_factory=lambda: np.zeros(3))  # east, north, up [m]
    vel: np.ndarray = field(default_factory=lambda: np.zeros(3))  # ground velocity [m/s]
    airspeed: float = 200.0
    heading: float = 0.0          # rad, 0 = north, + = clockwise (aviation convention)
    gamma: float = 0.0            # flight path angle [rad]
    bank: float = 0.0             # [rad]
    accel: np.ndarray = field(default_factory=lambda: np.zeros(3))  # d(ground velocity)/dt


def air_density(alt_m: float) -> float:
    """Exponential atmosphere - 1.5% off ISA up to ~11 km, which is plenty here."""
    return 1.225 * np.exp(-max(alt_m, 0.0) / 8500.0)


def air_velocity(v: float, heading: float, gamma: float) -> np.ndarray:
    """Airspeed vector in east/north/up."""
    c = v * np.cos(gamma)
    return np.array([c * np.sin(heading), c * np.cos(heading), v * np.sin(gamma)])


def step(ac: Aircraft, s: State, bank_cmd: float, gamma_cmd: float, throttle: float,
         wind: np.ndarray, dt: float) -> State:
    """Advance one tick. Commands are absolute (not rates); throttle is 0..1."""
    bank_cmd = float(np.clip(bank_cmd, -ac.bank_max, ac.bank_max))
    gamma_cmd = float(np.clip(gamma_cmd, -ac.gamma_max, ac.gamma_max))
    throttle = float(np.clip(throttle, 0.0, 1.0))

    # control lags: the aeroplane takes a second or two to get there
    bank = s.bank + (bank_cmd - s.bank) * min(dt / ac.tau_bank, 1.0)
    gamma = s.gamma + (gamma_cmd - s.gamma) * min(dt / ac.tau_gamma, 1.0)

    rho = air_density(s.pos[2])
    q = 0.5 * rho * s.airspeed ** 2                       # dynamic pressure
    # Lift balances weight through the bank angle; more bank costs more induced drag.
    cl = min(ac.mass * G / max(q * ac.wing_area, 1e-6) / max(np.cos(bank), 0.2), ac.cl_max)
    drag = q * ac.wing_area * (ac.cd0 + ac.k * cl ** 2)
    thrust = ac.thrust_max * throttle * (rho / 1.225)     # thrust falls off with altitude

    v = s.airspeed + ((thrust - drag) / ac.mass - G * np.sin(gamma)) * dt
    v = float(np.clip(v, 20.0, ac.v_max))
    heading = s.heading + G * np.tan(bank) / max(v, 1.0) * dt   # coordinated turn

    vel = air_velocity(v, heading, gamma) + wind                # the wind blows it sideways
    pos = s.pos + vel * dt
    return State(pos=pos, vel=vel, airspeed=v, heading=float(np.mod(heading, 2 * np.pi)),
                 gamma=gamma, bank=bank, accel=(vel - s.vel) / dt)


def trim_throttle(ac: Aircraft, v: float, alt: float) -> float:
    """Throttle that holds `v` in level flight at `alt` - a sane place to start."""
    rho = air_density(alt)
    q = 0.5 * rho * v ** 2
    cl = min(ac.mass * G / max(q * ac.wing_area, 1e-6), ac.cl_max)
    drag = q * ac.wing_area * (ac.cd0 + ac.k * cl ** 2)
    return float(np.clip(drag / max(ac.thrust_max * (rho / 1.225), 1e-6), 0.0, 1.0))
