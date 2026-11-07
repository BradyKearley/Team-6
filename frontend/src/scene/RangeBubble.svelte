<script>
  // A see-through sphere that glows at its edges (like a soap bubble), so you can
  // see where it is without it hiding everything behind it.
  import { T } from '@threlte/core';
  import { Color, ShaderMaterial } from 'three';

  let { position, radius, color = '#f0883e', strength = 1 } = $props();

  const material = new ShaderMaterial({
    transparent: true,
    depthWrite: false,
    uniforms: { color: { value: new Color(color) }, strength: { value: strength } },
    vertexShader: /* glsl */ `
      varying vec3 vNormal;
      varying vec3 vView;
      void main() {
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        vNormal = normalize(normalMatrix * normal);
        vView = normalize(-mv.xyz);
        gl_Position = projectionMatrix * mv;
      }
    `,
    fragmentShader: /* glsl */ `
      uniform vec3 color;
      uniform float strength;
      varying vec3 vNormal;
      varying vec3 vView;
      void main() {
        // 0 where the surface faces you, 1 at the silhouette edge
        float edge = 1.0 - abs(dot(normalize(vNormal), normalize(vView)));
        float alpha = (0.015 + pow(edge, 4.0) * 0.32) * strength;
        gl_FragColor = vec4(color, alpha);
      }
    `,
  });

  $effect(() => {
    material.uniforms.color.value.set(color);
    material.uniforms.strength.value = strength;
  });
  $effect(() => () => material.dispose());
</script>

<!-- unit sphere scaled to the radius, so it isn't rebuilt when the distance changes -->
<T.Mesh {position} scale={radius} {material} raycast={() => null}>
  <T.SphereGeometry args={[1, 48, 32]} />
</T.Mesh>
