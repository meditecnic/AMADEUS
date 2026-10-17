export const fixedTime = '2026-10-17T12:00:00.000Z';
export const model = {
  id: 'deepseek-chat', request_id: 'deepseek-chat', display_name: 'Mock Chat',
  lifecycle: 'stable', callable: true, source: 'manifest', thinking_control: null,
};
export const providers = [{
  id: 'deepseek', display_name: 'Mock DeepSeek', default_model: model.id,
  configured: true, credential_required: true, model_discovery: true,
  parameters: ['temperature'], base_url_configurable: false,
  catalog_version: 1, catalog_verified_at: fixedTime, default_model_capability: model,
  catalog: [model], enabled: true, selector_admission: 'admitted',
}];
export const searchSettings = {
  active_provider: 'tavily',
  providers: {
    tavily: { id: 'tavily', label: 'Tavily', configured: false },
    firecrawl: { id: 'firecrawl', label: 'Firecrawl', configured: false },
  },
  configured: false,
};
