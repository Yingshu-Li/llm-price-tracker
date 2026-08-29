"""中国厂商官方 token 价目表适配器。

StepFun、百度千帆、腾讯 TokenHub 的页面都直接包含 HTML 表格，可以每日抓取。
火山引擎与 DeepSeek 的公开页在部分网络环境中只返回 JS 壳或证书链失败，
这两家暂由 ``verified_official_prices.yaml`` 保存人工核验快照；快照仍保留官方
URL、原文摘录和核验日期，不能伪装成实时抓取结果。
"""

from __future__ import annotations

import html
import re
from pathlib import Path

import yaml

from ..records import PriceRecord, TIER_OFFICIAL

STEPFUN_URL = "https://platform.stepfun.com/docs/zh/guides/pricing/details"
BAIDU_URL = "https://cloud.baidu.com/doc/qianfan/s/wmh4sv6ya"
TENCENT_URL = "https://cloud.tencent.com/document/product/1823/130055"

_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.I | re.S)
_CELL = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_NUMBER = re.compile(r"(?<![\w.])([0-9]+(?:\.[0-9]+)?)")


def _plain(fragment: str) -> str:
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    return "\n".join(
        " ".join(part.split())
        for part in html.unescape(_TAG.sub(" ", fragment)).splitlines()
        if part.strip()
    )


def _rows(fragment: str) -> list[list[str]]:
    return [[_plain(cell) for cell in _CELL.findall(row)] for row in _ROW.findall(fragment)]


def _number(value: str) -> float | None:
    match = _NUMBER.search(value or "")
    return float(match.group(1)) if match else None


def _record(
    *, source: str, source_url: str, fetched_at: str,
    source_version: str | None, snippet: str, model_id: str,
    provider: str, unit: str, currency: str = "CNY",
    input_price: float | None = None, output_price: float | None = None,
    cache_price: float | None = None, qualifier: str | None = None,
    context_length: int | None = None,
    per_image: float | None = None, per_video: float | None = None,
    per_second: float | None = None, per_frame: float | None = None,
    per_call: float | None = None,
    service_tier: str = "standard",
) -> PriceRecord:
    return PriceRecord(
        source=source,
        source_tier=TIER_OFFICIAL,
        is_official=True,
        source_url=source_url,
        fetched_at=fetched_at,
        source_snippet=snippet[:500],
        unit_original=unit,
        model_id=model_id,
        provider=provider,
        input_per_1m=input_price,
        output_per_1m=output_price,
        cache_read_per_1m=cache_price,
        per_image=per_image,
        per_video=per_video,
        per_second=per_second,
        per_frame=per_frame,
        per_call=per_call,
        qualifier=qualifier,
        context_length=context_length,
        service_tier=service_tier,
        currency=currency,
        source_version=source_version,
    )


def parse_stepfun(
    text: str, *, source_url: str, fetched_at: str, source_version: str | None
) -> tuple[list[PriceRecord], list[str]]:
    """解析 ``模型 / 1M tokens / 输入 / 缓存 / 输出`` 的官方表格。"""
    records: list[PriceRecord] = []
    for cells in _rows(text):
        if len(cells) < 5 or not cells[0].startswith("step-"):
            continue
        if "1M" not in cells[1] or "token" not in cells[1].lower():
            continue
        prices = [_number(cell) for cell in cells[2:5]]
        if any(price is None for price in prices):
            continue
        records.append(_record(
            source="stepfun_official_html",
            source_url=source_url,
            fetched_at=fetched_at,
            source_version=source_version,
            snippet=" | ".join(cells),
            model_id=cells[0],
            provider="stepfun",
            unit="CNY per 1M tokens",
            input_price=prices[0],
            cache_price=prices[1],
            output_price=prices[2],
        ))
    warnings = [] if records else ["stepfun_official_html: 未找到 1M token 定价行"]
    return records, warnings


def _first_table_after(text: str, marker: str) -> str:
    start = text.find(marker)
    if start < 0:
        return ""
    table_start = text.find("<table", start)
    table_end = text.find("</table>", table_start)
    if table_start < 0 or table_end < 0:
        return ""
    return text[table_start: table_end + len("</table>")]


_BAIDU_MODEL = re.compile(r"\b(?:ERNIE-[A-Za-z0-9.]+(?:-[A-Za-z0-9.]+)*|Embedding-V1)\b")


