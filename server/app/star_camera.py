"""Star camera: one photo of the night sky -> latitude and longitude.

The oldest navigation technique on the list, done by machine. A camera pointed straight
up photographs the stars; `tetra3` matches the pattern it sees against a catalogue and
reports where on the celestial sphere the lens was aimed (right ascension and
declination). Because the lens was aimed at the *zenith*, that direction is the point
directly overhead - and the point directly overhead has a fixed relationship to where
you are standing:

    latitude  = declination of the zenith
    longitude = right ascension of the zenith - Greenwich sidereal time at that instant

So the photo gives latitude for free, and longitude only once you know *when* the shutter
opened. Everything hinges on that clock: the Earth turns 0.0042 degrees of longitude per
second, so one second of timing error is about 460 m of position error at the equator.
That is the whole trade of celestial navigation - no transmitter to jam, no satellite to
lose, but you must know the time.

Two things live here:

  * `solve_image()` - bytes in (an upload, a stock sample, or a frame pulled off the Pi),
    a fix out. This is the real work.
  * `render_sky()` - a synthetic star field for a latitude/longitude we choose, so the
    page has images to solve with no camera and no night sky. Stars come from the same
    catalogue `tetra3` matches against, projected through a pinhole camera, which means a
    sample has a *known* answer and the page can show how far off the fix landed.

Three ways to run it:

  python -m app.star_camera --sample boston       # CLI: solve a stock sample, print the fix
  python -m app.star_camera --image photo.jpg --at 2026-03-21T06:00:00Z
  from app.star_camera import solve_image         # server: POST /api/star/solve

The star catalogue and pattern database are the `tetra3` checkout in `server/test/`
(49 MB of patterns, loaded once on the first solve and kept in memory). The shipped
database covers 10-30 degree fields of view - a narrower lens than that needs its own
database, see `generate_database()` in tetra3.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import math
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

log = logging.getLogger("star_camera")

HERE = Path(__file__).resolve().parent          # server/app
SERVER = HERE.parent                            # server
# Where the vendored tetra3 checkout might be. `server/test` is where ours landed.
TETRA3_PATHS = (SERVER / "test", SERVER, SERVER.parent / "star-tracker")

DEG = math.pi / 180.0
J2000 = datetime(2000, 1, 1, 12, 0, tzinfo=timezone.utc)
EARTH_R_KM = 6371.0088                          # mean radius, for the great-circle error


class StarCameraError(RuntimeError):
    """Something the caller can act on: no database, unreadable image, Pi unreachable."""


# ---- time and the sky -> the ground ------------------------------------------------------
def gmst_deg(when: datetime) -> float:
    """Greenwich mean sidereal time in degrees.

    How far the Earth has turned, measured against the stars rather than the Sun. This is
    the whole clock dependency of the technique: the 360.9856.. coefficient is degrees per
    *day*, so a second of error moves the answer 0.0042 degrees east or west.
    """
    days = (when.astimezone(timezone.utc) - J2000).total_seconds() / 86400.0
    return (280.46061837 + 360.98564736629 * days) % 360.0


def lat_lon_from_zenith(ra_deg: float, dec_deg: float, when: datetime) -> tuple[float, float]:
    """Where you are, given the celestial coordinates of the point directly overhead."""
    lon = (ra_deg - gmst_deg(when) + 180.0) % 360.0 - 180.0
    return dec_deg, lon


def zenith_from_lat_lon(lat_deg: float, lon_deg: float, when: datetime) -> tuple[float, float]:
    """The inverse: what is overhead at this place and time. Used to build the samples."""
    return (gmst_deg(when) + lon_deg) % 360.0, lat_deg


def great_circle_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance over the surface between two lat/lon pairs [km] (haversine)."""
    p1, p2 = lat1 * DEG, lat2 * DEG
    dp, dl = p2 - p1, (lon2 - lon1) * DEG
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_KM * math.asin(min(1.0, math.sqrt(a)))


# ---- the tetra3 solver (loaded once, kept) -------------------------------------------------
_tetra3_mod = None
_solver = None
_solver_lock = threading.Lock()


