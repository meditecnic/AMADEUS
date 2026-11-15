import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ProviderMetadata } from '../components/ProviderControls';
import { ConnectionList } from './ConnectionList';
import { ConnectionsPanel, normalizeCustomBaseUrl, type ConnectionsPanelProps } from './ConnectionsPanel';

const row = (id: string, display_name: string, extra: Partial<ProviderMetadata> = {}) =>
  ({ id, display_name, default_model: 'm', configured: true, credential_required: true, ...extra }) as ProviderMetadata;

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

function makePanelProps(overrides: Partial<ConnectionsPanelProps> = {}): ConnectionsPanelProps {
  return {
    providers: [],
    activeProviderId: '',
    activeModelId: '',
    locked: false,
    checkFor: () => undefined,
    onCheck: vi.fn(async () => null),
    onUse: vi.fn(),
    onAdmit: vi.fn(async () => undefined),
    onReload: vi.fn(async () => undefined),
    onConfigChanged: vi.fn(),
    search: {
      active: 'tavily',
      providers: { tavily: { id: 'tavily', label: 'Tavily', configured: false } },
      onSelect: vi.fn(async () => undefined),
      onSaveKey: vi.fn(async () => undefined),
      onClear: vi.fn(async () => undefined),
    },
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('ConnectionList', () => {
  it('switches only to admitted connections and never to one still pending its first check', () => {
    const onSelect = vi.fn();
    render(
      <ConnectionList
        providers={[
          row('custom:a', 'Pending One', { selector_admission: 'pending' }),
          row('glm', 'GLM', { configured: false }),
          row('deepseek', 'DeepSeek', { selector_admission: 'admitted' }),
        ]}
        activeProviderId="deepseek"
        checkFor={() => undefined}
        locked={false}
        onSelect={onSelect}
      />,
    );

    expect(screen.getAllByRole('button')[0]).toHaveAccessibleName(/DeepSeek，当前接入/);
    fireEvent.click(screen.getByRole('button', { name: /Pending One/ }));
    expect(onSelect).not.toHaveBeenCalled();
    // A connection that only lacks a key may be chosen: that is how its key field is reached.
    fireEvent.click(screen.getByRole('button', { name: /设为当前接入：GLM，缺密钥/ }));
    expect(onSelect).toHaveBeenCalledWith('glm');
  });
});

describe('normalizeCustomBaseUrl', () => {
  it('strips /chat/completions suffixes case-insensitively and trailing slashes while preserving /v1', () => {
    expect(normalizeCustomBaseUrl('  https://gateway.local/v1/chat/completions  ')).toBe('https://gateway.local/v1');
    expect(normalizeCustomBaseUrl('https://gateway.local/v1/CHAT/Completions///')).toBe('https://gateway.local/v1');
    expect(normalizeCustomBaseUrl('https://gateway.local/chat/completions/chat/completions/')).toBe('https://gateway.local');
    expect(normalizeCustomBaseUrl('http://127.0.0.1:8080/v1///')).toBe('http://127.0.0.1:8080/v1');
    expect(normalizeCustomBaseUrl('   /chat/completions/  ')).toBe('');
  });
});

describe('ConnectionsPanel custom service onboarding', () => {
  it('allows keyless custom OpenAI-compatible services and strips /chat/completions on blur and submit', async () => {
    const connectBodies: Array<Record<string, unknown>> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/providers/connect' && init?.method === 'POST') {
        connectBodies.push(JSON.parse(String(init.body)));
        return json(200, {
          provider_id: 'custom:gw',
          models: ['local-qwen'],
          model_id: 'local-qwen',
          check: { ok: false, code: 'model_selection_required' },
          configuration_version: 'v1',
        });
      }
      return json(404, {});
    }));

    render(<ConnectionsPanel {...makePanelProps()} />);
    fireEvent.click(screen.getByRole('button', { name: '其他 OpenAI 兼容服务' }));

    const dialog = screen.getByRole('dialog', { name: '接入 OpenAI 兼容服务' });
    expect(within(dialog).getByText('无密钥的自建或本地网关服务可留空。')).toBeInTheDocument();

    const nextBtn = within(dialog).getByRole('button', { name: '下一步' });
    expect(nextBtn).toBeDisabled();

    const urlInput = within(dialog).getByLabelText(/^服务地址/) as HTMLInputElement;
    fireEvent.change(urlInput, { target: { value: 'https://gateway.local/v1/chat/completions/' } });
    fireEvent.blur(urlInput);
    expect(urlInput.value).toBe('https://gateway.local/v1');
    expect(nextBtn).toBeEnabled();

    fireEvent.click(nextBtn);
    await waitFor(() => expect(connectBodies).toHaveLength(1));
    expect(connectBodies[0]).toEqual({ base_url: 'https://gateway.local/v1', display_name: 'gateway.local' });
    expect('api_key' in connectBodies[0]).toBe(false);
    expect(await within(dialog).findByText('未填密钥（无密钥服务）')).toBeInTheDocument();
  });

  it('normalizes /chat/completions on direct submit without prior blur and on inline base_url edits', async () => {
    const connectBodies: Array<Record<string, unknown>> = [];
    const patchBodies: Array<Record<string, unknown>> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/providers/connect' && init?.method === 'POST') {
        connectBodies.push(JSON.parse(String(init.body)));
        return json(200, {
          provider_id: 'custom:gw',
          models: ['local-model'],
          model_id: 'local-model',
          check: { ok: false, code: 'model_selection_required' },
          configuration_version: 'v1',
        });
      }
      if (url === '/api/providers/custom-profiles/custom%3Agw' && init?.method === 'PATCH') {
        patchBodies.push(JSON.parse(String(init.body)));
        return json(200, { provider_id: 'custom:gw', configuration_version: 'v2' });
      }
      return json(200, { models: ['local-model'], catalog: [] });
    }));

    const existingCustom = row('custom:gw', 'Local Gateway', {
      source: 'user_config',
      base_url: 'https://old.local/v1',
      credential_required: false,
      configured: false,
    });
    render(<ConnectionsPanel {...makePanelProps({ providers: [existingCustom], activeProviderId: 'custom:gw', activeModelId: 'local-model' })} />);

    // Open existing custom row and edit its base URL with /chat/completions suffix.
    fireEvent.click(screen.getByRole('button', { name: /Local Gateway/ }));
    fireEvent.click(screen.getByRole('button', { name: '修改' }));
    const inlineUrl = screen.getByLabelText('服务地址') as HTMLInputElement;
    fireEvent.change(inlineUrl, { target: { value: 'https://edited.local/v1/chat/completions///' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => expect(patchBodies).toHaveLength(1));
    expect(patchBodies[0]).toEqual({ base_url: 'https://edited.local/v1' });

    // Open Add custom dialog and submit directly without blurring URL field first.
    fireEvent.click(screen.getByRole('button', { name: '其他 OpenAI 兼容服务' }));
    const dialog = screen.getByRole('dialog', { name: '接入 OpenAI 兼容服务' });
    const urlInput = within(dialog).getByLabelText(/^服务地址/) as HTMLInputElement;
    fireEvent.change(urlInput, { target: { value: 'https://direct.local/v1/CHAT/COMPLETIONS' } });
    fireEvent.click(within(dialog).getByRole('button', { name: '下一步' }));

    await waitFor(() => expect(connectBodies).toHaveLength(1));
    expect(connectBodies[0]).toEqual({ base_url: 'https://direct.local/v1', display_name: 'direct.local' });
  });

  it('keeps API key required for named cloud catalog providers such as DeepSeek and SiliconFlow', () => {
    render(<ConnectionsPanel {...makePanelProps()} />);

    fireEvent.click(screen.getByRole('button', { name: /深度求索/ }));
    const deepseekDialog = screen.getByRole('dialog', { name: '接入 DeepSeek' });
    expect(within(deepseekDialog).queryByText('无密钥的自建或本地网关服务可留空。')).not.toBeInTheDocument();
    expect(within(deepseekDialog).getByRole('button', { name: '下一步' })).toBeDisabled();
    fireEvent.change(within(deepseekDialog).getByLabelText(/^API 密钥/), { target: { value: 'sk-test-123' } });
    expect(within(deepseekDialog).getByRole('button', { name: '下一步' })).toBeEnabled();
    fireEvent.click(within(deepseekDialog).getByRole('button', { name: '取消' }));

    fireEvent.click(screen.getByRole('button', { name: /硅基流动/ }));
    const sfDialog = screen.getByRole('dialog', { name: '接入 硅基流动' });
    expect(within(sfDialog).queryByText('无密钥的自建或本地网关服务可留空。')).not.toBeInTheDocument();
    expect(within(sfDialog).getByRole('button', { name: '下一步' })).toBeDisabled();
    fireEvent.change(within(sfDialog).getByLabelText(/^API 密钥/), { target: { value: 'sk-sf-456' } });
    expect(within(sfDialog).getByRole('button', { name: '下一步' })).toBeEnabled();
  });
});
