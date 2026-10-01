/**
 * Production D luminous-cognition materials.
 * Consumed by OrreryRuntime. No look-dev fixtures.
 * Topic interior is a continuous aggregation field — not three fake clusters.
 */
import * as THREE from 'three';

import { createFilament } from './filaments';

import type { DisplayNode, Worldline } from './types';

export type CognitionKind = 'soul' | 'topic' | 'fact' | 'experience';

export type CognitionOptics = {
  plateColor: number;
  flowColor: number;
  phaseColor: number;
  plateFill: number;
  volumeFill: number;
  fissure: number;
  plateRegularity: number;
  flowStrands: number;
  flowSpeed: number;
  flowJitter: number;
  minAlpha: number;
  emphasis: number;
  hovered: boolean;
  shellRoughness: number;
  shellIridescence: number;
};

export function topicChildCount(node: DisplayNode): number {
  const raw = node.node;
  if (raw.kind !== 'topic') return 0;
  return (raw.fact_count ?? 0) + (raw.experience_count ?? 0);
}

export function cognitionKind(kind: DisplayNode['kind']): CognitionKind {
  if (kind === 'continuity_hub') return 'soul';
  if (kind === 'topic') return 'topic';
  if (kind === 'experience') return 'experience';
  return 'fact';
}

export function cognitionOptics(
  worldline: Worldline,
  kind: CognitionKind,
  emphasis: number,
  childCount = 0,
  hovered = false,
): CognitionOptics {
  const sg = worldline === 'steins_gate';
  // Context emphasis stays restrained; pointer hover owns the material response.
  const boost = Math.max(0.12, Math.min(0.5, emphasis));

  const finish = (optics: CognitionOptics): CognitionOptics => {
    if (!hovered) return optics;
    // Shift hue without mixing pale pigment into the glass or lifting its shadows.
    const shift = (hex: number, amount: number, lightness = 1) => {
      const color = new THREE.Color(hex);
      const hsl = color.getHSL({ h: 0, s: 0, l: 0 }, THREE.SRGBColorSpace);
      const targetHue = sg ? 0.64 : 0.025;
      const hueDelta = ((targetHue - hsl.h + 1.5) % 1) - 0.5;
      return color.setHSL((hsl.h + hueDelta * amount + 1) % 1,
        Math.min(0.88, hsl.s * 1.04), hsl.l * lightness, THREE.SRGBColorSpace).getHex();
    };
    return { ...optics, plateColor: shift(optics.plateColor, 0.24, 0.92),
      flowColor: shift(optics.flowColor, 0.32), phaseColor: shift(optics.phaseColor, 0.22, 0.98) };
  };

  if (kind === 'soul') {
    return finish({
      plateColor: sg ? 0x386890 : 0x5a202b,
      flowColor: sg ? 0x4ccce8 : 0xd86b45,
      phaseColor: sg ? 0x9c87ff : 0xe77762,
      plateFill: 0.18 * Math.max(0.7, boost),
      volumeFill: 0.28 * Math.max(0.65, boost),
      fissure: sg ? 0.22 : 0.48,
      plateRegularity: sg ? 0.92 : 0.55,
      flowStrands: 7,
      flowSpeed: sg ? 0.34 : 0.42,
      flowJitter: sg ? 0.04 : 0.38,
      minAlpha: 0.07,
      emphasis: boost,
      hovered,
      shellRoughness: sg ? 0.07 : 0.15,
      shellIridescence: sg ? 0.22 : 0.12,
    });
  }

  if (kind === 'topic') {
    const density = 4 + Math.min(3, Math.ceil(Math.max(0, childCount) / 4));
    return finish({
      plateColor: sg ? 0x5c8b9b : 0x6c2830,
      flowColor: sg ? 0x55c9dc : 0xce6148,
      phaseColor: sg ? 0x8f7cff : 0xee936e,
      plateFill: 0.28 * (0.55 + 0.45 * boost),
      volumeFill: 0.26 * (0.55 + 0.45 * boost),
      fissure: sg ? 0.16 : 0.4,
      plateRegularity: sg ? 0.88 : 0.5,
      flowStrands: density,
      flowSpeed: sg ? 0.55 : 0.68,
      flowJitter: sg ? 0.05 : 0.42,
      minAlpha: 0.13,
      emphasis: boost,
      hovered,
      shellRoughness: sg ? 0.12 : 0.17,
      shellIridescence: sg ? 0.4 : 0.18,
    });
  }

  const experience = kind === 'experience';
  return finish({
    plateColor: sg
      ? (experience ? 0x956744 : 0x526b98)
      : experience ? 0x47786f : 0x8d5043,
    flowColor: sg ? (experience ? 0xd79458 : 0x6dcddd) : experience ? 0x66b9aa : 0xe46f47,
    phaseColor: sg ? (experience ? 0xffc46b : 0x9b8cf4) : experience ? 0x78d0bd : 0xff7650,
    plateFill: 0.21 * (0.6 + 0.4 * boost),
    volumeFill: 0.17 * (0.6 + 0.4 * boost),
    fissure: sg ? 0.1 : 0.28,
    plateRegularity: sg ? 0.9 : 0.58,
    flowStrands: experience ? 4 : 3,
    flowSpeed: sg ? (experience ? 0.48 : 0.8) : experience ? 0.6 : 0.92,
    flowJitter: sg ? (experience ? 0.05 : 0.03) : experience ? 0.34 : 0.28,
    minAlpha: 0.085,
    emphasis: boost,
    hovered,
    shellRoughness: sg ? 0.055 : 0.18,
    shellIridescence: sg ? 0.46 : 0.56,
  });
}

