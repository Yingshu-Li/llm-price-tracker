"""配置驱动的通用价格 API 适配器。

`config/price_apis.yaml` 里每条描述一个可直连的根源 API：列表在哪、id 用哪些
字段、价格字段的点号路径、以及**单位**。加一个新源通常只需在 YAML 里加一段，
不用写代码。

单位是这里唯一不能出错的地方：
  per_token       每 token 美元      ×1e6
  per_1m          每 100 万 token    ×1
  cents_per_token 每 token 美分      ×1e6 ÷ 100
未知单位直接抛错而不是猜——猜错是 1000 倍的静默错误，且数字看起来完全合理。

`unit` 只管 token 类价格。按张/按秒/按次是**另一套量纲**，不参与 token 换算，
由 `flat_unit` 单独声明（缺省 `usd`，即原值就是美元）：
  usd    $0.025/张 直接可用
  cents  DeepInfra 的 cents_per_image_unit=2.5 表示 $0.025/张，需 ÷100
漏声明 cents 会得到 100 倍高价，而 2.5 这个数字本身看不出异常。
"""

from __future__ import annotations

from typing import Any

from ..records import PriceRecord

UNIT_MULTIPLIER = {
    "per_token": 1_000_000.0,
    "per_1m": 1.0,
    "cents_per_token": 1_000_000.0 / 100.0,
}

# 按张/按秒/按次这类扁平价的量纲
FLAT_UNIT_MULTIPLIER = {
    "usd": 1.0,
    "cents": 1.0 / 100.0,
}

# 与 token 无关、不参与 UNIT_MULTIPLIER 换算的价格字段
FLAT_PRICE_FIELDS = ("per_image", "per_second", "per_video", "per_call")

# 同一模型的变体后缀，不是独立模型
_VARIANT_SUFFIXES = (":free", ":extended", ":thinking", ":online", ":nitro")


def _dig(obj: Any, path: str) -> Any:
    """按点号路径取值，任一层缺失返回 None。"""
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    return cur


