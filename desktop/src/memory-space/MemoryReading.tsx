import { useLayoutEffect, useRef, type ReactNode } from 'react';

import { plateGeometry, platePoint, zoneOf, type ChartModel, type ChartStar } from './chartCoordinates';
import { hm, md, recordNo, ymd } from './chartFormat';
import type { ChartSelection } from './MemoryPlate';
import { NOT_FOUND_NOTICE, type DetailsOutcome } from './readingDetails';
import type { MemoryChart } from './types';
import './MemoryReading.css';

type Said = { text: string | null; erased: boolean; at: number | null };

const KIND_ZH = { fact: '认知', loose: '认知', fragment: '片段' } as const;

function stamp(at: number | null): string { return at === null ? '' : `${ymd(at)} ${hm(at)}`; }

// Your own words come first. A corrected cognition leads with the line that made
// it change (the active version's source); otherwise the earliest mention.
export function saidLines(outcome: DetailsOutcome): Said[] {
  if (outcome.type !== 'ok') return [];
  const payload = outcome.payload;
  const rows = [...payload.provenance].map((row) => ({
    version: 'version_no' in row ? row.version_no : 1,
    said: {
      text: row.source_state === 'deleted' ? null : row.excerpt,
      erased: row.source_state === 'deleted',
      at: row.source_created_at ? Date.parse(row.source_created_at) : null,
    },
  })).sort((a, b) => (a.said.at ?? Infinity) - (b.said.at ?? Infinity));
  if (!rows.length) return [];
  let lead = 0;
  if ('fact' in payload && payload.versions.length > 1) {
    const hit = rows.findIndex((row) => row.version === payload.fact.active_version);
    if (hit >= 0) lead = hit;
  }
  return [rows[lead].said, ...rows.filter((_, i) => i !== lead).map((row) => row.said)];
}

function Quote({ said, small }: { said: Said; small?: boolean }) {
  if (said.erased) return <p className="r-missing" data-testid="reading-source-erased">原始消息已删除，仅保留来源记录</p>;
  if (!said.text) return <p className="r-missing" data-testid="reading-source-excerpt-missing">这条来源记录暂未提供摘录</p>;
  return <p className={`r-quote${small ? ' small' : ''}`} data-testid="reading-quote">{said.text}</p>;
}

export function zoneOfStar(model: ChartModel, star: ChartStar, width: number, height: number): string {
  const g = plateGeometry(width, height);
  const p = platePoint(star.theta, star.phi, g.radius, model.thetaFit);
  return zoneOf(g.cx + p.x, g.cy + p.y, width, height);
}

function StarReading(props: {
  star: ChartStar; model: ChartModel; details: DetailsOutcome; zone: string;
  onRetry: () => void; onOpenArchive: (kind: 'fact' | 'experience', id: string) => void;
}) {
  const { star, model, details } = props;
  const topic = star.topicId ? model.topics.find((t) => t.id === star.topicId) : null;
  const slot = topic ? topic.label : star.kind === 'loose' ? '未归组' : null;
  const kick = [recordNo(star.no), KIND_ZH[star.kind], slot].filter(Boolean).join(' · ');
  const lines = saidLines(details);
  const payload = details.type === 'ok' ? details.payload : null;
  const herText = payload ? ('fact' in payload ? payload.fact.display_text : payload.experience.display_text) : null;
  const version = payload && 'fact' in payload && payload.versions.length > 1
    ? { count: payload.versions.length, active: payload.fact.active_version } : null;
  return (
    <>
      <p className="r-kick" data-testid="reading-kick">{kick} · <b>{props.zone}</b></p>
      {details.type === 'loading' ? <p className="r-wait" data-testid="reading-evidence-loading">正在读取来源与版本…</p> : null}
      {details.type === 'not-found' ? <p className="r-wait" data-testid="reading-evidence-missing">{NOT_FOUND_NOTICE}</p> : null}
      {details.type === 'backend-failure' || details.type === 'transport-error' ? (
        <p className="r-wait" data-testid="reading-evidence-error">
          原话和版本暂时读不出来。
          <button type="button" className="r-link" data-testid="reading-evidence-retry" onClick={props.onRetry}>重试</button>
        </p>
      ) : null}
      {payload ? (
        <>
          {lines.length ? (
            <>
              <Quote said={lines[0]} />
              {lines[0].at !== null ? <p className="r-said"><b>你说</b>{stamp(lines[0].at)}</p> : null}
              {lines.length > 1 ? (
                <details className="r-more" data-testid="reading-more">
                  <summary>你还提过 {lines.length - 1} 次 ▾</summary>
                  {lines.slice(1).map((said, i) => (
                    <div key={i}><Quote said={said} small />{said.at !== null ? <p className="r-said">{stamp(said.at)}</p> : null}</div>
                  ))}
                </details>
              ) : null}
            </>
          ) : <p className="r-missing" data-testid="reading-sources-empty">未提供可显示的来源</p>}
          <div className="r-her"><div className="k">她记成</div><p data-testid="reading-her">{herText}</p></div>
          <dl className="r-facts" data-testid="reading-facts">
            <dt>第一次记下</dt><dd>{ymd(star.createdAt)}</dd>
            {star.kind === 'fragment' ? (
              star.pinned || star.expiresAt !== null
                ? <><dt>时效</dt><dd className="zh">{star.pinned ? '已置顶，不会淡去' : `${md(star.expiresAt!)} 后淡去`}</dd></>
                : null
            ) : <><dt>你提过</dt><dd>{star.evidenceCount} 次</dd></>}
            {version ? <><dt>版本</dt><dd className="zh">改过 {version.count - 1} 次 · 现在是第 {version.active} 版</dd></> : null}
            {star.pinned ? <><dt>置顶</dt><dd className="zh">是</dd></> : null}
          </dl>
        </>
      ) : null}
      <button type="button" className="r-open" data-testid="open-in-archive"
        onClick={() => props.onOpenArchive(star.kind === 'fragment' ? 'experience' : 'fact', star.id)}>在档案中打开 ↗</button>
    </>
  );
}

