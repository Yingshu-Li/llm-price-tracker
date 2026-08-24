# LLM 模型价格追踪器

给 `raw.csv`（548 个模型 / 33 家公司的人工清单）自动补上价格，并标注哪些拿不到。

```bash
pip install -r requirements.txt     # httpx + PyYAML
python update_prices.py             # 抓取全部源并导出
```

`raw.csv` 只读，脚本永不修改它。

📋 **项目进度、覆盖现状、TODO、踩过的坑 → 见 [STATUS.md](STATUS.md)**

## 自动更新

[.github/workflows/update-prices.yml](.github/workflows/update-prices.yml) 每天
**UTC 00:00**（悉尼 11:00）跑一次，也可在 Actions 页手动触发。单次约 95 秒，
25 个源全部无需 API key（含百川人民币官网价、302.AI 讯飞转售价与 ECB 每日参考汇率）。

- 用 `--refresh-vendor` 拉取 models.dev / LiteLLM 最新数据（最低价约八成靠这两个源），
  但**刷新下来的 `vendor/` 不提交** —— 5.2MB × 每天会让仓库迅速膨胀。
- **只提交 `out/`**；价格无变化时跳过提交，不产生空 commit。
- **`raw.csv` 被改动会直接让流程失败** —— 它是只读的人工清单，脚本不该写它。
  真被写了要报错，而不是悄悄提交上去。
- 产物同时上传为 artifact（留存 14 天），流程失败时也能拿到。

## 产物

| 文件 | 内容 |
| --- | --- |
| `out/models_with_prices.csv` | 总表，364 行 / 350 个模型（**当前只导出 General-Purpose，并隐藏指定模型**），47 列 |
| `out/sources.md` | 数据源清单 + 许可 + 解析告警 |
| `out/exchange_rates.json` | ECB 最近一次成功的每日参考汇率；周末和短时故障回退使用 |

总表关键列：

| 列 | 说明 |
| --- | --- |
| `display_name` | 网页展示用可读名（`Gemini 3.7 Flash`）。**派生自 `Model`，不是 key** |
| `Model` | 规范标识符：可直接调用的 API id 或可直接拉取的 HF 路径 |
| `weights` | `free`（权重公开可自取）/ `proprietary`（只能买 API） |
| `price_status` | `got` / `weights_free` / `not_found` —— **见下方三值说明** |
| `price_kind` | `official`（拿到厂商牌价）/ `hosted`（**只有第三方转售价**） |
| `text_cheapest_input_seller_url` | 当前最低输入价卖家的模型目录/模型详情页；每日比价后随 seller 同步切换 |
| `text_cheapest_input_source_url` | 得出最低输入价的数据证据链接（API、JSON 或官方价格表），不是商家入口 |
| `text_cheapest_output_seller_url` | 当前最低输出价卖家的模型目录/模型详情页；每日比价后随 seller 同步切换 |
| `text_cheapest_output_source_url` | 得出该价格的数据证据链接（API、JSON 或官方价格表），不是商家入口 |
| `text_*_fx_marker` | `⇄` 表示该价格由非美元原始报价换算而来 |
| `text_*_fx_note` / `text_*_fx_source_url` | 原币金额、换算率、汇率日期与 ECB 出处 |
| `text_*` `audio_*` `image_*` `video_*` | 四组模态价，每组含官方价+最低价 —— 见下 |
| `context_tier` | 上下文长度；厂商分档定价时按官网原文，一档一行 —— 见下 |
| `official_price` | `got` / `weight open source` / `None` —— 见下 |
| `*_quote_count` | 该组参与比价的独立报价方数量，`1` 时最低价无比较意义 |
| `fetched_at` | 本行价格的抓取日期 |

> `seller` 与 `provider` 是两回事：`seller` 是**收钱的人**（`nano-gpt`），
> `provider` 是**我们从哪儿读到这个价**（`models.dev`）。
>
> 表里**不再有**笼统的 `input_per_1m` 主列。价格一律分成"官方牌价"和
> "全网最低价"两组，各自带完整溯源——主列曾在分层展开后把短档价填进长档行，
> 同一行里两个数字自相矛盾。

### 网页两行式渲染

`Model` 列的大小写**不是不统一，是有意义的**：`gpt-5-mini` 是 OpenAI 的真实
API id，`google/gemma-2-27b-it` 是真实 HF 路径（大小写敏感，改了就 404）。
所以不统一 `Model`，而是另加 `display_name` 供展示：

```html
<h3>Gemini 3.7 Flash</h3>                      <!-- display_name -->
<code>google/gemini-3.7-flash</code>           <!-- Model，等宽+浅色 -->
```

