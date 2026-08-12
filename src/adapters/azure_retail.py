"""Tier 1c：Azure Retail Prices API。

`https://prices.azure.com/api/retail/prices?$filter=serviceName eq 'Foundry Models'`
无需认证。⚠️ 过滤条件必须是 `'Foundry Models'`——`'Cognitive Services'` 和
`'Azure OpenAI'` 都返回 0（微软改过名）。

⚠️ 这个源的 meterName 是全项目最脏的数据：**没有模型 ID 字段**，同一概念有多种拼法

    输入   Inp / Inpt / Input / input
    输出   Outp / Opt / Output / output
    缓存   cd / cchd / cached
    部署   regnl / glbl / Gl / DZ / DZn / DZone / Dzone
    模型名 '5.4 nano' / 'GPT 5 Nano' / 'gpt 4.1 nano' / 'o3 mini 0131' / '5 mini'

因此本适配器**按 productName 白名单收窄**，只处理名字模式可靠的产品线，
其余一律跳过并上报。这是有意的取舍：

- Azure OpenAI / Grok 系列（占一半以上）**故意不收**。我们已有 OpenAI 和 xAI
  的厂商官方 .md，那才是牌价；Azure 只是转售价，收进来既冗余又更脏。
- 真正独有的价值是 **Microsoft 自家 Phi 系列**（raw.csv 里 10 个模型，
  没有任何其他来源）和 DeepSeek（官网被本机代理拦截时的替代路径）。
"""

from __future__ import annotations

import re
from urllib.parse import quote

from ..records import TIER_OFFICIAL, PriceRecord

API = "https://prices.azure.com/api/retail/prices"
SERVICE_NAME = "Foundry Models"
DEFAULT_REGION = "eastus"


def query_url(region: str = DEFAULT_REGION) -> str:
    flt = f"serviceName eq '{SERVICE_NAME}' and armRegionName eq '{region}'"
    return f"{API}?$filter={quote(flt)}"


# productName → raw.csv 的 Company。只列名字模式可靠的产品线。
# 不在这里的一律跳过并上报，不猜。
_PRODUCT_TO_COMPANY = {
    "Azure Phi Models": "Microsoft",
    "MAI Models": "Microsoft",
    "Azure Deepseek Models": "DeepSeek",
    "Azure Mistral Models": "Mistral AI",
    "Azure Llama Models": "Meta",
    "Azure Kimi": "Moonshot AI / Kimi",
    "Cohere Models": "Cohere",
    "Qwen models": "Alibaba / Qwen",
}

# 故意排除：已有厂商官方牌价，Azure 的转售价属于冗余且更脏
_DELIBERATELY_SKIPPED = {
    "Azure OpenAI",
    "Azure OpenAI GPT5",
    "Azure OpenAI Reasoning",
    "Azure OpenAI Media",
    "Azure OpenAI PP FT GPT4s",
    "Azure OpenAI OSS Models",
    "Azure OpenAI Embedding",
    "Azure Grok Models",
}

# 某些产品线的模型名省略了厂商前缀（DeepSeek 的写成 `V4 Flash` / `R1`），
# 补回来才能和 raw.csv 对上
_MODEL_PREFIX = {
    "DeepSeek": "DeepSeek-",
}

_UNIT_MULTIPLIER = {"1k": 1000.0, "1m": 1.0}

# meterName 尾部的修饰词，从右往左剥；剥完剩下的就是模型名。
# 大小写不敏感匹配。顺序无关，但必须列全——漏一个就会把它当成模型名的一部分。
_DEPLOYMENT_TOKENS = {
    "regnl", "regional", "glbl", "global", "gl", "dz", "dzn", "dzone",
    "zone", "data", "us", "eu",
}
_TIER_TOKENS = {"batch", "pp", "ptu", "prov", "provisioned", "spillover"}
_CACHE_TOKENS = {"cd", "cchd", "cached", "cache"}
_INPUT_TOKENS = {"inp", "inpt", "input", "inpu"}
_OUTPUT_TOKENS = {"outp", "opt", "output", "otp"}
_UNIT_TOKENS = {"1m", "1k", "tokens", "token"}
_FINETUNE_TOKENS = {"ft", "finetuned", "finetune", "mm-ft"}

_ALL_MODIFIERS = (
    _DEPLOYMENT_TOKENS
    | _TIER_TOKENS
    | _CACHE_TOKENS
    | _INPUT_TOKENS
    | _OUTPUT_TOKENS
    | _UNIT_TOKENS
    | _FINETUNE_TOKENS
)


