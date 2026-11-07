<script>
  // Small stroke icons (24x24 grid, currentColor). Add new ones to `paths`.
  let { name, size = 18 } = $props();

  const paths = {
    tower: [
      'M12 9 8 21', // left leg
      'M12 9l4 12', // right leg
      'M9.5 15h5', // brace
      'M9 18.5h6',
      'M12 6.5a.5.5 0 1 0 0 .01', // antenna tip
      'M8.5 3.5a5 5 0 0 0 0 7', // signal arcs
      'M15.5 3.5a5 5 0 0 1 0 7',
      'M6 1.5a8.5 8.5 0 0 0 0 11',
      'M18 1.5a8.5 8.5 0 0 1 0 11',
    ],
    compass: ['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z', 'm15.5 8.5-2 5-5 2 2-5 5-2Z'],
    star: ['M12 3l2.6 5.6 6.1.7-4.5 4.2 1.2 6L12 16.6 6.6 19.5l1.2-6L3.3 9.3l6.1-.7L12 3Z'],
    satellite: [
      'm13 7 4 4', // body
      'M9.5 10.5 13.5 6.5 17.5 10.5 13.5 14.5Z',
      'm6 6 3.5 3.5', // solar panel left
      'M3 9 6 6 9 9 6 12Z',
      'm18 18-3.5-3.5', // solar panel right
      'M15 21l3-3 3 3-3 3Z',
      'M4.5 15.5a5 5 0 0 0 4 4', // signal
      'M2.5 15.5a7.5 7.5 0 0 0 6 6',
    ],
    fusion: ['M6 3v6a6 6 0 0 0 6 6h0a6 6 0 0 0 6-6V3', 'M12 15v6', 'M6 3h.01', 'M18 3h.01'],
    activity: ['M3 12h4l3-8 4 16 3-8h4'],
    menu: ['M4 6h16', 'M4 12h16', 'M4 18h16'],
  };
</script>

<svg
  width={size}
  height={size}
  viewBox="0 0 24 24"
  fill="none"
  stroke="currentColor"
  stroke-width="1.8"
  stroke-linecap="round"
  stroke-linejoin="round"
  aria-hidden="true"
>
  {#each paths[name] ?? [] as d}<path {d} />{/each}
</svg>
