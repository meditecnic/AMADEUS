export type RenderSurface = '3d' | 'compatibility';

export type CapabilityReason =
  | 'healthy'
  | 'create-fail'
  | 'context-loss'
  | 'low-fps'
  | 'explicit';

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
    surface: 'compatibility',
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
    surface: 'compatibility',
    reason: 'context-loss',
    sticky: true,
    decoration: 'reduced',
    retrying: false,
  };
}

export function applyExplicitCompatibility(state: CapabilityState, on: boolean): CapabilityState {
  if (state.sticky && state.reason !== 'explicit') {
    return on ? { ...state, surface: 'compatibility' } : state;
  }
  return {
    ...state,
    surface: on ? 'compatibility' : '3d',
    reason: on ? 'explicit' : 'healthy',
    sticky: false,
    retrying: false,
    decoration: on ? 'reduced' : 'full',
  };
}

export function applyRetry3d(state: CapabilityState, canCreate: boolean): CapabilityState {
  if (!canCreate) {
    return {
      ...state,
      surface: 'compatibility',
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
      surface: 'compatibility',
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

export function reducedMotionChangesSurface(): false {
  return false;
}
