<script>
  /**
   * "Connect to the Pi" - shared by the IMU page and the Star Camera page.
   *
   * The server SSHes in (server/app/pi.py), copies pi/team6_pi_agent.py over, starts it, and
   * from then on talks to it over plain HTTP on Wi-Fi: the IMU is polled for the 3D plane, and
   * the star camera is pointed at the same Pi. The password goes to our own server only and
   * lives in its memory for this run; it is not saved anywhere.
   */
  import { api } from './api.js';
  import { live } from './live.svelte.js';

  let { onchange = () => {} } = $props();

  const KEY = 'team6.pi';
  let saved = {};
  try {
    saved = JSON.parse(localStorage.getItem(KEY) || '{}');
  } catch {}

  let host = $state(saved.host ?? '172.20.10.5');
  let user = $state(saved.user ?? 'pi');
  let password = $state('');
  let fakeImu = $state(false);
  let showManual = $state(false);
  let manualUrl = $state(saved.url ?? 'http://172.20.10.5:5000');
  let busy = $state('');
  let error = $state('');

  let pi = $derived(live.data?.pi);
  let mode = $derived(pi?.mode ?? 'off');

  function remember() {
    try {
      localStorage.setItem(KEY, JSON.stringify({ host, user, url: manualUrl }));
    } catch {}
  }

  async function act(label, fn) {
    busy = label;
    error = '';
    try {
      const r = await fn();
      onchange(r);
    } catch (e) {
      error = e.message;
    } finally {
      busy = '';
    }
  }

  const connect = () => {
    remember();
    return act('Connecting over SSH…', () =>
      api.piConnect({ host: host.trim(), user: user.trim(), password: password || null, fake_imu: fakeImu }),
    );
  };
  const attach = () => {
    remember();
    return act('Checking the Pi…', () => api.piAttach(manualUrl.trim()));
  };
  const sim = () => act('Starting the simulator…', () => api.piSim());
  const stop = () => act('Stopping…', () => api.piStop());

  const LABEL = { off: 'Not connected', connecting: 'Connecting…', live: 'Connected', sim: 'Simulated IMU', error: 'Error' };
  const COLOR = { off: 'var(--muted)', connecting: 'var(--warn)', live: 'var(--ok)', sim: 'var(--accent)', error: 'var(--bad)' };
</script>

<section>
  <h2>Raspberry Pi</h2>
  <div class="status">
    <i style:background={COLOR[mode]}></i>
    <b>{busy || LABEL[mode]}</b>
    {#if mode === 'live'}<span class="mono">{pi.base_url}</span>{/if}
  </div>

  {#if mode === 'live' || mode === 'sim'}
    <button class="wide" onclick={stop} disabled={!!busy}>Disconnect{mode === 'live' ? ' and stop the Pi agent' : ''}</button>
  {:else}
    <p class="sub">
      Turn the Pi on and join it to the same Wi-Fi as this laptop. The server logs in over SSH, copies the agent across
      and starts it; you don't copy anything by hand.
    </p>
    <label>Pi address<input type="text" bind:value={host} placeholder="172.20.10.5 or raspberrypi.local" /></label>
    <label>Username<input type="text" bind:value={user} autocomplete="username" /></label>
    <label
      >Password<input
        type="password"
        bind:value={password}
        autocomplete="current-password"
        placeholder="blank = SSH key"
        onkeydown={(e) => e.key === 'Enter' && connect()}
      /></label
    >
    <label class="check"><input type="checkbox" bind:checked={fakeImu} /> No sensor yet: fake the IMU on the Pi</label>
    <button class="wide primary" onclick={connect} disabled={!!busy || !host.trim()}>Connect to the Pi</button>
    <div class="row">
      <button class="link" onclick={() => (showManual = !showManual)}>Agent already running?</button>
      <button class="link" onclick={sim} disabled={!!busy}>Try with no Pi (simulated)</button>
    </div>
    {#if showManual}
      <label class="stack"
        ><span>Agent address</span><input type="text" bind:value={manualUrl} placeholder="http://172.20.10.5:5000" /></label
      >
      <button class="wide" onclick={attach} disabled={!!busy}>Use this address</button>
    {/if}
  {/if}

  {#if error}<p class="err">{error}</p>{/if}
  {#if pi?.log?.length}
    <details open={mode === 'error' || mode === 'connecting'}>
      <summary>What happened</summary>
      <ul class="log mono">
        {#each pi.log as line}<li>{line}</li>{/each}
      </ul>
    </details>
  {/if}
</section>

<style>
  h2 {
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--muted);
    margin: 0 0 8px;
  }
  .status {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 8px;
    flex-wrap: wrap;
  }
  .status i {
    width: 9px;
    height: 9px;
    border-radius: 50%;
  }
  .mono {
    font-family: ui-monospace, Consolas, monospace;
    font-size: 11px;
    color: var(--muted);
  }
  .sub {
    color: var(--muted);
    font-size: 11px;
    line-height: 1.5;
    margin: 0 0 8px;
  }
  label {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    font-size: 12px;
    margin-bottom: 6px;
  }
  label input[type='text'],
  label input[type='password'] {
    width: 190px;
  }
  label.stack {
    flex-direction: column;
    align-items: stretch;
  }
  label.stack input {
    width: 100%;
  }
  .check {
    justify-content: flex-start;
    color: var(--muted);
  }
  .check input {
    width: auto;
    margin: 0;
    accent-color: var(--accent);
  }
  button.wide {
    width: 100%;
    margin-top: 4px;
  }
  .row {
    display: flex;
    justify-content: space-between;
    margin-top: 4px;
  }
  button.link {
    background: none;
    border: none;
    padding: 2px 0;
    color: var(--accent);
    font-size: 12px;
  }
  .err {
    color: #ffb3b0;
    background: #3d1418;
    border: 1px solid var(--bad);
    border-radius: 6px;
    padding: 6px 8px;
    font-size: 12px;
    white-space: pre-wrap;
    margin: 8px 0 0;
  }
  details {
    margin-top: 8px;
    font-size: 12px;
  }
  summary {
    cursor: pointer;
    color: var(--muted);
  }
  .log {
    margin: 6px 0 0;
    padding-left: 16px;
    line-height: 1.5;
    white-space: pre-wrap;
  }
</style>
