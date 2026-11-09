<script>
  /**
   * The globe the Star Camera page drops its fix onto.
   *
   * Simpler than SpaceGlobe: there is no run to play back here, just one position - so
   * this draws the Earth (same palette, coastlines and graticule as the other pages) and
   * a single pin at the latitude/longitude the photo solved to.
   *
   * The pin is drawn as a spike standing straight up out of the surface because that is
   * literally what was measured: the camera looked along that line, and the stars it saw
   * at the far end are what put the pin where it is. The cone around it is the lens's
   * field of view, opened out from the same point.
   */
  import { T } from '@threlte/core';
  import { HTML, OrbitControls } from '@threlte/extras';
  import {
    AdditiveBlending,
    BackSide,
    BufferAttribute,
    BufferGeometry,
    LineBasicMaterial,
    LineSegments,
  } from 'three';
  import { COASTLINES } from './coastlines.js';

  // `fix`/`truth`: { lat, lon } or null. `fovDeg` opens the sight cone to the lens angle.
  let { fix = null, truth = null, fovDeg = 20, showCone = true } = $props();

  const DEG = Math.PI / 180;
  const COL = { fix: '#4ea1ff', truth: '#3fb950' };

  /** lat/lon in degrees -> scene units at radius `r` (1 = sea level), north = +Y. */
  const sph = (lat, lon, r = 1) => [
    r * Math.cos(lat * DEG) * Math.cos(lon * DEG),
    r * Math.sin(lat * DEG),
    -r * Math.cos(lat * DEG) * Math.sin(lon * DEG),
  ];

  function segments(positions, color, opacity = 1) {
    const g = new BufferGeometry();
    g.setAttribute('position', new BufferAttribute(new Float32Array(positions), 3));
    return new LineSegments(g, new LineBasicMaterial({ color, transparent: opacity < 1, opacity }));
  }

  // ---- static Earth decoration (built once, shared with the LEO/fusion globe) ----
  const coast = (() => {
    const p = [];
    for (const line of COASTLINES)
      for (let i = 0; i + 3 < line.length; i += 2) {
        p.push(...sph(line[i + 1], line[i], 1.002), ...sph(line[i + 3], line[i + 2], 1.002));
      }
    return segments(p, 0x3d7ab8, 0.95);
  })();

  const graticule = (() => {
    const p = [];
    for (let lon = -180; lon < 180; lon += 30)
      for (let lat = -90; lat < 90; lat += 5) p.push(...sph(lat, lon, 1.001), ...sph(lat + 5, lon, 1.001));
    for (let lat = -60; lat <= 60; lat += 30)
      for (let lon = -180; lon < 180; lon += 5) p.push(...sph(lat, lon, 1.001), ...sph(lat, lon + 5, 1.001));
    return segments(p, 0x1e3246, 0.8);
  })();

  // ---- the pin ------------------------------------------------------------------
  const SPIKE = 0.32; // how far the sight line reaches out of the surface, in Earth radii

  /** A ring of constant angular radius around a point, walked in that point's own basis. */
  function ring(lat, lon, radiusRad, steps = 96) {
    const up = sph(lat, lon, 1);
    const [ex, ny] = tangent(up);
    const at = (a) =>
      up.map(
        (u, i) => (u * Math.cos(radiusRad) + (ex[i] * Math.cos(a) + ny[i] * Math.sin(a)) * Math.sin(radiusRad)) * 1.003,
      );
    const p = [];
    for (let k = 0; k < steps; k++) p.push(...at((k / steps) * 2 * Math.PI), ...at(((k + 1) / steps) * 2 * Math.PI));
    return p;
  }

  /** An orthonormal pair spanning the plane tangent at `up`. */
  function tangent(up) {
    const east = [-up[2], 0, up[0]];
    const en = Math.hypot(...east) || 1;
    const ex = east.map((v) => v / en);
    return [ex, [up[1] * ex[2] - up[2] * ex[1], up[2] * ex[0] - up[0] * ex[2], up[0] * ex[1] - up[1] * ex[0]]];
  }

  /** The sight cone: a rim circle at the top of the spike, plus ribs down to the ground. */
  function cone(up, fov, ribs = 8, steps = 64) {
    const [ex, ny] = tangent(up);
    const r = Math.tan((fov / 2) * DEG) * SPIKE;
    const rim = (a) => up.map((u, i) => u * (1 + SPIKE) + (ex[i] * Math.cos(a) + ny[i] * Math.sin(a)) * r);
    const p = [];
    for (let k = 0; k < steps; k++) p.push(...rim((k / steps) * 2 * Math.PI), ...rim(((k + 1) / steps) * 2 * Math.PI));
    for (let k = 0; k < ribs; k++) p.push(...up, ...rim((k / ribs) * 2 * Math.PI));
    return p;
  }

  let marks = $derived.by(() => {
    const parts = [];
    if (fix) {
      const up = sph(fix.lat, fix.lon, 1);
      parts.push({
        key: 'fix',
        color: COL.fix,
        up,
        head: up.map((v) => v * (1 + SPIKE)),
        spike: segments([...up, ...up.map((v) => v * (1 + SPIKE))], COL.fix),
        ring: segments(ring(fix.lat, fix.lon, 2.2 * DEG), COL.fix, 0.75),
        // The lens's field of view, opened out along the sight line: a rim circle at the
        // top with a few ribs down to the ground, which reads as a cone from any angle
        // (a full fan of ribs turns into a starburst when you look straight down it).
        cone: segments(cone(up, fovDeg), COL.fix, 0.3),
      });
    }
    if (truth) {
      const up = sph(truth.lat, truth.lon, 1);
      parts.push({
        key: 'truth',
        color: COL.truth,
        up,
        head: up.map((v) => v * 1.035),
        spike: segments([...up, ...up.map((v) => v * 1.035)], COL.truth),
        ring: segments(ring(truth.lat, truth.lon, 1.4 * DEG), COL.truth, 0.8),
        cone: null,
      });
    }
    return parts;
  });

  // free the GPU buffers when a new fix replaces them
  $effect(() => {
    const built = marks;
    return () => {
      for (const m of built)
        for (const o of [m.spike, m.ring, m.cone]) {
          o?.geometry.dispose();
          o?.material.dispose();
        }
    };
  });

  // Frame the fix: the whole globe in view, but with the camera a little to the south
  // and east of the pin rather than straight down the spike, which would foreshorten it
  // into a dot. That offset also carries the pin up and left, clear of the photo panel
  // in the opposite corner. Keyed on the position, so each new solve re-frames; dragging
  // in between is left alone.
  let home = $derived(fix ?? truth ?? { lat: 20, lon: -40 });
  let camPos = $derived(sph(Math.max(-86, Math.min(86, home.lat - 20)), home.lon + 16, 3.1));
  let camKey = $derived(`${home.lat.toFixed(3)},${home.lon.toFixed(3)}`);
