import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import type { ProviderMetadata } from '../components/ProviderControls';
import type { WebSearchProviderId, WebSearchProviderInfo } from '../components/SettingsModal';
import { ConfirmDialog } from './ConfirmDialog';
import { chatCheckMessage, type ChatCheckState } from './ConnectionCheck';
import { connectionLamp, type ConnectionLamp } from './ConnectionList';
import { clearKey, connect, ConnectFailure, connectError, listModels, removeProfile, saveKey, updateProfile } from './connectApi';
import { ApiError } from './dataApi';
import { setShelf, useModelShelf } from './modelShelf';
import {
  baseUrlOf, canConnect, CATALOG, catalogEntry, chatModels, CUSTOM_ENTRY, entryFor, GROUP_LABEL, GROUP_SHORT, GROUPS, searchCatalog,
  suggestModel, type CatalogEntry, type CatalogGroup,
} from './providerCatalog';
import { ProviderMark } from './ProviderMark';
import { openExternal } from './shell';
import { focusableTarget } from './focusHandoff';


/*
 * 02 连接 (U 11-09: "做到更多更全，更为用户考虑", opencode's provider pages for reference, small windows where they
 * fit). One page, three parts and a fourth for search:
 *   已接入 — the services this computer can talk through, the one in use first. A row opens in place to change its
 *            key or address, test it, pick which of its models show in the composer, or remove it.
 *   添加   — the common services not yet connected, each one line on what it is; 全部服务商 opens the whole list in
 *            a small window with search. Choosing one opens the connect window: paste the key and it saves the key,
 *            reads the model list, and says one sentence with the suggested model, ticking each step; a failure says
 *            what went wrong and what to try, in plain words.
 *   联网搜索 — the search service and its key.
 */

const LAMP_TEXT: Record<ConnectionLamp, string> = {
  ok: '可用', manual: '手动启用', idle: '未测试', checking: '测试中', bad: '测试没通过', key: '缺密钥', pending: '待测试', off: '已停用',
};

const isCustom = (row: ProviderMetadata) => row.source === 'user_config' || row.id === 'custom' || row.id.startsWith('custom:');

/** Connected: a built-in with its key, a working custom profile, or whatever is in use now. */
function connected(row: ProviderMetadata, activeId: string): boolean {
  if (row.id === activeId) return true;
  if (row.enabled === false) return false;
  if (row.id === 'custom') return Boolean(row.configured); // the legacy single slot shows only once it has been set up
  if (isCustom(row)) return true;
  return !row.credential_required || Boolean(row.configured);
}

export type ConnectionsPanelProps = {
  providers: ProviderMetadata[];
  activeProviderId: string;
  activeModelId: string;
  /** A turn is running: the service in use is kept until it ends. */
  locked: boolean;
  checkFor: (providerId: string, modelId: string) => ChatCheckState | undefined;
  onCheck: (providerId: string, modelId: string) => Promise<ChatCheckState | null>;
  onUse: (providerId: string) => void;
  onAdmit: (providerId: string, modelId: string, version: string | null | undefined, admission?: 'admitted' | 'manual') => Promise<void>;
  onReload: () => Promise<unknown>;
  /** A key, address or model changed: earlier test results for it no longer count. */
  onConfigChanged: (providerId: string) => void;
  search: {
    active: WebSearchProviderId;
    providers: Partial<Record<WebSearchProviderId, WebSearchProviderInfo>>;
    onSelect: (id: WebSearchProviderId) => Promise<void>;
    onSaveKey: (id: WebSearchProviderId, key: string) => Promise<void>;
    onClear: (id: WebSearchProviderId) => Promise<void>;
  };
};

