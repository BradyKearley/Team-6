<script>
  // Calibration sweep: put all towers together, record every tower's signal with the
  // plane at several known distances, fit each tower's curve, then Apply.
  // The recorded points live on the server (server/sweep.json), so a reload keeps them.
  import { live } from './live.svelte.js';
  import { api } from './api.js';
  import { toFt, toM } from './units.js';

  let { onclose } = $props();

  let data = $state({ points: [], fits: {}, spread_warn_db: 4, min_fit_distance_m: 0.95 });
  // Closer than ~1 m the radio saturates, so those points are shown but not used in the fit.
  const tooClose = (meters) => meters < data.min_fit_distance_m;
  let error = $state('');
  let message = $state('');

  /** Run an API call; on failure show its message and return undefined. */
  async function tryApi(fn) {
    error = '';
    try {
      return await fn();
    } catch (e) {
      error = e.message;
    }
  }

  $effect(() => {
    tryApi(api.sweep).then((r) => r && (data = r));
  });

  // ---- distance plan (feet, like the rest of the UI) --------------------------------
  // Defaults are 1, 2, 3 and 5 m. (Closer than 1 m reads the same as 1 m, so it isn't useful.)
  let plan = $state([3.3, 6.6, 9.8, 16.4]);
  let selected = $state(0); // index into plan
  let newDistFt = $state('');

  const recordedMeters = $derived([...new Set(data.points.map((p) => p.distance))]);
  const isRecorded = (ft) => recordedMeters.some((m) => Math.abs(m - toM(ft)) < 0.02);

  function addDistance() {
    const ft = +newDistFt;
    if (!(ft > 0)) return;
    plan = [...plan, ft].sort((a, b) => a - b);
    selected = plan.indexOf(ft);
    newDistFt = '';
  }
  function removeDistance(i) {
    plan = plan.filter((_, j) => j !== i);
    selected = Math.min(selected, plan.length - 1);
  }

  // ---- record ------------------------------------------------------------------------
  const RECORD_SECONDS = 6;
  let recording = $state(false);
  let progress = $state(0);
  async function record() {
    const ft = plan[selected];
    if (ft == null) return;
    recording = true;
    message = '';
    const t0 = performance.now();
    const timer = setInterval(() => (progress = Math.min((performance.now() - t0) / (RECORD_SECONDS * 1000), 1)), 50);
    const res = await tryApi(() => api.sweepRecord(toM(ft), RECORD_SECONDS));
    clearInterval(timer);
    progress = 0;
    recording = false;
    if (!res) return;
    data = res;
    // move on to the next distance that hasn't been recorded yet
    const next = plan.findIndex((d) => !isRecorded(d));
    if (next >= 0) selected = next;
  }

  async function undo() {
    const r = await tryApi(api.sweepUndo);
    if (r) data = r;
  }
  async function clearAll() {
    if (!confirm('Delete all recorded sweep points?')) return;
    const r = await tryApi(api.sweepClear);
    if (r) data = r;
  }
  async function apply() {
    const r = await tryApi(api.sweepApply);
    if (r) message = r.applied.length ? `Saved to towers: ${r.applied.map((a) => `${a.name} (${a.rssi1m} dBm, n ${a.n})`).join(', ')}` : 'No towers had a usable fit yet.';
  }

  // ---- table: one row per Record press, one column per tower ---------------------------
  let towerNames = $derived(
    [...new Set([...data.points.map((p) => p.tower), ...(live.data?.towers ?? []).map((t) => t.name)])].sort(),
  );
  let rows = $derived.by(() => {
    const byBatch = {};
    for (const p of data.points) (byBatch[p.batch] ??= { batch: p.batch, distance: p.distance, cells: {} }).cells[p.tower] = p;
    return Object.values(byBatch).sort((a, b) => a.distance - b.distance || a.batch - b.batch);
  });
  let lastBatch = $derived(Math.max(0, ...data.points.map((p) => p.batch)));

  // Live signal right now, so you can see every tower is on before recording.
  let liveRssi = $derived(
    Object.fromEntries([
      ...(live.data?.towers ?? []).filter((t) => t.live).map((t) => [t.name, t.live.rssi]),
      ...(live.data?.unknown_towers ?? []).map((t) => [t.name, t.rssi]),
    ]),
  );
  let onSim = $derived(live.data?.link?.source === 'sim');

  // ---- mini chart per tower ---------------------------------------------------------------
  const CW = 170;
  const CH = 90;
  function chart(name) {
    const pts = data.points.filter((p) => p.tower === name && p.rssi != null);
    if (!pts.length) return null;
    const fit = data.fits[name];
    const ds = pts.map((p) => p.distance);
    const lo = Math.log10(Math.min(...ds) * 0.8);
    const hi = Math.log10(Math.max(...ds) * 1.25);
    const curve = (d) => fit.rssi1m - 10 * fit.n * Math.log10(d);
    const ys = pts.map((p) => p.rssi);
    if (fit?.ok) ys.push(curve(10 ** lo), curve(10 ** hi));
    const yMin = Math.min(...ys) - 2;
    const yMax = Math.max(...ys) + 2;
    const sx = (d) => 6 + ((Math.log10(d) - lo) / (hi - lo || 1)) * (CW - 12);
    const sy = (r) => 6 + ((yMax - r) / (yMax - yMin || 1)) * (CH - 12);
    const line = fit?.ok
      ? Array.from({ length: 24 }, (_, i) => 10 ** (lo + ((hi - lo) * i) / 23))
          .map((d) => `${sx(d).toFixed(1)},${sy(curve(d)).toFixed(1)}`)
          .join(' ')
      : null;
    return {
      dots: pts.map((p) => ({
        x: sx(p.distance),
        y: sy(p.rssi),
        warn: p.spread > data.spread_warn_db,
        unused: tooClose(p.distance),
      })),
      line,
    };
  }

  const ft1 = (m) => Math.round(toFt(m) * 10) / 10;
  function onKey(e) {
    if (e.key === 'Escape' && !recording) onclose();
  }
