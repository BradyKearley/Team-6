<script>
  import { Canvas } from '@threlte/core';
  import { ACESFilmicToneMapping } from 'three';
  import World from '../scene/World.svelte';
  import TowerPanel from '../lib/TowerPanel.svelte';
  import LiveStatus from '../lib/LiveStatus.svelte';
  import SetupTowers from '../lib/SetupTowers.svelte';
  import CalibrationSweep from '../lib/CalibrationSweep.svelte';
  import { live } from '../lib/live.svelte.js';
  import { api } from '../lib/api.js';
  import { toFt, toM, snapFt } from '../lib/units.js';
  import { nextTowerName } from '../lib/towers.js';

  let selected = $state(null); // tower name
  let placing = $state(false); // click-on-grid-to-add mode
  let showSetup = $state(false); // Setup towers guide open?
  let showSweep = $state(false); // Calibration sweep open?
  let dragPos = $state({}); // name -> {x, y} of a tower being dragged, until the server catches up
  let error = $state('');

  // View toggles, remembered per browser.
  const load = (k, d) => {
    try {
      const v = localStorage.getItem(k);
      return v == null ? d : JSON.parse(v);
    } catch {
      return d;
    }
  };
  let showRanges = $state(load('t6.showRanges', true));
  let mode = $state(load('t6.viewMode', '3d'));
  $effect(() => {
    try {
      localStorage.setItem('t6.showRanges', JSON.stringify(showRanges));
      localStorage.setItem('t6.viewMode', JSON.stringify(mode));
    } catch {}
  });

  // Simulator speed slider (only shown while the simulator is running). Works in ft/s; the server takes m/s.
  let simSpeed = $derived(live.data?.link?.sim_speed);
  let speedFtps = $state(null); // what the user picked; before that, show the server's value
  let speedShown = $derived(speedFtps ?? Math.round(toFt(simSpeed ?? 1) * 2) / 2);
  function setSpeed(ftps) {
    speedFtps = ftps;
    run(() => api.setSimSpeed(toM(ftps)));
  }

  // Towers from the server, with any tower being dragged shown at its dragged spot.
  let towers = $derived((live.data?.towers ?? []).map((t) => (dragPos[t.name] ? { ...t, ...dragPos[t.name] } : t)));

  // Once the server reports a dragged tower at its new spot, stop overriding it.
  $effect(() => {
    for (const t of live.data?.towers ?? []) {
      const d = dragPos[t.name];
      if (d && Math.abs(t.x - d.x) < 0.01 && Math.abs(t.y - d.y) < 0.01) {
        const { [t.name]: _, ...rest } = dragPos;
        dragPos = rest;
      }
    }
  });

  /** Run an API call; show its error message in the corner if it fails. */
  async function run(fn) {
    error = '';
    try {
      return await fn();
    } catch (e) {
      error = e.message;
    }
  }

  // Placing/dragging snaps to the nearest half foot (positions are sent in meters).
  const snap = (m) => snapFt(m, 0.5);

  async function place(p) {
    placing = false;
    const t = await run(() => api.createTower({ name: nextTowerName(towers), x: snap(p.x), y: snap(p.y) }));
    if (t) selected = t.name;
  }

  function move(name, p) {
    dragPos = { ...dragPos, [name]: { x: snap(p.x), y: snap(p.y) } };
  }

  async function moveEnd(name) {
    const t = towers.find((x) => x.name === name);
    if (!t || !dragPos[name]) return;
    const saved = await run(() => api.updateTower(name, { name, x: t.x, y: t.y, z: t.z, rssi1m: t.rssi1m, n: t.n }));
    if (!saved) {
      const { [name]: _, ...rest } = dragPos; // save failed: snap back
      dragPos = rest;
    }
  }
</script>

