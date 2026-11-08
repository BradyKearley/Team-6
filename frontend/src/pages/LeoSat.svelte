<script>
  // LEO Satellite: runs server/app/leo_sat.py through POST /api/leo/simulate and plays the
  // result back on the globe. The question it answers: when GPS dies, does listening to
  // Iridium Doppler shifts beat dead-reckoning on the IMU alone?
  import SpaceView from '../lib/SpaceView.svelte';
  import { api } from '../lib/api.js';
  import { hint as tip } from '../lib/hint.js';   // `hint` below is the error-message helper

  let { title } = $props();

  // One colour per entity across the whole page (checked for dark-mode contrast + CVD).
  const C = { fused: '#2f81f7', imu: '#db6d28', truth: '#8b949e', sats: '#2ea043' };

  // What the server accepts (see LeoIn in app/main.py). Defaults match SimConfig.
  const DEFAULTS = {
    minutes: 20,
    dt: 1,
    min_elev_deg: 10,
    speed: 250,
    alt_m: 10000,
    start_lat: 45,
    start_lon: -60,
    heading_deg: 50,
    turn_start_s: 400,
    turn_rate_deg_s: 3,
    turn_total_deg: 90,
    accel_noise: 0.005,
    doppler_sigma_hz: 5,
    clock_rw_hz_rts: 0.2,
    orbit_err_m: 0,
    seed: 42,
  };

  const PRESETS = [
    { name: 'Default', p: {} },
    { name: 'Long outage', p: { minutes: 60 } },
    { name: 'Stale TLEs', p: { orbit_err_m: 1000 } },
    { name: 'Noisy radio', p: { doppler_sigma_hz: 50 } },
    { name: 'Clear sky', p: { min_elev_deg: 5 } },
  ];

  let form = $state({ ...DEFAULTS });
  let refreshTle = $state(false);
  let res = $state(null);
  let busy = $state(false);
  let error = $state('');
  let showFlight = $state(false);
  let frame = $state(0);
  let playing = $state(true);

  async function run() {
    busy = true;
    error = '';
    try {
      res = await api.leoSimulate({ ...form, refresh_tle: refreshTle });
      refreshTle = false;
    } catch (e) {
      error = e.message;
    } finally {
      busy = false;
    }
  }

  // Why it failed, in terms you can act on.
  let hint = $derived.by(() => {
    const e = error.toLowerCase();
    if (!e) return '';
    if (e.includes('not found') || e.includes('404'))
      return 'The server is running an older build without the LEO routes. Restart it: python -m app.main --source sim';
    if (e.includes('missing package')) return 'Install the server requirements first: pip install -r requirements.txt';
    if (e.includes('orbit data') || e.includes('tle') || e.includes('urlopen') || e.includes('celestrak'))
      return 'Orbit elements come from celestrak.org on the first run — that needs the internet once. The server keeps a copy afterwards.';
    if (e.includes('failed to fetch')) return 'The Python server is not reachable. Start it in server/ with: python -m app.main --source sim';
    return 'Check the server terminal for the traceback.';
  });

  function preset(p) {
    form = { ...DEFAULTS, ...p };
    run();
  }

  run(); // show something as soon as the page opens

  // Both paths on the globe: the IMU drifting away, and the Doppler-aided estimate.
  let tracks = $derived(
    !res
      ? []
      : [
          { key: 'imu', label: 'IMU only', color: C.imu, km: res.scene.plane_dr_km },
          { key: 'fused', label: 'IMU + Doppler', color: C.fused, km: res.scene.plane_est_km, primary: true },
        ],
  );

  const km = (m) => (m == null ? '—' : Math.abs(m) >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${m.toFixed(0)} m`);

  // Where each number in the Result panel comes from.
  const WHY = {
    fused: 'Distance between the filter\'s final position and the truth. The filter saw the IMU plus the Doppler shift of every Iridium satellite above the mask.',
    imu: 'The same IMU integrated on its own with nothing correcting it. Almost all of this is the accelerometer bias, doubly integrated.',
    gain: 'The IMU-only error divided by the fused error. Below 1x the Doppler made things worse, which is what happens when the satellite positions are wrong.',
    mean: 'Average fused error over the second half of the run, once the filter has settled and stopped reacting to its initial uncertainty.',
    sats: 'Satellites above the elevation mask, from real Iridium NEXT orbit data. Each one contributes a Doppler measurement per step.',
    clock: 'How far the filter\'s estimate of the receiver\'s oscillator offset ended up from the truth. It has to solve for this at the same time as position.',
  };
  let gain = $derived(res ? res.summary.err_dr_final_m / Math.max(res.summary.err_ekf_final_m, 1e-6) : null);

  function csv() {
    const t = res.track,
      q = res.series;
    const head = 'time_min,est_lat,est_lon,est_alt_m,true_lat,true_lon,err_fused_m,err_imu_only_m,sats_in_view';
    const rows = q.t_min.map((v, i) =>
      [v, t.est_lat[i], t.est_lon[i], t.est_alt_m[i], t.true_lat[i], t.true_lon[i], q.err_ekf_m[i], q.err_dr_m[i], q.n_vis[i]].join(','),
    );
    const url = URL.createObjectURL(new Blob([[head, ...rows].join('\n')], { type: 'text/csv' }));
    const a = Object.assign(document.createElement('a'), { href: url, download: 'leo_estimated_track.csv' });
    a.click();
    URL.revokeObjectURL(url);
  }
</script>

<div class="page">
  <section class="view">
    {#if res}
      <SpaceView scene={res.scene} {tracks} bind:frame bind:playing name="leo" {busy} />
    {:else}
      <p class="loading">{busy ? 'Propagating the constellation…' : 'No run yet.'}</p>
    {/if}
    {#if error}
      <div class="error" role="alert"><b>{error}</b><span>{hint}</span></div>
    {/if}
  </section>

  <aside>
    <section>
      <h2>{title}</h2>
      <p class="lede">
        GPS is gone. Each Iridium satellite's Doppler shift constrains the plane's velocity along the line to it, and an extended Kalman
        filter fuses those with the IMU — which on its own drifts away without limit.
      </p>
      <ul class="key">
        <li><i class="ln" style:background={C.truth}></i>true flight path</li>
        <li><i class="ln" style:background={C.imu}></i>IMU only</li>
        <li><i class="ln" style:background={C.fused}></i>IMU + Doppler</li>
        <li><i class="dot" style:background={C.sats}></i>satellite in view</li>
      </ul>
    </section>

    {#if res}
      <section>
        <h2>Result</h2>
        <div class="kv mono">
          <span class="hinted" use:tip={WHY.fused}>IMU + Doppler</span><span class="big" style:color={C.fused}>{km(res.summary.err_ekf_final_m)}</span>
          <span class="hinted" use:tip={WHY.imu}>IMU only</span><span class="big" style:color={C.imu}>{km(res.summary.err_dr_final_m)}</span>
          <span class="hinted" use:tip={WHY.gain}>Improvement</span><span>{gain >= 10 ? Math.round(gain) : gain.toFixed(1)}× {gain >= 1 ? 'closer' : 'worse'}</span>
          <span class="hinted" use:tip={WHY.mean}>Mean, 2nd half</span><span>{km(res.summary.err_ekf_mean_second_half_m)}</span>
          <span class="hinted" use:tip={WHY.sats}>Sats in view</span><span>{res.summary.sats_mean} avg · {res.summary.sats_min}–{res.summary.sats_max}</span>
          <span class="hinted" use:tip={WHY.clock}>Clock error</span><span>{res.summary.clock_err_final_hz.toFixed(2)} Hz</span>
        </div>
        <p class="tle mono">
          {res.tle.satellites} satellites · elements {res.tle.age_days} days old · {res.summary.steps} steps in {res.runtime_s}s
        </p>
      </section>

      <section>
        <h2>Error at each checkpoint</h2>
        <table class="mono">
          <thead><tr><th>Elapsed</th><th>IMU only</th><th>+ Doppler</th></tr></thead>
          <tbody>
            {#each res.summary.checkpoints as c}
              <tr>
                <td>{c.minute} min</td>
                <td style:color={C.imu}>{km(c.imu_only_m)}</td>
                <td style:color={C.fused}>{km(c.fused_m)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </section>

      <section>
        <h2>Final position</h2>
        <div class="kv mono">
          <span>Estimated</span><span>{res.summary.final.est_lat.toFixed(4)}°, {res.summary.final.est_lon.toFixed(4)}°</span>
          <span>True</span><span>{res.summary.final.true_lat.toFixed(4)}°, {res.summary.final.true_lon.toFixed(4)}°</span>
          <span>Altitude</span><span>{res.summary.final.est_alt_m.toFixed(0)} m vs {res.summary.final.true_alt_m.toFixed(0)} m</span>
        </div>
        <button class="wide" onclick={csv}>Download track CSV</button>
      </section>
    {/if}

    <section>
      <h2>Scenario</h2>
      <div class="chips">
        {#each PRESETS as p}
          <button onclick={() => preset(p.p)} disabled={busy}>{p.name}</button>
        {/each}
      </div>
    </section>

    <section>
      <h2>Outage</h2>
      <label>GPS outage <span class="u">min</span><input type="number" min="1" max="120" step="1" bind:value={form.minutes} /></label>
      <label>Filter step <span class="u">s</span><input type="number" min="0.25" max="10" step="0.25" bind:value={form.dt} /></label>
      <label>Elevation mask <span class="u">deg</span><input type="number" min="0" max="80" step="1" bind:value={form.min_elev_deg} /></label>
    </section>

    <section>
      <h2>Flight</h2>
      <label>Ground speed <span class="u">m/s</span><input type="number" min="0" max="1000" step="10" bind:value={form.speed} /></label>
      <label>Altitude <span class="u">m</span><input type="number" min="0" max="20000" step="500" bind:value={form.alt_m} /></label>
      <label>Start latitude <span class="u">deg</span><input type="number" min="-85" max="85" step="1" bind:value={form.start_lat} /></label>
      <label>Start longitude <span class="u">deg</span><input type="number" min="-180" max="180" step="1" bind:value={form.start_lon} /></label>
      <button class="link" onclick={() => (showFlight = !showFlight)}>{showFlight ? 'Hide' : 'Show'} the turn</button>
      {#if showFlight}
        <label>Heading <span class="u">deg</span><input type="number" min="0" max="360" step="5" bind:value={form.heading_deg} /></label>
        <label>Turn starts at <span class="u">s</span><input type="number" min="0" step="10" bind:value={form.turn_start_s} /></label>
        <label>Turn rate <span class="u">deg/s</span><input type="number" min="0" max="20" step="0.5" bind:value={form.turn_rate_deg_s} /></label>
        <label>Turn total <span class="u">deg</span><input type="number" min="0" max="360" step="5" bind:value={form.turn_total_deg} /></label>
      {/if}
    </section>

    <section>
      <h2>Errors</h2>
      <label title="White noise on each accelerometer sample">IMU noise <span class="u">m/s²</span><input type="number" min="0" max="1" step="0.001" bind:value={form.accel_noise} /></label>
      <label title="How noisy each Doppler measurement is">Doppler noise <span class="u">Hz</span><input type="number" min="0.1" max="500" step="1" bind:value={form.doppler_sigma_hz} /></label>
      <label title="How fast the receiver's oscillator wanders">Clock drift <span class="u">Hz/√s</span><input type="number" min="0" max="50" step="0.1" bind:value={form.clock_rw_hz_rts} /></label>
      <label title="How wrong the satellite positions the filter uses are; real TLEs are 0.5–2 km off">Orbit error <span class="u">m</span><input type="number" min="0" max="20000" step="100" bind:value={form.orbit_err_m} /></label>
      <label title="Same seed = same run">Random seed<input type="number" min="0" step="1" bind:value={form.seed} /></label>
    </section>

    <section>
      <button class="primary wide" onclick={run} disabled={busy}>{busy ? 'Running…' : 'Run simulation'}</button>
      <button class="wide" onclick={() => preset({})} disabled={busy}>Reset to defaults</button>
      <label class="check"><input type="checkbox" bind:checked={refreshTle} /> Re-download orbit elements</label>
      <p class="hint">
        Physics and filter live in <code>server/app/leo_sat.py</code> — run it directly with
        <code>python -m app.leo_sat --minutes 20</code> for the matplotlib plots.
      </p>
    </section>
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
    isolation: isolate;
    background: #070d16;
  }
  .loading {
    position: absolute;
    inset: 0;
    display: grid;
    place-items: center;
    color: var(--muted);
  }
  .error {
    position: absolute;
    bottom: 60px;
    left: 12px;
    right: 12px;
    display: flex;
    flex-direction: column;
    gap: 4px;
    background: #3d1418;
    border: 1px solid var(--bad);
    color: #ffb3b0;
    padding: 8px 10px;
    border-radius: 8px;
    font-size: 12px;
    z-index: 3;
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
  .lede {
    color: var(--muted);
    font-size: 12px;
    line-height: 1.5;
    margin: 0 0 8px;
  }
  .key {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 12px;
    list-style: none;
    margin: 0;
    padding: 0;
    font-size: 11px;
    color: var(--muted);
  }
  .key li {
    display: flex;
    align-items: center;
    gap: 5px;
  }
  .key .ln {
    width: 14px;
    height: 2px;
  }
  .key .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
  }
  .kv {
    display: grid;
    grid-template-columns: 104px 1fr;
    gap: 3px 10px;
    font-size: 12px;
    align-items: baseline;
  }
  .kv span:nth-child(odd) {
    color: var(--muted);
  }
  .kv .big {
    font-size: 17px;
    font-weight: 600;
  }
  .tle {
    font-size: 11px;
    color: var(--muted);
    margin: 8px 0 0;
    line-height: 1.5;
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
    padding: 3px 4px;
    border-bottom: 1px solid var(--border);
  }
  td {
    padding: 3px 4px;
  }
  aside label {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
    margin-bottom: 5px;
    color: var(--muted);
  }
  aside label input[type='number'] {
    width: 76px;
    flex: none;
    margin-left: auto;
    text-align: right;
  }
  .u {
    font-size: 10px;
    opacity: 0.7;
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .chips button {
    font-size: 12px;
    padding: 3px 9px;
  }
  button.wide {
    width: 100%;
    margin-top: 8px;
  }
  button.link {
    background: none;
    border: none;
    padding: 0;
    color: var(--accent);
    font-size: 12px;
    margin-bottom: 6px;
  }
  .check {
    color: var(--muted);
  }
  .check input {
    width: auto;
    accent-color: var(--accent);
  }
  .hint {
    font-size: 11px;
    color: var(--muted);
    line-height: 1.5;
  }
  code {
    background: var(--panel-2);
    padding: 1px 4px;
    border-radius: 3px;
  }
  @media (max-width: 900px) {
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
