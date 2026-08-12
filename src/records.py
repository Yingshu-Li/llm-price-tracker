"""价格记录的溯源契约。

本项目的核心不变量：**任何一个价格数字都必须能回答「哪个源、哪个 URL、
什么时候、原文长什么样」**。这个不变量在这里用构造期断言强制，而不是靠
各个 adapter 自觉——写错了在构造时就炸，不会静默产出无法溯源的数字。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# 源层级：官方 > 可 vendor 的开源 > 在线第三方。导出取值时按此优先。
TIER_OFFICIAL = 1
TIER_VENDORED = 2
TIER_THIRDPARTY = 3



@dataclass
class PriceRecord:
    """一个源在某一时刻，对某一个模型的一次价格观测。

    一条 PriceRecord = 一个源的一行原文。跨源合并发生在 merge 阶段，
    这里不做任何合并，以保证每条观测都可以独立追回出处。
    """

    # ── 溯源字段：全部必填，缺失即视为 bug ────────────────────────
    source: str  # adapter id，如 "openai_md"
    source_tier: int  # 1=官方 2=vendored 3=在线第三方
    is_official: bool  # 是否厂商自己发布的价格
    source_url: str  # 实际服务内容的最终 URL（跟随重定向后）
    fetched_at: str  # ISO8601，这次观测的时间
    source_snippet: str  # 产出这些数字的原始行/片段，原样保存
    unit_original: str  # 换算前的原始单位描述

    # ── 身份 ──────────────────────────────────────────────────
    model_id: str  # 源里的模型标识，未经规范化
    provider: str  # 实际提供服务的一方

    # ── 价格：统一为「每 100 万 token 美元」，非 token 计价另立字段 ──
    input_per_1m: float | None = None
    output_per_1m: float | None = None
    cache_read_per_1m: float | None = None
    cache_write_per_1m: float | None = None
    cache_write_1h_per_1m: float | None = None
    audio_input_per_1m: float | None = None
    audio_output_per_1m: float | None = None
    per_image: float | None = None
    per_video: float | None = None
    per_second: float | None = None
    per_call: float | None = None

    # ── 元信息 ────────────────────────────────────────────────
    context_length: int | None = None
    max_output: int | None = None
    modality: str | None = None
    # 价格分层限定，如 "<272K context length" / "≥ 200k prompt tokens"。
    # 同一个模型会因此有多条观测，不能互相覆盖。
    qualifier: str | None = None
    # 服务层级：standard / batch / flex / fast / priority
    service_tier: str = "standard"
    currency: str = "USD"

    source_version: str | None = None  # ETag / Last-Modified / git commit
    raw: dict[str, Any] = field(default_factory=dict)

    # 溯源字段清单，导出与校验共用，避免两处各写一份而漂移
    PROVENANCE_FIELDS = (
        "source",
        "source_tier",
        "is_official",
        "source_url",
        "fetched_at",
        "source_snippet",
        "unit_original",
    )

    PRICE_FIELDS = (
        "input_per_1m",
        "output_per_1m",
        "cache_read_per_1m",
        "cache_write_per_1m",
        "cache_write_1h_per_1m",
        "audio_input_per_1m",
        "audio_output_per_1m",
        "per_image",
        "per_video",
        "per_second",
        "per_call",
    )

    def __post_init__(self) -> None:
        for name in self.PROVENANCE_FIELDS:
            value = getattr(self, name)
            if value is None or (isinstance(value, str) and not value.strip()):
                raise ValueError(
                    f"PriceRecord 缺少溯源字段 {name!r}（model_id={self.model_id!r}, "
                    f"source={self.source!r}）。每个价格数字都必须可追溯，不允许留空。"
                )

        if self.source_tier not in (TIER_OFFICIAL, TIER_VENDORED, TIER_THIRDPARTY):
            raise ValueError(f"source_tier 必须是 1/2/3，得到 {self.source_tier!r}")

        if not self.model_id or not self.model_id.strip():
            raise ValueError(f"model_id 不能为空（source={self.source!r}）")

        # source_url 必须是能点开核对的真实 URL，不接受 adapter 名之类的占位
        if not re.match(r"^https?://", self.source_url):
            raise ValueError(
                f"source_url 必须是 http(s) URL，得到 {self.source_url!r}。"
                "溯源要求这个字段能直接点开人工核对。"
            )

        # 一条什么价格都没有的观测没有意义，多半是解析器把表头当数据行了
        if not self.has_any_price():
            raise ValueError(
                f"PriceRecord 没有任何价格字段（model_id={self.model_id!r}, "
                f"source={self.source!r}, snippet={self.source_snippet[:80]!r}）"
            )

        for name in self.PRICE_FIELDS:
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} 为负数 {value!r}（model_id={self.model_id!r}）")

    def has_any_price(self) -> bool:
        return any(getattr(self, name) is not None for name in self.PRICE_FIELDS)


