"""图像生成厂商的官方按张价适配器。

两家的页面形态不同，但都能服务端抓取：

* **Black Forest Labs**：定价页内嵌 schema.org 的 ``Product``/``Offer``
  JSON-LD。这比抓 DOM 稳得多——单位写在 ``unitText`` 里，不用从
  "$0.04 / Image" 这类文案里猜。
* **Bria**：定价页是一张服务端渲染的价目表，"模型名" 与
  "$0.03 / Image" 分处相邻文本节点。

⚠️ **只收 unitText == "image" 的条目。** BFL 的 FLUX.2 全家（[pro]/[max]/
[flex]/[klein]）和一批编辑类模型按**兆像素**计价，FLUX 3 Video 按秒计价。
把兆像素折成"每张"必须先假定分辨率（1MP ≈ 1024×1024），那是编数据不是换算，
与 README 里"不做跨单位换算"的规则冲突。这些条目只记入告警，不进表。
"""

from __future__ import annotations

import json
import re

from ..records import PriceRecord, TIER_OFFICIAL

BFL_URL = "https://docs.bfl.ai/pricing"
BFL_WEBLINK = "https://bfl.ai"
BRIA_URL = "https://bria.ai/pricing"
BRIA_WEBLINK = "https://bria.ai"

_LD_JSON = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_SCRIPT_STYLE = re.compile(r"<script.*?</script>|<style.*?</style>", re.I | re.S)
# "$0.03 / Image"、"$0.03/ Image"、"$0.018/Image" 都要认
_PER_IMAGE = re.compile(r"^\$\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*image\s*$", re.I)

# 官网展示名 → raw.csv 的 Model 值。
# 依据是 BFL 官方 API endpoint（页面同源数据里的 apiEndpoint 字段）：
#   FLUX.1 Kontext [pro] → /v1/flux-kontext-pro
#   FLUX.1 Kontext [max] → /v1/flux-kontext-max
#   FLUX 1.1 [pro]       → /v1/flux-pro-1.1
#   FLUX 1.1 [pro] Ultra → /v1/flux-pro-1.1-ultra
# 展示名与 API id 词序相反（"1.1 [pro]" vs "pro-1.1"），规范化救不了，
# 只能人工对一次。没列在这里的（FLUX.1 [pro]、Fill [pro] 等）raw.csv 里
# 没有对应行，照常产出记录、匹配不上即忽略。
_BFL_MODEL_IDS = {
    "FLUX.1 Kontext [pro]": "flux-kontext-pro",
    "FLUX.1 Kontext [max]": "flux-kontext-max",
    "FLUX 1.1 [pro]": "flux-pro-1.1",
    "FLUX 1.1 [pro] Ultra": "flux-pro-1.1-ultra",
    "FLUX.1 [dev]": "black-forest-labs/FLUX.1-dev",
}

# Bria 价目表里只有 Fibo 家族；raw.csv 的 Bria/Bria-3.2 官网未列价，
# 因此不会有官方价——留空是正确结果，不是抓漏。
_BRIA_MODEL_IDS = {
    "Fibo": "briaai/FIBO",
    "Fibo Edit": "Bria/fibo_edit",
}


def _record(
    *, source: str, source_url: str, fetched_at: str,
    source_version: str | None, snippet: str, model_id: str,
    provider: str, company: str, weblink: str, per_image: float,
) -> PriceRecord:
    return PriceRecord(
        source=source,
        source_tier=TIER_OFFICIAL,
        is_official=True,
        source_url=source_url,
        fetched_at=fetched_at,
        source_snippet=snippet[:500],
        unit_original="USD per image",
        model_id=model_id,
        provider=provider,
        per_image=per_image,
        currency="USD",
        source_version=source_version,
        raw={
            "company": company,
            "weblink": weblink,
            "provider_name": provider,
            "seller": provider,
            "seller_url": weblink,
        },
    )


def _products(node):
    """深度遍历 JSON-LD，产出所有 Product 节点。"""
    if isinstance(node, dict):
        if node.get("@type") == "Product":
            yield node
        for value in node.values():
            yield from _products(value)
    elif isinstance(node, list):
        for value in node:
            yield from _products(value)


