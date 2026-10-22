/** First candidate that can really take focus: still in the document, enabled, not inert and rendered. */
export function focusableTarget(candidates: ReadonlyArray<HTMLElement | null | undefined>): HTMLElement | null {
  for (const node of candidates) {
    if (!node || !node.isConnected || node === document.body) continue;
    if (node.matches(':disabled') || node.closest('[inert]')) continue;
    if (typeof node.checkVisibility === 'function' && !node.checkVisibility()) continue;
    return node;
  }
  return null;
}

/** Where focus goes after a session dialog closes: that row's menu button while the row survives (cancel,
 *  failure, rename), otherwise the place the user lands — the composer of a blank draft, or the current
 *  session in the list — then the list's other surviving controls. */
export function sessionDialogFallback(root: HTMLElement | null, conversationId: string, landedOnDraft: boolean): HTMLElement[] {
  if (!root) return [];
  const pick = (selector: string) => root.querySelector<HTMLElement>(selector);
  const row = Array.from(root.querySelectorAll<HTMLElement>('.conversation-item')).find(node => node.dataset.conversationId === conversationId);
  const composer = pick('textarea.chat-input-field');
  const current = pick('.conversation-item.active .conversation-select');
  const fresh = pick('.history-new-btn');
  // The composer can be disabled for a moment while a blank draft reconnects; the list's own controls and the
  // always-present sidebar toggle keep the keyboard on a real control meanwhile.
  const order = landedOnDraft ? [composer, fresh] : [current, fresh, composer];
  order.push(pick('.conversation-search'), pick('.ws2-top .ws2-icon-btn'));
  return [row?.querySelector<HTMLElement>('.conversation-more'), ...order].filter((node): node is HTMLElement => Boolean(node));
}