function membraneMaterial(optics: CognitionOptics, kind: CognitionKind): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    uniforms: {
      uTime: { value: 0 },
      uLife: { value: 1 },
      uPlateColor: { value: new THREE.Color(optics.plateColor) },
      uPhaseColor: { value: new THREE.Color(optics.phaseColor) },
      uFill: { value: optics.plateFill },
      uFissure: { value: optics.fissure },
      uRegular: { value: optics.plateRegularity },
      uEmphasis: { value: optics.emphasis },
      uMinAlpha: { value: optics.minAlpha },
      uPhase: { value: kind === 'soul' ? 0.4 : kind === 'topic' ? 1.8 : kind === 'experience' ? 4.2 : 3.1 },
    },
    vertexShader: /* glsl */ `
      varying vec3 vObj;
      varying vec3 vN;
      varying vec3 vV;
      void main() {
        vObj = position;
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        vN = normalize(normalMatrix * normal);
        vV = normalize(-mv.xyz);
        gl_Position = projectionMatrix * mv;
      }
    `,
    fragmentShader: /* glsl */ `
      uniform vec3 uPlateColor;
      uniform vec3 uPhaseColor;
      uniform float uTime;
      uniform float uLife;
      uniform float uFill;
      uniform float uFissure;
      uniform float uRegular;
      uniform float uEmphasis;
      uniform float uMinAlpha;
      uniform float uPhase;
      varying vec3 vObj;
      varying vec3 vN;
      varying vec3 vV;
      void main() {
        vec3 p = normalize(vObj);
        vec3 n = normalize(vN);
        float facing = clamp(dot(n, normalize(vV)), 0.0, 1.0);
        vec3 keyDir = normalize(vec3(-0.48, 0.66, 0.56));
        float key = pow(max(dot(n, keyDir), 0.0), 1.45);
        float phase = sin(dot(p, vec3(5.1, 7.3, 4.2)) + sin(p.y * 6.2 + p.x * 2.1) * 0.62 + uPhase + uTime * uLife * 0.11);
        float trace = smoothstep(0.72, 0.97, phase) * (0.35 + 0.65 * facing);
        float c1 = abs(dot(p, normalize(vec3(0.58, 0.22, -0.78))) - 0.16);
        float c2 = abs(dot(p, normalize(vec3(-0.34, 0.82, 0.44))) + 0.08);
        float fissure = 1.0 - smoothstep(0.0, 0.022, c1);
        fissure = max(fissure, (1.0 - uRegular) * (1.0 - smoothstep(0.0, 0.018, c2)));
        fissure *= uFissure;
        float vis = mix(0.72, 1.0, uEmphasis);
        float body = mix(0.31, 0.78, facing);
        float rim = pow(1.0 - facing, 2.65);
        float alpha = (uFill * body + rim * 0.09 + trace * 0.025 - fissure * 0.1) * vis;
        alpha = clamp(alpha, uMinAlpha, 0.58);
        float hero = smoothstep(0.68, 1.0, uEmphasis);
        vec3 phaseDirection = normalize(vec3(-0.44, 0.28, 0.85));
        float phaseLobe = pow(max(dot(p, phaseDirection), 0.0), 2.6);
        float phaseAccent = clamp(trace * phaseLobe * (0.12 + hero * 0.82) + rim * hero * 0.1 + fissure * 0.22, 0.0, 0.68);
        vec3 surfaceColor = mix(uPlateColor, uPhaseColor, phaseAccent);
        vec3 col = surfaceColor * (0.26 + 0.46 * facing + 0.2 * key + rim * 0.12 + trace * 0.11 + fissure * 0.08);
        gl_FragColor = vec4(col, alpha);
      }
    `,
    transparent: true,
    depthWrite: false,
    depthTest: true,
    side: THREE.FrontSide,
  });
}

