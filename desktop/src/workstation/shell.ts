import { invoke } from '@tauri-apps/api/core';

/*
 * What the desktop shell can do for a page: show a file in Explorer and open a web page in the browser (the opener
 * plugin's default permissions), tell the app's version, and find the bundled licence inventory. In a plain browser
 * (the dev servers) none of the shell is there: links open in a new tab and the rest says it is unavailable.
 */

export const inShell = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;

export const ISSUES_URL = 'https://github.com/meditecnic/AMADEUS/issues/new/choose';
export const NOTICES_URL = 'https://github.com/meditecnic/AMADEUS/blob/main/THIRD_PARTY_NOTICES.md';
export const JINA_MODEL_URL = 'https://huggingface.co/jinaai/jina-embeddings-v5-text-nano-retrieval';
export const CC_BY_NC_URL = 'https://creativecommons.org/licenses/by-nc/4.0/';

/** Shows the file selected in its folder. False when there is no shell to ask. */
export async function revealInFolder(path: string): Promise<boolean> {
  if (!inShell) return false;
  await invoke('plugin:opener|reveal_item_in_dir', { paths: [path] });
  return true;
}

export async function openExternal(url: string): Promise<void> {
  if (inShell) {
    await invoke('plugin:opener|open_url', { url });
    return;
  }
  window.open(url, '_blank', 'noopener,noreferrer');
}

/** The installed version, or null outside the shell. */
export async function shellVersion(): Promise<string | null> {
  if (!inShell) return null;
  const { getVersion } = await import('@tauri-apps/api/app');
  return getVersion();
}

/** The installer's licence inventory (`licenses/manifest.json` beside the app), or null outside the shell. */
export async function licensesManifest(): Promise<string | null> {
  if (!inShell) return null;
  const { resolveResource } = await import('@tauri-apps/api/path');
  return resolveResource('licenses/manifest.json');
}

/** Opens a native directory or zip picker in the desktop shell, or returns null outside Tauri. */
export async function pickPath(kind: 'directory' | 'zip'): Promise<string | null> {
  if (!inShell) return null;
  const picked = await invoke<string | null>('pick_path', { kind });
  return typeof picked === 'string' && picked.length > 0 ? picked : null;
}

/** Restarts the desktop shell and backend so a pending restore or relocation takes effect. */
export async function restartApp(): Promise<void> {
  if (!inShell) throw new Error('shell_unavailable');
  await invoke('restart_app');
}

/** Hands a downloaded file to the browser's save. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.rel = 'noopener';
  link.style.display = 'none';
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
