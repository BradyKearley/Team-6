<script>
  // 2D-only map styling: navy background, grid, axis lines, and ruler labels every 10 ft.
  import { T, useThrelte } from '@threlte/core';
  import { Grid, HTML } from '@threlte/extras';
  import { Color } from 'three';
  import { M_PER_FT } from '../lib/units.js';
  import { toScene } from './coords.js';

  // fit = tower layout bounds in server coords (meters): { cx, cy, w, h }
  let { fit } = $props();

  const { scene } = useThrelte();
  $effect(() => {
    scene.background = new Color('#0a111c');
    return () => (scene.background = null);
  });

  const TEN_FT = 10 * M_PER_FT;
  const half = 400; // axis line half-length, m

  // Ruler marks: every 10 ft, covering the layout plus a margin.
  function marks(center, size) {
    const from = Math.floor((center - size) / TEN_FT);
    const to = Math.ceil((center + size) / TEN_FT);
    const out = [];
    for (let i = from; i <= to; i++) if (i !== 0) out.push(i * 10);
    return out;
  }
  let xMarks = $derived(marks(fit.cx, fit.w));
  let yMarks = $derived(marks(fit.cy, fit.h));
</script>

<T.AmbientLight intensity={1} />

<Grid
  cellSize={M_PER_FT}
  sectionSize={TEN_FT}
  gridSize={[300, 300]}
  cellColor="#162131"
  cellThickness={0.6}
  sectionColor="#2b4566"
  sectionThickness={1.1}
  fadeDistance={150}
  infiniteGrid
/>

<!-- X and Y axis lines through (0, 0) -->
<T.Mesh position={[0, 0.004, 0]} rotation.x={-Math.PI / 2} raycast={() => null}>
  <T.PlaneGeometry args={[half * 2, 0.04]} />
  <T.MeshBasicMaterial color="#e06c75" transparent opacity={0.7} />
</T.Mesh>
<T.Mesh position={[0, 0.004, 0]} rotation.x={-Math.PI / 2} raycast={() => null}>
  <T.PlaneGeometry args={[0.04, half * 2]} />
  <T.MeshBasicMaterial color="#61afef" transparent opacity={0.7} />
</T.Mesh>

<!-- ruler labels -->
{#each xMarks as ft (ft)}
  <HTML position={toScene(ft * M_PER_FT, 0, 0.01)} center zIndexRange={[10, 0]}>
    <span class="tick x">{ft}</span>
  </HTML>
{/each}
{#each yMarks as ft (ft)}
  <HTML position={toScene(0, ft * M_PER_FT, 0.01)} center zIndexRange={[10, 0]}>
    <span class="tick y">{ft}</span>
  </HTML>
{/each}
<HTML position={toScene(0, 0, 0.01)} center zIndexRange={[10, 0]}>
  <span class="tick origin">0,0</span>
</HTML>

<style>
  .tick {
    font: 600 10px ui-monospace, Consolas, monospace;
    padding: 0 3px;
    border-radius: 3px;
    background: rgba(10, 17, 28, 0.85);
    pointer-events: none;
    white-space: nowrap;
  }
  .x {
    color: #e06c75;
  }
  .y {
    color: #61afef;
  }
  .origin {
    color: #c8d3e0;
  }
</style>
