import * as THREE from 'three';
import type { Point3 } from './types';

export type Filament = THREE.Mesh<THREE.BufferGeometry, THREE.ShaderMaterial>;

/** Soft ribbons share one draw call per body/path and keep their buffers while moving. */
export function updateFilamentPaths(mesh: Filament, paths: readonly (readonly Point3[])[]): void {
  const count = paths.reduce((sum, path) => sum + path.length * 2, 0);
  let geometry = mesh.geometry;
  const topology = paths.map(path => path.length).join(',');
  if (geometry.userData.topology !== topology) {
    if (geometry.getAttribute('position')) {
      geometry.dispose();
      geometry = mesh.geometry = new THREE.BufferGeometry();
    }
    geometry.userData.topology = topology;
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(new Float32Array(count * 3), 3));
    geometry.setAttribute('aTangent', new THREE.Float32BufferAttribute(new Float32Array(count * 3), 3));
    geometry.setAttribute('aRibbon', new THREE.Float32BufferAttribute(new Float32Array(count * 3), 3));
    const indices: number[] = [];
    let offset = 0;
    for (const path of paths) {
      for (let i = 0; i < path.length - 1; i++) {
        const a = offset + i * 2;
        indices.push(a, a + 1, a + 2, a + 1, a + 3, a + 2);
      }
      offset += path.length * 2;
    }
    geometry.setIndex(indices);
  }
  const positions = geometry.getAttribute('position');
  const tangents = geometry.getAttribute('aTangent');
  const ribbons = geometry.getAttribute('aRibbon');
  let offset = 0;
  paths.forEach((path, strand) => {
    path.forEach((point, index) => {
      const prev = path[Math.max(0, index - 1)];
      const next = path[Math.min(path.length - 1, index + 1)];
      for (let side = 0; side < 2; side++) {
        positions.setXYZ(offset, point.x, point.y, point.z);
        tangents.setXYZ(offset, next.x - prev.x, next.y - prev.y, next.z - prev.z);
        ribbons.setXYZ(offset, side * 2 - 1, index / Math.max(1, path.length - 1), strand * 0.381966);
        offset++;
      }
    });
  });
  positions.needsUpdate = true;
  tangents.needsUpdate = true;
  ribbons.needsUpdate = true;
}

