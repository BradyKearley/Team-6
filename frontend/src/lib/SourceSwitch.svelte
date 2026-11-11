<script>
  // Pick where plane data comes from: simulator, plane on USB, or plane over Bluetooth.
  import { live } from './live.svelte.js';
  import { api } from './api.js';
  import PiConnect from './PiConnect.svelte';

  const kinds = [
    { id: 'sim', label: 'Sim', title: 'Simulated plane, no hardware' },
    { id: 'serial', label: 'USB', title: 'Real plane plugged in by USB cable' },
    { id: 'ble', label: 'BLE', title: 'Real plane over Bluetooth (PLANE-T6)' },
    { id: 'pi', label: 'Pi', title: 'Heltec plugged into the Raspberry Pi, relayed over Wi-Fi' },
  ];

  let current = $derived(live.data?.link?.source);
  let busy = $state(false);
  let error = $state('');
  let ports = $state([]);
  let port = $state(''); // '' = auto-detect

  async function refreshPorts() {
    try {
      const r = await api.ports();
      ports = r.ports;
    } catch {
      ports = [];
    }
  }

  async function choose(kind) {
    busy = true;
    error = '';
    try {
      await api.setSource(kind, kind === 'serial' ? port || null : null);
    } catch (e) {
      error = e.message;
    }
    busy = false;
  }

  $effect(() => {
    if (current === 'serial') refreshPorts();
  });
</script>

<div class="wrap">
  <div class="label">Data source</div>
  <div class="seg" role="group" aria-label="Data source">
    {#each kinds as k}
      <button
        class:on={current === k.id}
        title={k.title}
        disabled={busy || !live.connected}
        onclick={() => choose(k.id)}>{k.label}</button
      >
    {/each}
  </div>
  {#if current === 'serial'}
    <div class="port">
      <select bind:value={port} onfocus={refreshPorts} onchange={() => choose('serial')} title="Serial port">
        <option value="">Auto-detect</option>
        {#each ports as p}
          <option value={p.device}>{p.device} · {p.description}</option>
        {/each}
      </select>
    </div>
  {/if}
  {#if current === 'pi'}
    <div class="status">{live.data?.link?.status}</div>
    <div class="pi"><PiConnect /></div>
  {/if}
  {#if error}<div class="err">{error}</div>{/if}
</div>

<style>
  .wrap {
    padding: 10px 12px 0;
    border-top: 1px solid var(--border);
  }
  .label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--muted);
    margin-bottom: 6px;
  }
  /* base .seg styles are in app.css; here the buttons just share the width */
  .seg button {
    flex: 1;
    padding: 4px 0;
    font-size: 12px;
  }
  .port {
    margin-top: 6px;
  }
  select {
    width: 100%;
    font: inherit;
    font-size: 12px;
    color: var(--text);
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 3px 4px;
  }
  .status {
    margin-top: 6px;
    font-size: 11px;
    color: var(--muted);
  }
  .pi {
    margin-top: 8px;
  }
  .err {
    margin-top: 6px;
    font-size: 11px;
    color: var(--bad);
  }
</style>