function opticalShellMaterial(optics: CognitionOptics, kind: CognitionKind, seed: number): THREE.MeshPhysicalMaterial {
  const soul = kind === 'soul';
  const material = new THREE.MeshPhysicalMaterial({
    color: new THREE.Color(optics.plateColor).multiplyScalar(soul ? 0.78 : 0.62),
    emissive: new THREE.Color(optics.flowColor),
    emissiveIntensity: 0.025,
    metalness: 0,
    roughness: optics.shellRoughness,
    // The interior uses transparent layers, absent from Three's transmission
    // buffer. A thin reflective shell preserves them without an opaque veil.
    transmission: 0,
    ior: optics.plateRegularity > 0.7 ? 1.42 : 1.5,
    specularIntensity: 0.82,
    specularColor: new THREE.Color(optics.phaseColor),
    clearcoat: 0.9,
    clearcoatRoughness: Math.max(0.025, optics.shellRoughness * 0.38),
    anisotropy: soul ? 0.22 : kind === 'topic' ? 0.68 : 0.48,
    anisotropyRotation: seed * Math.PI * 2,
    iridescence: optics.shellIridescence,
    iridescenceIOR: optics.plateRegularity > 0.7 ? 1.33 : 1.48,
    iridescenceThicknessRange: optics.plateRegularity > 0.7 ? [110, 360] : [180, 620],
    transparent: true,
    opacity: soul ? 0.15 : 0.12,
    depthWrite: false,
    depthTest: true,
    side: THREE.FrontSide,
    flatShading: true,
  });
  material.userData.cognitionKind = kind;
  return material;
}

function identitySeed(id: string): number {
  let hash = 2166136261;
  for (let index = 0; index < id.length; index += 1) {
    hash ^= id.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0) / 4294967295;
}

