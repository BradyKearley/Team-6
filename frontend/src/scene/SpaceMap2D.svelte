<script>
  /**
   * Flat (equirectangular) version of the space view: world coastlines, where every
   * Iridium satellite is right now, the ones in view, and the plane's true vs
   * estimated ground track. Plain SVG - no WebGL, no assets.
   */
  import { COASTLINES } from './coastlines.js';

  // `tracks`: [{ key, label, color, km, primary? }] - same contract as SpaceGlobe.
  let { scene, frame = 0, tracks = [], showOrbits = true, showLinks = true, zoom = 'world', satColor = '#2ea043' } = $props();

  const R = 6378.137;
  const DEG = 180 / Math.PI;
  /** ECEF km -> [lon, lat] degrees (a sphere is plenty for a world map). */
  function ll(x, y, z) {
    return [Math.atan2(y, x) * DEG, Math.atan2(z, Math.hypot(x, y)) * DEG];
  }
  const at = (flat, i) => ll(flat[i * 3], flat[i * 3 + 1], flat[i * 3 + 2]);

  let f = $derived(Math.min(Math.max(Math.round(frame) || 0, 0), scene.frames_min.length - 1));
  let visible = $derived(scene.visible[f] ?? []);
  let lead = $derived(tracks.find((t) => t.primary) ?? tracks[tracks.length - 1] ?? { km: scene.plane_true_km });
  let plane = $derived(at(lead.km, f));
  let beacons = $derived((scene.beacons_km ?? []).map((b) => ll(...b)));
  let beaconLinks = $derived(scene.beacon_links?.[f] ?? []);
  let satLL = $derived(scene.sat_km.map((s) => at(s, f)));

  /** Split a lon/lat path wherever it crosses the ±180° seam, so nothing streaks across. */
  function paths(points) {
    const out = [];
    let run = [];
    for (let i = 0; i < points.length; i++) {
      if (i && Math.abs(points[i][0] - points[i - 1][0]) > 180) {
        out.push(run);
        run = [];
      }
      run.push(points[i]);
    }
    out.push(run);
    return out.filter((r) => r.length > 1).map((r) => r.map(([lo, la]) => `${lo.toFixed(2)},${(-la).toFixed(2)}`).join(' '));
  }

  const coastPaths = COASTLINES.map((l) => {
    const pts = [];
    for (let i = 0; i < l.length; i += 2) pts.push([l[i], l[i + 1]]);
    return paths(pts);
  }).flat();

  const trackPaths = (flat) => paths(Array.from({ length: flat.length / 3 }, (_, i) => at(flat, i)));
  let truthPaths = $derived(trackPaths(scene.plane_true_km));
  let estPaths = $derived(tracks.map((t) => ({ ...t, d: trackPaths(t.km) })));
  // Ground track of each satellite that is in view right now.
  let visPaths = $derived(visible.map((s) => trackPaths(scene.sat_km[s])).flat());

  // "World": the whole map. "Plane": framed on the flight itself - every track that is
  // drawn, so a badly drifting one stays on screen instead of running off the edge.
  let box = $derived.by(() => {
    if (zoom === 'world') return { x: -180, y: -90, w: 360, h: 180 };
    let lo0 = Infinity, lo1 = -Infinity, la0 = Infinity, la1 = -Infinity;
    for (const flat of [scene.plane_true_km, ...tracks.map((t) => t.km)])
      for (let i = 0; i < flat.length / 3; i++) {
        const [lo, la] = at(flat, i);
        lo0 = Math.min(lo0, lo); lo1 = Math.max(lo1, lo);
        la0 = Math.min(la0, la); la1 = Math.max(la1, la);
      }
    if (!isFinite(lo0)) return { x: -180, y: -90, w: 360, h: 180 };
    const w = Math.max((lo1 - lo0) * 1.5, (la1 - la0) * 2.4, 0.6);   // 2:1 viewBox, 25% margin
    return { x: (lo0 + lo1) / 2 - w / 2, y: -(la0 + la1) / 2 - w / 4, w, h: w / 2 };
  });
  let k = $derived(box.w / 360); // keeps strokes and dots the same size on screen at any zoom

  // Name only the satellites that are actually on screen, and drop any label that would
  // land on top of another one (or on the plane's).
  let tags = $derived.by(() => {
    const out = [];
    // roughly the rendered size of one label, in map degrees
    const clashes = (lo, la, l2, a2) => Math.abs(lo - l2) < box.w * 0.085 && Math.abs(la - a2) < box.h * 0.035;
    for (const s of visible) {
      const [lo, la] = satLL[s];
      if (lo < box.x || lo > box.x + box.w || -la < box.y || -la > box.y + box.h) continue;
      if (clashes(lo, la, plane[0], plane[1])) continue;
      if (out.some((t) => clashes(lo, la, t.lo, t.la))) continue;
      out.push({ lo, la, name: scene.sat_names[s] });
      if (out.length >= 5) break;
    }
    return out;
  });
