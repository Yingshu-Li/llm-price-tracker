"""Upstage 官方 API 价格页。"""

from __future__ import annotations

import html
import re

from ..records import PriceRecord, TIER_OFFICIAL

PRICE_URL = "https://www.upstage.ai/pricing/api"
WEBLINK = PRICE_URL

_TAG = re.compile(r"<[^>]+>")
_EMBED_PRICE = re.compile(
    r"\bEmbed\s+Embedding model\b.*?"
    r"\$\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*1M\s*tokens\s+Embed\s+2\b",
    re.I | re.S,
)


def _plain(text: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", text)).split())


def parse_prices(
    text: str, *, source_url: str, fetched_at: str, source_version: str | None
) -> tuple[list[PriceRecord], list[str]]:
    """解析仍在售的第一代 Upstage Embed 每百万 token 美元价。

    用后继产品 ``Embed 2`` 作为边界，避免误取前面的 Solar Mini 或后面的
    Embed 2 价格。官网的 EOL 提示保留在 source_snippet 中供人工核对。
    """
    plain = _plain(text)
    match = _EMBED_PRICE.search(plain)
    if not match:
        return [], ["upstage_official_html: 官方价格页未找到 Embed 的 1M token 价格"]

    price = float(match.group(1))
    start = max(0, match.start() - 80)
    end = min(len(plain), match.end() + 20)
    record = PriceRecord(
        source="upstage_official_html",
        source_tier=TIER_OFFICIAL,
        is_official=True,
        source_url=source_url,
        fetched_at=fetched_at,
        source_snippet=plain[start:end],
        unit_original="USD per 1M tokens",
        model_id="Upstage Embed",
        provider="upstage",
        input_per_1m=price,
        source_version=source_version,
        raw={"company": "Upstage"},
    )
    return [record], []
