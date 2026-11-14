import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
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
    cleanup();
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

  it('hot-reloads pack state, busts cache on reload, removes CSS vars on uninstall, and notifies subscribers', async () => {
    const {
      on,
      reloadAssetPack,
      subscribeAssetPack,
      getAssetPackRevision,
      packAsset,
      hasAssetPack,
    } = await load({ installed: false }, 200);
    expect(on).toBe(false);
    expect(getAssetPackRevision()).toBe(0);
    expect(document.documentElement.style.getPropertyValue('--pack-ring')).toBe('');

    const listener = vi.fn();
    const unsubscribe = subscribeAssetPack(listener);

    apiFetch.mockResolvedValueOnce(new Response(JSON.stringify({ format: 1 }), { status: 200 }));
    const afterInstall = await reloadAssetPack();
    expect(afterInstall).toBe(true);
    expect(hasAssetPack()).toBe(true);
    expect(getAssetPackRevision()).toBe(1);
    expect(apiFetch).toHaveBeenLastCalledWith(
      '/api/asset-pack/manifest.json',
      expect.objectContaining({ cache: 'no-store' }),
    );
    expect(packAsset('assets/images/kurisu_normal1.png')).toBe('/api/asset-pack/assets/images/kurisu_normal1.png?v=1');
    expect(document.documentElement.dataset.assetPack).toBe('on');
    expect(document.documentElement.style.getPropertyValue('--pack-login-bg')).toContain('SG0_IBG006D.png?v=1');
    expect(document.documentElement.style.getPropertyValue('--pack-ring')).toContain('ring_config.png?v=1');
    expect(listener).toHaveBeenCalledTimes(1);

    apiFetch.mockResolvedValueOnce(new Response(JSON.stringify({ installed: false }), { status: 200 }));
    const afterRemove = await reloadAssetPack();
    expect(afterRemove).toBe(false);
    expect(hasAssetPack()).toBe(false);
    expect(getAssetPackRevision()).toBe(2);
    expect(packAsset('assets/images/kurisu_normal1.png')).toBeNull();
    expect(document.documentElement.dataset.assetPack).toBe('off');
    expect(document.documentElement.style.getPropertyValue('--pack-login-bg')).toBe('');
    expect(document.documentElement.style.getPropertyValue('--pack-ring')).toBe('');
    expect(listener).toHaveBeenCalledTimes(2);

    unsubscribe();
    apiFetch.mockResolvedValueOnce(new Response(JSON.stringify({ format: 1 }), { status: 200 }));
    await reloadAssetPack();
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it('re-renders AvatarViewer live on reloadAssetPack and resets imgError state on revision change', async () => {
    const pack = await load({ installed: false }, 200);
    const { default: AvatarViewer } = await import('./components/AvatarViewer');

    render(React.createElement(AvatarViewer, { emotion: 'happy', isSpeaking: false, glitchState: 'none', variant: 'stage' }));
    expect(screen.getByTestId('avatar-absent')).toBeTruthy();

    apiFetch.mockResolvedValueOnce(new Response(JSON.stringify({ format: 1 }), { status: 200 }));
    await pack.reloadAssetPack();

    const calm = await screen.findByAltText('Kurisu Calm') as HTMLImageElement;
    expect(calm.getAttribute('src')).toBe('/api/asset-pack/assets/images/kurisu_happy1.png?v=1');

    fireEvent.error(calm);
    await waitFor(() => {
      expect((screen.getByAltText('Kurisu Calm') as HTMLImageElement).getAttribute('src')).toBe(
        '/api/asset-pack/assets/images/kurisu_normal1.png?v=1',
      );
    });

    apiFetch.mockResolvedValueOnce(new Response(JSON.stringify({ format: 1 }), { status: 200 }));
    await pack.reloadAssetPack();
    await waitFor(() => {
      expect((screen.getByAltText('Kurisu Calm') as HTMLImageElement).getAttribute('src')).toBe(
        '/api/asset-pack/assets/images/kurisu_happy1.png?v=2',
      );
    });
  });
});
