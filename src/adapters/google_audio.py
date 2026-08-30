"""Google Gemini API 的音频按次官方价（Tier 1 官方）。

覆盖范围**故意收窄到「per song」这一种单位**：

* 它是目前唯一没有任何源覆盖的音频计费单位（按次表此前 0 行）；
* 只认这一种写法，就不可能与 token / 按分钟 / 按字符 三张表里的
  任何数据撞车，也不会误伤同页上的 Veo、Gemini 文本模型价格。

同页的 TTS / Live 按 token 价是另一件事：那些模型已经能从 litellm 拿到
报价，若要再补官方价应当单独做并单独验证，不塞进这个解析器。

为什么能抓：companies.yaml 已判定 Google 定价页为 ``server_html``——
实测 curl 直接拿到 240KB 服务端渲染 HTML，"per song" 就在字节里，
不需要无头浏览器。

⚠️ 展示名到模型 id 用**人工核验的映射表**，不做模糊匹配。
   页面上的 "Lyria 3 Clip Preview (30s)" 与 id ``lyria-3-clip-preview``
   之间没有可靠的机械对应关系（括号里的 30s / Full Song 是规格不是版本），
   猜一个就等于给模型安一个别人的价格。
"""

from __future__ import annotations

import html as html_mod
import re

from ..records import PriceRecord, TIER_OFFICIAL

GOOGLE_PRICING_URL = "https://ai.google.dev/gemini-api/docs/pricing"

# 人工核验：2026-08-30 对照官方定价页 Lyria 3 区块逐行确认。
# 左边是页面表格第一列的原文，右边是 raw.csv / Gemini API 使用的模型 id。
_ROW_TO_MODEL = {
    "lyria 3 clip preview (30s)": "lyria-3-clip-preview",
    "lyria 3 pro preview (full song)": "lyria-3-pro-preview",
}

# "$0.04 per song"，容忍千分位与小数
_PRICE_RE = re.compile(r"\$\s*([0-9]+(?:\.[0-9]+)?)\s*per\s+song", re.I)


def _to_text(fragment: str) -> str:
    """HTML 片段 -> 用 | 分隔的可读单元格流。"""
    text = re.sub(r"<[^>]+>", " | ", fragment)
    text = html_mod.unescape(text)
    text = re.sub(r"(\s*\|\s*)+", " | ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def parse_google_audio(
    text: str, *, source_url: str, fetched_at: str, source_version: str | None
) -> tuple[list[PriceRecord], list[str]]:
    warnings: list[str] = []
    records: list[PriceRecord] = []
    seen: set[str] = set()

    for match in _PRICE_RE.finditer(_to_text(text)):
        price = float(match.group(1))
        # 往前找最近的、能对上核验表的那一行标签
        before = match.string[max(0, match.start() - 400): match.start()]
        cells = [c.strip() for c in before.split("|") if c.strip()]
        model_id = None
        label = None
        for cell in reversed(cells):
            key = cell.lower().strip()
            if key in _ROW_TO_MODEL:
                model_id, label = _ROW_TO_MODEL[key], cell
                break
        if not model_id:
            warnings.append(
                f"google_audio: 找到 ${price} per song，但前面没有核验表里的行标签"
                f"（最近几格：{cells[-3:]}）。页面结构可能变了，"
                "需人工确认后更新 _ROW_TO_MODEL，**不要**改成模糊匹配。"
            )
            continue
        if model_id in seen:
            continue          # 同一价格在页面里出现两次（概览 + 详表）
        seen.add(model_id)

        records.append(PriceRecord(
            source="google_audio_official",
            source_tier=TIER_OFFICIAL,
            is_official=True,
            source_url=source_url,
            fetched_at=fetched_at,
            source_snippet=f"{label} | ${price} per song",
            unit_original="USD per song (per request)",
            model_id=model_id,
            provider="Google Gemini API",
            modality="audio",
            # 按次价与时长无关，billing_basis 留空是正确的——
            # 那一列只描述「时长价计的是哪段时间」。
            per_call=price,
            source_version=source_version,
            raw={
                "company": "Google",
                "provider_name": "Google Gemini API",
                "weblink": GOOGLE_PRICING_URL,
                "seller_url": GOOGLE_PRICING_URL,
            },
        ))

    if not records:
        warnings.append(
            "google_audio: 一条 per song 价都没解析到，"
            "疑似 Google 改了定价页结构或下架了 Lyria 按次计费。"
        )
    return records, warnings