{#if showSetup}
  <SetupTowers onclose={() => (showSetup = false)} />
{/if}
{#if showSweep}
  <CalibrationSweep onclose={() => (showSweep = false)} />
{/if}

<div class="page">
  <section class="view" class:placing>
    <Canvas shadows dpr={[1, 2]} toneMapping={ACESFilmicToneMapping}>
      <World
        {towers}
        estimate={live.data?.estimate}
        trail={live.data?.trail ?? []}
        truth={live.data?.truth}
        {selected}
        {placing}
        {showRanges}
        {mode}
        onplace={place}
        onselect={(n) => (selected = n)}
        onmove={move}
        onmoveend={moveEnd}
      />
    </Canvas>
    <div class="toolbar">
      <div class="seg" role="group" aria-label="View mode">
        <button class:on={mode === '3d'} onclick={() => (mode = '3d')}>3D</button>
        <button class:on={mode === '2d'} onclick={() => (mode = '2d')}>2D</button>
      </div>
      <button class:primary={placing} onclick={() => (placing = !placing)}>
        {placing ? 'Click the grid to place…' : '+ Place tower'}
      </button>
      <button class="primary" onclick={() => (showSetup = true)} title="Step-by-step: measure and place your real towers">
        Setup towers
      </button>
      <button onclick={() => (showSweep = true)} title="Record all towers at several known distances to calibrate them">
        Calibration sweep
      </button>
      <button onclick={() => run(api.resetTrack)}>Reset track</button>
      <label class="chip">
        <input type="checkbox" bind:checked={showRanges} />
        Range circles
      </label>
      {#if simSpeed != null}
        <label class="chip" title="Simulated plane ground speed (0 = hover)">
          Sim speed
          <input
            type="range"
            min="0"
            max="16"
            step="0.5"
            value={speedShown}
            oninput={(e) => setSpeed(+e.currentTarget.value)}
          />
          <span class="mono">{speedShown.toFixed(1)} ft/s</span>
        </label>
      {/if}
    </div>
    <div class="hintbar">
      <span class="hint">
        {mode === '2d'
          ? 'Top-down map · drag towers to move · scroll to zoom · drag empty space to pan'
          : 'Drag towers to move · scroll to zoom · right-drag to pan'}
      </span>
    </div>
    {#if mode === '2d'}
      <!-- compass + scale key for the map -->
      <div class="mapkey" aria-hidden="true">
        <svg viewBox="0 0 40 40" width="40" height="40">
          <circle cx="20" cy="20" r="18" class="ring" />
          <path d="M20 5 L25 21 L20 18 L15 21 Z" class="north" />
          <path d="M20 35 L25 19 L20 22 L15 19 Z" class="south" />
        </svg>
        <div>
          <b>+Y</b> is up<br />
          <span class="sq small"></span> 1 ft &nbsp;<span class="sq big"></span> 10 ft
        </div>
      </div>
    {/if}
    {#if error}<div class="error" role="alert">{error}</div>{/if}
  </section>
  <aside>
    <TowerPanel {towers} bind:selected {run} />
    <LiveStatus {run} />
  </aside>
</div>

<style>
  .page {
    display: flex;
    height: 100%;
  }
  .view {
    flex: 1;
    min-width: 0;
    position: relative;
    isolation: isolate; /* keeps the 3D labels' huge z-index inside this box, under the setup dialog */
    background: radial-gradient(ellipse at center, #141a22 0%, #0b0e13 100%);
  }
  .view.placing {
    cursor: crosshair;
  }
  .toolbar {
    position: absolute;
    top: 12px;
    left: 12px;
    display: flex;
    gap: 8px;
    align-items: center;
    flex-wrap: wrap;
  }
  .chip {
    display: flex;
    align-items: center;
    gap: 6px;
    background: var(--panel-2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 3px 10px;
    font-size: 13px;
    cursor: pointer;
    user-select: none;
  }
  .chip input[type='checkbox'] {
    width: auto;
    accent-color: var(--tower);
    margin: 0;
  }
  .chip input[type='range'] {
    width: 110px;
    padding: 0;
    accent-color: var(--accent);
  }
  .chip .mono {
    font-size: 12px;
    color: var(--muted);
    min-width: 58px;
  }
  .hintbar {
    position: absolute;
    top: 48px;
    left: 12px;
  }
  .hint {
    color: var(--muted);
    font-size: 12px;
  }
  .mapkey {
    position: absolute;
    right: 12px;
    bottom: 12px;
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 12px;
    font-size: 11px;
    line-height: 1.6;
    color: #c8d3e0;
    background: rgba(10, 17, 28, 0.82);
    border: 1px solid #22324a;
    border-radius: 10px;
    pointer-events: none;
  }
  .mapkey .ring {
    fill: none;
    stroke: #2b4566;
  }
  .mapkey .north {
    fill: #61afef;
  }
  .mapkey .south {
    fill: #3a4a60;
  }
  .mapkey b {
    color: #61afef;
  }
  .sq {
    display: inline-block;
    vertical-align: -1px;
    border: 1px solid #2b4566;
  }
  .sq.small {
    width: 6px;
    height: 6px;
  }
  .sq.big {
    width: 11px;
    height: 11px;
    border-width: 2px;
  }
  .error {
    position: absolute;
    bottom: 12px;
    left: 12px;
    background: #3d1418;
    border: 1px solid var(--bad);
    color: #ffb3b0;
    padding: 6px 10px;
    border-radius: 6px;
  }
  aside {
    width: 340px;
    flex-shrink: 0;
    background: var(--panel);
    border-left: 1px solid var(--border);
    overflow-y: auto;
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  @media (max-width: 800px) {
    .page {
      flex-direction: column;
    }
    aside {
      width: 100%;
      height: 45%;
      border-left: none;
      border-top: 1px solid var(--border);
    }
  }
</style>