def parse_bfl(
    text: str, *, source_url: str, fetched_at: str, source_version: str | None
) -> tuple[list[PriceRecord], list[str]]:
    """BFL 定价页的 JSON-LD → 按张价记录。"""
    records: list[PriceRecord] = []
    warnings: list[str] = []
    seen: set[str] = set()
    other_units: dict[str, str] = {}

    for block in _LD_JSON.findall(text or ""):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            warnings.append("bfl_official: 有一个 ld+json 块无法解析，已跳过")
            continue
        for product in _products(data):
            name = (product.get("name") or "").strip()
            if not name or name in seen:
                continue
            offers = product.get("offers")
            offers = offers[0] if isinstance(offers, list) else offers
            if not isinstance(offers, dict):
                continue
            specs = offers.get("priceSpecification")
            specs = specs if isinstance(specs, list) else ([specs] if specs else [])
            per_image = None
            units = set()
            for spec in specs:
                if not isinstance(spec, dict):
                    continue
                unit = str(spec.get("unitText") or "").strip().lower()
                units.add(unit)
                if unit != "image":
                    continue
                value = spec.get("price")
                if isinstance(value, (int, float)) and value > 0:
                    # 同一产品出现多档按张价时取最低的那档，与最低价口径一致
                    per_image = value if per_image is None else min(per_image, value)
            if per_image is None:
                if units:
                    other_units[name] = "/".join(sorted(units))
                continue
            if str(offers.get("priceCurrency") or "USD").upper() != "USD":
                warnings.append(f"bfl_official: {name} 币种非 USD，已跳过")
                continue
            seen.add(name)
            records.append(_record(
                source="bfl_official",
                source_url=source_url,
                fetched_at=fetched_at,
                source_version=source_version,
                snippet=f"{name} | USD {per_image} per image (schema.org Offer)",
                model_id=_BFL_MODEL_IDS.get(name, name),
                provider="black-forest-labs",
                company="Black Forest Labs",
                weblink=BFL_WEBLINK,
                per_image=per_image,
            ))

    if other_units:
        listed = ", ".join(f"{k}({v})" for k, v in sorted(other_units.items()))
        warnings.append(
            "bfl_official: 以下条目不是按张计价，未纳入按张表（不做跨单位换算）："
            + listed
        )
    if not records:
        warnings.append("bfl_official: 未解析到任何按张价，页面结构可能已变")
    return records, warnings


def parse_bria(
    text: str, *, source_url: str, fetched_at: str, source_version: str | None
) -> tuple[list[PriceRecord], list[str]]:
    """Bria 价目表 → 按张价记录。

    表格扁平化后形如：
        Fibo
        $0.03 / Image
    价格行的**上一非空行**就是模型名。页面把整张表渲染了两遍，按名字去重。
    """
    body = _SCRIPT_STYLE.sub(" ", text or "")
    lines = [line.strip() for line in _TAG.sub("\n", body).split("\n")]
    lines = [line for line in lines if line]

    records: list[PriceRecord] = []
    warnings: list[str] = []
    seen: dict[str, float] = {}

    for index, line in enumerate(lines):
        match = _PER_IMAGE.match(line)
        if not match or index == 0:
            continue
        name = lines[index - 1].strip()
        price = float(match.group(1))
        if name in seen:
            if seen[name] != price:
                warnings.append(
                    f"bria_official: {name} 在页面上出现两个不同价格"
                    f"（{seen[name]} / {price}），已跳过")
                seen[name] = price
            continue
        seen[name] = price
        model_id = _BRIA_MODEL_IDS.get(name)
        if model_id is None:
            continue
        records.append(_record(
            source="bria_official",
            source_url=source_url,
            fetched_at=fetched_at,
            source_version=source_version,
            snippet=f"{name} | ${price} / Image",
            model_id=model_id,
            provider="bria",
            company="Bria",
            weblink=BRIA_WEBLINK,
            per_image=price,
        ))

    if not records:
        warnings.append("bria_official: 未解析到任何按张价，页面结构可能已变")
    return records, warnings
