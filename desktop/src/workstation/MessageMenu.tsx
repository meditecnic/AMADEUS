import { useEffect, useRef, useState } from 'react';
import * as DropdownMenu from '@radix-ui/react-dropdown-menu';
import { Ellipsis } from 'lucide-react';
import type { Message } from '../hooks/useAmadeusWS';
import { ConfirmDialog } from './ConfirmDialog';
import { Stamp } from './Stamp';
import { useOverlayRoot } from './Tip';
import { keepImeEscape } from './menus';

export type TruncateMode = 'rollback' | 'edit';
export interface TruncateTarget {
  mode: TruncateMode;
  message: Message;
  fromMessageId: number;
  expectedLastMessageId: number;
  deletedTurns: number;
}
export interface ReplayStatus {
  messageId: string;
  phase: 'loading' | 'playing';
  source?: 'original' | 'synthesized';
}

/** Resolve the backend truncation point and affected turn count for one message action. */
export function resolveTruncateTarget(messages: readonly Message[], targetId: string, mode: TruncateMode): TruncateTarget | null {
  const index = messages.findIndex((msg) => msg.id === targetId);
  if (index < 0) return null;
  const target = messages[index];
  if (target.systemError || target.streaming || !target.dbId) return null;
  const expectedLastMessageId = messages.reduce(
    (max, msg) => (!msg.systemError && typeof msg.dbId === 'number' && msg.dbId > max ? msg.dbId : max),
    0,
  );
  if (!expectedLastMessageId) return null;
  const cutIndex = mode === 'edit'
    ? (target.role === 'user' && target.dbId ? index : -1)
    : messages.findIndex((msg, i) => i > index && !msg.systemError && msg.role === 'user' && !!msg.dbId);
  const fromMessageId = cutIndex >= 0 ? messages[cutIndex].dbId : undefined;
  if (!fromMessageId) return null;
  const deletedTurns = messages.slice(cutIndex).filter((msg) => !msg.systemError && msg.role === 'user' && !!msg.dbId).length;
  return deletedTurns > 0 ? { mode, message: target, fromMessageId, expectedLastMessageId, deletedTurns } : null;
}

export function formatTruncateError(code: string | null | undefined): string {
  if (code === 'conversation_busy') return '她正在回复这一轮，请先停掉当前回复后再试。';
  if (code === 'conversation_changed') return '对话内容刚刚有更新，请刷新画面确认后再试。';
  if (code === 'invalid_truncate_point') return '这条消息不能作为回退起点，请刷新对话后再试。';
  return '回退对话没有成功，请稍后再试。';
}

export function formatReplayError(code: string | null | undefined): { text: string; kind: 'info' | 'error' } {
  if (code === 'tts_unavailable') return { text: '语音服务未启动或不可用，暂时无法重播。', kind: 'error' };
  if (code === 'replay_unplayable') return { text: '这条回复没有可朗读的语音内容。', kind: 'info' };
  if (code === 'message_erased' || code === 'message_not_found') return { text: '这条消息已被删除，无法重播。', kind: 'error' };
  return { text: '重播没有成功，请稍后再试。', kind: 'error' };
}

export async function copyText(text: string): Promise<boolean> {
  if (!text) return false;
  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
    try { await navigator.clipboard.writeText(text); return true; } catch {}
  }
  if (typeof document === 'undefined') return false;
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  area.style.cssText = 'position:fixed;opacity:0';
  document.body.appendChild(area);
  area.select();
  try { return document.execCommand('copy'); } catch { return false; } finally { document.body.removeChild(area); }
}