</script>

{#key camKey}
  <T.PerspectiveCamera makeDefault position={camPos} fov={38} near={0.0005} far={100}>
    <OrbitControls target={[0, 0, 0]} enableDamping minDistance={1.04} maxDistance={20} />
  </T.PerspectiveCamera>
{/key}

<T.AmbientLight intensity={0.55} />
<T.DirectionalLight position={[3, 2, 4]} intensity={1.6} />

<!-- Earth -->
<T.Mesh>
  <T.SphereGeometry args={[1, 64, 48]} />
  <T.MeshStandardMaterial color="#0d1f33" roughness={0.95} metalness={0} />
</T.Mesh>
<T is={graticule} />
<T is={coast} />
<!-- thin atmosphere -->
<T.Mesh>
  <T.SphereGeometry args={[1.045, 48, 32]} />
  <T.MeshBasicMaterial color="#2f81f7" transparent opacity={0.07} side={BackSide} blending={AdditiveBlending} depthWrite={false} />
</T.Mesh>

{#each marks as m (m.key)}
  <T is={m.spike} />
  <T is={m.ring} />
  {#if m.cone && showCone}<T is={m.cone} />{/if}
  <T.Mesh position={m.head}>
    <T.SphereGeometry args={[m.key === 'fix' ? 0.022 : 0.012, 14, 12]} />
    <T.MeshBasicMaterial color={m.color} />
  </T.Mesh>
{/each}

<!-- Both labels sit on the ground rather than at the top of the spike: that is where the
     position actually is, and it keeps the fix next to the truth marker it should be
     sitting on top of. -->
{#if fix}
  <HTML position={marks.find((m) => m.key === 'fix').up} center zIndexRange={[10, 0]}>
    <span class="tag fix">{fix.lat.toFixed(4)}°, {fix.lon.toFixed(4)}°</span>
  </HTML>
{/if}
{#if truth}
  <HTML position={marks.find((m) => m.key === 'truth').up} center zIndexRange={[9, 0]}>
    <span class="tag truth">true position</span>
  </HTML>
{/if}

<style>
  .tag {
    font: 11px ui-monospace, monospace;
    white-space: nowrap;
    pointer-events: none;
    text-shadow: 0 0 5px #000, 0 0 5px #000;
    display: inline-block;
  }
  .fix {
    color: #9ecbff;
    transform: translate(20px, -11px);
  }
  .truth {
    color: #7ee787;
    transform: translate(20px, 11px);
  }
</style>
