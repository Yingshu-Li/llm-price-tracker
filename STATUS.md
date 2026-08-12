# 项目状态

> 最后更新：2026-08-12 · 覆盖 **282/492**（官方牌价 82 · 托管价 200）

## 一句话

从 22 个价格源自动抓取，匹配到 `raw.csv` 的 492 个模型，导出一张带完整溯源的总表，
并明确标注哪些拿不到。

```bash
python update_prices.py          # 全流程
python update_prices.py --dry-run        # 只看统计
python update_prices.py --refresh-vendor # 顺便刷新本地 MIT 数据副本
```

---

## 已完成

### 数据源（22 个，10000+ 条价格观测）

| 层 | 源 | 状态 | 说明 |
| --- | --- | --- | --- |
| **1 官方** | OpenAI / Anthropic / xAI / Zhipu / Moonshot 的 `.md` 文档 | ✅ | 厂商为 agent 维护的 markdown 表格，比抓 HTML 稳 |
| **1 官方** | AWS Bedrock 价格表（2 个 offer） | ✅ | 无需认证，regional CSV |
| **1 官方** | Azure Foundry Models API | ✅ | 无需认证，按 productName 白名单收窄 |
| **2 vendored** | models.dev、LiteLLM | ✅ | MIT，已存 `vendor/`（5.3MB），上游挂了照常用 |
| **3 直连** | OpenRouter、DeepInfra、HF Router、Vercel、Pioneer、ofox、Empirio、OVHcloud、Chutes、Tinfoil、Cortecs、Requesty | ✅ | 12 个，全部无需 key，配置驱动 |

加新源改 `config/price_apis.yaml` 即可，通常不用写代码。

### 能力

- **溯源到原文**：每个价格都带 `data_provider` / `provider_weblink` / `source_url` /
  `source_snippet`（产出该数字的原始行）。构造期断言强制，缺出处直接报错。
- **官方价 vs 托管价**：`price_kind` 列区分厂商牌价与平台转售价
  （实测 Bedrock 上的 Claude 普遍比 Anthropic 官方贵约 10%）。
- **未获取标注**：`price_status = got / not_found`。
- **离线可用**：`vendor/` 有 models.dev 与 LiteLLM 的本地 MIT 副本。
- **解析告警**：认不出的列头、跳过的表格全部写进 `out/sources.md`。
- **单源失败隔离**：任一源 404 / DNS 失败 / 被企业代理拦截都不影响其他源。

### 产物

| 文件 | 内容 |
| --- | --- |
| `out/models_with_prices.csv` | 总表，492 行 |
| `out/sources.md` | 源清单 + 许可 + 解析告警 |

---

## 覆盖现状

### 已获取 282

| 数据 Provider | 数量 |
| --- | ---: |
| models.dev | 137 |
| LiteLLM | 38 |
| OpenAI 官方文档 | 28 |
| Zhipu 官方文档 | 22 |
| AWS Bedrock | 19 |
| xAI 官方文档 | 10 |
| Anthropic 官方文档 | 10 |
| Azure AI Foundry | 7 |
| 其余（DeepInfra / HF Router / Vercel / Empirio…） | 11 |

### 未获取 210 —— 大部分是客观上没有价格

| 公司 | 未获取 | 其中纯开源权重 |
| --- | ---: | ---: |
| Alibaba / Qwen | 35/89 | 31 |
| Google | 28/66 | 19 |
| TII / Falcon | 16/16 | 16 |
| Tencent / Hunyuan | 13/16 | 11 |
| OpenAI | 12/48 | 9 |
| Cohere | 12/20 | 6 |
| IBM | 9/11 | 9 |
| NAVER | 9/9 | 6 |
| iFLYTEK | 8/8 | 0 |

小众开源变体没有任何托管方在跑，厂商也不作为服务售卖——**价格不存在，不是抓不到**。
TII/Falcon 全部 16 个都属此类，他们根本不卖推理服务。

---

## TODO

按性价比排序。目前没有在做的项。

### 可能有实际收益

- [ ] **Google Gemini 定价页抓取器**（约 +14~28）
      定价页是服务端渲染，价格确在 HTML 字节里。注意：models.dev 在这里也是
      **人工维护 TOML**，说明没有更省事的路子。预计每年要修 2–4 次选择器。
