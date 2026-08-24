"""解析 302.AI 的公开价格表。

价格页是 Astro 生成的静态 HTML；同一张表用并行 list 表示模型名和各渠道报价。
这里仅读取表内明确标为 ``302.AI`` 的美元价格，不使用页面展示的换算币种。
"""

from __future__ import annotations

import html
import json
import re

from ..records import PriceRecord, TIER_THIRDPARTY

PRICE_URL = "https://price.302.ai/en/pricing_website/"
WEBLINK = "https://302.ai/price"

_TABLE = re.compile(
    r'\[\{"list":\["China AI Model"(?P<models>.*?)\]\},'
    r'\{"list":\["302\.AI"(?P<prices>.*?)\]\}',
    re.S,
)
_USD = re.compile(
    r"Input:\s*.*?pricing-en[^>]*>\$([0-9]+(?:\.[0-9]+)?)</span>"
    r".*?Output:\s*.*?pricing-en[^>]*>\$([0-9]+(?:\.[0-9]+)?)</span>",
    re.I | re.S,
)
_TAG = re.compile(r"<[^>]+>")

# 302.AI 自己的讯飞接口文档明确支持这三个 id。只收白名单，避免表格中其他
# 中国模型被错误归到 iFLYTEK。
IFLYTEK_IDS = {"general", "generalv3.5", "4.0Ultra"}


def _decode_escaped_list(first: str, tail: str) -> list[str]:
    """解开 Astro 页面内嵌 JSON 中的一列。"""
    value = json.loads(f'["{first}"{tail}]')
    if not isinstance(value, list):
        raise ValueError("302.AI 价格表 list 不是数组")
    return [str(item) for item in value]


def _plain(value: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", value)).split())


def parse_prices(
    text: str, *, source_url: str, fetched_at: str, source_version: str | None
) -> tuple[list[PriceRecord], list[str]]:
    decoded_html = html.unescape(text)
    matches = list(_TABLE.finditer(decoded_html))
    if not matches:
        return [], ["ai302_html: 未找到 China AI Model / 302.AI 价格表"]
    records: list[PriceRecord] = []
    warnings: list[str] = []
    found: dict[str, tuple[float, float, str]] = {}
    for match in matches:
        try:
            models = _decode_escaped_list("China AI Model", match.group("models"))
            prices = _decode_escaped_list("302.AI", match.group("prices"))
        except (json.JSONDecodeError, ValueError) as exc:
            warnings.append(f"ai302_html: 某个价格分区 JSON 解析失败：{exc}")
            continue
        if len(models) != len(prices):
            warnings.append(
                f"ai302_html: 某分区模型列与价格列长度不同"
                f"（{len(models)} != {len(prices)}）"
            )
            continue
        for model_id, price_html in zip(models[1:], prices[1:]):
            if model_id not in IFLYTEK_IDS:
                continue
            price_match = _USD.search(price_html)
            if not price_match:
                warnings.append(f"ai302_html: {model_id} 未识别出 USD 输入/输出价")
                continue
            quote = (*map(float, price_match.groups()), price_html)
            previous = found.get(model_id)
            if previous and previous[:2] != quote[:2]:
                warnings.append(
                    f"ai302_html: {model_id} 在不同分区报价冲突："
                    f"{previous[:2]} vs {quote[:2]}（保留首次）"
                )
                continue
            found.setdefault(model_id, quote)

    for model_id, (input_price, output_price, price_html) in found.items():
        records.append(
            PriceRecord(
                source="ai302_html",
                source_tier=TIER_THIRDPARTY,
                is_official=False,
                source_url=source_url,
                fetched_at=fetched_at,
                source_snippet=f"{model_id} | {_plain(price_html)}",
                unit_original="USD per 1M tokens",
                model_id=model_id,
                provider="302ai",
                input_per_1m=input_price,
                output_per_1m=output_price,
                currency="USD",
                source_version=source_version,
                raw={
                    "company": "iFLYTEK",
                    "seller": "302ai",
                    "seller_url": WEBLINK,
                    "provider_name": "302.AI",
                    "weblink": WEBLINK,
                },
            )
        )
    if not records and not warnings:
        warnings.append("ai302_html: 价格表没有可用的讯飞模型报价")
    return records, warnings
