import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createMemoryStore, handleMemory } from '../dev-mock/memory';
import { buildChartModel } from './chartCoordinates';
import { md } from './chartFormat';
import { LIST_MAX, MemoryReading, type MemoryReadingProps } from './MemoryReading';
import type { MemoryChart } from './types';

afterEach(cleanup);

const chart = handleMemory(new URL('http://mock/api/memory/chart?session_id=s&worldline=steins_gate&identity_mode=okabe'), 'GET', {},
  createMemoryStore({ facts: [], topics: [], preset: 'huge' }))!.body as MemoryChart;
const model = buildChartModel(chart, { today: Date.parse('2026-11-04T04:00:00Z'), lastOpen: null });

function reading(overrides: Partial<MemoryReadingProps>) {
  const props: MemoryReadingProps = {
    selection: null, model, chart, details: { type: 'idle' }, width: 1440, height: 840,
    onSelect: vi.fn(), onClose: vi.fn(), onRetryDetails: vi.fn(), onOpenArchive: vi.fn(), onOpenArchiveTopic: vi.fn(),
    onOpenArchiveWithin: vi.fn(), ...overrides,
  };
  render(<MemoryReading {...props} />);
  return props;
}
const cellWith = (min: number, max = Infinity) => [...model.cells.values()]
  .find((c) => c.starIds.length >= min && c.starIds.length <= max && model.groups.find((t) => t.id === c.groupId)!.isTopic)!;

describe('MemoryReading: the cell', () => {
  it('names the topic, the month and the frame cell, newest 12 first, with "还有 N 条"', () => {
    const cell = cellWith(14);
    const group = model.groups.find((t) => t.id === cell.groupId)!;
    const props = reading({ selection: { kind: 'cell', key: cell.key } });
    expect(screen.getByTestId('reading-kick')).toHaveTextContent('格子');
    expect(screen.getByTestId('reading-cell-title').textContent).toMatch(new RegExp(`^${group.label} · ${cell.band.key.replace('-', '\\.')} · [A-F][1-8]$`));
    const items = within(screen.getByTestId('reading-list')).getAllByRole('button');
    expect(items).toHaveLength(LIST_MAX);
    const newest = [...cell.starIds].reverse().slice(0, LIST_MAX).map((id) => model.byId.get(id)!);
    expect(items.map((el) => el.textContent)).toEqual(newest.map((s) => `${s.label}${md(s.createdAt)}`));
    expect(screen.getByTestId('reading-rest')).toHaveTextContent(`还有 ${cell.starIds.length - LIST_MAX} 条`);
    fireEvent.click(items[0]);
    expect(props.onSelect).toHaveBeenCalledWith({ kind: 'star', id: newest[0].id });
  });

  it('sends the archive the whole cell and the topic', () => {
    const cell = cellWith(14);
    const props = reading({ selection: { kind: 'cell', key: cell.key } });
    const button = screen.getByTestId('open-cell-in-archive');
    expect(button).toHaveTextContent(`在档案中看这一格的全部 ${cell.starIds.length} 条 ↗`);
    fireEvent.click(button);
    const group = model.groups.find((t) => t.id === cell.groupId)!;
    expect(props.onOpenArchiveWithin).toHaveBeenCalledWith(cell.starIds, `${group.label} · ${cell.band.key.replace('-', '.')}`, group.id);
  });

  it('says "这 N 条" for a small cell and has no "还有" line', () => {
    const cell = cellWith(3, 8);
    reading({ selection: { kind: 'cell', key: cell.key } });
    expect(screen.getByTestId('open-cell-in-archive')).toHaveTextContent(`在档案中看这 ${cell.starIds.length} 条 ↗`);
    expect(screen.queryByTestId('reading-rest')).toBeNull();
  });

  it('opens a cell of the ungrouped group without a topic filter', () => {
    const cell = [...model.cells.values()].find((c) => c.groupId === '@loose' && c.starIds.length > 1)!;
    const props = reading({ selection: { kind: 'cell', key: cell.key } });
    fireEvent.click(screen.getByTestId('open-cell-in-archive'));
    expect(props.onOpenArchiveWithin).toHaveBeenCalledWith(cell.starIds, `未归组 · ${cell.band.key.replace('-', '.')}`, null);
  });

  it('shows nothing for a cell that is gone', () => {
    reading({ selection: { kind: 'cell', key: 'gone|2020-01' } });
    expect(screen.queryByTestId('reading-dom')).toBeNull();
  });
});

describe('MemoryReading: topic and month lists', () => {
  it('lists a topic newest first, at most 12, and draws a bar for every month it spans', () => {
    const group = model.groups.filter((t) => t.isTopic).sort((a, b) => b.starIds.length - a.starIds.length)[0];
    reading({ selection: { kind: 'topic', id: group.id } });
    const items = within(screen.getByTestId('reading-list')).getAllByRole('button');
    expect(items).toHaveLength(LIST_MAX);
    const newest = model.byId.get(group.starIds[group.starIds.length - 1])!;
    expect(items[0]).toHaveTextContent(newest.label);
    expect(screen.getByTestId('reading-rest')).toHaveTextContent(`还有 ${group.starIds.length - LIST_MAX} 条`);
    const bars = [...screen.getByTestId('reading-months').querySelectorAll('i')];
    const first = model.bands.findIndex((b) => b.key === group.cells[0].band.key);
    const last = model.bands.findIndex((b) => b.key === group.cells[group.cells.length - 1].band.key);
    expect(bars).toHaveLength(last - first + 1);
    expect(bars.reduce((n, el) => n + Number(el.getAttribute('data-count')), 0)).toBe(group.starIds.length);
    expect(screen.getByTestId('reading-months')).toHaveAccessibleName(/^每月几条：\d{4}\.\d{2} 到 \d{4}\.\d{2}，最多 \d+ 条$/);
    expect(screen.getByTestId('open-topic-in-archive')).toBeInTheDocument();
  });

  it('reads the ungrouped group like a topic but without the topic link', () => {
    reading({ selection: { kind: 'topic', id: '@loose' } });
    expect(screen.getByTestId('reading-months')).toBeInTheDocument();
    expect(screen.queryByTestId('open-topic-in-archive')).toBeNull();
  });

  it('lists a busy month newest 12 and links its archive; a quiet month has no link', () => {
    const busy = [...model.bands].sort((a, b) => b.starIds.length - a.starIds.length)[0];
    const props = reading({ selection: { kind: 'month', key: busy.key } });
    expect(within(screen.getByTestId('reading-list')).getAllByRole('button')).toHaveLength(LIST_MAX);
    expect(screen.getByTestId('reading-rest')).toHaveTextContent(`还有 ${busy.starIds.length - LIST_MAX} 条`);
    fireEvent.click(screen.getByTestId('open-month-in-archive'));
    const [ids, label, topic] = (props.onOpenArchiveWithin as ReturnType<typeof vi.fn>).mock.calls[0];
    expect([...ids].sort()).toEqual([...busy.starIds].sort());
    expect(label).toBe(busy.key.replace('-', '.'));
    expect(topic).toBeNull();
    cleanup();
    const quiet = [...model.bands].reverse().find((b) => b.starIds.length > 0 && b.starIds.length <= LIST_MAX);
    if (quiet) {
      reading({ selection: { kind: 'month', key: quiet.key } });
      expect(screen.queryByTestId('open-month-in-archive')).toBeNull();
      expect(screen.queryByTestId('reading-rest')).toBeNull();
    }
  });
});