def _to_float(value: Any) -> float | None:
    """价格可能是数字，也可能是字符串（OpenRouter/Vercel 都用字符串）。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def _to_int(value: Any) -> int | None:
    number = _to_float(value)
    return int(number) if number is not None else None


def _extract_list(payload: Any, list_path: str | None) -> list[dict]:
    data = _dig(payload, list_path) if list_path else payload
    if data is None and isinstance(payload, dict):
        for key in ("data", "models", "items"):
            if isinstance(payload.get(key), list):
                data = payload[key]
                break
    if isinstance(payload, list) and data is None:
        data = payload
    return [x for x in (data or []) if isinstance(x, dict)]


def parse_api(
    payload: Any,
    spec: dict,
    *,
    source_url: str,
    fetched_at: str,
    source_version: str | None,
) -> tuple[list[PriceRecord], list[str], dict[str, tuple[str, str]]]:
    """按一份 YAML spec 解析一个价格 API 的响应。

    返回 (记录, 告警, 免费额度层)。告警不是致命错误，但要能被看见——静默少解析比报错更危险。
    """
    unit = spec.get("unit")
    if unit not in UNIT_MULTIPLIER:
        raise ValueError(
            f"源 {spec['id']!r} 的 unit={unit!r} 未知。已知：{sorted(UNIT_MULTIPLIER)}。"
            "不做猜测是有意的——单位猜错会产出差几个数量级但看起来合理的价格。"
        )
    multiplier = UNIT_MULTIPLIER[unit]
    flat_unit = spec.get("flat_unit", "usd")
    if flat_unit not in FLAT_UNIT_MULTIPLIER:
        raise ValueError(
            f"源 {spec['id']!r} 的 flat_unit={flat_unit!r} 未知。"
            f"已知：{sorted(FLAT_UNIT_MULTIPLIER)}。"
        )
    flat_multiplier = FLAT_UNIT_MULTIPLIER[flat_unit]
    currency = spec.get("currency", "USD")
    fields: dict[str, str] = spec.get("fields") or {}
    id_fields: list[str] = spec.get("id_fields") or ["id"]
    nested = spec.get("nested_list")
    skip_prefixes = tuple(spec.get("skip_id_prefixes") or ())
    skip_if_true = spec.get("skip_if_true") or []

    records: list[PriceRecord] = []
    warnings: list[str] = []
    seen_any_price = False
    # {规范化后的 model_id: (卖家, 该模型在该平台的页面)}
    # 免费层条目价格全为 0，构造不出 PriceRecord（构造期断言要求至少有一个
    # 价格），所以单独收集，不走记录那条路。
    free_tiers: dict[str, tuple[str, str]] = {}

    for item in _extract_list(payload, spec.get("list_path")):
        model_id = next(
            (str(item[f]) for f in id_fields if item.get(f)), None
        )
        if not model_id:
            continue
        if skip_prefixes and model_id.startswith(skip_prefixes):
            continue
        if any(item.get(flag) is True for flag in skip_if_true):
            continue
        # `:free` 是**免费额度层**，不是"这个模型免费"——有每日请求数上限、
        # 速率限制，高峰期可能排不上。把它当最低价报出去会让人以为能零成本
        # 无限用，所以后缀照剥、不参与比价；但"存在免费层"这个事实有价值，
        # 在剥掉之前先记下来（剥完就分辨不出是哪个变体了）。
        free_tier = model_id.endswith(":free")
        raw_id = model_id
        for suffix in _VARIANT_SUFFIXES:
            if model_id.endswith(suffix):
                model_id = model_id[: -len(suffix)]

        if free_tier:
            # 免费层的落地页：能拼出该平台的模型页就用它，否则退回站点首页
            link = spec.get("model_url_template")
            link = link.format(id=raw_id) if link else (spec.get("weblink") or "")
            free_tiers.setdefault(model_id, (spec.get("name") or spec["id"], link))

        # hf_router 这类把价格挂在 providers[] 数组里，每个托管方一条记录
        containers = item.get(nested) if nested else None
        buckets = (
            [c for c in containers if isinstance(c, dict)]
            if isinstance(containers, list)
            else [item]
        )

        for bucket in buckets:
            prices: dict[str, float] = {}
            context_length = None
            for target, path in fields.items():
                raw_value = _dig(bucket, path)
                if raw_value is None:
                    raw_value = _dig(item, path)
                if target == "context_length":
                    context_length = _to_int(raw_value)
                    continue
                value = _to_float(raw_value)
                if value is None or value <= 0:
                    continue
                # 按张/按秒/按次是另一套量纲，走 flat_unit 而非 token 单位换算
                factor = (
                    flat_multiplier
                    if target in FLAT_PRICE_FIELDS
                    else multiplier
                )
                prices[target] = value * factor

            if not prices:
                continue
            seen_any_price = True

            provider = spec["id"]
            if nested and bucket.get("provider"):
                provider = f"{spec['id']}/{bucket['provider']}"

            model_url = spec.get("model_url_template")
            seller_url = (
                model_url.format(id=raw_id)
                if model_url
                else (spec.get("weblink") or "")
            )

            snippet_parts = [f"{k}={_dig(bucket, v) or _dig(item, v)!r}" for k, v in fields.items()]
            snippet = f"{model_id} | " + " ".join(snippet_parts)

            try:
                records.append(
                    PriceRecord(
                        source=spec["id"],
                        source_tier=int(spec.get("tier", 3)),
                        # 这些都是聚合/托管方，不是模型厂商自己的牌价。
                        # is_model_author 为真时例外（HF Router 提供这个字段）。
                        is_official=bool(bucket.get("is_model_author")),
                        source_url=source_url,
                        fetched_at=fetched_at,
                        source_snippet=snippet[:500],
                        unit_original=f"{unit} ({currency})",
                        source_version=source_version,
                        model_id=model_id,
                        provider=provider,
                        currency=currency,
                        context_length=context_length,
                        raw={
                            "api": spec["id"],
                            "weblink": spec.get("weblink"),
                            "provider_name": spec.get("name"),
                            "seller": provider,
                            "seller_url": seller_url,
                            # HF Router 顺带给了速度，先存着不用
                            "throughput": bucket.get("throughput"),
                            "ttft_ms": bucket.get("first_token_latency_ms"),
                        },
                        **prices,
                    )
                )
            except ValueError as exc:
                warnings.append(f"{spec['id']}: {model_id} 构造失败 {exc}")

    if not seen_any_price:
        warnings.append(
            f"{spec['id']}: 响应解析成功但**一条价格都没取到**，"
            "多半是字段路径变了——这是静默失效，必须查"
        )
    # 免费层挂在返回值上而不是塞进 records：它不是一条价格观测，
    # 不该参与比价，只是"该模型在此平台另有免费额度"这个事实。
    return records, warnings, free_tiers