⚠️ `display_name` **不唯一**（当前 25 组重名）。raw.csv 里同一模型会分别以
API 形态和权重形态各列一行（`MiniMax-M2` / `MiniMaxAI/MiniMax-M2`），展示名
自然相同。所以 React 的 `key`、去重、路由一律用 `Model`，不要用 `display_name`。

### 官方牌价 `official_price`

厂商自己发布的价。**没有官方价的两种情况含义完全不同，所以不都写空**：

| 取值 | 数量 | 含义 |
| --- | ---: | --- |
| `got` | 116 | 拿到了厂商牌价，数字见 `text_official_*` 等列 |
| `weight open source` | 200 | 开源权重，厂商只放权重不卖服务——**官方价客观不存在** |
| `None` | 63 | 闭源在售却没拿到牌价——**这是真缺口** |

⚠️ 判据是"有任意一种价格"，不是只看 token 价：`grok-imagine-image` 按张
（\$0.02/张）、`CogVideoX-3` 按秒计价，只查 input/output 会把这些**已有官方价**
的模型误判成缺失（曾因此把缺口从 38 错报成 48）。

`None` 的 63 个里，闭源真缺口主要在 Qwen 商用版、ByteDance doubao——
定价页是 SPA 或未写抓取器，详见 [STATUS.md](STATUS.md)。

### 按 Function 分表导出

`out/models_with_prices.csv` 仍只显示 `Function = General-Purpose`。此外，同一次
更新会生成两张能力分表：

| 文件 | Function | 价格口径 |
| --- | --- | --- |
| `out/coding_models_with_prices.csv` | Coding | 只展示每 100 万 token 价格 |
| `out/embedding_models_with_prices.csv` | Embedding（含 Rerank） | 只展示每 100 万输入/处理 token 价格；不展示输出价 |

能力分表保留对应 Function 的全部模型行。若某模型目前只有按次、按秒、按图片等
非 token 报价，价格列留空，不把不同结算单位强行换算或放进表格；原始报价仍照常
抓取并计入 `out/sources.md`。

其余 Function 的模型也照常抓取、照常计入 `out/sources.md`，只是暂不单独导出。

改回全量：把 [src/export.py](src/export.py) 里的 `EXPORT_FUNCTIONS` 设成 `None`，
或加入想显示的 Function 名。抓取层完全不受这个开关影响。

**全空的列不导出**：每张分表只保留实际有值的列。Coding 与 Embedding 分表明确
排除按张、按秒、按次等非 token 价格，因此不会出现对应价格列。
Embedding 与 Rerank 的响应分别是向量和相关性分数，不是生成文本；聚合源通用
schema 中即使带有 `output_per_1m`，也只保留在底层观测，不导出为文本输出价。

⚠️ 因此**列集合会随 `EXPORT_FUNCTIONS` 变化** —— 前端不能假定某列一定存在，
读表时先看表头。放开过滤后那 25 列会自动回来。

### 四组模态价，每组都有「官方价 + 最低价」

一个模型可以同时按多种模态计价，**每种模态各一组完整的价**，互不覆盖。
**单位写在列名里**，不另设单位列：

| 组 | 单位 | 列名前缀 |
| --- | --- | --- |
| 文本 | 每 100 万 token 美元 | `text_*_per_1m_usd` |
| 音频 | 每 100 万 token 美元（audio token 与 text token 分开计价） | `audio_*_per_1m_usd` |
| 图像 | 每张美元 | `image_*_per_image_usd` |
| 视频 | 每秒美元 | `video_*_per_second_usd` |

每组的结构一致：

```
<组>_official_<单位>          厂商牌价
<组>_official_provider         这个价来自哪个数据源
<组>_official_source_url       出处
<组>_cheapest_<单位>          全网最低价
<组>_cheapest_seller           谁收钱
<组>_cheapest_provider         数据来自哪个源
<组>_cheapest_source_url       出处
<组>_quote_count               参与比价的独立报价方数量
```

（text 组的输入/输出各有一套 cheapest，因为两者常来自不同卖家；
audio 组的输出价取同一条官方记录，保证同档自洽。）

实际效果：

```
GPT Realtime 2.1     text $4/$24    audio $32/$64          ← 同一模型两组价
Grok Imagine Video   video 官方 $0.05/秒   最低 $0.05/秒
Doubao Seedance 2.0  video 官方 —          最低 $0.07/秒   ← 曾误显示 $4.7/1M token
CogView 4            image 官方 $0.01/张   最低 $0.01/张
```

对语音模型来说 audio 价才是主价，只报文本价会严重低估成本（\$4 vs \$32）。