export function ConnectionsPanel(props: ConnectionsPanelProps) {
  const { providers, activeProviderId } = props;
  const anchor = useRef<HTMLDivElement>(null);
  // The service in use without its key opens straight to the key field (the composer's 缺密钥 lands here).
  const [open, setOpen] = useState<string | null>(() => {
    const active = providers.find((row) => row.id === activeProviderId);
    return active?.credential_required && !active.configured ? active.id : null;
  });
  // The whole list, opened on one group (套餐 from its own link) or on all of them.
  const [catalogOpen, setCatalogOpen] = useState<CatalogGroup | 'all' | null>(null);
  const [connecting, setConnecting] = useState<CatalogEntry | null>(null);

  const rows = providers
    .filter((row) => connected(row, activeProviderId))
    .sort((a, b) => Number(b.id === activeProviderId) - Number(a.id === activeProviderId));
  const taken = new Set(rows.map((row) => entryFor(row)?.id).filter(Boolean));
  const addable = CATALOG.filter((entry) => entry.popular && !taken.has(entry.id));

  return (
    <div className="ws2-cx" ref={anchor}>
      <section className="ws2-m-sec" aria-labelledby="ws2-cx-on">
        <div className="ws2-m-group" id="ws2-cx-on">已接入 <small>{rows.length}</small></div>
        {rows.length ? (
          <ul className="ws2-cx-list">
            {rows.map((row) => (
              <ConnectedRow
                key={row.id}
                row={row}
                {...props}
                open={open === row.id}
                onToggle={() => setOpen((current) => (current === row.id ? null : row.id))}
                anchor={anchor}
              />
            ))}
          </ul>
        ) : (
          <p className="ws2-m-flag">还没有能用的服务。从下面选一家接入，粘贴密钥就行。</p>
        )}
      </section>

      <section className="ws2-m-sec" aria-labelledby="ws2-cx-add">
        <div className="ws2-m-group" id="ws2-cx-add">添加</div>
        <ul className="ws2-cx-add">
          {addable.map((entry) => (
            <li key={entry.id}>
              <button type="button" className="ws2-cx-entry" onClick={() => setConnecting(entry)}>
                <ProviderMark provider={{ id: entry.builtin ?? entry.id, display_name: entry.name, base_url: baseUrlOf(entry) }} />
                <span className="txt"><b>{entry.name}</b><small>{entry.blurb}</small></span>
                <span className="go" aria-hidden="true">接入</span>
              </button>
            </li>
          ))}
        </ul>
        <div className="ws2-m-foot">
          <p className="ws2-m-note">密钥只存在这台电脑上，不回显，也不会发给 AMADEUS 以外的地方。</p>
          <span className="ws2-m-acts">
            <button type="button" className="ws2-m-link" onClick={() => setConnecting(CUSTOM_ENTRY)}>其他 OpenAI 兼容服务</button>
            <button type="button" className="ws2-m-link" onClick={() => setCatalogOpen('plan')}>用订阅套餐</button>
            <button type="button" className="ws2-m-btn" onClick={() => setCatalogOpen('all')}>全部服务商（{CATALOG.length}）</button>
          </span>
        </div>
      </section>

      <SearchGroup {...props.search} />

      {catalogOpen ? (
        <Sheet anchor={anchor} title="全部服务商" sub="选一家，下一步粘贴密钥。按月订阅的套餐在「套餐」里。" onClose={() => setCatalogOpen(null)} wide>
          <CatalogList
            taken={taken}
            initial={catalogOpen}
            onPick={(entry) => { setCatalogOpen(null); setConnecting(entry); }}
          />
        </Sheet>
      ) : null}
      {connecting ? (
        <ConnectSheet
          key={connecting.id}
          anchor={anchor}
          entry={connecting}
          onSwitch={setConnecting}
          existing={connecting.builtin ? providers.find((row) => row.id === connecting.builtin) : undefined}
          {...props}
          onClose={() => setConnecting(null)}
        />
      ) : null}
    </div>
  );
}

/* ---------- a connected service ---------- */