</script>

<svg viewBox={`${box.x} ${box.y} ${box.w} ${box.h}`} preserveAspectRatio="xMidYMid meet" role="img" aria-label="World map of the satellites in view and the plane's ground track">
  <rect x={box.x} y={box.y} width={box.w} height={box.h} fill="#0b1726" />
  <!-- graticule -->
  <g stroke="#172a3e" stroke-width={0.6 * k} fill="none">
    {#each [-150, -120, -90, -60, -30, 0, 30, 60, 90, 120, 150] as lon}
      <line x1={lon} y1="-90" x2={lon} y2="90" />
    {/each}
    {#each [-60, -30, 0, 30, 60] as lat}
      <line x1="-180" y1={-lat} x2="180" y2={-lat} />
    {/each}
  </g>
  <g stroke="#2f5f8c" stroke-width={0.9 * k} fill="none" stroke-linejoin="round">
    {#each coastPaths as d}<polyline points={d} />{/each}
  </g>

  {#if showOrbits}
    <g stroke={satColor} stroke-width={0.8 * k} fill="none" opacity="0.35">
      {#each visPaths as d}<polyline points={d} />{/each}
    </g>
  {/if}

  <!-- plane ground tracks: truth underneath, every estimate on top -->
  <g fill="none" stroke-linecap="round">
    {#each truthPaths as d}<polyline points={d} stroke="#8b949e" stroke-width={2.4 * k} />{/each}
    {#each estPaths as t (t.key)}
      {#each t.d as d}<polyline points={d} stroke={t.color} stroke-width={1.3 * k} />{/each}
    {/each}
  </g>

  {#if scene.beacons_km?.length}
    <g>
      {#if showLinks}
        {#each beaconLinks as b}
          {#if Math.abs(beacons[b][0] - plane[0]) < 180}
            <line x1={plane[0]} y1={-plane[1]} x2={beacons[b][0]} y2={-beacons[b][1]} stroke="#bc5cc4" stroke-width={0.8 * k} opacity="0.6" />
          {/if}
        {/each}
      {/if}
      {#each beacons as [lo, la], i}
        {#if scene.beacon_range_km}
          <!-- how far this tower reaches (a circle on the ground is an ellipse on a flat map) -->
          <ellipse
            cx={lo}
            cy={-la}
            rx={scene.beacon_range_km / 111.32 / Math.max(Math.cos((la * Math.PI) / 180), 0.05)}
            ry={scene.beacon_range_km / 111.32}
            fill="none"
            stroke="#bc5cc4"
            stroke-width={0.7 * k}
            opacity="0.35"
          />
        {/if}
        <rect x={lo - 1.4 * k} y={-la - 1.4 * k} width={2.8 * k} height={2.8 * k} fill="#bc5cc4" />
        <text x={lo + 3 * k} y={-la + 4 * k} fill="#d8a0dd" font-size={5 * k}>{scene.beacon_names[i]}</text>
      {/each}
    </g>
  {/if}

  {#if showLinks}
    <g stroke={satColor} stroke-width={0.8 * k} opacity="0.6">
      {#each visible as s}
        {#if Math.abs(satLL[s][0] - plane[0]) < 180}
          <line x1={plane[0]} y1={-plane[1]} x2={satLL[s][0]} y2={-satLL[s][1]} />
        {/if}
      {/each}
    </g>
  {/if}

  <!-- satellites -->
  <g>
    {#each satLL as [lo, la], i}
      <circle cx={lo} cy={-la} r={(visible.includes(i) ? 2.2 : 1.3) * k} fill={visible.includes(i) ? satColor : '#48566a'} />
    {/each}
    {#each tags as t (t.name)}
      <text x={t.lo + 3 * k} y={-t.la - 2 * k} fill={satColor} font-size={5 * k}>{t.name}</text>
    {/each}
  </g>

  <!-- the plane -->
  <circle cx={plane[0]} cy={-plane[1]} r={2.4 * k} fill={lead.color ?? '#4ea1ff'} stroke="#0b1726" stroke-width={0.8 * k} />
  <text x={plane[0] + 3.5 * k} y={-plane[1] + 1.5 * k} fill="#9ecbff" font-size={5 * k}>plane</text>
</svg>

<style>
  svg {
    width: 100%;
    height: 100%;
    display: block;
    background: #0b1726;
  }
  text {
    font-family: ui-monospace, 'Cascadia Code', Consolas, monospace;
    paint-order: stroke;
    stroke: #0b1726;
    stroke-width: 0.6px;
  }
</style>
