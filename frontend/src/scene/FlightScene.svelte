<script>
  /**
   * The RL agent's flight, replayed in a local 3D world.
   *
   * Scene units are kilometres, east/up/north (three.js is Y-up, so north maps to -Z).
   * The aeroplane, the towers and the waypoint gates are drawn much larger than life -
   * a 15 m aircraft is invisible on a 30 km route - so this is map symbology, not scale.
   */
  import { T, useTask } from '@threlte/core';
  import { HTML, OrbitControls } from '@threlte/extras';
  import { BufferAttribute, BufferGeometry, CatmullRomCurve3, Line, LineBasicMaterial, LineSegments, Vector3 } from 'three';
  import { aircraftParts } from './models.js';
  import SkyDome from './SkyDome.svelte';

  let { flight, frame = 0, camera = 'chase', showEstimate = true, showSensors = true } = $props();

  const KM = 1 / 1000;
  /** sim east/north/up metres -> scene kilometres (x = east, y = up, z = -north) */
  const sc = (p) => [p[0] * KM, p[2] * KM, -p[1] * KM];

  const parts = aircraftParts(); // ~1.24 units across; scaled right down below
  const PLANE_SCALE = 0.85; // => a ~1 km "aeroplane", big enough to see from 8 km back

  let frames = $derived(flight.frames);
  let f = $derived(Math.min(Math.max(Math.round(frame) || 0, 0), frames.length - 1));
  let now = $derived(frames[f]);
  let pos = $derived(sc(now.p));
  let est = $derived(sc(now.e));

  // Smooth the aeroplane between recorded ticks, and hold its attitude.
  let shown = $state([0, 0, 0]);
  let yaw = $state(0);
  let bank = $state(0);
  let pitch = $state(0);
  let spin = $state(0);
  useTask((dt) => {
    const k = 1 - Math.exp(-dt * 8);
    shown = shown.map((v, i) => v + (pos[i] - v) * k);
    const wrap = (a) => Math.atan2(Math.sin(a), Math.cos(a));
    // The model's nose is +X and scene yaw turns about +Y, while the sim's heading is
    // measured clockwise from north (which is -Z here): nose direction = yaw of pi/2 - hdg.
    yaw += wrap(Math.PI / 2 - now.hdg - yaw) * k;
    bank += (now.bank - bank) * k;
    pitch += (now.gam - pitch) * k;
    spin = (spin + dt * 40) % (Math.PI * 2);
  });

  function polyline(points, color, width = 1) {
    const g = new BufferGeometry();
    g.setAttribute('position', new BufferAttribute(new Float32Array(points.flat()), 3));
    return new Line(g, new LineBasicMaterial({ color, linewidth: width }));
  }

  // The flown track, drawn up to wherever playback is.
  let truthLine = $derived.by(() => polyline(frames.slice(0, f + 1).map((x) => sc(x.p)), 0x4ea1ff));
  let estLine = $derived.by(() => polyline(frames.slice(0, f + 1).map((x) => sc(x.e)), 0xdb6d28));
  $effect(() => {
    const a = truthLine,
      b = estLine;
    return () => {
      for (const o of [a, b]) (o.geometry.dispose(), o.material.dispose());
    };
  });

  // The planned route, as a smooth ribbon through the waypoints.
  let routePts = $derived(flight.route.map(sc));
  let routeLine = $derived.by(() => {
    const pts = [sc(frames[0].p), ...routePts].map((p) => new Vector3(...p));
    return polyline(new CatmullRomCurve3(pts).getPoints(120).map((v) => [v.x, v.y, v.z]), 0x8b949e);
  });

  let towers = $derived(flight.towers.map(sc));
  let gateR = $derived(flight.capture_m * KM);

  // Satellites are 780 km up - drawing them to scale would put them outside the world.
  // They are placed in the right DIRECTION at a fixed distance instead, so you can see
  // which way the aeroplane is listening. The sky plot on the page has the real angles.
  const SAT_R = 9; // km from the aeroplane - close enough to stay in a chase shot
  const D2R = Math.PI / 180;
  let sats = $derived(
    (now.sky ?? []).map((s) => {
      const ce = Math.cos(s.el * D2R);
      const dir = [Math.sin(s.az * D2R) * ce, Math.cos(s.az * D2R) * ce, Math.sin(s.el * D2R)]; // e,n,u
      return { ...s, p: [pos[0] + dir[0] * SAT_R, pos[1] + dir[2] * SAT_R, pos[2] - dir[1] * SAT_R] };
    }),
  );
  // Which towers the filter is actually ranging off this tick.
  let links = $derived((now.twr ?? []).map((t) => towers[t.i]).filter(Boolean));

  /** One line per pair of points, rebuilt each tick (a handful of segments). */
  function beams(from, tos, color, opacity) {
    const g = new BufferGeometry();
    const a = new Float32Array(tos.length * 6);
    tos.forEach((t, i) => a.set([...from, ...t], i * 6));
    g.setAttribute('position', new BufferAttribute(a, 3));
    return new LineSegments(g, new LineBasicMaterial({ color, transparent: true, opacity }));
  }
  let satBeams = $derived(beams(shown, sats.map((s) => s.p), 0x2ea043, 0.5));
  let towerBeams = $derived(beams(shown, links, 0xbc5cc4, 0.5));
  $effect(() => {
    const a = satBeams, b = towerBeams;
    return () => {
      for (const o of [a, b]) (o.geometry.dispose(), o.material.dispose());
    };
  });

  // Cameras: a chase view just behind the aeroplane, or free orbit around the route.
  let chaseRef = $state();
  useTask(() => {
    if (camera !== 'chase' || !chaseRef) return;
    const back = 7.5, up = 2.2;                    // km: far enough back to see a 3 km gate
    const nose = [Math.cos(yaw), 0, -Math.sin(yaw)];
    chaseRef.position.set(shown[0] - nose[0] * back, shown[1] + up, shown[2] - nose[2] * back);
    chaseRef.lookAt(shown[0], shown[1] - 0.3, shown[2]);
  });
  // Free camera: frame the whole route rather than a fixed distance from the origin.
  let frame3d = $derived.by(() => {
    const pts = [...routePts, sc(frames[0].p)];
    const xs = pts.map((p) => p[0]),
      zs = pts.map((p) => p[2]);
    const cx = (Math.min(...xs) + Math.max(...xs)) / 2;
    const cz = (Math.min(...zs) + Math.max(...zs)) / 2;
    const span = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...zs) - Math.min(...zs), 8);
    return { target: [cx, 9, cz], pos: [cx + span * 0.75, 9 + span * 0.6, cz + span * 0.95] };
  });
