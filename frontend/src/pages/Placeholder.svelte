<script>
  // Empty page for a tracking method that hasn't been built yet.
  import Icon from '../lib/Icon.svelte';
  let { title, icon } = $props();
</script>

<div class="wrap">
  <div class="card">
    <div class="icon"><Icon name={icon} size={44} /></div>
    <h1>{title}</h1>
    <p>Coming soon. This page is a placeholder for another tracking method.</p>
    <p class="hint">Add it in <code>frontend/src/pages/</code> and register it in <code>App.svelte</code>.</p>
  </div>
</div>

<style>
  .wrap {
    height: 100%;
    display: grid;
    place-items: center;
    padding: 16px;
  }
  .card {
    text-align: center;
    max-width: 420px;
    color: var(--muted);
  }
  .icon {
    color: var(--accent);
    opacity: 0.8;
  }
  h1 {
    color: var(--text);
    font-size: 22px;
    margin: 8px 0;
  }
  .hint {
    font-size: 12px;
  }
  code {
    background: var(--panel-2);
    padding: 1px 5px;
    border-radius: 4px;
  }
</style>