- [ ] **NAVER / iFLYTEK 首方抓取器**（约 +17）
      所有聚合器零覆盖，只能自建。NAVER 定价页 2.47MB HTML，iFLYTEK 仅中文文档。
      投入产出比最低。
- [ ] **xAI 原生价格 API**
      已确认 `api.x.ai/v1/models` 返回 `prompt_text_token_price` 等字段（需 key）。
      是目前已知**唯一**厂商自己在 API 里给价格的。我们已从 `.md` 拿到 xAI 价格，
      所以这项优先级低，但值得记录。

### 工程改进

- [ ] **匹配建议**（辅助人工补 `config/aliases.yaml`）
      原有的模糊匹配候选生成已在清理中移除（算出来没有消费者，且是
      O(未匹配数 × 索引键数) 的开销）。如果要做人工补别名的工作流，
      建议做成 `--suggest` 开关按需计算，而不是每次跑都算。
- [ ] **历史与趋势**
      当前只导出最新快照。要看降价趋势需要落 SQLite 追加观测。
      原计划有 schema 设计，本轮未实现。
- [ ] **定时运行**
      本机 `sudo` 被 BeyondTrust EPM 拦截，只能用用户级 launchd agent
      （`~/Library/LaunchAgents/`）或 `crontab -e`，装不了系统级 LaunchDaemon。

### 已明确放弃

- **速度数据（tok/s、TTFT）** —— 本轮范围外。
  顺带发现 HF Router 免费提供 `throughput` 和 `first_token_latency_ms`
  （129 个模型全有），已在 `price_apis.py` 里存进 `raw` 字段但未使用，
  以后要做可以直接取。若要自测，最实际是用一把 OpenRouter key 打 406 个模型，
  一次全量扫约 $1.5–5。注意从悉尼测会叠加约 150–200ms 跨洋 RTT。
- **腾讯 / 火山方舟 / 阿里 Model Studio / 百度千帆 / SenseTime / Baichuan 定价页**
  —— 纯 SPA 壳，需无头浏览器，脆弱度高于收益。

### 未被代码消费的资产

- `config/companies.yaml`（28 家公司的官网 / 博客 / RSS / HF org / 定价页）
  当前**没有脚本读它**。它是为"监测新模型发布"准备的，那个功能本轮没做。
  内容都经过实测验证（含哪些确认无 RSS、哪些 HF org slug 大小写易错），
  保留作参考资料。

---

## 踩过的坑（改动前请先读）

每一条都对应一次真实事故，不是理论担忧。

1. **单位差 1000 倍**。AWS 的 `Unit` 多是 `1K tokens`，OpenRouter 是每 token，
   Cortecs 是欧元。每个源的单位都用**已知官方价做锚**实测确定，不许猜。
2. **币种混用**。Cortecs 报欧元，选价时已排除。
3. **规范化不对称**。源侧和 raw 侧必须走同一个 `name_candidates()`。
   曾只对 raw 侧剥公司前缀，导致 NVIDIA / Amazon / Microsoft 整批 0 匹配，
   唯一症状是匹配率莫名偏低。
4. **匹配提前 break**。同一模型的不同候选形式命中不同源，提前退出会让
   低优先级源挡住官方牌价（Anthropic 曾因此从 10 掉到 7）。
5. **纯数字后缀歧义**。`claude-opus-4-5-20251101` 是日期快照（同一模型），
   `minimax-m2` + `7` 是版本号（不同模型）。按位数区分：≥4 位是日期。
6. **部分列静默丢失**。Moonshot 的 `Input Price` 一度没进映射表，那批模型
   只有输出价没有输入价，表面完全正常。现在未识别列头会报进 `out/sources.md`。
7. **跨公司误配**。匹配严格限制在同一家公司内。聚合器给的 id 先经
   `infer_company()` 归属，推断不出就丢弃。⚠️ 严禁用 `spark` 子串匹配
   （会命中 OpenAI 的 `gpt-5.3-codex-spark`）。
8. **macOS 文件系统大小写不敏感**。删 `SOURCES.md` 会连 `sources.md` 一起删。
