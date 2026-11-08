<script>
  /**
   * Where the satellites are, the way a GNSS receiver draws it: the centre is straight up,
   * the rim is the horizon, north is up and east is right. Every dot here is a Doppler
   * measurement going into the navigation filter this instant.
   */
  let { sats = [], mask = 10, size = 168 } = $props();

  const R = $derived(size / 2 - 16);
  const D2R = Math.PI / 180;
  /** azimuth/elevation -> x,y on the plot (straight down from above) */
  const xy = (az, el) => {
    const r = ((90 - Math.max(el, 0)) / 90) * R;
    return [r * Math.sin(az * D2R), -r * Math.cos(az * D2R)];
  };
  let maskR = $derived(((90 - mask) / 90) * R);
</script>

<figure class="sky" style:width={`${size}px`}>
  <figcaption>Satellites overhead <span>{sats.length} feeding the filter</span></figcaption>
  <svg viewBox={`${-size / 2} ${-size / 2} ${size} ${size}`} width={size} height={size} role="img"
    aria-label={`Sky plot: ${sats.length} satellites in view`}>
    <circle r={R} class="ring" />
    <circle r={R * 0.667} class="ring faint" />
    <circle r={R * 0.333} class="ring faint" />
    <circle r={maskR} class="mask" />
    <line x1={-R} y1="0" x2={R} y2="0" class="cross" />
    <line x1="0" y1={-R} x2="0" y2={R} class="cross" />
    <text class="pt" x="0" y={-R - 4} text-anchor="middle">N</text>
    <text class="pt" x={R + 4} y="3">E</text>
    <text class="lbl" x="3" y={-R * 0.333 - 2}>60°</text>
    <text class="lbl" x="3" y={-maskR - 2}>{mask}°</text>
    {#each sats as s (s.n)}
      {@const p = xy(s.az, s.el)}
      <g>
        <circle cx={p[0]} cy={p[1]} r="5" class="sat" />
        <text class="name" x={p[0]} y={p[1] - 8} text-anchor="middle">{s.n}</text>
      </g>
    {/each}
    {#if !sats.length}<text class="none" x="0" y="4" text-anchor="middle">nothing in view</text>{/if}
  </svg>
  <ul>
    {#each sats as s (s.n)}
      <li><i></i>IRIDIUM {s.n}<b class="mono">{s.khz > 0 ? '+' : ''}{s.khz.toFixed(1)} kHz</b></li>
    {/each}
  </ul>
</figure>

<style>
  .sky {
    margin: 0;
    padding: 8px 10px 6px;
    background: rgba(10, 17, 28, 0.88);
    border: 1px solid var(--border);
    border-radius: 10px;
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
  .ring {
    fill: none;
    stroke: #2a3b50;
  }
  .ring.faint {
    stroke: #1e2c3e;
  }
  .mask {
    fill: none;
    stroke: #2ea043;
    stroke-dasharray: 3 3;
    opacity: 0.5;
  }
  .cross {
    stroke: #1e2c3e;
  }
  .sat {
    fill: #2ea043;
    stroke: #0a111c;
    stroke-width: 2;
  }
  .pt {
    fill: var(--muted);
    font-size: 9px;
  }
  .lbl,
  .none {
    fill: var(--muted);
    font-size: 8px;
  }
  .none {
    font-size: 10px;
  }
  .name {
    fill: #9fd8a8;
    font: 9px ui-monospace, monospace;
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
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: #2ea043;
    flex: none;
  }
  li b {
    margin-left: auto;
    color: var(--text);
    font-weight: 500;
  }
</style>
