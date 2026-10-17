/** Serialized into a dev-only classic script before the app's module entry. */
export function installScenarioBridge(names: string[]) {
  const fallback = 'streaming';
  let name = new URLSearchParams(location.search).get('scenario');
  try {
    name ??= localStorage.getItem('amadeus_mock_scenario');
  } catch { /* Storage may be blocked; URL selection still works. */ }
  if (!name || !names.includes(name)) name = fallback;
  try { localStorage.setItem('amadeus_mock_scenario', name); } catch { /* Cookie below is sufficient. */ }
  let clientId: string | null = null;
  try {
    clientId = sessionStorage.getItem('amadeus_mock_client');
    if (!clientId) {
      clientId = crypto.randomUUID();
      sessionStorage.setItem('amadeus_mock_client', clientId);
    }
  } catch { clientId = crypto.randomUUID(); }
  document.cookie = `amadeus_mock_scenario=${encodeURIComponent(name)}; Path=/; SameSite=Lax`;
  document.cookie = `amadeus_mock_client=${encodeURIComponent(clientId)}; Path=/; SameSite=Lax`;
}
