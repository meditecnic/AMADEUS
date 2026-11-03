import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createMemoryStore, handleMemory } from '../dev-mock/memory';
import { buildChartModel } from './chartCoordinates';
import { hm, md, ymd } from './chartFormat';
import { MemoryReading, saidLines, type MemoryReadingProps } from './MemoryReading';
import type { DetailsOutcome, DetailsPayload, DetailsRequest } from './readingDetails';
import type { MemoryChart } from './types';

afterEach(cleanup);

const store = createMemoryStore({ facts: [], topics: [], preset: 'normal' });
const q = 'session_id=s&worldline=steins_gate&identity_mode=okabe';
const get = (path: string) => handleMemory(new URL(`http://mock/api/memory${path}?${q}`), 'GET', {}, store)!.body;
const chart = get('/chart') as MemoryChart;
const model = buildChartModel(chart, { today: Date.parse('2026-11-02T14:10:00Z'), lastOpen: null });
const scope = { sessionId: 's', worldline: 'steins_gate' as const, identityMode: 'okabe' as const };
function ok(kind: 'fact' | 'experience', id: string): DetailsOutcome {
  const request: DetailsRequest = { scope, kind, recordId: id };
  return { type: 'ok', request, payload: get(`/${kind === 'fact' ? 'facts' : 'experiences'}/${id}/details`) as DetailsPayload };
}

function reading(overrides: Partial<MemoryReadingProps>) {
  const props: MemoryReadingProps = {
    selection: null, model, chart, details: { type: 'idle' }, width: 1440, height: 840,
    onSelect: vi.fn(), onClose: vi.fn(), onRetryDetails: vi.fn(), onOpenArchive: vi.fn(), onOpenArchiveTopic: vi.fn(), ...overrides,
  };
  render(<MemoryReading {...props} />);
  return props;
}

describe('MemoryReading', () => {
  it('reads a cognition: number line, your words and date, the rest folded, what she made of it, readouts', () => {
    const cat = model.byId.get('f12')!;
    const props = reading({ selection: { kind: 'star', id: 'f12' }, details: ok('fact', 'f12') });
    expect(screen.getByTestId('reading-kick').textContent).toMatch(new RegExp(`^No\\.${String(cat.no).padStart(3, '0')} · 认知 · 家里的猫 · [A-F][1-8]$`));
    expect(screen.getAllByTestId('reading-quote')[0]).toHaveTextContent('我家小黑今天又把杯子推下去了。');
    const said = Date.parse('2026-09-15T19:20:00+08:00');
    expect(document.querySelector('.r-said')).toHaveTextContent(`你说${ymd(said)} ${hm(said)}`);
    expect(screen.getByTestId('reading-more')).toHaveTextContent('你还提过 3 次 ▾');
    expect(screen.getByTestId('reading-her')).toHaveTextContent('你养了一只叫小黑的猫。');
    const facts = screen.getByTestId('reading-facts');
    expect(facts).toHaveTextContent(`第一次记下${ymd(cat.createdAt)}`);
    expect(facts).toHaveTextContent('你提过4 次');
    expect(facts).toHaveTextContent('置顶是');
    fireEvent.click(screen.getByTestId('open-in-archive'));
    expect(props.onOpenArchive).toHaveBeenCalledWith('fact', 'f12');
  });

  it('leads a corrected cognition with the line that changed it', () => {
    const lines = saidLines(ok('fact', 'f07'));
    expect(lines[0].text).toBe('我最近不喝美式了，改喝拿铁。');
    reading({ selection: { kind: 'star', id: 'f07' }, details: ok('fact', 'f07') });
    expect(screen.getByTestId('reading-facts')).toHaveTextContent('改过 1 次 · 现在是第 2 版');
  });

  it('reads a fragment with its fading date, and a pinned fragment as kept', () => {
    reading({ selection: { kind: 'star', id: 'e1' }, details: ok('experience', 'e1') });
    expect(screen.getByTestId('reading-kick').textContent).toMatch(/· 片段 · [A-F][1-8]$/);
    expect(screen.getByTestId('reading-facts')).toHaveTextContent(`时效${md(model.byId.get('e1')!.expiresAt!)} 后淡去`);
    cleanup();
    reading({ selection: { kind: 'star', id: 'e4' }, details: ok('experience', 'e4') });
    expect(screen.getByTestId('reading-facts')).toHaveTextContent('已置顶，不会淡去');
  });

  it('shows only the honest loading and failure lines before the details arrive', () => {
    const request: DetailsRequest = { scope, kind: 'fact', recordId: 'f12' };
    reading({ selection: { kind: 'star', id: 'f12' }, details: { type: 'loading', request } });
    expect(screen.getByTestId('reading-evidence-loading')).toHaveTextContent('正在读取来源与版本…');
    expect(screen.queryByTestId('reading-her')).toBeNull();
    cleanup();
    const props = reading({ selection: { kind: 'star', id: 'f12' }, details: { type: 'transport-error', request } });
    expect(screen.getByTestId('reading-evidence-error')).toHaveTextContent('原话和版本暂时读不出来。');
    fireEvent.click(screen.getByTestId('reading-evidence-retry'));
    expect(props.onRetryDetails).toHaveBeenCalled();
  });

  it('lists a whole topic with its span and links to the archive', () => {
    const props = reading({ selection: { kind: 'topic', id: 't4' } });
    expect(screen.getByText('家里的猫')).toBeInTheDocument();
    const [first, , last] = model.topics.find((t) => t.id === 't4')!.starIds.map((id) => model.byId.get(id)!);
    expect(screen.getByText(`3 条 · ${md(first.createdAt)} 起 · 最近 ${md(last.createdAt)}`)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /小黑三岁了。/ }));
    expect(props.onSelect).toHaveBeenCalledWith({ kind: 'star', id: 'f13' });
    fireEvent.click(screen.getByTestId('open-topic-in-archive'));
    expect(props.onOpenArchiveTopic).toHaveBeenCalledWith('t4');
  });

  it('reviews a month and describes SOUL without diagnostics', () => {
    reading({ selection: { kind: 'month', key: '2026-10' } });
    expect(screen.getByText('10 月')).toBeInTheDocument();
    expect(screen.getByTestId('reading-month-summary').textContent).toMatch(/^新记 \d+ 件事 · 片段 \d+ · 新主题 \d+/);
    cleanup();
    reading({ selection: { kind: 'soul' } });
    expect(screen.getByText('SOUL 是她的连续性，不是一条记忆。从中心往外，是你们第一次对话以来的每一个月。')).toBeInTheDocument();
    const facts = screen.getByTestId('soul-facts');
    expect(facts.textContent).toContain(`起点${ymd(model.origin!)}\u3000第一次对话`);
    expect(facts).toHaveTextContent(`认知${chart.counts.facts}片段${chart.counts.experiences}主题${chart.counts.topics}最近变更`);
    expect(screen.queryByText(/诊断|节点|预算|展开核心|阅读起点/)).toBeNull();
  });
});