function ConnectedRow({
  row, open, onToggle, anchor, activeProviderId, activeModelId, locked, checkFor, onCheck, onUse, onAdmit, onReload, onConfigChanged,
}: ConnectionsPanelProps & { row: ProviderMetadata; open: boolean; onToggle: () => void; anchor: React.RefObject<HTMLDivElement | null> }) {
  const active = row.id === activeProviderId;
  const model = active ? activeModelId : row.default_model;
  const check = checkFor(row.id, model);
  const lamp = connectionLamp(row, check);
  const custom = isCustom(row);
  const entry = entryFor(row);
  const detailId = useId();
  const [models, setModels] = useState<string[] | null | 'loading' | 'failed'>(null);
  const [filter, setFilter] = useState('');
  const [keyEdit, setKeyEdit] = useState<string | null>(active && row.credential_required && !row.configured ? '' : null);
  const [urlEdit, setUrlEdit] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ bad?: boolean; text: string } | null>(null);
  const [asking, setAsking] = useState<'remove' | 'clear' | null>(null);
  const shelf = useModelShelf();

  useEffect(() => {
    if (!open || models !== null || !row.model_discovery || (row.credential_required && !row.configured)) return;
    setModels('loading');
    listModels(row.id).then((ids) => setModels(ids ? chatModels(ids) : null)).catch(() => setModels('failed'));
  }, [open, models, row]);

  const test = async () => {
    setNote(null);
    const result = await onCheck(row.id, model);
    if (result?.status === 'ok' && row.selector_admission === 'pending') await onAdmit(row.id, model, row.configuration_version);
  };
  const act = async (work: () => Promise<unknown>, done?: string) => {
    setBusy(true);
    setNote(null);
    try {
      await work();
      onConfigChanged(row.id);
      await onReload();
      if (done) setNote({ text: done });
      return true;
    } catch (failure) {
      const words = connectError(failure);
      setNote({ bad: true, text: `${words.title}。${words.fix}` });
      return false;
    } finally {
      setBusy(false);
    }
  };
  const list = Array.isArray(models) ? models : [];
  const kept = shelf[row.id];
  const shown = (id: string) => !kept?.length || kept.includes(id);
  const visible = list.filter((id) => id.toLowerCase().includes(filter.trim().toLowerCase()));
  const toggle = (id: string) => {
    const base = kept?.length ? kept : list;
    const next = base.includes(id) ? base.filter((x) => x !== id) : [...base, id];
    setShelf(row.id, next.length === list.length ? null : next);
  };

  return (
    <li className={`ws2-cx-row${active ? ' is-active' : ''}${open ? ' is-open' : ''}`}>
      <div className="head">
        <button type="button" className="main" aria-expanded={open} aria-controls={detailId} onClick={onToggle}>
          <ProviderMark provider={row} />
          <span className="txt">
            <b>{row.display_name}</b>
            <small>{model || '未选模型'}{custom && row.base_url ? ` · ${hostOf(row.base_url)}` : ''}</small>
          </span>
          <span className={`ws2-cx-lamp is-${lamp}`}><i aria-hidden="true" />{LAMP_TEXT[lamp]}</span>
          <span className="chev" aria-hidden="true" />
        </button>
        {active ? <span className="ws2-cx-tag">当前</span> : (
          <button type="button" className="ws2-m-link ws2-cx-use" disabled={locked || lamp === 'pending' || lamp === 'off' || lamp === 'key'}
            title={locked ? '这一轮回复结束后才能换' : undefined} onClick={() => onUse(row.id)}>使用</button>
        )}
      </div>
      {open ? (
        <div className="detail" id={detailId}>
          {lamp === 'pending' ? (
            <p className="ws2-m-flag">这个接入还没通过测试，暂时不出现在对话的模型菜单里。测一下，通过后自动启用。</p>
          ) : null}
          <div className="ws2-m-row">
            <div className="ws2-m-label">
              <b>连接测试</b>
              <small>
                {check?.status === 'checking' ? '正在用 ' + model + ' 试说一句…'
                  : check?.status === 'ok' ? `通过 · ${model} 能正常回复`
                    : check?.status === 'failed' ? chatCheckMessage(check.code)
                      : '会用所选模型发一句很短的话，可能产生极少的费用；不含聊天内容。'}
              </small>
            </div>
            <span className="ws2-m-acts">
              {lamp === 'pending' ? (
                <button type="button" className="ws2-m-link" onClick={() => { void onAdmit(row.id, model, row.configuration_version, 'manual'); }}>不测，直接启用</button>
              ) : null}
              <button type="button" className="ws2-m-btn" onClick={() => { void test(); }} disabled={check?.status === 'checking' || !model}>测一下</button>
            </span>
          </div>

          {row.credential_required || custom ? (
            <div className="ws2-m-row">
              <div className="ws2-m-label">
                <b>密钥</b>
                <small>{row.configured ? '已保存 · 不回显' : custom ? '没有密钥（本机服务通常不需要）' : '还没有密钥'}</small>
              </div>
              {keyEdit === null ? (
                <span className="ws2-m-acts">
                  {entry?.keyUrl ? <button type="button" className="ws2-m-link" onClick={() => { void openExternal(entry.keyUrl!).catch(() => undefined); }}>去获取 ↗</button> : null}
                  {row.configured && !custom ? <button type="button" className="ws2-m-link is-danger" onClick={() => setAsking('clear')}>清除</button> : null}
                  <button type="button" className="ws2-m-btn" onClick={() => setKeyEdit('')}>{row.configured ? '更换' : '填写'}</button>
                </span>
              ) : (
                <form className="ws2-cx-inline" onSubmit={(e) => {
                  e.preventDefault();
                  const key = keyEdit.trim();
                  if (!key) return;
                  void act(() => saveKey(row.id, key), '密钥已保存。测一下看看能不能用。').then((ok) => { if (ok) setKeyEdit(null); });
                }}>
                  <input type="password" className="ws2-cx-field" value={keyEdit} autoFocus autoComplete="new-password" spellCheck={false}
                    placeholder="粘贴新的 API 密钥" aria-label={`${row.display_name} 的新密钥`} data-testid={active ? 'credential-input' : undefined}
                    onChange={(e) => setKeyEdit(e.target.value)} onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); e.preventDefault(); setKeyEdit(null); } }} />
                  <button type="button" className="ws2-m-link" onClick={() => setKeyEdit(null)}>取消</button>
                  <button type="submit" className="ws2-m-btn" disabled={busy || !keyEdit.trim()}>{busy ? '保存中…' : '保存'}</button>
                </form>
              )}
            </div>
          ) : null}

          {custom && row.id !== 'custom' ? (
            <div className="ws2-m-row">
              <div className="ws2-m-label"><b>服务地址</b><small className="ws2-path">{row.base_url || '—'}</small></div>
              {urlEdit === null ? (
                <span className="ws2-m-acts"><button type="button" className="ws2-m-btn" onClick={() => setUrlEdit(row.base_url ?? '')}>修改</button></span>
              ) : (
                <form className="ws2-cx-inline" onSubmit={(e) => {
                  e.preventDefault();
                  void act(() => updateProfile(row.id, { base_url: urlEdit.trim() }, row.configuration_version ?? null), '地址已改。测一下看看能不能用。')
                    .then((ok) => { if (ok) { setUrlEdit(null); setModels(null); } });
                }}>
                  <input type="url" className="ws2-cx-field" value={urlEdit} autoFocus spellCheck={false} aria-label="服务地址"
                    onChange={(e) => setUrlEdit(e.target.value)} onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); e.preventDefault(); setUrlEdit(null); } }} />
                  <button type="button" className="ws2-m-link" onClick={() => setUrlEdit(null)}>取消</button>
                  <button type="submit" className="ws2-m-btn" disabled={busy || !urlEdit.trim()}>{busy ? '保存中…' : '保存'}</button>
                </form>
              )}
            </div>
          ) : null}

          {row.model_discovery ? (
            <div className="ws2-cx-models">
              <div className="ws2-m-label">
                <b>对话里显示的模型</b>
                <small>
                  {models === 'loading' ? '正在读取模型列表…'
                    : models === 'failed' ? '没能读到模型列表。'
                      : models === null ? (row.credential_required && !row.configured ? '先填密钥才能读到模型列表。' : '这个服务不提供模型列表。')
                        : `勾选的会出现在对话的模型菜单里 · 已选 ${kept?.length ? list.filter(shown).length : list.length} / 共 ${list.length}`}
                </small>
              </div>
              {list.length ? (
                <>
                  {list.length > 10 ? (
                    <input type="search" className="ws2-cx-field ws2-cx-filter" placeholder="筛选模型" aria-label="筛选模型" value={filter} onChange={(e) => setFilter(e.target.value)} />
                  ) : null}
                  <ul className="ws2-cx-modelgrid">
                    {visible.map((id) => (
                      <li key={id}>
                        <label className={shown(id) ? 'on' : undefined}>
                          <input type="checkbox" checked={shown(id)} onChange={() => toggle(id)} />
                          <span className="ws2-path">{id}</span>
                          {id === row.default_model ? <em>默认</em> : null}
                          {custom && id !== row.default_model ? (
                            <button type="button" className="ws2-m-link mk" onClick={(e) => {
                              e.preventDefault();
                              void act(() => updateProfile(row.id, { default_model: id }, row.configuration_version ?? null), `默认模型改为 ${id}。`);
                            }}>设为默认</button>
                          ) : null}
                        </label>
                      </li>
                    ))}
                  </ul>
                  {kept?.length ? <button type="button" className="ws2-m-link" onClick={() => setShelf(row.id, null)}>全部显示</button> : null}
                </>
              ) : null}
            </div>
          ) : null}

          <div className="ws2-m-foot">
            {note ? <p className={`ws2-m-note${note.bad ? ' is-bad' : ''}`} role="status">{note.text}</p> : <span />}
            {custom && row.id !== 'custom' ? (
              <span className="ws2-m-acts">
                <button type="button" className="ws2-m-link is-danger" onClick={() => setAsking('remove')} disabled={active && locked}>移除这个接入</button>
              </span>
            ) : null}
          </div>
        </div>
      ) : null}
      {asking ? (
        <OnSheet anchor={anchor}>
          <ConfirmDialog
            title={asking === 'remove' ? `移除「${row.display_name}」？` : `清除 ${row.display_name} 的密钥？`}
            confirmLabel={asking === 'remove' ? '移除' : '清除密钥'}
            danger
            busy={busy}
            onConfirm={() => {
              void act(() => (asking === 'remove' ? removeProfile(row.id) : clearKey(row.id))).then(() => setAsking(null));
            }}
            onCancel={() => setAsking(null)}
          >
            <p>{asking === 'remove' ? '它会从列表和模型菜单里消失；用过它的对话还在，换个服务就能接着聊。' : '密钥从这台电脑上删掉。之后要用它，需要重新粘贴密钥。'}</p>
            {active ? <p className="ws2-dialog-hint">它正在用；之后发消息前要先换一个服务。</p> : null}
          </ConfirmDialog>
        </OnSheet>
      ) : null}
    </li>
  );
}

