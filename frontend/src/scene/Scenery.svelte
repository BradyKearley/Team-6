<script>
  // 3D-only surroundings: a concrete pad under the towers, trees around it, hills far away.
  import { T } from '@threlte/core';
  import { concreteTexture, makeHills, makeTrees } from './scenery.js';
  import { toScene } from './coords.js';

  // fit = tower layout bounds in server coords (meters): { cx, cy, w, h }
  let { fit } = $props();

  let center = $derived(toScene(fit.cx, fit.cy, 0));
  let padW = $derived(fit.w + 4); // pad is the layout plus a 2 m border
  let padH = $derived(fit.h + 4);

  const concrete = concreteTexture(1); // repeat is set below once we know the pad size
  $effect(() => concrete.repeat.set(padW / 3, padH / 3));

  // Trees and hills are built once; the tree clearing is sized from the first layout.
  const trees = makeTrees(Math.max(fit.w, fit.h) / 2 + 12);
  const hills = makeHills();
</script>

<!-- concrete test pad with a painted white border -->
<!-- Heights and polygonOffset keep these flat layers from flickering (see the grass in World.svelte). -->
<T.Mesh position={[center[0], 0, center[2]]} rotation.x={-Math.PI / 2} receiveShadow raycast={() => null}>
  <T.PlaneGeometry args={[padW, padH]} />
  <T.MeshStandardMaterial
    map={concrete}
    roughness={0.9}
    metalness={0}
    envMapIntensity={0.25}
    polygonOffset
    polygonOffsetFactor={1}
    polygonOffsetUnits={1}
  />
</T.Mesh>
{#each [[padW, 0.12, 0, padH / 2], [padW, 0.12, 0, -padH / 2], [0.12, padH, padW / 2, 0], [0.12, padH, -padW / 2, 0]] as [w, h, dx, dz]}
  <T.Mesh position={[center[0] + dx, 0.012, center[2] + dz]} rotation.x={-Math.PI / 2} raycast={() => null}>
    <T.PlaneGeometry args={[w, h]} />
    <T.MeshStandardMaterial color="#f2f2ee" roughness={0.7} polygonOffset polygonOffsetFactor={-1} polygonOffsetUnits={-1} />
  </T.Mesh>
{/each}

<!-- trees ring (centered on the layout) -->
<T is={trees} position={[center[0], 0, center[2]]} raycast={() => null} />

<!-- distant hills; the fog fades them into the sky -->
<T.Mesh geometry={hills} raycast={() => null}>
  <T.MeshStandardMaterial color="#4f6b48" roughness={1} flatShading />
</T.Mesh>
