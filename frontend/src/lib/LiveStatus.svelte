<script>
  import { live } from './live.svelte.js';
  import { api } from './api.js';
  import { fmtFt } from './units.js';

  let { run } = $props();

  let d = $derived(live.data);
  let est = $derived(d?.estimate);
  let truthErr = $derived(
    Array.isArray(d?.truth) && Array.isArray(est?.position)
      ? Math.hypot(...est.position.map((v, i) => v - d.truth[i]))
      : null,
  );
  const fmt = (v, n = 1) => (v == null ? '—' : Number(v).toFixed(n));
</script>

<section>
  <h2>Link</h2>
  {#if !live.connected}
    <p class="bad">Server not reachable. Start it with <code>python -m app.main</code> in <code>server/</code>.</p>
  {:else if d}
    <div class="kv mono">
      <span>Source</span><span>{d.link.source}</span>
      <span>Status</span><span>{d.link.status}</span>
      <span>Last report</span>
      <span class:bad={d.link.last_report_age == null || d.link.last_report_age > 2}>
        {d.link.last_report_age == null ? 'never' : `${fmt(d.link.last_report_age)} s ago`}
      </span>
      <span>Reports</span><span>{d.link.reports}</span>
    </div>
  {/if}
</section>

<section>
  <h2>Estimate</h2>
  {#if est?.ok && est.position}
    <div class="kv mono">
      <span>Position</span><span>{est.position.map((v) => fmtFt(v, 1, false)).join(', ')} ft</span>
      <span>Error ±</span><span>{fmtFt(est.error_radius)}</span>
      <span>Residual</span><span>{fmtFt(est.residual_rms)}</span>
      <span>Speed</span><span>{fmtFt(Math.hypot(...est.velocity), 1, false)} ft/s</span>
      <span>Towers</span><span>{est.used.join(' ')}</span>
      {#if truthErr != null}<span>vs truth</span><span>{fmtFt(truthErr)}</span>{/if}
    </div>
  {:else}
    <p class="muted">{est?.reason || 'No fix yet'}</p>
  {/if}
</section>

<section>
  <h2>Tower readings</h2>
  <table class="mono">
    <thead><tr><th>Tower</th><th>RSSI</th><th>±dB</th><th>Dist ft</th><th>Age</th></tr></thead>
    <tbody>
      {#each d?.towers ?? [] as t (t.name)}
        <tr class:stale={!t.live}>
          <td>{t.name}</td>
          <td>{fmt(t.live?.rssi)}</td>
          <td>{fmt(t.live?.std)}</td>
          <td>{fmtFt(t.live?.distance, 1, false)}</td>
          <td>{t.live ? fmt(t.live.age) : 'off'}</td>
        </tr>
      {/each}
    </tbody>
  </table>
  {#if d?.unknown_towers?.length}
    <p class="warn">Heard but not placed:</p>
    {#each d.unknown_towers as u}
      <div class="unknown mono">
        <span>{u.name} · {fmt(u.rssi)} dBm</span>
        <button onclick={() => run(() => api.createTower({ name: u.name, x: 0, y: 0 }))}>Add</button>
      </div>
    {/each}
  {/if}
</section>

<section>
  <h2>Plane sensors</h2>
  {#if d?.sensors && Object.keys(d.sensors).length}
    <div class="kv mono">
      {#each Object.entries(d.sensors) as [k, v]}
        <span>{k}</span><span>{typeof v === 'object' ? JSON.stringify(v) : v}</span>
      {/each}
    </div>
  {:else}
    <p class="muted">None reported</p>
  {/if}
</section>

<style>
  .kv {
    display: grid;
    grid-template-columns: 90px 1fr;
    gap: 3px 10px;
    font-size: 12px;
  }
  .kv span:nth-child(odd) {
    color: var(--muted);
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
  }
  th {
    text-align: left;
    color: var(--muted);
    font-weight: 500;
    padding: 3px 4px;
    border-bottom: 1px solid var(--border);
  }
  td {
    padding: 3px 4px;
  }
  tr.stale {
    color: var(--muted);
  }
  .muted {
    color: var(--muted);
    font-size: 12px;
    margin: 0;
  }
  .bad {
    color: var(--bad);
  }
  .warn {
    color: var(--warn);
    font-size: 12px;
    margin: 8px 0 4px;
  }
  .unknown {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 12px;
    margin-bottom: 4px;
  }
  p {
    font-size: 12px;
  }
  code {
    background: var(--panel-2);
    padding: 0 4px;
    border-radius: 3px;
  }
</style>
