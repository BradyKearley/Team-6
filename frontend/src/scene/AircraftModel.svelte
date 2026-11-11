<script>
  // The light aircraft drawn by Plane.svelte (trilateration) and ImuScene.svelte (live IMU).
  // Nose along +X, up +Y, wings along Z, about 1.2 m wingspan. `prop` = propeller angle [rad].
  import { T } from '@threlte/core';
  import { aircraftParts } from './models.js';

  let { prop = 0 } = $props();
  const parts = aircraftParts();
</script>

  <!-- fuselage -->
  <T.Mesh geometry={parts.fuselage} castShadow raycast={() => null}>
    <T.MeshPhysicalMaterial color="#f4f6f8" metalness={0.1} roughness={0.35} clearcoat={0.8} clearcoatRoughness={0.15} />
  </T.Mesh>
  <!-- cheat line stripe -->
  <T.Mesh position={[0.02, 0.0, 0]} scale={[1.002, 0.18, 1.01]} rotation.z={-Math.PI / 2} raycast={() => null}>
    <T.CylinderGeometry args={[0.087, 0.07, 0.7, 24, 1, true]} />
    <T.MeshStandardMaterial color="#1f5fbf" roughness={0.4} />
  </T.Mesh>
  <!-- canopy -->
  <T.Mesh geometry={parts.canopy} position={[0.1, 0.07, 0]} castShadow raycast={() => null}>
    <T.MeshPhysicalMaterial color="#1a2633" metalness={0.2} roughness={0.05} clearcoat={1} clearcoatRoughness={0.02} />
  </T.Mesh>
  <!-- low wing with slight dihedral -->
  <T.Mesh geometry={parts.wing} position={[0.04, -0.045, 0]} castShadow raycast={() => null}>
    <T.MeshPhysicalMaterial color="#f4f6f8" metalness={0.1} roughness={0.35} clearcoat={0.6} />
  </T.Mesh>
  <!-- wingtips -->
  {#each [-1, 1] as side}
    <T.Mesh position={[0.01, -0.038, side * 0.625]} scale={[0.1, 0.012, 0.012]} raycast={() => null}>
      <T.SphereGeometry args={[1, 12, 8]} />
      <T.MeshStandardMaterial
        color={side > 0 ? '#1fbf4a' : '#e0262b'}
        emissive={side > 0 ? '#1fbf4a' : '#e0262b'}
        emissiveIntensity={1.5}
      />
    </T.Mesh>
  {/each}
  <!-- tail -->
  <T.Mesh geometry={parts.hstab} position={[-0.53, 0.01, 0]} castShadow raycast={() => null}>
    <T.MeshPhysicalMaterial color="#f4f6f8" roughness={0.35} clearcoat={0.6} />
  </T.Mesh>
  <T.Mesh geometry={parts.fin} position={[-0.5, 0.02, 0]} castShadow raycast={() => null}>
    <T.MeshStandardMaterial color="#1f5fbf" roughness={0.4} />
  </T.Mesh>
  <!-- spinner + propeller -->
  <T.Mesh geometry={parts.spinner} position={[0.42, 0, 0]} castShadow raycast={() => null}>
    <T.MeshStandardMaterial color="#c9ced6" metalness={0.9} roughness={0.25} />
  </T.Mesh>
  <T.Group position={[0.45, 0, 0]} rotation.x={prop}>
    <T.Mesh geometry={parts.blade} position={[0, 0.17, 0]} raycast={() => null}>
      <T.MeshStandardMaterial color="#222629" roughness={0.6} />
    </T.Mesh>
    <T.Mesh geometry={parts.blade} position={[0, -0.17, 0]} rotation.x={Math.PI} raycast={() => null}>
      <T.MeshStandardMaterial color="#222629" roughness={0.6} />
    </T.Mesh>
    <!-- motion-blur disc -->
    <T.Mesh rotation.z={Math.PI / 2} raycast={() => null}>
      <T.CircleGeometry args={[0.34, 32]} />
      <T.MeshBasicMaterial color="#9aa3ad" transparent opacity={0.08} depthWrite={false} side={2} />
    </T.Mesh>
  </T.Group>
