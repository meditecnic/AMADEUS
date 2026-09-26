// Disposable SOUL study. Kept separate until the opening is visually accepted.
import * as THREE from 'three';
import type { OrreryCamera } from './orreryRenderer';

export class SoulOpeningPrototype {
  open = false;
  reading = false;
  progress = 0;
  private clock = performance.now();
  private yaw = 0;
  private pitch = 0;
  private zoom = 1;
  private shells: THREE.Group[] = [];
  private dimmed = new Map<THREE.Material, number>();
  private shaderDimmers = new Map<THREE.ShaderMaterial, { value: number }>();
  private core: THREE.Mesh | null = null;
  private strata: THREE.Mesh[] = [];
  private openingCamera: OrreryCamera | null = null;

  setOpen(open: boolean) {
    if (open && !this.open && this.progress === 0) {
      this.yaw = 0; this.pitch = 0; this.zoom = 1;
    }
    this.open = open;
  }
  orbit(dx: number, dy: number) { this.yaw += dx * 0.008; this.pitch = THREE.MathUtils.clamp(this.pitch + dy * 0.006, -0.8, 0.8); }
  dolly(factor: number) { this.zoom = THREE.MathUtils.clamp(this.zoom * factor, 0.55, 1.8); }
  restore() { for (const [material, opacity] of this.dimmed) material.opacity = opacity; this.dimmed.clear(); }

