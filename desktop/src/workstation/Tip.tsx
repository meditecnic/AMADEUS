import { createContext, useContext, useRef, useState, type FocusEvent, type ReactElement, type ReactNode } from 'react';
import * as Tooltip from '@radix-ui/react-tooltip';

/** Where workstation overlays (tips, menus) mount: inside the .ws2 root so they inherit the worldline palette. */
export const OverlayRootContext = createContext<HTMLElement | null>(null);
export const useOverlayRoot = () => useContext(OverlayRootContext) ?? undefined;

const TipScope = createContext(false);

/** One provider per surface keeps the timing shared: the first tip waits, neighbours within 600ms open at once. */
export function TipProvider({ children }: { children: ReactNode }) {
  return (
    <Tooltip.Provider delayDuration={560} skipDelayDuration={600}>
      <TipScope.Provider value={true}>{children}</TipScope.Provider>
    </Tooltip.Provider>
  );
}

function isTruncated(el: Element | null | undefined): boolean {
  return Boolean(el && (el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1));
}

/** Keyboard focus shows the tip; focus handed back by the app after a click (a menu closing, a field finishing)
 *  does not, just as :focus-visible would not draw a ring there. A prevented event skips Radix's own handler. */
function skipQuietFocus(event: FocusEvent<HTMLElement>) {
  let visible = true;
  try { visible = event.currentTarget.matches(':focus-visible'); } catch { /* selector unsupported: show */ }
  if (!visible) event.preventDefault();
}

/** Replaces the native title tooltip. The trigger keeps its own accessible name; Radix links the tip as its description.
 *  `truncated` limits the tip to the moment its text is actually cut off (it then repeats the full text):
 *  `true` checks the trigger itself, a selector checks that element inside the trigger. */
export function Tip({ label, children, side = 'bottom', align = 'center', sideOffset = 7, truncated, className }: {
  label: ReactNode;
  children: ReactElement;
  side?: 'top' | 'right' | 'bottom' | 'left';
  align?: 'start' | 'center' | 'end';
  sideOffset?: number;
  truncated?: true | string;
  className?: string;
}) {
  const scoped = useContext(TipScope);
  const container = useOverlayRoot();
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const cutOff = () => {
    const el = triggerRef.current;
    return isTruncated(truncated === true ? el : el?.querySelector(truncated as string));
  };
  if (label === null || label === undefined || label === '' || label === false) return children;
  const root = (
    <Tooltip.Root
      open={open}
      onOpenChange={(next) => setOpen(next && (!truncated || cutOff()))}
    >
      <Tooltip.Trigger asChild ref={triggerRef} onFocus={skipQuietFocus}>{children}</Tooltip.Trigger>
      <Tooltip.Portal container={container}>
        <Tooltip.Content
          className={`ws2-tip${className ? ` ${className}` : ''}`}
          side={side}
          align={align}
          sideOffset={sideOffset}
          collisionPadding={10}
        >
          {label}
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
  // Standalone use (unit tests, surfaces outside the workstation) still works, just without the shared timing.
  return scoped ? root : <TipProvider>{root}</TipProvider>;
}
