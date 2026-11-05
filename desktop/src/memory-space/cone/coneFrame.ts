// The light cone's coordinate frame and camera maths (DIRECTION 15.3). Pure: nothing here touches WebGL,
// so the tests can project stars exactly the way the renderer does.
//
// The disc is today and sits at y = 0; a record dated theta lies on the cone at height y(theta) < 0 and at
// distance r(theta) from the axis, so looked at from straight above the cone is the SVG plate (same
// (theta, phi) pairs, same scale).
import { Euler, PerspectiveCamera, Quaternion, Vector3 } from 'three';

import type { ChartModel } from '../chartCoordinates';

export type Vec3 = [number, number, number];
export type RGB = [number, number, number];

export const rad = (deg: number) => (deg * Math.PI) / 180;
export const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
export const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
export const easeInOut = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);
export const easeOut = (t: number) => 1 - (1 - t) ** 3;

export type ConeFrame = {
  fit: number;
  thToday: number;
  /** World radius of the disc (today). */
  rToday: number;
  /** Depth of the apex below the disc. */
  hc: number;
  /** Outer edge of the bezel band round the disc. */
  rim: number;
  r: (theta: number) => number;
  y: (theta: number) => number;
  pos: (theta: number, phi: number) => Vec3;
  apex: Vec3;
};

export function coneFrame(model: Pick<ChartModel, 'thetaFit' | 'thetaToday'>): ConeFrame {
  const fit = model.thetaFit;
  const thToday = model.thetaToday;
  const rToday = (thToday / fit) * 0.96;
  const hc = rToday * 1.38;
  const r = (theta: number) => (theta / fit) * 0.96;
  const y = (theta: number) => -hc * (1 - theta / thToday);
  return {
    fit, thToday, rToday, hc, rim: rToday + 0.12, r, y,
    pos: (theta, phi) => [Math.sin(rad(phi)) * r(theta), y(theta), Math.cos(rad(phi)) * r(theta)],
    apex: [0, -hc, 0],
  };
}

/** Where the camera looks from. `flat` squashes the cone onto the disc: 1 is the plate. */
export type Pose = { yaw: number; el: number; dist: number; fov: number; tx: number; ty: number; tz: number; flat: number };
export const POSE_KEYS = ['yaw', 'el', 'dist', 'fov', 'tx', 'ty', 'tz', 'flat'] as const;

/** Straight down, nearly orthographic: what the plate draws. */
export const PLATE_POSE: Pose = { yaw: 0, el: rad(89.6), dist: 1, fov: 6, tx: 0, ty: 0, tz: 0, flat: 1 };
export const EL_MIN = rad(-22);
export const EL_MAX = rad(86);
export const DIST_MIN = 0.7;
export const DIST_MAX = 2.2;

/** The overview camera. `yaw` keeps the current bearing when the caller wants the cone not to swing round. */
export function homePose(frame: ConeFrame, yaw = rad(24)): Pose {
  return {
    yaw, el: rad(17), dist: 1.04 * clamp(Math.sqrt(0.96 / frame.rToday), 1, 1.4),
    fov: 34, tx: 0, ty: -frame.hc * 0.47, tz: 0, flat: 0,
  };
}

/** The plate circle the camera is fitted to, in canvas px. */
export type Viewport = { w: number; h: number; cx: number; cy: number; R: number };

/** One pixel at distance 1 is `R * dist` px per world unit; the principal point is moved so the target lands on (cx, cy). */
export function placeCamera(camera: PerspectiveCamera, pose: Pose, vp: Viewport): number {
  camera.fov = pose.fov;
  camera.aspect = vp.w / vp.h;
  const away = vp.h / 2 / (vp.R * pose.dist * Math.tan(rad(pose.fov) / 2));
  camera.near = Math.max(0.02, away * 0.02);
  camera.far = away + 40;
  const q = new Quaternion().setFromEuler(new Euler(-pose.el, pose.yaw, 0, 'YXZ'));
  const back = new Vector3(0, 0, away).applyQuaternion(q);
  camera.position.set(pose.tx + back.x, pose.ty + back.y, pose.tz + back.z);
  camera.quaternion.copy(q);
  camera.updateMatrixWorld();
  camera.updateProjectionMatrix();
  const m = camera.projectionMatrix.elements;
  m[8] = -(2 * vp.cx / vp.w - 1);
  m[9] = -(1 - 2 * vp.cy / vp.h);
  camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();
  return away;
}

export type Screen = { x: number; y: number; z: number };

export function projector(camera: PerspectiveCamera, vp: Viewport, flat: number): (p: Vec3) => Screen {
  const v = new Vector3();
  return (p) => {
    v.set(p[0], p[1] * (1 - flat), p[2]).project(camera);
    return { x: (v.x + 1) * 0.5 * vp.w, y: (1 - v.y) * 0.5 * vp.h, z: v.z };
  };
}

/** Screen position of a world point seen from `pose`, without a renderer. */
export function projectAt(pose: Pose, vp: Viewport, p: Vec3, camera = new PerspectiveCamera()): Screen {
  placeCamera(camera, pose, vp);
  return projector(camera, vp, pose.flat)(p);
}

/** The shortest way round to `target` from `from`, in radians. */
export function nearestAngle(from: number, target: number): number {
  const d = ((target - from) % (2 * Math.PI) + 3 * Math.PI) % (2 * Math.PI) - Math.PI;
  return from + d;
}

/** Springs: critically damped (z = 1) for SG, a heavier 0.74 with a little overshoot for β. */
export class Spring {
  v: number;
  g: number;
  vel = 0;
  constructor(v: number) { this.v = v; this.g = v; }
  step(dt: number, f: number, z: number) {
    const s = 2 * Math.PI * f;
    this.vel += (-s * s * (this.v - this.g) - 2 * z * s * this.vel) * dt;
    this.v += this.vel * dt;
  }
  snap(v: number) { this.v = v; this.g = v; this.vel = 0; }
  get moving() { return Math.abs(this.v - this.g) > 1e-4 || Math.abs(this.vel) > 1e-4; }
}
