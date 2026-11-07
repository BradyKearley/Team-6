<script>
  import { live } from './live.svelte.js';
  import SourceSwitch from './SourceSwitch.svelte';
  import Icon from './Icon.svelte';

  let { pages, current } = $props();
  let collapsed = $state(false);

  let status = $derived.by(() => {
    if (!live.connected) return { cls: 'bad', text: 'Server offline' };
    const l = live.data?.link;
    if (!l) return { cls: 'warn', text: 'Waiting…' };
    const fresh = l.last_report_age != null && l.last_report_age < 2;
    return { cls: fresh ? 'ok' : 'warn', text: `${l.source}: ${l.status}` };
  });
</script>

<nav class:collapsed>
  <div class="brand">
    <button class="toggle" onclick={() => (collapsed = !collapsed)} title="Toggle sidebar" aria-label="Toggle sidebar"
      ><Icon name="menu" size={16} /></button
    >
    {#if !collapsed}<span>Team-6 Tracker</span>{/if}
  </div>
  <ul>
    {#each pages as p}
      <li>
        <a href={`#/${p.id}`} class:active={p.id === current} title={p.label}>
          <span class="icon"><Icon name={p.icon} /></span>
          {#if !collapsed}<span>{p.label}</span>{/if}
        </a>
      </li>
    {/each}
  </ul>
  {#if !collapsed}<SourceSwitch />{/if}
  <div class="status" title={status.text}>
    <span class="dot {status.cls}"></span>
    {#if !collapsed}<span class="text">{status.text}</span>{/if}
  </div>
</nav>

<style>
  nav {
    width: 230px;
    flex-shrink: 0;
    background: var(--panel);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    transition: width 0.15s;
  }
  nav.collapsed {
    width: 56px;
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 12px;
    font-weight: 600;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }
  .toggle {
    padding: 2px 8px;
  }
  ul {
    list-style: none;
    margin: 0;
    padding: 8px;
    flex: 1;
  }
  a {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 10px;
    border-radius: 6px;
    color: var(--text);
    text-decoration: none;
    white-space: nowrap;
    overflow: hidden;
  }
  a:hover {
    background: var(--panel-2);
  }
  a.active {
    background: var(--panel-2);
    box-shadow: inset 3px 0 0 var(--accent);
  }
  .icon {
    width: 20px;
    display: grid;
    place-items: center;
    color: var(--muted);
    flex-shrink: 0;
  }
  a.active .icon,
  a:hover .icon {
    color: var(--accent);
  }
  .toggle {
    display: grid;
    place-items: center;
  }
  .status {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 12px;
    border-top: 1px solid var(--border);
    color: var(--muted);
    font-size: 12px;
  }
  .text {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .dot {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .ok {
    background: var(--ok);
  }
  .warn {
    background: var(--warn);
  }
  .bad {
    background: var(--bad);
  }
</style>