function phaseGlintMaterial(optics: CognitionOptics, kind: CognitionKind, seed: number): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    uniforms: {
      uTime: { value: 0 },
      uLife: { value: 1 },
      uBaseColor: { value: new THREE.Color(optics.flowColor) },
      uPhaseColor: { value: new THREE.Color(optics.phaseColor) },
      uEmphasis: { value: optics.emphasis },
      uHovered: { value: optics.hovered ? 1 : 0 },
      uRegular: { value: optics.plateRegularity },
      uPhase: { value: kind === 'soul' ? 0.2 : kind === 'topic' ? 1.7 : kind === 'experience' ? 4.6 : 3.2 },
      uSeed: { value: seed },
      uReflectionGain: { value: kind === 'soul' ? 0.48 : 0.8 },
    },
    vertexShader: /* glsl */ `
      varying vec3 vObj;
      varying vec3 vN;
      varying vec3 vV;
      void main() {
        vObj = position;
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        vN = normalize(normalMatrix * normal);
        vV = normalize(-mv.xyz);
        gl_Position = projectionMatrix * mv;
      }
    `,
    fragmentShader: /* glsl */ `
      uniform float uTime;
      uniform float uLife;
      uniform float uEmphasis;
      uniform float uHovered;
      uniform float uRegular;
      uniform float uPhase;
      uniform float uSeed;
      uniform float uReflectionGain;
      uniform vec3 uBaseColor;
      uniform vec3 uPhaseColor;
      varying vec3 vObj;
      varying vec3 vN;
      varying vec3 vV;
      void main() {
        vec3 p = normalize(vObj);
        vec3 n = normalize(vN);
        vec3 v = normalize(vV);
        float facing = clamp(dot(n, v), 0.0, 1.0);
        float hero = smoothstep(0.76, 1.0, uEmphasis);
        float drift = uTime * uLife * mix(0.34, 0.18, uRegular) + uPhase;
        vec3 lightDir = normalize(vec3(-0.58, 0.72, 0.38));
        vec3 halfDir = normalize(lightDir + v);
        float specular = pow(max(dot(n, halfDir), 0.0), mix(34.0, 82.0, uRegular));
        float rim = pow(1.0 - facing, 3.8);
        float seedAngle = uSeed * 6.2831853;
        float spectralPhase = dot(p, vec3(2.1, 3.7, 1.6)) + rim * 3.2 + drift * 0.18;
        vec3 spectrum = 0.5 + 0.5 * cos(spectralPhase + vec3(0.0, 2.094, 4.188));
        vec3 accent = mix(uPhaseColor, uBaseColor, 0.22);
        accent = mix(accent, spectrum, specular * (0.05 + hero * 0.08));
        // Broken, directional reflections describe a curved surface even at rest.
        // Keep the center transparent so the aggregation field remains legible.
        float shoulder = pow(1.0 - facing, 1.65) * smoothstep(0.015, 0.16, facing);
        float coolArc = pow(max(dot(n, normalize(vec3(-0.78, 0.52, 0.32))), 0.0), 3.0);
        float phaseArc = pow(max(dot(n, normalize(vec3(0.82, -0.32, 0.46))), 0.0), 4.0);
        float arc = shoulder * (coolArc + phaseArc);
        vec3 reflection = mix(uBaseColor, uPhaseColor, phaseArc / max(0.001, coolArc + phaseArc));
        float presence = smoothstep(0.12, 0.35, uEmphasis);
        float energy = specular * (0.7 + hero * 0.8)
          + arc * (2.8 + hero * 1.4)
          + rim * 0.035;
        vec3 color = mix(accent, reflection, clamp(arc * 4.0, 0.0, 1.0));
        energy *= uReflectionGain;
        float alpha = clamp(energy * (0.12 + presence * 0.46 + hero * 0.16), 0.0, 0.72);
        // Slow, broad surface currents fade around the sphere instead of forming rings.
        vec3 tideAxis = normalize(vec3(0.7, 0.45, -0.35));
        float tidePhase = dot(p, tideAxis) * 4.2 + sin(p.y * 2.4 + p.z * 1.7) * 0.65
          - uTime * uLife * 0.32 + seedAngle;
        float tide = smoothstep(0.42, 0.98, sin(tidePhase));
        float tideWindow = pow(max(0.0, dot(p, normalize(vec3(-0.3, 0.75, 0.55)))), 1.4);
        float current = tide * tideWindow * smoothstep(0.02, 0.3, facing);
        vec3 currentColor = mix(uBaseColor, uPhaseColor, 0.5 + 0.5 * sin(tidePhase * 0.45));
        vec3 base = color * (0.9 + energy * 1.8);
        float currentAlpha = current * (0.17 + uHovered * 0.1);
        gl_FragColor = vec4(mix(base, currentColor * 1.25, current * 0.6),
          clamp(alpha + currentAlpha, 0.0, 0.8));
      }
    `,
    transparent: true,
    depthWrite: false,
    depthTest: true,
    blending: THREE.AdditiveBlending,
    side: THREE.FrontSide,
    toneMapped: true,
  });
}

function chromaticAuraMaterial(optics: CognitionOptics): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    uniforms: {
      uTime: { value: 0 },
      uLife: { value: 1 },
      uEmphasis: { value: optics.emphasis },
      uBaseColor: { value: new THREE.Color(optics.flowColor) },
      uPhaseColor: { value: new THREE.Color(optics.phaseColor) },
    },
    vertexShader: /* glsl */ `
      varying vec3 vN;
      varying vec3 vV;
      varying vec3 vObj;
      void main() {
        vObj = position;
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        vN = normalize(normalMatrix * normal);
        vV = normalize(-mv.xyz);
        gl_Position = projectionMatrix * mv;
      }
    `,
    fragmentShader: /* glsl */ `
      uniform float uTime;
      uniform float uLife;
      uniform float uEmphasis;
      uniform vec3 uBaseColor;
      uniform vec3 uPhaseColor;
      varying vec3 vN;
      varying vec3 vV;
      varying vec3 vObj;
      void main() {
        float facing = abs(dot(normalize(vN), normalize(vV)));
        float rim = pow(1.0 - facing, 2.1);
        float phase = 0.5 + 0.5 * sin(normalize(vObj).y * 4.8 + uTime * uLife * 0.22);
        float hero = smoothstep(0.72, 1.0, uEmphasis);
        vec3 color = mix(uBaseColor, uPhaseColor, 0.28 + phase * 0.42);
        float alpha = rim * (0.035 + hero * 0.22) * mix(0.58, 1.0, uEmphasis);
        gl_FragColor = vec4(color * (0.48 + hero * 2.2), alpha);
      }
    `,
    transparent: true,
    depthWrite: false,
    depthTest: true,
    blending: THREE.AdditiveBlending,
    side: THREE.BackSide,
    toneMapped: true,
  });
}

