<script>
  /**
   * 3D space view of one LEO run: the Earth in ECEF (so it never spins - the ground
   * track stays put and the satellites do the moving), the whole Iridium NEXT
   * constellation, and the plane with the links it is hearing Doppler on.
   *
   * Everything is built from the server's `scene` block and plain three.js geometry -
   * no textures, no downloads.
   */
  import { T } from '@threlte/core';
  import { HTML, OrbitControls } from '@threlte/extras';
  import {
    AdditiveBlending,
    BackSide,
    BufferAttribute,
    BufferGeometry,
    Color,
    InstancedMesh,
    Line,
    LineBasicMaterial,
    LineSegments,
    Matrix4,
    MeshBasicMaterial,
    SphereGeometry,
  } from 'three';
  import { COASTLINES } from './coastlines.js';

  // `tracks`: [{ key, label, color, km: [x,y,z,...], primary? }] - the estimated paths to draw.
  // The primary one (or the last) carries the plane marker.
  // satColor: the highlight for satellites in view. The fusion page turns it neutral so
  // all four track hues stay distinguishable (size still marks the ones in view).
  // focus: 'globe' looks at the whole Earth, 'flight' drops the camera onto the route so
  // the ground towers, their reach and the tracks are actually big enough to see.
  let { scene, frame = 0, tracks = [], showOrbits = true, showLinks = true, satColor = '#2ea043', focus = 'globe' } = $props();

  const R = 6378.137; // Earth radius [km] = 1 scene unit
  const DEG = Math.PI / 180;
  /** ECEF km -> scene units, three.js style (north = +Y). */
  const ecef = (x, y, z) => [x / R, z / R, -y / R];
  /** lat/lon in degrees -> scene units at radius `r` (1 = sea level). */
  const sph = (lat, lon, r = 1) => [
    r * Math.cos(lat * DEG) * Math.cos(lon * DEG),
    r * Math.sin(lat * DEG),
    -r * Math.cos(lat * DEG) * Math.sin(lon * DEG),
  ];

  const COL = { sat: 0x5b6b7f, truth: 0x8b949e, beacon: 0xbc5cc4 };
  // Marker sizes are in Earth radii: big enough to see from orbit, small enough up close.
  let S = $derived(
    focus === 'flight'
      ? { mast: 0.003, head: 0.0006, plane: 0.0005, sat: 0.003 } // ~4 km markers, 450 km up
      : { mast: 0.018, head: 0.006, plane: 0.009, sat: 0.013 },
  );
  let visHex = $derived(new Color(satColor).getHex());

  function segments(positions, color, opacity = 1) {
    const g = new BufferGeometry();
    g.setAttribute('position', new BufferAttribute(new Float32Array(positions), 3));
    return new LineSegments(g, new LineBasicMaterial({ color, transparent: opacity < 1, opacity }));
  }

  // ---- static Earth decoration (built once) ----------------------------------
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

  // ---- per-run objects (rebuilt when a new simulation arrives) -----------------
  let built = $derived.by(() => {
    const n = scene.sat_km.length;
    const frames = scene.frames_min.length;

    // every satellite's path over the run, one faint polyline each
    const orbit = [];
    for (const s of scene.sat_km)
      for (let i = 0; i + 5 < s.length; i += 3) {
        orbit.push(...ecef(s[i], s[i + 1], s[i + 2]), ...ecef(s[i + 3], s[i + 4], s[i + 5]));
      }

    const sats = new InstancedMesh(new SphereGeometry(S.sat, 10, 8), new MeshBasicMaterial(), n);
    sats.frustumCulled = false;

    const links = new BufferGeometry();
    links.setAttribute('position', new BufferAttribute(new Float32Array(n * 6), 3));

    // one continuous polyline through every sampled position
    const line = (flat, color) => {
      const g = new BufferGeometry();
      const p = new Float32Array(flat.length);
      for (let i = 0; i < flat.length; i += 3) p.set(ecef(flat[i], flat[i + 1], flat[i + 2]), i);
      g.setAttribute('position', new BufferAttribute(p, 3));
      return new Line(g, new LineBasicMaterial({ color }));
    };

    // Ground towers: a mast standing on the surface (so they never read as satellites) and
    // a ring at the edge of their range - the plane has to be inside one to hear it.
    const beacons = [];
    const rings = [];
    const heads = [];
    const reach = (scene.beacon_range_km ?? 0) / R; // angular radius, in radians
    for (const [x, y, z] of scene.beacons_km ?? []) {
      const p = ecef(x, y, z);
      const up = p.map((v) => v / Math.hypot(...p));
      const top = p.map((v, i) => v + up[i] * S.mast);
      beacons.push(...p, ...top);
      heads.push(top);
      if (reach > 0) {
        // a circle of constant angular radius around the tower, walked in its own basis
        const e = [-up[1], up[0], 0];
        const en = Math.hypot(...e) || 1;
        const ex = e.map((v) => v / en);
        const ny = [up[1] * ex[2] - up[2] * ex[1], up[2] * ex[0] - up[0] * ex[2], up[0] * ex[1] - up[1] * ex[0]];
        const pt = (a) =>
          up.map((u, i) => (u * Math.cos(reach) + (ex[i] * Math.cos(a) + ny[i] * Math.sin(a)) * Math.sin(reach)) * 1.001);
        for (let k = 0; k < 72; k++) rings.push(...pt((k / 72) * 2 * Math.PI), ...pt(((k + 1) / 72) * 2 * Math.PI));
      }
    }
    const blinks = new BufferGeometry();
    blinks.setAttribute('position', new BufferAttribute(new Float32Array(Math.max(1, (scene.beacons_km ?? []).length) * 6), 3));

    return {
      n,
      frames,
      orbit: segments(orbit, 0x2b3a4d, 0.38),
      sats,
      links,
      beacons: segments(beacons, COL.beacon, 0.95),
      rings: segments(rings, COL.beacon, 0.3),
      heads,
      blinks,
      truth: line(scene.plane_true_km, COL.truth),
      paths: tracks.map((t) => ({ ...t, obj: line(t.km, new Color(t.color).getHex()) })),
    };
  });

  // free the GPU buffers when a new run replaces them
  $effect(() => {
    const b = built;
    return () => {
      b.orbit.geometry.dispose();
      b.orbit.material.dispose();
      b.sats.geometry.dispose();
      b.sats.material.dispose();
      b.sats.dispose();
      b.links.dispose();
      b.blinks.dispose();
      b.beacons.geometry.dispose();
      b.beacons.material.dispose();
      b.rings.geometry.dispose();
      b.rings.material.dispose();
      for (const t of [b.truth, ...b.paths.map((p) => p.obj)]) (t.geometry.dispose(), t.material.dispose());
    };
  });

  // ---- the current frame -------------------------------------------------------
  let f = $derived(Math.min(Math.max(Math.round(frame) || 0, 0), scene.frames_min.length - 1));
  let visible = $derived(scene.visible[f] ?? []);
  let lead = $derived(tracks.find((t) => t.primary) ?? tracks[tracks.length - 1] ?? { km: scene.plane_true_km });
  let planePos = $derived(ecef(lead.km[f * 3], lead.km[f * 3 + 1], lead.km[f * 3 + 2]));
  let beaconLinks = $derived(scene.beacon_links?.[f] ?? []);
  let satPos = $derived.by(() =>
    scene.sat_km.map((s) => ecef(s[f * 3], s[f * 3 + 1], s[f * 3 + 2])),
  );
  // Name tags for the handful of satellites currently in view.
  let tags = $derived(visible.slice(0, 6).map((i) => ({ name: scene.sat_names[i], pos: satPos[i] })));

  const M = new Matrix4();
  const c = new Color();
  $effect(() => {
    const { sats } = built;
    const vis = new Set(visible);
    for (let i = 0; i < satPos.length; i++) {
      const k = vis.has(i) ? 1.7 : 1; // size backs up the colour, for CVD and small screens
      sats.setMatrixAt(i, M.makeScale(k, k, k).setPosition(...satPos[i]));
      sats.setColorAt(i, c.setHex(vis.has(i) ? visHex : COL.sat));
    }
    sats.instanceMatrix.needsUpdate = true;
    if (sats.instanceColor) sats.instanceColor.needsUpdate = true;
  });

  $effect(() => {
    const pos = built.links.getAttribute('position');
    visible.forEach((s, k) => {
      pos.setXYZ(k * 2, ...planePos);
      pos.setXYZ(k * 2 + 1, ...satPos[s]);
    });
    pos.needsUpdate = true;
    built.links.setDrawRange(0, visible.length * 2);
    built.links.computeBoundingSphere();
  });

  $effect(() => {
    const n = scene.beacons_km?.length ?? 0;
    if (!n) return;
    const pos = built.blinks.getAttribute('position');
    beaconLinks.forEach((b, k) => {
      pos.setXYZ(k * 2, ...planePos);
      pos.setXYZ(k * 2 + 1, ...ecef(...scene.beacons_km[b]));
    });
    pos.needsUpdate = true;
    built.blinks.setDrawRange(0, beaconLinks.length * 2);
    built.blinks.computeBoundingSphere();
  });

  // Two framings: out over the plane looking at the whole Earth, or low over the route
  // looking along it, which is the only scale where towers and tracks are legible.
  let view = $derived.by(() => {
    const t = scene.plane_true_km;
    const a = ecef(t[0], t[1], t[2]);
    const b = ecef(t[t.length - 3], t[t.length - 2], t[t.length - 1]);
    if (focus !== 'flight') return { pos: a.map((v) => v * 3.1), target: [0, 0, 0], min: 1.12 };
    const mid = a.map((v, i) => (v + b[i]) / 2);
    const up = mid.map((v) => v / Math.hypot(...mid));
    const fwd = b.map((v, i) => v - a[i]);
    const fn = Math.hypot(...fwd) || 1;
    return {
      pos: mid.map((v, i) => v + up[i] * 0.055 - (fwd[i] / fn) * 0.05), // behind and above
      target: mid,
      min: 0.002,
    };
  });
