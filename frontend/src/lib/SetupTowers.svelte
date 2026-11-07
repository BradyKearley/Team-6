<script>
  // Step-by-step guide: connect the plane, put it at (0,0), measure each tower's
  // distance, drag each tower to its real direction, save.
  import { live } from './live.svelte.js';
  import { api } from './api.js';
  import { toFt, toM, fmtFt } from './units.js';

  let { onclose } = $props();

  const STEPS = ['Connect', 'Place plane', 'Point towers', 'Save'];
  let step = $state(0);
  let error = $state('');

  /** Run an API call; on failure show its message and return undefined. */
  async function tryApi(fn) {
    error = '';
    try {
      return await fn();
    } catch (e) {
      error = e.message;
    }
  }

  // Towers already on the map, by name.
  let towersByName = $derived(Object.fromEntries((live.data?.towers ?? []).map((t) => [t.name, t])));

  // ---- step 1: connection -------------------------------------------------
  let link = $derived(live.data?.link);
  let onSim = $derived(link?.source === 'sim');
  let fresh = $derived(link?.last_report_age != null && link.last_report_age < 2);
  let heardNow = $derived([
    ...(live.data?.towers ?? []).filter((t) => t.live).map((t) => ({ name: t.name, rssi: t.live.rssi })),
    ...(live.data?.unknown_towers ?? []).map((t) => ({ name: t.name, rssi: t.rssi, isNew: true })),
  ]);
  let offNow = $derived((live.data?.towers ?? []).filter((t) => !t.live).map((t) => t.name));
  let switching = $state(false);
  async function useSource(kind) {
    switching = true;
    await tryApi(() => api.setSource(kind));
    switching = false;
  }
  /** Signal strength as 0-4 bars. */
  function bars(rssi) {
    if (rssi == null) return 0;
    if (rssi > -55) return 4;
    if (rssi > -65) return 3;
    if (rssi > -75) return 2;
    return 1;
  }

  // ---- step 2: measure ----------------------------------------------------
  let planeHeightFt = $state(3);
  let measuring = $state(false);
  let progress = $state(0); // 0..1 for the progress bar
  let measured = $state(null); // [{name, known, distance (m), rssi}]
  async function measure() {
    measuring = true;
    progress = 0;
    const t0 = performance.now();
    const timer = setInterval(() => (progress = Math.min((performance.now() - t0) / 3000, 1)), 50);
    const res = await tryApi(() => api.measure(3));
    clearInterval(timer);
    progress = 1;
    measuring = false;
    if (!res) return;
    measured = res.towers.filter((t) => t.heard);
    if (measured.length) setupRows();
    else error = 'No towers heard. Check that they are powered on and the plane is connected.';
  }

  // ---- step 3: directions -----------------------------------------------------
  // rows: editable per tower, all in feet / radians
  let rows = $state([]);
  function setupRows() {
    const n = measured.length;
    rows = measured.map((m, i) => {
      const t = towersByName[m.name];
      const heightFt = t ? round1(toFt(t.z)) : 3;
      // Keep a tower's current direction if it has one, otherwise spread them out.
      const hasDir = t && Math.hypot(t.x, t.y) > 0.05;
      const angle = hasDir ? Math.atan2(t.y, t.x) : Math.PI / 2 - (i * 2 * Math.PI) / n;
      return { name: m.name, isNew: !m.known, distFt: round1(toFt(m.distance)), heightFt, angle };
    });
  }
  const round1 = (v) => Math.round(v * 10) / 10;
  const horizFt = (r) => {
    const dz = (+r.heightFt || 0) - (+planeHeightFt || 0);
    return Math.sqrt(Math.max((+r.distFt) ** 2 - dz * dz, 0));
  };
  const compass = (a) => {
    // 0 deg = up (+Y), clockwise, like a compass
    const deg = (((90 - (a * 180) / Math.PI) % 360) + 360) % 360;
    const names = ['up', 'up-right', 'right', 'down-right', 'down', 'down-left', 'left', 'up-left'];
    return `${Math.round(deg)}° (${names[Math.round(deg / 45) % 8]})`;
  };

  // map geometry
  const SIZE = 340;
  const C = SIZE / 2;
  const RPX = 145;
  let maxFt = $derived(Math.max(5, ...rows.map(horizFt)) * 1.1);
  let ringStep = $derived(maxFt > 40 ? 10 : 5);
  const pxOf = (r) => {
    const rr = (horizFt(r) / maxFt) * RPX;
    return [C + rr * Math.cos(r.angle), C - rr * Math.sin(r.angle)];
  };
  let dragging = $state(null);
  let svgEl = $state();
  function onMove(e) {
    if (dragging == null) return;
    const pt = svgEl.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    const p = pt.matrixTransform(svgEl.getScreenCTM().inverse());
    rows[dragging].angle = Math.atan2(-(p.y - C), p.x - C);
  }

  // ---- step 4: save -------------------------------------------------------------
  let saving = $state(false);
  let saved = $state(false);
  async function save() {
    saving = true;
    const ok = await tryApi(async () => {
      for (const r of rows) {
        const h = toM(horizFt(r)); // horizontal distance, meters
        const pos = {
          name: r.name,
          x: Math.round(h * Math.cos(r.angle) * 1000) / 1000,
          y: Math.round(h * Math.sin(r.angle) * 1000) / 1000,
          z: toM(+r.heightFt || 0),
        };
        const old = towersByName[r.name];
        if (old) await api.updateTower(r.name, { ...pos, rssi1m: old.rssi1m, n: old.n }); // keep calibration
        else await api.createTower(pos); // server fills in default calibration
      }
      await api.resetTrack();
      return true;
    });
    saved = !!ok;
    saving = false;
  }

  // Can the user press Next on each step?
  let canNext = $derived(
    [
      heardNow.length > 0 && !onSim, // step 1: real plane, hearing at least one tower
      !!measured?.length && !measuring, // step 2: measured something
      rows.length > 0, // step 3
      true, // step 4
    ][step],
  );

  function onKey(e) {
    if (e.key === 'Escape') onclose();
  }
