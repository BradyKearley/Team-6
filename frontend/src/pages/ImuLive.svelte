<script>
  /**
   * IMU, live from the Raspberry Pi: the MPU6050's orientation turns the 3D aircraft.
   *
   * Data path: MPU6050 --I2C--> Pi agent (pi/team6_pi_agent.py, Mahony filter at 100 Hz)
   *   --HTTP over Wi-Fi--> server (app/pi.py polls /imu 20x a second) --/ws 10x a second--> here.
   * "Zero" is done in the browser: whatever pose the sensor is in becomes level, nose forward.
   */
  import { untrack } from 'svelte';
  import { Canvas } from '@threlte/core';
  import ImuScene from '../scene/ImuScene.svelte';
  import PiConnect from '../lib/PiConnect.svelte';
  import { api } from '../lib/api.js';
  import { live } from '../lib/live.svelte.js';

  let { title } = $props();

  // ---- quaternions [w, x, y, z] ----
  const mul = (a, b) => [
    a[0] * b[0] - a[1] * b[1] - a[2] * b[2] - a[3] * b[3],
    a[0] * b[1] + a[1] * b[0] + a[2] * b[3] - a[3] * b[2],
    a[0] * b[2] - a[1] * b[3] + a[2] * b[0] + a[3] * b[1],
    a[0] * b[3] + a[1] * b[2] - a[2] * b[1] + a[3] * b[0],
  ];
  const conj = (q) => [q[0], -q[1], -q[2], -q[3]];
  const deg = (r) => (r * 180) / Math.PI;
  // Shown the aviation way: roll + = right wing down, pitch + = nose up, yaw + = turning right.
  // (The quaternion's frame is X forward, Y left, Z up, where nose-up and right turns are negative.)
  function euler([w, x, y, z]) {
    return {
      roll: deg(Math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))),
      pitch: -deg(Math.asin(Math.max(-1, Math.min(1, 2 * (w * y - z * x))))),
      yaw: -deg(Math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))),
    };
  }

  let pi = $derived(live.data?.pi);
  let imu = $derived(pi?.imu);
  let mode = $derived(pi?.mode ?? 'off');
  let flowing = $derived((mode === 'live' || mode === 'sim') && imu?.q && pi.age_s != null && pi.age_s < 1.5);

  let zero = $state(null); // the raw orientation that counts as "level, nose forward"
  let q = $derived(imu?.q ? (zero ? mul(imu.q, conj(zero)) : imu.q) : null);
  let ang = $derived(q ? euler(q) : null);

  // ~10 s of history for the strip chart
  const N = 100;
  let hist = $state([]);
  $effect(() => {
    const a = ang;
    if (a && flowing) untrack(() => (hist = [...hist.slice(-(N - 1)), { ...a }]));
  });
  const path = (key, span) =>
    hist.map((h, i) => `${i ? 'L' : 'M'}${((i / (N - 1)) * 300).toFixed(1)},${(40 - (h[key] / span) * 38).toFixed(1)}`).join(' ');

  let note = $state('');
  async function recal() {
    note = '';
    try {
      await api.piCalibrate();
      note = 'Calibrating for 2 s: keep the sensor still.';
      zero = null;
    } catch (e) {
      note = e.message;
    }
  }

  const f = (v, d = 1) => (v == null ? '–' : v.toFixed(d));
</script>

