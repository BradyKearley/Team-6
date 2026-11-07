<script>
  import { T, useTask } from '@threlte/core';
  import { latticeTower } from './models.js';
  import RangeBubble from './RangeBubble.svelte';
  import { HTML } from '@threlte/extras';
  import { DoubleSide } from 'three';
  import { toScene } from './coords.js';
  import { fmtFt } from '../lib/units.js';

  let { tower, selected, showRanges = true, mode = '3d', planeZ = null, ondown } = $props();

  let base = $derived(toScene(tower.x, tower.y, 0));
  let top = $derived(toScene(tower.x, tower.y, tower.z));
  let live = $derived(tower.live);

  // 2D: horizontal radius of the range sphere at the plane's altitude,
  // i.e. where the circles should cross on the map.
  let dz = $derived((planeZ ?? tower.z) - tower.z);
  let flatRadius = $derived(Math.sqrt(Math.max((live?.distance ?? 0) ** 2 - dz * dz, 0)));
  const round5cm = (m) => Math.round(m * 20) / 20;

  // 3D model sized to the real antenna height (with a minimum so short towers stay visible).
  let H = $derived(Math.max(tower.z - 0.04, 0.35));
  let lattice = $derived({ ...latticeTower(H), padSize: Math.max(0.3, H * 0.3) });
  let panelH = $derived(Math.max(0.12, H * 0.16));
  let rodH = $derived(Math.max(0.1, H * 0.14));

  // Aviation beacon: short flash every 1.5 s, offset per tower so they don't sync up.
  let beaconOn = $state(false);
  const phase = Math.random() * 1.5;
  let t = 0;
  useTask((dt) => {
    t += dt;
    const on = (t + phase) % 1.5 < 0.25;
    if (on !== beaconOn) beaconOn = on;
  });
  let color = $derived(tower.used ? '#f0883e' : live ? '#d29922' : '#6e7681');
</script>

