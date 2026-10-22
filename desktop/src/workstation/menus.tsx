import { useRef, useState, type ComponentType, type ReactElement, type ReactNode } from 'react';
import * as ContextMenu from '@radix-ui/react-context-menu';
import { useOverlayRoot } from './Tip';

const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
export const shortcut = (key: string) => (isMac ? `⌘${key}` : `Ctrl+${key}`);

/** Composition Escape belongs to the IME, never to the menu. */
export const keepImeEscape = (event: KeyboardEvent) => {
  if (event.isComposing || event.keyCode === 229) event.preventDefault();
};

type Field = HTMLInputElement | HTMLTextAreaElement;

function fieldState(el: Field | null) {
  if (!el) return { hasSelection: false, editable: false, hasText: false };
  const start = el.selectionStart ?? 0;
  const end = el.selectionEnd ?? 0;
  return { hasSelection: end > start, editable: !el.readOnly && !el.disabled, hasText: el.value.length > 0 };
}

/** Right-click on a text field: cut / copy / paste / select all, drawn like every other workstation menu.
 *  Cut and copy go through execCommand so the field keeps its own undo stack and fires input as usual. */
export function TextFieldMenu({ children, fieldRef }: {
  children: ReactElement;
  fieldRef: { current: Field | null };
}) {
  const container = useOverlayRoot();
  const [state, setState] = useState(() => fieldState(null));
  const canPaste = typeof navigator !== 'undefined' && Boolean(navigator.clipboard?.readText);
  const run = (fn: (el: Field) => void) => () => {
    const el = fieldRef.current;
    if (!el) return;
    el.focus({ preventScroll: true });
    fn(el);
  };
  return (
    <ContextMenu.Root onOpenChange={(open) => { if (open) setState(fieldState(fieldRef.current)); }}>
      <ContextMenu.Trigger asChild>{children}</ContextMenu.Trigger>
      <ContextMenu.Portal container={container}>
        <ContextMenu.Content
          className="ws2-menu ws2-menu-text"
          data-ws2-overlay=""
          onEscapeKeyDown={keepImeEscape}
          onCloseAutoFocus={(event) => { event.preventDefault(); fieldRef.current?.focus({ preventScroll: true }); }}
        >
          <ContextMenu.Item className="ws2-menu-item" disabled={!state.hasSelection || !state.editable} onSelect={run(() => document.execCommand('cut'))}>
            剪切<span className="ws2-menu-key">{shortcut('X')}</span>
          </ContextMenu.Item>
          <ContextMenu.Item className="ws2-menu-item" disabled={!state.hasSelection} onSelect={run(() => document.execCommand('copy'))}>
            复制<span className="ws2-menu-key">{shortcut('C')}</span>
          </ContextMenu.Item>
          <ContextMenu.Item
            className="ws2-menu-item"
            disabled={!state.editable || !canPaste}
            onSelect={run((el) => {
              // Reading the clipboard needs the page's clipboard permission; a refusal leaves the field untouched.
              void navigator.clipboard.readText().then((text) => {
                if (!text) return;
                el.focus({ preventScroll: true });
                document.execCommand('insertText', false, text);
              }).catch(() => undefined);
            })}
          >
            粘贴<span className="ws2-menu-key">{shortcut('V')}</span>
          </ContextMenu.Item>
          <ContextMenu.Separator className="ws2-menu-sep" />
          <ContextMenu.Item className="ws2-menu-item" disabled={!state.hasText} onSelect={run((el) => el.select())}>
            全选<span className="ws2-menu-key">{shortcut('A')}</span>
          </ContextMenu.Item>
        </ContextMenu.Content>
      </ContextMenu.Portal>
    </ContextMenu.Root>
  );
}

function selectionWithin(el: Element | null): boolean {
  const selection = typeof window !== 'undefined' ? window.getSelection() : null;
  if (!el || !selection || selection.isCollapsed || !selection.rangeCount || !String(selection).trim()) return false;
  const range = selection.getRangeAt(0);
  return el.contains(range.commonAncestorContainer);
}

/** Right-click on selected transcript text offers 复制; anywhere else in the transcript nothing opens. */
export function SelectionCopyMenu({ children }: { children: ReactElement }) {
  const container = useOverlayRoot();
  const scope = useRef<Element | null>(null);
  return (
    <ContextMenu.Root>
      <ContextMenu.Trigger
        asChild
        onContextMenu={(event) => {
          scope.current = event.currentTarget as Element;
          // A prevented event never reaches Radix, so there is no menu without a selection.
          if (!selectionWithin(scope.current)) event.preventDefault();
        }}
      >
        {children}
      </ContextMenu.Trigger>
      <ContextMenu.Portal container={container}>
        <ContextMenu.Content className="ws2-menu ws2-menu-text" data-ws2-overlay="" onEscapeKeyDown={keepImeEscape}>
          <ContextMenu.Item className="ws2-menu-item" onSelect={() => { document.execCommand('copy'); }}>
            复制<span className="ws2-menu-key">{shortcut('C')}</span>
          </ContextMenu.Item>
        </ContextMenu.Content>
      </ContextMenu.Portal>
    </ContextMenu.Root>
  );
}

/** ContextMenu and DropdownMenu share Radix's Menu parts, so either namespace fits. */
export type MenuPrimitives = {
  Item: ComponentType<{ className?: string; onSelect?: (event: Event) => void; 'aria-label'?: string; children?: ReactNode }>;
  Separator: ComponentType<{ className?: string }>;
};

/** One list of session actions for both the "…" button and the row's right-click menu. */
export function SessionMenuItems<T extends { title: string; is_pinned: boolean }>({ M, conversation, onTogglePin, onRename, onForget, onDelete }: {
  M: MenuPrimitives;
  conversation: T;
  onTogglePin: (c: T) => void;
  onRename: (c: T) => void;
  onForget: (c: T) => void;
  onDelete: (c: T) => void;
}): ReactNode {
  return (
    <>
      <M.Item className="ws2-menu-item" onSelect={() => onTogglePin(conversation)} aria-label={`${conversation.is_pinned ? '取消置顶' : '置顶'} ${conversation.title}`}>
        {conversation.is_pinned ? '取消置顶' : '置顶'}
      </M.Item>
      <M.Item className="ws2-menu-item" onSelect={() => onRename(conversation)} aria-label={`重命名 ${conversation.title}`}>重命名</M.Item>
      <M.Separator className="ws2-menu-sep" />
      <M.Item className="ws2-menu-item" onSelect={() => onForget(conversation)} aria-label={`清空 ${conversation.title}`}>清空聊天记录…</M.Item>
      <M.Item className="ws2-menu-item danger" onSelect={() => onDelete(conversation)} aria-label={`删除 ${conversation.title}`}>删除会话…</M.Item>
    </>
  );
}