def _tetra3():
    """Import the vendored tetra3, adding its checkout to sys.path on the first call."""
    global _tetra3_mod
    if _tetra3_mod is not None:
        return _tetra3_mod
    for p in TETRA3_PATHS:
        if (p / "tetra3" / "tetra3.py").exists() and str(p) not in sys.path:
            sys.path.insert(0, str(p))
    try:
        import tetra3
    except ImportError as e:
        raise StarCameraError(
            f"cannot import tetra3 ({e}); expected the checkout at server/test/tetra3"
        ) from e
    _tetra3_mod = tetra3
    return tetra3


def solver():
    """The Tetra3 instance with the pattern database loaded.

    The first call reads 49 MB off disk and takes a second or two; every later one is
    free. Locked because the server solves in worker threads, and two requests arriving
    together would otherwise each load their own copy.
    """
    global _solver
    with _solver_lock:
        if _solver is None:
            t3 = _tetra3()
            t0 = time.perf_counter()
            loaded = t3.Tetra3()
            if not loaded.has_database:
                raise StarCameraError(
                    "tetra3 loaded but has no pattern database; expected "
                    "server/test/tetra3/data/default_database.npz"
                )
            _solver = loaded
            log.info("tetra3 database loaded in %.1fs", time.perf_counter() - t0)
    return _solver


def database_info() -> dict:
    """What the loaded database can solve - the UI warns when a lens falls outside it."""
    props = solver().database_properties
    return {
        "stars": int(solver().star_table.shape[0]),
        "patterns": int(solver().pattern_catalog.shape[0]),
        "fov_min_deg": float(props["min_fov"]),
        "fov_max_deg": float(props["max_fov"]),
        "star_max_magnitude": float(props["star_max_magnitude"]),
        "catalog": str(props["star_catalog"]),
        "epoch_equinox": int(props["epoch_equinox"]),
    }


def star_catalog() -> np.ndarray:
    """The database's star table: [ra_rad, dec_rad, x, y, z, magnitude] per row."""
    return solver().star_table


# ---- solving a photo -------------------------------------------------------------------------
@dataclass
class SolveOptions:
    """Knobs for one solve. The defaults suit a clean, wide-field, zenith-pointing frame.

    The preprocessing three (`rotate180`, `crop`, `threshold`) exist because the Pi star
    camera needs them: its lens inverts the image, the tube it sits in vignettes the
    corners, and stray room light raises the floor until centroiding finds hundreds of
    "stars" that are really background. They are no-ops at their defaults.
    """

    fov_estimate: float | None = None   # deg across the frame; None = search the database's range
    fov_max_error: float | None = None  # how far from that estimate to accept
    rotate180: bool = False             # the Pi lens inverts the frame
    crop: float = 1.0                   # keep this fraction of width/height, centred
    threshold: int | None = None        # cut pixels below this (0-255); None = tetra3's own
    max_stars: int = 8                  # brightest stars combined into candidate patterns
    search_distortion: bool = True      # let the solver fit barrel/pincushion (real lenses have it)
    timeout_ms: float = 8000.0          # give up rather than pin the server


def _load_image(data: bytes, opt: SolveOptions):
    """Decode the upload and apply the preprocessing, returning a greyscale PIL image."""
    try:
        from PIL import Image
    except ImportError as e:  # pragma: no cover - Pillow ships with the requirements
        raise StarCameraError(f"Pillow is required to read images ({e})") from e
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:
        raise StarCameraError(f"could not read that image: {e}") from e

    # 16-bit astronomy frames (the tetra3 samples are I;16) have to come down to 8-bit
    # greyscale, scaled by their own range or everything lands in the bottom few counts.
    if img.mode in ("I;16", "I;16B", "I;16L", "I", "F"):
        a = np.asarray(img).astype(np.float32)
        lo, hi = float(a.min()), float(np.percentile(a, 99.9))
        a = (a - lo) / max(hi - lo, 1.0) * 255.0
        img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "L")
    else:
        img = img.convert("L")

    if opt.rotate180:
        img = img.rotate(180)
    if 0.1 <= opt.crop < 1.0:
        w, h = img.size
        mw, mh = w * (1 - opt.crop) / 2, h * (1 - opt.crop) / 2
        img = img.crop((round(mw), round(mh), round(w - mw), round(h - mh)))
    if opt.threshold is not None:
        t = int(opt.threshold)
        img = img.point(lambda p: 255 if p > t else 0)
    return img