</script>

<SkyDome sunDir={[0.4, 0.55, -0.5]} zenith="#1d3f74" horizon="#8fb6d9" ground="#2b3a2b" radius={400} />
<T.Fog attach="fog" args={['#8fb6d9', 40, 220]} />
<T.HemisphereLight args={['#cfe2f3', '#3a4a35', 0.7]} />
<T.DirectionalLight position={[40, 60, -30]} intensity={2.2} color="#fff4e2" />

{#if camera === 'chase'}
  <T.PerspectiveCamera makeDefault fov={55} near={0.05} far={600} bind:ref={chaseRef} />
{:else}
  <T.PerspectiveCamera makeDefault fov={45} near={0.05} far={600} position={frame3d.pos}>
    <OrbitControls target={frame3d.target} enableDamping maxPolarAngle={Math.PI / 2 - 0.02} />
  </T.PerspectiveCamera>
{/if}

<!-- sea -->
<T.Mesh rotation.x={-Math.PI / 2} receiveShadow>
  <T.PlaneGeometry args={[800, 800]} />
  <T.MeshStandardMaterial color="#17304a" roughness={0.75} metalness={0.1} />
</T.Mesh>
<T.GridHelper args={[400, 80, 0x2a4a68, 0x203a52]} position.y={0.01} />

<!-- the planned route and the two tracks -->
<T is={routeLine} />
<T is={truthLine} />
{#if showEstimate}<T is={estLine} />{/if}

<!-- waypoint gates: a ring you have to fly through, the next one lit up -->
{#each routePts as p, i}
  <T.Mesh position={p} rotation.x={Math.PI / 2}>
    <T.TorusGeometry args={[gateR, gateR * 0.025, 8, 48]} />
    <T.MeshStandardMaterial
      color={i < now.wp ? '#2ea043' : i === now.wp ? '#f0883e' : '#51607a'}
      emissive={i === now.wp ? '#f0883e' : '#000000'}
      emissiveIntensity={0.5}
      roughness={0.5}
    />
  </T.Mesh>
  <T.Mesh position={[p[0], p[1] / 2, p[2]]} scale.y={p[1]}>
    <T.CylinderGeometry args={[0.03, 0.03, 1, 6]} />
    <T.MeshBasicMaterial color={i === now.wp ? '#f0883e' : '#51607a'} transparent opacity={0.35} />
  </T.Mesh>
  <HTML position={[p[0], p[1] + gateR + 0.6, p[2]]} center zIndexRange={[8, 0]}>
    <span class="tag wp" class:next={i === now.wp}>WP{i + 1}</span>
  </HTML>
{/each}

<!-- ground towers -->
{#each towers as t, i}
  <T.Mesh position={[t[0], 1.1, t[2]]}>
    <T.CylinderGeometry args={[0.035, 0.09, 2.2, 6]} />
    <T.MeshStandardMaterial color="#bc5cc4" roughness={0.6} />
  </T.Mesh>
  <T.Mesh position={[t[0], 2.3, t[2]]}>
    <T.SphereGeometry args={[0.16, 12, 10]} />
    <T.MeshStandardMaterial color="#d8a0dd" emissive="#bc5cc4" emissiveIntensity={0.6} />
  </T.Mesh>
  <HTML position={[t[0], 3.1, t[2]]} center zIndexRange={[8, 0]}>
    <span class="tag gnd">GND-{i + 1}</span>
  </HTML>
{/each}

<!-- what the aeroplane is listening to right now -->
{#if showSensors}
  <T is={satBeams} />
  <T is={towerBeams} />
  {#each sats as s (s.n)}
    <T.Mesh position={s.p}>
      <T.SphereGeometry args={[0.36, 12, 10]} />
      <T.MeshBasicMaterial color="#2ea043" />
    </T.Mesh>
    <HTML position={[s.p[0], s.p[1] + 0.6, s.p[2]]} center zIndexRange={[8, 0]}>
      <span class="tag sat">IRIDIUM {s.n}<em>{s.el.toFixed(0)}° · {s.khz > 0 ? '+' : ''}{s.khz.toFixed(1)} kHz</em></span>
    </HTML>
  {/each}
{/if}

<!-- where the filter thinks the aeroplane is -->
{#if showEstimate}
  <!-- small on purpose: the fix is only ~15 m out, so this sits almost on the aeroplane -->
  <T.Mesh position={est}>
    <T.SphereGeometry args={[0.08, 10, 8]} />
    <T.MeshBasicMaterial color="#db6d28" />
  </T.Mesh>
{/if}

<!-- the aeroplane: yaw group, then bank/pitch inside it, nose along +X -->
<T.Group position={shown} rotation.y={yaw}>
  <!-- with the nose on +X, rolling is about X (+ = right wing down) and pitching about Z (+ = nose up) -->
  <T.Group rotation.x={bank} rotation.z={pitch} scale={PLANE_SCALE}>
    <T.Mesh geometry={parts.fuselage}>
      <T.MeshPhysicalMaterial color="#f4f6f8" metalness={0.1} roughness={0.35} clearcoat={0.7} />
    </T.Mesh>
    <T.Mesh geometry={parts.canopy} position={[0.1, 0.07, 0]}>
      <T.MeshPhysicalMaterial color="#1a2633" metalness={0.2} roughness={0.05} clearcoat={1} />
    </T.Mesh>
    <T.Mesh geometry={parts.wing} position={[0.04, -0.045, 0]}>
      <T.MeshPhysicalMaterial color="#eaf0f6" metalness={0.1} roughness={0.35} />
    </T.Mesh>
    <T.Mesh geometry={parts.hstab} position={[-0.5, 0.02, 0]}>
      <T.MeshStandardMaterial color="#eaf0f6" roughness={0.4} />
    </T.Mesh>
    <T.Mesh geometry={parts.fin} position={[-0.52, 0.04, 0]}>
      <T.MeshStandardMaterial color="#2f81f7" roughness={0.4} />
    </T.Mesh>
    <T.Mesh geometry={parts.spinner} position={[0.42, 0, 0]}>
      <T.MeshStandardMaterial color="#2f81f7" roughness={0.3} />
    </T.Mesh>
    <T.Group position={[0.44, 0, 0]} rotation.x={spin}>
      {#each [0, 1, 2] as b}
        <T.Mesh geometry={parts.blade} rotation.x={(b * Math.PI * 2) / 3} position.y={0.17}>
          <T.MeshStandardMaterial color="#20262e" roughness={0.6} />
        </T.Mesh>
      {/each}
    </T.Group>
  </T.Group>
</T.Group>

<style>
  .tag {
    font: 10px ui-monospace, monospace;
    white-space: nowrap;
    pointer-events: none;
    text-shadow: 0 0 5px #000;
  }
  .wp {
    color: #c8d3e0;
  }
  .wp.next {
    color: #f0883e;
    font-weight: 700;
  }
  .gnd {
    color: #e2b6e6;
  }
  .sat {
    color: #56d364;
    text-align: center;
    display: inline-block;
  }
  .sat em {
    display: block;
    font-style: normal;
    font-size: 9px;
    color: #9fd8a8;
  }
</style>
