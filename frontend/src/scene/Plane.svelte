<script>
  import { T, useTask } from '@threlte/core';
  import { HTML } from '@threlte/extras';
  import { toScene } from './coords.js';
  import { fmtFt } from '../lib/units.js';
  import { aircraftParts, chevronGeometry } from './models.js';

  // flat = 2D map: draw a flat arrow instead of the 3D aircraft
  let { position, velocity = [0, 0, 0], errorRadius = 0, flat = false } = $props();

  const parts = aircraftParts(); // model is ~1.2 m wingspan
  const arrow = chevronGeometry(1);
  const arrowOutline = chevronGeometry(1.25);

  // Server updates arrive ~10 Hz; glide between them every frame instead of snapping.
  let target = $derived(toScene(...position));
  let sp = $state(toScene(...position)); // smoothed scene position
  let yaw = 0;
  let bank = 0;
  let prop = $state(0);
  let outer = $state(); // yaw group
  let inner = $state(); // bank group

  const wrap = (a) => Math.atan2(Math.sin(a), Math.cos(a));

  useTask((dt) => {
    const k = 1 - Math.exp(-dt * 6);
    const n = sp.map((v, i) => v + (target[i] - v) * k);
    if (Math.hypot(n[0] - sp[0], n[1] - sp[1], n[2] - sp[2]) > 1e-4) sp = n;

    // Heading from horizontal velocity (server frame: vx, vy -> yaw about scene Y).
    const [vx, vy] = velocity;
    const speed = Math.hypot(vx, vy);
    let yawRate = 0;
    if (speed > 0.15) {
      const want = Math.atan2(vy, vx);
      const step = wrap(want - yaw) * (1 - Math.exp(-dt * 3));
      yaw = wrap(yaw + step);
      yawRate = step / Math.max(dt, 1e-3);
    }
    // Bank into turns, level out when flying straight.
    const wantBank = Math.max(-0.6, Math.min(0.6, -yawRate * 0.45));
    bank += (wantBank - bank) * (1 - Math.exp(-dt * 2.5));

    if (outer) outer.rotation.y = yaw;
    if (inner) inner.rotation.x = flat ? 0 : bank;
    prop = (prop + dt * 60) % (Math.PI * 2);
  });

  let r = $derived(Math.min(Math.max(errorRadius || 0, 0.1), 20));
</script>