function StarList({ stars, onSelect }: { stars: ChartStar[]; onSelect: (selection: ChartSelection) => void }) {
  return (
    <ul className="r-list">
      {stars.map((s) => (
        <li key={s.id}>
          <button type="button" onClick={() => onSelect({ kind: 'star', id: s.id })}>
            <span>{s.label}</span><time>{md(s.createdAt)}</time>
          </button>
        </li>
      ))}
    </ul>
  );
}

export type MemoryReadingProps = {
  selection: ChartSelection;
  model: ChartModel;
  chart: MemoryChart;
  details: DetailsOutcome;
  width: number;
  height: number;
  onSelect: (selection: ChartSelection) => void;
  onClose: () => void;
  onRetryDetails: () => void;
  onOpenArchive: (kind: 'fact' | 'experience', id: string) => void;
  onOpenArchiveTopic: (topicId: string) => void;
};

export function MemoryReading(props: MemoryReadingProps) {
  const { selection, model, chart } = props;
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const key = selection ? JSON.stringify(selection) : '';
  useLayoutEffect(() => { if (bodyRef.current) bodyRef.current.scrollTop = 0; }, [key]);
  if (!selection) return null;

  let content: ReactNode = null;
  if (selection.kind === 'star') {
    const star = model.byId.get(selection.id);
    if (!star) return null;
    content = <StarReading star={star} model={model} details={props.details} zone={zoneOfStar(model, star, props.width, props.height)}
      onRetry={props.onRetryDetails} onOpenArchive={props.onOpenArchive} />;
  } else if (selection.kind === 'topic') {
    const topic = model.topics.find((t) => t.id === selection.id);
    if (!topic || !topic.starIds.length) return null;
    const stars = topic.starIds.map((id) => model.byId.get(id)!);
    content = (
      <>
        <p className="r-kick" data-testid="reading-kick">主题 · <b>{zoneOfStar(model, stars[0], props.width, props.height)}</b></p>
        <p className="r-title">{topic.label}</p>
        <p className="r-sub">{stars.length} 条 · {md(stars[0].createdAt)} 起 · 最近 {md(stars[stars.length - 1].createdAt)}</p>
        <StarList stars={stars} onSelect={props.onSelect} />
        <button type="button" className="r-open" data-testid="open-topic-in-archive" onClick={() => props.onOpenArchiveTopic(topic.id)}>在档案中查看这个主题 ↗</button>
      </>
    );
  } else if (selection.kind === 'month') {
    const [y, m] = selection.key.split('-').map(Number);
    const inMonth = (at: number) => { const d = new Date(at); return d.getFullYear() === y && d.getMonth() + 1 === m; };
    const stars = model.stars.filter((s) => inMonth(s.createdAt));
    const fresh = model.topics.filter((t) => t.starIds.length && inMonth(model.byId.get(t.starIds[0])!.createdAt));
    content = (
      <>
        <p className="r-kick" data-testid="reading-kick">{y}.{String(m).padStart(2, '0')}</p>
        <p className="r-title">{m} 月</p>
        <p className="r-sub" data-testid="reading-month-summary">
          新记 {stars.filter((s) => s.kind !== 'fragment').length} 件事 · 片段 {stars.filter((s) => s.kind === 'fragment').length} · 新主题 {fresh.length}
          {fresh.length ? `（${fresh.map((t) => t.label).join('、')}）` : ''}
        </p>
        <StarList stars={stars} onSelect={props.onSelect} />
      </>
    );
  } else {
    const changed = [...chart.facts, ...chart.experiences].map((row) => Date.parse(row.updated_at)).sort((a, b) => b - a)[0];
    content = (
      <>
        <p className="r-kick" data-testid="reading-kick">SOUL</p>
        <p className="r-title soul">SOUL</p>
        <p className="r-note">SOUL 是她的连续性，不是一条记忆。从中心往外，是你们第一次对话以来的每一个月。</p>
        <dl className="r-facts" data-testid="soul-facts">
          <dt>起点</dt><dd>{model.origin !== null ? `${ymd(model.origin)}　第一次对话` : '--'}</dd>
          <dt>认知</dt><dd>{chart.counts.facts}</dd>
          <dt>片段</dt><dd>{chart.counts.experiences}</dd>
          <dt>主题</dt><dd>{chart.counts.topics}</dd>
          <dt>最近变更</dt><dd>{changed ? stamp(changed) : '--'}</dd>
        </dl>
      </>
    );
  }

  return (
    <aside className="m-reading" data-testid="reading-dom" aria-label="阅读面" aria-live="polite">
      <button type="button" className="r-x" aria-label="关闭阅读" onClick={props.onClose}>×</button>
      <div className="r-body" key={key} ref={bodyRef}>{content}</div>
    </aside>
  );
}