def _baidu_qualifier(label: str) -> str | None:
    """把官网括号内的输入长度条件规范成与 GPT/Grok 相同的一档一行标签。"""
    match = re.search(r"[（(]([^）)]+)[）)]", label)
    if not match:
        return None
    condition = re.sub(r"\s+", "", match.group(1)).lower()
    if match := re.fullmatch(r"输入(?:<=|=<)(\d+)k", condition):
        return f"input tokens ≤ {match.group(1)}K"
    if match := re.fullmatch(r"(\d+)k<输入(?:<=|=<)(\d+)k", condition):
        return f"{match.group(1)}K < input tokens ≤ {match.group(2)}K"
    return None


def _baidu_context_length(model_id: str) -> int | None:
    suffix = re.search(r"-(32|128)K(?:-|$)", model_id, re.I)
    if suffix:
        return int(suffix.group(1)) * 1_000
    if model_id.startswith(("ERNIE-5.1", "ERNIE-5.0")):
        return 128_000
    if model_id in {"ERNIE-4.5-Turbo-20260402", "ERNIE-4.5-Turbo-VL"}:
        return 128_000
    return None


def _parse_baidu_table(
    table: str,
) -> list[tuple[str, float | None, float | None, float | None, str | None, int | None, str]]:
    current: list[str] = []
    prices: dict[tuple[str, str | None], dict[str, float]] = {}
    snippets: dict[tuple[str, str | None], list[str]] = {}
    for cells in _rows(table):
        found: list[str] = []
        for cell in cells:
            found.extend(_BAIDU_MODEL.findall(cell))
        if found:
            current = list(dict.fromkeys(found))
        if not current:
            continue

        label_idx = next((i for i, cell in enumerate(cells)
                          if cell.startswith(("输入", "输出", "命中缓存"))), None)
        if label_idx is None:
            continue
        price = next((_number(cell) for cell in cells[label_idx + 1:]
                      if _number(cell) is not None), None)
        if price is None:
            continue
        label = cells[label_idx]
        qualifier = _baidu_qualifier(label)
        field = "cache" if label.startswith("命中缓存") else (
            "input" if label.startswith("输入") else "output"
        )
        for model_id in current:
            key = (model_id, qualifier)
            prices.setdefault(key, {}).setdefault(field, price * 1_000)
            snippets.setdefault(key, []).append(" | ".join(cells))

    out = []
    for (model_id, qualifier), values in prices.items():
        if not (values.get("input") is not None or values.get("output") is not None):
            continue
        out.append((
            model_id, values.get("input"), values.get("output"), values.get("cache"),
            qualifier, _baidu_context_length(model_id),
            " ; ".join(snippets[(model_id, qualifier)]),
        ))
    return out


def parse_baidu(
    text: str, *, source_url: str, fetched_at: str, source_version: str | None
) -> tuple[list[PriceRecord], list[str]]:
    """解析千帆按量后付费的文本、视觉、推理与向量首档价格。"""
    tables = [
        _first_table_after(text, '<h4 id="按量后付费">'),
        _first_table_after(text, '<h4 id="按量后付费-1">'),
        _first_table_after(text, '<h4 id="按量后付费-2">'),
        _first_table_after(text, '<h3 id="文本向量">'),
    ]
    merged: dict[
        tuple[str, str | None],
        tuple[float | None, float | None, float | None, int | None, str],
    ] = {}
    for table in tables:
        for (
            model_id, input_price, output_price, cache_price,
            qualifier, context_length, snippet,
        ) in _parse_baidu_table(table):
            merged.setdefault(
                (model_id, qualifier),
                (input_price, output_price, cache_price, context_length, snippet),
            )

    records = [
        _record(
            source="baidu_qianfan_official_html",
            source_url=source_url,
            fetched_at=fetched_at,
            source_version=source_version,
            snippet=snippet,
            model_id=model_id,
            provider="baidu-qianfan",
            unit="CNY per 1K tokens; converted to per 1M before FX",
            input_price=input_price,
            output_price=output_price,
            cache_price=cache_price,
            qualifier=qualifier,
            context_length=context_length,
        )
        for (model_id, qualifier), (
            input_price, output_price, cache_price, context_length, snippet,
        ) in merged.items()
    ]
    warnings = [] if records else ["baidu_qianfan_official_html: 未找到按量 token 定价"]
    return records, warnings


