import { describe, expect, it, vi } from 'vitest';
import { createFilament, relationCurve, updateFilamentPaths } from './filaments';

describe('memory filaments', () => {
  it('reuses moving relation buffers and releases them when topology changes', () => {
    const path = relationCurve({ x: 0, y: 0, z: 0 }, { x: 3, y: 0, z: 0 }, 0.5, 0.2);
    const mesh = createFilament([path], { color: 0xffffff, accent: 0x8888ff, width: 1, opacity: 0.3 });
    const geometry = mesh.geometry;
    const positions = geometry.getAttribute('position');
    const dispose = vi.spyOn(geometry, 'dispose');
    const moved = path.map(p => ({ ...p, y: p.y + 1 }));
    updateFilamentPaths(mesh, [moved]);
    expect(mesh.geometry).toBe(geometry);
    expect(mesh.geometry.getAttribute('position')).toBe(positions);
    expect(positions.getY(0)).toBeCloseTo(1);
    expect(dispose).not.toHaveBeenCalled();
    updateFilamentPaths(mesh, [moved.slice(0, 12), moved.slice(12)]);
    expect(dispose).toHaveBeenCalledOnce();
    expect(mesh.geometry).not.toBe(geometry);
    mesh.geometry.dispose();
    mesh.material.dispose();
  });

  it('attaches the relation at node surfaces and stays finite for coincident nodes', () => {
    const path = relationCurve({ x: 0, y: 0, z: 0 }, { x: 3, y: 0, z: 0 }, 0.5, 0.2);
    expect(path[0].x).toBeCloseTo(0.5);
    expect(path.at(-1)!.x).toBeCloseTo(2.8);
    for (const point of relationCurve({ x: 1, y: 2, z: 3 }, { x: 1, y: 2, z: 3 }, 0.5, 0.2)) {
      expect(Object.values(point).every(Number.isFinite)).toBe(true);
    }
  });
});