</script>

<svelte:window onkeydown={onKey} />

<div class="backdrop" role="presentation" onclick={(e) => e.target === e.currentTarget && !recording && onclose()}>
  <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="sweep-title">
    <header>
      <h1 id="sweep-title">Calibration sweep</h1>
      <button class="close" onclick={onclose} disabled={recording} aria-label="Close">×</button>
    </header>

    <div class="body">
      <ol class="howto">
        <li>Put <b>all towers together</b> in one spot, powered on, at their usual height.</li>
        <li>Put the plane at the distance selected below (measure from the towers), <b>step back</b>, and press <b>Record</b>. It listens for {RECORD_SECONDS} seconds.</li>
        <li>Repeat for each distance (1 m and farther; closer than that the signal saturates). Then press <b>Apply</b>.</li>
      </ol>

      {#if onSim}
        <p class="callout warn">You're on the simulator. Switch the data source to USB or BLE in the sidebar to calibrate real towers.</p>
      {/if}

      <div class="live">
        <span class="muted">Hearing now:</span>
        {#each towerNames as n}
          <span class="pill" class:off={liveRssi[n] == null}>{n} {liveRssi[n] != null ? `${liveRssi[n].toFixed(0)} dBm` : 'off'}</span>
        {:else}
          <span class="muted">no towers</span>
        {/each}
      </div>

      <h2>Distances</h2>
      <div class="chips">
        {#each plan as ft, i}
          <span class="chip" class:sel={i === selected} class:done={isRecorded(ft)}>
            <button class="pick" onclick={() => (selected = i)} disabled={recording}>
              {#if isRecorded(ft)}✓ {/if}{ft} ft <span class="muted">({toM(ft).toFixed(2)} m)</span>
              {#if tooClose(toM(ft))}<span class="close-tag" title="Under 1 m: recorded but not used in the fit">too close</span>{/if}
            </button>
            <button class="x" title="Remove distance" onclick={() => removeDistance(i)} disabled={recording}>×</button>
          </span>
        {/each}
        <span class="add">
          <input type="number" min="0.1" step="0.5" placeholder="ft" bind:value={newDistFt} onkeydown={(e) => e.key === 'Enter' && addDistance()} />
          <button onclick={addDistance} disabled={recording}>Add</button>
        </span>
      </div>

      <div class="recordbar">
        <button class="primary big" onclick={record} disabled={recording || plan[selected] == null}>
          {recording ? 'Recording… stay back' : `Record at ${plan[selected] ?? '?'} ft`}
        </button>
        {#if recording}<div class="progress"><div style={`width:${progress * 100}%`}></div></div>{/if}
      </div>

      {#if rows.length}
        <h2>Recorded signal (dBm)</h2>
        <div class="tablewrap">
          <table>
            <thead>
              <tr><th>Distance</th>{#each towerNames as n}<th>{n}</th>{/each}</tr>
            </thead>
            <tbody>
              {#each rows as row (row.batch)}
                <tr class:last={row.batch === lastBatch} class:unused={tooClose(row.distance)}>
                  <td>{ft1(row.distance)} ft{#if tooClose(row.distance)} <small>(not fitted)</small>{/if}</td>
                  {#each towerNames as n}
                    {@const p = row.cells[n]}
                    {#if !p}
                      <td class="muted">·</td>
                    {:else if p.rssi == null}
                      <td class="bad" title="Not heard during this recording">not heard</td>
                    {:else}
                      <td class:warn={p.spread > data.spread_warn_db} title={`spread ${p.spread.toFixed(1)} dB, ${p.samples} readings`}>
                        {p.rssi.toFixed(1)}{#if p.spread > data.spread_warn_db}<small> ±{(p.spread / 2).toFixed(0)}</small>{/if}
                      </td>
                    {/if}
                  {/each}
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        <p class="muted small">
          Amber = readings jumped around (over {data.spread_warn_db} dB). Use Undo last and record that distance again.
          Points under 1 m are greyed out and left out of the fit: that close, every tower reads about the same as at 1 m.
        </p>

        <h2>Fit per tower</h2>
        <div class="fits">
          {#each Object.entries(data.fits) as [name, fit] (name)}
            {@const c = chart(name)}
            <div class="fit">
              <div class="fithead">
                <b>{name}</b>
                <span class="q {fit.quality.replace(' ', '-')}">{fit.quality}</span>
              </div>
              {#if c}
                <svg viewBox={`0 0 ${CW} ${CH}`} class="chart" aria-label={`Tower ${name} signal vs distance`}>
                  {#if c.line}<polyline points={c.line} class="curve" />{/if}
                  {#each c.dots as d}<circle cx={d.x} cy={d.y} r="3" class:warn={d.warn} class:unused={d.unused} />{/each}
                </svg>
              {/if}
              {#if fit.ok}
                <div class="nums">
                  <span>RSSI@1m <b>{fit.rssi1m.toFixed(1)}</b></span>
                  <span>n <b>{fit.n.toFixed(2)}</b></span>
                  <span>error <b>{fit.rms.toFixed(1)} dB</b></span>
                </div>
              {/if}
              <p class="note">{fit.note}</p>
            </div>
          {/each}
        </div>
      {/if}

      {#if message}<p class="ok" role="status">{message}</p>{/if}
      {#if error}<p class="err" role="alert">{error}</p>{/if}
    </div>

    <footer>
      <div class="row">
        <button onclick={undo} disabled={recording || !rows.length}>Undo last</button>
        <button class="danger" onclick={clearAll} disabled={recording || !rows.length}>Clear</button>
      </div>
      <div class="row">
        <button onclick={onclose} disabled={recording}>Close</button>
        <button class="primary" onclick={apply} disabled={recording || !Object.values(data.fits).some((f) => f.ok)}>Apply to towers</button>
      </div>
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
    width: min(820px, 100%);
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
    padding: 14px 18px 10px;
    border-bottom: 1px solid var(--border);
  }
  h1 {
    font-size: 18px;
    margin: 0;
  }
  h2 {
    font-size: 13px;
    margin: 16px 0 8px;
  }
  .close {
    border: none;
    background: none;
    font-size: 22px;
    line-height: 1;
    color: var(--muted);
  }
  .body {
    padding: 12px 18px;
    overflow-y: auto;
  }
  .howto {
    margin: 0 0 10px;
    padding-left: 20px;
    line-height: 1.6;
    color: #c9d1d9;
  }
  .callout.warn {
    border: 1px solid var(--warn);
    background: #2a2210;
    border-radius: 8px;
    padding: 8px 12px;
  }
  .live {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    align-items: center;
    font-size: 12px;
  }
  .pill {
    background: #12261a;
    border: 1px solid var(--ok);
    border-radius: 999px;
    padding: 1px 8px;
  }
  .pill.off {
    background: #2a1416;
    border-color: var(--bad);
    color: #ffb3b0;
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    align-items: center;
  }
  .chip {
    display: inline-flex;
    border: 1px solid var(--border);
    border-radius: 999px;
    overflow: hidden;
  }
  .chip button {
    border: none;
    border-radius: 0;
    background: var(--panel-2);
    padding: 4px 10px;
    font-size: 12px;
  }
  .chip .x {
    padding: 4px 8px;
    color: var(--muted);
  }
  .chip.done .pick {
    color: var(--ok);
  }
  .chip.sel {
    border-color: var(--accent);
  }
  .chip.sel .pick {
    background: var(--accent);
    color: #fff;
  }
  .chip.sel .pick .muted {
    color: #dbe9ff;
  }
  .add {
    display: inline-flex;
    gap: 4px;
  }
  .add input {
    width: 64px;
  }
  .recordbar {
    display: flex;
    gap: 12px;
    align-items: center;
    margin-top: 12px;
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
  }
  .tablewrap {
    overflow-x: auto;
  }
  table {
    border-collapse: collapse;
    font-size: 12px;
    font-variant-numeric: tabular-nums;
    min-width: 100%;
  }
  th,
  td {
    padding: 4px 10px;
    border-bottom: 1px solid var(--border);
    text-align: right;
    white-space: nowrap;
  }
  th:first-child,
  td:first-child {
    text-align: left;
  }
  th {
    color: var(--muted);
    font-weight: 500;
  }
  tr.last td {
    background: #15202d;
  }
  td.warn {
    color: var(--warn);
  }
  td.bad {
    color: var(--bad);
  }
  .fits {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
    gap: 10px;
  }
  .fit {
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 8px 10px;
    background: var(--panel-2);
  }
  .fithead {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 4px;
  }
  .q {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    border-radius: 4px;
    padding: 1px 6px;
  }
  .q.good {
    background: #12261a;
    color: var(--ok);
  }
  .q.ok {
    background: #2a2210;
    color: var(--warn);
  }
  .q.bad,
  .q.weak {
    background: #2a1416;
    color: var(--bad);
  }
  .q.need-more {
    background: #1f2630;
    color: var(--muted);
  }
  .chart {
    width: 100%;
    height: auto;
    background: #0d1117;
    border-radius: 4px;
  }
  .chart .curve {
    fill: none;
    stroke: var(--accent);
    stroke-width: 1.5;
  }
  .chart circle {
    fill: var(--tower);
  }
  .chart circle.warn {
    fill: var(--warn);
    stroke: #fff;
    stroke-width: 0.8;
  }
  .chart circle.unused {
    fill: none;
    stroke: var(--muted);
    stroke-width: 1;
  }
  tr.unused td {
    color: var(--muted);
  }
  .close-tag {
    font-size: 10px;
    color: var(--warn);
    margin-left: 4px;
  }
  .nums {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 10px;
    font-size: 11px;
    color: var(--muted);
    margin-top: 4px;
  }
  .nums b {
    color: var(--text);
  }
  .note {
    font-size: 11px;
    color: #c9d1d9;
    margin: 4px 0 0;
  }
  .muted {
    color: var(--muted);
  }
  .small {
    font-size: 11px;
  }
  .ok {
    color: var(--ok);
  }
  .err {
    color: var(--bad);
  }
  footer {
    display: flex;
    justify-content: space-between;
    padding: 12px 18px;
    border-top: 1px solid var(--border);
  }
  .row {
    display: flex;
    gap: 8px;
  }
</style>
