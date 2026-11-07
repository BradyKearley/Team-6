<script>
  import { T, useThrelte } from '@threlte/core';
  import { OrbitControls, Grid, HTML, interactivity } from '@threlte/extras';
  import { PMREMGenerator } from 'three';
  import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';
  import { grassTexture } from './models.js';
  import SkyDome from './SkyDome.svelte';
  import Scenery from './Scenery.svelte';
  import Map2D from './Map2D.svelte';
  import Tower from './Tower.svelte';
  import Plane from './Plane.svelte';
  import Trail from './Trail.svelte';
  import { toScene, fromScene } from './coords.js';
  import { M_PER_FT } from '../lib/units.js';

  let {
    towers,
    estimate,
    trail,
    truth,
    selected,
    placing,
    showRanges = true,
    mode = '3d', // '3d' orbit view, or '2d' top-down map
    layoutKey = '', // changes when the tower layout is swapped (e.g. sim <-> real), to re-frame the camera
    onplace,
    onselect,
    onmove,
    onmoveend,
  } = $props();

  interactivity();
  const { size, renderer, scene } = useThrelte();
  $effect(() => {
    renderer.toneMappingExposure = mode === '3d' ? 0.9 : 1.0;
  });
  // Reflections for metal/glossy parts: three's procedural room environment (no downloads).
  $effect(() => {
    if (mode !== '3d') return;
    const pmrem = new PMREMGenerator(renderer);
    const env = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    scene.environment = env;
    scene.environmentIntensity = 0.45;
    return () => {
      scene.environment = null;
      env.dispose();
      pmrem.dispose();
    };
  });

  // Grid: 1 square = 1 ft, heavier line every 10 ft. Scene units stay meters.
  const FT = M_PER_FT;

  // Sun direction, shared by the sky glow and the shadow-casting light.
  const SUN_ELEV = (38 * Math.PI) / 180; // height above the horizon
  const SUN_AZ = (140 * Math.PI) / 180; // compass direction
  const sunDir = [Math.cos(SUN_ELEV) * Math.sin(SUN_AZ), Math.sin(SUN_ELEV), Math.cos(SUN_ELEV) * Math.cos(SUN_AZ)];
  const GROUND = 2000; // meters of grass (reaches the hills at 750 m)
  const grass = grassTexture(4, GROUND);

  // Camera framing (server coords, meters). Worked out once when the towers first
  // arrive, so the view doesn't jump around while you drag a tower.
  let fit = $state({ cx: 5, cy: 5, w: 10, h: 10 });
  let framed = $state(false);
  $effect(() => {
    layoutKey; // re-run when the layout is swapped
    framed = false; // ...so the camera re-frames on the new towers
  });
  $effect(() => {
    if (framed || !towers.length) return;
    const xs = towers.map((t) => t.x);
    const ys = towers.map((t) => t.y);
    fit = {
      cx: (Math.min(...xs) + Math.max(...xs)) / 2,
      cy: (Math.min(...ys) + Math.max(...ys)) / 2,
      w: Math.max(Math.max(...xs) - Math.min(...xs), 4),
      h: Math.max(Math.max(...ys) - Math.min(...ys), 4),
    };
    framed = true;
  });
  let target = $derived(toScene(fit.cx, fit.cy, 0));
  let zoom2d = $derived(Math.max(2, Math.min($size.width / (fit.w * 1.4), $size.height / (fit.h * 1.4)))); // pixels per meter
  let shadowR = $derived(Math.min(Math.max(Math.hypot(fit.w, fit.h) / 2 + 8, 12), 80)); // shadow box half-size

  let sun = $state();
  $effect(() => {
    if (!sun) return;
    sun.target.position.set(target[0], 0, target[2]); // aim the shadow box at the layout
    sun.target.updateMatrixWorld();
    sun.shadow.camera.updateProjectionMatrix();
  });

  let planeZ = $derived(estimate?.ok ? estimate.position[2] : null);
  let dragging = $state(null);
  let hover = $state(null); // ghost marker while placing

  function endDrag() {
    if (dragging) {
      onmoveend(dragging);
      dragging = null;
    }
  }
  $effect(() => {
    window.addEventListener('pointerup', endDrag);
    return () => window.removeEventListener('pointerup', endDrag);
  });
</script>

