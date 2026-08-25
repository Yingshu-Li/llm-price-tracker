"""Tier 2：可 vendor 到本地的 MIT 开源价格数据集。

models.dev 和 LiteLLM 都是 MIT 许可，数据可以合法地永久保存一份副本。
这是对"上游站点挂掉"最直接的对冲：**先读本地 vendor/ 副本，读不到才联网**。

需要说清楚的一点：这两个数据集**不是抓来的，它们本身就是根源**。
models.dev 的 google.ts 里写着 `cost: existing.cost`（价格取自本地手工 TOML），
并注明 Google 的 Models API 不提供价格；LiteLLM 的 JSON 由人和 AI bot 依据
厂商公告手工维护。所以这部分无法"从根源自己获取"——能做的是合法留存副本。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..normalize import infer_company
from ..records import TIER_VENDORED, PriceRecord
from ..seller_urls import catalog_url_for

VENDOR_DIR = Path(__file__).resolve().parent.parent.parent / "vendor"

MODELSDEV_URL = "https://models.dev/api.json"
MODELSDEV_WEBLINK = "https://models.dev"
LITELLM_URL = (
    "https://cdn.jsdelivr.net/gh/BerriAI/litellm@main/"
    "model_prices_and_context_window.json"
)
LITELLM_WEBLINK = "https://github.com/BerriAI/litellm"

# 有些 LiteLLM 第一方记录的 key 是裸模型名，唯一的厂商字段只有 provider。
# 这里只列不会产生歧义的模型厂商自营 provider；云平台（bedrock/azure/oci）
# 不能映射成模型公司。
_LITELLM_PROVIDER_COMPANIES = {
    "cohere": "Cohere",
}


def local_path(name: str) -> Path:
    return VENDOR_DIR / f"{name}.json"


def load_local(name: str) -> Any | None:
    path = local_path(name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_local(name: str, text: str) -> None:
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    local_path(name).write_text(text, encoding="utf-8")


def parse_modelsdev(
    payload: dict, *, source_url: str, fetched_at: str, source_version: str | None
) -> list[PriceRecord]:
    """{provider_id: {models: {model_id: {..., cost: {input, output, ...}}}}}

    cost 已经是「每 100 万 token 美元」，不需换算。
    """
    records: list[PriceRecord] = []
    for provider_id, provider in payload.items():
        if not isinstance(provider, dict):
            continue
        for model_id, model in (provider.get("models") or {}).items():
            cost = model.get("cost") or {}
            if not isinstance(cost, dict):
                continue
            prices = {
                "input_per_1m": cost.get("input"),
                "output_per_1m": cost.get("output"),
                "cache_read_per_1m": cost.get("cache_read"),
                "cache_write_per_1m": cost.get("cache_write"),
            }
            prices = {
                k: float(v)
                for k, v in prices.items()
                if isinstance(v, (int, float)) and v > 0
            }
            if not prices:
                continue

            company = infer_company(model_id) or infer_company(model.get("name") or "")
            if not company:
                continue

            # models.dev 的 key 是 `卖家/模型路径`，provider_id 段就是**实际报价方**。
            # 卖家正是模型厂商自己时（`google/gemini-2.5-pro`），这条就是厂商牌价，
            # 不是转售价。一律 is_official=False 会把约 25 个官方价误标成 hosted，
            # 而判据其实就在 key 里——丢掉它才是信息损失。
            # 推断不出卖家归属时保持 False：宁可把官方价谦称为 hosted，
            # 也不能把转售价谎报成牌价。
            seller_company = infer_company(provider_id)
            is_first_party = seller_company == company

            limit = model.get("limit") or {}
            records.append(
                PriceRecord(
                    source="modelsdev",
                    source_tier=TIER_VENDORED,
                    is_official=is_first_party,
                    source_url=source_url,
                    fetched_at=fetched_at,
                    source_snippet=f"{provider_id}/{model_id} cost={json.dumps(cost)}",
                    unit_original="per 1M tokens (USD)",
                    source_version=source_version,
                    model_id=model_id,
                    provider=f"modelsdev/{provider_id}",
                    context_length=limit.get("context"),
                    max_output=limit.get("output"),
                    raw={
                        "company": company,
                        "weblink": MODELSDEV_WEBLINK,
                        "provider_name": "models.dev",
                        "open_weights": model.get("open_weights"),
                        "seller": provider_id,
                        # 商家自己的模型目录；source_url 仍保留价格数据证据。
                        "seller_url": (
                            catalog_url_for(provider_id)
                            or provider.get("doc")
                            or provider.get("api")
                            or ""
                        ),
                    },
                    **prices,
                )
            )
    return records


def parse_litellm(
    payload: dict, *, source_url: str, fetched_at: str, source_version: str | None
) -> list[PriceRecord]:
    """{model_key: {input_cost_per_token, output_cost_per_token, ...}}

    单位是「每 token 美元」，要 ×1e6。
    key 按**服务方**命名（`bedrock/ap-northeast-1/qwen...`），取最后一段作模型 id。
    """
    records: list[PriceRecord] = []
    for key, entry in payload.items():
        if key == "sample_spec" or not isinstance(entry, dict):
            continue
        # LiteLLM 这条直连 Cohere 记录把 0.10/1M 错写成了 0.0001/token
        #（即 100/1M）。同一数据集里的 OCI 四个区域记录均为 0.10/1M。
        # 仅在异常量级仍存在时跳过；上游修正后会自动恢复使用。
        if (
            key == "embed-multilingual-light-v3.0"
            and (entry.get("input_cost_per_token") or 0) >= 0.00001
        ):
            continue
        prices = {
            "input_per_1m": entry.get("input_cost_per_token"),
            "output_per_1m": entry.get("output_cost_per_token"),
            "cache_read_per_1m": entry.get("cache_read_input_token_cost"),
            "cache_write_per_1m": entry.get("cache_creation_input_token_cost"),
        }
        prices = {
            k: float(v) * 1_000_000
            for k, v in prices.items()
            if isinstance(v, (int, float)) and v > 0
        }
        # 按张/按秒计价与 token 无关，**绝不能** ×1e6——那会把 $0.04/张
        # 变成 $40000/张。这类字段原值即最终值，单独取。
        #
        # 视频的每秒价上游有两套字段名：Google 系用 output_cost_per_second，
        # OpenAI/Runway 系用 output_cost_per_video_per_second，语义相同，
        # 这里归一到 per_second。
        flat = {
            "per_image": entry.get("output_cost_per_image"),
            "per_second": (
                entry.get("output_cost_per_second")
                or entry.get("output_cost_per_video_per_second")
            ),
        }
        flat = {
            k: float(v)
            for k, v in flat.items()
            if isinstance(v, (int, float)) and v > 0
        }
        prices.update(flat)
        if not prices:
            continue

        # 这里**故意不设 modality**。导出层按模态分组比价，而聚合器
        # （empiriolabs / vercel / ofox…）都不给这个字段、一律落在默认的
        # Text 组。若只给 litellm 标上 Image，两边就进了不同的池子：
        # 图像组非空 → 导出层的回退分支不触发 → 聚合器的报价被排除在
        # 「最低价」之外（实测 gpt-image-2 会漏掉 empiriolabs 的 $0.012，
        # 错报成 litellm 的 $0.054）。
        # 按张价靠 per_image 字段本身即可识别，无需 modality。

        model_id = key.split("/")[-1]
        seller = entry.get("litellm_provider") or (
            key.split("/")[0] if "/" in key else ""
        )
        # 部分第一方 key 只有裸模型名（如 embed-english-v3.0），公司信息
        # 只存在于 litellm_provider。此前没检查 seller，导致整组 Cohere
        # Embedding 价格在构造 PriceRecord 之前就被丢弃。
        company = (
            infer_company(key)
            or infer_company(model_id)
            or infer_company(seller)
            or _LITELLM_PROVIDER_COMPANIES.get(str(seller).lower())
            or (
                "Cohere"
                if model_id.lower().startswith("cohere.")
                else None
            )
        )
        if not company:
            continue

        # 同 models.dev：key 按服务方命名，首段是卖家。`litellm_provider` 更可靠
        # 时优先用它。卖家 == 厂商本人即为牌价。
        seller_company = (
            infer_company(seller)
            or _LITELLM_PROVIDER_COMPANIES.get(str(seller).lower())
        )
        is_first_party = bool(seller) and seller_company == company

        records.append(
            PriceRecord(
                source="litellm",
                source_tier=TIER_VENDORED,
                is_official=is_first_party,
                source_url=source_url,
                fetched_at=fetched_at,
                source_snippet=(
                    f"{key} input={entry.get('input_cost_per_token')} "
                    f"output={entry.get('output_cost_per_token')}"
                    + (f" image={entry.get('output_cost_per_image')}"
                       if "per_image" in flat else "")
                    + (f" second={flat['per_second']}"
                       if "per_second" in flat else "")
                ),
                # 溯源要求单位描述与实际取到的字段一致，不能一律写 per token
                unit_original=" + ".join(
                    filter(None, [
                        "per token (USD)" if any(
                            k.endswith("_per_1m") for k in prices) else "",
                        "per image (USD)" if "per_image" in flat else "",
                        "per second (USD)" if "per_second" in flat else "",
                    ])
                ),
                source_version=source_version,
                model_id=model_id,
                provider=f"litellm/{entry.get('litellm_provider') or '?'}",
                context_length=entry.get("max_input_tokens"),
                max_output=entry.get("max_output_tokens"),
                raw={
                    "company": company,
                    "weblink": LITELLM_WEBLINK,
                    "provider_name": "LiteLLM",
                    "mode": entry.get("mode"),
                    "litellm_key": key,
                    "seller": seller,
                    "seller_url": catalog_url_for(seller),
                },
                **prices,
            )
        )
    return records