const hostOf = (url: string) => { try { return new URL(url).host; } catch { return url; } };

/* ---------- the whole list ---------- */

/** A short tag after the name when the entry is not a plain "paste the key". */
function entryTag(entry: CatalogEntry): { text: string; tone: 'ok' | 'warn' | 'off' } | null {
  if (entry.waiting) return { text: '暂时接不了', tone: 'off' };
  if (entry.plan?.fit === 'coding') return { text: '面向编程', tone: 'warn' };
  if (entry.plan?.fit === 'ok') return { text: '可在这里用', tone: 'ok' };
  return null;
}

function CatalogList({ taken, initial, onPick }: { taken: Set<string | undefined>; initial: CatalogGroup | 'all'; onPick: (entry: CatalogEntry) => void }) {
  const [query, setQuery] = useState('');
  const [only, setOnly] = useState<CatalogGroup | 'all'>(initial);
  const searching = Boolean(query.trim());
  const found = searchCatalog(query);
  // A search looks through every group; the chips narrow the list while nothing is typed.
  const groups = GROUPS
    .filter((group) => searching || only === 'all' || group === only)
    .map((group) => ({ group, entries: found.filter((entry) => entry.group === group) }))
    .filter((g) => g.entries.length);
  return (
    <div className="ws2-cx-catalog">
      <div className="head">
        <input type="search" className="ws2-cx-field ws2-cx-search" placeholder="搜索服务商，比如 kimi、千问、claude、coding plan" aria-label="搜索服务商"
          value={query} onChange={(e) => setQuery(e.target.value)} data-autofocus />
        <span className="ws2-cx-regions ws2-cx-only" role="radiogroup" aria-label="只看">
          {(['all', ...GROUPS] as const).map((group) => (
            <button key={group} type="button" role="radio" aria-checked={!searching && only === group} className={!searching && only === group ? 'on' : undefined}
              onClick={() => { setOnly(group); setQuery(''); }}>
              {group === 'all' ? '全部' : GROUP_SHORT[group]}
            </button>
          ))}
        </span>
      </div>
      <div className="groups">
        {groups.map(({ group, entries }) => (
          <section key={group} aria-label={GROUP_LABEL[group]}>
            <h4>{GROUP_LABEL[group]}</h4>
            <ul>
              {entries.map((entry) => {
                const tag = entryTag(entry);
                const open = canConnect(entry);
                return (
                  <li key={entry.id}>
                    <button type="button" className={`ws2-cx-entry${open ? '' : ' is-closed'}`} onClick={() => onPick(entry)}>
                      <ProviderMark provider={{ id: entry.builtin ?? entry.id, display_name: entry.name, base_url: baseUrlOf(entry) }} />
                      <span className="txt">
                        <b><span className="nm">{entry.name}</span>{tag ? <em className={`ws2-cx-fit is-${tag.tone}`}>{tag.text}</em> : null}</b>
                        <small>{entry.blurb}</small>
                      </span>
                      <span className={`go${taken.has(entry.id) || !open ? ' is-taken' : ''}`} aria-hidden="true">
                        {!open ? '说明' : taken.has(entry.id) ? '已接入' : '接入'}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
        {!groups.length ? <p className="ws2-m-note">没找到「{query}」。它多半也支持 OpenAI 兼容接口，可以用「其他 OpenAI 兼容服务」接入。</p> : null}
        <button type="button" className="ws2-cx-entry is-custom" onClick={() => onPick(CUSTOM_ENTRY)}>
          <ProviderMark provider={{ id: 'custom', display_name: '+' }} />
          <span className="txt"><b>{CUSTOM_ENTRY.name}</b><small>{CUSTOM_ENTRY.blurb}</small></span>
          <span className="go" aria-hidden="true">接入</span>
        </button>
      </div>
    </div>
  );
}

/* ---------- connecting one ---------- */

type StepState = { state: 'idle' | 'doing' | 'done' | 'failed'; note?: string };
const STEPS = [
  { key: 'save', label: '保存密钥' },
  { key: 'list', label: '读取模型列表' },
  { key: 'check', label: '用所选模型试说一句' },
] as const;
type StepKey = typeof STEPS[number]['key'];

/** For an entry that cannot take a key yet: why, and what to use instead. */
function ClosedSheet({ anchor, entry, onClose, onSwitch }: {
  anchor: React.RefObject<HTMLDivElement | null>;
  entry: CatalogEntry;
  onClose: () => void;
  onSwitch: (entry: CatalogEntry) => void;
}) {
  const instead = catalogEntry(entry.instead);
  return (
    <Sheet anchor={anchor} title={entry.name} sub={entry.blurb} onClose={onClose}
      mark={<ProviderMark provider={{ id: entry.id, display_name: entry.name, base_url: baseUrlOf(entry) }} />}>
      <div className="ws2-cx-connect">
        <div className="ws2-m-flag is-bad" role="note">
          <b>现在还接不了</b>
          <span>{entry.waiting}</span>
          {entry.plan ? <span>{entry.plan.note}</span> : null}
        </div>
        {entry.plan ? (
          <p className="ws2-cx-terms">
            <button type="button" className="ws2-m-link" onClick={() => { void openExternal(entry.plan!.url).catch(() => undefined); }}>看 {hostOf(entry.plan.url)} 上的原文 ↗</button>
          </p>
        ) : null}
        {instead ? (
          <p className="ws2-m-note">想现在就用，可以先接：{instead.name}。</p>
        ) : null}
        <div className="ws2-cx-actions">
          <button type="button" className="ws2-m-link" onClick={onClose}>关闭</button>
          {instead ? <button type="button" className="ws2-m-btn" onClick={() => onSwitch(instead)} data-autofocus>改接 {instead.name}</button> : null}
        </div>
      </div>
    </Sheet>
  );
}

function ConnectSheet(props: ConnectionsPanelProps & {
  anchor: React.RefObject<HTMLDivElement | null>;
  entry: CatalogEntry;
  existing?: ProviderMetadata;
  onClose: () => void;
  onSwitch: (entry: CatalogEntry) => void;
}) {
  if (!canConnect(props.entry)) return <ClosedSheet anchor={props.anchor} entry={props.entry} onClose={props.onClose} onSwitch={props.onSwitch} />;
  return <KeySheet {...props} />;
}

function KeySheet({ anchor, entry, existing, providers, onClose, onCheck, onAdmit, onReload, onConfigChanged, onUse, locked }: ConnectionsPanelProps & {
  anchor: React.RefObject<HTMLDivElement | null>;
  entry: CatalogEntry;
  existing?: ProviderMetadata;
  onClose: () => void;
}) {
  const custom = !entry.builtin;
  const [key, setKey] = useState('');
  const [region, setRegion] = useState(0);
  const [baseUrl, setBaseUrl] = useState(baseUrlOf(entry, 0));
  const [name, setName] = useState(entry.id === 'custom' ? '' : entry.name);
  const [advanced, setAdvanced] = useState(entry.id === 'custom');
  const [steps, setSteps] = useState<Record<StepKey, StepState>>({ save: { state: 'idle' }, list: { state: 'idle' }, check: { state: 'idle' } });
  const [failure, setFailure] = useState<{ title: string; fix: string } | null>(null);
  const [models, setModels] = useState<string[] | null>(null);
  const [model, setModel] = useState('');
  const [phase, setPhase] = useState<'form' | 'working' | 'pick' | 'done'>('form');
  // The connection this window is working on; `made` when it did not exist before (so leaving early removes it).
  const profile = useRef<{ id: string; version: string | null; made: boolean } | null>(null);
  const admitted = useRef(false);

  useEffect(() => { if (entry.regions) setBaseUrl(baseUrlOf(entry, region)); }, [entry, region]);

  const mark = (step: StepKey, state: StepState) => setSteps((current) => ({ ...current, [step]: state }));
  const providerId = () => entry.builtin ?? profile.current?.id ?? '';
  const needKey = !entry.noKey && !(entry.builtin && existing?.configured && !key);

  /**
   * Steps 1 and 2 in one call: the backend keeps the key (making the connection, or reusing the one at this address
   * with this label) and reads the list. A failure leaves nothing behind and says which step it was.
   */
  const start = async () => {
    setFailure(null);
    setPhase('working');
    setSteps({ save: { state: 'doing' }, list: { state: 'idle' }, check: { state: 'idle' } });
    try {
      const url = baseUrl.trim();
      const got = await connect(
        entry.builtin
          ? { providerId: entry.builtin }
          : { baseUrl: url, name: name.trim() || hostOf(url), catalogId: entry.id === 'custom' ? undefined : entry.id },
        key.trim(),
      );
      if (custom) {
        profile.current = { id: got.provider_id, version: got.configuration_version ?? null, made: !providers.some((row) => row.id === got.provider_id) };
      }
      onConfigChanged(got.provider_id);
      // An existing connection that only got a new key keeps its model and its place in the menu.
      const rekeyed = got.check.code === 'not_checked';
      mark('save', { state: 'done', note: rekeyed ? '密钥已换，原来的模型不变' : key.trim() ? '只存在这台电脑上' : entry.noKey ? '本机服务，不需要密钥' : '沿用已保存的密钥' });
      const chat = got.models_listed === false ? null : chatModels(got.models);
      setModels(chat);
      setModel((current) => current || got.model_id || (chat ? suggestModel(entry, chat) : existing?.default_model ?? ''));
      mark('list', { state: 'done', note: chat ? `${chat.length} 个对话模型` : '这家不提供列表，请手动填写模型名' });
      setPhase('pick');
    } catch (problem) {
      const step: StepKey = problem instanceof ConnectFailure && problem.step ? problem.step : 'save';
      if (step !== 'save') mark('save', { state: 'done' });
      mark(step, { state: 'failed' });
      setFailure(connectError(problem, step));
      setPhase('form');
    }
  };

  /** Step 3: say one sentence with the chosen model; on success it joins the menu. */
  const finish = async () => {
    const id = providerId();
    const chosen = model.trim();
    if (!chosen) return;
    setFailure(null);
    setPhase('working');
    mark('check', { state: 'doing' });
    try {
      if (custom && profile.current) {
        const set = await updateProfile(profile.current.id, { default_model: chosen }, profile.current.version);
        profile.current = { ...profile.current, version: set.configuration_version ?? profile.current.version };
        onConfigChanged(id);
      }
      const result = await onCheck(id, chosen);
      if (result?.status !== 'ok') throw new ApiError(0, result?.status === 'failed' ? result.code : 'configuration_changed');
      if (custom && profile.current) await onAdmit(id, chosen, profile.current.version);
      admitted.current = true;
      await onReload();
      mark('check', { state: 'done', note: `${chosen} 回复正常` });
      setPhase('done');
    } catch (problem) {
      mark('check', { state: 'failed' });
      setFailure(connectError(problem, 'check'));
      setPhase('pick');
    }
  };

  const close = () => {
    // A connection made here that never passed stays out of the way: it is removed, not left pending.
    if (profile.current?.made && !admitted.current) {
      void removeProfile(profile.current.id).catch(() => undefined).then(() => onReload());
    }
    onClose();
  };

  const busy = phase === 'working';
  return (
    <Sheet anchor={anchor} title={entry.id === 'custom' ? '接入 OpenAI 兼容服务' : `接入 ${entry.name}`} sub={entry.blurb} onClose={busy ? undefined : close}
      mark={<ProviderMark provider={{ id: entry.builtin ?? entry.id, display_name: entry.name || '+', base_url: baseUrlOf(entry) }} />}>
      <form className="ws2-cx-connect" onSubmit={(e) => { e.preventDefault(); if (phase === 'form') void start(); else if (phase === 'pick') void finish(); }}>
        {phase === 'done' ? null : (
          <fieldset disabled={busy || phase === 'pick'}>
            {entry.id === 'custom' ? (
              <label className="ws2-cx-labelled"><span>名称</span>
                <input className="ws2-cx-field" value={name} onChange={(e) => setName(e.target.value)} placeholder="给它起个名字，比如「中转站」" data-autofocus />
              </label>
            ) : null}
            {entry.regions ? (
              <div className="ws2-cx-labelled"><span>区域</span>
                <span className="ws2-cx-regions" role="radiogroup" aria-label="区域">
                  {entry.regions.map((r, i) => (
                    <button key={r.label} type="button" role="radio" aria-checked={region === i} className={region === i ? 'on' : undefined} onClick={() => setRegion(i)}>{r.label}</button>
                  ))}
                </span>
              </div>
            ) : null}
            {entry.plan ? (
              <p className={`ws2-m-note ws2-cx-plan is-${entry.plan.fit}`}>
                {entry.plan.note}
                <button type="button" className="ws2-m-link" onClick={() => { void openExternal(entry.plan!.url).catch(() => undefined); }}>原文 ↗</button>
              </p>
            ) : null}
            {entry.noKey ? (
              <p className="ws2-m-note">{entry.name} 在这台电脑上运行，不需要密钥。先确认它已经打开，并且下载了至少一个模型。</p>
            ) : (
              <label className="ws2-cx-labelled"><span>API 密钥</span>
                <input type="password" className="ws2-cx-field" value={key} onChange={(e) => setKey(e.target.value)} autoComplete="new-password" spellCheck={false}
                  placeholder={entry.builtin && existing?.configured ? '已保存过密钥 · 留空就沿用' : entry.plan ? '粘贴套餐的专属密钥' : '粘贴密钥，通常以 sk- 开头'} data-autofocus={entry.id !== 'custom' || undefined} />
              </label>
            )}
            {entry.keyUrl && !entry.noKey ? (
              <p className="ws2-cx-get">
                还没有密钥？<button type="button" className="ws2-m-link" onClick={() => { void openExternal(entry.keyUrl!).catch(() => undefined); }}>到 {hostOf(entry.keyUrl)} 创建一个 ↗</button>
                <small>{entry.keyHint ?? '注册、充值或领取免费额度后，在「API Keys」里新建，复制回来粘贴。'}</small>
              </p>
            ) : entry.keyUrl && entry.noKey ? (
              <p className="ws2-cx-get">还没装？<button type="button" className="ws2-m-link" onClick={() => { void openExternal(entry.keyUrl!).catch(() => undefined); }}>下载 {entry.name} ↗</button></p>
            ) : null}
            {custom ? (
              advanced ? (
                <label className="ws2-cx-labelled"><span>服务地址</span>
                  <input type="url" className="ws2-cx-field ws2-path" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} spellCheck={false}
                    placeholder="https://example.com/v1" />
                  <small>服务商文档里的 Base URL，通常以 /v1 结尾，不要带 /chat/completions。</small>
                </label>
              ) : (
                <p className="ws2-cx-addr">地址 <span className="ws2-path">{baseUrl}</span> <button type="button" className="ws2-m-link" onClick={() => setAdvanced(true)}>改</button></p>
              )
            ) : null}
          </fieldset>
        )}

        <ol className="ws2-cx-steps" aria-label="接入进度">
          {STEPS.map(({ key: k, label }) => (
            <li key={k} className={`is-${steps[k].state}`}>
              <i aria-hidden="true" />
              <span>{k === 'save' && entry.noKey ? '保存服务地址' : label}</span>
              {steps[k].note ? <small>{steps[k].note}</small> : null}
            </li>
          ))}
        </ol>

        {phase === 'pick' || (phase === 'working' && models !== null) || steps.check.state !== 'idle' ? (
          phase === 'done' ? null : (
            <label className="ws2-cx-labelled"><span>模型</span>
              {models?.length ? (
                <select className="ws2-cx-field" value={model} onChange={(e) => setModel(e.target.value)} disabled={busy}>
                  {models.map((id) => <option key={id} value={id}>{id}{id === suggestModel(entry, models) ? '（推荐）' : ''}</option>)}
                </select>
              ) : (
                <input className="ws2-cx-field ws2-path" value={model} onChange={(e) => setModel(e.target.value)} placeholder="模型名，比如 deepseek-chat" disabled={busy} data-autofocus />
              )}
              <small>之后可以在对话的模型菜单里随时换。</small>
            </label>
          )
        ) : null}

        {failure ? (
          <div className="ws2-m-flag is-bad" role="alert"><b>{failure.title}</b><span>{failure.fix}</span></div>
        ) : null}
        {phase === 'done' ? (
          <p className="ws2-m-flag" role="status">已接入。{entry.name || name} 出现在对话的模型菜单里了。</p>
        ) : null}

        <div className="ws2-cx-actions">
          {phase === 'done' ? (
            <>
              <button type="button" className="ws2-m-link" onClick={onClose}>完成</button>
              <button type="button" className="ws2-m-btn" disabled={locked} onClick={() => { onUse(providerId()); onClose(); }}>现在就用它</button>
            </>
          ) : (
            <>
              <button type="button" className="ws2-m-link" onClick={close} disabled={busy}>取消</button>
              {phase === 'pick' || (busy && steps.check.state === 'doing') ? (
                <button type="submit" className="ws2-m-btn" disabled={busy || !model.trim()}>{busy ? '正在试…' : '试一下并接入'}</button>
              ) : (
                <button type="submit" className="ws2-m-btn" disabled={busy || (needKey && !key.trim()) || (custom && !baseUrl.trim())}>{busy ? '正在接…' : '下一步'}</button>
              )}
            </>
          )}
        </div>
      </form>
    </Sheet>
  );
}

/* ---------- search ---------- */

function SearchGroup({ active, providers, onSelect, onSaveKey, onClear }: ConnectionsPanelProps['search']) {
  const ids = (Object.keys(providers) as WebSearchProviderId[]).length ? (Object.keys(providers) as WebSearchProviderId[]) : (['tavily', 'firecrawl'] as WebSearchProviderId[]);
  const info = providers[active];
  const [key, setKey] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ bad?: boolean; text: string } | null>(null);
  const run = async (work: () => Promise<void>, done?: string) => {
    setBusy(true);
    setNote(null);
    try { await work(); if (done) setNote({ text: done }); return true; }
    catch { setNote({ bad: true, text: '没有成功，稍后再试。' }); return false; }
    finally { setBusy(false); }
  };
  return (
    <section className="ws2-m-sec" aria-labelledby="ws2-cx-search">
      <div className="ws2-m-group" id="ws2-cx-search">联网搜索</div>
      <p className="ws2-m-note">她需要查资料时用这里的搜索服务。不配也能聊天，只是不会上网查。</p>
      <div className="ws2-m-row">
        <div className="ws2-m-label"><b>搜索服务</b><small>{info?.configured ? '密钥已保存' : '还没有密钥'}</small></div>
        <span className="ws2-cx-regions" role="radiogroup" aria-label="搜索服务">
          {ids.map((id) => (
            <button key={id} type="button" role="radio" aria-checked={active === id} className={active === id ? 'on' : undefined}
              disabled={busy} onClick={() => { if (id !== active) void run(() => onSelect(id)); }}>
              {providers[id]?.label ?? id}{providers[id]?.configured ? ' ·' : ''}
            </button>
          ))}
        </span>
      </div>
      <div className="ws2-m-row">
        <div className="ws2-m-label"><b>{info?.label ?? active} 密钥</b><small>{info?.configured ? '已保存 · 不回显' : '粘贴后保存'}</small></div>
        {key === null ? (
          <span className="ws2-m-acts">
            {info?.docs_url ? <button type="button" className="ws2-m-link" onClick={() => { void openExternal(info.docs_url!).catch(() => undefined); }}>去获取 ↗</button> : null}
            {info?.configured ? <button type="button" className="ws2-m-link is-danger" disabled={busy} onClick={() => { void run(() => onClear(active), '密钥已清除。'); }}>清除</button> : null}
            <button type="button" className="ws2-m-btn" onClick={() => setKey('')}>{info?.configured ? '更换' : '填写'}</button>
          </span>
        ) : (
          <form className="ws2-cx-inline" onSubmit={(e) => {
            e.preventDefault();
            const value = key.trim();
            if (value) void run(() => onSaveKey(active, value), '密钥已保存。').then((ok) => { if (ok) setKey(null); });
          }}>
            <input type="password" className="ws2-cx-field" value={key} autoFocus autoComplete="new-password" spellCheck={false}
              placeholder={`粘贴 ${info?.label ?? active} 密钥`} aria-label={`${info?.label ?? active} 密钥`} data-testid="web-search-credential-input"
              onChange={(e) => setKey(e.target.value)} onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); e.preventDefault(); setKey(null); } }} />
            <button type="button" className="ws2-m-link" onClick={() => setKey(null)}>取消</button>
            <button type="submit" className="ws2-m-btn" disabled={busy || !key.trim()}>{busy ? '保存中…' : '保存'}</button>
          </form>
        )}
      </div>
      {note ? <p className={`ws2-m-note${note.bad ? ' is-bad' : ''}`} role="status">{note.text}</p> : null}
    </section>
  );
}

/* ---------- a small window on the sheet ---------- */

function OnSheet({ anchor, children }: { anchor: React.RefObject<HTMLElement | null>; children: ReactNode }) {
  const root = anchor.current?.closest<HTMLElement>('.ws2');
  return root ? createPortal(children, root) : <>{children}</>;
}

function Sheet({ anchor, title, sub, mark, wide, onClose, children }: {
  anchor: React.RefObject<HTMLElement | null>;
  title: string;
  sub?: string;
  mark?: ReactNode;
  wide?: boolean;
  /** Absent while it is busy: Esc and the scrim do nothing. */
  onClose?: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const close = useRef(onClose);
  close.current = onClose;
  const opener = useMemo(() => (document.activeElement instanceof HTMLElement ? document.activeElement : null), []);
  useLayoutEffect(() => {
    const first = ref.current?.querySelector<HTMLElement>('[data-autofocus]') ?? focusableTarget([ref.current?.querySelector<HTMLElement>('input, select, button')]);
    (first ?? ref.current)?.focus({ preventScroll: true });
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.isComposing) return;
      event.preventDefault();
      event.stopPropagation();
      close.current?.();
    };
    window.addEventListener('keydown', onKey, true);
    return () => {
      window.removeEventListener('keydown', onKey, true);
      if (opener?.isConnected) opener.focus({ preventScroll: true });
    };
  }, [opener]);
  return (
    <OnSheet anchor={anchor}>
      <div className="ws2-dialog-scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) close.current?.(); }}>
        <div className={`ws2-dialog ws2-sheet${wide ? ' is-wide' : ''}`} ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby={titleId}>
          <header className="ws2-sheet-head">
            {mark}
            <span className="txt"><h3 id={titleId}>{title}</h3>{sub ? <small>{sub}</small> : null}</span>
            {onClose ? <button type="button" className="ws2-sheet-x" aria-label="关闭" onClick={onClose}>×</button> : null}
          </header>
          <div className="ws2-sheet-body">{children}</div>
        </div>
      </div>
    </OnSheet>
  );
}