{#if mode === '2d'}
  <!-- Straight down, +X right and +Y up on screen. Tiny z offset avoids a degenerate lookAt. -->
  <T.OrthographicCamera makeDefault position={[target[0], 100, target[2] + 0.001]} zoom={zoom2d} near={0.1} far={500}>
    <OrbitControls
      {target}
      enabled={!dragging}
      enableRotate={false}
      enableDamping
      screenSpacePanning
      mouseButtons={{ LEFT: 2, MIDDLE: 1, RIGHT: 2 }}
    />
  </T.OrthographicCamera>
{:else}
  <!-- near/far: keep near as large as we can; a tiny near plane makes flat layers flicker (z-fighting) -->
  <T.PerspectiveCamera makeDefault position={[target[0] + 14, 5.5, target[2] + 20]} fov={42} near={0.25} far={2500}>
    <OrbitControls {target} enabled={!dragging} enableDamping maxPolarAngle={Math.PI / 2 - 0.04} maxDistance={400} />
  </T.PerspectiveCamera>
{/if}

{#if mode === '3d'}
  <!-- Realistic 3D: gradient sky, haze, sunlight with shadows, grass. All generated in code (works offline). -->
  <SkyDome {sunDir} />
  <T.Fog attach="fog" args={['#cfe2f3', 200, 1400]} />
  <T.HemisphereLight args={['#dfe9f5', '#4a5a35', 0.5]} />
  <T.DirectionalLight
    position={[target[0] + sunDir[0] * 60, sunDir[1] * 60, target[2] + sunDir[2] * 60]}
    intensity={2.4}
    color="#fff4e2"
    castShadow
    shadow.mapSize={[4096, 4096]}
    shadow.bias={-0.0004}
    shadow.normalBias={0.02}
    shadow.camera.left={-shadowR}
    shadow.camera.right={shadowR}
    shadow.camera.top={shadowR}
    shadow.camera.bottom={-shadowR}
    shadow.camera.near={1}
    shadow.camera.far={200}
    bind:ref={sun}
  />

  <!-- Grass ground -->
  <!-- Flat ground layers are stacked a few cm apart AND use polygonOffset so they never
       flicker against each other: grass (bottom) < concrete pad < pad border < grid (top). -->
  <T.Mesh rotation.x={-Math.PI / 2} position.y={-0.02} receiveShadow raycast={() => null}>
    <T.PlaneGeometry args={[GROUND, GROUND]} />
    <T.MeshStandardMaterial
      map={grass}
      color="#c2d4a8"
      roughness={0.95}
      metalness={0}
      envMapIntensity={0.3}
      polygonOffset
      polygonOffsetFactor={4}
      polygonOffsetUnits={4}
    />
  </T.Mesh>
  {#if framed}<Scenery {fit} />{/if}

  <Grid
    cellSize={FT}
    sectionSize={10 * FT}
    gridSize={[200, 200]}
    cellColor="#2f4219"
    cellThickness={0.5}
    sectionColor="#e9f0e2"
    sectionThickness={1}
    fadeDistance={35}
    fadeStrength={2}
    infiniteGrid
    position.y={0.03}
  />
  <!-- Axes: red = +X, blue = +Y, green = up (Z) -->
  <T.AxesHelper args={[2]} position.y={0.01} />
  <HTML position={[2.3, 0, 0]} center zIndexRange={[10, 0]}><span class="axis x">+X</span></HTML>
  <HTML position={[0, 0, -2.3]} center zIndexRange={[10, 0]}><span class="axis y">+Y</span></HTML>
  <HTML position={[0, 2.3, 0]} center zIndexRange={[10, 0]}><span class="axis z">+Z</span></HTML>
{:else}
  <Map2D {fit} />
{/if}

<!-- Invisible floor that catches clicks for placing and dragging towers -->
<T.Mesh
  rotation.x={-Math.PI / 2}
  onclick={(e) => {
    if (placing) onplace(fromScene(e.point));
    else onselect(null);
  }}
  onpointermove={(e) => {
    if (dragging) onmove(dragging, fromScene(e.point));
    hover = placing ? e.point.clone() : null;
  }}
  onpointerleave={() => (hover = null)}
>
  <T.PlaneGeometry args={[400, 400]} />
  <T.MeshBasicMaterial visible={false} />
</T.Mesh>

{#if placing && hover}
  <T.Mesh position={[hover.x, 0.05, hover.z]} rotation.x={-Math.PI / 2}>
    <T.RingGeometry args={[0.25, 0.4, 32]} />
    <T.MeshBasicMaterial color="#f0883e" transparent opacity={0.8} />
  </T.Mesh>
{/if}

{#each towers as t (t.name)}
  <Tower
    tower={t}
    selected={selected === t.name}
    {showRanges}
    {mode}
    {planeZ}
    ondown={() => {
      if (placing) return;
      onselect(t.name);
      dragging = t.name;
    }}
  />
{/each}

{#if estimate?.ok && estimate.position}
  <Plane position={estimate.position} velocity={estimate.velocity} errorRadius={estimate.error_radius} flat={mode === '2d'} />
{/if}

<Trail points={trail} />

{#if truth}
  <T.Mesh position={toScene(...truth)}>
    <T.SphereGeometry args={[0.12, 16, 16]} />
    <T.MeshBasicMaterial color="#3fb950" />
  </T.Mesh>
  <HTML position={toScene(truth[0], truth[1], truth[2] + 0.4)} center>
    <span class="truth">sim truth</span>
  </HTML>
{/if}

<style>
  .axis {
    font: 600 11px system-ui;
    pointer-events: none;
  }
  .x {
    color: #ff6b6b;
  }
  .y {
    color: #6b9bff;
  }
  .z {
    color: #6bff8b;
  }
  .truth {
    font: 10px system-ui;
    color: #3fb950;
    white-space: nowrap;
    pointer-events: none;
  }
</style>
