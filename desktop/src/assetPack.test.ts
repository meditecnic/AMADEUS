import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetch = vi.fn();
vi.mock('./apiBase', async (original) => ({
  ...(await original<typeof import('./apiBase')>()),
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  apiUrl: (path: string) => path,
}));

async function load(manifest: unknown, status = 200) {
  apiFetch.mockResolvedValue(new Response(JSON.stringify(manifest), { status }));
  const pack = await import('./assetPack');
  const on = await pack.loadAssetPack();
  return { on, ...pack };
}

describe('asset pack', () => {
  beforeEach(() => { vi.resetModules(); apiFetch.mockReset(); });
  afterEach(() => {
    document.documentElement.style.removeProperty('--pack-ring');
    document.documentElement.style.removeProperty('--pack-login-bg');
    delete document.documentElement.dataset.assetPack;
  });

  it('treats a manifest without a file list as the full pack', async () => {
    const { on, packAsset, hasPackFile } = await load({ format: 1 });
    expect(on).toBe(true);
    expect(packAsset('/assets/boot/boot_ring_1.png')).toBe('/api/asset-pack/assets/boot/boot_ring_1.png');
    expect(hasPackFile('audio/bgm/anything.ogg')).toBe(true);
    expect(document.documentElement.style.getPropertyValue('--pack-ring')).toContain('ring_config.png');
  });

  it('serves only the listed files of a partial pack', async () => {
    const { on, packAsset, hasPackFile } = await load({ format: 1, files: ['assets/images/kurisu_normal1.png'] });
    expect(on).toBe(true);
    expect(document.documentElement.dataset.assetPack).toBe('on');
    expect(packAsset('/assets/images/kurisu_normal1.png')).toBe('/api/asset-pack/assets/images/kurisu_normal1.png');
    expect(hasPackFile('assets\\images\\kurisu_normal1.png')).toBe(true);
    expect(packAsset('assets/boot/boot_ring_1.png')).toBeNull();
    expect(packAsset('assets/ui/spinner_sg_20f.png')).toBeNull();
    expect(document.documentElement.style.getPropertyValue('--pack-ring')).toBe('');
  });

  it('has nothing without a pack', async () => {
    const { on, packAsset, hasPackFile } = await load({ detail: 'asset not found' }, 404);
    expect(on).toBe(false);
    expect(document.documentElement.dataset.assetPack).toBe('off');
    expect(packAsset('assets/images/kurisu_normal1.png')).toBeNull();
    expect(hasPackFile('assets/images/kurisu_normal1.png')).toBe(false);
  });
});
