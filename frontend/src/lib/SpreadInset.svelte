<script>
  /**
   * "Where does each filter think the plane is, right now?"
   *
   * The globe can't show the thing the fusion page is about: at world scale, 9 m and
   * 12 km land on the same pixel. This little display zooms all the way in - the truth
   * sits at the crosshair and every filter's estimate is plotted as its offset from it,
   * in local east/north. During playback you watch the unaided IMU wander off the edge
   * while the fused one stays on the cross.
   */
  let { truthKm, tracks = [], frame = 0, size = 190 } = $props();

  const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const norm = (a) => {
    const n = Math.hypot(...a) || 1;
    return [a[0] / n, a[1] / n, a[2] / n];
  };
  const at = (flat, i) => [flat[i * 3], flat[i * 3 + 1], flat[i * 3 + 2]];

  /** Offset of an estimate from the truth at step i, as [east, north] in km. */
  function offset(flat, i) {
    const t = at(truthKm, i);
    const up = norm(t);
    const east = norm([-t[1], t[0], 0]);
    const north = [up[1] * east[2] - up[2] * east[1], up[2] * east[0] - up[0] * east[2], up[0] * east[1] - up[1] * east[0]];
    const d = sub(at(flat, i), t);
    return [dot(d, east), dot(d, north)];
  }

  let n = $derived(Math.floor(truthKm.length / 3));
  let f = $derived(Math.min(Math.max(Math.round(frame) || 0, 0), n - 1));

  // Outer ring: a round number big enough for the whole run, so it never jumps mid-playback.
  let outer = $derived.by(() => {
    let max = 0.05;
    for (const t of tracks)
      for (let i = 0; i < n; i++) {
        const [e, no] = offset(t.km, i);
        max = Math.max(max, Math.hypot(e, no));
      }
    const p = Math.pow(10, Math.ceil(Math.log10(max)));
    return [1, 2, 5, 10].map((m) => m * p / 10).find((v) => v >= max) ?? p;
  });

  const R = $derived(size / 2 - 14);
  let scale = $derived(R / outer); // px per km
  const fmt = (km) => (km >= 1 ? `${km} km` : `${Math.round(km * 1000)} m`);

  /** Last few frames of a track, so the drift has a direction you can see. */
  function trail(t) {
    const pts = [];
    for (let i = Math.max(0, f - 12); i <= f; i++) {
      const [e, no] = offset(t.km, i);
      pts.push(`${(e * scale).toFixed(1)},${(-no * scale).toFixed(1)}`);
    }
    return pts.join(' ');
  }
  let dots = $derived(tracks.map((t) => ({ ...t, p: offset(t.km, f), off: Math.hypot(...offset(t.km, f)) })));
</script>

<figure class="inset" style:width={`${size}px`}>
  <figcaption>Where each filter thinks it is <span>horizontal offset from the truth</span></figcaption>
  <svg viewBox={`${-size / 2} ${-size / 2} ${size} ${size}`} width={size} height={size} role="img" aria-label="Offset of each estimate from the true position">
    <g class="rings">
      <circle r={R} />
      <circle r={R / 10} />
      <line x1={-R} y1="0" x2={R} y2="0" />
      <line x1="0" y1={-R} x2="0" y2={R} />
    </g>
    <text class="tick" x="3" y={-R + 2}>{fmt(outer)}</text>
    <text class="tick" x="3" y={-R / 10 + 2}>{fmt(outer / 10)}</text>

    {#each dots as d (d.key)}
      <polyline points={trail(d)} fill="none" stroke={d.color} stroke-width="1.5" opacity="0.45" />
    {/each}
    {#each dots as d (d.key)}
      <circle
        cx={Math.max(-R - 6, Math.min(R + 6, d.p[0] * scale))}
        cy={Math.max(-R - 6, Math.min(R + 6, -d.p[1] * scale))}
        r="4.5"
        fill={d.color}
        stroke="#0a111c"
        stroke-width="2"
      />
    {/each}
    <!-- the truth is the cross at the middle -->
    <g class="truth"><line x1="-6" y1="0" x2="6" y2="0" /><line x1="0" y1="-6" x2="0" y2="6" /></g>
  </svg>
  <ul>
    {#each dots as d (d.key)}
      <li><i style:background={d.color}></i>{d.short}<b class="mono">{d.off >= 1 ? `${d.off.toFixed(1)} km` : `${Math.round(d.off * 1000)} m`}</b></li>
    {/each}
  </ul>
</figure>

<style>
  .inset {
    position: absolute;
    right: 12px;
    bottom: 58px;
    margin: 0;
    padding: 8px 10px 6px;
    background: rgba(10, 17, 28, 0.88);
    border: 1px solid var(--border);
    border-radius: 10px;
    z-index: 2;
  }
  figcaption {
    font-size: 11px;
    color: var(--text);
    margin-bottom: 2px;
  }
  figcaption span {
    display: block;
    color: var(--muted);
    font-size: 10px;
  }
  svg {
    display: block;
    margin: 0 auto;
  }
  .rings circle {
    fill: none;
    stroke: #2a3b50;
  }
  .rings line {
    stroke: #22314a;
  }
  .truth line {
    stroke: #c9d1d9;
    stroke-width: 1.5;
  }
  .tick {
    fill: var(--muted);
    font-size: 9px;
  }
  ul {
    list-style: none;
    margin: 4px 0 0;
    padding: 0;
    font-size: 11px;
    color: var(--muted);
  }
  li {
    display: flex;
    align-items: center;
    gap: 6px;
    line-height: 1.6;
  }
  li i {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex: none;
  }
  li b {
    margin-left: auto;
    color: var(--text);
    font-weight: 500;
  }
</style>