</script>

{#key focus}
  <T.PerspectiveCamera makeDefault position={view.pos} fov={38} near={0.0005} far={100}>
    <OrbitControls target={view.target} enableDamping minDistance={view.min} maxDistance={20} />
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

{#if showOrbits}<T is={built.orbit} />{/if}
<T is={built.sats} />
<T is={built.truth} />
{#each built.paths as p (p.key)}<T is={p.obj} />{/each}
{#if scene.beacons_km?.length}
  <T is={built.beacons} />
  <T is={built.rings} />
  {#each built.heads as h, i}
    <T.Mesh position={h}>
      <T.SphereGeometry args={[S.head, 10, 8]} />
      <T.MeshBasicMaterial color="#bc5cc4" />
    </T.Mesh>
    {#if focus === 'flight'}
      <HTML position={h} center zIndexRange={[9, 0]}><span class="tag gnd">{scene.beacon_names[i]}</span></HTML>
    {/if}
  {/each}
  {#if showLinks && beaconLinks.length}
    <T.LineSegments geometry={built.blinks}>
      <T.LineBasicMaterial color={COL.beacon} transparent opacity={0.6} />
    </T.LineSegments>
  {/if}
{/if}

{#if showLinks && visible.length}
  <T.LineSegments geometry={built.links}>
    <T.LineBasicMaterial color={satColor} transparent opacity={0.75} />
  </T.LineSegments>
{/if}

<!-- the plane -->
<T.Mesh position={planePos}>
  <T.SphereGeometry args={[S.plane, 12, 10]} />
  <T.MeshBasicMaterial color={lead.color ?? '#4ea1ff'} />
</T.Mesh>
<HTML position={planePos} center zIndexRange={[10, 0]}><span class="tag plane">plane</span></HTML>
{#each tags as t (t.name)}
  <HTML position={t.pos} center zIndexRange={[9, 0]}><span class="tag sat" style:--sat-tag={satColor}>{t.name}</span></HTML>
{/each}

<style>
  .tag {
    font: 10px ui-monospace, monospace;
    white-space: nowrap;
    pointer-events: none;
    text-shadow: 0 0 4px #000;
  }
  .plane {
    color: #9ecbff;
    transform: translate(14px, -10px);
    display: inline-block;
  }
  .gnd {
    color: #d8a0dd;
    transform: translate(16px, -8px);
    display: inline-block;
  }
  .sat {
    color: var(--sat-tag, #56d364);
    transform: translateY(-12px);
    display: inline-block;
  }
</style>
