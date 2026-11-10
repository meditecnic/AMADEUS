import { describe, expect, it } from 'vitest';
import { connectError } from './connectApi';
import { ApiError } from './dataApi';
import { CATALOG, chatModels, entryFor, searchCatalog, suggestModel } from './providerCatalog';

describe('provider catalog', () => {
  it('has one entry per id, an address or a built-in for each, and http(s) addresses only', () => {
    const ids = CATALOG.map((entry) => entry.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const entry of CATALOG) {
      const urls = [entry.baseUrl, ...(entry.regions ?? []).map((r) => r.baseUrl)].filter(Boolean) as string[];
      expect(entry.builtin || urls.length).toBeTruthy();
      for (const url of urls) expect(url).toMatch(/^https?:\/\/[^/?#\s]+(\/[^?#\s]*)?$/);
      if (entry.keyUrl) expect(entry.keyUrl).toMatch(/^https:\/\//);
    }
  });

  it('finds the entry a connected row came from, by built-in id or by address', () => {
    expect(entryFor({ id: 'glm' })?.id).toBe('glm');
    expect(entryFor({ id: 'custom:x', base_url: 'https://api.moonshot.ai/v1/' })?.id).toBe('moonshot');
    expect(entryFor({ id: 'custom:y', base_url: 'https://relay.example.com/v1' })).toBeUndefined();
  });

  it('keeps chat models and suggests the preferred one', () => {
    expect(chatModels(['gpt-5.1', 'text-embedding-3-large', 'whisper-1', 'tts-1'])).toEqual(['gpt-5.1']);
    const moonshot = CATALOG.find((entry) => entry.id === 'moonshot');
    expect(suggestModel(moonshot, ['moonshot-v1-8k', 'kimi-k2-turbo-preview'])).toBe('kimi-k2-turbo-preview');
    expect(suggestModel(undefined, ['b', 'a'])).toBe('b');
  });

  it('searches names, aliases and blurbs', () => {
    expect(searchCatalog('千问').map((entry) => entry.id)).toContain('dashscope');
    expect(searchCatalog('claude').map((entry) => entry.id)).toContain('openrouter');
    expect(searchCatalog('')).toHaveLength(CATALOG.length);
  });

  it('says what to do for each failure, and blames the key for a list the service refused', () => {
    expect(connectError(new ApiError(401, 'credential_rejected')).title).toBe('密钥被拒绝了');
    expect(connectError(new ApiError(502, 'provider_unavailable'), 'list').title).toBe('读不到模型列表');
    expect(connectError(new ApiError(502, 'provider_unavailable'), 'check').title).toBe('服务商没有正常回应');
    expect(connectError(new ApiError(0, 'something_new')).fix).toContain('something_new');
  });
});
