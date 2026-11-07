<script>
  // Fading line through the plane's recent positions.
  import { T } from '@threlte/core';
  import { BufferAttribute, BufferGeometry } from 'three';
  import { toScene } from './coords.js';

  let { points = [] } = $props();

  // Buffers are made once (the server keeps at most 300 points) and refilled on each update.
  const MAX = 300;
  const positions = new BufferAttribute(new Float32Array(MAX * 3), 3);
  const colors = new BufferAttribute(new Float32Array(MAX * 3), 3);
  const geometry = new BufferGeometry();
  geometry.setAttribute('position', positions);
  geometry.setAttribute('color', colors);

  $effect(() => {
    const n = Math.min(points.length, MAX);
    for (let i = 0; i < n; i++) {
      const [x, y, z] = toScene(...points[i]);
      positions.setXYZ(i, x, y, z);
      const a = n > 1 ? i / (n - 1) : 1; // 0 = oldest (dim), 1 = newest (bright blue)
      colors.setXYZ(i, 0.12 + 0.19 * a, 0.2 + 0.43 * a, 0.3 + 0.7 * a);
    }
    positions.needsUpdate = true;
    colors.needsUpdate = true;
    geometry.setDrawRange(0, n);
    geometry.computeBoundingSphere();
  });
  $effect(() => () => geometry.dispose());
</script>

{#if points.length > 1}
  <T.Line {geometry} raycast={() => null}>
    <T.LineBasicMaterial vertexColors />
  </T.Line>
{/if}
