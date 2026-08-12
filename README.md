# LLM 模型价格追踪器

给 `raw.csv`（492 个模型 / 28 家公司的人工清单）自动补上价格，并标注哪些拿不到。

```bash
pip install -r requirements.txt     # httpx + PyYAML
python update_prices.py             # 抓取全部源并导出
```

`raw.csv` 只读，脚本永不修改它。

📋 **项目进度、覆盖现状、TODO、踩过的坑 → 见 [STATUS.md](STATUS.md)**

## 产物

| 文件 | 内容 |
| --- | --- |
| `out/models_with_prices.csv` | 总表，492 行（未获取的也保留） |
| `out/sources.md` | 数据源清单 + 许可 + 解析告警 |

总表关键列：

| 列 | 说明 |
| --- | --- |
| `price_status` | `got` / `not_found` —— **哪些没拿到看这一列** |
| `price_kind` | `official`（厂商牌价）/ `hosted`（平台转售价） |
| `input_per_1m` `output_per_1m` … | 统一每 100 万 token 美元；图像/视频/秒另列 |
| `data_provider` `provider_weblink` | 这个价格是谁给的、去哪儿看 |
| `source_url` `source_snippet` | 实际抓取地址 + 产出该数字的**原文片段** |

## 目录

```
config/
  price_apis.yaml        12 个可直连价格 API 的配置（加源改这里，通常不用写代码）
  official_sources.yaml  厂商官方 .md 文档端点
  companies.yaml         28 家公司的官网/博客/RSS/HF org/定价页（参考资料，暂未被代码消费）
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
