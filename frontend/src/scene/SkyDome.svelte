<script>
  // Gradient sky dome with a soft sun glow. Cheap, predictable, no assets.
  import { T } from '@threlte/core';
  import { BackSide, Color, ShaderMaterial, Vector3 } from 'three';

  let { sunDir = [0.5, 0.6, -0.6], zenith = '#2f6fc4', horizon = '#cfe2f3', ground = '#9fb2a0', radius = 1500 } = $props();

  const material = new ShaderMaterial({
    side: BackSide,
    depthWrite: false,
    fog: false,
    uniforms: {
      zenith: { value: new Color(zenith) },
      horizon: { value: new Color(horizon) },
      ground: { value: new Color(ground) },
      sunDir: { value: new Vector3(...sunDir).normalize() },
    },
    vertexShader: /* glsl */ `
      varying vec3 vDir;
      void main() {
        vDir = normalize((modelMatrix * vec4(position, 1.0)).xyz - cameraPosition);
        gl_Position = projectionMatrix * viewMatrix * modelMatrix * vec4(position, 1.0);
        gl_Position.z = gl_Position.w; // always behind everything
      }
    `,
    fragmentShader: /* glsl */ `
      uniform vec3 zenith, horizon, ground, sunDir;
      varying vec3 vDir;
      void main() {
        vec3 d = normalize(vDir);
        float h = d.y;
        vec3 col = h > 0.0 ? mix(horizon, zenith, pow(h, 0.45)) : mix(horizon, ground, pow(min(-h * 4.0, 1.0), 0.6));
        float s = max(dot(d, normalize(sunDir)), 0.0);
        col += vec3(1.0, 0.92, 0.75) * (pow(s, 900.0) * 3.0 + pow(s, 12.0) * 0.25);
        gl_FragColor = vec4(col, 1.0);
        #include <colorspace_fragment>
      }
    `,
  });

  $effect(() => {
    material.uniforms.zenith.value.set(zenith);
    material.uniforms.horizon.value.set(horizon);
    material.uniforms.ground.value.set(ground);
    material.uniforms.sunDir.value.set(...sunDir).normalize();
  });
  $effect(() => () => material.dispose());
</script>

<T.Mesh {material} renderOrder={-1} frustumCulled={false} raycast={() => null}>
  <T.SphereGeometry args={[radius, 32, 16]} />
</T.Mesh>
