<script>
  import Sidebar from './lib/Sidebar.svelte';
  import Trilateration from './pages/Trilateration.svelte';
  import Placeholder from './pages/Placeholder.svelte';
  import LeoSat from './pages/LeoSat.svelte';
  import Fusion from './pages/Fusion.svelte';
  import Autopilot from './pages/Autopilot.svelte';
  import StarCamera from './pages/StarCamera.svelte';

  // Add new tracking methods here; each gets a sidebar entry and a page.
  const pages = [
    { id: 'trilateration', label: 'Cell Tower Trilateration', icon: 'tower', component: Trilateration },
    { id: 'imu', label: 'IMU / Dead Reckoning', icon: 'compass', component: Placeholder },
    { id: 'star', label: 'Star Camera', icon: 'star', component: StarCamera },
    { id: 'leo', label: 'LEO Satellite', icon: 'satellite', component: LeoSat },
    { id: 'fusion', label: 'Sensor Fusion', icon: 'fusion', component: Fusion },
    { id: 'autopilot', label: 'RL Autopilot', icon: 'plane', component: Autopilot },
    { id: 'sensors', label: 'Raw Sensor Data', icon: 'activity', component: Placeholder },
  ];

  const fromHash = () => location.hash.replace('#/', '') || pages[0].id;
  let current = $state(fromHash());
  $effect(() => {
    const onHash = () => (current = fromHash());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  });

  let page = $derived(pages.find((p) => p.id === current) ?? pages[0]);
</script>

<div class="layout">
  <Sidebar {pages} {current} />
  <main>
    {#key page.id}
      <page.component title={page.label} icon={page.icon} />
    {/key}
  </main>
</div>

<style>
  .layout {
    display: flex;
    height: 100%;
  }
  main {
    flex: 1;
    min-width: 0;
    height: 100%;
    overflow: hidden;
  }
</style>
