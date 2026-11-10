/*
 * Who AMADEUS can talk through, for someone who has never set one up (U 11-09: 做得更多更全，对小白友好). Each entry
 * says what the service is in one line, where to get a key, and the address to use, so connecting is "paste the key".
 *
 * The four built-in providers have their own adapters in the backend; everything else speaks the OpenAI-compatible
 * protocol and is connected as a custom profile pointed at its address. Addresses follow the open models.dev catalog
 * (MIT, github.com/anomalyco/models.dev) and each service's own docs. Until the backend serves that catalog itself
 * (amadeus-ops hand-off, 2026-11-09), this list is what the page offers; "其他 OpenAI 兼容服务" covers anything not on it.
 *
 * Monthly plans (U 11-10: 用户可能也会用些 token plan) are listed with what their terms say about use outside the
 * vendor's tools, read from the vendor's own pages on 2026-11-10. Plans whose terms limit them to the vendor's listed
 * tools (智谱 GLM、阿里云百炼、火山方舟 Coding Plan) are left out (U 11-10: 条款不允许的就不用写在里面了); their
 * pay-as-you-go entries are in the list.
 */

export type CatalogGroup = 'cn' | 'global' | 'plan' | 'router' | 'local';

export const GROUPS: CatalogGroup[] = ['cn', 'global', 'plan', 'router', 'local'];

export const GROUP_LABEL: Record<CatalogGroup, string> = {
  cn: '国内服务商',
  global: '海外服务商',
  plan: '订阅套餐（按月付费，用套餐自己的密钥）',
  router: '聚合平台（一个密钥，多家模型）',
  local: '本机运行（不需要密钥）',
};

/** The filter chips over the whole list. */
export const GROUP_SHORT: Record<CatalogGroup, string> = { cn: '国内', global: '海外', plan: '套餐', router: '聚合', local: '本机' };

export type Region = { label: string; baseUrl: string };

/** What a plan's terms say about using it in AMADEUS. */
export type PlanTerms = {
  /** ok: allowed in any compatible app. coding: written for coding tools, the terms do not rule chat out. */
  fit: 'ok' | 'coding';
  note: string;
  /** The page that says so. */
  url: string;
};

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
  /** Where the key is found, when it is not the usual "API Keys" page. */
  keyHint?: string;
  /** No key: a service running on this computer. */
  noKey?: boolean;
  /** Shown first on the page. */
  popular?: boolean;
  /** Model ids to prefer when the list comes back, best first (matched as substrings). */
  prefer?: string[];
  /** Searched as well as the name. */
  aliases?: string;
  /** A monthly plan, and what its terms allow. */
  plan?: PlanTerms;
  /** Why AMADEUS cannot connect it yet (the backend is missing something). */
  waiting?: string;
  /** The entry to offer instead when this one cannot be connected. */
  instead?: string;
};

