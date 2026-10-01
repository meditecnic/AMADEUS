import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SalieriSelect } from './SalieriSelect';

afterEach(cleanup);

const options = [
  { value: 'deepseek', label: 'DeepSeek' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'custom', label: 'Custom', disabled: true },
  { value: 'glm', label: 'GLM' },
];

describe('SalieriSelect', () => {
  it('opens a listbox portal and commits a click selection', () => {
    const onChange = vi.fn();
    render(
      <SalieriSelect
        aria-label="模型供应商"
        value="deepseek"
        options={options}
        onChange={onChange}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '模型供应商' }));
    const listbox = screen.getByRole('listbox', { name: '模型供应商' });
    expect(listbox).toBeTruthy();
    fireEvent.click(screen.getByRole('option', { name: 'OpenAI' }));
    expect(onChange).toHaveBeenCalledWith('openai');
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('portals menu to document.body with portal markers (dark semantic tokens host)', () => {
    render(
      <div className="model-bottom-bar">
        <SalieriSelect
          aria-label="模型供应商"
          value="deepseek"
          options={options}
          onChange={vi.fn()}
        />
      </div>,
    );

    fireEvent.click(screen.getByRole('button', { name: '模型供应商' }));
    const listbox = screen.getByRole('listbox', { name: '模型供应商' });
    expect(listbox.parentElement).toBe(document.body);
    expect(listbox).toHaveAttribute('data-salieri-portal', 'true');
    expect(listbox.className).toMatch(/salieri-select-menu--portal/);
    expect(listbox.className).toMatch(/salieri-select-menu/);
    // Menu is not a descendant of composer chrome (portal contract).
    expect(document.querySelector('.model-bottom-bar')?.contains(listbox)).toBe(false);
  });

  it('supports arrow navigation, enter commit, escape close, and activedescendant', () => {
    const onChange = vi.fn();
    render(
      <SalieriSelect
        aria-label="模型"
        value="deepseek"
        options={options}
        onChange={onChange}
      />,
    );

    const trigger = screen.getByRole('button', { name: '模型' });
    fireEvent.keyDown(trigger, { key: 'ArrowDown' });
    const listbox = screen.getByRole('listbox', { name: '模型' });
    const activeId = listbox.getAttribute('aria-activedescendant');
    expect(activeId).toBeTruthy();
    expect(document.getElementById(activeId!)).toHaveAttribute('aria-selected', 'true');

    fireEvent.keyDown(listbox, { key: 'ArrowDown' });
    const nextId = listbox.getAttribute('aria-activedescendant');
    expect(nextId).toBeTruthy();
    expect(nextId).not.toBe(activeId);
    expect(document.getElementById(nextId!)).toHaveTextContent('OpenAI');

    fireEvent.keyDown(listbox, { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith('openai');
    expect(screen.queryByRole('listbox')).toBeNull();

    fireEvent.keyDown(trigger, { key: 'ArrowDown' });
    expect(screen.getByRole('listbox')).toBeTruthy();
    fireEvent.keyDown(screen.getByRole('listbox'), { key: 'Escape' });
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('does not select disabled options and skips them with arrow keys', () => {
    const onChange = vi.fn();
    render(
      <SalieriSelect
        aria-label="供应商"
        value="openai"
        options={options}
        onChange={onChange}
      />,
    );
    const trigger = screen.getByRole('button', { name: '供应商' });
    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole('option', { name: 'Custom' }));
    expect(onChange).not.toHaveBeenCalled();

    fireEvent.keyDown(screen.getByRole('listbox'), { key: 'ArrowDown' });
    // skip custom (disabled) from openai -> glm
    fireEvent.keyDown(screen.getByRole('listbox'), { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith('glm');
  });

  it('Home/End jump to first/last enabled options', () => {
    const onChange = vi.fn();
    render(
      <SalieriSelect
        aria-label="列表"
        value="openai"
        options={options}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '列表' }));
    const listbox = screen.getByRole('listbox');
    fireEvent.keyDown(listbox, { key: 'End' });
    fireEvent.keyDown(listbox, { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith('glm');
  });

  it('does not open when disabled or options empty', () => {
    const { rerender } = render(
      <SalieriSelect
        aria-label="禁用"
        value=""
        options={options}
        onChange={vi.fn()}
        disabled
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '禁用' }));
    expect(screen.queryByRole('listbox')).toBeNull();

    rerender(
      <SalieriSelect
        aria-label="空"
        value=""
        options={[]}
        onChange={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '空' }));
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('closes on outside mousedown without committing', () => {
    const onChange = vi.fn();
    render(
      <div>
        <button type="button">outside</button>
        <SalieriSelect
          aria-label="外部点击"
          value="deepseek"
          options={options}
          onChange={onChange}
        />
      </div>,
    );
    fireEvent.click(screen.getByRole('button', { name: '外部点击' }));
    expect(screen.getByRole('listbox')).toBeTruthy();
    fireEvent.mouseDown(screen.getByRole('button', { name: 'outside' }));
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
  });

  it('renders optional triggerPrefix as compact prefixed label', () => {
    render(
      <SalieriSelect
        aria-label="推理强度"
        value="max"
        triggerPrefix="推理强度 · "
        options={[
          { value: 'high', label: 'HIGH' },
          { value: 'max', label: 'MAX' },
        ]}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: '推理强度' })).toHaveTextContent(
      '推理强度 · MAX',
    );
  });
});
