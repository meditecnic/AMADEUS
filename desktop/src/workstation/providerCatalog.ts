/*
 * Who AMADEUS can talk through, for someone who has never set one up (U 11-09: 做得更多更全，对小白友好). Each entry
 * says what the service is in one line, where to get a key, and the address to use, so connecting is "paste the key".
 *
 * The four built-in providers have their own adapters in the backend; everything else speaks the OpenAI-compatible
 * protocol and is connected as a custom profile pointed at its address. Addresses follow the open models.dev catalog
 * (MIT, github.com/anomalyco/models.dev). Until the backend serves that catalog itself (amadeus-ops hand-off,
 * 2026-11-09), this short list is what the page offers; "其他 OpenAI 兼容服务" covers anything not on it.
 */

export type CatalogGroup = 'cn' | 'global' | 'router' | 'local';

export const GROUP_LABEL: Record<CatalogGroup, string> = {
  cn: '国内服务商',
  global: '海外服务商',
  router: '聚合平台（一个密钥，多家模型）',
  local: '本机运行（不需要密钥）',
};

export type Region = { label: string; baseUrl: string };

export type CatalogEntry = {
  id: string;
  name: string;
  /** One line on what it is. */
  blurb: string;
  group: CatalogGroup;
  /** A backend built-in provider id: connecting saves its key instead of creating a profile. */
  builtin?: 'deepseek' | 'glm' | 'openai' | 'gemini';
  /** The OpenAI-compatible address; the first region's when there are several. */
  baseUrl?: string;
  regions?: Region[];
  /** Where to make a key (opened in the browser). */
  keyUrl?: string;
  /** No key: a service running on this computer. */
  noKey?: boolean;
  /** Shown first on the page. */
  popular?: boolean;
  /** Model ids to prefer when the list comes back, best first (matched as substrings). */
  prefer?: string[];
  /** Searched as well as the name. */
  aliases?: string;
};

