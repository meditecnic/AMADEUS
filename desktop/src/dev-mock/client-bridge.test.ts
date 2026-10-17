import { beforeEach, describe, expect, it } from 'vitest';
import { installScenarioBridge } from './client-bridge';

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  history.replaceState({}, '', '/');
});

describe('dev-only scenario selection bridge', () => {
  it('takes URL selection over saved state and sends it to REST/WS through cookies', () => {
    localStorage.setItem('amadeus_mock_scenario', 'streaming');
    history.replaceState({}, '', '/?scenario=empty');
    installScenarioBridge(['streaming', 'empty']);
    expect(localStorage.getItem('amadeus_mock_scenario')).toBe('empty');
    expect(document.cookie).toContain('amadeus_mock_scenario=empty');
    expect(document.cookie).toContain(`amadeus_mock_client=${sessionStorage.getItem('amadeus_mock_client')}`);
  });

  it('honors localStorage on reload and retains the browser client id', () => {
    localStorage.setItem('amadeus_mock_scenario', 'empty');
    installScenarioBridge(['streaming', 'empty']);
    const client = sessionStorage.getItem('amadeus_mock_client');
    installScenarioBridge(['streaming', 'empty']);
    expect(document.cookie).toContain('amadeus_mock_scenario=empty');
    expect(sessionStorage.getItem('amadeus_mock_client')).toBe(client);
  });

  it('falls back deterministically for invalid scenario names', () => {
    history.replaceState({}, '', '/?scenario=unknown');
    installScenarioBridge(['streaming', 'empty']);
    expect(localStorage.getItem('amadeus_mock_scenario')).toBe('streaming');
    expect(document.cookie).toContain('amadeus_mock_scenario=streaming');
  });
});
