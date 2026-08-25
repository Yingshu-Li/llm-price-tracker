"""解析商汤大装置官方计费、模型列表与 Token Plan 公测额度。"""

from __future__ import annotations

import html
import re

from ..records import PriceRecord, TIER_OFFICIAL

PRICE_URL = "https://www.sensecore.cn/help/docs/model-as-a-service/nova/pricing"
MODEL_LIST_URL = (
    "https://www.sensecore.cn/help/docs/model-as-a-service/nova/"
    "overview/compatible-mode"
)
TOKEN_PLAN_URL = "https://www.sensenova.cn/token-plan"
WEBLINK = "https://www.sensecore.cn/help/docs/model-as-a-service/nova/"

PRICING_TOKEN_MODELS = (
    "SenseNova-V6.5-Pro",
    "SenseNova-V6.5-Turbo",
    "SenseNova-V6-Pro",
    "SenseNova-V6-Turbo",
    "SenseNova-V6-Reasoner",
    "SenseChat-Vision",
    "SenseChat-Character-Pro",
    "SenseChat-Character",
)
OMNI_MODEL = "SenseNova-V6-Omni"
GENERAL_MODELS = (
    "SenseChat-5",
    "SenseChat",
    "SenseChat-Turbo",
    "SenseChat-5-Cantonese",
)
FREE_MODELS = (
    "SenseNova 6.8 Flash Lite",
    "SenseNova U1 Fast",
)

CONTEXT_LENGTHS = {
    "SenseNova-V6.5-Pro": 131_072,
    "SenseNova-V6.5-Turbo": 131_072,
    "SenseNova-V6-Pro": 32_768,
    "SenseNova-V6-Turbo": 32_768,
    "SenseNova-V6-Reasoner": 32_768,
    "SenseChat-Vision": 4_096,
    "SenseChat-Character-Pro": 32_768,
    "SenseChat-Character": 8_192,
    "SenseChat-5": 131_072,
    "SenseChat": 4_096,
    "SenseChat-5-Cantonese": 32_768,
}

_TAG = re.compile(r"<[^>]+>")
_INPUT_PER_1K = re.compile(
    r"输入\s*tokens?\s*([0-9]+(?:\.[0-9]+)?)\s*元\s*/\s*千\s*tokens?",
    re.I,
)
_OUTPUT_PER_1K = re.compile(
    r"输出\s*tokens?\s*([0-9]+(?:\.[0-9]+)?)\s*元\s*/\s*千\s*tokens?",
    re.I,
)
_COMBINED_PER_1K = re.compile(
    r"输入\s*tokens?\s*[、,/和及]\s*输出\s*tokens?\s*"
    r"([0-9]+(?:\.[0-9]+)?)\s*元\s*/\s*千\s*tokens?",
    re.I,
)
_ANY_PER_1K = re.compile(
    r"([0-9]+(?:\.[0-9]+)?)\s*元\s*/\s*千\s*tokens?", re.I
)
_PER_MINUTE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*元\s*/\s*分钟", re.I)