export function MessageMenu({
  message, messages, replay, disabled = false, onReplay, onStopReplay, onCopy, onTruncateRequest,
}: {
  message: Message; messages: readonly Message[]; replay: ReplayStatus | null; disabled?: boolean;
  onReplay?: (message: Message) => void; onStopReplay?: () => void;
  onCopy: (text: string, label: string) => void; onTruncateRequest: (target: TruncateTarget) => void;
}) {
  const container = useOverlayRoot();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const openingDialogRef = useRef(false);
  const [open, setOpen] = useState(false);
  if (message.systemError || message.streaming) return null;

  const isAssistant = message.role === 'assistant';
  const isThisReplaying = replay?.messageId === message.id;
  const unpersistedTitle = !message.dbId ? '这条消息尚未写入记录，暂不可操作' : undefined;
  const rollbackTarget = resolveTruncateTarget(messages, message.id, 'rollback');
  const editTarget = isAssistant ? null : resolveTruncateTarget(messages, message.id, 'edit');
  const jaText = (message.segments?.length ? message.segments.map((s) => s.ja).join('') : message.content).trim();
  const zhText = (message.segments?.length ? message.segments.map((s) => s.zh).join('') : message.translation || '').trim();
  const openTruncate = (target: TruncateTarget | null) => {
    if (!target) return;
    openingDialogRef.current = true;
    onTruncateRequest(target);
  };

  return (
    <div className={`ws2-msg-menu${open ? ' is-open' : ''}`}>
      <DropdownMenu.Root open={open} onOpenChange={setOpen}>
        <DropdownMenu.Trigger asChild>
          <button ref={triggerRef} type="button" className="ws2-msg-more" aria-label="消息操作"
            data-testid={`message-menu-trigger-${message.id}`} disabled={disabled}>
            <Ellipsis size={14} strokeWidth={1.9} aria-hidden="true" />
          </button>
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal container={container}>
          <DropdownMenu.Content
            className="ws2-menu ws2-msg-menu-pop" data-ws2-overlay="" side="bottom"
            align={isAssistant ? 'end' : 'start'} sideOffset={4} onEscapeKeyDown={keepImeEscape}
            onCloseAutoFocus={(event) => {
              event.preventDefault();
              if (openingDialogRef.current) { openingDialogRef.current = false; return; }
              triggerRef.current?.focus({ preventScroll: true });
            }}
          >
            {isAssistant ? (
              <>
                <DropdownMenu.Item className="ws2-menu-item" disabled={!message.dbId && !isThisReplaying}
                  title={!message.dbId && !isThisReplaying ? unpersistedTitle : undefined}
                  onSelect={() => { if (isThisReplaying) onStopReplay?.(); else onReplay?.(message); }}>
                  {isThisReplaying ? (replay?.phase === 'loading' ? '取消重播' : '停止重播') : '重播'}
                </DropdownMenu.Item>
                <DropdownMenu.Item className="ws2-menu-item" disabled={!jaText} onSelect={() => onCopy(jaText, '已复制日文')}>复制日文</DropdownMenu.Item>
                <DropdownMenu.Item className="ws2-menu-item" disabled={!zhText} onSelect={() => onCopy(zhText, '已复制中文')}>复制中文</DropdownMenu.Item>
                <DropdownMenu.Separator className="ws2-menu-sep" />
                <DropdownMenu.Item className="ws2-menu-item danger" disabled={!rollbackTarget}
                  title={!rollbackTarget ? unpersistedTitle : undefined}
                  onSelect={() => openTruncate(rollbackTarget)}>回到这里…</DropdownMenu.Item>
              </>
            ) : (
              <>
                <DropdownMenu.Item className="ws2-menu-item" disabled={!message.content.trim()} onSelect={() => onCopy(message.content, '已复制')}>复制</DropdownMenu.Item>
                <DropdownMenu.Separator className="ws2-menu-sep" />
                <DropdownMenu.Item className="ws2-menu-item danger" disabled={!editTarget}
                  title={!editTarget ? unpersistedTitle : undefined}
                  onSelect={() => openTruncate(editTarget)}>改这句重发…</DropdownMenu.Item>
                <DropdownMenu.Item className="ws2-menu-item danger" disabled={!rollbackTarget}
                  title={!rollbackTarget ? unpersistedTitle : undefined}
                  onSelect={() => openTruncate(rollbackTarget)}>回到这里…</DropdownMenu.Item>
              </>
            )}
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
    </div>
  );
}

export function TruncateConfirmDialog({
  target, busy, error, onConfirm, onCancel,
}: {
  target: TruncateTarget; busy: boolean; error: string | null;
  onConfirm: (editedText?: string) => void; onCancel: () => void;
}) {
  const isEdit = target.mode === 'edit';
  const [draft, setDraft] = useState(target.message.content);
  const [localError, setLocalError] = useState<string | null>(null);
  useEffect(() => { setDraft(target.message.content); setLocalError(null); }, [target]);

  return (
    <ConfirmDialog
      title={isEdit ? '修改这句并重新发送？' : '回到这一轮？'}
      confirmLabel={isEdit ? '截断并重发' : '确认回到这里'}
      danger focusCancel={!isEdit}
      stamp={<Stamp sub={`${target.deletedTurns} 轮`}>不可撤回</Stamp>}
      busy={busy} error={localError ?? error}
      onConfirm={() => {
        if (busy) return;
        const trimmed = draft.trim();
        if (isEdit && !trimmed) { setLocalError('发送内容不能为空。'); return; }
        setLocalError(null);
        onConfirm(isEdit ? trimmed : undefined);
      }}
      onCancel={onCancel}
    >
      {isEdit ? (
        <>
          <label className="ws2-truncate-edit">
            <span>改写内容</span>
            <textarea aria-label="修改要重发的消息" data-testid="truncate-edit-textarea" rows={3}
              value={draft} disabled={busy}
              onChange={(event) => { setDraft(event.target.value); if (localError) setLocalError(null); }} />
          </label>
          <p>将删除从这句起的 {target.deletedTurns} 轮对话，再用修改后的内容重新发送。</p>
        </>
      ) : (
        <p>将删除之后的 {target.deletedTurns} 轮对话，只保留到这一轮为止。</p>
      )}
      <p>她会忘掉只在这 {target.deletedTurns} 轮里说过的事，相关的手记与摘要也会同步清除。</p>
      <p className="ws2-dialog-hint">你置顶、手动确认或手动修改过的记忆会保留。此操作无法撤销。</p>
    </ConfirmDialog>
  );
}
