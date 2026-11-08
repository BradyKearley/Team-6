<script>
  /**
   * The space view shared by the LEO Satellite and Sensor Fusion pages: a 3D globe or a
   * 2D world map of one simulated run, with a transport bar to play it back.
   *
   * Laid out like the tower page: the view fills the pane, controls float on top.
   */
  import { Canvas } from '@threlte/core';
  import { untrack } from 'svelte';
  import SpaceGlobe from '../scene/SpaceGlobe.svelte';
  import SpaceMap2D from '../scene/SpaceMap2D.svelte';

  let {
    scene,
    tracks = [],
    frame = $bindable(0),
    playing = $bindable(true),
    seconds = 20, // how long the whole run takes to play back at 1x
    satColor = '#2ea043', // highlight for satellites in view
    name = 'view', // namespaces the remembered settings, so pages don't fight over them
    defaultFocus = 'globe',
    busy = false,
    children,
  } = $props();

  const load = (k, d) => {
    try {
      const v = localStorage.getItem(k);
      return v == null ? d : JSON.parse(v);
    } catch {
      return d;
    }
  };
  let view = $state(load(`t6.${name}.view`, '3d'));
  let zoom = $state(load(`t6.${name}.zoom`, 'world'));
  let focus = $state(load(`t6.${name}.focus`, defaultFocus)); // 3D camera: whole Earth or down on the route
  let showOrbits = $state(true);
  let showLinks = $state(true);
  let speed = $state(1);
  let seek = $state(0); // bumped by the scrubber so the playback loop restarts there

  $effect(() => {
    try {
      localStorage.setItem(`t6.${name}.view`, JSON.stringify(view));
      localStorage.setItem(`t6.${name}.zoom`, JSON.stringify(zoom));
      localStorage.setItem(`t6.${name}.focus`, JSON.stringify(focus));
    } catch {}
  });

  let frames = $derived(scene?.frames_min ?? []);
  // frame is a float and can briefly be out of range (or NaN) while a new run swaps in,
  // so every read of it is clamped.
  let nowMin = $derived(frames[Math.min(Math.max(Math.round(frame) || 0, 0), frames.length - 1)] ?? 0);

  $effect(() => {
    if (!playing || !frames.length) return;
    seek;
    let pos = untrack(() => frame);
    let last = performance.now();
    let raf = requestAnimationFrame(function tick(t) {
      const n = frames.length;
      if (!n) return; // the run was swapped out from under us
      const dt = (t - last) / 1000;
      last = t;
      pos = (pos + dt * (n / seconds) * speed) % n;
      frame = pos;
      raf = requestAnimationFrame(tick);
    });
    return () => cancelAnimationFrame(raf);
  });

  // Reset to the start whenever a new run arrives.
  $effect(() => {
    scene;
    untrack(() => {
      frame = 0;
      seek++;
    });
  });
</script>

<div class="stage" class:stale={busy}>
  {#if view === '3d'}
    <Canvas>
      <SpaceGlobe {scene} {frame} {tracks} {showOrbits} {showLinks} {satColor} {focus} />
    </Canvas>
  {:else}
    <SpaceMap2D {scene} {frame} {tracks} {showOrbits} {showLinks} {zoom} {satColor} />
  {/if}

  <div class="toolbar">
    <div class="seg" role="group" aria-label="Space view">
      <button class:on={view === '3d'} onclick={() => (view = '3d')}>3D</button>
      <button class:on={view === '2d'} onclick={() => (view = '2d')}>2D</button>
    </div>
    {#if view === '2d'}
      <div class="seg" role="group" aria-label="Map extent">
        <button class:on={zoom === 'world'} onclick={() => (zoom = 'world')}>World</button>
        <button class:on={zoom === 'plane'} onclick={() => (zoom = 'plane')}>Near plane</button>
      </div>
    {:else}
      <div class="seg" role="group" aria-label="Camera">
        <button class:on={focus === 'globe'} onclick={() => (focus = 'globe')}>Globe</button>
        <button class:on={focus === 'flight'} onclick={() => (focus = 'flight')}>Fly along</button>
      </div>
    {/if}
    <label class="chip"><input type="checkbox" bind:checked={showOrbits} /> Orbit paths</label>
    <label class="chip"><input type="checkbox" bind:checked={showLinks} /> Links</label>
    {@render children?.()}
  </div>

  <div class="hintbar">
    <span class="hint">
      {view === '2d'
        ? 'Earth-fixed map · a highlighted satellite is in view, its Doppler is feeding the filter'
        : focus === 'flight'
          ? 'Down at flight level: towers, their range rings and the tracks · drag to look around, scroll to zoom'
          : 'Earth-fixed frame: the ground stays put, the constellation moves · drag to orbit, scroll to zoom'}
    </span>
  </div>

  <div class="player">
    <button class="play" onclick={() => (playing = !playing)} aria-label={playing ? 'Pause' : 'Play'}>
      {playing ? '❚❚' : '▶'}
    </button>
    <input
      class="scrub"
      type="range"
      min="0"
      max={Math.max(frames.length - 1, 0)}
      step="0.01"
      value={frame}
      aria-label="Playback position"
      oninput={(e) => {
        frame = +e.currentTarget.value;
        seek++;
      }}
    />
    <span class="clock mono">{nowMin.toFixed(1)} / {(frames[frames.length - 1] ?? 0).toFixed(0)} min</span>
    <select bind:value={speed} aria-label="Playback speed">
      <option value={0.5}>0.5×</option>
      <option value={1}>1×</option>
      <option value={2}>2×</option>
      <option value={4}>4×</option>
    </select>
  </div>
</div>

<style>
  .stage {
    position: absolute;
    inset: 0;
    background: #070d16;
    transition: opacity 0.15s;
  }
  .stage.stale {
    opacity: 0.6;
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
  .hintbar {
    position: absolute;
    top: 46px;
    left: 12px;
  }
  .hint {
    color: var(--muted);
    font-size: 12px;
    text-shadow: 0 0 6px #000;
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
    accent-color: var(--accent);
    margin: 0;
  }
  .player {
    position: absolute;
    left: 12px;
    right: 12px;
    bottom: 12px;
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 6px 10px;
    background: rgba(10, 17, 28, 0.82);
    border: 1px solid var(--border);
    border-radius: 10px;
  }
  .play {
    width: 34px;
    font-size: 11px;
    line-height: 1.5;
    flex: none;
  }
  .scrub {
    flex: 1;
    min-width: 80px;
    accent-color: var(--accent);
    padding: 0;
  }
  .clock {
    font-size: 12px;
    color: var(--muted);
    min-width: 94px;
    text-align: right;
  }
  select {
    font: inherit;
    font-size: 12px;
    color: var(--text);
    background: var(--panel-2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 2px 4px;
  }
</style>