def _png_data_url(img, max_side: int = 520, dilate: int = 0) -> str:
    """A small PNG data URL, so the page can show what the solver was given.

    `dilate` fattens every bright point first. Stars are two or three pixels across, and
    shrinking a 1024 px frame to thumbnail size averages them straight back into the
    black sky - so a plain resize of a real sky photo shows nothing at all. This is a
    display aid only; the solve always runs on the original pixels.
    """
    from PIL import Image, ImageFilter

    if dilate:
        img = img.filter(ImageFilter.MaxFilter(dilate))
    w, h = img.size
    if max(w, h) > max_side:
        k = max_side / max(w, h)
        img = img.resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def solve_image(
    data: bytes,
    captured_at: datetime | None = None,
    opt: SolveOptions | None = None,
    truth: tuple[float, float] | None = None,
) -> dict:
    """Solve one photo of the sky into a position fix.

    `captured_at` is the UTC instant the shutter opened (defaults to now, which is only
    right for a live frame). `truth` is an optional known lat/lon - the samples have one,
    so the result can carry how far off the fix landed.

    Returns a JSON-ready dict. `solved` is False when the pattern was not recognised;
    everything else is None in that case except the diagnostics, which are what you need
    to work out why (how many centroids were found is usually the answer).
    """
    opt = opt or SolveOptions()
    when = (captured_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    t3 = _tetra3()
    t3s = solver()
    img = _load_image(data, opt)
    height, width = img.size[1], img.size[0]

    # Centroid first, on its own, so a failed solve can still say how many stars it saw.
    centroids = np.asarray(t3.get_centroids_from_image(img))
    n_centroids = int(len(centroids))

    distortion = [-0.2, 0.1] if opt.search_distortion else 0
    t0 = time.perf_counter()
    if n_centroids:
        sol = t3s.solve_from_centroids(
            centroids,
            (height, width),
            fov_estimate=opt.fov_estimate,
            fov_max_error=opt.fov_max_error,
            pattern_checking_stars=opt.max_stars,
            distortion=distortion,
            solve_timeout=opt.timeout_ms,
            return_visual=True,
        )
    else:
        sol = {"RA": None, "Dec": None, "Roll": None, "FOV": None, "RMSE": None,
               "Matches": None, "Prob": None, "distortion": None, "visual": None}
    wall_ms = (time.perf_counter() - t0) * 1000.0

    num = lambda v: None if v is None else float(v)   # numpy scalars are not JSON
    solved = sol.get("RA") is not None
    out = {
        "solved": solved,
        "captured_at": when.isoformat().replace("+00:00", "Z"),
        "gmst_deg": round(gmst_deg(when), 6),
        "centroids": n_centroids,
        "image": {"width": width, "height": height},
        "attitude": {
            "ra_deg": num(sol.get("RA")),
            "dec_deg": num(sol.get("Dec")),
            "roll_deg": num(sol.get("Roll")),
            "fov_deg": num(sol.get("FOV")),
            "distortion": num(sol.get("distortion")),
        },
        "quality": {
            "matches": None if sol.get("Matches") is None else int(sol["Matches"]),
            "rmse_arcsec": num(sol.get("RMSE")),
            "false_positive_prob": num(sol.get("Prob")),
            "solve_ms": round(wall_ms, 1),
        },
        "fix": None,
        "truth": None if truth is None else {"lat": truth[0], "lon": truth[1]},
        "error_km": None,
        # Two thumbnails: the frame itself, and tetra3's own view of what it detected
        # and matched. Both dilated, or the stars vanish at this size.
        "photo": _png_data_url(img, dilate=5),
        "preview": _png_data_url(sol.get("visual") or img, dilate=3),
    }

    if solved:
        lat, lon = lat_lon_from_zenith(float(sol["RA"]), float(sol["Dec"]), when)
        out["fix"] = {"lat": lat, "lon": lon}
        if truth is not None:
            out["error_km"] = great_circle_km(lat, lon, truth[0], truth[1])
    return out


# ---- synthetic sky, so the page works with no camera ------------------------------------------
def render_sky(
    ra_deg: float,
    dec_deg: float,
    fov_deg: float = 20.0,
    size: int = 1024,
    mag_limit: float = 6.5,
    roll_deg: float = 0.0,
    seed: int = 1,
    noise: float = 2.0,
    background: float = 6.0,
    saturate_mag: float | None = None,
):
    """Draw the sky as a pinhole camera aimed at (ra, dec) would see it.

    Stars come from the same catalogue the solver matches against, so this is a fair test
    of the matching but not of the optics - there is no atmosphere, no vignetting and no
    hot pixels here. Returns a greyscale PIL image.

    The projection is tetra3's own pinhole convention (see `_compute_centroids` there):
    with the camera frame (i, j, k) = (boresight, image x, image y),
    pixel = centre - (width/2/tan(fov/2)) * (k, j) / i.
    """
    from PIL import Image

    stars = star_catalog()
    stars = stars[stars[:, 5] <= mag_limit]
    vectors, mags = stars[:, 2:5].astype(np.float64), stars[:, 5].astype(np.float64)

    R = _camera_rotation(ra_deg, dec_deg, roll_deg)
    v = vectors @ R.T                               # catalogue -> camera frame
    half = size / 2.0
    scale = half / math.tan(fov_deg / 2 * DEG)
    sigma, rad = 1.6, 4                             # star blur [px] and the stamp half-width

    ahead = v[:, 0] > math.cos(fov_deg * DEG)       # generous cone; the crop below is exact
    v, mags = v[ahead], mags[ahead]
    y = half - scale * v[:, 2] / v[:, 0]
    x = half - scale * v[:, 1] / v[:, 0]
    inside = (x > rad) & (x < size - rad - 1) & (y > rad) & (y < size - rad - 1)
    x, y, mags = x[inside], y[inside], mags[inside]

    img = np.zeros((size, size), np.float32)
    off = np.arange(-rad, rad + 1)
    gx, gy = np.meshgrid(off, off)
    # Exposure: `saturate_mag` is the magnitude that just fills a pixel, so anything
    # brighter blooms into a disc and the faintest stars still clear the noise. Tied to
    # the magnitude limit rather than fixed, or a dim catalogue cut renders a blank sky.
    zero = mag_limit - 2.5 if saturate_mag is None else saturate_mag
    for xi, yi, m in zip(x, y, mags):
        # Magnitudes are a log scale: 5 magnitudes is a factor of 100 in brightness.
        amp = min(255.0 * 10 ** (-0.4 * (m - zero)), 2400.0)
        x0, y0 = int(xi), int(yi)
        g = amp * np.exp(-(((gx + x0 - xi) ** 2 + (gy + y0 - yi) ** 2) / (2 * sigma**2)))
        img[y0 - rad : y0 + rad + 1, x0 - rad : x0 + rad + 1] += g

    rng = np.random.default_rng(seed)
    img += background + rng.normal(0.0, noise, img.shape)
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "L"), int(len(x))


