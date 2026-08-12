# 数据源清单

由 `update_prices.py` 于 2026-08-12T07:27:10 自动生成。

| 数据 Provider | 网页 | 抓取地址 | 许可 | 状态 | 记录数 |
| --- | --- | --- | --- | --- | --- |
| Anthropic 官方文档 | https://platform.claude.com/docs/en/about-claude/pricing | `https://platform.claude.com/docs/en/about-claude/pricing.md` | 厂商官方文档 | ✅ | 31 |
| AWS Bedrock | https://aws.amazon.com/bedrock/pricing/ | `https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonBedrock/` | AWS 公开价格表 | ✅ | 791 |
| AWS Bedrock | https://aws.amazon.com/bedrock/pricing/ | `https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonBedrockF` | AWS 公开价格表 | ✅ | 235 |
| Azure AI Foundry | https://azure.microsoft.com/pricing/details/phi-3/ | `https://prices.azure.com/api/retail/prices?$filter=serviceName%20eq%20` | Azure 公开价格表 | ✅ | 136 |
| Chutes | https://chutes.ai | `https://llm.chutes.ai/v1/models` | 公开 API | ✅ | 13 |
| Cortecs | https://cortecs.ai | `https://api.cortecs.ai/v1/models` | 公开 API | ✅ | 97 |
| DeepInfra | https://deepinfra.com/models | `https://api.deepinfra.com/models/list` | 公开 API | ✅ | 205 |
| Empirio Labs | https://empiriolabs.ai | `https://api.empiriolabs.ai/v1/models` | 公开 API | ✅ | 56 |
| HuggingFace Router | https://huggingface.co/models?inference_provider=all | `https://router.huggingface.co/v1/models` | 公开 API | ✅ | 186 |
| LiteLLM | https://github.com/BerriAI/litellm | `https://cdn.jsdelivr.net/gh/BerriAI/litellm@main/model_prices_and_cont` | MIT | ✅ | 2145 |
| models.dev | https://models.dev | `https://models.dev/api.json` | MIT | ✅ | 4925 |
| Moonshot AI / Kimi 官方文档 | https://platform.moonshot.ai/docs/pricing/chat-k3 | `https://platform.kimi.ai/docs/pricing/chat-k3.md` | 厂商官方文档 | ✅ | 10 |
| Moonshot AI / Kimi 官方文档 | https://platform.moonshot.ai/docs/pricing/chat-k3 | `https://platform.kimi.ai/docs/pricing/chat-k27-code.md` | 厂商官方文档 | ✅ | 10 |
| Moonshot AI / Kimi 官方文档 | https://platform.moonshot.ai/docs/pricing/chat-k3 | `https://platform.kimi.ai/docs/pricing/chat-k26.md` | 厂商官方文档 | ✅ | 10 |
| Moonshot AI / Kimi 官方文档 | https://platform.moonshot.ai/docs/pricing/chat-k3 | `https://platform.kimi.ai/docs/pricing/chat-v1.md` | 厂商官方文档 | ✅ | 10 |
| ofox | https://ofox.ai | `https://api.ofox.ai/v2/models/catalog?include=provider_price&limit=100` | 公开 API | ✅ | 125 |
| OpenAI 官方文档 | https://platform.openai.com/docs/pricing | `https://developers.openai.com/api/docs/pricing.md` | 厂商官方文档 | ✅ | 216 |
| OpenRouter | https://openrouter.ai/models | `https://openrouter.ai/api/v1/models` | 公开 API | ✅ | 317 |
| OVHcloud AI Endpoints | https://endpoints.ai.cloud.ovh.net | `https://catalog.endpoints.ai.ovh.net/rest/v2/openrouter` | 公开 API | ✅ | 12 |
| Pioneer | https://pioneer.ai | `https://api.pioneer.ai/v1/models` | 公开 API | ✅ | 193 |
| Requesty | https://requesty.ai | `https://router.requesty.ai/v1/models/managed` | 公开 API | ✅ | 47 |
| Tinfoil | https://tinfoil.sh | `https://inference.tinfoil.sh/v1/models` | 公开 API | ✅ | 8 |
| Vercel AI Gateway | https://vercel.com/ai-gateway/models | `https://ai-gateway.vercel.sh/v1/models` | 公开 API | ✅ | 212 |
| xAI 官方文档 | https://docs.x.ai/docs/models | `https://docs.x.ai/developers/models.md` | 厂商官方文档 | ✅ | 17 |
| Zhipu AI / GLM 官方文档 | https://docs.z.ai/guides/overview/pricing | `https://docs.z.ai/guides/overview/pricing.md` | 厂商官方文档 | ✅ | 34 |

