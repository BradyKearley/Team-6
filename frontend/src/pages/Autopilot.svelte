<script>
  // RL Autopilot: replay a flight flown by the PPO agent in flightsim/.
  //
  // The agent never saw a GPS fix or the true state - it flew on the navigation filter's
  // output, which comes from the IMU, the ground towers and Iridium Doppler. This page
  // just plays the recording back.
  import { Canvas } from '@threlte/core';
  import { ACESFilmicToneMapping } from 'three';
  import { untrack } from 'svelte';
  import FlightScene from '../scene/FlightScene.svelte';
  import SkyPlot from '../lib/SkyPlot.svelte';
  import NetworkView from '../lib/NetworkView.svelte';
  import { api } from '../lib/api.js';
  import { hint as tip } from '../lib/hint.js';   // `hint` below is the error-message helper

  let { title } = $props();

  let flight = $state(null);
  let flights = $state([]);
  let name = $state('fused');
  let error = $state('');
  let busy = $state(false);
  let frame = $state(0);
  let playing = $state(true);
  let speed = $state(1);
  let seek = $state(0);
  let camera = $state('chase');
  let showEstimate = $state(true);
  let showSensors = $state(true);
  let net = $state(null);      // the weights-over-training movie, loaded on demand
  let netBusy = $state(false);
  let netError = $state('');

  async function openNetwork() {
    netBusy = true;
    netError = '';
    try {
      net = await api.flightNetwork(name);
    } catch (e) {
      netError = e.message;
    } finally {
      netBusy = false;
    }
  }

  async function load(n = name) {
    busy = true;
    error = '';
    try {
      flights = (await api.flightList()).flights;
      flight = await api.flightReplay(n);
      name = n;
      frame = 0;
      seek++;
    } catch (e) {
      error = e.message;
      flight = null;
    } finally {
      busy = false;
    }
  }
  load();

  let frames = $derived(flight?.frames ?? []);
  let f = $derived(Math.min(Math.max(Math.round(frame) || 0, 0), Math.max(frames.length - 1, 0)));
  let now = $derived(frames[f]);

  // Play back at `speed` x real time.
  $effect(() => {
    if (!playing || !frames.length) return;
    seek;
    let pos = untrack(() => frame);
    let last = performance.now();
    let raf = requestAnimationFrame(function tick(t) {
      const n = frames.length;
      if (!n) return;
      const dt = (t - last) / 1000;
      last = t;
      pos += (dt * speed) / flight.dt;
      if (pos >= n - 1) {
        pos = 0; // loop the flight
      }
      frame = pos;
      raf = requestAnimationFrame(tick);
    });
    return () => cancelAnimationFrame(raf);
  });

  const m = (v) => (v == null ? '—' : `${Math.round(v)} m`);

  // Where every number on this page comes from. The observation rows get theirs from the
  // recording itself (flightsim/env.py), so they can never drift out of date.
  const ACT_HINT = [
    'The policy network\'s first output, scaled to the 45 deg bank limit. The aeroplane rolls towards it with a 1.5 s lag.',
    'Second output, scaled to the 8 deg flight path angle limit: climb or descend.',
    'Third output, mapped to 0-100% thrust. At 9 km it takes about 75% to hold 200 m/s.',
  ];
  const HUD = {
    spd: 'True airspeed from the simulation. The agent sees a noisier pitot version of this.',
    alt: 'True altitude. The agent never sees this - it only has the filter\'s estimate.',
    bank: 'True roll angle. The agent sees it through the AHRS, with noise and a bias.',
    wp: 'Which waypoint it is heading for. Counted on the TRUE position, not the estimate.',
    nav: 'How far the navigation filter\'s position is from the truth right now. The agent cannot see this number - it is the error inside everything it does see.',
    sats: 'Iridium satellites above the 10 deg elevation mask, each feeding one Doppler measurement per tick.',
    towers: 'Ground towers in range, each feeding one distance measurement per tick.',
  };
  const SUMMARY = [
    { k: 'Waypoints', why: 'Gates passed within 1.5 km, judged on the true position.',
      v: (f) => `${f.summary.captured} of ${f.route.length}${f.summary.crashed ? ' · crashed' : ''}` },
    { k: 'Flight time', why: 'Length of the recording. Flights are cut off at 300 s.',
      v: (f, fr) => `${((fr.length - 1) * f.dt).toFixed(0)} s` },
    { k: 'Altitude hold', why: 'Mean distance from the 9,000 m target, measured on the truth. Partly the agent flying badly and partly its altimeter being wrong.',
      v: (f) => `±${Math.round(f.summary.alt_error)} m mean` },
    { k: 'Nav error', why: 'Mean gap between the filter and the truth over the flight: the IMU, towers and satellites working together.',
      v: (f) => `${Math.round(f.summary.nav_error)} m mean` },
    { k: 'Wind', why: 'Constant for the flight, drawn at random, and never put into the observation. The agent can only infer it from ground speed differing from airspeed.',
      v: (f) => `${Math.hypot(f.wind[0], f.wind[1]).toFixed(0)} m/s, unseen` },
    { k: 'Towers', why: 'How many ground stations this map happened to get - between 1 and 5, scattered at random.',
      v: (f) => `${f.towers.length} on this map` },
    { k: 'Position', why: 'Where on Earth this flight took place, drawn per episode. Latitude changes how many Iridium satellites are overhead.',
      v: (f) => `${f.anchor[0].toFixed(1)}°, ${f.anchor[1].toFixed(1)}°` },
    { k: 'Sees', why: 'Which observation mode the policy was trained with: fused = the filter\'s answer, raw = unfiltered radios, truth = perfect knowledge.',
      v: (f) => f.obs_mode },
  ];
  let hint = $derived.by(() => {
    const e = error.toLowerCase();
    if (!e) return '';
    if (e.includes('no replay') || e.includes('404'))
      return 'Train and fly a policy first: cd flightsim && .venv/bin/python train.py && .venv/bin/python fly.py';
    if (e.includes('failed to fetch')) return 'The Python server is not reachable. Start it in server/ with: python -m app.main --source sim';
    return 'Check the server terminal for the traceback.';
  });