</script>

<svelte:window onkeydown={onKey} onpointerup={() => (dragging = null)} />

<div class="backdrop" role="presentation" onclick={(e) => e.target === e.currentTarget && onclose()}>
  <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="setup-title">
    <header>
      <h1 id="setup-title">Set up towers</h1>
      <button class="close" onclick={onclose} aria-label="Close">×</button>
    </header>

    <ol class="steps">
      {#each STEPS as s, i}
        <li class:done={i < step} class:current={i === step}>
          <span class="num">{i < step ? '✓' : i + 1}</span>{s}
        </li>
      {/each}
    </ol>

    <div class="body">
      {#if step === 0}
        <h2>Turn everything on and connect the plane</h2>
        <ol class="howto">
          <li>Power on every tower ESP32 (USB power bank or wall plug is fine).</li>
          <li>Connect the plane to this laptop with its USB cable, or turn it on for Bluetooth.</li>
        </ol>

        {#if onSim}
          <div class="callout warn">
            You're looking at the <b>simulator</b>. Switch to your real plane:
            <div class="row">
              <button class="primary" disabled={switching} onclick={() => useSource('serial')}>Use USB cable</button>
              <button disabled={switching} onclick={() => useSource('ble')}>Use Bluetooth</button>
            </div>
          </div>
        {:else}
          <div class="callout" class:ok={fresh} class:warn={!fresh}>
            {#if fresh}
              Plane connected ({link.source === 'serial' ? 'USB' : 'Bluetooth'}).
            {:else}
              Waiting for the plane… <span class="muted">({link?.status ?? 'no status'})</span>
            {/if}
          </div>
        {/if}

        <h3>Towers the plane can hear</h3>
        {#if heardNow.length}
          <ul class="heard">
            {#each heardNow as t}
              <li>
                <b>{t.name}</b>
                <span class="bars" title={`${t.rssi?.toFixed(0)} dBm`}>
                  {#each [1, 2, 3, 4] as b}<i class:on={bars(t.rssi) >= b} style={`height:${b * 4 + 2}px`}></i>{/each}
                </span>
                {#if t.isNew}<span class="tag">new</span>{/if}
              </li>
            {/each}
          </ul>
        {:else}
          <p class="muted">None yet. Towers show up here within a few seconds of turning on.</p>
        {/if}
        {#if offNow.length && !onSim}
          <p class="muted small">Not heard: {offNow.join(', ')}. If one isn't built yet, you can ignore it.</p>
        {/if}
        {#if heardNow.length > 0 && heardNow.length < 3 && !onSim}
          <p class="note">You need at least <b>3 towers</b> to locate the plane. You can continue now and add more later.</p>
        {/if}
      {:else if step === 1}
        <h2>Put the plane at the center spot</h2>
        <div class="split">
          <svg class="diagram" viewBox="0 0 200 150" aria-hidden="true">
            <rect x="8" y="8" width="184" height="134" rx="6" class="room" />
            <line x1="100" y1="20" x2="100" y2="130" class="axis" />
            <line x1="20" y1="75" x2="180" y2="75" class="axis" />
            <circle cx="100" cy="75" r="22" class="ring" />
            <circle cx="100" cy="75" r="44" class="ring" />
            {#each [[40, 30], [165, 40], [150, 125], [30, 115]] as [x, y], i}
              <g><circle cx={x} cy={y} r="6" class="tw" /><text x={x} y={y - 10}>{'ABCD'[i]}</text></g>
            {/each}
            <g transform="translate(100 75)"><path d="M-9 0h18M0-3v6M-3 6h6" class="pl" /></g>
            <text x="108" y="70" class="lbl">0,0</text>
          </svg>
          <ol class="howto">
            <li>Pick the spot that should be <b>(0, 0)</b>, for example the middle of the room. Everything is measured from here.</li>
            <li>
              Put the plane there at the height it usually sits:
              <label class="inline">Plane height <input type="number" step="0.5" min="0" bind:value={planeHeightFt} /> ft</label>
            </li>
            <li>Keep it still, and don't stand between the plane and the towers.</li>
          </ol>
        </div>

        <div class="measure">
          <button class="primary big" disabled={measuring} onclick={measure}>
            {measuring ? 'Measuring… hold still' : measured ? 'Measure again' : 'Measure distances'}
          </button>
          {#if measuring || progress > 0}
            <div class="progress"><div style={`width:${progress * 100}%`}></div></div>
          {/if}
        </div>

        {#if measured?.length}
          <table class="results">
            <thead><tr><th>Tower</th><th>Distance from plane</th><th>Signal</th></tr></thead>
            <tbody>
              {#each measured as m}
                <tr>
                  <td><b>{m.name}</b>{#if !m.known} <span class="tag">new</span>{/if}</td>
                  <td>{fmtFt(m.distance)}</td>
                  <td>{m.rssi.toFixed(0)} dBm</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      {:else if step === 2}
        <h2>Drag each tower to where it really is</h2>
        <p class="lead">
          Each tower sits on a circle at its measured distance. The plane only knows <i>how far</i>, not <i>which way</i>, so drag
          each dot around its circle to match your room. Choose a wall to be the <b>top</b> of this map.
        </p>
        <div class="split">
          <svg
            bind:this={svgEl}
            class="map"
            viewBox={`0 0 ${SIZE} ${SIZE}`}
            onpointermove={onMove}
            role="application"
            aria-label="Tower direction map"
          >
            {#each Array.from({ length: Math.floor(maxFt / ringStep) }, (_, i) => (i + 1) * ringStep) as ft}
              <circle cx={C} cy={C} r={(ft / maxFt) * RPX} class="grid" />
              <text x={C + 3} y={C - (ft / maxFt) * RPX - 3} class="gridlbl">{ft} ft</text>
            {/each}
            <line x1={C} y1="8" x2={C} y2={SIZE - 8} class="axis" />
            <line x1="8" y1={C} x2={SIZE - 8} y2={C} class="axis" />
            <text x={C} y="18" class="dir">top (+Y)</text>
            <text x={SIZE - 10} y={C - 6} class="dir end">+X</text>

            {#each rows as r, i}
              {@const [x, y] = pxOf(r)}
              <circle cx={C} cy={C} r={(horizFt(r) / maxFt) * RPX} class="track" class:active={dragging === i} />
              <line x1={C} y1={C} x2={x} y2={y} class="spoke" />
              <g
                class="handle"
                class:active={dragging === i}
                role="slider"
                tabindex="0"
                aria-label={`Tower ${r.name} direction`}
                aria-valuenow={Math.round((r.angle * 180) / Math.PI)}
                onpointerdown={(e) => {
                  dragging = i;
                  e.currentTarget.setPointerCapture?.(e.pointerId);
                }}
                onkeydown={(e) => {
                  if (e.key === 'ArrowLeft') rows[i].angle += 0.05;
                  if (e.key === 'ArrowRight') rows[i].angle -= 0.05;
                }}
              >
                <circle cx={x} cy={y} r="13" />
                <text {x} y={y + 4}>{r.name}</text>
              </g>
            {/each}
            <g transform={`translate(${C} ${C})`} class="plane">
              <circle r="7" />
              <text y="22">plane</text>
            </g>
          </svg>

          <table class="results edit">
            <thead><tr><th>Tower</th><th title="Edit if you measured with a tape">Distance ft</th><th>Height ft</th><th>Direction</th></tr></thead>
            <tbody>
              {#each rows as r}
                <tr>
                  <td><b>{r.name}</b></td>
                  <td><input type="number" step="0.5" min="0" bind:value={r.distFt} /></td>
                  <td><input type="number" step="0.5" min="0" bind:value={r.heightFt} /></td>
                  <td class="muted small">{compass(r.angle)}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        <p class="muted small">
          Tip: if you tape-measured a distance, type it in. That's more accurate than the radio estimate. Height is the antenna's
          height off the floor.
        </p>
      {:else}
        {#if !saved}
          <h2>Save the layout</h2>
          <p class="lead">
            This moves {rows.length} tower{rows.length === 1 ? '' : 's'} on the map: {rows.map((r) => r.name).join(', ')}. Towers
            the plane didn't hear stay where they are.
          </p>
          <button class="primary big" disabled={saving} onclick={save}>{saving ? 'Saving…' : 'Save tower layout'}</button>
        {:else}
          <h2>Towers are set up</h2>
          <ol class="howto">
            <li>Pick up the plane and walk it around. The plane in the 3D view should follow you.</li>
            <li>If it's off in one direction, a tower is probably pointed wrong. Run this again or drag it on the map.</li>
            <li>
              For better accuracy, select a tower in the panel on the right and use <b>Calibrate</b> at 3 ft and 15 ft.
            </li>
          </ol>
          {#if rows.length < 3}
            <p class="note">Only {rows.length} tower{rows.length === 1 ? '' : 's'} set up. You'll see a position once a third tower is added.</p>
          {/if}
        {/if}
      {/if}

      {#if error}<p class="err" role="alert">{error}</p>{/if}
    </div>

    <footer>
      <button onclick={() => (step === 0 ? onclose() : (step -= 1))} disabled={saving || measuring}>
        {step === 0 ? 'Cancel' : 'Back'}
      </button>
      {#if step < 3}
        <button class="primary" disabled={!canNext} onclick={() => (step += 1)}>Next</button>
      {:else if saved}
        <button class="primary" onclick={onclose}>Done</button>
      {/if}
    </footer>
  </div>
</div>

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    background: rgba(5, 8, 12, 0.6);
    display: grid;
    place-items: center;
    z-index: 100;
    padding: 16px;
  }
  .dialog {
    width: min(760px, 100%);
    max-height: calc(100vh - 32px);
    display: flex;
    flex-direction: column;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 12px;
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
  }
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 18px 0;
  }
  h1 {
    font-size: 18px;
    margin: 0;
  }
  .close {
    border: none;
    background: none;
    font-size: 22px;
    line-height: 1;
    color: var(--muted);
  }
  .steps {
    display: flex;
    gap: 6px;
    list-style: none;
    margin: 12px 18px 0;
    padding: 0 0 12px;
    border-bottom: 1px solid var(--border);
    flex-wrap: wrap;
  }
  .steps li {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--muted);
    font-size: 13px;
    padding-right: 10px;
  }
  .steps li:not(:last-child)::after {
    content: '';
    width: 22px;
    height: 1px;
    background: var(--border);
    margin-left: 4px;
  }
  .num {
    width: 22px;
    height: 22px;
    border-radius: 50%;
    display: grid;
    place-items: center;
    border: 1px solid var(--border);
    font-size: 12px;
  }
  .steps li.current {
    color: var(--text);
    font-weight: 600;
  }
  .steps li.current .num {
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
  }
  .steps li.done .num {
    background: #1d3b26;
    border-color: var(--ok);
    color: var(--ok);
  }
  .body {
    padding: 14px 18px;
    overflow-y: auto;
  }
  h2 {
    font-size: 16px;
    margin: 0 0 10px;
    text-transform: none;
    letter-spacing: 0;
    color: var(--text);
  }
  h3 {
    font-size: 13px;
    color: var(--muted);
    margin: 16px 0 6px;
    font-weight: 600;
  }
  .lead {
    color: #c9d1d9;
    margin: 0 0 12px;
    line-height: 1.45;
  }
  .howto {
    margin: 0 0 12px;
    padding-left: 20px;
    line-height: 1.6;
    color: #c9d1d9;
  }
  .howto li {
    margin-bottom: 4px;
  }
  .callout {
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px 12px;
    margin: 8px 0;
  }
  .callout.ok {
    border-color: var(--ok);
    background: #12261a;
  }
  .callout.warn {
    border-color: var(--warn);
    background: #2a2210;
  }
  .row {
    display: flex;
    gap: 8px;
    margin-top: 8px;
  }
  .heard {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .heard li {
    display: flex;
    align-items: center;
    gap: 8px;
    background: var(--panel-2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 6px 10px;
  }
  .bars {
    display: inline-flex;
    align-items: flex-end;
    gap: 2px;
    height: 18px;
  }
  .bars i {
    width: 4px;
    background: #3a434f;
    border-radius: 1px;
  }
  .bars i.on {
    background: var(--ok);
  }
  .tag {
    font-size: 10px;
    text-transform: uppercase;
    background: #1f2d3f;
    color: var(--accent);
    border-radius: 4px;
    padding: 1px 5px;
  }
  .muted {
    color: var(--muted);
  }
  .small {
    font-size: 12px;
  }
  .note {
    color: var(--warn);
    font-size: 13px;
  }
  .err {
    color: var(--bad);
  }
  .split {
    display: flex;
    gap: 16px;
    align-items: flex-start;
    flex-wrap: wrap;
  }
  .split > * {
    flex: 1 1 260px;
  }
  .diagram {
    max-width: 260px;
  }
  .diagram .room {
    fill: #0f141a;
    stroke: var(--border);
  }
  .diagram .axis,
  .map .axis {
    stroke: #2a3340;
    stroke-dasharray: 3 3;
  }
  .diagram .ring {
    fill: none;
    stroke: #2a3340;
  }
  .diagram .tw {
    fill: var(--tower);
  }
  .diagram text {
    fill: var(--muted);
    font-size: 9px;
    text-anchor: middle;
  }
  .diagram .lbl {
    text-anchor: start;
    fill: var(--accent);
  }
  .diagram .pl {
    stroke: var(--accent);
    stroke-width: 2.5;
    stroke-linecap: round;
    fill: none;
  }
  label.inline {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    margin-top: 4px;
    color: var(--muted);
  }
  label.inline input {
    width: 70px;
  }
  .measure {
    display: flex;
    align-items: center;
    gap: 12px;
    margin: 6px 0 12px;
  }
  button.big {
    padding: 8px 16px;
    font-size: 14px;
  }
  .progress {
    flex: 1;
    height: 6px;
    background: var(--panel-2);
    border-radius: 3px;
    overflow: hidden;
  }
  .progress div {
    height: 100%;
    background: var(--accent);
    transition: width 0.05s linear;
  }
  .results {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }
  .results th {
    text-align: left;
    color: var(--muted);
    font-weight: 500;
    padding: 4px 6px;
    border-bottom: 1px solid var(--border);
  }
  .results td {
    padding: 5px 6px;
    border-bottom: 1px solid var(--border);
  }
  .results.edit input {
    width: 70px;
  }
  .map {
    max-width: 340px;
    background: #0d1117;
    border: 1px solid var(--border);
    border-radius: 8px;
    touch-action: none;
    user-select: none;
  }
  .map .grid {
    fill: none;
    stroke: #1f2630;
  }
  .map .gridlbl {
    fill: #4b5563;
    font-size: 9px;
  }
  .map .dir {
    fill: var(--muted);
    font-size: 10px;
    text-anchor: middle;
  }
  .map .dir.end {
    text-anchor: end;
  }
  .map .track {
    fill: none;
    stroke: var(--tower);
    stroke-opacity: 0.35;
    stroke-dasharray: 4 4;
  }
  .map .track.active {
    stroke-opacity: 0.9;
  }
  .map .spoke {
    stroke: var(--tower);
    stroke-opacity: 0.25;
  }
  .map .handle {
    cursor: grab;
  }
  .map .handle circle {
    fill: var(--tower);
    stroke: #fff;
    stroke-width: 1.5;
  }
  .map .handle.active circle {
    fill: #ffb070;
  }
  .map .handle text {
    fill: #111;
    font-size: 11px;
    font-weight: 700;
    text-anchor: middle;
    pointer-events: none;
  }
  .map .handle:focus-visible circle {
    stroke: var(--accent);
    stroke-width: 3;
  }
  .map .plane circle {
    fill: var(--accent);
  }
  .map .plane text {
    fill: var(--accent);
    font-size: 10px;
    text-anchor: middle;
  }
  footer {
    display: flex;
    justify-content: space-between;
    padding: 12px 18px;
    border-top: 1px solid var(--border);
  }
</style>