def _camera_rotation(ra_deg: float, dec_deg: float, roll_deg: float) -> np.ndarray:
    """Rotation taking catalogue vectors into the camera frame (rows: boresight, x, y).

    Built the same way tetra3 reads one back out of a solve, so a rendered frame round
    trips: ra = atan2(R[0,1], R[0,0]), dec = atan2(R[0,2], |R[1:3,2]|), roll =
    atan2(R[1,2], R[2,2]). The determinant check keeps it a proper rotation - a mirrored
    frame is a different sky and the solver would (rightly) refuse it.
    """
    ra, dec, roll = ra_deg * DEG, dec_deg * DEG, roll_deg * DEG
    boresight = np.array([math.cos(dec) * math.cos(ra), math.cos(dec) * math.sin(ra), math.sin(dec)])
    pole = np.array([0.0, 0.0, 1.0])
    up = pole - np.dot(pole, boresight) * boresight     # celestial north, projected into the frame
    if np.linalg.norm(up) < 1e-6:                        # staring at a pole: any up will do
        up = np.array([1.0, 0.0, 0.0]) - boresight[0] * boresight
    up /= np.linalg.norm(up)
    right = np.cross(boresight, up)
    R = np.stack([
        boresight,
        math.sin(roll) * up - math.cos(roll) * right,
        math.cos(roll) * up + math.sin(roll) * right,
    ])
    if np.linalg.det(R) < 0:
        R[1] *= -1
    return R


