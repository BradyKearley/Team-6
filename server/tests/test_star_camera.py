import io
from datetime import datetime, timedelta, timezone

import pytest

from app import star_camera as sc

# Solving needs the tetra3 checkout and its 49 MB pattern database. Skip (don't fail)
# when they aren't there, the way the LEO tests skip without orbit elements - the rest
# of the suite still runs on a fresh clone.
try:
    sc.solver()
    HAVE_DB = True
except Exception:  # no tetra3, no database, no Pillow
    HAVE_DB = False
needs_db = pytest.mark.skipif(not HAVE_DB, reason="no tetra3 pattern database")

AT = datetime(2026, 3, 21, 6, 0, tzinfo=timezone.utc)


# ---- the astronomy, which needs no database -------------------------------------------
def test_zenith_round_trip():
    """lat/lon -> what's overhead -> lat/lon again, at several places and times."""
    for lat, lon in [(42.36, -71.06), (0.0, -150.0), (-41.29, 174.78), (64.1, -21.9)]:
        for hours in (0, 7, 19):
            when = AT + timedelta(hours=hours)
            ra, dec = sc.zenith_from_lat_lon(lat, lon, when)
            back_lat, back_lon = sc.lat_lon_from_zenith(ra, dec, when)
            assert back_lat == pytest.approx(lat, abs=1e-9)
            assert back_lon == pytest.approx(lon, abs=1e-9)


def test_gmst_advances_a_full_turn_per_sidereal_day():
    """A sidereal day is ~3m56s short of a solar one - that difference is the whole trick."""
    sidereal = timedelta(hours=23, minutes=56, seconds=4.0905)
    assert sc.gmst_deg(AT + sidereal) == pytest.approx(sc.gmst_deg(AT), abs=0.01)


def test_one_second_of_clock_error_is_about_460_m_at_the_equator():
    """The cost of a bad clock, which is what the page warns about."""
    ra, dec = sc.zenith_from_lat_lon(0.0, 0.0, AT)
    _, lon = sc.lat_lon_from_zenith(ra, dec, AT + timedelta(seconds=1))
    assert sc.great_circle_km(0.0, 0.0, 0.0, lon) == pytest.approx(0.46, abs=0.02)


def test_latitude_does_not_care_about_the_clock():
    ra, dec = sc.zenith_from_lat_lon(42.36, -71.06, AT)
    lat, _ = sc.lat_lon_from_zenith(ra, dec, AT + timedelta(hours=3))
    assert lat == pytest.approx(42.36)


def test_great_circle_known_distances():
    assert sc.great_circle_km(0, 0, 0, 0) == 0.0
    assert sc.great_circle_km(0, 0, 0, 180) == pytest.approx(20015, rel=0.001)   # half way round
    assert sc.great_circle_km(-90, 0, 90, 0) == pytest.approx(20015, rel=0.001)  # pole to pole


def test_parse_time_treats_a_bare_timestamp_as_utc():
    assert sc.parse_time("2026-03-21T06:00:00") == AT
    assert sc.parse_time("2026-03-21T06:00:00Z") == AT
    assert sc.parse_time("2026-03-21T08:00:00+02:00") == AT
    assert sc.parse_time(None).tzinfo is timezone.utc
    with pytest.raises(sc.StarCameraError):
        sc.parse_time("yesterday")


# ---- solving ----------------------------------------------------------------------------
@needs_db
@pytest.mark.parametrize("key", [s.key for s in sc.SAMPLES if not s.real])
def test_every_stock_image_solves_to_its_own_position(key):
    """The point of the page: the rendered sky gives back the position it was made for."""
    res = sc.solve_sample(key)
    assert res["solved"], f"{key} did not solve ({res['centroids']} centroids)"
    assert res["quality"]["matches"] >= 6
    # A pixel of a 20 deg frame across 1024 px is ~2 km on the ground, so a few km is the
    # floor here - but tens of km would mean the geometry is wrong, not just quantised.
    assert res["error_km"] < 10.0