⚠️ **不做跨单位换算**：视频只认 `per_second`，不拿"每条视频"折算——一条视频
几秒是未知的，折算等于编数据。单位不符的源仍保留在 `out/sources.md`，
只是值不参与统计。

### 上下文分层 `context_tier`

厂商按 prompt 长度分档定价时，**一档一行**——只报一个数字等于把另一档藏起来
（`gpt-5.5` 短档 \$5.00、长档 \$10.00，差一倍）：

```
Model      context_tier            official_input  official_output
gpt-5.5    <272K context length         $5.00          $30.00
gpt-5.5    Long context                $10.00          $45.00
grok-4.5   < 200k prompt tokens         $2.00           $6.00
grok-4.5   ≥ 200k prompt tokens         $4.00          $12.00
gpt-5      400000                       $1.25          $10.00
```

这一列的填法：

| 情况 | 填什么 | 数量 |
| --- | --- | ---: |
| 厂商分档定价 | **官网原文**（`<272K context length` / `Long context` / `≥ 200k prompt tokens`） | 24 行 |
| 不分档 | 该模型的上下文长度数值 | 206 行 |
| 两者都拿不到 | 留空，**不猜** | 110 行 |

各厂商写法不统一（OpenAI 写 `Long context`，xAI 写 `≥ 200k prompt tokens`），
这里**照抄官网**而不改写成统一格式——改写就不再是"官网怎么写"了。

上下文长度取全部观测的**众数**：厂商定价表往往不含这个字段（OpenAI 的就没有），
得从聚合器拿；不同卖家可能报不同值（某些平台限流到更短上下文），多数派更接近
模型的真实规格。

总表因此是 **379 行 / 365 个模型**（14 个模型有多档），⚠️ **`Model` 列不再唯一**。

实测官方价与最低价的差距：

```
GPT-5.5           官方 $5.00  ->  最低 $0.1875  (27x)  via unorouter   q=45
Claude Opus 4.8   官方 $5.00  ->  最低 $0.425   (12x)  via unorouter   q=45
Gemini 3.5 Flash  官方 $1.50  ->  最低 $0.1857   (8x)  via unorouter   q=34
```

### 权重成本与服务价是两个正交维度

开源权重模型照样能有 API 报价——那是**别人替你部署的服务费**，不是权重的价。
所以 `weights` 与 `price_kind` 分开两列，`price_status` 也相应拆成三值：

| `price_status` | 数量 | 含义 |
| --- | ---: | --- |
| `got` | 238 | 拿到了推理服务价 |
| `weights_free` | 78 | 开源权重、无人转售——**价格不存在，不是抓漏了**，下权重自己跑即可 |
| `not_found` | 28 | 闭源且只能在线用，却没拿到价——**这才是真缺口** |

⚠️ 同一个开源模型在不同平台价差可达十几倍（gemma-3 从 \$0.05 到 \$0.65），
一个最低价只代表**那个卖家**，不代表"这个模型值多少钱"。看 `cheapest_*_seller`
和 `quote_count`。

## 目录

```
config/
  price_apis.yaml        12 个可直连价格 API 的配置（加源改这里，通常不用写代码）
  official_sources.yaml  厂商官方 .md 文档端点
  companies.yaml         33 家公司的官网/博客/RSS/HF org/定价页（参考资料，暂未被代码消费）
  aliases.yaml           人工确认的模型名映射（最高优先级）
src/
  records.py             PriceRecord 溯源契约（构造期断言，缺出处即报错）
  http.py                ETag 条件请求、重试、失败隔离、企业代理拦截识别
  normalize.py           可用性/参数量解析、名字候选、公司推断
  match.py               分级匹配：alias > exact > contains（不做模糊匹配）
  export.py              总表 + 源清单
  adapters/              md_docs · aws_bedrock · azure_retail · price_apis · vendored
vendor/                  models.dev / LiteLLM 的本地 MIT 副本（离线可用）
out/                     产物
```

## 加一个新价格源

多数情况下只需在 `config/price_apis.yaml` 追加一段：

```yaml
  - id: someapi
    name: Some API
    url: https://api.example.com/v1/models
    weblink: https://example.com/pricing
    list_path: data
    id_fields: [id]
    unit: per_token        # ⚠️ 必须实测确定，见下
    tier: 3
    fields:
      input_per_1m: pricing.input
      output_per_1m: pricing.output
```

⚠️ **`unit` 绝不能猜**。用已知官方价做锚实测：拿 Claude Sonnet（≈$3/1M）或
Qwen3-32B（≈$0.1/1M）去比对，看原始值 ×1 还是 ×1e6 更接近。猜错是 1000 倍的
静默错误，且数字看起来完全合理。未知单位代码会直接抛错而不是猜。