# ---- the stock images the page offers ----------------------------------------------------------
@dataclass
class Sample:
    """One quick-select image on the Star Camera page."""

    key: str
    title: str
    blurb: str
    lat: float | None = None            # the true position, for the synthetic ones
    lon: float | None = None
    at: str = "2026-03-21T06:00:00Z"    # the instant the frame is stamped with
    fov_deg: float = 20.0
    seed: int = 1                       # fixed, so a sample renders identically every run
    real: bool = False                  # a photograph rather than a rendering
    path: str | None = None             # relative to server/, for the real ones
    opt: dict = field(default_factory=dict)   # SolveOptions overrides this frame needs

    @property
    def when(self) -> datetime:
        return parse_time(self.at)


# Four synthetic frames at known positions, spread over both hemispheres and all the way
# round the globe, so the fix on the page is somewhere different every time - and verifiable.
# Then the two real sky photographs that ship with tetra3: no known observer position (they
# were taken on a tripod pointed 40 and 60 degrees up, not at the zenith), but they prove
# the solver works on an actual camera's output and not just on our own renderings.
SAMPLES: list[Sample] = [
    Sample("boston", "Over Boston", "Northern mid-latitudes, 42 deg N.", 42.3601, -71.0589, seed=1),
    Sample("sahara", "Over the Sahara", "Just on the Tropic of Cancer, 23 deg N.", 23.4162, 12.0, seed=2),
    Sample("pacific", "Mid-Pacific", "On the equator, nothing below for 2000 km.", 0.0, -150.0, seed=3),
    Sample("wellington", "Over Wellington", "Southern hemisphere, 41 deg S.", -41.2866, 174.7756, seed=4),
    Sample(
        "tetra3_alt60", "Real photo, 60 deg up",
        "A real sky photograph from the tetra3 project, 11.4 deg lens.",
        at="2019-07-29T20:47:26Z", fov_deg=11.4, real=True,
        path="test/examples/data/2019-07-29T204726_Alt60_Azi-135_Try1.tiff",
        opt={"fov_estimate": 11.4, "fov_max_error": 2.0},
    ),
    Sample(
        "tetra3_alt40", "Real photo, 40 deg up",
        "The same camera, aimed lower - more atmosphere to shoot through.",
        at="2019-07-29T20:47:26Z", fov_deg=11.4, real=True,
        path="test/examples/data/2019-07-29T204726_Alt40_Azi-135_Try1.tiff",
        opt={"fov_estimate": 11.4, "fov_max_error": 2.0},
    ),
]
SAMPLES_BY_KEY = {s.key: s for s in SAMPLES}
_sample_png: dict[str, bytes] = {}      # rendered once, then kept


def sample_list() -> list[dict]:
    """The samples the page can offer, skipping any whose file is missing."""
    out = []
    for s in SAMPLES:
        if s.real and not (SERVER / s.path).exists():
            continue
        out.append({
            "key": s.key, "title": s.title, "blurb": s.blurb, "real": s.real,
            "at": s.at, "fov_deg": s.fov_deg,
            "truth": None if s.lat is None else {"lat": s.lat, "lon": s.lon},
            "opt": s.opt,
        })
    return out


def sample_png(key: str) -> bytes:
    """The sample's image bytes: read off disk for the real ones, rendered for the rest."""
    s = SAMPLES_BY_KEY.get(key)
    if s is None:
        raise StarCameraError(f"no sample called {key}")
    if key in _sample_png:
        return _sample_png[key]
    if s.real:
        path = SERVER / s.path
        if not path.exists():
            raise StarCameraError(f"sample image {s.path} is missing from the checkout")
        # Converted to PNG so a browser can display it (the originals are 16-bit TIFF).
        img = _load_image(path.read_bytes(), SolveOptions())
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        _sample_png[key] = buf.getvalue()
    else:
        ra, dec = zenith_from_lat_lon(s.lat, s.lon, s.when)
        img, _ = render_sky(ra, dec, fov_deg=s.fov_deg, seed=s.seed)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        _sample_png[key] = buf.getvalue()
    return _sample_png[key]