@needs_db
@pytest.mark.parametrize("key", [s.key for s in sc.SAMPLES if s.real])
def test_the_real_photographs_solve(key):
    """Not our own rendering: actual camera frames, with real noise and real distortion."""
    if not (sc.SERVER / sc.SAMPLES_BY_KEY[key].path).exists():
        pytest.skip(f"{key} image is not in the checkout")
    res = sc.solve_sample(key)
    assert res["solved"]
    assert res["quality"]["matches"] >= 8
    assert res["attitude"]["fov_deg"] == pytest.approx(11.4, abs=0.5)


@needs_db
def test_samples_are_deterministic():
    """Same bytes every run - a salted hash in the seed made these change per restart."""
    sc._sample_png.clear()
    first = sc.sample_png("boston")
    sc._sample_png.clear()
    assert sc.sample_png("boston") == first


@needs_db
def test_solve_is_stamped_with_the_time_it_is_given():
    """Longitude follows the clock. Call the same frame an hour later and the sky has
    turned 15 degrees east under you, so the position it gives moves 15 degrees west."""
    png = sc.sample_png("boston")
    a = sc.solve_image(png, AT)
    b = sc.solve_image(png, AT + timedelta(hours=1))
    assert a["solved"] and b["solved"]
    assert a["fix"]["lat"] == pytest.approx(b["fix"]["lat"], abs=1e-9)
    west = (a["fix"]["lon"] - b["fix"]["lon"]) % 360
    assert west == pytest.approx(360 * 3600 / 86164.0905, abs=0.05)  # one sidereal hour


@needs_db
def test_a_blank_frame_does_not_solve_but_says_why():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("L", (512, 512), 0).save(buf, format="PNG")
    res = sc.solve_image(buf.getvalue(), AT)
    assert res["solved"] is False
    assert res["fix"] is None
    assert res["centroids"] == 0
    assert res["preview"].startswith("data:image/png;base64,")  # still shows the frame


def test_an_unreadable_upload_raises_something_actionable():
    with pytest.raises(sc.StarCameraError, match="could not read"):
        sc.solve_image(b"this is not an image", AT)


def test_unknown_sample_raises():
    with pytest.raises(sc.StarCameraError, match="no sample called"):
        sc.sample_png("nope")


# ---- the Pi camera, which is not wired up yet ---------------------------------------------
def test_capture_refuses_politely_until_a_camera_is_configured():
    was = sc.camera.enabled
    try:
        sc.set_camera(enabled=False)
        with pytest.raises(sc.StarCameraError, match="switched off"):
            sc.capture_frame()
    finally:
        sc.set_camera(enabled=was)


def test_set_camera_clamps_the_timeout():
    was = (sc.camera.url, sc.camera.timeout_s, sc.camera.enabled)
    try:
        assert sc.set_camera(timeout_s=1000)["timeout_s"] == 30.0
        assert sc.set_camera(timeout_s=0)["timeout_s"] == 0.5
        assert sc.set_camera(url="http://pi.local:5000/frame")["url"] == "http://pi.local:5000/frame"
    finally:
        sc.set_camera(*was[:2], enabled=was[2])


# ---- the rendered sky ----------------------------------------------------------------------
@needs_db
def test_render_puts_stars_in_the_frame():
    img, drawn = sc.render_sky(45.0, 20.0, fov_deg=20.0, size=512)
    assert drawn > 10
    assert img.size == (512, 512)
    assert img.getextrema()[1] > 200   # something is actually bright in there


@needs_db
def test_render_round_trips_through_the_solver_at_an_awkward_roll():
    """A rolled frame still solves, and reports the roll it was given."""
    img, _ = sc.render_sky(45.0, 20.0, fov_deg=20.0, roll_deg=137.0)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    res = sc.solve_image(buf.getvalue(), AT)
    assert res["solved"]
    assert res["attitude"]["ra_deg"] == pytest.approx(45.0, abs=0.1)
    assert res["attitude"]["dec_deg"] == pytest.approx(20.0, abs=0.1)
    assert res["attitude"]["roll_deg"] % 360 == pytest.approx(137.0, abs=0.5)
