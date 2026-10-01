import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ProviderMetadata } from '../components/ProviderControls';
import { ConnectionList } from './ConnectionList';

const row = (id: string, display_name: string, extra: Partial<ProviderMetadata> = {}) =>
  ({ id, display_name, default_model: 'm', configured: true, credential_required: true, ...extra }) as ProviderMetadata;

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
