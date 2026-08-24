"""解析讯飞星火 MaaS 模型广场的官方公开价格接口。"""

from __future__ import annotations

import json
from typing import Any

from ..records import PriceRecord, TIER_OFFICIAL

API_URL = (
    "https://maas.xfyun.cn/api/v1/gpt-finetune/model/base/"
    "list-v2?page=1&size=9999"
)
WEBLINK = "https://maas.xfyun.cn/modelSquare"

# 只收已经人工核对过、且 raw.csv 中确有对应项的官方服务 ID。
SUPPORTED_SERVICE_IDS = {"xsparkx2", "xsparkx2flash"}
EXPECTED_UNIT = "元/百万tokens"


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_prices(
    payload: Any, *, source_url: str, fetched_at: str,
    source_version: str | None,
) -> tuple[list[PriceRecord], list[str]]:
    """读取 X2 / X2-Flash 的人民币输入、输出 token 单价。"""
    warnings: list[str] = []
    if not isinstance(payload, dict) or payload.get("code") != 0:
        return [], ["iflytek_maas_api: 接口返回码不是 0 或响应不是 JSON 对象"]

    data = payload.get("data")
    rows = data.get("rows") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return [], ["iflytek_maas_api: 响应缺少 data.rows 数组"]

    records: list[PriceRecord] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        service_id = str(row.get("serviceId") or "").strip()
        if service_id not in SUPPORTED_SERVICE_IDS:
            continue
        if service_id in seen:
            warnings.append(f"iflytek_maas_api: {service_id} 重复出现，保留首次")
            continue
        if row.get("showPrice") is not True:
            warnings.append(f"iflytek_maas_api: {service_id} 当前未公开展示价格")
            continue

        price = row.get("price")
        inference = price.get("inferencePrice") if isinstance(price, dict) else None
        if not isinstance(inference, dict):
            warnings.append(f"iflytek_maas_api: {service_id} 缺少 inferencePrice")
            continue

        input_price = _number(inference.get("inTokensPrice"))
        output_price = _number(inference.get("outTokensPrice"))
        input_unit = str(inference.get("inTokensUnit") or "").replace(" ", "")
        output_unit = str(inference.get("outTokensUnit") or "").replace(" ", "")
        if input_price is None or output_price is None:
            warnings.append(f"iflytek_maas_api: {service_id} 输入/输出价格不是数字")
            continue
        if input_unit != EXPECTED_UNIT or output_unit != EXPECTED_UNIT:
            warnings.append(
                f"iflytek_maas_api: {service_id} 单位发生变化："
                f"{input_unit!r} / {output_unit!r}"
            )
            continue

        snippet_fields = {
            "name": row.get("name"),
            "serviceId": service_id,
            "showPrice": row.get("showPrice"),
            "inferencePrice": inference,
            "updateTime": row.get("updateTime"),
        }
        records.append(
            PriceRecord(
                source="iflytek_maas_api",
                source_tier=TIER_OFFICIAL,
                is_official=True,
                source_url=source_url,
                fetched_at=fetched_at,
                source_snippet=json.dumps(
                    snippet_fields, ensure_ascii=False, separators=(",", ":")
                ),
                unit_original="CNY per 1M tokens",
                model_id=service_id,
                provider="iflytek",
                input_per_1m=input_price,
                output_per_1m=output_price,
                currency="CNY",
                source_version=source_version,
                raw={"official_model_name": row.get("name")},
            )
        )
        seen.add(service_id)

    for missing in sorted(SUPPORTED_SERVICE_IDS - seen):
        warnings.append(f"iflytek_maas_api: 官方接口未返回 {missing} 的可用价格")
    return records, warnings