function phaseFlareMaterial(optics: CognitionOptics): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    uniforms: {
      uTime: { value: 0 },
      uLife: { value: 1 },
      uColor: { value: new THREE.Color(optics.phaseColor) },
      uOpacity: { value: 0 },
    },
    vertexShader: /* glsl */ `
      varying vec2 vUv;
      void main() {
        vUv = uv;
        vec4 center = modelViewMatrix * vec4(0.0, 0.0, 0.0, 1.0);
        float scaleX = length(modelMatrix[0].xyz);
        float scaleY = length(modelMatrix[1].xyz);
        center.xy += position.xy * vec2(scaleX, scaleY);
        gl_Position = projectionMatrix * center;
      }
    `,
    fragmentShader: /* glsl */ `
      uniform vec3 uColor;
      uniform float uOpacity;
      varying vec2 vUv;
      void main() {
        vec2 q = vUv * 2.0 - 1.0;
        float radial = exp(-(q.x * q.x * 17.0 + q.y * q.y * 17.0));
        float horizontal = exp(-(q.x * q.x * 2.3 + q.y * q.y * 190.0));
        float vertical = exp(-(q.x * q.x * 260.0 + q.y * q.y * 8.5));
        float alpha = clamp(radial * 0.72 + horizontal * 0.28 + vertical * 0.14, 0.0, 1.0) * uOpacity;
        gl_FragColor = vec4(uColor * 1.85, alpha);
      }
    `,
    transparent: true,
    depthWrite: false,
    depthTest: true,
    blending: THREE.AdditiveBlending,
    toneMapped: true,
  });
}

function volumeMaterial(optics: CognitionOptics): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    uniforms: {
      uTime: { value: 0 },
      uLife: { value: 1 },
      uColor: { value: new THREE.Color(optics.flowColor) },
      uFill: { value: optics.volumeFill },
      uEmphasis: { value: optics.emphasis },
    },
    vertexShader: /* glsl */ `
      varying float vFacing;
      varying vec3 vObj;
      void main() {
        vObj = position;
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        vec3 n = normalize(normalMatrix * normal);
        vFacing = clamp(dot(n, normalize(-mv.xyz)), 0.0, 1.0);
        gl_Position = projectionMatrix * mv;
      }
    `,
    fragmentShader: /* glsl */ `
      uniform vec3 uColor;
      uniform float uTime;
      uniform float uLife;
      uniform float uFill;
      uniform float uEmphasis;
      varying float vFacing;
      varying vec3 vObj;
      void main() {
        vec3 p = normalize(vObj);
        float core = pow(vFacing, 0.82);
        float phase = 0.5 + 0.5 * sin(dot(p, vec3(4.3, 6.1, 5.4)) + uTime * uLife * 0.16);
        float alpha = uFill * (0.1 + 0.34 * core) * (0.72 + 0.28 * phase) * mix(0.7, 1.0, uEmphasis);
        vec3 col = uColor * (0.18 + 0.48 * core + 0.14 * phase);
        gl_FragColor = vec4(col, clamp(alpha, 0.0, 0.44));
      }
    `,
    transparent: true,
    depthWrite: false,
    depthTest: true,
    side: THREE.FrontSide,
  });
}

