<script>
  // Sensor Fusion: does combining the techniques actually help?
  //
  // server/app/fusion.py flies ONE truth trajectory and then runs the same Kalman filter
  // over it four times - IMU alone, + Iridium Doppler, + ground tower ranges, + both -
  // with identical noise draws, so the only difference is what the filter could listen to.
  import SpaceView from '../lib/SpaceView.svelte';
  import SpreadInset from '../lib/SpreadInset.svelte';
  import { api } from '../lib/api.js';
  import { hint as tip } from '../lib/hint.js';

  let { title } = $props();

  // Plain names for the four runs, and one hue each (all four pass the contrast and
  // colour-blindness checks against the dark map).
  const WAYS = {
    imu: { name: 'IMU alone', short: 'IMU alone', note: 'nothing but the accelerometers', color: '#db6d28',
      why: 'The accelerometers integrated twice, with no outside correction. Its error grows as the square of time, so it is the baseline everything else is measured against.' },
    doppler: { name: 'IMU + satellites', short: '+ satellites', note: 'Iridium Doppler shifts', color: '#2f81f7',
      why: 'The same IMU, corrected by the Doppler shift of every Iridium satellite above the elevation mask. Doppler constrains speed along the line to each satellite; position comes from how that geometry sweeps during a pass.' },
    beacons: { name: 'IMU + ground towers', short: '+ towers', note: 'distance to each tower', color: '#bc5cc4',
      why: 'The same IMU, corrected by a measured distance to each ground station whose position is known. This is the cell-tower page\'s method at aviation scale.' },
    both: { name: 'All three together', short: 'All three', note: 'satellites and towers', color: '#1fa394',
      why: 'Both corrections feeding one filter. Satellites come and go while towers fall behind, so together they cover each other\'s gaps.' },
  };
  const STAT_WHY = {
    sats: 'Average number of Iridium satellites above the elevation mask over the flight, from real orbit data.',
    towers: 'Average number of ground stations within range, out of the ones placed along the route.',
    outage: 'How long GPS was unavailable, and how many filter updates that came to.',
    runtime: 'How long the server took to run all four filters over the same flight.',
  };
  const SAT = '#adbac7'; // satellites stay neutral so the four hues stay free

  // Measurement quality in one choice, instead of two noise figures.
  const QUALITY = {
    good: { doppler_sigma_hz: 2, beacon_range_sigma_m: 10 },
    typical: { doppler_sigma_hz: 5, beacon_range_sigma_m: 30 },
    rough: { doppler_sigma_hz: 50, beacon_range_sigma_m: 500 },
  };

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
    beacon_count: 4,
    beacon_spacing_km: 70,
    beacon_range_sigma_m: 30,
    beacon_max_range_km: 250,
  };

  const PRESETS = [
    { name: 'Default', p: {} },
    { name: 'Hour-long outage', p: { minutes: 60 } },
    { name: 'One lonely tower', p: { beacon_count: 1 } },
    { name: 'No towers at all', p: { beacon_count: 0 } },
    { name: 'Everything is noisy', p: { ...QUALITY.rough } },
    { name: 'Stale orbit data', p: { orbit_err_m: 1000 } },
  ];

  let form = $state({ ...DEFAULTS });
  let res = $state(null);
  let busy = $state(false);
  let error = $state('');
  let shown = $state({ imu: true, doppler: false, beacons: false, both: true });
  let frame = $state(0);
  let playing = $state(true);

  async function run() {
    busy = true;
    error = '';
    try {
      res = await api.fusionSimulate(form);
    } catch (e) {
      error = e.message;
    } finally {
      busy = false;
    }
  }

  let hint = $derived.by(() => {
    const e = error.toLowerCase();
    if (!e) return '';
    if (e.includes('not found') || e.includes('404'))
      return 'The server is running an older build without /api/fusion. Restart it: python -m app.main --source sim';
    if (e.includes('missing package')) return 'Install the server requirements first: pip install -r requirements.txt';
    if (e.includes('orbit data') || e.includes('tle') || e.includes('celestrak'))
      return 'Orbit elements come from celestrak.org on the first run — that needs the internet once.';
    if (e.includes('failed to fetch')) return 'The Python server is not reachable. Start it in server/ with: python -m app.main --source sim';
    return 'Check the server terminal for the traceback.';
  });

  function preset(p) {
    form = { ...DEFAULTS, ...p };
    run();
  }

  run();

  // ---- the answer --------------------------------------------------------------
  let combos = $derived(res?.combos ?? []);
  let by = $derived(Object.fromEntries(combos.map((c) => [c.key, c])));
  let best = $derived(res?.summary.best);
  let helped = $derived(best === 'both');
  let nothingToCombine = $derived(res?.config.beacon_count === 0); // only one aid available

  let tracks = $derived(
    combos
      .filter((c) => shown[c.key])
      .map((c) => ({ key: c.key, label: WAYS[c.key].name, short: WAYS[c.key].short, color: WAYS[c.key].color, km: c.km, primary: c.key === best })),
  );

  // Bars share one log scale from 1 m up to the next round decade, so 9 m and 12 km fit
  // in the same picture. Decade ticks are drawn underneath.
  // All four bars share one log scale from 1 m to the worst error, so 9 m and 12 km fit in
  // the same picture. Decade ticks are placed by the same scale, not spread evenly.
  let top = $derived(Math.max(...combos.map((c) => c.final_m), 10));
  const bar = (m, topM) => `${Math.max(2, (Math.log10(Math.max(m, 1)) / Math.log10(topM)) * 100)}%`;
  let decades = $derived(
    Array.from({ length: Math.floor(Math.log10(top)) + 1 }, (_, i) => Math.pow(10, i)).filter((d) => d > 1),
  );
  const dist = (m) => (m == null ? '—' : Math.abs(m) >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${m.toFixed(0)} m`);
  const tick = (m) => (m >= 1000 ? `${m / 1000}km` : `${m}m`);

  // Which quality bundle the current settings match (if any).
  let quality = $derived(
    Object.entries(QUALITY).find(
      ([, q]) => q.doppler_sigma_hz === form.doppler_sigma_hz && q.beacon_range_sigma_m === form.beacon_range_sigma_m,
    )?.[0] ?? 'custom',
  );
  function setQuality(k) {
    if (QUALITY[k]) form = { ...form, ...QUALITY[k] };
  }

  function csv() {
    const head = ['minutes', ...combos.map((c) => `${c.key}_err_m`)].join(',');
    const rows = res.scene.frames_min.map((t, i) => [t, ...combos.map((c) => c.err_m[i])].join(','));
    const url = URL.createObjectURL(new Blob([[head, ...rows].join('\n')], { type: 'text/csv' }));
    const a = Object.assign(document.createElement('a'), { href: url, download: 'fusion_comparison.csv' });
    a.click();
    URL.revokeObjectURL(url);
  }
</script>

<div class="page">
  <section class="view">
    {#if res}
      <SpaceView scene={res.scene} {tracks} bind:frame bind:playing satColor={SAT} name="fusion" defaultFocus="flight" {busy} />
      {#if tracks.length}
        <SpreadInset truthKm={res.scene.plane_true_km} {tracks} {frame} />
      {/if}
    {:else}
      <p class="loading">{busy ? 'Running four filters over one flight…' : 'No run yet.'}</p>
    {/if}
    {#if error}
      <div class="error" role="alert"><b>{error}</b><span>{hint}</span></div>
    {/if}
  </section>

  <aside>
    <section>
      <h2>{title}</h2>
      <p class="lede">
        One flight. GPS fails. The same Kalman filter runs four times over it — fed the same IMU and the same noise, but allowed to listen
        to different things. Whatever separates them is the sensors, not luck.
      </p>
      <ul class="key">
        <li><i class="ln"></i>the true flight path</li>
        <li><i class="dot" style:background={SAT}></i>satellite (brighter = in view)</li>
        <li><i class="sq" style:background={WAYS.beacons.color}></i>ground tower (ring = its range)</li>
      </ul>
    </section>

    {#if res}
      <section class="answer">
        <h2>Does combining help?</h2>
        <p class="verdict">
          <b class="yes" style:color={WAYS[best].color}>{nothingToCombine ? 'Nothing to combine' : helped ? 'Yes' : 'Not here'}</b>
          <span class="num" style:color={WAYS[best].color}>{dist(by[best]?.final_m)}</span>
        </p>
        <p class="sub">
          {WAYS[best].name} was closest after {res.config.minutes} minutes without GPS —
          {#if nothingToCombine}
            but with no ground towers switched on, the satellites were the only aid the filter had. Turn some towers back on to see whether
            two sources beat one.
          {:else if helped}
            better than satellites alone ({dist(by.doppler?.final_m)}) or towers alone ({dist(by.beacons?.final_m)}), and
            {Math.round(by.imu?.final_m / Math.max(by[best]?.final_m, 1e-6))}× closer than the IMU by itself.
          {:else}
            combining the two didn't beat it with these settings.
          {/if}
        </p>
      </section>

      <section>
        <h2>How far off each one ended</h2>
        <div class="bars">
          {#each combos as c (c.key)}
            <div class="way" class:best={c.key === best}>
              <label>
                <input
                  type="checkbox"
                  checked={shown[c.key]}
                  aria-label={`Draw ${WAYS[c.key].name} on the map`}
                  onchange={(e) => (shown = { ...shown, [c.key]: e.currentTarget.checked })}
                />
                <i style:background={WAYS[c.key].color}></i>
                <span class="name hinted" use:tip={WAYS[c.key].why}>{WAYS[c.key].name}</span>
                <span class="val mono">{dist(c.final_m)}</span>
              </label>
              <div class="meter"><span style:width={bar(c.final_m, top)} style:background={WAYS[c.key].color}></span></div>
              <span class="note">{WAYS[c.key].note}</span>
            </div>
          {/each}
          <div class="scale" aria-hidden="true">
            {#each decades as d}<span style:left={bar(d, top)}>{tick(d)}</span>{/each}
          </div>
        </div>
        <p class="hint">Each step along the scale is 10× — the bars would be invisible otherwise. Tick a row to draw its path.</p>
        <button class="wide" onclick={csv}>Download comparison CSV</button>
      </section>

      <section>
        <h2>What each one brings</h2>
        <ul class="sensors">
          <li>
            <i style:background={WAYS.imu.color}></i>
            <span><b>IMU</b> — fast and smooth, but its error grows for as long as it runs alone.</span>
          </li>
          <li>
            <i style:background={WAYS.doppler.color}></i>
            <span>
              <b>Satellites</b> — the Doppler shift of a pass pins down how fast you're moving along the line to it.
              {res.summary.sats_mean} in view on average here.
            </span>
          </li>
          <li>
            <i style:background={WAYS.beacons.color}></i>
            <span>
              <b>Ground towers</b> — a distance to each station whose position you know. That's the cell-tower page's trick, scaled up to
              aviation: {res.summary.beacons_mean} of {res.summary.beacons_total} in range on average.
            </span>
          </li>
        </ul>
        <p class="note">
          Neither one fixes the IMU on its own schedule: satellites come and go, towers fall behind. Together they cover each other's gaps,
          which is why the teal line wins. The star camera isn't in here — it measures attitude, which this filter doesn't carry yet.
        </p>
      </section>
    {/if}

    <section>
      <h2>Try a different situation</h2>
      <div class="chips">
        {#each PRESETS as p}
          <button onclick={() => preset(p.p)} disabled={busy}>{p.name}</button>
        {/each}
      </div>
      <label class="row">GPS out for <span class="u">min</span><input type="number" min="1" max="120" step="5" bind:value={form.minutes} /></label>
      <label class="row">
        Ground towers
        <select bind:value={form.beacon_count}>
          <option value={0}>none</option>
          <option value={1}>1</option>
          <option value={2}>2</option>
          <option value={4}>4</option>
          <option value={8}>8</option>
        </select>
      </label>
      <label class="row">
        Measurement quality
        <select value={quality} onchange={(e) => setQuality(e.currentTarget.value)}>
          <option value="good">good</option>
          <option value="typical">typical</option>
          <option value="rough">rough</option>
          {#if quality === 'custom'}<option value="custom">custom</option>{/if}
        </select>
      </label>
      <button class="primary wide" onclick={run} disabled={busy}>{busy ? 'Running…' : 'Run it again'}</button>
      <button class="wide" onclick={() => preset({})} disabled={busy}>Reset everything</button>

      <details>
        <summary>Advanced settings</summary>
        <label>Ground speed <span class="u">m/s</span><input type="number" min="0" max="1000" step="10" bind:value={form.speed} /></label>
        <label>Altitude <span class="u">m</span><input type="number" min="0" max="20000" step="500" bind:value={form.alt_m} /></label>
        <label>Start latitude <span class="u">deg</span><input type="number" min="-85" max="85" step="1" bind:value={form.start_lat} /></label>
        <label>Start longitude <span class="u">deg</span><input type="number" min="-180" max="180" step="1" bind:value={form.start_lon} /></label>
        <label>Tower offset from route <span class="u">km</span><input type="number" min="1" max="500" step="5" bind:value={form.beacon_spacing_km} /></label>
        <label>Tower max range <span class="u">km</span><input type="number" min="10" max="2000" step="10" bind:value={form.beacon_max_range_km} /></label>
        <label>Tower ranging noise <span class="u">m</span><input type="number" min="1" max="5000" step="5" bind:value={form.beacon_range_sigma_m} /></label>
        <label>Doppler noise <span class="u">Hz</span><input type="number" min="0.1" max="500" step="1" bind:value={form.doppler_sigma_hz} /></label>
        <label>IMU noise <span class="u">m/s²</span><input type="number" min="0" max="1" step="0.001" bind:value={form.accel_noise} /></label>
        <label>Elevation mask <span class="u">deg</span><input type="number" min="0" max="80" step="1" bind:value={form.min_elev_deg} /></label>
        <label title="How wrong the satellite positions the filter uses are">Orbit error <span class="u">m</span><input type="number" min="0" max="20000" step="100" bind:value={form.orbit_err_m} /></label>
        <label title="Same seed = same run">Random seed<input type="number" min="0" step="1" bind:value={form.seed} /></label>
      </details>

      <p class="hint">
        The experiment lives in <code>server/app/fusion.py</code> — run it on its own with
        <code>python -m app.fusion --minutes 20</code>.
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
    width: 360px;
    flex-shrink: 0;
    background: var(--panel);
    border-left: 1px solid var(--border);
    overflow-y: auto;
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 18px;
  }
  .lede,
  .note,
  .sub {
    color: var(--muted);
    font-size: 12px;
    line-height: 1.55;
    margin: 0;
  }
  .note {
    margin-top: 8px;
  }
  .answer {
    background: var(--panel-2);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 10px 12px;
  }
  .verdict {
    display: flex;
    align-items: baseline;
    gap: 10px;
    margin: 0 0 4px;
  }
  .verdict .yes {
    font-size: 17px;
    font-weight: 600;
  }
  .verdict .num {
    font-size: 30px;
    font-weight: 600;
    line-height: 1.1;
  }
  .bars {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .way label {
    display: flex;
    align-items: center;
    gap: 7px;
    font-size: 12.5px;
    cursor: pointer;
    margin: 0;
  }
  .way input[type='checkbox'] {
    width: auto;
    margin: 0;
    accent-color: var(--accent);
    flex: none;
  }
  .way i {
    width: 9px;
    height: 9px;
    border-radius: 2px;
    flex: none;
  }
  .way .name {
    color: var(--text);
  }
  .way .val {
    margin-left: auto;
    color: var(--text);
    font-size: 12px;
  }
  .way.best .name {
    font-weight: 600;
  }
  .meter {
    height: 7px;
    background: #10161f;
    border-radius: 4px;
    overflow: hidden;
    margin: 4px 0 2px 16px;
  }
  .meter span {
    display: block;
    height: 100%;
    border-radius: 4px;
  }
  .way .note {
    display: block;
    margin: 0 0 0 16px;
    font-size: 11px;
    color: var(--muted);
  }
  .scale {
    position: relative;
    height: 13px;
    margin-left: 16px;
    font-size: 9px;
    color: var(--muted);
    border-top: 1px solid var(--border);
  }
  .scale span {
    position: absolute;
    top: 2px;
    transform: translateX(-50%);
    white-space: nowrap;
  }
  .sensors {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
    font-size: 12px;
    color: var(--muted);
    line-height: 1.5;
  }
  .sensors li {
    display: grid;
    grid-template-columns: 10px 1fr;
    gap: 8px;
  }
  .sensors i {
    width: 9px;
    height: 9px;
    border-radius: 2px;
    margin-top: 5px;
  }
  .sensors b {
    color: var(--text);
    font-weight: 600;
  }
  aside label {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
    margin-bottom: 6px;
    color: var(--muted);
  }
  label.row {
    margin-top: 8px;
  }
  aside label input[type='number'],
  aside label select {
    width: 84px;
    flex: none;
    margin-left: auto;
    text-align: right;
  }
  aside select {
    font: inherit;
    font-size: 12px;
    color: var(--text);
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 3px 4px;
  }
  .u {
    font-size: 10px;
    opacity: 0.7;
  }
  .key {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 12px;
    list-style: none;
    margin: 8px 0 0;
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
    background: #8b949e;
  }
  .key .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
  }
  .key .sq {
    width: 8px;
    height: 8px;
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
  details {
    margin-top: 12px;
  }
  summary {
    cursor: pointer;
    font-size: 12px;
    color: var(--muted);
    margin-bottom: 8px;
  }
  .hint {
    font-size: 11px;
    color: var(--muted);
    line-height: 1.5;
    margin-top: 10px;
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