</script>

<div class="page">
  <section class="view">
    {#if flight}
      <Canvas toneMapping={ACESFilmicToneMapping}>
        <FlightScene {flight} {frame} {camera} {showEstimate} {showSensors} />
      </Canvas>

      <div class="toolbar">
        <div class="seg" role="group" aria-label="Camera">
          <button class:on={camera === 'chase'} onclick={() => (camera = 'chase')}>Chase</button>
          <button class:on={camera === 'free'} onclick={() => (camera = 'free')}>Free</button>
        </div>
        <label class="chip"><input type="checkbox" bind:checked={showEstimate} /> Filter's estimate</label>
        <label class="chip"><input type="checkbox" bind:checked={showSensors} /> What it's listening to</label>
        {#if flights.length > 1}
          <select bind:value={name} onchange={() => load(name)} aria-label="Recorded flight">
            {#each flights as fl}<option value={fl}>{fl}</option>{/each}
          </select>
        {/if}
      </div>

      <div class="hud mono">
        <div use:tip={HUD.spd}><span>SPD</span>{Math.round(now.v)} m/s</div>
        <div use:tip={HUD.alt}><span>ALT</span>{Math.round(now.p[2])} m</div>
        <div use:tip={HUD.bank}><span>BANK</span>{(now.bank * 57.3).toFixed(0)}°</div>
        <div use:tip={HUD.wp}><span>WP</span>{Math.min(now.wp + 1, flight.route.length)}/{flight.route.length}</div>
        <div class="sep" use:tip={HUD.nav}><span>NAV ERR</span>{m(now.nav)}</div>
        <div use:tip={HUD.sats}><span>SATS</span>{now.sats}</div>
        <div use:tip={HUD.towers}><span>TOWERS</span>{now.towers}</div>
      </div>

      {#if showSensors}
        <div class="skywrap"><SkyPlot sats={now.sky ?? []} /></div>
      {/if}

      {#if net}
        <NetworkView {net} onclose={() => (net = null)} />
      {/if}

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
        <span class="clock">{(f * flight.dt).toFixed(0)} / {((frames.length - 1) * flight.dt).toFixed(0)} s</span>
        <select bind:value={speed} aria-label="Playback speed">
          <option value={1}>1×</option>
          <option value={2}>2×</option>
          <option value={4}>4×</option>
          <option value={8}>8×</option>
        </select>
      </div>
    {:else}
      <p class="loading">{busy ? 'Loading the flight…' : error ? '' : 'No flight loaded.'}</p>
    {/if}
    {#if error}
      <div class="error" role="alert"><b>{error}</b><span>{hint}</span></div>
    {/if}
  </section>

  <aside>
    <section>
      <h2>{title}</h2>
      <p class="lede">
        A PPO agent flying the route with no GPS and no knowledge of the true state. Everything it sees comes from the same sensors as the
        rest of this project — the IMU, the ground towers and Iridium Doppler — fused by the Kalman filter and handed to the network, which
        commands bank, pitch and throttle twice a second.
      </p>
      <ul class="key">
        <li><i class="ln" style:background="#4ea1ff"></i>where it actually went</li>
        <li><i class="ln" style:background="#db6d28"></i>where it thought it was</li>
        <li><i class="ln" style:background="#8b949e"></i>the planned route</li>
      </ul>
    </section>

    {#if flight}
      <section>
        <h2>This flight</h2>
        <div class="kv mono">
          {#each SUMMARY as row}
            <span class="hinted" use:tip={row.why}>{row.k}</span><span>{row.v(flight, frames)}</span>
          {/each}
        </div>
        <p class="note">
          The aeroplane, the gates and the towers are drawn far larger than life — a 15 m aircraft is invisible on a 30 km route.
        </p>
      </section>
    {/if}

    {#if flight && now.obs}
      <section>
        <h2>What the network sees</h2>
        <p class="note">
          The {now.obs.length} numbers going into the policy on this tick — everything it knows. Nothing here is the true state.
        </p>
        {#each flight.obs_groups ?? [{ title: '', note: '', idx: flight.obs_labels.map((_, i) => i) }] as g}
          <h3>{g.title}<em>{g.note}</em></h3>
          <div class="bars">
            {#each g.idx as i}
              <div class="bar" use:tip={flight.obs_sources?.[i] ?? flight.obs_labels[i]}>
                <span class="lab hinted">{flight.obs_labels[i]}</span>
                <span class="track"><i style:left={`${50 + 50 * Math.max(-1, Math.min(1, now.obs[i] / 2))}%`}></i></span>
                <span class="num mono">{now.obs[i].toFixed(2)}</span>
              </div>
            {/each}
          </div>
        {/each}
      </section>

      <section>
        <h2>What it commands</h2>
        <div class="bars out">
          {#each flight.act_labels as lab, i}
            <div class="bar" use:tip={ACT_HINT[i]}>
              <span class="lab hinted">{lab}</span>
              <span class="track"><i style:left={`${50 + 50 * Math.max(-1, Math.min(1, now.act[i]))}%`}></i></span>
              <span class="num mono">
                {i === 0 ? `${(now.act[0] * 45).toFixed(0)}°` : i === 1 ? `${(now.act[1] * 8).toFixed(1)}°` : `${(50 * (now.act[2] + 1)).toFixed(0)}%`}
              </span>
            </div>
          {/each}
        </div>
      </section>
    {/if}

    <section>
      <h2>Make your own</h2>
      <p class="note">In <code>flightsim/</code>:</p>
      <pre class="mono">.venv/bin/python train.py
.venv/bin/python fly.py</pre>
      <p class="note">
        <code>train.py --obs-mode truth</code> is the ceiling (perfect knowledge), <code>--obs-mode raw</code> takes the filter away
        entirely, and <code>--towers 0</code> leaves it on satellites alone. Each writes a checkpoint you can fly and replay here.
      </p>
      <button class="primary wide" onclick={openNetwork} disabled={netBusy}>
        {netBusy ? 'Loading…' : 'Watch the network learn'}
      </button>
      {#if netError}<p class="note bad">{netError}</p>{/if}
      <button class="wide" onclick={() => load(name)} disabled={busy}>{busy ? 'Loading…' : 'Reload flight'}</button>
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
    background: #0a1420;
  }
  .loading {
    position: absolute;
    inset: 0;
    display: grid;
    place-items: center;
    color: var(--muted);
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
  .chip input {
    width: auto;
    accent-color: var(--accent);
    margin: 0;
  }
  .hud {
    position: absolute;
    top: 12px;
    right: 12px;
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 8px 12px;
    font-size: 12px;
    background: rgba(10, 17, 28, 0.72);
    border: 1px solid var(--border);
    border-radius: 10px;
    min-width: 150px;
  }
  .hud div {
    display: flex;
    justify-content: space-between;
    gap: 14px;
  }
  .hud span {
    color: var(--muted);
  }
  .hud .sep {
    margin-top: 6px;
    padding-top: 6px;
    border-top: 1px solid var(--border);
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
    flex: none;
  }
  .scrub {
    flex: 1;
    accent-color: var(--accent);
    padding: 0;
  }
  .clock {
    font-size: 12px;
    color: var(--muted);
    min-width: 90px;
    text-align: right;
    font-family: ui-monospace, monospace;
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
  .lede,
  .note {
    color: var(--muted);
    font-size: 12px;
    line-height: 1.55;
    margin: 0;
  }
  .note {
    margin-top: 8px;
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
  }
  .note.bad {
    color: var(--bad);
  }
  .skywrap {
    position: absolute;
    right: 12px;
    bottom: 58px;
  }
  aside h3 {
    font-size: 11px;
    font-weight: 600;
    color: var(--text);
    margin: 12px 0 2px;
  }
  aside h3 em {
    display: block;
    font-style: normal;
    font-weight: 400;
    font-size: 10px;
    color: var(--muted);
  }
  .bars {
    display: flex;
    flex-direction: column;
    gap: 2px;
    margin-top: 6px;
  }
  .bar {
    display: grid;
    grid-template-columns: 96px 1fr 50px;
    align-items: center;
    gap: 7px;
    font-size: 11px;
  }
  .bar .lab {
    color: var(--muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .bar .track {
    position: relative;
    height: 7px;
    background: #10161f;
    border-radius: 3px;
    overflow: hidden;
  }
  .bar .track::before {
    content: '';
    position: absolute;
    left: 50%;
    top: 0;
    bottom: 0;
    width: 1px;
    background: #2a313c;
  }
  .bar .track i {
    position: absolute;
    top: 0;
    width: 4px;
    height: 7px;
    margin-left: -2px;
    border-radius: 2px;
    background: var(--accent);
    transition: left 0.06s linear;
  }
  .out .track i {
    background: #f0883e;
    width: 5px;
  }
  .bar .num {
    text-align: right;
    color: var(--text);
  }
  .kv {
    display: grid;
    grid-template-columns: 96px 1fr;
    gap: 3px 10px;
    font-size: 12px;
  }
  .kv span:nth-child(odd) {
    color: var(--muted);
  }
  pre {
    background: var(--panel-2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 8px 10px;
    font-size: 11px;
    margin: 6px 0;
    overflow-x: auto;
  }
  code {
    background: var(--panel-2);
    padding: 1px 4px;
    border-radius: 3px;
  }
  button.wide {
    width: 100%;
    margin-top: 10px;
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