<div class="page">
  <section class="view">
    <Canvas>
      <ImuScene {q} live={flowing} />
    </Canvas>

    <div class="hud">
      {#if ang}
        <div><span>ROLL</span><b>{f(ang.roll)}°</b></div>
        <div><span>PITCH</span><b>{f(ang.pitch)}°</b></div>
        <div><span>YAW</span><b>{f(ang.yaw)}°</b></div>
      {:else}
        <p class="wait">
          {mode === 'off' ? 'Connect to the Pi on the right (or try the simulated IMU) and the plane follows the sensor.' : pi?.imu_error || 'Waiting for IMU data…'}
        </p>
      {/if}
    </div>
    {#if mode === 'live' && !flowing && pi?.imu_error}
      <div class="error">{pi.imu_error}</div>
    {/if}
    {#if imu?.calibrating}<div class="cal">Calibrating: keep the sensor still…</div>{/if}

    {#if hist.length > 1}
      <svg class="chart" viewBox="0 0 300 80" preserveAspectRatio="none">
        <line x1="0" x2="300" y1="40" y2="40" />
        <path d={path('roll', 90)} style:stroke="#4ea1ff" />
        <path d={path('pitch', 90)} style:stroke="#3fb950" />
        <path d={path('yaw', 180)} style:stroke="#f0883e" />
      </svg>
      <div class="legend"><i style:background="#4ea1ff"></i>roll <i style:background="#3fb950"></i>pitch <i style:background="#f0883e"></i>yaw · last 10 s</div>
    {/if}
  </section>

  <aside>
    <section>
      <h2 class="title">{title}</h2>
      <p class="lede">
        An MPU6050 on the Raspberry Pi measures rotation (gyro) and gravity (accelerometer). The Pi blends them into an
        orientation 100 times a second and the plane here turns with it. There's no magnetometer, so yaw is relative to
        where the sensor pointed at start-up and slowly drifts. Roll and pitch stay locked to gravity.
      </p>
    </section>

    <PiConnect onchange={() => (zero = null)} />

    <section>
      <h2>Orientation</h2>
      <button class="wide" onclick={() => (zero = imu?.q ? [...imu.q] : null)} disabled={!imu?.q}>
        Zero: make this pose level, nose forward
      </button>
      {#if zero}<button class="link" onclick={() => (zero = null)}>Clear zero</button>{/if}
      <button class="wide" onclick={recal} disabled={mode !== 'live' && mode !== 'sim'}>Recalibrate gyro (hold still 2 s)</button>
      {#if note}<p class="sub top">{note}</p>{/if}
      <p class="sub top">
        Mount the sensor with its printed X arrow toward the nose. If it's mounted any other way, set the model level and
        press Zero.
      </p>
    </section>

    {#if imu}
      <section>
        <h2>Raw sensor</h2>
        <div class="kv mono">
          <span>accel X/Y/Z</span><span>{imu.accel?.map((v) => f(v, 2)).join(' / ')} m/s²</span>
          <span>gyro X/Y/Z</span><span>{imu.gyro?.map((v) => f(deg(v), 1)).join(' / ')} °/s</span>
          {#if imu.temp_c != null}<span>temperature</span><span>{f(imu.temp_c)} °C</span>{/if}
          <span>filter rate</span><span>{f(imu.rate_hz, 0)} Hz</span>
          <span>data age</span><span class:stale={pi.age_s > 1}>{f(pi.age_s, 2)} s</span>
        </div>
      </section>
    {/if}
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
  }
  .hud {
    position: absolute;
    top: 12px;
    left: 12px;
    display: flex;
    gap: 10px;
    z-index: 2;
  }
  .hud div {
    background: rgba(10, 17, 28, 0.85);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 6px 12px;
    min-width: 92px;
  }
  .hud span {
    display: block;
    font: 700 10px system-ui;
    letter-spacing: 0.08em;
    color: var(--muted);
  }
  .hud b {
    font: 600 20px ui-monospace, Consolas, monospace;
  }
  .wait {
    max-width: 420px;
    color: var(--muted);
    background: rgba(10, 17, 28, 0.85);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 8px 12px;
    margin: 0;
    font-size: 13px;
  }
  .cal,
  .error {
    position: absolute;
    top: 70px;
    left: 12px;
    padding: 6px 10px;
    border-radius: 8px;
    font-size: 12px;
    z-index: 2;
  }
  .cal {
    background: #3a2e0c;
    border: 1px solid var(--warn);
    color: #f2d58a;
  }
  .error {
    background: #3d1418;
    border: 1px solid var(--bad);
    color: #ffb3b0;
    right: 12px;
  }
  .chart {
    position: absolute;
    left: 12px;
    bottom: 34px;
    width: min(420px, calc(100% - 24px));
    height: 90px;
    background: rgba(10, 17, 28, 0.85);
    border: 1px solid var(--border);
    border-radius: 8px;
  }
  .chart line {
    stroke: var(--border);
  }
  .chart path {
    fill: none;
    stroke-width: 1.6;
    vector-effect: non-scaling-stroke;
  }
  .legend {
    position: absolute;
    left: 14px;
    bottom: 12px;
    font-size: 11px;
    color: var(--muted);
    display: flex;
    align-items: center;
    gap: 5px;
  }
  .legend i {
    width: 10px;
    height: 3px;
    border-radius: 2px;
    margin-left: 6px;
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
  h2 {
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--muted);
    margin: 0 0 8px;
  }
  h2.title {
    font-size: 16px;
    text-transform: none;
    letter-spacing: 0;
    color: var(--text);
  }
  .lede {
    color: var(--muted);
    font-size: 12px;
    line-height: 1.5;
    margin: 0;
  }
  .sub {
    color: var(--muted);
    font-size: 11px;
    line-height: 1.5;
    margin: 0;
  }
  .sub.top {
    margin-top: 8px;
  }
  button.wide {
    width: 100%;
    margin-top: 6px;
  }
  button.link {
    background: none;
    border: none;
    padding: 4px 0 0;
    color: var(--accent);
    font-size: 12px;
  }
  .kv {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 4px 12px;
    font-size: 12px;
  }
  .kv span:nth-child(odd) {
    color: var(--muted);
  }
  .mono {
    font-family: ui-monospace, Consolas, monospace;
  }
  .stale {
    color: var(--bad);
  }
  @media (max-width: 760px) {
    .page {
      flex-direction: column;
    }
    .view {
      min-height: 55vh;
    }
    aside {
      width: 100%;
      border-left: none;
      border-top: 1px solid var(--border);
    }
  }
</style>