def solve_sample(key: str, opt: SolveOptions | None = None) -> dict:
    """Solve one stock image, stamped with its own capture time and known position."""
    s = SAMPLES_BY_KEY.get(key)
    if s is None:
        raise StarCameraError(f"no sample called {key}")
    truth = None if s.lat is None else (s.lat, s.lon)
    res = solve_image(sample_png(key), s.when, opt or SolveOptions(**s.opt), truth)
    res["sample"] = {"key": s.key, "title": s.title}
    return res


# ---- the Pi star camera (wired up, waiting for the hardware) ------------------------------------
# `star-tracker/camera.py` runs on the Raspberry Pi: a Flask app that serves the current
# Picamera2 frame as a JPEG at GET /frame. Point `camera.url` at it and the page's
# "Capture" button goes through to the real sky. Nothing here needs the Pi to exist - the
# upload and sample paths are the same code from `solve_image()` down.
@dataclass
class CameraConfig:
    url: str = "http://172.20.10.5:5000/frame"   # the IP the Pi came up on in testing
    timeout_s: float = 5.0
    enabled: bool = False                        # flipped on once someone sets a reachable URL


camera = CameraConfig()
last_frame: tuple[bytes, str, float] | None = None   # (image, content type, unix time) of the last Pi frame


def camera_info() -> dict:
    return {"url": camera.url, "timeout_s": camera.timeout_s, "enabled": camera.enabled,
            "note": "Runs star-tracker/camera.py on the Pi; GET /frame returns a JPEG."}


def set_camera(url: str | None = None, timeout_s: float | None = None, enabled: bool | None = None) -> dict:
    if url is not None:
        camera.url = url
    if timeout_s is not None:
        camera.timeout_s = max(0.5, min(30.0, timeout_s))
    if enabled is not None:
        camera.enabled = enabled
    return camera_info()


def capture_frame() -> bytes:
    """Pull one frame off the Pi. Raises StarCameraError with something actionable."""
    if not camera.enabled:
        raise StarCameraError(
            "the Pi camera is switched off; set its address and enable it first "
            "(the Pi runs star-tracker/camera.py)"
        )
    global last_frame
    try:
        with urllib.request.urlopen(camera.url, timeout=camera.timeout_s) as r:
            if r.status != 200:
                raise StarCameraError(f"the Pi answered HTTP {r.status}")
            data = r.read()
            last_frame = (data, r.headers.get("Content-Type") or "image/jpeg", time.time())
            return data
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode()).get("error")
        except Exception:
            detail = None
        raise StarCameraError(f"the Pi camera answered HTTP {e.code}: {detail or e.reason}") from e
    except urllib.error.URLError as e:
        raise StarCameraError(f"cannot reach the Pi camera at {camera.url}: {e.reason}") from e
    except TimeoutError as e:
        raise StarCameraError(f"the Pi camera at {camera.url} timed out") from e


def solve_capture(opt: SolveOptions | None = None) -> dict:
    """Capture a frame from the Pi right now and solve it. Time is the capture instant."""
    data = capture_frame()
    when = datetime.now(timezone.utc)
    # The Pi's lens inverts the frame and its tube vignettes the corners, so the live path
    # defaults to the preprocessing the hardware actually needs.
    res = solve_image(data, when, opt or SolveOptions(rotate180=True, crop=0.5, threshold=190))
    res["sample"] = {"key": "pi", "title": "Pi star camera"}
    return res


# ---- helpers shared with the API ----------------------------------------------------------------
def parse_time(value: str | None) -> datetime:
    """Parse an ISO-8601 instant, treating a bare one as UTC. None/empty = now."""
    if not value:
        return datetime.now(timezone.utc)
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise StarCameraError(f"could not read the capture time {value!r}: {e}") from e
    return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when.astimezone(timezone.utc)