export const CATALOG: CatalogEntry[] = [
  /*
   * Each group runs most used first (OpenRouter's request share by model author, week of 2026-11-02, and the order
   * the popular Chinese desktop clients list them in); small regional hosts sit at the end of their group.
   */

  // China
  { id: 'deepseek', builtin: 'deepseek', name: 'DeepSeek', blurb: '深度求索。中文好，价格低，国内直连。', group: 'cn', popular: true,
    keyUrl: 'https://platform.deepseek.com/api_keys', prefer: ['deepseek-chat'], aliases: '深度求索' },
  { id: 'dashscope', name: '通义千问（阿里云百炼）', blurb: '阿里云百炼平台的 Qwen 系列，按量付费。', group: 'cn', popular: true,
    regions: [{ label: '国内', baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1' }, { label: '国际', baseUrl: 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1' }],
    keyUrl: 'https://bailian.console.aliyun.com/', prefer: ['qwen-plus', 'qwen-max', 'qwen3'], aliases: 'qwen aliyun alibaba bailian 阿里 百炼 千问 通义' },
  { id: 'glm', builtin: 'glm', name: '智谱 GLM', blurb: '智谱 AI 开放平台的 GLM 系列，按量付费，国内直连。', group: 'cn', popular: true,
    keyUrl: 'https://open.bigmodel.cn/usercenter/apikeys', aliases: 'zhipu bigmodel glm 智谱' },
  { id: 'moonshot', name: 'Kimi（月之暗面）', blurb: 'Moonshot AI 开放平台的 Kimi 系列，按量付费，长文本见长。', group: 'cn', popular: true,
    regions: [{ label: '国内', baseUrl: 'https://api.moonshot.cn/v1' }, { label: '海外', baseUrl: 'https://api.moonshot.ai/v1' }],
    keyUrl: 'https://platform.moonshot.cn/console/api-keys', prefer: ['kimi-k3', 'kimi-k2', 'kimi-latest'], aliases: 'moonshot kimi 月之暗面' },
  { id: 'volcengine', name: '火山方舟（豆包）', blurb: '字节跳动火山引擎的豆包与第三方模型，按量付费。', group: 'cn',
    baseUrl: 'https://ark.cn-beijing.volces.com/api/v3', keyUrl: 'https://console.volcengine.com/ark', prefer: ['doubao'], aliases: 'volcengine ark doubao 豆包 字节 火山' },
  { id: 'siliconflow', name: '硅基流动', blurb: '一个密钥用多家开源模型（DeepSeek、Qwen、GLM 等），有免费额度。', group: 'cn', popular: true,
    regions: [{ label: '国内', baseUrl: 'https://api.siliconflow.cn/v1' }, { label: '海外', baseUrl: 'https://api.siliconflow.com/v1' }],
    keyUrl: 'https://cloud.siliconflow.cn/account/ak', prefer: ['deepseek-ai/DeepSeek-V3', 'Qwen/Qwen3'], aliases: 'siliconflow 硅基' },
  { id: 'minimax', name: 'MiniMax', blurb: 'MiniMax 开放平台的 M 系列，按量付费。', group: 'cn',
    regions: [{ label: '国内', baseUrl: 'https://api.minimax.cn/v1' }, { label: '海外', baseUrl: 'https://api.minimax.io/v1' }],
    keyUrl: 'https://platform.minimax.cn/', prefer: ['MiniMax-M3.1-Flash', 'MiniMax-M3'], aliases: 'minimax 海螺 稀宇' },
  { id: 'tokenhub', name: '腾讯混元（TokenHub）', blurb: '腾讯云 TokenHub：混元 Hy 系列，也能用 DeepSeek、GLM、Kimi 等。', group: 'cn',
    regions: [{ label: '广州', baseUrl: 'https://tokenhub.tencentmaas.com/v1' }, { label: '新加坡', baseUrl: 'https://tokenhub-intl.tencentmaas.com/v1' }],
    keyUrl: 'https://console.cloud.tencent.com/', prefer: ['hy4', 'hy3'], aliases: 'tencent hunyuan hy tokenhub 腾讯 混元' },
  { id: 'stepfun', name: '阶跃星辰', blurb: 'StepFun 的 Step 系列。', group: 'cn',
    regions: [{ label: '国内', baseUrl: 'https://api.stepfun.com/v1' }, { label: '海外', baseUrl: 'https://api.stepfun.ai/v1' }],
    keyUrl: 'https://platform.stepfun.com/', aliases: 'stepfun step 阶跃' },
  { id: 'mimo', name: '小米 MiMo', blurb: '小米 MiMo 开放平台，按量付费。', group: 'cn',
    baseUrl: 'https://api.xiaomimimo.com/v1', keyUrl: 'https://mimo.mi.com/', prefer: ['mimo-v2.6-pro', 'mimo'], aliases: 'xiaomi mimo 小米' },
  { id: 'qianfan', name: '百度千帆（文心）', blurb: '百度智能云千帆平台的文心 ERNIE 与第三方模型。', group: 'cn',
    baseUrl: 'https://qianfan.baidubce.com/v2', keyUrl: 'https://console.bce.baidu.com/iam/#/iam/apikey/list', prefer: ['ernie'], aliases: 'baidu qianfan ernie wenxin 百度 千帆 文心' },
  { id: 'modelscope', name: '魔搭 ModelScope', blurb: '阿里魔搭社区的推理服务，开源模型多，有每日免费额度。', group: 'cn',
    baseUrl: 'https://api-inference.modelscope.cn/v1', keyUrl: 'https://modelscope.cn/my/myaccesstoken', aliases: 'modelscope 魔搭' },
  { id: 'qiniu', name: '七牛云 AI', blurb: '七牛云的大模型推理服务。', group: 'cn',
    baseUrl: 'https://api.qnaigc.com/v1', keyUrl: 'https://portal.qiniu.com/', aliases: 'qiniu 七牛' },

  // global
  { id: 'openai', builtin: 'openai', name: 'OpenAI', blurb: 'GPT 系列。需要能访问海外网络。', group: 'global', popular: true,
    keyUrl: 'https://platform.openai.com/api-keys', aliases: 'gpt chatgpt' },
  { id: 'gemini', builtin: 'gemini', name: 'Google Gemini', blurb: 'Google 的 Gemini 系列，AI Studio 可免费申请密钥。', group: 'global', popular: true,
    keyUrl: 'https://aistudio.google.com/apikey', aliases: 'google gemini' },
  { id: 'anthropic', name: 'Anthropic Claude', blurb: 'Claude 系列的官方接口。', group: 'global',
    baseUrl: 'https://api.anthropic.com/v1', keyUrl: 'https://console.anthropic.com/settings/keys', aliases: 'claude anthropic',
    waiting: 'Claude 的官方接口用的是 Anthropic 协议，AMADEUS 还在接。OpenRouter 里也有 Claude。', instead: 'openrouter' },
  { id: 'xai', name: 'xAI Grok', blurb: 'xAI 的 Grok 系列。', group: 'global',
    baseUrl: 'https://api.x.ai/v1', keyUrl: 'https://console.x.ai/', aliases: 'grok xai' },
  { id: 'zai', name: 'Z.AI', blurb: '智谱的海外站，GLM 系列，按量付费。', group: 'global',
    baseUrl: 'https://api.z.ai/api/paas/v4', keyUrl: 'https://z.ai/', aliases: 'zai glm zhipu' },
  { id: 'mistral', name: 'Mistral', blurb: '法国 Mistral AI 的模型。', group: 'global',
    baseUrl: 'https://api.mistral.ai/v1', keyUrl: 'https://console.mistral.ai/api-keys', aliases: 'mistral' },
  { id: 'groq', name: 'Groq', blurb: '极快的开源模型推理，有免费额度。', group: 'global',
    baseUrl: 'https://api.groq.com/openai/v1', keyUrl: 'https://console.groq.com/keys', aliases: 'groq' },
  { id: 'together', name: 'Together AI', blurb: '大量开源模型的推理服务。', group: 'global',
    baseUrl: 'https://api.together.xyz/v1', keyUrl: 'https://api.together.ai/', aliases: 'together' },
  { id: 'fireworks', name: 'Fireworks AI', blurb: '开源模型推理服务。', group: 'global',
    baseUrl: 'https://api.fireworks.ai/inference/v1', keyUrl: 'https://fireworks.ai/account/api-keys', aliases: 'fireworks' },
  { id: 'deepinfra', name: 'DeepInfra', blurb: '开源模型推理服务，按量计费。', group: 'global',
    baseUrl: 'https://api.deepinfra.com/v1/openai', keyUrl: 'https://deepinfra.com/dash/api_keys', aliases: 'deepinfra' },
  { id: 'cerebras', name: 'Cerebras', blurb: '自研芯片上的开源模型推理，出字很快。', group: 'global',
    baseUrl: 'https://api.cerebras.ai/v1', keyUrl: 'https://cloud.cerebras.ai/', aliases: 'cerebras' },
  { id: 'nvidia', name: 'NVIDIA NIM', blurb: 'NVIDIA 托管的模型，有试用额度。', group: 'global',
    baseUrl: 'https://integrate.api.nvidia.com/v1', keyUrl: 'https://build.nvidia.com/', aliases: 'nvidia nim' },
  { id: 'perplexity', name: 'Perplexity', blurb: '回答时自带联网搜索的模型。', group: 'global',
    baseUrl: 'https://api.perplexity.ai', keyUrl: 'https://www.perplexity.ai/settings/api', aliases: 'perplexity sonar' },
  { id: 'huggingface', name: 'Hugging Face', blurb: 'Hugging Face 推理路由。', group: 'global',
    baseUrl: 'https://router.huggingface.co/v1', keyUrl: 'https://huggingface.co/settings/tokens', aliases: 'huggingface hf' },

  // monthly plans: the ones written for any app first
  { id: 'minimax-plan', name: 'MiniMax M Plan', blurb: 'MiniMax 的包月套餐（原 Token Plan），文字、语音、图像共用一份额度。', group: 'plan',
    regions: [{ label: '国内', baseUrl: 'https://api.minimax.cn/v1' }, { label: '海外', baseUrl: 'https://api.minimax.io/v1' }],
    keyUrl: 'https://platform.minimax.cn/', prefer: ['MiniMax-M3.1-Flash', 'MiniMax-M3'], aliases: 'minimax token plan coding plan m plan 套餐 订阅',
    keyHint: '订阅后在「订阅管理 › M Plan」里找订阅 Key（sk-cp- 开头）；它和按量付费的密钥不通用。',
    plan: { fit: 'ok', note: '官方写明可以接入任何 OpenAI 兼容的工具，日常聊天也在用途里。额度按 5 小时和每周刷新。', url: 'https://platform.minimax.io/docs/m-plan/faq' } },
  { id: 'commandcode', name: 'Command Code', blurb: 'Command Code 的订阅。GOAT、Pro、Max 档带 API 用量，30 多个模型。', group: 'plan',
    baseUrl: 'https://api.commandcode.ai/provider/v1', keyUrl: 'https://commandcode.ai/', prefer: ['deepseek/deepseek-v4-flash'], aliases: 'command code goat 套餐 订阅',
    keyHint: '订阅 GOAT 或更高档后，在 Studio 的 API keys 里新建；最低的 Go 档不含 API。',
    plan: { fit: 'ok', note: '官方写明除 Go 档外都能调用 API，从套餐额度里扣。', url: 'https://commandcode.ai/docs/provider' } },
  { id: 'kimi-code', name: 'Kimi Code（Kimi 会员）', blurb: 'Kimi 会员附带的编程额度，Plus 档起可用。', group: 'plan',
    regions: [{ label: '国内', baseUrl: 'https://api.kimi.com/coding/v1' }, { label: '海外', baseUrl: 'https://api.kimi.ai/coding/v1' }],
    keyUrl: 'https://www.kimi.com/code', prefer: ['k3', 'kimi-for-coding'], aliases: 'kimi code moonshot 会员 coding plan 套餐 订阅',
    keyHint: '在 Kimi Code 控制台新建 API Key（最多 5 个，只显示一次）；它和开放平台的密钥不通用。',
    plan: { fit: 'coding', note: '会员权益按编程场景设计，官方允许接第三方工具。在这里聊天同样计入会员额度，受 5 小时频率限制。', url: 'https://www.kimi.com/help/kimi-code/membership-guide' } },
  { id: 'mimo-plan', name: '小米 MiMo Token Plan', blurb: '小米 MiMo 的包月套餐。', group: 'plan',
    regions: [{ label: '国内', baseUrl: 'https://token-plan-cn.xiaomimimo.com/v1' }, { label: '新加坡', baseUrl: 'https://token-plan-sgp.xiaomimimo.com/v1' }, { label: '欧洲', baseUrl: 'https://token-plan-ams.xiaomimimo.com/v1' }],
    keyUrl: 'https://mimo.mi.com/', prefer: ['mimo-v2.6-pro', 'mimo'], aliases: 'xiaomi mimo 小米 token plan 套餐 订阅',
    keyHint: '订阅后在 Token Plan 页面取专属密钥（tp- 开头）和地址；地址以那个页面为准，不一样时点「改」。',
    plan: { fit: 'coding', note: '官方只给了编程工具的接法，没有写明聊天应用能不能用。', url: 'https://mimo.mi.com/docs/zh-CN/quick-start/summary/first-api-call' } },
  { id: 'opencode-go', name: 'OpenCode Go', blurb: 'OpenCode 的低价订阅，GLM、Kimi、DeepSeek、MiniMax 等开源模型。', group: 'plan',
    baseUrl: 'https://opencode.ai/inference/go/openai/v1', keyUrl: 'https://opencode.ai/', aliases: 'opencode go 套餐 订阅',
    plan: { fit: 'coding', note: '官方说明 Go 为编程工具设计，会监测不像编程工具发出的流量。', url: 'https://opencode.ai/docs/console/go/' },
    waiting: 'OpenCode Go 要求每个请求带上会话标识，AMADEUS 还没有发送它，现在接上也会被拒绝。' },

  // routers
  { id: 'openrouter', name: 'OpenRouter', blurb: '一个密钥用几百个模型，含 Claude、GPT、Gemini。', group: 'router', popular: true,
    baseUrl: 'https://openrouter.ai/api/v1', keyUrl: 'https://openrouter.ai/settings/keys', aliases: 'openrouter claude anthropic' },
  { id: 'vercel', name: 'Vercel AI Gateway', blurb: 'Vercel 的模型网关，一个密钥用各家模型。', group: 'router',
    baseUrl: 'https://ai-gateway.vercel.sh/v1', keyUrl: 'https://vercel.com/ai-gateway', aliases: 'vercel gateway claude' },
  { id: 'aihubmix', name: 'AiHubMix', blurb: '聚合平台，含 Claude、GPT、Gemini 与国产模型。', group: 'router',
    baseUrl: 'https://aihubmix.com/v1', keyUrl: 'https://aihubmix.com/token', aliases: 'aihubmix claude' },
  { id: 'ppio', name: 'PPIO 派欧云', blurb: '国内的开源模型推理与聚合平台。', group: 'router',
    baseUrl: 'https://api.ppinfra.com/v3/openai', keyUrl: 'https://ppio.com/settings/key-management', aliases: 'ppio 派欧' },
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

/** Whether the connect form can take a key for it: not while the backend lacks something. */
export const canConnect = (entry: CatalogEntry) => !entry.waiting;

export const catalogEntry = (id: string | undefined) => (id ? CATALOG.find((entry) => entry.id === id) : undefined);

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