def _plain(text: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", text)).split())


def _model_pattern(model: str) -> re.Pattern[str]:
    # SenseChat-5 不能命中 SenseChat-5-Cantonese 的前缀。
    return re.compile(
        rf"(?<![A-Za-z0-9_.-]){re.escape(model)}(?![A-Za-z0-9_.-])", re.I
    )


def _model_segments(text: str, model: str, known: tuple[str, ...]) -> list[str]:
    """返回模型名到下一个已知模型名之间的片段，避免跨行误取价格。"""
    plain = _plain(text)
    segments: list[str] = []
    for match in _model_pattern(model).finditer(plain):
        end = len(plain)
        tail = plain[match.end():]
        for other in known:
            later = _model_pattern(other).search(tail)
            if later:
                end = min(end, match.end() + later.start())
        segments.append(plain[match.start(): min(end, match.start() + 900)])
    return segments


def _token_record(
    model: str,
    input_per_1k: float,
    output_per_1k: float,
    snippet: str,
    *,
    source: str,
    source_url: str,
    fetched_at: str,
    source_version: str | None,
) -> PriceRecord:
    return PriceRecord(
        source=source,
        source_tier=TIER_OFFICIAL,
        is_official=True,
        source_url=source_url,
        fetched_at=fetched_at,
        source_snippet=snippet,
        unit_original="CNY per 1K tokens",
        model_id=model,
        provider="sensenova",
        input_per_1m=input_per_1k * 1_000,
        output_per_1m=output_per_1k * 1_000,
        context_length=CONTEXT_LENGTHS.get(model),
        currency="CNY",
        source_version=source_version,
        raw={"official_model_name": model},
    )


def parse_prices(
    text: str, *, source_url: str, fetched_at: str, source_version: str | None
) -> tuple[list[PriceRecord], list[str]]:
    """解析当前计费页的 Token 模型和 V6 Omni 按分钟价。"""
    records: list[PriceRecord] = []
    warnings: list[str] = []
    known = (*PRICING_TOKEN_MODELS, OMNI_MODEL)

    for model in PRICING_TOKEN_MODELS:
        parsed = False
        for snippet in _model_segments(text, model, known):
            input_match = _INPUT_PER_1K.search(snippet)
            output_match = _OUTPUT_PER_1K.search(snippet)
            combined_match = _COMBINED_PER_1K.search(snippet)
            if input_match and output_match:
                input_price = float(input_match.group(1))
                output_price = float(output_match.group(1))
            elif combined_match:
                input_price = output_price = float(combined_match.group(1))
            else:
                continue
            records.append(
                _token_record(
                    model,
                    input_price,
                    output_price,
                    snippet,
                    source="sensenova_official_html",
                    source_url=source_url,
                    fetched_at=fetched_at,
                    source_version=source_version,
                )
            )
            parsed = True
            break
        if not parsed:
            warnings.append(f"sensenova_official_html: 未找到 {model} 的 Token 价格")

    omni_parsed = False
    for snippet in _model_segments(text, OMNI_MODEL, known):
        minute_match = _PER_MINUTE.search(snippet)
        if not minute_match:
            continue
        per_minute = float(minute_match.group(1))
        records.append(
            PriceRecord(
                source="sensenova_official_html",
                source_tier=TIER_OFFICIAL,
                is_official=True,
                source_url=source_url,
                fetched_at=fetched_at,
                source_snippet=snippet,
                unit_original="CNY per minute",
                model_id=OMNI_MODEL,
                provider="sensenova",
                per_second=per_minute / 60.0,
                modality="audio",
                currency="CNY",
                source_version=source_version,
                raw={
                    "official_model_name": OMNI_MODEL,
                    "original_per_minute": per_minute,
                },
            )
        )
        omni_parsed = True
        break
    if not omni_parsed:
        warnings.append(f"sensenova_official_html: 未找到 {OMNI_MODEL} 的分钟价")
    return records, warnings


def parse_model_list_prices(
    text: str, *, source_url: str, fetched_at: str, source_version: str | None
) -> tuple[list[PriceRecord], list[str]]:
    """解析兼容模式模型表中 4 个通用模型的人民币价格。"""
    records: list[PriceRecord] = []
    warnings: list[str] = []
    for model in GENERAL_MODELS:
        parsed = False
        for snippet in _model_segments(text, model, GENERAL_MODELS):
            prices = [float(value) for value in _ANY_PER_1K.findall(snippet)]
            if not prices:
                continue
            records.append(
                _token_record(
                    model,
                    prices[0],
                    prices[1] if len(prices) > 1 else prices[0],
                    snippet,
                    source="sensenova_model_list_html",
                    source_url=source_url,
                    fetched_at=fetched_at,
                    source_version=source_version,
                )
            )
            parsed = True
            break
        if not parsed:
            warnings.append(f"sensenova_model_list_html: 未找到 {model} 的 Token 价格")
    return records, warnings


def parse_token_plan(
    text: str, *, source_url: str
) -> tuple[dict[str, tuple[str, str]], list[str]]:
    """提取官方公测免费额度；它有调用限制，不作为无限量最低价。"""
    plain = _plain(text)
    if "公测" not in plain or "免费" not in plain:
        return {}, ["sensenova_token_plan: 页面不再声明免费公测"]

    free_tiers: dict[str, tuple[str, str]] = {}
    warnings: list[str] = []
    for model in FREE_MODELS:
        pattern = re.escape(model).replace(r"\ ", r"[\s-]+")
        if not re.search(pattern, plain, re.I):
            warnings.append(f"sensenova_token_plan: 免费方案未找到 {model}")
            continue
        free_tiers[model] = ("SenseNova Token Plan（公测）", source_url)
    return free_tiers, warnings
