#!/usr/bin/env python3
"""
LEO (Iridium) Doppler analysis for navigation: quantitative measurements.

Run this next to nav_sim.py (it reuses its satellite loader, flight model and filter).

    python3 leo_analysis.py                     # sections 1-3 (fast) + plot
    python3 leo_analysis.py --sweep             # also run the Monte Carlo sweep (slower)
    python3 leo_analysis.py --sweep --sweep-runs 5 --sweep-orbit 0 500 1000 2000
    python3 leo_analysis.py --synthetic         # force the built-in fake constellation
    python3 leo_analysis.py --no-plot           # numbers only
    python3 leo_analysis.py --show              # open the plot window (needs a display)

Sections
--------
 1. COVERAGE      How many Iridium satellites an aircraft at cruise altitude can hear,
                  by latitude: mean count, % of time with >=1/2/3/4 satellites,
                  longest gap with none, typical pass length.
 2. DOPPLER       Size of the Doppler shift, how fast it changes, and how sensitive it is to
                  aircraft velocity, receiver position, satellite orbit (TLE) error and the
                  receiver clock.
 3. PRECISION     How well Doppler ALONE could fix a position over a window of 1-10 minutes
                  if the aircraft velocity were known, and how much a TLE error of a given
                  size moves that fix (the error floor from imperfect orbit data).
 4. SWEEP         (--sweep) Full IMU + Doppler + baro EKF runs from nav_sim.py at random
                  locations/headings, for several IMU grades and orbit errors, with and
                  without Doppler. Gives medians, 90th percentiles and success rates.

Outputs: tables on screen, leo_analysis_results.png, and (with --sweep) leo_analysis_sweep.csv.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import math
import os
import sys
import time
from dataclasses import replace

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    import nav_sim as ns
except ImportError:
    sys.exit("leo_analysis.py needs nav_sim.py in the same folder.")

_announced = [False]


def hdr(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def get_sats(cfg, n_ep, dt, args):
    """Load the constellation. Only the first call prints messages."""
    if not _announced[0]:
        _announced[0] = True
        return ns.load_constellation(cfg, n_ep, dt, args.tle, args.synthetic, args.offline)
    with contextlib.redirect_stdout(io.StringIO()):
        return ns.load_constellation(cfg, n_ep, dt, args.tle, args.synthetic, args.offline)


def run_lengths(mask):
    m = np.asarray(mask, dtype=np.int8)
    d = np.diff(np.concatenate([[0], m, [0]]))
    return np.where(d == -1)[0] - np.where(d == 1)[0]


def pct(a, q):
    a = np.asarray(a, float)
    a = a[np.isfinite(a)]
    return float(np.percentile(a, q)) if a.size else float("nan")


def dist(m):
    if m is None or not np.isfinite(m):
        return "n/a"
    return f"{m / 1000:.1f} km" if m >= 10_000 else f"{m:.0f} m"


# ----------------------------------------------------------------------------
# 1. Coverage
# ----------------------------------------------------------------------------
def coverage(cfg, args):
    hdr(f"1. COVERAGE: how many Iridium satellites can the aircraft hear?  "
        f"(altitude {cfg.alt_m / 1000:.0f} km, elevation mask {cfg.min_elev_deg:g} deg, {args.hours:g} h)")
    step = 30.0
    n_ep = int(args.hours * 3600 / step) + 1
    SP, SV, names, info = get_sats(cfg, n_ep, step, args)
    print(f"Satellite source: {info['source']}\n")
    print(f"{'lat':>5} | {'mean #':>6} | {'>=1':>6} {'>=2':>6} {'>=3':>6} {'>=4':>6} | {'longest gap':>12} | {'median pass':>11}")
    print("-" * 78)
    rows = []
    lon = math.radians(cfg.start_lon)
    for lat_deg in args.lats:
        lat = math.radians(lat_deg)
        p = ns.geodetic_to_ecef(lat, lon, cfg.alt_m)
        up = ns.up_vector(lat, lon)
        rel = SP - p
        elev = np.degrees(np.arcsin((rel @ up) / np.linalg.norm(rel, axis=2)))
        vis = elev > cfg.min_elev_deg
        nv = vis.sum(axis=0)
        frac = [100.0 * float(np.mean(nv >= j)) for j in (1, 2, 3, 4)]
        gaps = run_lengths(nv == 0) * step
        passes = np.concatenate([run_lengths(v) for v in vis]) * step
        longest = float(gaps.max()) if gaps.size else 0.0
        medp = float(np.median(passes) / 60) if passes.size else float("nan")
        rows.append(dict(lat=lat_deg, mean=float(nv.mean()), frac=frac, gap=longest, med_pass=medp))
        print(f"{lat_deg:>4.0f}° | {nv.mean():>6.2f} | {frac[0]:>5.0f}% {frac[1]:>5.0f}% {frac[2]:>5.0f}% {frac[3]:>5.0f}% | "
              f"{longest / 60:>9.1f} min | {medp:>8.1f} min")
    return rows


# ----------------------------------------------------------------------------
# 2. Doppler signal
# ----------------------------------------------------------------------------
def build_scene(cfg, args):
    """Truth flight (1 s steps) plus constellation geometry over the same time."""
    n = int(args.analysis_minutes * 60)
    SP, SV, names, info = get_sats(cfg, n + 1, 1.0, args)
    tr = ns.make_truth(cfg, n, 1.0)
    p, v = tr["p"], tr["v"]
    up = ns.up_vector(tr["lat"], tr["lon"])
    rel = SP - p[None]
    rho = np.linalg.norm(rel, axis=2)
    u = rel / rho[..., None]
    elev = np.degrees(np.arcsin(np.einsum("snj,nj->sn", rel, up) / rho))
    vis = elev > cfg.min_elev_deg
    k = cfg.fc_hz / ns.C_LIGHT
    rr = np.sum((SV - v[None]) * u, axis=2)
    return dict(t=np.arange(n + 1) * 1.0, p=p, v=v, lat=tr["lat"], lon=tr["lon"], up=up, SP=SP, SV=SV,
                names=names, info=info, rho=rho, u=u, elev=elev, vis=vis, k=k, rr=rr, z=-k * rr)


def doppler_stats(sc, cfg, args):
    hdr(f"2. DOPPLER SIGNAL: size, speed and sensitivity  "
        f"(flight: {cfg.speed:.0f} m/s, {args.analysis_minutes:g} min)")
    k, z, vis = sc["k"], sc["z"], sc["vis"]
    if not vis.any():
        print("No satellite was ever visible in this scenario.")
        return None
    zv = z[vis]
    print(f"Carrier {cfg.fc_hz / 1e6:.0f} MHz, so 1 m/s along the line of sight = {k:.2f} Hz.")
    print(f"Your Doppler noise of {cfg.doppler_sigma_hz:g} Hz is about {cfg.doppler_sigma_hz / k:.2f} m/s.\n")
    print("Signal (satellite + aircraft motion, before clock error):")
    print(f"  range over all visible passes     : {zv.min() / 1e3:+.1f} .. {zv.max() / 1e3:+.1f} kHz")

    rate = np.full_like(z, np.nan)
    rate[:, 1:-1] = (z[:, 2:] - z[:, :-2]) / 2.0
    ok = np.zeros_like(vis)
    ok[:, 1:-1] = vis[:, :-2] & vis[:, 1:-1] & vis[:, 2:]
    r = np.abs(rate[ok])
    if r.size:
        print(f"  Doppler rate |dz/dt|              : median {np.median(r):.0f} Hz/s, 95th pct {pct(r, 95):.0f}, max {r.max():.0f}")
    own = np.abs(k * np.einsum("snj,nj->sn", sc["u"], sc["v"]))[vis]
    print(f"  aircraft's own motion contributes : median {np.median(own):.0f} Hz, max {own.max():.0f} Hz "
          f"({own.max() / k:.0f} m/s)")

    dv = sc["SV"] - sc["v"][None]
    dvp = dv - sc["u"] * np.sum(dv * sc["u"], axis=2)[..., None]
    sens = (k * np.linalg.norm(dvp, axis=2) / sc["rho"] * 1000.0)[vis]            # Hz per km of receiver position
    print("\nSensitivity to the aircraft's position (how much Doppler changes per km of position error):")
    print(f"  median {np.median(sens):.1f} Hz/km, 95th pct {pct(sens, 95):.1f} Hz/km")
    print(f"  so {cfg.doppler_sigma_hz:g} Hz of noise is roughly {1000 * cfg.doppler_sigma_hz / np.median(sens):.0f} m "
          f"of position uncertainty per measurement (median geometry)")

    SP, SV = sc["SP"], sc["SV"]
    acc = np.gradient(SV, 1.0, axis=1)
    print("\nImpact of satellite orbit (TLE) error, shifting every satellite along its track:")
    print(f"  {'along-track error':>18} | {'|Doppler error| median':>22} {'95th pct':>9} {'max':>7} | worst case as velocity")
    orb_rows = []
    for orb in (250, 500, 1000, 2000):
        tau = orb / 7500.0
        SPs = SP + SV * tau + 0.5 * acc * tau ** 2
        SVs = SV + acc * tau
        d2 = SPs - sc["p"][None]
        u2 = d2 / np.linalg.norm(d2, axis=2)[..., None]
        z2 = -k * np.sum((SVs - sc["v"][None]) * u2, axis=2)
        dz = np.abs(z2 - z)[vis]
        orb_rows.append((orb, float(np.median(dz)), pct(dz, 95), float(dz.max())))
        print(f"  {orb:>16} m | {np.median(dz):>19.1f} Hz {pct(dz, 95):>7.1f} Hz {dz.max():>5.0f} Hz | {dz.max() / k:.1f} m/s")

    print("\nReceiver oscillator (this is why the clock offset must be estimated):")
    for ppm in (0.1, 0.5, 1.0, 2.0):
        hz = cfg.fc_hz * ppm * 1e-6
        print(f"  {ppm:>4.1f} ppm error = {hz:>6.0f} Hz = {hz / k:>5.0f} m/s of fake velocity")
    return dict(rate=r, orb=orb_rows, sens=sens)


# ----------------------------------------------------------------------------
# 3. Doppler-only position precision and the orbit-error floor
# ----------------------------------------------------------------------------
def precision(sc, cfg, args):
    hdr("3. HOW WELL CAN DOPPLER ALONE PIN DOWN POSITION?")
    print("Assumes the aircraft velocity is known (e.g. from the IMU) so only a constant position offset and the")
    print(f"clock offset are unknown inside each window. Up to {cfg.max_sats} satellites per second, "
          f"Doppler noise {cfg.doppler_sigma_hz:g} Hz,")
    print(f"barometer {'on (' + str(cfg.baro_sigma) + ' m)' if cfg.baro_sigma > 0 else 'off'}. "
          "A window counts as 'fixed' only if the geometry actually pins position down.\n")
    n = len(sc["t"])
    k, sig, sig_b = sc["k"], cfg.doppler_sigma_hz, cfg.baro_sigma
    vis, SP, SV, v, p = sc["vis"], sc["SP"], sc["SV"], sc["v"], sc["p"]
    elev_m = np.where(vis, sc["elev"], -np.inf)
    I3 = np.eye(3)
    E, Sx, A = [], [], []
    for e in range(n):
        nv = int(vis[:, e].sum())
        if not nv:
            continue
        for s in np.argsort(-elev_m[:, e])[:min(cfg.max_sats, nv)]:
            u = sc["u"][s, e]
            hp = k * (I3 - np.outer(u, u)) @ (SV[s, e] - v[e]) / sc["rho"][s, e]
            E.append(e)
            Sx.append(s)
            A.append([hp[0], hp[1], hp[2], 1.0])
    if len(E) < 20:
        print("Too few Doppler measurements in this scenario to analyse.")
        return None
    E, Sx, A = np.array(E), np.array(Sx), np.array(A)

    Jb = None
    if sig_b > 0:
        ub = np.concatenate([sc["up"], np.zeros((n, 1))], axis=1)
        Jb = np.einsum("ni,nj->nij", ub, ub) / sig_b ** 2

    def solve(start, L):
        sel = np.where((E >= start) & (E < start + L))[0]
        if sel.size < 8:
            return None
        Aw = A[sel] / sig
        J = Aw.T @ Aw
        if Jb is not None:
            J = J + Jb[start:start + L].sum(axis=0)
        d = np.sqrt(np.diag(J))
        if np.any(d <= 0) or np.linalg.cond(J / np.outer(d, d)) > 1e7:
            return None
        return sel, Aw, np.linalg.inv(J)

    def horiz(cov, mid):
        Cne = ns.dcm_ned_to_ecef(sc["lat"][mid], sc["lon"][mid])
        ned = Cne.T @ cov[:3, :3] @ Cne
        return math.sqrt(ned[0, 0] + ned[1, 1])

    print(f"{'window':>8} | {'windows with a fix':>20} | horizontal 1-sigma from noise alone (median / 90th pct)")
    print("-" * 78)
    by_len = {}
    for L in (60, 180, 300, 600):
        if L >= n:
            continue
        hs, total = [], 0
        for st in range(0, n - L, 60):
            total += 1
            r = solve(st, L)
            if r is not None:
                hs.append(horiz(r[2], st + L // 2))
        by_len[L] = (len(hs), total, hs)
        fixed = f"{len(hs)}/{total} ({100 * len(hs) / max(total, 1):.0f}%)"
        stats = f"{dist(pct(hs, 50))} / {dist(pct(hs, 90))}" if hs else "no fix"
        print(f"{L // 60:>5} min | {fixed:>20} | {stats}")

    print("\nOrbit-error floor: position error pushed into the fix by wrong satellite orbits (300 s window).")
    L = 300 if n > 600 else max(60, n // 2)
    sols = []
    for st in range(0, n - L, 60):
        r = solve(st, L)
        if r is not None:
            sols.append((st, r))
    orb_stats = []
    if not sols:
        print("  no windows with a valid fix, so no floor can be computed.")
    else:
        acc = np.gradient(SV, 1.0, axis=1)
        z_true = -k * sc["rr"][Sx, E]
        rng = np.random.default_rng(args.seed)
        print(f"  {'orbit error (1-sigma)':>22} | horizontal position error caused (median / 90th pct)")
        for orb in args.orbit_errs:
            errs = []
            for _ in range(args.mc):
                tau = rng.normal(0, orb / 7500.0, SP.shape[0])[Sx][:, None]
                SPr = SP[Sx, E] + SV[Sx, E] * tau + 0.5 * acc[Sx, E] * tau ** 2
                SVr = SV[Sx, E] + acc[Sx, E] * tau
                dd = SPr - p[E]
                u2 = dd / np.linalg.norm(dd, axis=1)[:, None]
                dz = -k * np.sum((SVr - v[E]) * u2, axis=1) - z_true
                for st, (sel, Aw, cov) in sols:
                    theta = cov @ (Aw.T @ (dz[sel] / sig))
                    mid = st + L // 2
                    Cne = ns.dcm_ned_to_ecef(sc["lat"][mid], sc["lon"][mid])
                    ned = Cne.T @ theta[:3]
                    errs.append(math.hypot(ned[0], ned[1]))
            orb_stats.append((orb, pct(errs, 50), pct(errs, 90)))
            print(f"  {orb:>20.0f} m | {dist(pct(errs, 50))} / {dist(pct(errs, 90))}")
        print(f"  ({len(sols)} windows x {args.mc} random orbit-error draws per row)")
    return dict(by_len=by_len, orb=orb_stats, L=L)


# ----------------------------------------------------------------------------
# 4. Monte Carlo sweep with the full filter
# ----------------------------------------------------------------------------
def sweep(cfg, args):
    hdr(f"4. MONTE CARLO SWEEP: full IMU + Doppler + baro filter, {args.sweep_runs} random flights per row, "
        f"{cfg.minutes:g} min GPS outage")
    ratio = max(1, int(round(cfg.meas_dt * cfg.imu_rate_hz)))
    sats = get_sats(cfg, cfg.n_epochs, ratio / cfg.imu_rate_hz, args)
    rng = np.random.default_rng(args.seed)
    locs = [(rng.uniform(25, 65), rng.uniform(-130, -40), rng.uniform(0, 360)) for _ in range(args.sweep_runs)]
    modes = []
    for imu in args.sweep_imu:
        modes.append((imu, "baro only", None))
        for orb in args.sweep_orbit:
            modes.append((imu, "Doppler+baro", orb))

    print(f"{'IMU':>9} | {'aiding':>12} | {'orbit err':>9} | {'median final':>12} {'90th pct':>9} {'worst':>9} | "
          f"{'IMU only':>9} | {'<2 km':>6} {'<10 km':>7}")
    print("-" * 100)
    results, csv_rows = {}, []
    for imu, aid, orb in modes:
        finals, drs, means, cons = [], [], [], []
        for i, (lat, lon, hdg) in enumerate(locs):
            c = replace(cfg, imu=imu, use_doppler=(aid != "baro only"), orbit_err_m=(orb or 0.0),
                        seed=args.seed + i, start_lat=lat, start_lon=lon, heading_deg=hdg)
            r = ns.simulate(c, sats, verbose=False)
            half = len(r.t) // 2
            finals.append(float(r.err_fused[-1]))
            drs.append(float(r.err_dr[-1]))
            means.append(float(r.err_fused[half:].mean()))
            cons.append(float(np.mean(r.err_fused[1:] <= 3 * r.sigma_pos[1:])))
            csv_rows.append((imu, aid, "" if orb is None else orb, args.seed + i, lat, lon, hdg,
                             finals[-1], drs[-1], means[-1], cons[-1]))
        f = np.array(finals)
        results[(imu, aid, orb)] = dict(finals=f, dr=np.array(drs), means=np.array(means), cons=np.array(cons))
        orb_s = "-" if orb is None else f"{orb:.0f} m"
        print(f"{imu:>9} | {aid:>12} | {orb_s:>9} | {dist(np.median(f)):>12} {dist(pct(f, 90)):>9} {dist(f.max()):>9} | "
              f"{dist(np.median(drs)):>9} | {100 * np.mean(f < 2000):>5.0f}% {100 * np.mean(f < 10000):>6.0f}%", flush=True)

    print("\nWhat Doppler adds (median final error, same flights):")
    for imu in args.sweep_imu:
        base = results[(imu, "baro only", None)]["finals"]
        for orb in args.sweep_orbit:
            d = results[(imu, "Doppler+baro", orb)]["finals"]
            print(f"  {imu:>9}, orbit error {orb:>5.0f} m: baro only {dist(np.median(base))} -> "
                  f"with Doppler {dist(np.median(d))}  (x{np.median(base) / max(np.median(d), 1e-9):.1f} better)")
    path = f"{args.out}_sweep.csv"
    with open(path, "w") as fh:
        fh.write("imu,aiding,orbit_err_m,seed,lat,lon,heading,final_err_fused_m,final_err_imu_only_m,"
                 "mean_err_second_half_m,fraction_inside_3sigma\n")
        for row in csv_rows:
            fh.write(",".join(str(x) if not isinstance(x, float) else f"{x:.4f}" for x in row) + "\n")
    print(f"\nSaved per-run results: {path}")
    return results


def save_summary(cov_rows, dop, prec, args):
    """Write the coverage and orbit-error tables to CSV files so they can be plotted or cited."""
    path = f"{args.out}_coverage.csv"
    with open(path, "w") as fh:
        fh.write("lat_deg,mean_sats,pct_1plus,pct_2plus,pct_3plus,pct_4plus,longest_gap_min,median_pass_min\n")
        for r in cov_rows:
            fr = [100 * x for x in r["frac"]]
            fh.write(f"{r['lat']:.0f},{r['mean']:.3f},{fr[0]:.1f},{fr[1]:.1f},{fr[2]:.1f},{fr[3]:.1f},"
                     f"{r['gap']:.2f},{r['med_pass']:.2f}\n")
    print(f"\nSaved coverage table: {path}")
    path = f"{args.out}_orbit_error.csv"
    with open(path, "w") as fh:
        fh.write("table,orbit_err_m,median,p90_or_p95,max\n")
        for o, med, p95, mx in dop["orb"]:
            fh.write(f"doppler_error_hz,{o:.0f},{med:.3f},{p95:.3f},{mx:.3f}\n")
        for o, med, p90 in prec["orb"]:
            fh.write(f"position_error_m,{o:.0f},{med:.1f},{p90:.1f},\n")
    print(f"Saved orbit-error table: {path}")


# ----------------------------------------------------------------------------
# Plots
# ----------------------------------------------------------------------------
def make_plots(cov_rows, sc, dop, prec, sweep_res, args):
    try:
        import matplotlib
        if not args.show:
            matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("\nmatplotlib is not installed, so no plot was made. (pip install matplotlib)")
        return
    fig, ax = plt.subplots(2, 3, figsize=(18, 9))

    a = ax[0, 0]
    x = np.arange(len(cov_rows))
    for j, label in enumerate((">=1 satellite", ">=2", ">=3")):
        a.bar(x + (j - 1) * 0.27, [r["frac"][j] for r in cov_rows], 0.27, label=label)
    a.set_xticks(x)
    a.set_xticklabels([f"{r['lat']:.0f}°" for r in cov_rows])
    a.set(title="Coverage: % of time satellites are in view", xlabel="latitude", ylabel="% of time", ylim=(0, 105))
    a.legend(); a.grid(alpha=0.3, axis="y")

    a = ax[0, 1]
    tmax = min(1800, len(sc["t"]) - 1)
    for s in range(sc["z"].shape[0]):
        m = sc["vis"][s, :tmax + 1]
        if m.any():
            a.plot(sc["t"][:tmax + 1] / 60, np.where(m, sc["z"][s, :tmax + 1], np.nan) / 1000, lw=1.5)
    a.set(title="Doppler of visible satellites (clock error excluded)", xlabel="time [min]", ylabel="Doppler [kHz]")
    a.grid(alpha=0.3)

    a = ax[0, 2]
    if dop is not None and dop["rate"].size:
        a.hist(dop["rate"], bins=40, color="tab:purple")
        a.set(title="How fast the Doppler changes", xlabel="|Doppler rate| [Hz/s]", ylabel="samples")
    a.grid(alpha=0.3)

    a = ax[1, 0]
    if prec is not None and prec["by_len"]:
        Ls = [L for L, (nf, nt, hs) in prec["by_len"].items() if hs]
        a.semilogy([L / 60 for L in Ls], [pct(prec["by_len"][L][2], 50) for L in Ls], "o-", label="median")
        a.semilogy([L / 60 for L in Ls], [pct(prec["by_len"][L][2], 90) for L in Ls], "s--", label="90th percentile")
        for L in Ls:
            nf, nt, _ = prec["by_len"][L]
            a.annotate(f"{100 * nf / max(nt, 1):.0f}% fixed", (L / 60, pct(prec["by_len"][L][2], 50)),
                       textcoords="offset points", xytext=(5, 8), fontsize=8)
        a.set(title="Doppler-only fix precision (noise only)", xlabel="window length [min]", ylabel="horizontal 1-sigma [m]")
        a.legend()
    a.grid(alpha=0.3, which="both")

    a = ax[1, 1]
    if prec is not None and prec["orb"]:
        o = [r[0] for r in prec["orb"]]
        a.plot(o, [r[1] for r in prec["orb"]], "o-", label="median")
        a.plot(o, [r[2] for r in prec["orb"]], "s--", label="90th percentile")
        a.set(title="Position error caused by satellite orbit error", xlabel="along-track orbit error [m]",
              ylabel="horizontal position error [m]")
        a.legend()
    a.grid(alpha=0.3)

    a = ax[1, 2]
    if sweep_res:
        labels, meds, p90 = [], [], []
        for (imu, aid, orb), r in sweep_res.items():
            labels.append(f"{imu}\n{'baro' if aid == 'baro only' else f'Dop {orb:.0f}m'}")
            meds.append(np.median(r["finals"]))
            p90.append(pct(r["finals"], 90))
        xs = np.arange(len(labels))
        a.bar(xs - 0.2, meds, 0.4, label="median")
        a.bar(xs + 0.2, p90, 0.4, label="90th pct")
        a.set_yscale("log")
        a.set_xticks(xs)
        a.set_xticklabels(labels, fontsize=7)
        a.set(title="Full filter: final error after the outage", ylabel="error [m]")
        a.legend()
    else:
        a.axis("off")
        a.text(0.5, 0.5, "Run with --sweep to fill this panel\n(Monte Carlo of the full filter)", ha="center", va="center")
    a.grid(alpha=0.3, axis="y")

    plt.tight_layout()
    path = f"{args.out}_results.png"
    plt.savefig(path, dpi=120)
    print(f"\nSaved plot: {path}")
    if args.show:
        plt.show()


def main(argv=None):
    d = ns.Config()
    ap = argparse.ArgumentParser(description="Quantitative analysis of LEO Doppler for navigation")
    ap.add_argument("--hours", type=float, default=3.0, help="coverage analysis length [h]")
    ap.add_argument("--lats", type=float, nargs="+", default=[0, 30, 45, 60, 75], help="latitudes for coverage")
    ap.add_argument("--analysis-minutes", type=float, default=60.0, help="flight length for sections 2-3")
    ap.add_argument("--minutes", type=float, default=d.minutes, help="GPS outage length in the sweep")
    ap.add_argument("--doppler-sigma", type=float, default=d.doppler_sigma_hz)
    ap.add_argument("--max-sats", type=int, default=d.max_sats)
    ap.add_argument("--min-elev", type=float, default=d.min_elev_deg)
    ap.add_argument("--baro-sigma", type=float, default=d.baro_sigma, help="0 = no barometer")
    ap.add_argument("--orbit-errs", type=float, nargs="+", default=[250, 500, 1000, 2000],
                    help="orbit errors to test in section 3 [m]")
    ap.add_argument("--mc", type=int, default=20, help="random orbit-error draws per row in section 3")
    ap.add_argument("--sweep", action="store_true", help="run the Monte Carlo sweep (section 4)")
    ap.add_argument("--sweep-runs", type=int, default=3, help="random flights per sweep row")
    ap.add_argument("--sweep-imu", nargs="+", default=["mpu6050", "consumer", "tactical"],
                    choices=sorted(ns.IMU_PRESETS))
    ap.add_argument("--sweep-orbit", type=float, nargs="+", default=[0, 1000], help="orbit errors in sweep [m]")
    ap.add_argument("--seed", type=int, default=d.seed)
    ap.add_argument("--tle", help="TLE file (default: iridium_next.tle, downloaded if missing)")
    ap.add_argument("--synthetic", action="store_true", help="force the synthetic constellation")
    ap.add_argument("--offline", action="store_true", help="never try to download TLEs")
    ap.add_argument("--out", default="results/leo_analysis", help="output file prefix (folder is created)")
    ap.add_argument("--no-plot", action="store_true")
    ap.add_argument("--show", action="store_true", help="show the plot window (needs a display)")
    a = ap.parse_args(argv)

    cfg = replace(d, minutes=a.minutes, doppler_sigma_hz=a.doppler_sigma, max_sats=a.max_sats,
                  min_elev_deg=a.min_elev, baro_sigma=a.baro_sigma, seed=a.seed)
    started = time.perf_counter()
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    cov_rows = coverage(cfg, a)
    sc = build_scene(cfg, a)
    dop = doppler_stats(sc, cfg, a)
    prec = precision(sc, cfg, a)
    sweep_res = sweep(cfg, a) if a.sweep else None
    save_summary(cov_rows, dop, prec, a)
    if not a.no_plot:
        make_plots(cov_rows, sc, dop, prec, sweep_res, a)

    print("\nNotes: IMU error values are datasheet-style assumptions; Doppler is simulated (no RF); "
          "light-time and ionosphere are not modelled.")
    print(f"Finished in {time.perf_counter() - started:.0f} s.")


if __name__ == "__main__":
    main()