export function createFilament(
  paths: readonly (readonly Point3[])[],
  options: { color: number; accent: number; width: number; opacity: number; screenSpace?: boolean },
): Filament {
  const material = new THREE.ShaderMaterial({
    uniforms: {
      uTime: { value: 0 }, uLife: { value: 1 },
      uSpeed: { value: 0.18 }, uJitter: { value: 0 },
      uDeform: { value: 0 },
      uHoverStrength: { value: 0 },
      uEmphasis: { value: 1 }, uOpacity: { value: options.opacity },
      uColor: { value: new THREE.Color(options.color) },
      uPhaseColor: { value: new THREE.Color(options.accent) },
      uWidth: { value: options.width },
      uScreenSpace: { value: options.screenSpace ? 1 : 0 },
      uResolution: { value: new THREE.Vector2(1280, 800) },
    },
    vertexShader: /* glsl */ `
      attribute vec3 aTangent;
      attribute vec3 aRibbon;
      uniform float uWidth;
      uniform float uScreenSpace;
      uniform vec2 uResolution;
      uniform float uTime;
      uniform float uLife;
      uniform float uJitter;
      uniform float uDeform;
      uniform float uHoverStrength;
      varying vec3 vRibbon;
      vec3 turn(vec3 p, vec3 axis, float angle) {
        return p * cos(angle) + cross(axis, p) * sin(angle)
          + axis * dot(axis, p) * (1.0 - cos(angle));
      }
      void main() {
        vec3 normal = normalize(cross(aTangent, vec3(0.3, 0.8, 0.5)) + vec3(0.00001));
        vec3 p = position + normal * sin(uTime * uLife * 0.85 + aRibbon.y * 8.0 + aRibbon.z * 6.0) * uDeform;
        float strand = floor(aRibbon.z / 0.381966 + 0.5);
        float direction = mod(strand, 2.0) < 0.5 ? 1.0 : -1.0;
        vec3 axis = normalize(vec3(0.25 + mod(strand, 3.0) * 0.18, 1.0, 0.3));
        float angle = sin(uTime * uLife * 0.48) * 0.9 * direction * uHoverStrength;
        p = turn(p, axis, angle);
        vec4 mv = modelViewMatrix * vec4(p, 1.0);
        vec3 tangent = mat3(modelViewMatrix) * turn(aTangent, axis, angle);
        vec2 across = normalize(vec2(-tangent.y, tangent.x) + vec2(0.00001));
        float scale = length(modelMatrix[0].xyz);
        float width = mix(uWidth * scale,
          uWidth * 2.0 * max(0.01, -mv.z) / (projectionMatrix[1][1] * uResolution.y), uScreenSpace);
        float sway = sin(aRibbon.y * 6.283 + aRibbon.z * 7.0 + uTime * uLife * 0.3) * uJitter;
        mv.xy += across * width * (aRibbon.x + sway);
        vRibbon = aRibbon;
        gl_Position = projectionMatrix * mv;
      }
    `,
    fragmentShader: /* glsl */ `
      uniform float uTime;
      uniform float uLife;
      uniform float uSpeed;
      uniform float uHoverStrength;
      uniform float uEmphasis;
      uniform float uOpacity;
      uniform vec3 uColor;
      uniform vec3 uPhaseColor;
      varying vec3 vRibbon;
      void main() {
        float across = abs(vRibbon.x);
        float soft = exp(-across * across * 3.8) * (1.0 - smoothstep(0.7, 1.0, across));
        float ends = pow(max(0.0, sin(vRibbon.y * 3.141593)), 0.45);
        float travel = fract(vRibbon.y - uTime * uLife * uSpeed * 0.45 + vRibbon.z);
        float front = travel - 0.5;
        float current = exp(-max(0.0, front) * 25.0 - max(0.0, -front) * 7.0);
        float tint = 0.5 + 0.5 * sin(vRibbon.y * 5.0 + vRibbon.z * 9.0);
        vec3 color = mix(uColor, uPhaseColor, tint * 0.72);
        float relay = pow(0.5 + 0.5 * sin(uTime * uLife * 1.4 - vRibbon.z * 6.2831853), 3.0);
        float pulse = current * mix(0.8, 0.55 + relay * 1.35, uHoverStrength);
        float alpha = soft * ends * (0.2 + pulse) * uOpacity * mix(0.6, 1.0, uEmphasis);
        gl_FragColor = vec4(color * (1.0 + current * (2.0 + relay * uHoverStrength)), alpha);
      }
    `,
    transparent: true, depthWrite: false, side: THREE.DoubleSide,
    blending: THREE.AdditiveBlending,
  });
  material.opacity = options.opacity;
  const mesh = new THREE.Mesh(new THREE.BufferGeometry(), material);
  mesh.frustumCulled = false;
  mesh.onBeforeRender = () => { material.uniforms.uOpacity.value = material.opacity * (options.screenSpace ? 3.2 : 1); };
  updateFilamentPaths(mesh, paths);
  return mesh;
}

/** A relation ends at the surfaces; its slight bend changes no graph ownership. */
export function relationCurve(from: Point3, to: Point3, fromRadius: number, toRadius: number): Point3[] {
  const a = new THREE.Vector3(from.x, from.y, from.z);
  const b = new THREE.Vector3(to.x, to.y, to.z);
  const direction = b.clone().sub(a);
  const distance = direction.length();
  direction.normalize();
  const trim = Math.min(1, distance * 0.85 / Math.max(0.001, fromRadius + toRadius));
  a.addScaledVector(direction, fromRadius * trim);
  b.addScaledVector(direction, -toRadius * trim);
  const normal = new THREE.Vector3().crossVectors(direction, Math.abs(direction.y) > 0.9 ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(0, 1, 0));
  normal.normalize();
  const bend = Math.min(0.16, distance * 0.05);
  const points: Point3[] = [];
  for (let i = 0; i <= 32; i++) {
    const t = i / 32;
    const point = a.clone().lerp(b, t).addScaledVector(normal, Math.sin(t * Math.PI) * bend);
    points.push({ x: point.x, y: point.y, z: point.z });
  }
  return points;
}