  update(body: THREE.Group, camera: THREE.PerspectiveCamera, base: OrreryCamera, background: THREE.Object3D[], reduced: boolean) {
    const now = performance.now();
    const dt = Math.max(0, (now - this.clock) / 1000); this.clock = now;
    this.progress = reduced ? Number(this.open) : THREE.MathUtils.clamp(this.progress + dt * (this.open ? 1 : -1), 0, 1);
    const p = this.progress * this.progress * (3 - 2 * this.progress);
    if (p > 0 && !this.openingCamera) this.openingCamera = {...base, lookAt: {...base.lookAt}};
    if (p === 0) this.openingCamera = null;
    if (!this.shells.length && p === 0) return;
    for (const uniform of this.shaderDimmers.values()) uniform.value = 1 - p * 0.82;
    if (!this.shells.length) {
      this.core = new THREE.Mesh(new THREE.SphereGeometry(0.15, 40, 32), new THREE.MeshPhysicalMaterial({ color: 0x03101a, roughness: 0.44, metalness: 0.1, clearcoat: 0.35, transparent: true, opacity: 0, depthWrite: false }));
      body.add(this.core);
      for (let layer = 0; layer < 4; layer++) {
        const stratum = new THREE.Mesh(new THREE.IcosahedronGeometry(0.215 + layer * 0.018, 2), new THREE.MeshPhysicalMaterial({color: 0x1b3643, roughness: 0.22 + layer * 0.065, metalness: 0.18, transparent: true, opacity: 0, depthWrite: false, side: THREE.DoubleSide, clearcoat: 0.4}));
        stratum.scale.set(1, 0.74 + layer * 0.035, 0.8);
        stratum.rotation.set(0.24 + layer * 0.3, layer * 0.48, layer * 0.21);
        body.add(stratum); this.strata.push(stratum);
      }
      const starts = [0, 2.25, 4.3];
      const lengths = [2.25, 2.05, Math.PI * 2 - 4.3];
      for (let i = 0; i < 3; i++) {
        const shell = new THREE.Group();
        for (const name of ['soul-glass', 'optical-shell', 'phase-glint']) {
          const original = body.getObjectByName(name) as THREE.Mesh;
          const mesh = new THREE.Mesh(new THREE.SphereGeometry(name === 'soul-glass' ? 0.5 : 0.508, 32, 28, starts[i], lengths[i]), original.material);
          mesh.renderOrder = original.renderOrder;
          shell.add(mesh);
        }
        const lining = new THREE.Mesh(new THREE.SphereGeometry(0.499, 32, 28, starts[i], lengths[i]), new THREE.MeshBasicMaterial({color: 0x183545, transparent: true, opacity: 0.18, side: THREE.BackSide, depthWrite: false}));
        lining.name = 'shell-lining'; shell.add(lining);
        for (const phi of [starts[i], starts[i] + lengths[i]]) {
          const points = Array.from({length: 49}, (_, j) => {
            const theta = j / 48 * Math.PI;
            return new THREE.Vector3(-0.505 * Math.cos(phi) * Math.sin(theta), 0.505 * Math.cos(theta), 0.505 * Math.sin(phi) * Math.sin(theta));
          });
          shell.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(points), new THREE.LineBasicMaterial({color: 0x427a90, transparent: true, opacity: 0.2})));
        }
        body.add(shell); this.shells.push(shell);
      }
    }
    (this.core!.material as THREE.MeshPhysicalMaterial).opacity = p * 0.94;
    const directions = [[0.05, 0.48, 0.38], [-0.58, -0.18, -0.1], [0.62, -0.28, 0.04]];
    const closedRotation = body.getObjectByName('soul-glass')!.rotation;
    this.shells.forEach((shell, i) => {
      shell.visible = p > 0;
      shell.position.set(...directions[i].map(v => v * p) as [number, number, number]);
      shell.rotation.set(closedRotation.x + p * (i === 0 ? -0.38 : 0.15), closedRotation.y + p * (i - 1) * 0.3, closedRotation.z + p * (i - 1) * 0.25);
    });
    for (const name of ['soul-glass', 'optical-shell', 'phase-glint', 'chromatic-aura']) body.getObjectByName(name)!.visible = p === 0;
    const flow = body.getObjectByName('inner-flow')!;
    const flowColor = ((flow as THREE.Mesh).material as THREE.ShaderMaterial).uniforms.uColor.value as THREE.Color;
    (this.core!.material as THREE.MeshPhysicalMaterial).color.copy(flowColor).multiplyScalar(0.035);
    this.strata.forEach((stratum, index) => {
      const material = stratum.material as THREE.MeshPhysicalMaterial;
      material.color.copy(flowColor).multiplyScalar(0.2 + index * 0.07);
      material.opacity = p * (this.reading ? 0.23 : 0.18);
    });
    for (const shell of this.shells) for (const child of shell.children) if (child instanceof THREE.Line) (child.material as THREE.LineBasicMaterial).color.copy(flowColor).multiplyScalar(0.6);
    for (const shell of this.shells) ((shell.getObjectByName('shell-lining') as THREE.Mesh).material as THREE.MeshBasicMaterial).color.copy(flowColor).multiplyScalar(0.25);
    flow.scale.setScalar(1 - 0.22 * p);
    // Readable form stays still while its public meaning is being read.
    if (this.reading) flow.rotation.set(0.12, 0.3, -0.1);
    const volume = body.getObjectByName('volume') as THREE.Mesh;
    volume.scale.setScalar(1 - 0.28 * p);
    if (p === 0) return;
    for (const group of background) group.traverse(obj => {
      const mat = (obj as THREE.Mesh).material;
      if (!mat) return;
      for (const material of Array.isArray(mat) ? mat : [mat]) {
        if (this.dimmed.has(material)) continue;
        if (material instanceof THREE.ShaderMaterial && !this.shaderDimmers.has(material)) {
          const uniform = { value: 1 - p * 0.82 };
          this.shaderDimmers.set(material, uniform);
          material.uniforms.uSoulPrototypeDim = uniform;
          material.fragmentShader = 'uniform float uSoulPrototypeDim;\n' + material.fragmentShader.replace(/}\s*$/, 'gl_FragColor.rgb *= uSoulPrototypeDim; gl_FragColor.a *= uSoulPrototypeDim;\n}');
          material.needsUpdate = true;
        }
        this.dimmed.set(material, material.opacity);
        material.opacity *= 1 - p * (obj instanceof THREE.Sprite ? 1 : 0.84);
      }
    });
    const origin = this.openingCamera ?? base;
    const yaw = origin.yaw + p * (0.18 + this.yaw);
    const pitch = origin.pitch + p * (-0.12 + this.pitch);
    const distance = origin.distance * (1 + p * (0.72 * this.zoom - 1)) * (1 + (camera.aspect < 1.2 ? 0.35 * p : 0));
    camera.position.set(origin.lookAt.x - distance * Math.cos(pitch) * Math.sin(yaw), origin.lookAt.y - distance * Math.sin(pitch), origin.lookAt.z - distance * Math.cos(pitch) * Math.cos(yaw));
    camera.lookAt(origin.lookAt.x, origin.lookAt.y, origin.lookAt.z);
    // Keep the subject beside its reading surface, including on narrow screens.
    const horizontal = camera.aspect >= 1.2 ? -0.15 : 0;
    const vertical = camera.aspect < 1.2 ? 0.13 : 0;
    camera.translateX(-distance * horizontal * p);
    camera.translateY(-distance * vertical * p);
  }
}