def info() -> dict:
    """Everything the page needs before its first solve."""
    return {"database": database_info(), "samples": sample_list(), "camera": camera_info()}


# ---- CLI ------------------------------------------------------------------------------------------
def report(res: dict) -> None:
    """Print one fix, the way the ground station in star-tracker/test_tracker.py does."""
    a, q = res["attitude"], res["quality"]
    print("=" * 41)
    print("            STAR CAMERA FIX")
    print("=" * 41)
    if not res["solved"]:
        print(f" NO LOCK - {res['centroids']} centroids found, no pattern matched")
        print("=" * 41)
        return
    print(f" LOCK - {q['matches']} stars matched of {res['centroids']} found")
    print(f"  Right ascension : {a['ra_deg']:10.4f} deg")
    print(f"  Declination     : {a['dec_deg']:10.4f} deg")
    print(f"  Roll            : {a['roll_deg']:10.4f} deg")
    print(f"  Field of view   : {a['fov_deg']:10.4f} deg")
    print(f"  Residual        : {q['rmse_arcsec']:10.2f} arcsec")
    print(f"  Solve time      : {q['solve_ms']:10.1f} ms")
    print("-" * 41)
    print(f"  Captured at     : {res['captured_at']}")
    print(f"  Sidereal time   : {res['gmst_deg']:10.4f} deg")
    print(f"  Latitude        : {res['fix']['lat']:10.5f}")
    print(f"  Longitude       : {res['fix']['lon']:10.5f}")
    if res["truth"]:
        t = res["truth"]
        print(f"  True position   : {t['lat']:.5f}, {t['lon']:.5f}")
        print(f"  Error           : {res['error_km']:10.2f} km")
    print("=" * 41)


def main(argv: list[str] | None = None) -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Solve a sky photo into a latitude and longitude")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--sample", help=f"a stock image: {', '.join(s.key for s in SAMPLES)}")
    src.add_argument("--image", help="path to a photo of the sky")
    src.add_argument("--capture", action="store_true", help="pull a frame off the Pi camera")
    ap.add_argument("--at", help="UTC capture time, ISO-8601 (default: now; required for --image)")
    ap.add_argument("--fov", type=float, help="field of view estimate [deg]")
    ap.add_argument("--fov-error", type=float, help="how far from that estimate to accept [deg]")
    ap.add_argument("--rotate180", action="store_true", help="the lens inverts the frame")
    ap.add_argument("--crop", type=float, default=1.0, help="keep this fraction of the frame")
    ap.add_argument("--threshold", type=int, help="drop pixels at or below this (0-255)")
    ap.add_argument("--all", action="store_true", help="solve every stock image")
    ap.add_argument("--save", help="write the image being solved to this path (samples only)")
    a = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    # Only what was actually typed, so a sample's own settings survive anything left out.
    given = {k: v for k, v in {
        "fov_estimate": a.fov, "fov_max_error": a.fov_error, "threshold": a.threshold,
        "rotate180": a.rotate180 or None, "crop": a.crop if a.crop != 1.0 else None,
    }.items() if v is not None}

    if a.all:
        for s in sample_list():
            print(f"\n--- {s['title']} ({s['key']}) ---")
            report(solve_sample(s["key"], SolveOptions(**{**s["opt"], **given})))
    elif a.capture:
        report(solve_capture(SolveOptions(**{**{"rotate180": True, "crop": 0.5, "threshold": 190}, **given})))
    elif a.image:
        report(solve_image(Path(a.image).read_bytes(), parse_time(a.at), SolveOptions(**given)))
    else:
        key = a.sample or SAMPLES[0].key
        if key not in SAMPLES_BY_KEY:
            ap.error(f"no sample called {key}; try one of: {', '.join(s.key for s in SAMPLES)}")
        if a.save:
            Path(a.save).write_bytes(sample_png(key))
            print(f"wrote {a.save}")
        report(solve_sample(key, SolveOptions(**{**SAMPLES_BY_KEY[key].opt, **given})))


if __name__ == "__main__":
    main()
