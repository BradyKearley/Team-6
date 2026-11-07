<script>
  import { api } from './api.js';
  import { live } from './live.svelte.js';
  import { toFt, toM, fmtFt } from './units.js';
  import { nextTowerName } from './towers.js';

  let { towers, selected = $bindable(), run } = $props();

  // The editor works in feet (rounded to 0.01 ft); the server stores meters.
  const ft = (m) => Math.round(toFt(m) * 100) / 100;
  const toDraft = (t) => ({ name: t.name, x: ft(t.x), y: ft(t.y), z: ft(t.z), rssi1m: t.rssi1m, n: t.n });

  let draft = $state(null); // editable copy of the selected tower (feet)
  let draftFor = $state(null);
  $effect(() => {
    const t = towers.find((x) => x.name === selected);
    if (!t) {
      draft = null;
      draftFor = null;
    } else if (draftFor !== selected) {
      draft = toDraft(t);
      draftFor = selected;
    } else if (draft && !editing) {
      // follow drags / server updates while not typing
      const { name: _, ...rest } = toDraft(t);
      Object.assign(draft, rest);
    }
  });
  let editing = $state(false);

  function revert() {
    const t = towers.find((x) => x.name === draftFor);
    if (t) draft = toDraft(t);
    editing = false;
  }

  let calDist = $state(3); // feet
  let calBusy = $state(false);
  let calMsg = $state('');

  async function save() {
    const body = {
      name: draft.name,
      x: toM(+draft.x),
      y: toM(+draft.y),
      z: toM(+draft.z),
      rssi1m: +draft.rssi1m,
      n: +draft.n,
    };
    const res = await run(() => api.updateTower(draftFor, body));
    if (res) {
      selected = res.name;
      draftFor = res.name;
      editing = false;
    }
  }

  async function remove(name) {
    if (!confirm(`Delete tower ${name}?`)) return;
    await run(() => api.deleteTower(name));
    if (selected === name) selected = null;
  }

  async function calibrate() {
    calBusy = true;
    calMsg = `Hold the plane ${calDist} ft from tower ${draftFor}… measuring 3 s`;
    const res = await run(() => api.calibrate(draftFor, toM(+calDist), 3));
    calBusy = false;
    if (res) {
      calMsg = `RSSI ${res.measured_rssi.toFixed(1)} dBm (${res.samples} samples, ${res.points.length} pts) → rssi1m ${res.tower.rssi1m.toFixed(1)}, n ${res.tower.n.toFixed(2)}`;
      editing = false;
    } else calMsg = '';
  }

  async function addAtOrigin() {
    const t = await run(() => api.createTower({ name: nextTowerName(towers), x: 0, y: 0 }));
    if (t) selected = t.name;
  }
</script>