class AzureParseIssue:
    """没能可靠解析的计量项。跳过是有意的，但必须可见。"""

    def __init__(self, product: str, meter: str, reason: str):
        self.product = product
        self.meter = meter
        self.reason = reason


def _tokenize(meter: str) -> list[str]:
    """`Phi-3.5-Mini-128K-Instruct-Output Tokens` 的方向词是用短横连的，
    而 `V4 Flash Outp DZ Tokens` 是用空格连的。两种都要能切开，
    但不能把 `Phi-3.5-Mini-128K-Instruct` 本身切碎。

    做法：先按空格切；对最后一段，若它以 `-<修饰词>` 结尾则再切一刀。
    """
    parts = meter.split()
    out: list[str] = []
    for part in parts:
        if "-" in part:
            head, _, tail = part.rpartition("-")
            if head and tail.lower() in _ALL_MODIFIERS:
                out.append(head)
                out.append(tail)
                continue
        out.append(part)
    return out


def parse_items(
    items: list[dict],
    *,
    source_url: str,
    fetched_at: str,
    source_version: str | None,
    region: str = DEFAULT_REGION,
) -> tuple[list[PriceRecord], list[AzureParseIssue]]:
    records: list[PriceRecord] = []
    issues: list[AzureParseIssue] = []

    for item in items:
        product = item.get("productName") or ""
        if product in _DELIBERATELY_SKIPPED:
            continue
        company = _PRODUCT_TO_COMPANY.get(product)
        if company is None:
            issues.append(
                AzureParseIssue(product, item.get("meterName", ""), "productName 不在白名单")
            )
            continue

        if item.get("type") != "Consumption":
            continue

        unit = (item.get("unitOfMeasure") or "").strip().lower()
        multiplier = _UNIT_MULTIPLIER.get(unit)
        if multiplier is None:
            continue  # 按小时/按次计费，非 token

        price = item.get("retailPrice")
        if not isinstance(price, (int, float)) or price <= 0:
            continue

        meter = item.get("meterName") or ""
        tokens = _tokenize(meter)
        lowered = [t.lower() for t in tokens]

        is_cache = any(t in _CACHE_TOKENS for t in lowered)
        is_input = any(t in _INPUT_TOKENS for t in lowered)
        is_output = any(t in _OUTPUT_TOKENS for t in lowered)

        if is_cache and is_input:
            field = "cache_read_per_1m"
        elif is_output:
            field = "output_per_1m"
        elif is_input:
            field = "input_per_1m"
        else:
            issues.append(AzureParseIssue(product, meter, "meterName 里认不出输入/输出方向"))
            continue

        tier = "standard"
        if "batch" in lowered:
            tier = "batch"
        elif "pp" in lowered:
            tier = "priority"

        # 从右往左剥掉修饰词，剩下的是模型名
        end = len(tokens)
        while end > 0 and lowered[end - 1] in _ALL_MODIFIERS:
            end -= 1
        model = " ".join(tokens[:end]).strip(" -")

        if not model:
            issues.append(AzureParseIssue(product, meter, "剥掉修饰词后没有剩下模型名"))
            continue

        model = _MODEL_PREFIX.get(company, "") + model

        # 部署范围会让同一模型有多个价（regional/global/data-zone），
        # 记成 qualifier 而不是互相覆盖
        deployment = [t for t in lowered if t in _DEPLOYMENT_TOKENS]
        qualifier = " ".join(deployment) if deployment else None

        records.append(
            PriceRecord(
                source="azure_retail",
                source_tier=TIER_OFFICIAL,
                # Azure 转售第三方模型；只有微软自家的 Phi/MAI 算厂商牌价
                is_official=(company == "Microsoft"),
                source_url=source_url,
                fetched_at=fetched_at,
                source_snippet=(
                    f"{product} | {meter} | {item.get('retailPrice')} "
                    f"per {item.get('unitOfMeasure')} | {item.get('armRegionName')}"
                ),
                unit_original=item.get("unitOfMeasure") or unit,
                source_version=source_version,
                model_id=model,
                provider=f"azure-foundry/{region}",
                service_tier=tier,
                qualifier=qualifier,
                raw={
                    "company": company,
                    "product": product,
                    "meter_id": item.get("meterId"),
                    "sku": item.get("skuName"),
                    "region": region,
                },
                **{field: price * multiplier},
            )
        )

    return records, issues
