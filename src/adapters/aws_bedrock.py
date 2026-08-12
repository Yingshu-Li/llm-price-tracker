"""Tier 1b：AWS Bedrock 官方价格表。

AWS Price List Bulk API 无需认证。两个 offer 要分别处理，结构完全不同：

  AmazonBedrock                  1014 行，有 Model/Provider/Inference Type 结构化列
  AmazonBedrockFoundationModels   374 行，只有 serviceName + usageType 两个字符串

必须用 regional CSV（~460KB / ~140KB），不要下 15MB 的全量 JSON。

⚠️ 三个会静默出错的坑：
  1. `Unit` 大多是 **1K tokens** 而非 1M——不换算就差 1000 倍
  2. `service_tier` 列不可信（只有 1/3 有值，且与 Inference Type 矛盾），
     层级必须从 `Inference Type` 文本解析
  3. Provider 命名有重复（Mistral/Mistral AI、Moonshot AI/Kimi AI）

关于 is_official：AWS 发布的是**它自己转售**这些模型的价格。只有 Amazon 自家
的 Nova/Titan 才算"厂商牌价"，第三方模型在 Bedrock 上的价是托管价。
source_tier 仍是 1（AWS 价格表本身是权威官方源），但 is_official=False。
"""

from __future__ import annotations

import csv
import io
import re

from ..records import TIER_OFFICIAL, PriceRecord

BASE = "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws"
OFFERS = ("AmazonBedrock", "AmazonBedrockFoundationModels")
DEFAULT_REGION = "us-east-1"


def offer_csv_url(offer: str, region: str = DEFAULT_REGION) -> str:
    return f"{BASE}/{offer}/current/{region}/index.csv"


# 单位 → 换算成「每 100 万」的乘数。故意穷举而非模糊匹配：
# 未知单位宁可跳过，也不猜——猜错就是几个数量级的静默错误。
_TOKEN_UNIT_MULTIPLIER = {
    "1k tokens": 1000.0,
    "1m tokens": 1.0,
}

# Bedrock 的 Provider / serviceName 前缀 → raw.csv 的 Company 值。
# 匹配层按公司限定范围，映射错了会导致整批对不上。
_PROVIDER_TO_COMPANY = {
    "anthropic": "Anthropic",
    "mistral": "Mistral AI",
    "mistral ai": "Mistral AI",
    "qwen": "Alibaba / Qwen",
    "alibaba": "Alibaba / Qwen",
    "google": "Google",
    "nvidia": "NVIDIA",
    "openai": "OpenAI",
    "z ai": "Zhipu AI / GLM",
    "zai": "Zhipu AI / GLM",
    "minimax ai": "MiniMax",
    "minimax": "MiniMax",
    "meta": "Meta",
    "deepseek": "DeepSeek",
    "moonshot ai": "Moonshot AI / Kimi",
    "kimi ai": "Moonshot AI / Kimi",
    "amazon": "Amazon / Nova",
    "cohere": "Cohere",
    "ai21": "AI21 Labs",
    "ai21 labs": "AI21 Labs",
    "stability ai": None,  # 不在 raw.csv 里
    "writer": None,
    "twelvelabs": None,
    "luma ai": None,
}

# FoundationModels 只有 serviceName，得从名字前缀推断厂商。
# 顺序有意义：先匹配到的胜出，所以更长更具体的前缀要排在前面。
_NAME_PREFIX_TO_COMPANY = (
    ("claude", "Anthropic"),
    ("meta llama", "Meta"),
    ("llama", "Meta"),
    ("cohere", "Cohere"),
    ("command", "Cohere"),
    ("embed", "Cohere"),
    ("rerank", "Cohere"),
    ("jamba", "AI21 Labs"),
    ("jurassic", "AI21 Labs"),
    ("ai21", "AI21 Labs"),
    ("mistral", "Mistral AI"),
    ("mixtral", "Mistral AI"),
    ("ministral", "Mistral AI"),
    ("pixtral", "Mistral AI"),
    ("magistral", "Mistral AI"),
    ("devstral", "Mistral AI"),
    ("nova", "Amazon / Nova"),
    ("titan", "Amazon / Nova"),
    ("amazon", "Amazon / Nova"),
    ("deepseek", "DeepSeek"),
    ("qwen", "Alibaba / Qwen"),
    ("glm", "Zhipu AI / GLM"),
    ("kimi", "Moonshot AI / Kimi"),
    ("minimax", "MiniMax"),
    ("nemotron", "NVIDIA"),
    ("gpt-oss", "OpenAI"),
)

_EDITION_SUFFIX = re.compile(r"\s*\(Amazon Bedrock Edition\)\s*$", re.I)

# Inference Type / usageType → (价格字段, 服务层级)
# 大小写不一致（"Output tokens flex" vs "output tokens batch"），一律小写后匹配。
# ⚠️ 不能用 \binput\b：`InputTokenCount` 里 "Input" 后面紧跟 "T"，没有词边界，
#    曾因此静默丢掉 127 行（Cohere / AI21 / Llama 整批消失）。
#    缓存模式必须排在前面——`CacheReadInputTokenCount` 也含 "Input"。
_DIRECTION_PATTERNS = (
    (re.compile(r"cache.*read", re.I), "cache_read_per_1m"),
    (re.compile(r"cache.*write.*1h|cache_write_tokens_1h", re.I), "cache_write_1h_per_1m"),
    (re.compile(r"cache.*write", re.I), "cache_write_per_1m"),
    (re.compile(r"input", re.I), "input_per_1m"),
    (re.compile(r"output", re.I), "output_per_1m"),
)