function buildInnerFlow(radius: number, optics: CognitionOptics, kind: CognitionKind, seed: number) {
  const paths = [];
  for (let strand = 0; strand < optics.flowStrands; strand++) {
    const phase = strand / optics.flowStrands * Math.PI * 2;
    const rotation = new THREE.Euler(seed * 1.2, seed * 4.6, seed * 0.8);
    const path = [];
    for (let index = 0; index <= 64; index++) {
      const u = index / 64;
      let point: THREE.Vector3;
      if (kind === 'soul') {
        // A broad interwoven mantle surrounds a stable center.
        const angle = u * Math.PI * 1.96 + phase * 0.12;
        const reach = 0.47 + Math.cos(angle * 3 + phase) * 0.1;
        point = new THREE.Vector3(Math.cos(angle) * reach, Math.sin(angle * 2 + phase) * 0.22, Math.sin(angle) * reach);
      } else if (kind === 'topic') {
        // Distinct streams gather inward, rather than repeating SOUL's mantle.
        const angle = u * Math.PI * 2.8 + phase;
        const reach = 0.6 * Math.pow(1 - u, 0.8) + 0.06;
        point = new THREE.Vector3(Math.cos(angle) * reach, Math.sin(u * Math.PI) * Math.sin(phase) * 0.32, Math.sin(angle) * reach);
      } else {
        // Records carry a compact axial weave; experiences sweep more broadly.
        const angle = u * Math.PI * (kind === 'experience' ? 1.4 : 4.6) + phase;
        const reach = (kind === 'experience' ? 0.23 : 0.17) * Math.sin(u * Math.PI);
        point = new THREE.Vector3((u - 0.5) * 0.72, Math.cos(angle) * reach, Math.sin(angle) * reach);
      }
      point.multiplyScalar(radius).applyEuler(rotation);
      path.push({ x: point.x, y: point.y, z: point.z });
    }
    paths.push(path);
  }
  const flow = createFilament(paths, {
    color: optics.flowColor, accent: optics.phaseColor,
    width: radius * (kind === 'fact' ? 0.018 : 0.028), opacity: 0.5,
  });
  flow.userData.phaseSeed = seed;
  flow.material.uniforms.uDeform.value = radius * (kind === 'soul' ? 0.025 : kind === 'topic' ? 0.035 : 0.012);
  flow.renderOrder = 2;
  return flow;
}

function mark(object: THREE.Object3D, id: string, kind: string): void {
  object.userData.nodeId = id;
  object.userData.kind = kind;
  object.userData.cognition = true;
}

export function buildCognitionBody(options: {
  id: string;
  kind: CognitionKind;
  radius: number;
  optics: CognitionOptics;
  names?: { membrane?: string; hit?: string };
}): THREE.Group {
  const { id, kind, radius, optics } = options;
  const seed = identitySeed(id);
  const names = options.names ?? {};
  const group = new THREE.Group();
  mark(group, id, kind);

  const flow = buildInnerFlow(radius, optics, kind, seed);
  flow.name = 'inner-flow';
  mark(flow, id, kind);


  const flare = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), phaseFlareMaterial(optics));
  flare.name = 'phase-flare';
  const flareWidth = radius * (kind === 'soul' ? 1.35 : kind === 'topic' ? 1.15 : 1.35);
  const flareHeight = radius * (kind === 'soul' ? 0.4 : 0.32);
  flare.scale.set(flareWidth, flareHeight, 1);
  flare.userData.baseWidth = flareWidth;
  flare.userData.baseHeight = flareHeight;
  flare.userData.baseOpacity = kind === 'soul' ? 0.1 : 0;
  mark(flare, id, kind);

  const volume = new THREE.Mesh(new THREE.SphereGeometry(radius * 0.88, 24, 16), volumeMaterial(optics));
  volume.name = 'volume';
  mark(volume, id, kind);

  const membraneMat = membraneMaterial(optics, kind);
  const membrane = new THREE.Mesh(new THREE.SphereGeometry(radius, 32, 24), membraneMat);
  membrane.name = names.membrane ?? 'membrane';
  mark(membrane, id, kind);
  membrane.rotation.set((seed - 0.5) * 0.45, seed * Math.PI * 1.7, (0.5 - seed) * 0.3);

  const opticalShell = new THREE.Mesh(
    new THREE.IcosahedronGeometry(radius * 1.012, kind === 'soul' ? 4 : kind === 'topic' ? 3 : 2),
    opticalShellMaterial(optics, kind, seed),
  );
  opticalShell.name = 'optical-shell';
  opticalShell.visible = kind === 'soul' || (kind === 'topic' ? optics.emphasis >= 0.5 : optics.emphasis >= 0.84);
  opticalShell.renderOrder = 4;
  mark(opticalShell, id, kind);

  const phaseGlint = new THREE.Mesh(
    new THREE.SphereGeometry(radius * 1.022, 40, 28),
    phaseGlintMaterial(optics, kind, seed),
  );
  phaseGlint.name = 'phase-glint';
  phaseGlint.renderOrder = 5;
  phaseGlint.userData.phaseSeed = seed;
  mark(phaseGlint, id, kind);

  const aura = new THREE.Mesh(
    new THREE.SphereGeometry(radius * 1.075, 32, 22),
    chromaticAuraMaterial(optics),
  );
  aura.name = 'chromatic-aura';
  aura.visible = kind === 'soul' || optics.emphasis >= 0.82;
  aura.renderOrder = 3;
  mark(aura, id, kind);

  const hit = new THREE.Mesh(
    new THREE.SphereGeometry(radius * 1.04, 16, 12),
    new THREE.MeshBasicMaterial({ visible: false }),
  );
  hit.name = names.hit ?? 'hit';
  mark(hit, id, kind);

  group.add(volume, flow, flare, membrane, opticalShell, aura, phaseGlint, hit);
  return group;
}