## 说明

- **官方价 (official)**：模型厂商自己发布的牌价。
- **托管价 (hosted)**：云平台/聚合器转售该模型的价格，通常与牌价不同（实测 Bedrock 上的 Claude 普遍比 Anthropic 官方贵约 10%）。
- 价格统一换算为**每 100 万 token 美元**；图像/视频/秒按次计价另列。
  `source_snippet` 保留产出该数字的原文，任何数字存疑可直接核对。
- ⚠️ Cortecs 报价为**欧元**，选价时已排除，不与美元混用。

## 关于 models.dev 与 LiteLLM

这两个是 MIT 许可的开源数据集，已 vendor 到本地 `vendor/`，
上游站点消失也不影响使用（只是停止获得更新）。

需要说明的是它们**本身就是根源**，无法"从更上游自己获取"：
models.dev 的 `google.ts` 里写着 `cost: existing.cost`（价格取自手工维护的 TOML），
并注明 Google 的 Models API 不提供价格；LiteLLM 的 JSON 由人和 AI bot
依据厂商公告手工维护。厂商侧确实不存在可抓的价格 API。

## 解析告警

厂商改一个列头就会静默丢掉一个价格维度，所以认不出的东西必须报出来。

- openai_md: 未识别的列头 `portrait`
- openai_md: 未识别的列头 `details`
- openai_md: 未识别的列头 `use case`
- openai_md: 未识别的列头 `training`
- openai_md: 未识别的列头 `pricing`
- openai_md: 未识别的列头 `landscape`
- openai_md: 未识别的列头 `size`
- openai_md: 未识别的列头 `category`
- openai_md: 未识别的列头 `output / cost`
- openai_md: 未识别的列头 `estimated cost`
- openai_md: 跳过表格「Grouped Pricing Table data」— 没有可识别的价格列
- anthropic_md: 未识别的列头 `additional input tokens`
- anthropic_md: 未识别的列头 `tool use system prompt token count`
- anthropic_md: 未识别的列头 `input tokens per tool definition`
- anthropic_md: 未识别的列头 `tool choice`
- anthropic_md: 跳过表格「Claude Platform on AWS pricing」— 找不到模型/工具名列
- anthropic_md: 跳过表格「Claude in Microsoft Foundry pricing」— 找不到模型/工具名列
- anthropic_md: 跳过表格「Prompt caching」— 找不到模型/工具名列
- anthropic_md: 跳过表格「Tool use pricing」— 没有可识别的价格列
- anthropic_md: 跳过表格「Bash tool」— 没有可识别的价格列
- anthropic_md: 跳过表格「Text editor tool」— 没有可识别的价格列
- anthropic_md: 跳过表格「Computer use tool」— 没有可识别的价格列
- anthropic_md: 跳过表格「Tokens」— 找不到模型/工具名列
- anthropic_md: 跳过表格「Session runtime」— 找不到模型/工具名列
- anthropic_md: 跳过表格「Worked example」— 找不到模型/工具名列
- anthropic_md: 跳过表格「Worked example」— 找不到模型/工具名列
- xai_md: 跳过表格「Voice Pricing」— 列头可映射但未产出任何记录
- zhipu_md: 未识别的列头 `cached input storage`