def parse_tencent(
    text: str, *, source_url: str, fetched_at: str, source_version: str | None
) -> tuple[list[PriceRecord], list[str]]:
    """解析 TokenHub 当前稳定 Hy3 与全部在售 HY 多模态理解模型。"""
    multimodal = {
        "hy-vision-2.0-instruct": ("hy-vision-2.0-instruct", 44_000),
        "hy-vision-1.5-thinking": ("hunyuan-t1-vision-20250916", 40_000),
        "hy-vision-video": ("hunyuan-turbos-vision-video-20250728", 32_000),
    }
    records: list[PriceRecord] = []
    seen: set[str] = set()
    for cells in _rows(text):
        model = cells[0].strip().lower() if cells else ""
        if len(cells) >= 6 and model == "hy3" and "Hy3" not in seen:
            # 模型、条件、峰谷、输入、输出、缓存。
            values = [_number(cell) for cell in cells[3:6]]
            if values[0] is not None and values[1] is not None:
                records.append(_record(
                    source="tencent_tokenhub_official_html",
                    source_url=source_url,
                    fetched_at=fetched_at,
                    source_version=source_version,
                    snippet=" | ".join(cells),
                    model_id="Hy3",
                    provider="tencent-tokenhub",
                    unit="CNY per 1M tokens",
                    input_price=values[0],
                    output_price=values[1],
                    cache_price=values[2],
                ))
                seen.add("Hy3")
        elif len(cells) >= 3 and model in multimodal:
            # 多模态理解表：模型、输入、输出。
            model_id, context_length = multimodal[model]
            if model_id in seen:
                continue
            values = [_number(cell) for cell in cells[1:3]]
            if values[0] is not None and values[1] is not None:
                records.append(_record(
                    source="tencent_tokenhub_official_html",
                    source_url=source_url,
                    fetched_at=fetched_at,
                    source_version=source_version,
                    snippet=" | ".join(cells),
                    model_id=model_id,
                    provider="tencent-tokenhub",
                    unit="CNY per 1M tokens",
                    input_price=values[0],
                    output_price=values[1],
                    context_length=context_length,
                ))
                seen.add(model_id)
    found = {record.model_id for record in records}
    warnings = []
    if "Hy3" not in found:
        warnings.append("tencent_tokenhub_official_html: 未找到稳定版 Hy3 定价")
    for label, (model_id, _context_length) in multimodal.items():
        if model_id not in found:
            warnings.append(
                "tencent_tokenhub_official_html: 未找到 "
                f"{label.upper()} 定价"
            )
    return records, warnings


def load_verified_snapshots(path: Path) -> tuple[list[PriceRecord], list[str], list[dict]]:
    """载入无法稳定服务端渲染页面的人工核验价格快照。"""
    cfg = yaml.safe_load(path.read_text("utf-8")) or {}
    records: list[PriceRecord] = []
    fetches: list[dict] = []
    warnings: list[str] = []
    grouped: dict[str, int] = {}
    sources = {item["id"]: item for item in cfg.get("sources", [])}
    for item in cfg.get("prices", []):
        source = sources[item["source"]]
        records.append(_record(
            source=source["id"],
            source_url=source["url"],
            fetched_at=source["verified_at"],
            source_version=f"verified-{source['verified_at']}",
            snippet=item["snippet"],
            model_id=item["model_id"],
            provider=source["provider"],
            unit=item.get("unit", "USD per 1M tokens"),
            currency=item.get("currency", "USD"),
            input_price=item.get("input_per_1m"),
            output_price=item.get("output_per_1m"),
            cache_price=item.get("cache_read_per_1m"),
            per_image=item.get("per_image"),
            per_video=item.get("per_video"),
            per_second=item.get("per_second"),
            per_frame=item.get("per_frame"),
            per_call=item.get("per_call"),
            qualifier=item.get("qualifier"),
            service_tier=item.get("service_tier", "standard"),
        ))
        grouped[source["id"]] = grouped.get(source["id"], 0) + 1
    for source_id, count in grouped.items():
        source = sources[source_id]
        fetches.append({
            "source": source_id,
            "url": source["url"],
            "ok": True,
            "status": None,
            "version": f"verified-{source['verified_at']}",
            "fetched_at": source["verified_at"],
            "from_cache": True,
            "error": None,
            "error_kind": None,
            "n_records": count,
            "provider_name": source["name"],
            "weblink": source["url"],
            "license": source.get("license", "厂商官方价格页（人工核验快照）"),
        })
        warnings.append(
            f"{source_id}: 价格页无法稳定服务端渲染，当前使用 {source['verified_at']} "
            "人工核验快照；URL 与原文摘录已保留"
        )
    return records, warnings, fetches
