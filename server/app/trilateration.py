"""Position solving from tower ranges.

`solve()` does weighted nonlinear least squares over (x, y, z).
`KalmanTracker` smooths successive fixes with a constant-velocity model.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import least_squares


@dataclass
class Range:
    name: str
    pos: tuple[float, float, float]
    distance: float
    sigma: float = 1.0  # expected 1-sigma error of this range, meters


@dataclass
class Fix:
    ok: bool
    position: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    error_radius: float = 0.0  # meters, ~1-sigma horizontal
    residual_rms: float = 0.0
    cov: list[list[float]] | None = None  # 3x3 position covariance (m^2)
    used: list[str] = field(default_factory=list)
    reason: str = ""


def range_sigma(distance: float, rssi_std: float | None, n: float) -> float:
    """RSSI ranging error grows with distance: dd/d = ln(10)/(10 n) * dRSSI."""
    noise_db = max(rssi_std if rssi_std is not None else 3.0, 1.0)
    return max(0.25, distance * np.log(10) / (10.0 * n) * noise_db)


def solve(
    ranges: list[Range],
    z_prior: float | None = None,
    z_prior_sigma: float = 1.0,
    z_min: float = 0.0,
    initial: np.ndarray | None = None,
) -> Fix:
    if len(ranges) < 3:
        return Fix(ok=False, used=[r.name for r in ranges], reason=f"need 3 towers, have {len(ranges)}")

    P = np.array([r.pos for r in ranges], dtype=float)
    d = np.array([r.distance for r in ranges], dtype=float)
    w = 1.0 / np.array([r.sigma for r in ranges], dtype=float)

    # Three towers leave a mirror ambiguity in z, so add a soft altitude prior.
    use_prior = len(ranges) == 3 or z_prior is not None
    zp = z_prior if z_prior is not None else float(np.mean(P[:, 2]))

    def residuals(p: np.ndarray) -> np.ndarray:
        r = w * (np.linalg.norm(P - p, axis=1) - d)
        if use_prior:
            r = np.append(r, (p[2] - zp) / z_prior_sigma)
        return r

    if initial is None:
        initial = P.mean(axis=0) + np.array([0.0, 0.0, 0.5])
    initial = np.asarray(initial, dtype=float).copy()
    initial[2] = max(initial[2], z_min + 1e-3)

    lower = np.array([-np.inf, -np.inf, z_min])
    upper = np.array([np.inf, np.inf, np.inf])
    res = least_squares(residuals, initial, bounds=(lower, upper), loss="soft_l1", f_scale=1.0)

    p = res.x
    geo = np.linalg.norm(P - p, axis=1) - d
    rms = float(np.sqrt(np.mean(geo**2)))
    try:
        J = res.jac
        cov = np.linalg.pinv(J.T @ J)
        cov = np.minimum(cov, 1e4)  # cap degenerate geometry
        err = float(np.sqrt(max(cov[0, 0] + cov[1, 1], 0.0)))
    except (np.linalg.LinAlgError, ValueError):
        cov = None
        err = float("nan")
    if not np.isfinite(err):
        err, cov = rms, None
    return Fix(
        ok=True,
        position=p.tolist(),
        error_radius=err,
        residual_rms=rms,
        used=[r.name for r in ranges],
        cov=None if cov is None else cov.tolist(),
    )


class KalmanTracker:
    """Constant-velocity Kalman filter on 3D position. State: [x y z vx vy vz]."""

    # accel_noise=2.0 was tuned on the simulator (1 m/s plane): it cuts frame-to-frame
    # jitter ~4x versus the raw solve without adding lag error.
    def __init__(self, accel_noise: float = 2.0, vel_tau: float = 2.0):
        self.q = accel_noise
        # Velocity decays toward 0 with this time constant, so poorly observed
        # axes (usually z) settle instead of coasting off on a stale velocity.
        self.vel_tau = vel_tau
        self.x: np.ndarray | None = None
        self.P = np.eye(6)
        self.t: float | None = None

    def reset(self) -> None:
        self.x = None
        self.t = None

    def update(self, pos: list[float], sigma: float, t: float, cov: list[list[float]] | None = None) -> np.ndarray:
        z = np.asarray(pos, dtype=float)
        if cov is not None:
            # Per-axis uncertainty from the solver: trust x/y, lean on the model for z.
            R = np.asarray(cov, dtype=float)
            R = 0.5 * (R + R.T) + np.eye(3) * 0.04
        else:
            R = np.eye(3) * max(sigma, 0.2) ** 2
        if self.x is None or self.t is None or t - self.t > 5.0:
            self.x = np.concatenate([z, np.zeros(3)])
            self.P = np.zeros((6, 6))
            self.P[:3, :3] = R
            self.P[3:, 3:] = np.eye(3) * 1.0
            self.t = t
            return self.x[:3].copy()

        dt = max(t - self.t, 1e-3)
        self.t = t
        F = np.eye(6)
        F[0:3, 3:6] = np.eye(3) * dt
        F[3:6, 3:6] = np.eye(3) * np.exp(-dt / self.vel_tau)
        G = np.vstack([np.eye(3) * 0.5 * dt**2, np.eye(3) * dt])
        Q = G @ G.T * self.q**2
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + Q

        H = np.hstack([np.eye(3), np.zeros((3, 3))])
        innov = z - H @ self.x
        S = H @ self.P @ H.T + R
        # Outlier gating: if a fix is wildly off (Mahalanobis^2 > 16), down-weight it.
        m2 = float(innov @ np.linalg.solve(S, innov))
        if m2 > 16.0:
            R = R * (m2 / 16.0)
            S = H @ self.P @ H.T + R
        K = self.P @ H.T @ np.linalg.inv(S)
        self.x = self.x + K @ innov
        self.P = (np.eye(6) - K @ H) @ self.P
        return self.x[:3].copy()

    @property
    def velocity(self) -> list[float]:
        return [0.0, 0.0, 0.0] if self.x is None else self.x[3:].tolist()
