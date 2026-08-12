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

VENDOR_DIR = Path(__file__).resolve().parent.parent.parent / "vendor"

MODELSDEV_URL = "https://models.dev/api.json"
MODELSDEV_WEBLINK = "https://models.dev"
LITELLM_URL = (
    "https://cdn.jsdelivr.net/gh/BerriAI/litellm@main/"
    "model_prices_and_context_window.json"
)
LITELLM_WEBLINK = "https://github.com/BerriAI/litellm"


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

            limit = model.get("limit") or {}
            records.append(
                PriceRecord(
                    source="modelsdev",
                    source_tier=TIER_VENDORED,
                    is_official=False,
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
        if not prices:
            continue

        model_id = key.split("/")[-1]
        company = infer_company(key) or infer_company(model_id)
        if not company:
            continue

        records.append(
            PriceRecord(
                source="litellm",
                source_tier=TIER_VENDORED,
                is_official=False,
                source_url=source_url,
                fetched_at=fetched_at,
                source_snippet=(
                    f"{key} input={entry.get('input_cost_per_token')} "
                    f"output={entry.get('output_cost_per_token')}"
                ),
                unit_original="per token (USD)",
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
                },
                **prices,
            )
        )
    return records
