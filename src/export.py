"""导出层：一张总表 + 一份源清单。

总表每行 = raw.csv 的一个模型，包含：
  - raw.csv 原样 7 列（便于和人工清单对照）
  - 规范化列
  - 价格 + 币种
  - **data_provider / provider_weblink / source_url / source_snippet**（溯源）
  - **price_status**：got / not_found，明确标注哪些没拿到
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from .records import PriceRecord

PRICE_FIELDS = [
    "input_per_1m",
    "output_per_1m",
    "cache_read_per_1m",
    "per_image",
    "per_video",
    "per_second",
]

COLUMNS = [
    # ── raw.csv 原样 ──
    "Model",
    "Company",
    "Function",
    "Total Para",
    "Activate Para",
    "On/Off Line",
    "Reasoning",
    # ── 规范化 ──
    "access_mode",
    "lifecycle",
    "is_open_weight",
    # ── 价格状态：这一列就是「哪些没获取到」的标注 ──
    "price_status",
    "price_kind",
    # ── 价格（统一每 100 万 token 美元；图像/视频/秒另计）──
    "input_per_1m",
    "output_per_1m",
    "cache_read_per_1m",
    "per_image",
    "per_video",
    "per_second",
    "currency",
    "context_length",
    # ── 溯源 ──
    "data_provider",
    "provider_weblink",
    "source_url",
    "source_snippet",
    "fetched_at",
]


def _sort_key(record: PriceRecord) -> tuple:
    """取值优先级，从主到次：

    1. 币种：非美元的排最后（本表是美元表，欧元报价不能当美元用）
    2. 源层级：官方 > vendored > 第三方
    3. 厂商自己的牌价 > 平台转售价
    4. standard 层级优先（batch/flex 便宜、fast/priority 贵，都不是牌价）
    5. 无 qualifier 优先（长上下文/大 prompt 档是加价，不是牌价）
    6. 较新者优先
    7. model_id 字典序——只为让结果确定，避免同分时随输入顺序漂移
    """
    return (
        0 if record.currency == "USD" else 1,
        record.source_tier,
        0 if record.is_official else 1,
        0 if record.service_tier == "standard" else 1,
        0 if not record.qualifier else 1,
        tuple(-ord(c) for c in record.fetched_at),
        record.model_id,
    )


def pick_best(records: list[PriceRecord]) -> PriceRecord | None:
    """从一个模型的多条观测里选出最能代表「牌价」的那条。

    只取一条而不是逐字段拼装，是为了让整行的溯源自洽——
    表里的价格和 source_snippet 必定来自同一条原始记录，可以直接核对。
    """
    priced = [r for r in records if r.input_per_1m or r.output_per_1m]
    pool = priced or records
    return sorted(pool, key=_sort_key)[0] if pool else None


def _fmt(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def write_table(
    path: Path, raw_models: list, best_by_model: dict[str, PriceRecord]
) -> dict:
    stats = defaultdict(int)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for raw in raw_models:
            best = best_by_model.get(raw.model)
            av = raw.availability

            if best is None:
                stats["not_found"] += 1
                row = [
                    raw.model, raw.company, raw.function,
                    _fmt(raw.total_params_b), _fmt(raw.active_params_b),
                    av.raw, "Yes" if raw.reasoning else "No",
                    av.access_mode, av.lifecycle,
                    "1" if av.is_open_weight else "0",
                    "not_found", "",
                    "", "", "", "", "", "", "", "",
                    "", "", "", "", "",
                ]
            else:
                stats["got"] += 1
                stats[
                    "official" if best.is_official else "hosted"
                ] += 1
                row = [
                    raw.model, raw.company, raw.function,
                    _fmt(raw.total_params_b), _fmt(raw.active_params_b),
                    av.raw, "Yes" if raw.reasoning else "No",
                    av.access_mode, av.lifecycle,
                    "1" if av.is_open_weight else "0",
                    "got",
                    "official" if best.is_official else "hosted",
                    _fmt(best.input_per_1m),
                    _fmt(best.output_per_1m),
                    _fmt(best.cache_read_per_1m),
                    _fmt(best.per_image),
                    _fmt(best.per_video),
                    _fmt(best.per_second),
                    best.currency,
                    _fmt(best.context_length),
                    best.raw.get("provider_name") or best.provider,
                    best.raw.get("weblink") or "",
                    best.source_url,
                    best.source_snippet[:300],
                    best.fetched_at[:10],
                ]
            writer.writerow(row)
    return dict(stats)


def write_sources_md(
    path: Path,
    fetches: list[dict],
    counts: dict,
    parse_warnings: list[str] | None = None,
) -> None:
    """源清单：每个数据 provider 的名字、weblink、许可、本次产出。

    末尾附解析告警。这不是可有可无的装饰——厂商改一个列头就会静默丢掉一个
    价格维度（Moonshot 的 `Input Price` 就中过一次，那批模型只剩输出价，
    表面上一切正常）。告警可见是唯一的发现途径。
    """
    lines = [
        "# 数据源清单",
        "",
        f"由 `update_prices.py` 于 {fetches[0]['fetched_at'][:19] if fetches else '—'} 自动生成。",
        "",
        "| 数据 Provider | 网页 | 抓取地址 | 许可 | 状态 | 记录数 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for f in sorted(fetches, key=lambda x: (not x["ok"], x["source"])):
        status = "✅" if f["ok"] else f"❌ {f.get('error_kind') or ''}"
        weblink = f.get("weblink") or ""
        lines.append(
            f"| {f.get('provider_name') or f['source']} | {weblink} | `{f['url'][:70]}` | "
            f"{f.get('license') or '—'} | {status} | {counts.get(f['source'], 0)} |"
        )
    lines += [
        "",
        "## 说明",
        "",
        "- **官方价 (official)**：模型厂商自己发布的牌价。",
        "- **托管价 (hosted)**：云平台/聚合器转售该模型的价格，通常与牌价不同"
        "（实测 Bedrock 上的 Claude 普遍比 Anthropic 官方贵约 10%）。",
        "- 价格统一换算为**每 100 万 token 美元**；图像/视频/秒按次计价另列。",
        "  `source_snippet` 保留产出该数字的原文，任何数字存疑可直接核对。",
        "- ⚠️ Cortecs 报价为**欧元**，选价时已排除，不与美元混用。",
        "",
        "## 关于 models.dev 与 LiteLLM",
        "",
        "这两个是 MIT 许可的开源数据集，已 vendor 到本地 `vendor/`，",
        "上游站点消失也不影响使用（只是停止获得更新）。",
        "",
        "需要说明的是它们**本身就是根源**，无法\"从更上游自己获取\"：",
        "models.dev 的 `google.ts` 里写着 `cost: existing.cost`（价格取自手工维护的 TOML），",
        "并注明 Google 的 Models API 不提供价格；LiteLLM 的 JSON 由人和 AI bot",
        "依据厂商公告手工维护。厂商侧确实不存在可抓的价格 API。",
        "",
    ]

    if parse_warnings:
        lines += [
            "## 解析告警",
            "",
            "厂商改一个列头就会静默丢掉一个价格维度，所以认不出的东西必须报出来。",
            "",
        ]
        lines += [f"- {w}" for w in parse_warnings]
        lines.append("")
    else:
        lines += ["## 解析告警", "", "本次无告警。", ""]

    path.write_text("\n".join(lines), encoding="utf-8")
