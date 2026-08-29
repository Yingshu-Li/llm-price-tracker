"""nano-gpt 图像模型转售价（Tier 3 第三方）。

为什么单独写适配器而不是配进 ``price_apis.yaml``：

* 它的 ``models.image`` 是**以模型 id 为键的嵌套字典**，不是列表，
  通用适配器的 ``_extract_list`` 接不了；
* 每个模型的 ``cost`` 又是**以分辨率为键的字典**（``{"1024x1024": 0.03, …}``），
  没有单一价格字段可指。

单位实测锚点（README 要求 unit 必须实测，不能猜）：

    cogview-4          $0.01  == 智谱官方 $0.01
    grok-imagine-image $0.02  == xAI 官方 $0.02
    seedream-v4.5      $0.04  == DeepInfra $0.04

三个独立锚点都精确吻合，确认原值就是**美元/张**，无需换算。
（gpt-image-1 报 $0.2839 而 OpenAI 官方最高档是 $0.167——那是转售加价，
不是单位错：若单位错，上面三个锚点不可能同时对上。）
"""

from __future__ import annotations

import json

from ..normalize import infer_company
from ..records import PriceRecord, TIER_THIRDPARTY
from ..seller_urls import catalog_url_for

NANOGPT_URL = "https://nano-gpt.com/api/models"
NANOGPT_WEBLINK = "https://nano-gpt.com"

# 取哪个分辨率的价。同一模型不同分辨率价格可能差 4 倍
# （Ideogram V4 Instant：1024² $0.0075 vs 2048² $0.03），
# 所以必须固定一个基准，否则「最低价」会变成「最小图的价」。
# 选 1024×1024：DeepInfra 的按张单位就明确定义在 1024×1024
# （default_width/height），同尺寸比才是同口径。
# 分隔符两种写法都有（`1024x1024` 与 `1024*1024`）。
_BASELINE_KEYS = ("1024x1024", "1024*1024", "1024X1024")


def _baseline_price(cost: dict) -> tuple[float | None, str]:
    """返回 (基准价, 用的是哪个分辨率)。全为 0 的工具类条目返回 None。"""
    priced = {k: v for k, v in cost.items()
              if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0}
    if not priced:
        return None, ""
    for key in _BASELINE_KEYS:
        if key in priced:
            return float(priced[key]), key
    if "auto" in priced:
        return float(priced["auto"]), "auto"
    # 没有基准尺寸就取最便宜的那档，并把用的档位写进 snippet 供核对
    key = min(priced, key=lambda k: priced[k])
    return float(priced[key]), key


# 不采纳 nano-gpt 报价的模型。
#
# OpenAI 对这几个是**按画质分档**公布按张价的（gpt-image-1 的
# low/medium/high 分别是 $0.011 / $0.042 / $0.167），nano-gpt 只给一个不带
# 档位的平价。那条记录落进「无档位」组后，会在分档行旁边多出一行看不出
# 档位、却比 OpenAI 任何一档都贵的"基准价"（$0.2839），读表的人无从判断
# 它对应哪种画质。
#
# ⚠️ 只排除有官方分档价的这三个。gpt-image-2 官方不分档，nano-gpt 的报价
# 会正常并入已有的无档位组、正常参与比价，不在此列。
_SKIP_MODEL_IDS = {
    "gpt-image-1",
    "gpt-image-1-mini",
    "gpt-image-1.5",
}


def _leaves(node, path=""):
    """image 下是多层嵌套，带 ``cost`` 的才是模型。"""
    if not isinstance(node, dict):
        return
    if "cost" in node:
        yield path, node
        return
    for key, value in node.items():
        yield from _leaves(value, f"{path}/{key}" if path else key)


def parse_nanogpt(
    text: str, *, source_url: str, fetched_at: str, source_version: str | None
) -> tuple[list[PriceRecord], list[str]]:
    records: list[PriceRecord] = []
    warnings: list[str] = []
    try:
        payload = json.loads(text or "")
    except json.JSONDecodeError:
        return [], ["nanogpt: 返回不是合法 JSON"]

    images = ((payload.get("models") or {}).get("image")
              if isinstance(payload, dict) else None)
    if not isinstance(images, dict):
        return [], ["nanogpt: 找不到 models.image，结构可能已变"]

    dropped = 0
    for path, node in _leaves(images):
        cost = node.get("cost")
        if not isinstance(cost, dict):
            continue
        price, resolution = _baseline_price(cost)
        if price is None:
            continue
        # ``model`` 是全路径（`pruna-ai/p-image/text-to-image`），叶子名常是
        # `text-to-image` / `edit` 这种通用词——**绝不能拿叶子名当标识**，
        # 否则同一个 `text-to-image` 会同时"命中" Cosmos3、MAI-Image、
        # Muse Image 等好几个毫不相干的模型。
        model_id = str(node.get("model") or path)
        if model_id in _SKIP_MODEL_IDS:
            continue
        company = infer_company(model_id) or infer_company(str(node.get("name") or ""))
        if not company:
            # 归属推断不出就丢弃，与 collect_price_apis 同一套纪律：
            # 归错公司等于拆掉跨公司误配的防线。
            dropped += 1
            continue
        records.append(PriceRecord(
            source="nanogpt",
            source_tier=TIER_THIRDPARTY,
            is_official=False,
            source_url=source_url,
            fetched_at=fetched_at,
            source_snippet=(
                f"{node.get('name') or model_id} | {model_id} | "
                f"${price} @ {resolution}"),
            unit_original="USD per image",
            model_id=model_id,
            provider="nanogpt",
            per_image=price,
            currency="USD",
            source_version=source_version,
            raw={
                "company": company,
                "weblink": NANOGPT_WEBLINK,
                "provider_name": "nano-gpt",
                "seller": "nano-gpt",
                "seller_url": catalog_url_for("nano-gpt") or NANOGPT_WEBLINK,
            },
        ))

    if dropped:
        warnings.append(f"nanogpt: {dropped} 条无法归属公司已丢弃（不硬塞）")
    if not records:
        warnings.append("nanogpt: 未解析到任何按张价，结构可能已变")
    return records, warnings
