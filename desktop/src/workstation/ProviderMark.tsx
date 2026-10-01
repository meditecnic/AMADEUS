import anthropic from '@lobehub/icons-static-svg/icons/anthropic.svg?raw';
import azure from '@lobehub/icons-static-svg/icons/azure.svg?raw';
import baichuan from '@lobehub/icons-static-svg/icons/baichuan.svg?raw';
import cohere from '@lobehub/icons-static-svg/icons/cohere.svg?raw';
import deepseek from '@lobehub/icons-static-svg/icons/deepseek.svg?raw';
import doubao from '@lobehub/icons-static-svg/icons/doubao.svg?raw';
import fireworks from '@lobehub/icons-static-svg/icons/fireworks.svg?raw';
import gemini from '@lobehub/icons-static-svg/icons/gemini.svg?raw';
import groq from '@lobehub/icons-static-svg/icons/groq.svg?raw';
import hunyuan from '@lobehub/icons-static-svg/icons/hunyuan.svg?raw';
import lmstudio from '@lobehub/icons-static-svg/icons/lmstudio.svg?raw';
import minimax from '@lobehub/icons-static-svg/icons/minimax.svg?raw';
import mistral from '@lobehub/icons-static-svg/icons/mistral.svg?raw';
import moonshot from '@lobehub/icons-static-svg/icons/moonshot.svg?raw';
import ollama from '@lobehub/icons-static-svg/icons/ollama.svg?raw';
import openai from '@lobehub/icons-static-svg/icons/openai.svg?raw';
import openrouter from '@lobehub/icons-static-svg/icons/openrouter.svg?raw';
import perplexity from '@lobehub/icons-static-svg/icons/perplexity.svg?raw';
import qwen from '@lobehub/icons-static-svg/icons/qwen.svg?raw';
import siliconcloud from '@lobehub/icons-static-svg/icons/siliconcloud.svg?raw';
import stepfun from '@lobehub/icons-static-svg/icons/stepfun.svg?raw';
import together from '@lobehub/icons-static-svg/icons/together.svg?raw';
import xai from '@lobehub/icons-static-svg/icons/xai.svg?raw';
import yi from '@lobehub/icons-static-svg/icons/yi.svg?raw';
import zhipu from '@lobehub/icons-static-svg/icons/zhipu.svg?raw';

/* Monochrome marks from Lobe Icons (MIT); they inherit `currentColor`, so the worldline decides the tint. */
const ICONS: Record<string, string> = {
  anthropic, azure, baichuan, cohere, deepseek, doubao, fireworks, gemini, groq, hunyuan, lmstudio, minimax,
  mistral, moonshot, ollama, openai, openrouter, perplexity, qwen, siliconcloud, stepfun, together, xai, yi, zhipu,
};

const BUILTIN: Record<string, string> = { deepseek: 'deepseek', glm: 'zhipu', openai: 'openai', gemini: 'gemini' };

const HOSTS: Array<[RegExp, string]> = [
  [/(^|\.)deepseek\.com$/, 'deepseek'],
  [/(^|\.)(bigmodel\.cn|z\.ai)$/, 'zhipu'],
  [/(^|\.)openai\.com$/, 'openai'],
  [/(^|\.)anthropic\.com$/, 'anthropic'],
  [/(^|\.)moonshot\.(cn|ai)$/, 'moonshot'],
  [/(^|\.)dashscope(-intl)?\.aliyuncs\.com$/, 'qwen'],
  [/(^|\.)siliconflow\.(cn|com)$/, 'siliconcloud'],
  [/(^|\.)openrouter\.ai$/, 'openrouter'],
  [/(^|\.)generativelanguage\.googleapis\.com$/, 'gemini'],
  [/(^|\.)x\.ai$/, 'xai'],
  [/(^|\.)mistral\.ai$/, 'mistral'],
  [/(^|\.)groq\.com$/, 'groq'],
  [/(^|\.)volces\.com$/, 'doubao'],
  [/(^|\.)(minimaxi?\.com|minimax\.chat)$/, 'minimax'],
  [/(^|\.)stepfun\.com$/, 'stepfun'],
  [/(^|\.)lingyiwanwu\.com$/, 'yi'],
  [/(^|\.)hunyuan\.cloud\.tencent\.com$/, 'hunyuan'],
  [/(^|\.)together\.(xyz|ai)$/, 'together'],
  [/(^|\.)fireworks\.ai$/, 'fireworks'],
  [/(^|\.)perplexity\.ai$/, 'perplexity'],
  [/(^|\.)cohere\.(com|ai)$/, 'cohere'],
  [/(^|\.)baichuan-ai\.com$/, 'baichuan'],
  [/(^|\.)azure\.com$/, 'azure'],
];

/* Local servers are recognised by their conventional ports only. */
const LOCAL_PORTS: Record<string, string> = { '11434': 'ollama', '1234': 'lmstudio' };

export interface ProviderMarkSource {
  id: string;
  display_name?: string;
  base_url?: string | null;
}

export function providerVendor(source: ProviderMarkSource): string | null {
  const builtin = BUILTIN[source.id];
  if (builtin) return builtin;
  if (!source.base_url) return null;
  let url: URL;
  try {
    url = new URL(source.base_url);
  } catch {
    return null;
  }
  const host = url.hostname.toLowerCase();
  for (const [pattern, vendor] of HOSTS) {
    if (pattern.test(host)) return vendor;
  }
  if (host === 'localhost' || host === '127.0.0.1' || host === '::1' || host === '[::1]') {
    return LOCAL_PORTS[url.port] ?? null;
  }
  return null;
}

export function ProviderMark({ provider, className = '' }: { provider: ProviderMarkSource; className?: string }) {
  const vendor = providerVendor(provider);
  const svg = vendor ? ICONS[vendor] : undefined;
  if (svg) {
    return (
      <span
        className={`ws2-pmark ${className}`}
        data-vendor={vendor}
        aria-hidden="true"
        // Static, bundled SVG from a pinned package; never user input.
        dangerouslySetInnerHTML={{ __html: svg }}
      />
    );
  }
  const initial = Array.from((provider.display_name || provider.id).trim())[0]?.toUpperCase() ?? '?';
  return <span className={`ws2-pmark is-initial ${className}`} aria-hidden="true">{initial}</span>;
}