export function tickCognitionBody(group: THREE.Object3D, time: number, life: number): void {
  group.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    const material = mesh.material;
    if (material instanceof THREE.ShaderMaterial) {
      if (material.uniforms.uTime) material.uniforms.uTime.value = time;
      if (material.uniforms.uLife) material.uniforms.uLife.value = life;
    }
    if (obj.name === 'phase-glint') {
      const seed = Number(obj.userData.phaseSeed ?? 0);
      obj.rotation.y = seed * Math.PI * 1.7 + time * life * 0.025;
      obj.rotation.x = (seed - 0.5) * 0.45 + Math.sin(time * 0.11) * life * 0.018;
    }
    if (obj.name === 'inner-flow') {
      const seed = Number(obj.userData.phaseSeed ?? 0);
      const kind = obj.userData.kind as CognitionKind;
      const t = time * life;
      const previousTime = Number(obj.userData.lastFocusTime ?? time);
      const dt = Math.min(0.1, Math.max(0, time - previousTime));
      obj.userData.lastFocusTime = time;
      const target = obj.userData.hovered ? 1 : 0;
      const previous = Number(obj.userData.focusAmount ?? 0);
      const focus = life ? THREE.MathUtils.lerp(previous, target, 1 - Math.exp(-dt * 7)) : target;
      obj.userData.focusAmount = focus;
      // Hover changes the current and strand orientation, never the body's volume.
      obj.scale.setScalar(1);
      const flowMaterial = material as THREE.ShaderMaterial;
      flowMaterial.uniforms.uHoverStrength.value = focus;
      flowMaterial.opacity = 0.5 + focus * 0.12;
      obj.rotation.y = seed * Math.PI * 0.6 + t * (kind === 'soul' ? 0.065 : kind === 'topic' ? -0.14 : 0.035);
      obj.rotation.x = kind === 'fact' ? t * 0.18 : Math.sin(t * 0.26 + seed * 4) * 0.12;
      obj.rotation.z = (seed - 0.5) * 0.35 + Math.sin(t * 0.22 + seed * 4) * 0.06;
    }
    if (obj.name === 'phase-flare') {
      const flare = obj as THREE.Mesh;
      const material = flare.material as THREE.ShaderMaterial;
      const width = Number(flare.userData.baseWidth ?? 1);
      const height = Number(flare.userData.baseHeight ?? 1);
      const baseOpacity = Number(flare.userData.baseOpacity ?? 0);
      const pulse = life ? 0.92 + Math.sin(time * 1.12) * 0.08 : 1;
      flare.scale.set(width * pulse, height * pulse, 1);
      material.uniforms.uOpacity.value = baseOpacity * (life ? 0.9 + Math.sin(time * 0.86) * 0.1 : 1);
    }
  });
}

