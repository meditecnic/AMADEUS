import { Children, isValidElement, useCallback, useState, type ReactNode } from 'react';
import * as Select from '@radix-ui/react-select';

/** Keep the popup inside Memory Space so worldline tokens and focus boundaries apply. */
export function MemorySelect({ value, onValueChange, children, ...props }: {
  value: string;
  onValueChange: (value: string) => void;
  children: ReactNode;
  'aria-label': string;
  'data-testid'?: string;
  disabled?: boolean;
}) {
  const [container, setContainer] = useState<HTMLElement | null>(null);
  const anchor = useCallback((node: HTMLButtonElement | null) => {
    if (node) setContainer(node.closest<HTMLElement>('.memory-space') ?? node.parentElement);
  }, []);
  const options = Children.toArray(children).filter(isValidElement<{ value: string; children: ReactNode }>);
  return (
    <Select.Root value={`value:${value}`} onValueChange={(next) => onValueChange(next.slice(6))} disabled={props.disabled}>
      <Select.Trigger {...props} value={value} className="memory-select-trigger" ref={anchor} onKeyDown={(event) => event.stopPropagation()}>
        <Select.Value />
        <Select.Icon className="memory-select-chevron" aria-hidden="true">
          <svg width="12" height="12" viewBox="0 0 12 12"><path d="m3 4.5 3 3 3-3" fill="none" stroke="currentColor" strokeWidth="1.4" /></svg>
        </Select.Icon>
      </Select.Trigger>
      <Select.Portal container={container}>
        <Select.Content className="memory-select-popup" position="popper" sideOffset={6} collisionPadding={12}
          onEscapeKeyDown={(event) => event.stopPropagation()} onKeyDown={(event) => event.stopPropagation()}>
          <Select.ScrollUpButton className="memory-select-scroll">▴</Select.ScrollUpButton>
          <Select.Viewport className="memory-select-viewport">
            {options.map((option) => (
              <Select.Item className="memory-select-option" data-value={option.props.value} key={option.props.value} value={`value:${option.props.value}`}>
                <Select.ItemText>{option.props.children}</Select.ItemText>
                <Select.ItemIndicator className="memory-select-check">✓</Select.ItemIndicator>
              </Select.Item>
            ))}
          </Select.Viewport>
          <Select.ScrollDownButton className="memory-select-scroll">▾</Select.ScrollDownButton>
        </Select.Content>
      </Select.Portal>
    </Select.Root>
  );
}