<!-- error-radius ghost (unit sphere scaled, so it isn't rebuilt every update) -->
<T.Mesh position={sp} scale={r} raycast={() => null}>
  <T.SphereGeometry args={[1, 32, 20]} />
  <T.MeshBasicMaterial color="#4ea1ff" transparent opacity={0.08} depthWrite={false} />
</T.Mesh>

{#if !flat}
  <!-- drop line + floor marker (1 m tall cylinder stretched to the plane's height) -->
  <T.Mesh position={[sp[0], sp[1] / 2, sp[2]]} scale.y={Math.max(sp[1], 0.01)} raycast={() => null}>
    <T.CylinderGeometry args={[0.006, 0.006, 1, 4]} />
    <T.MeshBasicMaterial color="#4ea1ff" transparent opacity={0.45} />
  </T.Mesh>
  <T.Mesh position={[sp[0], 0.012, sp[2]]} rotation.x={-Math.PI / 2} raycast={() => null}>
    <T.RingGeometry args={[0.12, 0.17, 32]} />
    <T.MeshBasicMaterial color="#4ea1ff" transparent opacity={0.6} />
  </T.Mesh>
{/if}

<!-- nose along +X of the yaw group -->
<T.Group position={flat ? [sp[0], 0.2, sp[2]] : sp} bind:ref={outer}>
  {#if flat}
    <!-- 2D: soft halo + white-outlined blue arrow pointing where the plane is heading -->
    <T.Mesh position.y={-0.02} rotation.x={-Math.PI / 2} raycast={() => null}>
      <T.CircleGeometry args={[0.7, 40]} />
      <T.MeshBasicMaterial color="#4ea1ff" transparent opacity={0.18} depthWrite={false} />
    </T.Mesh>
    <T.Mesh geometry={arrowOutline} position.x={-0.04} raycast={() => null}>
      <T.MeshBasicMaterial color="#ffffff" />
    </T.Mesh>
    <T.Mesh geometry={arrow} position.y={0.01} raycast={() => null}>
      <T.MeshBasicMaterial color="#2f81f7" />
    </T.Mesh>
  {/if}
  <T.Group bind:ref={inner} visible={!flat}>
    <!-- fuselage -->
    <T.Mesh geometry={parts.fuselage} castShadow raycast={() => null}>
      <T.MeshPhysicalMaterial color="#f4f6f8" metalness={0.1} roughness={0.35} clearcoat={0.8} clearcoatRoughness={0.15} />
    </T.Mesh>
    <!-- cheat line stripe -->
    <T.Mesh position={[0.02, 0.0, 0]} scale={[1.002, 0.18, 1.01]} rotation.z={-Math.PI / 2} raycast={() => null}>
      <T.CylinderGeometry args={[0.087, 0.07, 0.7, 24, 1, true]} />
      <T.MeshStandardMaterial color="#1f5fbf" roughness={0.4} />
    </T.Mesh>
    <!-- canopy -->
    <T.Mesh geometry={parts.canopy} position={[0.1, 0.07, 0]} castShadow raycast={() => null}>
      <T.MeshPhysicalMaterial color="#1a2633" metalness={0.2} roughness={0.05} clearcoat={1} clearcoatRoughness={0.02} />
    </T.Mesh>
    <!-- low wing with slight dihedral -->
    <T.Mesh geometry={parts.wing} position={[0.04, -0.045, 0]} castShadow raycast={() => null}>
      <T.MeshPhysicalMaterial color="#f4f6f8" metalness={0.1} roughness={0.35} clearcoat={0.6} />
    </T.Mesh>
    <!-- wingtips -->
    {#each [-1, 1] as side}
      <T.Mesh position={[0.01, -0.038, side * 0.625]} scale={[0.1, 0.012, 0.012]} raycast={() => null}>
        <T.SphereGeometry args={[1, 12, 8]} />
        <T.MeshStandardMaterial
          color={side > 0 ? '#1fbf4a' : '#e0262b'}
          emissive={side > 0 ? '#1fbf4a' : '#e0262b'}
          emissiveIntensity={1.5}
        />
      </T.Mesh>
    {/each}
    <!-- tail -->
    <T.Mesh geometry={parts.hstab} position={[-0.53, 0.01, 0]} castShadow raycast={() => null}>
      <T.MeshPhysicalMaterial color="#f4f6f8" roughness={0.35} clearcoat={0.6} />
    </T.Mesh>
    <T.Mesh geometry={parts.fin} position={[-0.5, 0.02, 0]} castShadow raycast={() => null}>
      <T.MeshStandardMaterial color="#1f5fbf" roughness={0.4} />
    </T.Mesh>
    <!-- spinner + propeller -->
    <T.Mesh geometry={parts.spinner} position={[0.42, 0, 0]} castShadow raycast={() => null}>
      <T.MeshStandardMaterial color="#c9ced6" metalness={0.9} roughness={0.25} />
    </T.Mesh>
    <T.Group position={[0.45, 0, 0]} rotation.x={prop}>
      <T.Mesh geometry={parts.blade} position={[0, 0.17, 0]} raycast={() => null}>
        <T.MeshStandardMaterial color="#222629" roughness={0.6} />
      </T.Mesh>
      <T.Mesh geometry={parts.blade} position={[0, -0.17, 0]} rotation.x={Math.PI} raycast={() => null}>
        <T.MeshStandardMaterial color="#222629" roughness={0.6} />
      </T.Mesh>
      <!-- motion-blur disc -->
      <T.Mesh rotation.z={Math.PI / 2} raycast={() => null}>
        <T.CircleGeometry args={[0.34, 32]} />
        <T.MeshBasicMaterial color="#9aa3ad" transparent opacity={0.08} depthWrite={false} side={2} />
      </T.Mesh>
    </T.Group>
  </T.Group>
</T.Group>

<!-- position tag: above the plane in 3D; just "north" of the arrow on the 2D map -->
<HTML position={flat ? [sp[0], 0.3, sp[2] - 1.0] : [sp[0], sp[1] + 0.55, sp[2]]} center zIndexRange={[100, 0]}>
  <div class="tag">
    <b>PLANE</b>
    {position.map((v) => fmtFt(v, 1, false)).join(', ')} ft
    <span>±{fmtFt(errorRadius || 0, 1, false)}</span>
  </div>
</HTML>

<style>
  .tag {
    pointer-events: none;
    display: flex;
    align-items: center;
    gap: 6px;
    font: 11px ui-monospace, Consolas, monospace;
    color: #e6f1ff;
    background: rgba(10, 30, 56, 0.78);
    backdrop-filter: blur(4px);
    border: 1px solid rgba(78, 161, 255, 0.7);
    border-radius: 999px;
    padding: 2px 9px;
    white-space: nowrap;
    box-shadow: 0 2px 10px rgba(0, 0, 0, 0.35);
  }
  b {
    font: 700 9px system-ui;
    letter-spacing: 0.08em;
    color: #4ea1ff;
  }
  span {
    color: #8bb8e8;
  }
</style>