export function applyCognitionOptics(
  group: THREE.Object3D,
  optics: CognitionOptics,
  time: number,
  life: number,
): void {
  group.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    const mat = mesh.material as THREE.ShaderMaterial | undefined;
    if ((mesh.name === 'membrane' || mesh.name === 'soul-glass') && mat && 'uniforms' in mat && mat.uniforms) {
      mat.uniforms.uTime.value = time;
      mat.uniforms.uLife.value = life;
      mat.uniforms.uFill.value = optics.plateFill;
      mat.uniforms.uFissure.value = optics.fissure;
      mat.uniforms.uEmphasis.value = optics.emphasis;
      mat.uniforms.uPlateColor.value.setHex(optics.plateColor);
      mat.uniforms.uPhaseColor.value.setHex(optics.phaseColor);
      mat.uniforms.uRegular.value = optics.plateRegularity;
      if (mat.uniforms.uMinAlpha) mat.uniforms.uMinAlpha.value = optics.minAlpha;
    }
    if (mesh.name === 'volume' && mat && 'uniforms' in mat && mat.uniforms) {
      mat.uniforms.uTime.value = time;
      mat.uniforms.uLife.value = life;
      mat.uniforms.uFill.value = optics.volumeFill;
      mat.uniforms.uEmphasis.value = optics.emphasis;
      mat.uniforms.uColor.value.setHex(optics.flowColor);
    }
    if (mesh.name === 'inner-flow' && mat && 'uniforms' in mat && mat.uniforms) {
      mesh.userData.hovered = optics.hovered;
      mat.uniforms.uTime.value = time;
      mat.uniforms.uLife.value = life;
      mat.uniforms.uSpeed.value = optics.flowSpeed * (optics.hovered ? 1.18 : 1);
      mat.uniforms.uJitter.value = optics.flowJitter;
      mat.uniforms.uPhaseColor.value.setHex(optics.phaseColor);
      mat.uniforms.uEmphasis.value = optics.emphasis;
      mat.uniforms.uColor.value.setHex(optics.flowColor);
    }
    if (mesh.name === 'phase-glint' && mat && 'uniforms' in mat && mat.uniforms) {
      mat.uniforms.uHovered.value = optics.hovered ? 1 : 0;
      mat.uniforms.uTime.value = time;
      mat.uniforms.uLife.value = life;
      mat.uniforms.uEmphasis.value = optics.emphasis;
      mat.uniforms.uRegular.value = optics.plateRegularity;
      mat.uniforms.uBaseColor.value.setHex(optics.flowColor);
      mat.uniforms.uPhaseColor.value.setHex(optics.phaseColor);
    }
    if (mesh.name === 'chromatic-aura' && mat && 'uniforms' in mat && mat.uniforms) {
      mat.uniforms.uTime.value = time;
      mat.uniforms.uLife.value = life;
      mat.uniforms.uEmphasis.value = optics.emphasis;
      mat.uniforms.uBaseColor.value.setHex(optics.flowColor);
      mat.uniforms.uPhaseColor.value.setHex(optics.phaseColor);
      const kind = mesh.userData.kind as CognitionKind | undefined;
      mesh.visible = kind === 'soul' || optics.emphasis >= 0.82;
    }
    if (mesh.name === 'phase-flare') {
      const flare = mesh as THREE.Mesh;
      const material = flare.material as THREE.ShaderMaterial;
      const kind = flare.userData.kind as CognitionKind | undefined;
      const hero = THREE.MathUtils.smoothstep(optics.emphasis, 0.78, 1);
      const baseOpacity = kind === 'soul'
        ? 0.08 + hero * 0.08
        : hero * (kind === 'topic' ? 0.16 : 0.24);
      flare.visible = kind === 'soul' || hero > 0.02;
      flare.userData.baseOpacity = baseOpacity;
      material.uniforms.uOpacity.value = baseOpacity;
      material.uniforms.uColor.value.setHex(optics.phaseColor);
    }
    if (mesh.name === 'optical-shell') {
      const physical = mesh.material as THREE.MeshPhysicalMaterial;
      const kind = mesh.userData.kind as CognitionKind | undefined;
      const soul = kind === 'soul';
      const record = kind === 'fact' || kind === 'experience';
      const hero = THREE.MathUtils.smoothstep(optics.emphasis, 0.76, 1);
      mesh.visible = soul || (kind === 'topic' ? optics.emphasis >= 0.5 : optics.emphasis >= 0.84);
      physical.color.setHex(optics.plateColor).multiplyScalar(soul ? 0.78 : 0.62);
      physical.emissive.setHex(optics.flowColor);
      physical.emissiveIntensity = 0.018 + hero * (record ? 0.12 : 0.075);
      physical.specularColor.setHex(optics.phaseColor);
      physical.roughness = Math.max(0.035, optics.shellRoughness - hero * 0.035);
      physical.iridescence = optics.shellIridescence + hero * 0.16;
      physical.opacity = soul
        ? 0.065 + hero * 0.085
        : 0.08 + hero * (record ? 0.28 : 0.22);
    }
  });
  tickCognitionBody(group, time, life);
}