{#if mode === '3d'}
  <T.Group position={base}>
    <!-- concrete pad -->
    <T.Mesh position.y={0.02} castShadow receiveShadow raycast={() => null}>
      <T.BoxGeometry args={[lattice.padSize, 0.04, lattice.padSize]} />
      <T.MeshStandardMaterial color="#a5a39b" roughness={0.9} />
    </T.Mesh>
    <!-- galvanized lattice mast -->
    <T.Mesh geometry={lattice.geometry} position.y={0.04} castShadow raycast={() => null}>
      <T.MeshStandardMaterial color={selected ? '#e6edf5' : '#b7bec7'} metalness={0.85} roughness={0.35} envMapIntensity={1.2} />
    </T.Mesh>
    <!-- platform + status band at the top -->
    <T.Mesh position.y={H + 0.04} castShadow raycast={() => null}>
      <T.CylinderGeometry args={[lattice.topRadius * 2.2, lattice.topRadius * 2.2, H * 0.025 + 0.01, 24]} />
      <T.MeshStandardMaterial color="#8e959e" metalness={0.8} roughness={0.4} />
    </T.Mesh>
    <T.Mesh position.y={H + 0.04 + H * 0.02 + 0.01} raycast={() => null}>
      <T.TorusGeometry args={[lattice.topRadius * 2.25, Math.max(0.006, H * 0.006), 8, 32]} />
      <T.MeshStandardMaterial {color} emissive={color} emissiveIntensity={live ? 1.6 : 0.2} />
    </T.Mesh>
    <!-- three sector antenna panels -->
    {#each [0, 1, 2] as i}
      {@const a = (i * 2 * Math.PI) / 3 + Math.PI / 6}
      {@const r = lattice.topRadius * 2.0}
      <T.Mesh
        position={[Math.cos(a) * r, H + 0.04 - panelH * 0.25, Math.sin(a) * r]}
        rotation.y={-a}
        castShadow
        raycast={() => null}
      >
        <T.BoxGeometry args={[panelH * 0.14, panelH, panelH * 0.32]} />
        <T.MeshStandardMaterial color="#eceff2" roughness={0.55} />
      </T.Mesh>
    {/each}
    <!-- lightning rod + aviation beacon -->
    <T.Mesh position.y={H + 0.04 + rodH / 2} castShadow raycast={() => null}>
      <T.CylinderGeometry args={[0.006, 0.01, rodH, 6]} />
      <T.MeshStandardMaterial color="#9aa1aa" metalness={0.9} roughness={0.3} />
    </T.Mesh>
    <T.Mesh position.y={H + 0.04 + rodH} raycast={() => null}>
      <T.SphereGeometry args={[Math.max(0.018, H * 0.02), 12, 12]} />
      <T.MeshStandardMaterial color="#ff2a1a" emissive="#ff2a1a" emissiveIntensity={beaconOn ? 4 : 0.15} />
    </T.Mesh>
    <!-- invisible hit box so the sparse lattice is easy to click and drag -->
    <T.Mesh
      position.y={(H + rodH) / 2}
      onpointerdown={(e) => {
        e.stopPropagation();
        ondown();
      }}
    >
      <T.CylinderGeometry args={[lattice.padSize * 0.6, lattice.padSize * 0.6, H + rodH, 8]} />
      <T.MeshBasicMaterial visible={false} />
    </T.Mesh>
  </T.Group>
{:else}
  <!-- 2D marker: flat colored disc with a white rim, like a map pin seen from above -->
  <T.Group position={[base[0], 0.05, base[2]]} rotation.x={-Math.PI / 2}>
    <T.Mesh
      onpointerdown={(e) => {
        e.stopPropagation();
        ondown();
      }}
    >
      <T.CircleGeometry args={[0.3, 32]} />
      <T.MeshBasicMaterial color="#ffffff" />
    </T.Mesh>
    <T.Mesh position.z={0.001} raycast={() => null}>
      <T.CircleGeometry args={[0.24, 32]} />
      <T.MeshBasicMaterial {color} />
    </T.Mesh>
    <T.Mesh position.z={0.002} raycast={() => null}>
      <T.CircleGeometry args={[0.07, 16]} />
      <T.MeshBasicMaterial color="#ffffff" />
    </T.Mesh>
  </T.Group>
{/if}

<!-- footprint ring (bigger when selected) -->
<T.Mesh position={[base[0], 0.02, base[2]]} rotation.x={-Math.PI / 2}>
  <T.RingGeometry args={selected ? [0.35, 0.5, 32] : [0.3, 0.36, 32]} />
  <T.MeshBasicMaterial color={selected ? '#4ea1ff' : color} side={DoubleSide} />
</T.Mesh>

<!-- Range shown as a disc/sphere (unit size, scaled) plus an outline ring.
     Rings can't be scaled (their line width would grow too), so their radius is
     rounded to 5 cm to avoid rebuilding them on every 10 Hz update. -->
{#if showRanges && live?.distance && mode === '2d'}
  <T.Mesh position={[base[0], 0.01, base[2]]} rotation.x={-Math.PI / 2} scale={flatRadius} raycast={() => null}>
    <T.CircleGeometry args={[1, 96]} />
    <T.MeshBasicMaterial {color} transparent opacity={selected ? 0.1 : 0.04} depthWrite={false} />
  </T.Mesh>
  <T.Mesh position={[base[0], 0.03, base[2]]} rotation.x={-Math.PI / 2} raycast={() => null}>
    <T.RingGeometry args={[Math.max(round5cm(flatRadius) - 0.06, 0), round5cm(flatRadius) + 0.06, 128]} />
    <T.MeshBasicMaterial {color} side={DoubleSide} transparent opacity={selected ? 0.95 : 0.6} depthWrite={false} />
  </T.Mesh>
{:else if showRanges && live?.distance}
  <RangeBubble position={top} radius={live.distance} {color} strength={selected ? 1.6 : 1} />
  <!-- ring where the range sphere crosses antenna height: easier to read than the sphere alone -->
  <T.Mesh position={top} rotation.x={-Math.PI / 2} raycast={() => null}>
    <T.RingGeometry args={[Math.max(round5cm(live.distance) - 0.03, 0), round5cm(live.distance) + 0.03, 96]} />
    <T.MeshBasicMaterial {color} side={DoubleSide} transparent opacity={selected ? 0.9 : 0.45} depthWrite={false} />
  </T.Mesh>
{/if}

<!-- name tag: above the beacon in 3D; just "north" of the marker on the 2D map -->
<HTML
  position={mode === '3d' ? [top[0], H + 0.04 + rodH + 0.3, top[2]] : [base[0], 0.1, base[2] - 0.75]}
  center
  zIndexRange={[100, 0]}
>
  <div class="label" class:selected>
    <i style={`background:${color}`}></i>
    <b>{tower.name}</b>
    {#if live?.distance}<span>{fmtFt(live.distance)}</span>{/if}
  </div>
</HTML>

<style>
  .label {
    pointer-events: none;
    display: flex;
    align-items: center;
    gap: 6px;
    font: 11px system-ui;
    color: #f0f4f8;
    background: rgba(12, 16, 22, 0.72);
    backdrop-filter: blur(4px);
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 999px;
    padding: 2px 9px 2px 6px;
    white-space: nowrap;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.35);
  }
  .label.selected {
    border-color: #4ea1ff;
    box-shadow: 0 0 0 2px rgba(78, 161, 255, 0.35);
  }
  i {
    width: 8px;
    height: 8px;
    border-radius: 50%;
  }
  span {
    color: #a7b1bc;
  }
</style>