_TIER_PATTERNS = (
    (re.compile(r"\bbatch\b", re.I), "batch"),
    (re.compile(r"\bflex\b", re.I), "flex"),
    (re.compile(r"\bpriority\b", re.I), "priority"),
    (re.compile(r"latencyoptimized|latency.optimized", re.I), "fast"),
)


def _classify(text: str) -> tuple[str | None, str]:
    """从 Inference Type / usageType 文本里解出 (价格字段, 服务层级)。"""
    field = None
    for pattern, name in _DIRECTION_PATTERNS:
        if pattern.search(text):
            field = name
            break
    tier = "standard"
    for pattern, name in _TIER_PATTERNS:
        if pattern.search(text):
            tier = name
            break
    return field, tier


def _company_from_provider(provider: str, model: str) -> str | None:
    if provider:
        mapped = _PROVIDER_TO_COMPANY.get(provider.strip().lower(), "__miss__")
        if mapped != "__miss__":
            return mapped
    return _company_from_name(model)


def _company_from_name(name: str) -> str | None:
    low = (name or "").strip().lower()
    for prefix, company in _NAME_PREFIX_TO_COMPANY:
        if low.startswith(prefix):
            return company
    return None


def _read_price_csv(text: str) -> tuple[list[dict], str]:
    """AWS 的 CSV 前面有 5 行元数据，表头从 `"SKU"` 那行开始。

    同时把 Version 抓出来当 source_version——这是 AWS 自己的价格表版本号，
    比 ETag 更能说明"这是哪一版价格"。
    """
    lines = text.splitlines()
    version = ""
    header_index = None
    for index, line in enumerate(lines):
        if line.startswith('"Version"'):
            parts = next(csv.reader([line]), [])
            if len(parts) > 1:
                version = parts[1]
        if line.startswith('"SKU"'):
            header_index = index
            break
    if header_index is None:
        return [], version
    reader = csv.DictReader(io.StringIO("\n".join(lines[header_index:])))
    return list(reader), version


def parse_offer(
    text: str,
    *,
    offer: str,
    source_url: str,
    fetched_at: str,
    source_version: str | None,
    region: str = DEFAULT_REGION,
) -> list[PriceRecord]:
    rows, file_version = _read_price_csv(text)
    version = file_version or source_version
    records: list[PriceRecord] = []

    for row in rows:
        if row.get("TermType") != "OnDemand":
            continue

        unit = (row.get("Unit") or "").strip().lower()
        multiplier = _TOKEN_UNIT_MULTIPLIER.get(unit)
        if multiplier is None:
            # 非 token 计价（image / hour / Model-month 等）本轮不收，
            # 但不静默——调用方可以从返回条数与总行数的差看出来。
            continue

        try:
            price = float(row.get("PricePerUnit") or "")
        except ValueError:
            continue
        if price <= 0:
            # AWS 用 0 表示"该档不适用"，不是真的免费
            continue

        if offer == "AmazonBedrock":
            model = (row.get("Model") or "").strip()
            provider = (row.get("Provider") or "").strip()
            # ⚠️ 用 Inference Type 而不是 service_tier 列：后者只有 1/3 有值，
            #    且与 Inference Type 矛盾（见模块开头说明）
            classifier = (row.get("Inference Type") or "") + " " + (
                row.get("usageType") or ""
            )
            if not model:
                continue
            company = _company_from_provider(provider, model)
        else:
            model = _EDITION_SUFFIX.sub("", row.get("serviceName") or "").strip()
            provider = ""
            classifier = row.get("usageType") or ""
            if not model or model.lower() == "amazon bedrock":
                continue
            company = _company_from_name(model)

        if not company:
            continue

        field, tier = _classify(classifier)
        if field is None:
            continue

        # PriceDescription 是 AWS 自己写的完整人类可读描述，
        # 例如 "$0.0011 per 1K output tokens batch for GLM 4.7 in US East (N. Virginia)"
        # 拿它当溯源片段比拼一行 CSV 更好核对
        snippet = (row.get("PriceDescription") or "").strip() or (
            f"{model} | {classifier} | {row.get('Unit')} | {row.get('PricePerUnit')}"
        )

        records.append(
            PriceRecord(
                source=f"aws_{offer}",
                source_tier=TIER_OFFICIAL,
                # AWS 卖的是转售服务；只有 Amazon 自家模型才算厂商牌价
                is_official=(company == "Amazon / Nova"),
                source_url=source_url,
                fetched_at=fetched_at,
                source_snippet=snippet,
                unit_original=row.get("Unit") or unit,
                source_version=version,
                model_id=model,
                provider=f"aws-bedrock/{region}",
                service_tier=tier,
                raw={
                    "company": company,
                    "offer": offer,
                    "region": region,
                    "sku": row.get("SKU"),
                    "usage_type": row.get("usageType"),
                    "upstream_provider": provider,
                },
                **{field: price * multiplier},
            )
        )

    return records