export const CATALOG: CatalogEntry[] = [
  // built in
  { id: 'deepseek', builtin: 'deepseek', name: 'DeepSeek', blurb: '深度求索。中文好，价格低，国内直连。', group: 'cn', popular: true,
    keyUrl: 'https://platform.deepseek.com/api_keys', prefer: ['deepseek-chat'], aliases: '深度求索' },
  { id: 'glm', builtin: 'glm', name: '智谱 GLM', blurb: '智谱 AI 开放平台的 GLM 系列，国内直连。', group: 'cn', popular: true,
    keyUrl: 'https://open.bigmodel.cn/usercenter/apikeys', aliases: 'zhipu bigmodel glm 智谱' },
  { id: 'openai', builtin: 'openai', name: 'OpenAI', blurb: 'GPT 系列。需要能访问海外网络。', group: 'global', popular: true,
    keyUrl: 'https://platform.openai.com/api-keys', aliases: 'gpt chatgpt' },
  { id: 'gemini', builtin: 'gemini', name: 'Google Gemini', blurb: 'Google 的 Gemini 系列，AI Studio 可免费申请密钥。', group: 'global', popular: true,
    keyUrl: 'https://aistudio.google.com/apikey', aliases: 'google gemini' },

  // China
  { id: 'moonshot', name: 'Kimi（月之暗面）', blurb: 'Moonshot AI 的 Kimi 系列，长文本见长。', group: 'cn', popular: true,
    regions: [{ label: '国内', baseUrl: 'https://api.moonshot.cn/v1' }, { label: '海外', baseUrl: 'https://api.moonshot.ai/v1' }],
    keyUrl: 'https://platform.moonshot.cn/console/api-keys', prefer: ['kimi-k2', 'kimi-latest', 'moonshot-v1-32k'], aliases: 'moonshot kimi 月之暗面' },
  { id: 'dashscope', name: '通义千问（阿里云百炼）', blurb: '阿里云百炼平台的 Qwen 系列。', group: 'cn', popular: true,
    regions: [{ label: '国内', baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1' }, { label: '国际', baseUrl: 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1' }],
    keyUrl: 'https://bailian.console.aliyun.com/', prefer: ['qwen-plus', 'qwen-max', 'qwen3'], aliases: 'qwen aliyun alibaba bailian 阿里 百炼 千问' },
  { id: 'siliconflow', name: '硅基流动', blurb: '一个密钥用多家开源模型（DeepSeek、Qwen、GLM 等），有免费额度。', group: 'cn', popular: true,
    regions: [{ label: '国内', baseUrl: 'https://api.siliconflow.cn/v1' }, { label: '海外', baseUrl: 'https://api.siliconflow.com/v1' }],
    keyUrl: 'https://cloud.siliconflow.cn/account/ak', prefer: ['deepseek-ai/DeepSeek-V3', 'Qwen/Qwen3'], aliases: 'siliconflow 硅基' },
  { id: 'volcengine', name: '火山方舟（豆包）', blurb: '字节跳动火山引擎的豆包与第三方模型。', group: 'cn',
    baseUrl: 'https://ark.cn-beijing.volces.com/api/v3', keyUrl: 'https://console.volcengine.com/ark', prefer: ['doubao'], aliases: 'volcengine ark doubao 豆包 字节 火山' },
  { id: 'stepfun', name: '阶跃星辰', blurb: 'StepFun 的 Step 系列。', group: 'cn',
    regions: [{ label: '国内', baseUrl: 'https://api.stepfun.com/v1' }, { label: '海外', baseUrl: 'https://api.stepfun.ai/v1' }],
    keyUrl: 'https://platform.stepfun.com/', aliases: 'stepfun step 阶跃' },
  { id: 'modelscope', name: '魔搭 ModelScope', blurb: '阿里魔搭社区的推理服务，开源模型多。', group: 'cn',
    baseUrl: 'https://api-inference.modelscope.cn/v1', keyUrl: 'https://modelscope.cn/my/myaccesstoken', aliases: 'modelscope 魔搭' },
  { id: 'qiniu', name: '七牛云 AI', blurb: '七牛云的大模型推理服务。', group: 'cn',
    baseUrl: 'https://api.qnaigc.com/v1', keyUrl: 'https://portal.qiniu.com/', aliases: 'qiniu 七牛' },

  // global
  { id: 'zai', name: 'Z.AI', blurb: '智谱的海外站，GLM 系列。', group: 'global',
    baseUrl: 'https://api.z.ai/api/paas/v4', keyUrl: 'https://z.ai/', aliases: 'zai glm zhipu' },
  { id: 'xai', name: 'xAI Grok', blurb: 'xAI 的 Grok 系列。', group: 'global',
    baseUrl: 'https://api.x.ai/v1', keyUrl: 'https://console.x.ai/', aliases: 'grok xai' },
  { id: 'mistral', name: 'Mistral', blurb: '法国 Mistral AI 的模型。', group: 'global',
    baseUrl: 'https://api.mistral.ai/v1', keyUrl: 'https://console.mistral.ai/api-keys', aliases: 'mistral' },
  { id: 'groq', name: 'Groq', blurb: '极快的开源模型推理。', group: 'global',
    baseUrl: 'https://api.groq.com/openai/v1', keyUrl: 'https://console.groq.com/keys', aliases: 'groq' },
  { id: 'together', name: 'Together AI', blurb: '大量开源模型的推理服务。', group: 'global',
    baseUrl: 'https://api.together.xyz/v1', keyUrl: 'https://api.together.ai/', aliases: 'together' },
  { id: 'fireworks', name: 'Fireworks AI', blurb: '开源模型推理服务。', group: 'global',
    baseUrl: 'https://api.fireworks.ai/inference/v1', keyUrl: 'https://fireworks.ai/account/api-keys', aliases: 'fireworks' },
  { id: 'deepinfra', name: 'DeepInfra', blurb: '开源模型推理服务，按量计费。', group: 'global',
    baseUrl: 'https://api.deepinfra.com/v1/openai', keyUrl: 'https://deepinfra.com/dash/api_keys', aliases: 'deepinfra' },
  { id: 'nvidia', name: 'NVIDIA NIM', blurb: 'NVIDIA 托管的模型，有试用额度。', group: 'global',
    baseUrl: 'https://integrate.api.nvidia.com/v1', keyUrl: 'https://build.nvidia.com/', aliases: 'nvidia nim' },
  { id: 'huggingface', name: 'Hugging Face', blurb: 'Hugging Face 推理路由。', group: 'global',
    baseUrl: 'https://router.huggingface.co/v1', keyUrl: 'https://huggingface.co/settings/tokens', aliases: 'huggingface hf' },

  // routers
  { id: 'openrouter', name: 'OpenRouter', blurb: '一个密钥用几百个模型，含 Claude、GPT、Gemini。', group: 'router', popular: true,
    baseUrl: 'https://openrouter.ai/api/v1', keyUrl: 'https://openrouter.ai/settings/keys', aliases: 'openrouter claude anthropic' },
  { id: '302ai', name: '302.AI', blurb: '国内可用的聚合平台。', group: 'router',
    baseUrl: 'https://api.302.ai/v1', keyUrl: 'https://302.ai/', aliases: '302' },
  { id: 'novita', name: 'Novita AI', blurb: '开源模型聚合平台。', group: 'router',
    baseUrl: 'https://api.novita.ai/openai', keyUrl: 'https://novita.ai/', aliases: 'novita' },

  // local
  { id: 'ollama', name: 'Ollama', blurb: '在这台电脑上运行模型；先装好 Ollama 并下载一个模型。', group: 'local', noKey: true, popular: true,
    baseUrl: 'http://127.0.0.1:11434/v1', keyUrl: 'https://ollama.com/download', aliases: 'ollama local 本地' },
  { id: 'lmstudio', name: 'LM Studio', blurb: '在这台电脑上运行模型；在 LM Studio 里打开本地服务器。', group: 'local', noKey: true,
    baseUrl: 'http://127.0.0.1:1234/v1', keyUrl: 'https://lmstudio.ai/', aliases: 'lmstudio local 本地' },
];

