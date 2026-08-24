"""厂商官网中以本币计价、且不是通用 Markdown 表格的价格页。"""

from __future__ import annotations

import html
import re

from ..records import PriceRecord, TIER_OFFICIAL

BAICHUAN_URL = "https://platform.baichuan-ai.com/prices"

_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_PRICE_CNY_PER_1K = re.compile(
    r"([0-9]+(?:\.[0-9]+)?)\s*元\s*/\s*千\s*tokens", re.I
)


def _plain(fragment: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", fragment)).split())


def parse_baichuan(
    text: str, *, source_url: str, fetched_at: str, source_version: str | None
) -> tuple[list[PriceRecord], list[str]]:
    """解析仍在售的 Baichuan2-Turbo 人民币合并 token 单价。

    官网明确写“包含输入和输出”，因此输入和输出各写同一个单位价；调用成本
    仍应按实际输入 token + 输出 token 分别乘价后相加。
    已下线并被路由到其他模型的 Baichuan2-Turbo-192k 不生成价格。
    """
    records: list[PriceRecord] = []
    warnings: list[str] = []
    for fragment in _ROW.findall(text):
        snippet = _plain(fragment)
        if not re.search(r"模型调用\s+Baichuan2-Turbo(?:\s|$)", snippet):
            continue
        match = _PRICE_CNY_PER_1K.search(snippet)
        if not match or "包含输入和输出" not in snippet:
            warnings.append("baichuan_official_html: 找到 Baichuan2-Turbo 行但无法识别价格")
            continue
        per_1m = float(match.group(1)) * 1_000
        records.append(
            PriceRecord(
                source="baichuan_official_html",
                source_tier=TIER_OFFICIAL,
                is_official=True,
                source_url=source_url,
                fetched_at=fetched_at,
                source_snippet=snippet,
                unit_original="CNY per 1K tokens（包含输入和输出）",
                model_id="Baichuan2-Turbo",
                provider="baichuan",
                input_per_1m=per_1m,
                output_per_1m=per_1m,
                context_length=32_000,
                currency="CNY",
                source_version=source_version,
            )
        )
    if not records and not warnings:
        warnings.append("baichuan_official_html: 官方价格页未找到 Baichuan2-Turbo")
    return records, warnings