<section>
  <div class="head">
    <h2>Towers ({towers.length})</h2>
    <button onclick={addAtOrigin}>+ Add</button>
  </div>
  {#if live.data?.link?.source === 'sim'}
    <p class="simnote">
      Simulator layout. It resets every time you switch to Sim, and changes here don't touch your real towers.
    </p>
  {/if}

  <table class="mono">
    <thead>
      <tr><th>Name</th><th>x ft</th><th>y ft</th><th>z ft</th><th>Range</th><th></th></tr>
    </thead>
    <tbody>
      {#each towers as t (t.name)}
        <tr class:sel={t.name === selected} onclick={() => (selected = t.name)}>
          <td>
            <span class="dot" class:used={t.used} class:live={t.live && !t.used}></span>{t.name}
          </td>
          <td>{fmtFt(t.x, 1, false)}</td>
          <td>{fmtFt(t.y, 1, false)}</td>
          <td>{fmtFt(t.z, 1, false)}</td>
          <td>{t.live ? fmtFt(t.live.distance) : '—'}</td>
          <td>
            <button
              class="danger small"
              title="Delete"
              onclick={(e) => {
                e.stopPropagation();
                remove(t.name);
              }}>✕</button
            >
          </td>
        </tr>
      {:else}
        <tr><td colspan="6" class="empty">No towers yet. Use “Place tower” on the grid.</td></tr>
      {/each}
    </tbody>
  </table>

  {#if draft}
    <form
      class="editor"
      oninput={() => (editing = true)}
      onsubmit={(e) => {
        e.preventDefault();
        save();
      }}
    >
      <h2>Edit tower {draftFor}</h2>
      <div class="grid">
        <label>Name<input bind:value={draft.name} maxlength="7" /></label>
        <label>x (ft)<input type="number" step="0.5" bind:value={draft.x} /></label>
        <label>y (ft)<input type="number" step="0.5" bind:value={draft.y} /></label>
        <label>z height (ft)<input type="number" step="0.5" bind:value={draft.z} /></label>
        <label title="RSSI measured 1 m (3.28 ft) from this tower">RSSI @1m (3.28 ft)<input type="number" step="0.5" bind:value={draft.rssi1m} /></label>
        <label title="Path-loss exponent: 2 free space, 2.5-4 indoors">n<input type="number" step="0.1" bind:value={draft.n} /></label>
      </div>
      <div class="row">
        <button type="submit" class="primary">Save</button>
        <button type="button" onclick={revert}>Revert</button>
      </div>
      <p class="note">The name must match <code>TOWER_NAME</code> flashed on that ESP32.</p>

      <h2>Calibrate</h2>
      <div class="row">
        <label class="inline">Distance (ft)<input type="number" step="0.5" min="0.5" bind:value={calDist} /></label>
        <button type="button" disabled={calBusy} onclick={calibrate}>{calBusy ? 'Measuring…' : 'Measure'}</button>
        <button type="button" disabled={calBusy} onclick={() => run(() => api.clearCalibration(draftFor)).then(() => (calMsg = 'Calibration points cleared'))}>Clear pts</button>
      </div>
      <p class="note">One distance sets RSSI@1m. Measure at two or more distances (for example 3 ft and 15 ft) to fit n as well.</p>
      {#if calMsg}<p class="cal mono">{calMsg}</p>{/if}
    </form>
  {/if}
</section>

<style>
  .simnote {
    font-size: 11px;
    color: #cfe5ff;
    background: #10233a;
    border: 1px solid #1f4a7a;
    border-radius: 6px;
    padding: 5px 8px;
    margin: 0 0 8px;
  }
  .head {
    display: flex;
    justify-content: space-between;
    align-items: center;
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
    padding: 4px;
    border-bottom: 1px solid var(--border);
  }
  td {
    padding: 4px;
    border-bottom: 1px solid var(--border);
  }
  tbody tr {
    cursor: pointer;
  }
  tbody tr:hover {
    background: var(--panel-2);
  }
  tr.sel {
    background: #1f2d3f;
  }
  .empty {
    color: var(--muted);
    text-align: center;
    cursor: default;
  }
  .small {
    padding: 0 6px;
    font-size: 11px;
  }
  .dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #6e7681;
    margin-right: 6px;
  }
  .dot.live {
    background: var(--warn);
  }
  .dot.used {
    background: var(--tower);
  }
  .editor {
    margin-top: 14px;
    padding-top: 12px;
    border-top: 1px solid var(--border);
  }
  .grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }
  label {
    display: flex;
    flex-direction: column;
    gap: 2px;
    font-size: 11px;
    color: var(--muted);
  }
  label.inline {
    flex: 1;
  }
  .row {
    display: flex;
    gap: 8px;
    align-items: flex-end;
    margin: 10px 0 4px;
  }
  .note {
    font-size: 11px;
    color: var(--muted);
    margin: 4px 0 12px;
  }
  .cal {
    font-size: 11px;
    color: var(--ok);
  }
  code {
    background: var(--panel-2);
    padding: 0 4px;
    border-radius: 3px;
  }
</style>
