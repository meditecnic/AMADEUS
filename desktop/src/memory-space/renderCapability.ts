export type RenderSurface = '3d' | 'list';

export type CapabilityReason =
  | 'healthy'
  | 'create-fail'
  | 'context-loss'
  | 'low-fps';

export type DecorationLevel = 'full' | 'reduced';

export type CapabilityState = {
  surface: RenderSurface;
  reason: CapabilityReason;
  sticky: boolean;
  contextLosses: number;
  decoration: DecorationLevel;
  retrying: boolean;
  decorationReducedAt: number | null;
};

export const HEALTHY_CAPABILITY: CapabilityState = {
  surface: '3d',
  reason: 'healthy',
  sticky: false,
  contextLosses: 0,
  decoration: 'full',
  retrying: false,
  decorationReducedAt: null,
};

export function applyCreateFailure(state: CapabilityState): CapabilityState {
  return {
    ...state,
    surface: 'list',
    reason: 'create-fail',
    sticky: true,
    decoration: 'reduced',
    retrying: false,
  };
}

export function applyContextLoss(state: CapabilityState): CapabilityState {
  const losses = state.contextLosses + 1;
  if (losses === 1) {
    return {
      ...state,
      contextLosses: losses,
      reason: 'context-loss',
      retrying: true,
      surface: '3d',
    };
  }
  return {
    ...state,
    contextLosses: losses,
    surface: 'list',
    reason: 'context-loss',
    sticky: true,
    decoration: 'reduced',
    retrying: false,
  };
}

export function applyRetry3d(state: CapabilityState, canCreate: boolean): CapabilityState {
  if (!canCreate) {
    return {
      ...state,
      surface: 'list',
      reason: state.reason === 'healthy' ? 'create-fail' : state.reason,
      sticky: true,
      retrying: false,
      decoration: 'reduced',
    };
  }
  return {
    ...state,
    surface: '3d',
    reason: 'healthy',
    sticky: false,
    retrying: false,
    decoration: 'full',
    decorationReducedAt: null,
  };
}

export type FrameSample = {
  at: number;
  visible: boolean;
  warmup: boolean;
};

/**
 * P1-B (round 4): deterministic visibility seam. rAF is system-throttled
 * when the document is hidden AND when the window is unfocused; neither
 * state may count toward the low-FPS judgment.
 */
export function perfSampleVisible(visibilityState: string, hasFocus: boolean): boolean {
  return visibilityState === 'visible' && hasFocus;
}

export function averageFps(samples: readonly FrameSample[], now: number, windowMs = 5000): number | null {
  const start = now - windowMs;
  const windowed = samples.filter((sample) => sample.at >= start && sample.at <= now && sample.visible && !sample.warmup);
  if (windowed.length < 2) return null;
  const elapsed = windowed[windowed.length - 1].at - windowed[0].at;
  if (elapsed <= 0) return null;
  return ((windowed.length - 1) * 1000) / elapsed;
}

export function applyPerformanceSample(
  state: CapabilityState,
  samples: readonly FrameSample[],
  now: number,
): CapabilityState {
  const fps = averageFps(samples, now);
  if (fps === null) return state;
  if (fps < 40) {
    if (state.decoration === 'full') {
      return { ...state, decoration: 'reduced', decorationReducedAt: now };
    }
    if (state.decorationReducedAt !== null && now - state.decorationReducedAt < 5000) {
      return state;
    }
    return {
      ...state,
      surface: 'list',
      reason: 'low-fps',
      sticky: true,
      decoration: 'reduced',
    };
  }
  return state;
}

export function ambientMotionEnabled(reducedMotion: boolean, surface: RenderSurface): boolean {
  return surface === '3d' && !reducedMotion;
}

export const FRAME_BUFFER_LIMIT = 240;

export type FrameAccumulation = {
  state: CapabilityState;
  buffer: FrameSample[];
};

/**
 * P1 (round 5): one sample applied against the rolling buffer. The buffer
 * itself is part of the FPS judgment window, so a visibility/focus recovery
 * MUST clear it together with the stage warmup; otherwise stale visible
 * low-fps samples demote the surface the moment recovery samples (all
 * warmup) arrive.
 */
export function applyFrameSample(
  state: CapabilityState,
  buffer: readonly FrameSample[],
  sample: FrameSample,
  limit: number = FRAME_BUFFER_LIMIT,
): FrameAccumulation {
  const trimmed = buffer.length >= limit ? buffer.slice(buffer.length - limit + 1) : buffer.slice();
  const nextBuffer = [...trimmed, sample];
  return {
    state: applyPerformanceSample(state, nextBuffer, sample.at),
    buffer: nextBuffer,
  };
}

export function reducedMotionChangesSurface(): false {
  return false;
}