/** Not in the list: any OpenAI-compatible address. */
export const CUSTOM_ENTRY: CatalogEntry = {
  id: 'custom', name: '其他 OpenAI 兼容服务', blurb: '填服务地址和密钥；中转站、自建服务都在这里接。', group: 'router',
};

export const baseUrlOf = (entry: CatalogEntry, region = 0) => entry.regions?.[region]?.baseUrl ?? entry.baseUrl ?? '';

/** The catalog entry a connected provider row came from (by built-in id, or by its address). */
export function entryFor(row: { id: string; base_url?: string | null }): CatalogEntry | undefined {
  const builtin = CATALOG.find((entry) => entry.builtin === row.id);
  if (builtin) return builtin;
  const address = (row.base_url ?? '').replace(/\/+$/, '').toLowerCase();
  if (!address) return undefined;
  return CATALOG.find((entry) => [entry.baseUrl, ...(entry.regions ?? []).map((r) => r.baseUrl)]
    .some((url) => url && url.replace(/\/+$/, '').toLowerCase() === address));
}

/** Chat models first: drop the obvious embedding, speech, image and moderation ids from a discovered list. */
export function chatModels(ids: string[]): string[] {
  const skip = /(embed|embedding|whisper|tts|speech|audio|dall-e|image|vision-preview|moderation|rerank|transcribe|realtime)/i;
  const kept = ids.filter((id) => !skip.test(id));
  return kept.length ? kept : ids;
}

/** The model to suggest from a discovered list. */
export function suggestModel(entry: CatalogEntry | undefined, ids: string[]): string {
  const list = chatModels(ids);
  for (const want of entry?.prefer ?? []) {
    const hit = list.find((id) => id.toLowerCase().includes(want.toLowerCase()));
    if (hit) return hit;
  }
  return list[0] ?? '';
}

export function searchCatalog(query: string): CatalogEntry[] {
  const q = query.trim().toLowerCase();
  if (!q) return CATALOG;
  return CATALOG.filter((entry) => `${entry.name} ${entry.aliases ?? ''} ${entry.blurb}`.toLowerCase().includes(q));
}
