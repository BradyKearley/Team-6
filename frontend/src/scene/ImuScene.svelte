<script>
  /**
   * The live IMU page's 3D view: the trilateration page's aircraft, turned by the
   * Raspberry Pi's MPU6050 orientation in real time.
   *
   * `q` is [w, x, y, z] in the IMU's world frame (X forward, Y left, Z up), already
   * tared by the page. Three.js is Y-up, so (X, Y, Z) -> (x, z, -y); the same proper
   * rotation applies to the quaternion's vector part.
   */
  import { T, useTask, useThrelte } from '@threlte/core';
  import { Grid, HTML, OrbitControls } from '@threlte/extras';
  import { Color, Quaternion } from 'three';
  import AircraftModel from './AircraftModel.svelte';

  let { q = null, live = false } = $props();

  const { scene } = useThrelte();
  $effect(() => {
    scene.background = new Color('#0b1220');
    return () => (scene.background = null);
  });

  let group = $state();
  let prop = $state(0);
  const want = new Quaternion();

  useTask((dt) => {
    if (group && q) {
      const [w, x, y, z] = q;
      want.set(x, z, -y, w).normalize();
      // Samples arrive ~10x a second; glide toward each one instead of snapping.
      group.quaternion.slerp(want, 1 - Math.exp(-dt * 14));
    }
    if (live) prop = (prop + dt * 50) % (Math.PI * 2);
  });

  const labels = [
    { p: [4.2, 0.02, 0], t: 'FORWARD (+X)' },
    { p: [0, 0.02, -4.2], t: 'LEFT (+Y)' },
  ];
</script>

<T.PerspectiveCamera makeDefault position={[-4.6, 2.4, 3.6]} fov={40} near={0.05} far={200}>
  <OrbitControls target={[0, 0.1, 0]} enableDamping minDistance={1.2} maxDistance={20} />
</T.PerspectiveCamera>

<T.HemisphereLight args={['#dfe9f5', '#2a3340', 0.9]} />
<T.DirectionalLight position={[3, 6, 2]} intensity={1.8} />
<T.AmbientLight intensity={0.25} />

<Grid
  position.y={-0.9}
  cellColor="#24324a"
  sectionColor="#3a5a8a"
  cellSize={0.5}
  sectionSize={2}
  gridSize={[20, 20]}
  fadeDistance={14}
  infiniteGrid
/>

<!-- world axes on the floor: where "forward" was when the IMU was zeroed -->
<T.Mesh position={[2, -0.89, 0]} rotation.z={-Math.PI / 2}>
  <T.CylinderGeometry args={[0.012, 0.012, 4, 6]} />
  <T.MeshBasicMaterial color="#e05d5d" />
</T.Mesh>
<T.Mesh position={[0, -0.89, -2]} rotation.x={Math.PI / 2}>
  <T.CylinderGeometry args={[0.012, 0.012, 4, 6]} />
  <T.MeshBasicMaterial color="#5dd06b" />
</T.Mesh>
{#each labels as l}
  <HTML position={[l.p[0], -0.85, l.p[2]]} center>
    <span class="axis">{l.t}</span>
  </HTML>
{/each}

<T.Group bind:ref={group} scale={1.7}>
  <AircraftModel {prop} />
</T.Group>

<style>
  .axis {
    font: 600 10px system-ui;
    letter-spacing: 0.08em;
    color: #8b949e;
    white-space: nowrap;
    pointer-events: none;
  }
</style>
