# 项目状态

> 最后更新：2026-08-14 · 覆盖 **282/492**（官方牌价 118 · 托管价 164）
> 其余 210 = **139 权重免费无报价**（价格不存在）+ **71 真缺口**（闭源却没拿到价）

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
- **官方价 vs 托管价**：`price_kind` 列区分厂商牌价与第三方转售价
  （实测 Bedrock 上的 Claude 普遍比 Anthropic 官方贵约 10%）。
  聚合器 key 里的卖家段（`gemini/gemini-2.5-pro`）会被识别为厂商自营，
  不再一律算作转售——这一项让官方价从 82 升到 118。
- **权重成本 vs 服务价**：`weights` 列（`free` / `proprietary`）与 `price_kind`
  正交。开源权重模型的 API 价是**别人替你部署的服务费**，不是权重的价。
- **卖家可见**：`hosted_seller` 列给出实际报价方。同款开源模型跨平台价差
  可达十几倍（gemma-3：\$0.05 ~ \$0.65），一个 hosted 价只代表那个卖家。
- **未获取标注**：`price_status = got / weights_free / not_found`，
  把"价格不存在"与"没抓到"分开。
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
| models.dev | 112 |
| LiteLLM | 59 |
| OpenAI 官方文档 | 28 |
| Zhipu 官方文档 | 22 |
| AWS Bedrock | 21 |
| xAI 官方文档 | 10 |
| Anthropic 官方文档 | 10 |
| Azure AI Foundry | 7 |
| 其余（DeepInfra / Empirio / Moonshot / Vercel） | 13 |

### 未获取 210 = 139 价格不存在 + 71 真缺口

拆开看才有意义，这两类的性质完全不同：

**139 `weights_free`**——开源权重、没有任何托管方在跑。**价格不存在，不是抓不到**；
想用直接下权重自己跑，成本是算力不是订阅。TII/Falcon 全部 16 个都属此类，
他们根本不卖推理服务。主要分布：Qwen 31 · Google 19 · TII 16 · Tencent 11 · IBM 9。

**71 `not_found`**——闭源且只能在线用，却没拿到价。这才是要补的。

| 公司 | 缺口 | | 能力类型 | 缺口 |
| --- | ---: | --- | --- | ---: |
| Google | 9 | | General-Purpose | 27 |
| iFLYTEK | 8 | | **Speech & Audio** | **20** |
| Amazon / Nova | 6 | | Image Generation | 9 |
| Cohere | 6 | | Embedding | 8 |
| Zhipu / MiniMax | 5 / 5 | | Video Generation | 6 |

⚠️ **44/71（62%）是非文本模型**（语音/图像/视频/嵌入）。这是结构性原因：
models.dev、LiteLLM、OpenRouter 这些聚合器主要收 chat 模型，按秒/按次计费的
语音视频几乎不收录。所以这批缺口不是"抓取器写得不够多"，而是**整个聚合器
生态就不覆盖这类模型**，只能走厂商首方源。

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
- [x] **定时运行** —— 已用 GitHub Actions 实现（每天 UTC 00:00），
      见 `.github/workflows/update-prices.yml`。绕开了本机 `sudo` 被
      BeyondTrust EPM 拦截、装不了系统级 LaunchDaemon 的限制。

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
9. **两个正交维度压成一列**。`price_kind` 曾同时承担"权重能否自取"和"这个价
   是谁报的"，结果 139 个开源权重模型被标成 `not_found`，读起来像抓取失败，
   实际是**价格根本不存在**。拆成 `weights` + `price_status` 三值后才对得上现实。
10. **同名字段在不同源里含义不同**。OpenRouter 的 `pricing.image` 与 `prompt`
   同值同量级（gemini-2.5-pro 都是 `0.00000125`），是**图像输入 token 的每
   token 价**；Empirio 的 `pricing.image` 才是真·每张美元（`0.035`）。
   我们曾把 OpenRouter 的映射到 `per_image` 且跳过单位换算，产出一批
   1e-6 量级的假"按张价"。**同名字段必须逐源实测**，不能照抄。
11. **非文本模型的计价单位必须按 Function 定死**。图像按张、视频按秒，
   源里塞进 token 字段的值差几万倍（Seedance 2.0 显示 `$4.7/1M token`，
   实际 `$0.07/秒`）。单位不符的源保留但值不参与统计——按张价和按 token 价
   之间没有换算关系。
12. **多模态表的输出列曾整列丢失**。OpenAI 分组表列头是 `Output / cost`，
   不在映射表里，导致音频/图像档**只有输入价没有输出价**（audio \$32 有、
   \$64 没有），表面完全正常。是 `out/sources.md` 的"未识别列头"告警
   暴露的——这条告警机制救回了一整列数据。
13. **分层标签不能自己拼接**。OpenAI 把长上下文价放在并列列组里，列头写
   `Long context`；模型名后括号里的 `<272K context length` 描述的是**短档**。
   解析器曾把两者拼成 `<272K context length; long context`——自相矛盾（长档
   恰恰是 272K 以上），而且那句话官网根本没写过。照抄列头原文即可。
   ⚠️ 按上下文分档时只收 standard 服务档：batch/flex/fast 是"怎么跑"，
   与上下文分层正交，混进来会让同一个上下文档冒出好几个价。
11. **多模态表按模态分行,不对齐就会比出假价差**。OpenAI 的表里
   `gpt-audio-1.5` 有 Text \$2.50 和 Audio \$32.00 两行,**都落进 input_per_1m**。
   不按 modality 收窄,"最低价"会拿 Text 价去比 Audio 官方价,得出 13 倍价差——
   那不是哪个卖家更便宜,只是换了个模态。`pick_official` 与比价池都必须
   优先/限定文本行。影响 6 个 OpenAI 多模态模型。
11. **产品线前缀也要剥,不只是公司名**。`_COMPANY_PREFIXES` 原本只收公司名,
   但 raw.csv 写 `Doubao Seedream 5.0 pro` / `CohereLabs/c4ai-command-r-plus`,
   而源里叫 `seedream-5-0-pro` / `command-r-plus`——`doubao` 和 `c4ai` 是
   产品线/仓库前缀,不剥就整批对不上。补上后 Cohere 从 8 涨到 11、
   ByteDance 补齐到 9/9。**症状同样是"匹配率莫名偏低",没有任何报错。**
11. **聚合器 key 里的卖家段不能丢**。models.dev 的 key 是 `卖家/模型路径`，
   `gemini/gemini-2.5-pro` 的卖家就是 Google 自己。一律 `is_official=False`
   把 36 个厂商牌价误标成转售价。判据一直在 key 里，丢掉它才是信息损失。
   反向仍保持保守：推断不出卖家归属时维持 False——宁可把官方价谦称为托管价，
   也不能把转售价谎报成牌价。
